UPDATE "Rotation" SET vehicle_id = NULL;
DELETE FROM "Event";
DELETE FROM "Vehicle";
DELETE FROM "AssocRouteStation" WHERE scenario_id != 1;
DELETE FROM "StopTime" WHERE scenario_id != 1;
DELETE FROM "Trip" WHERE scenario_id != 1;
DELETE FROM "Route" WHERE scenario_id != 1;
DELETE FROM "Line" WHERE scenario_id != 1;
ALTER TABLE "Station" DISABLE TRIGGER ALL;
ALTER TABLE "StopTime" DISABLE TRIGGER ALL;
DELETE FROM "Station" WHERE scenario_id != 1;
ALTER TABLE "Station" ENABLE TRIGGER ALL;
ALTER TABLE "StopTime" ENABLE TRIGGER ALL;
DELETE FROM "Rotation" WHERE scenario_id != 1;
DELETE FROM "Area" WHERE scenario_id != 1;
DELETE FROM "AssocRouteStation" WHERE scenario_id != 1;
DELETE FROM "AssocPlanProcess" WHERE scenario_id != 1;
DELETE FROM "Depot" WHERE scenario_id != 1;

