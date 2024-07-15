# Simulating an electrified network

Now that the schedule data has been imported, it may need to be cleaned up a little and then it can be simulated. This chapter is structured as follows:

- In [Fixing up the Schedule Data](./31_fixing_data.md) selecting the correct vehicle types and depots will be discussed. If you are confident that your data contains only what you want to simulate or just want to run a simulation first to check what data you have, you can skip this step.
- In [Assigning Schedules to Depots using `eflips-opt`](./32_eflips_opt.md), the `eflips-opt` package, which can create new depot-rotation assignments is demonstrated. If you only have one depot or do not wish to change the depot to which your vehicle is assigned, you can skip this step.