#!/usr/bin/env python3

"""
This script iteratively modifies a given scenario in order to make the network feasible "on the road". This means that
no bus arrives at the depot below 0% SOC. This is done by iteratively modifying the scenario, adding charging stations
and splitting rotations.
"""
import json
from collections import Counter
import argparse
import logging
import os
import warnings
from datetime import timedelta
from random import random
from typing import List, Tuple, Dict, Set, FrozenSet

import numpy as np
import pandas as pd
import sqlalchemy.orm.session
from ds_wrapper import DjangoSimbaWrapper
from eflips.model import *
from eflips.model import ConsistencyWarning
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session

from eflips.depot.api import (
    add_evaluation_to_database,
    delete_depots,
    init_simulation,
    run_simulation,
    generate_depot_layout,
    simple_consumption_simulation,
    apply_even_smart_charging,
)


def list_scenarios(database_url: str, session: sqlalchemy.orm.session.Session) -> None:
    scenarios = session.query(Scenario).all()
    for scenario in scenarios:
        rotation_count = (
            session.query(Rotation).filter(Rotation.scenario_id == scenario.id).count()
        )
        print(f"{scenario.id}: {scenario.name} with {rotation_count} rotations.")


def electrify_station(
    station_id: int, session: sqlalchemy.orm.session.Session, power=300
) -> None:
    """
    This method adds a charging station to the given station. The charging station will be sized "ludicrously large",
    with a following step needed to estimate the actual capacity.

    :param station_id: The id of the station to be electrified.
    :param session: The session to be used.
    :param power: The power of the charging station in kW.
    :return: Nothing. Database is modified in place.
    """

    station: Station = session.query(Station).filter(Station.id == station_id).one()
    station.is_electrified = True
    station.amount_charging_places = 100
    station.power_per_charger = power
    station.power_total = station.amount_charging_places * station.power_per_charger
    station.charge_type = ChargeType.OPPORTUNITY
    station.voltage_level = VoltageLevel.MV
    session.flush()


def split_rotation(
    rotation_id: int,
    session: sqlalchemy.orm.session.Session,
    deadhead_break=timedelta(minutes=5),
) -> None:
    """
    This method splits a rotation into two rotations. Deadhead trips will be added to the rotations after/before the
    split.

    :param rotation_id: The id of the rotation to be split.
    :param session: The session to be used.
    :param deadhead_break: The duration of the deadhead break.
    :return: Nothing. Database is modified in place.
    """
    rotation: Rotation = (
        session.query(Rotation).filter(Rotation.id == rotation_id).one()
    )

    # Find the total distance and the trips that are near the middle
    trip_distances = [trip.route.distance for trip in rotation.trips]
    total_distance = sum(trip_distances)
    cumulative_distances = np.cumsum(trip_distances)
    middle_trip_index = np.argmax(cumulative_distances > total_distance / 2)

    # Create the new rotations
    # The deadhead trips are time-shifted copies of the first/last trip of the original rotation
    first_trips = rotation.trips[:middle_trip_index]

    with session.no_autoflush:
        # Delete the original rotation
        saved_trips_sequence = rotation.trips.copy()
        rotation.trips = []
        session.delete(rotation)

        deadhead_after_start = first_trips[-1].arrival_time + deadhead_break
        deadhead_after_duration = (
            saved_trips_sequence[-1].arrival_time
            - saved_trips_sequence[-1].departure_time
        )
        deadhead_after = Trip(
            scenario_id=rotation.scenario_id,
            route=saved_trips_sequence[-1].route,
            departure_time=deadhead_after_start,
            arrival_time=deadhead_after_start + deadhead_after_duration,
            trip_type=TripType.EMPTY,
        )
        first_trips.append(deadhead_after)

        rotation_a = Rotation(
            scenario_id=rotation.scenario_id,
            name=rotation.name + " (A)",
            vehicle_type_id=rotation.vehicle_type_id,
            allow_opportunity_charging=rotation.allow_opportunity_charging,
        )
        rotation_a.trips = first_trips
        session.add(deadhead_after)
        session.add(rotation_a)

        second_trips = saved_trips_sequence[middle_trip_index:]

        deadhead_before_end = second_trips[0].departure_time - deadhead_break
        deadhead_before_duration = (
            saved_trips_sequence[0].arrival_time
            - saved_trips_sequence[0].departure_time
        )
        deadhead_before = Trip(
            scenario_id=rotation.scenario_id,
            route=saved_trips_sequence[0].route,
            departure_time=deadhead_before_end - deadhead_before_duration,
            arrival_time=deadhead_before_end,
            trip_type=TripType.EMPTY,
        )
        second_trips.insert(0, deadhead_before)

        rotation_b = Rotation(
            scenario_id=rotation.scenario_id,
            name=rotation.name + " (B)",
            vehicle_type_id=rotation.vehicle_type_id,
            allow_opportunity_charging=rotation.allow_opportunity_charging,
        )
        rotation_b.trips = second_trips
        session.add(deadhead_before)
        session.add(rotation_b)

    session.flush()


def simulate(scenario_id: int, session: sqlalchemy.orm.session.Session) -> int:
    """
    This method clones and then simulates a scenario with the given modifications. It then checks if the simulation
    was successful (no bus arrives at the depot with SOC < 0) and returns the result.

    :param original_scenario_id: The id of the scenario to be simulated.
    :param electrified_stations: The ids of the stations to be electrified.
    :param split_rotations: The ids of the rotations to be split.
    :return: The number of driving events that ended with SOC < 0.
    """

    # Run simple consumption simulation
    scenario = session.query(Scenario).filter(Scenario.id == scenario_id).one()
    simple_consumption_simulation(scenario, initialize_vehicles=True)

    # Count the driving events that ended with SOC < 0
    driving_event_count = (
        session.query(Event)
        .filter(Event.scenario_id == scenario_id)
        .filter(Event.event_type == EventType.DRIVING)
        .filter(Event.soc_end < 0)
        .count()
    )
    return driving_event_count


def random_step(
    scenario_id,
    electrified_stations: List[int],
    split_rotations: List[int],
    session: sqlalchemy.orm.session.Session,
    max_rotation_id: int,
    random_bias=0.5,
) -> Tuple[int | None, int | None]:
    """
    Take one random step in the optimization process. This means either electrifying a station or splitting a rotation.

    :param scenario_id: The id of the scenario to be optimized.
    :param electrified_stations: A list of the stations that are already electrified.
    :param split_rotations: A list of the rotations that are already split.
    :param split_rotations_weight: How much more likely it is to split a rotation than electrify a station.
    :return: A tuple of the new scenario id, the new list of electrified stations and the new list of split rotations.
    """

    # Choose whether to electrify a station or split a rotation
    if random() < random_bias:
        # Find the most popular terminus stations where SoC < 0
        low_soc_events = (
            session.query(Event)
            .filter(Event.scenario_id == scenario_id)
            .filter(Event.soc_end < 0)
            .filter(Rotation.id <= max_rotation_id)
            .filter(Event.event_type == EventType.DRIVING)
            .options(sqlalchemy.orm.joinedload(Event.trip).joinedload(Trip.route))
            .all()
        )
        station_popularity = Counter()

        for event in low_soc_events:
            station_popularity[event.trip.route.arrival_station_id] += 1

        most_popular_station = station_popularity.most_common(1)[0][0]

        return most_popular_station, None
    else:
        # Find asssociated with the lowest SoC event
        lowest_soc_event: Event = (
            session.query(Event)
            .filter(Event.soc_end < 0)
            .filter(Event.event_type == EventType.DRIVING)
            .filter(Event.scenario_id == scenario_id)
            .order_by(Event.soc_end)
            .first()
        )
        rotation_id = lowest_soc_event.trip.rotation_id
        return None, rotation_id


def clone_senario(
    original_scenario_id: int, session: sqlalchemy.orm.session.Session
) -> int:
    """
    Clone a scenario with all its rotations and trips. Delete Events and Vehicle Assignments from the new scenario.

    :param original_scenario_id: The id of the scenario to be cloned.

    :return: The id of the new scenario.
    """
    # Clone the scenario
    scenario: Scenario = (
        session.query(Scenario).filter(Scenario.id == original_scenario_id).one()
    )
    clone_scenario = scenario.clone(session)
    session.flush()

    session.query(Rotation).filter(Rotation.scenario_id == clone_scenario.id).update(
        {"vehicle_id": None}
    )
    session.query(Event).filter(Event.scenario_id == clone_scenario.id).delete()
    session.query(Vehicle).filter(Vehicle.scenario_id == clone_scenario.id).delete()
    session.commit()

    return clone_scenario.id


def optimize(
    original_scenario_id: int, session: sqlalchemy.orm.session.Session, random_bias=0.5
) -> None:
    """
    Use a directed random walk to find a feasible scenario. The optimization is done by iteratively modifying the
    scenario, simulating it and then checking if the simulation was successful. The process is repeated until a
    feasible scenario is found.

    :param original_scenario_id: The id of the scenario to be optimized.
    :return: Nothing. The database is modified in place.
    """
    logger = logging.getLogger(__name__)
    random_bias = 0.0  # TODO

    meta_result: List[Dict[str, int]] = []

    for i in range(100):
        electrified_stations = []
        split_rotations = []
        cached_scenarios: Dict[Tuple[FrozenSet[int], FrozenSet[int]], int] = {}
        result = simulate(scenario_id=original_scenario_id, session=session)
        step = 0

        while result > 0:
            step += 1
            new_electrified_station, new_split_rotation = random_step(
                scenario_id=original_scenario_id,
                electrified_stations=electrified_stations,
                split_rotations=split_rotations,
                random_bias=random_bias,
                session=session,
                max_rotation_id=session.query(func.max(Rotation.id)).scalar(),
            )
            if new_electrified_station is not None:
                electrified_stations.append(new_electrified_station)
            if new_split_rotation is not None:
                split_rotations.append(new_split_rotation)
            if (
                frozenset(electrified_stations),
                frozenset(split_rotations),
            ) in cached_scenarios:
                logger.debug("Scenario already exists.")
                result = cached_scenarios[
                    (frozenset(electrified_stations), frozenset(split_rotations))
                ]
            else:
                logger.debug("Simulating new scenario.")

                session.rollback()
                for station_id in electrified_stations:
                    electrify_station(station_id, session=session)
                for rotation_id in split_rotations:
                    split_rotation(rotation_id, session=session)
                result = simulate(original_scenario_id, session=session)
                cached_scenarios[
                    (frozenset(electrified_stations), frozenset(split_rotations))
                ] = result
                logger.info(
                    f"Currently generation {i} step {step} with {len(electrified_stations)} electrified stations and {len(split_rotations)} split rotations. Result: {result}"
                )
                meta_result.append(
                    {
                        "electrified_station_count": len(electrified_stations),
                        "electrified_stations": electrified_stations,
                        "split_rotation_count": len(split_rotations),
                        "split_rotations": split_rotations,
                        "result": result,
                    }
                )
                # Write to a file named after scenario ID and our PID
                pid = os.getpid()
                with open(f"meta_result_{original_scenario_id}_{pid}.csv", "w") as f:
                    json.dump(meta_result, f)

        df = pd.DataFrame(meta_result)
        df.to_csv("meta_result.csv")
        df.to_pickle("meta_result.pkl")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario_id",
        "--scenario-id",
        type=int,
        help="The id of the scenario to be simulated. Run with --list-scenarios to see all available scenarios.",
    )
    parser.add_argument(
        "--list_scenarios",
        "--list-scenarios",
        action="store_true",
        help="List all available scenarios.",
    )
    parser.add_argument(
        "--database_url",
        "--database-url",
        type=str,
        help="The url of the database to be used. If it is not specified, the environment variable DATABASE_URL is used.",
        required=False,
    )
    parser.add_argument(
        "--use_simba_consumption",
        help="Use the consumption simulation from the SimBA Django application.",
        required=False,
        action="store_true",
    )
    args = parser.parse_args()

    if args.database_url is None:
        if "DATABASE_URL" not in os.environ:
            raise ValueError(
                "The database url must be specified either as an argument or as the environment variable DATABASE_URL."
            )
        args.database_url = os.environ["DATABASE_URL"]

    if args.list_scenarios:
        engine = create_engine(args.database_url)
        session = Session(engine)
        list_scenarios(args.database_url, session)
        exit()

    if args.scenario_id is None:
        raise ValueError(
            "The scenario id must be specified. Use --list-scenarios to see all available scenarios, then run with "
            "--scenario-id <id>."
        )

    # Set default log level to INFO
    logging.basicConfig(level=logging.INFO)

    engine = create_engine(args.database_url)
    session = Session(engine)
    optimize(args.scenario_id, session)
