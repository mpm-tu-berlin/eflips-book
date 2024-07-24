#!/usr/bin/env python3

"""
This script iteratively modifies a given scenario in order to make the network feasible "on the road". This means that
no bus arrives at the depot below 0% SOC. This is done by iteratively modifying the scenario, adding charging stations
and splitting rotations.
"""
import argparse
import json
import logging
import multiprocessing
import os
import uuid
import warnings
from collections import Counter
from datetime import timedelta
import random
from typing import List, Tuple, Dict
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import sqlalchemy.orm.session
from ds_wrapper import DjangoSimbaWrapper
from eflips.model import *
from eflips.model import ConsistencyWarning
from matplotlib import pyplot as plt
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# We can ignore the ConsistencyWarning, as we are not interested in the consistency of rotations in this script.
warnings.simplefilter("ignore", category=ConsistencyWarning)


def list_scenarios(database_url: str, session: sqlalchemy.orm.session.Session) -> None:
    scenarios = session.query(Scenario).all()
    for scenario in scenarios:
        rotation_count = (
            session.query(Rotation).filter(Rotation.scenario_id == scenario.id).count()
        )
        print(f"{scenario.id}: {scenario.name} with {rotation_count} rotations.")


def database_url_components(database_url: str) -> Tuple[str, str, str, str, str, str]:
    """
    Extracts the components of a database URL.
    :param database_url: The URL of the database.
    :return: A tuple with the components of the URL: protocol, user, password, host, port, database name.
    """
    o = urlparse(database_url)
    if o.scheme != "postgresql":
        raise ValueError("Only PostgreSQL databases are supported.")
    if o.port is None:
        port = "5432"
    else:
        port = str(o.port)
    return o.scheme, o.username, o.password, o.hostname, port, o.path[1:]


def create_temporary_databases(
    orig_database_url: str, count: int, random_prefix: str
) -> List[str]:
    """
    Creates a number of temporary databases by cloning the original database.
    :param orig_database_url: The URL of the original database. It will be used to find username, password, etc.
    :param count: How many databases to create.
    :param random_prefix: A random prefix to add to the database name.
    :return: A list of URLs of the new databases.
    """
    logger = logging.getLogger(__name__)

    _, database_user, database_password, database_host, database_port, database_name = (
        database_url_components(orig_database_url)
    )

    new_database_urls = []
    for i in range(count):
        new_database_name = f"{database_name}_{random_prefix}_{i}"
        new_database_url = f"postgresql://{database_user}:{database_password}@{database_host}:{database_port}/{new_database_name}"
        new_database_urls.append(new_database_url)
        os.environ["PGPASSWORD"] = database_password
        result = os.system(
            f"createdb -h {database_host} -U {database_user} -p {database_port} {new_database_name} -T {database_name}"
        )
        if result != 0:
            logger.error(f"Could not create database {new_database_name}.")
            raise ValueError(f"Could not create database {new_database_name}.")
        logger.info(f"Created database {new_database_name}.")
    return new_database_urls


def delete_temporary_databases(
    orig_database_url: str, count: int, random_prefix: str
) -> None:
    """
    Deletes a number of temporary databases.
    :param orig_database_url: The URL of the original database. It will be used to find username, password, etc.
    :param count: The number of databases to delete.
    :param random_prefix: The random prefix used to create the databases.
    :return: Nothing.
    """

    logger = logging.getLogger(__name__)

    _, database_user, database_password, database_host, database_port, database_name = (
        database_url_components(orig_database_url)
    )

    for i in range(count):
        new_database_name = f"{database_name}_{random_prefix}_{i}"
        os.environ["PGPASSWORD"] = database_password
        result = os.system(
            f"dropdb -h {database_host} -U {database_user} -p {database_port} {new_database_name}"
        )
        if result != 0:
            logger.error(f"Could not delete database {new_database_name}.")
            raise ValueError(f"Could not delete database {new_database_name}.")
        logger.info(f"Deleted database {new_database_name}.")


def number_of_rotations_below_zero(scenario: Scenario, session: Session) -> int:
    """
    Counts the number of rotations in a scenario where the SOC at the depot is below 0%.
    :param scenario: The scenario to check.
    :param session: The database session.
    :return: The number of rotations with SOC below 0%.
    """
    rotations_q = (
        session.query(Rotation)
        .join(Trip)
        .join(Event)
        .filter(Rotation.scenario_id == scenario.id)
        .filter(Event.event_type == EventType.DRIVING)
        .filter(Event.soc_end < 0)
    )
    return rotations_q.count()


def add_charging_station(scenario, session, power: float = 300) -> int | None:
    """
    Adds a charging station to the scenario. The heuristic for selecting the charging station is to add is to select
    the one where the rotations with negative SoC spend the most time. If no such charging station can be found, None
    is returned.

    :param scenario: THE scenario to add the charging station to.
    :param session: An open database session.
    :param power: The power of the charging station to be added. Default is 300 kW.
    :return: Either the id of the charging station that was added, or None if no charging station could be added.
    """
    # First, we identify all the rotations containing a SoC < 0 event
    logger = logging.getLogger(__name__)

    rotations_with_low_soc = (
        session.query(Rotation)
        .join(Trip)
        .join(Event)
        .filter(Event.soc_end < 0)
        .filter(Event.event_type == EventType.DRIVING)
        .filter(Event.scenario == scenario)
        .options(sqlalchemy.orm.joinedload(Rotation.trips).joinedload(Trip.route))
        .all()
    )

    # For these rotations, we find all the arrival statiosn but the last one. The last one is the depot.
    # We sum up the time spent at a break at each of these stations.
    total_break_time_by_station = Counter()
    for rotation in rotations_with_low_soc:
        for i in range(len(rotation.trips) - 1):
            trip = rotation.trips[i]
            total_break_time_by_station[trip.route.arrival_station_id] += int(
                (
                    rotation.trips[i + 1].departure_time - trip.arrival_time
                ).total_seconds()
            )

    # If all stations have a score iof 0, we terminate the optimization
    if all(v == 0 for v in total_break_time_by_station.values()):
        return None

    most_popular_station_id = total_break_time_by_station.most_common(1)[0][0]
    if logger.isEnabledFor(logging.DEBUG):
        station_name = (
            session.query(Station)
            .filter(Station.id == most_popular_station_id)
            .one()
            .name
        )
        logger.debug(
            f"Station {most_popular_station_id} ({station_name}) was selected as the station where the most time is spent."
        )

    # Actually add the charging station in the database.
    station: Station = (
        session.query(Station).filter(Station.id == most_popular_station_id).one()
    )
    station.is_electrified = True
    station.amount_charging_places = 100
    station.power_per_charger = power
    station.power_total = station.amount_charging_places * station.power_per_charger
    station.charge_type = ChargeType.OPPORTUNITY
    station.voltage_level = VoltageLevel.MV

    return most_popular_station_id


def split_rotation(
    scenario,
    session: Session,
    deadhead_break: timedelta = timedelta(minutes=5),
) -> int | None:
    """
    Splits a rotation in the scenario. The rotation with the lowest SoC at the depot is selected for splitting. The
    rotation is split in its middle.
    :param scenario: The scenario to split the rotation in.
    :param max_rotation_id: The maximum id of the rotation to split. This is used to avoid splitting the same rotation
    multiple times.
    :param session: An open database session.
    :param deadhead_break: The break between the deadhead trips that are added and the passenger trips. Default is 5
    minutes.
    :return: The id of the rotation that was split, or None if no rotation could be split.
    """

    lowest_soc_event_q: Event = (
        session.query(Event)
        .join(Trip)
        .join(Rotation)
        .filter(Event.soc_end < 0)
        .filter(Event.event_type == EventType.DRIVING)
        .filter(Event.scenario == scenario)
        .order_by(Event.soc_end)
    )
    if lowest_soc_event_q.count() == 0:
        return None
    else:
        lowest_soc_event = lowest_soc_event_q.first()
    rotation_id = lowest_soc_event.trip.rotation_id

    # Actually split the rotation.
    rotation = session.query(Rotation).filter(Rotation.id == rotation_id).one()
    assert isinstance(rotation, Rotation)  # To make mypy happy

    # Find the total distance and the trips that are near the middle
    trip_distances = [trip.route.distance for trip in rotation.trips]
    total_distance = sum(trip_distances)
    cumulative_distances = np.cumsum(trip_distances)
    middle_trip_index = np.argmax(cumulative_distances > total_distance / 2)

    # Create the new rotations
    # The deadhead trips are time-shifted copies of the first/last trip of the original rotation
    first_trips = rotation.trips[:middle_trip_index]

    # Delete the original rotation
    saved_trips_sequence = rotation.trips.copy()
    rotation.trips = []
    session.delete(rotation)

    deadhead_after_start = first_trips[-1].arrival_time + deadhead_break
    deadhead_after_duration = (
        saved_trips_sequence[-1].arrival_time - saved_trips_sequence[-1].departure_time
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
        saved_trips_sequence[0].arrival_time - saved_trips_sequence[0].departure_time
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

    return rotation_id


def optimize_rotation(
    scenario_id: int, database_url: str, random_bias: float, power: float = 300
) -> List[Dict[str, int]]:
    """
    Optimizes a rotation by adding charging stations and splitting rotations. The optimiztaion is done by iteratively
    modifying the scenario until it is feasible (no SOC below 0% at the depot). The optimization terminates when either
    the scenario is feasible or the no further improvements can be made.

    :param scenario_id: The id of the scenario to optimize.
    :param database_url: The URL of the database to operate on.
    :param random_bias: A bias between 0 and 1. With 0 only rotation splitting is done, with 1 only charging stations
    are added.
    :param power: The power of the charging stations to be added. in kW. Default is 300 kW.
    :return: A tuple with two lists. The first list contains the ids of the charging stations that were added, the
    second list contains the ids of the rotations that were split.
    """
    logger = logging.getLogger(__name__)

    assert 0 <= random_bias <= 1

    # Run the simulation once to get the initial state.
    ds_wrapper = DjangoSimbaWrapper(database_url)
    ds_wrapper.run_simba_scenario(scenario_id, assign_vehicles=True)
    del ds_wrapper

    engine = create_engine(database_url)
    session = Session(engine)
    try:
        scenario = session.query(Scenario).filter(Scenario.id == scenario_id).one()
        current_value = number_of_rotations_below_zero(scenario, session)

        added_charging_stations: List[int] = (
            []
        )  # The ids of the charging stations that were added.
        split_rotations: List[int] = []  # The ids of the rotations that were split.

        step_results: List[Dict[str, int]] = (
            []
        )  # The results of each step in the optimization.

        while current_value > 0:
            # We need to seed our RNG each time???
            random.seed(os.urandom(32))
            # Choose a random action to take.
            random_value = random.random()
            # Write the random value and the bias to a debug log.
            logger.debug(f"Random value: {random_value}, random bias: {random_bias} (PID: {os.getpid()})")
            if random_value < random_bias:
                # Add a charging station.
                logger.debug(f"Adding a charging station to scenario {scenario_id}.")
                station_that_had_charger_added = add_charging_station(scenario, session)
                if station_that_had_charger_added is not None:
                    added_charging_stations.append(station_that_had_charger_added)
                else:
                    logger.warning(
                        f"No charging station could be added to scenario {scenario_id}."
                    )
                    break
            else:
                # Split a rotation.
                logger.debug(f"Splitting a rotation in scenario {scenario_id}.")
                rotation_that_was_split = split_rotation(scenario, session)
                if rotation_that_was_split is not None:
                    split_rotations.append(rotation_that_was_split)
                else:
                    logger.warning(
                        f"No rotation could be split in scenario {scenario_id}."
                    )
                    break

            # Run the consumption simulation to see if the scenario is feasible.
            session.commit()
            ds_wrapper = DjangoSimbaWrapper(database_url)
            ds_wrapper.run_simba_scenario(scenario.id, assign_vehicles=True)
            del ds_wrapper
            session.commit()
            session.expire_all()

            current_value = number_of_rotations_below_zero(scenario, session)
            step_results.append(
                {
                    "electrified_station_count": len(added_charging_stations),
                    "charging_station_ids": added_charging_stations,
                    "split_rotation_count": len(split_rotations),
                    "split_rotation_ids": split_rotations,
                    "rotations_below_zero": current_value,
                }
            )

            logger.info(
                f"Electrified {len(added_charging_stations)} stations and split {len(split_rotations)} rotation. {current_value} rotations below 0% SOC."
            )

    except Exception as e:
        logger.error(f"Error in scenario {scenario_id}: {e}")
        session.rollback()
        raise e
    finally:
        session.close()

    return step_results


def plot_traces(dfs: List[pd.DataFrame]) -> None:
    """
    Makes a 3D plot of the traces in the given dataframes.
    :param df: A dataframw with the following columns:
    electrified_station_count: int
    split_rotation_count: int
    rotations_below_zero: int
    :return: Nothing
    """
    fig = plt.figure()
    ax = fig.add_subplot(111, projection="3d")
    for df in dfs:
        ax.plot(
            df["electrified_station_count"],
            df["split_rotation_count"],
            df["rotations_below_zero"],
        )
    ax.set_xlabel("Electrified station count")
    ax.set_ylabel("Split rotation count")
    ax.set_zlabel("Number of rotations below 0% SOC")
    plt.savefig("rotation_optimization_trace.svg")
    plt.show()


def pareto_plot(dfs: List[pd.DataFrame]) -> None:
    """
    Makes a pareto plot of the results in the given dataframes.
    :param df: A dataframw with the following columns:
    electrified_station_count: int
    split_rotation_count: int
    rotations_below_zero: int
    :return: Nothing
    """
    fig = plt.figure()

    # Combine all dfs into one
    combined_df = pd.concat(dfs)

    # For each electrified station count, split rotation count pair, find the best result
    best_results = combined_df.groupby(
        ["electrified_station_count", "split_rotation_count"]
    )["rotations_below_zero"].min()
    combined_df = best_results.reset_index()

    # Identify the min and max of the result
    min_result = combined_df["rotations_below_zero"].min()
    max_result = combined_df["rotations_below_zero"].max()

    # Create a color map, mapping the result to a color
    color_map = plt.get_cmap("viridis")
    colors = []
    for possible_value in range(min_result, max_result + 1):
        colors.append(color_map(possible_value / max_result))

    # Plot the pareto front
    for i, row in combined_df.iterrows():
        if row["rotations_below_zero"] == 0:
            color = "green"
            shape = "x"
        else:
            color = colors[row["rotations_below_zero"] - min_result]
            shape = "o"
        plt.scatter(
            row["electrified_station_count"],
            row["split_rotation_count"],
            color=color,
            marker=shape,
        )

    plt.xlabel("Electrified station count")
    plt.ylabel("Split rotation count")
    plt.savefig("rotation_optimization_pareto.svg")
    plt.show()


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
        "--jobs",
        "-j",
        type=int,
        help="The number of jobs to be used for parallel processing. Set to 0 to use all available cores.",
        default=1,
    )
    parser.add_argument(
        "--paths",
        type=int,
        help="The number of paths to explore.",
        default=5,
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

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

    RESULTS_FILE_NAME = "results.json"

    if os.path.exists(RESULTS_FILE_NAME):
        with open(RESULTS_FILE_NAME, "r") as f:
            all_results = json.load(f)
    else:
        random_bias_range = np.linspace(0.0, 1.0, args.paths)

        # Performance hack: We do not clone the scenario, but instead clone the whole database for each path we explore.
        # This seems to be quite a bit faster.
        random_prefix = uuid.uuid4().hex
        new_database_urls = create_temporary_databases(
            args.database_url, args.paths, random_prefix
        )

        pool_args = []
        for i in range(random_bias_range.size):
            random_bias = random_bias_range[i]
            database_url = new_database_urls[i]
            pool_args.append((args.scenario_id, database_url, random_bias))
        with multiprocessing.Pool(random_bias_range.size) as pool:
            all_results = pool.starmap(optimize_rotation, pool_args)

        # Finally, clean up the temporary databases.
        delete_temporary_databases(args.database_url, args.paths, random_prefix)

        with open("results.json", "w") as f:
            json.dump(all_results, f)

    print("Done. Creating plots.")

    dataframes = [pd.DataFrame(result) for result in all_results]
    pareto_plot(dataframes)
    plot_traces(dataframes)
