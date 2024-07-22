#!/usr/bin/env python
# coding: utf-8

from typing import Dict, Union, Tuple, List

import numpy as np
from eflips.model.general import VehicleType
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, joinedload
from eflips.model import Rotation, Trip, Station
from collections import Counter
from matplotlib import pyplot as plt
import os
from tqdm.auto import tqdm
from eflips.eval.input.prepare import rotation_info as prepare_rotation_info
from eflips.eval.input.visualize import rotation_info as visualize_rotation_info
from eflips.model.general import Scenario
import pytz
from datetime import date, datetime, timedelta
from eflips.eval.input.prepare import (
    geographic_trip_plot as prepare_geographic_trip_plot,
)
from eflips.eval.input.visualize import (
    geographic_trip_plot as visualize_geographic_trip_plot,
)
from eflips.opt.depot_rotation_optimizer import DepotRotationOptimizer

DATABASE_URL = "postgresql://arbeit:moose@localhost/eflips_testing"
SCENARIO_ID = 1

engine = create_engine(DATABASE_URL)
session = Session(engine)


# Save a geographic trip plot
df = prepare_geographic_trip_plot(1, session)
# map = visualize_geographic_trip_plot(df) TODO: RE-ENABLE
# map.save(os.path.join("src", "media", "pre_opt_geographic_trip_plot.html"))


# Also, prepare for a bar chart of how many rotations each depot supports
# Create a counter of the number of rotations per depot
pre_opt_counter = Counter()
dropped = df.drop_duplicates(subset=["rotation_id"])
grouped = dropped.groupby("originating_depot_name")
for depot, group in grouped:
    name_and_id = group[["originating_depot_name", "originating_depot_id"]].iloc[0]
    print(list(name_and_id))
    pre_opt_counter[depot] = len(group)

# # Put the new capacities into a variable
#
# - "Abstellfläche Mariendorf" will not be equipped with charging infrastructure, therefore it cannot serve as a depot for electrified buses
# - There will be a new depot "Köpenicker Landstraße" at the coordinates 52.4654085,13.4964867 with a capacity of 200 12m buses
# - There will be a new depot "Rummelsburger Landstraße" at the coordinates "52.4714167,13.5053889" with a capacity of 60 12m buses
# - There will be a new depot "Säntisstraße" at the coordinates "52.416735,13.3844563" with a capacity of 230 12m buses
# - There will be a new depot "Alt Friedrichsfelde" at the coordinates "52.5123056,13.5401389" with a capacity of 135 12m buses
# - The capacity of the existing depot "Spandau" will be 220 12m buses
# - The capacity of the existing depot "Indira-Gandhi-Straße" will be 300 12m buses
# - The capacity of the existing depot "Britz" weill be 140 12m buses
# - The capacity of the existing depot "Cicerostraße" will be 209 12m buses
# - The capacity of the existing depot "Müllerstraße" will be 155 12m buses
# - The capacity of the existing depot "Lichtenberg" will be 120 12m buses
#
# As for vehicle types, we will (at the current time) allow any vehicle type to be used at any depot.
#
# The new capacities should be specified as a dictionary containing the following keys:
# - "depot_station": Either the ID of the existing station or a (lon, lat) tuple for a depot that does not yet exist in the database
# - "capacity": The new capacity of the depot, in 12m buses
# - "vehicle_type": A list of vehicle type ids that can be used at this depot
# - "name": The name of the depot (only for new depots)
#
depot_list: List[Dict[str, Dict[str, Union[int, Tuple[float, float], List[int]]]]] = []
all_vehicle_type_ids = (
    session.query(VehicleType.id).filter(VehicleType.scenario_id == SCENARIO_ID).all()
)
all_vehicle_type_ids = [x[0] for x in all_vehicle_type_ids]

# "Abstellfläche Mariendorf" will have a capacity of zero
depot_list.append(
    {"depot_station": 103159411, "capacity": 0, "vehicle_type": all_vehicle_type_ids}
)

# "Betriebshof Spandau will hava a capacity of 220
# "Abstellfläche Mariendorf" will have a capacity of zero
depot_list.append(
    {"depot_station": 103109411, "capacity": 220, "vehicle_type": all_vehicle_type_ids}
)

# "Betriebshof Indira-Gandhi-Straße" will have a capacity of 300
depot_list.append(
    {"depot_station": 150518, "capacity": 300, "vehicle_type": all_vehicle_type_ids}
)

# "Betriebshof Britz" will have a capacity of 140
depot_list.append(
    {"depot_station": 80181, "capacity": 140, "vehicle_type": all_vehicle_type_ids}
)

# "Betriebshof Cicerostraße" will have a capacity of 209
depot_list.append(
    {"depot_station": 103109407, "capacity": 209, "vehicle_type": all_vehicle_type_ids}
)

# "Betriebshof Müllerstraße" will have a capacity of 155
depot_list.append(
    {"depot_station": 103109408, "capacity": 155, "vehicle_type": all_vehicle_type_ids}
)

# "Betriebshof Lichtenberg" will have a capacity of 120
depot_list.append(
    {"depot_station": 160522, "capacity": 120, "vehicle_type": all_vehicle_type_ids}
)

# "Betriebshof Köpenicker Landstraße" will have a capacity of 200
depot_list.append(
    {
        "depot_station": (13.4964867, 52.4654085),
        "name": "Betriebshof Köpenicker Landstraße",
        "capacity": 200,
        "vehicle_type": all_vehicle_type_ids,
    }
)

# "Betriebshof Rummelsburger Landstraße" will have a capacity of 60
depot_list.append(
    {
        "depot_station": (13.5053889, 52.4714167),
        "name": "Betriebshof Rummelsburger Landstraße",
        "capacity": 60,
        "vehicle_type": all_vehicle_type_ids,
    }
)

# "Betriebshof Säntisstraße" will have a capacity of 230
depot_list.append(
    {
        "depot_station": (13.3844563, 52.416735),
        "name": "Betriebshof Säntisstraße",
        "capacity": 230,
        "vehicle_type": all_vehicle_type_ids,
    }
)

# "Betriebshof Alt Friedrichsfelde" will have a capacity of 135
depot_list.append(
    {
        "depot_station": (13.5401389, 52.5123056),
        "name": "Betriebshof Alt Friedrichsfelde",
        "capacity": 135,
        "vehicle_type": all_vehicle_type_ids,
    }
)


# # Intialize the Optimizer
optimizer = DepotRotationOptimizer(session, SCENARIO_ID)
optimizer.get_depot_from_input(depot_list)

# The optimizer requires a "BASE_URL" environment variable to be set. This should be the URL of the API server for the openrouteservice instance that the optimizer will use.
os.environ["BASE_URL"] = "http://mpm-v-ors.mpm.tu-berlin.de:8080/ors/"
optimizer.data_preparation()
optimizer.optimize()
optimizer.write_optimization_results(delete_original_data=True)

assert optimizer.data["result"] is not None
assert optimizer.data["result"].shape[0] == optimizer.data["rotation"].shape[0]

fig = optimizer.visualize()
fig.write_html(os.path.join("src", "media", "sankey.html"))
fig.write_image(os.path.join("src", "media", "sankey.svg"))

# Save a geographic trip plot
# We will need to flush and expunge the session in order for the geom to be converted to binary
# (which is necessary for the plot to be created)
session.flush()
session.expunge_all()
post_df = prepare_geographic_trip_plot(1, session)
post_map = visualize_geographic_trip_plot(post_df)
post_map.save(os.path.join("src", "media", "post_opt_geographic_trip_plot.html"))


# Also, prepare for a bar chart of how many rotations each depot supports
# Create a counter of the number of rotations per depot
post_opt_counter = Counter()
dropped = post_df.drop_duplicates(subset=["rotation_id"])
grouped = dropped.groupby("originating_depot_name")
for depot, group in grouped:
    name_and_id = group[["originating_depot_name", "originating_depot_id"]].iloc[0]
    print(list(name_and_id))
    post_opt_counter[depot] = len(group)

# Make sure the pre- and post-optimization counters have the same keys
all_keys = set(pre_opt_counter.keys()).union(set(post_opt_counter.keys()))
for key in all_keys:
    if key not in pre_opt_counter:
        pre_opt_counter[key] = 0
    if key not in post_opt_counter:
        post_opt_counter[key] = 0

# Compare the number of rotations per depot before and after optimization in a barh plot
fig, ax = plt.subplots()
ind = np.arange(len(pre_opt_counter))
width = 0.4

# Turn the counter dictionaries into lists
keys = list(pre_opt_counter.keys())
pre_opt_values = [pre_opt_counter[key] for key in keys]
post_opt_values = [post_opt_counter[key] for key in keys]

ax.barh(ind - width / 2, pre_opt_values, width, label="Before Optimization")
ax.barh(ind + width / 2, post_opt_values, width, label="After Optimization")
ax.set_xlabel("Number of Rotations")
ax.set_ylabel("Depot")
ax.legend()

# Set the y-ticks to the depot names (keys)
ax.set_yticks(ind)
ax.set_yticklabels(keys)

plt.tight_layout()
plt.savefig(os.path.join("src", "media", "depot_rotations_opt.svg"))
plt.show()

session.rollback()
session.close()
