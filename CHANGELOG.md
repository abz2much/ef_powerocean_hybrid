# Changelog

## 1.0.0

This is the first release. It combines two integrations into one, with its own domain
`ef_powerocean_hybrid`, so it can run beside both while you move over:

- [EF-PowerOcean-TcpModbus](https://github.com/MaxGrmm/EF-PowerOcean-TcpModbus)
  v2.10.0 by MaxGrmm, for Modbus reading and battery control.
- [powerocean_single_phase](https://github.com/abz2much/powerocean_single_phase)
  v1.0.1, for the EcoFlow cloud, its MQTT connection and the app's scheduled
  tasks, on the PowerOcean Single Phase.

Home Assistant 2026.9 or newer is required. The integration is in English only.

### Added to EF-PowerOcean-TcpModbus

- A PowerOcean Single Phase can use the EcoFlow cloud, set up beside Modbus or
  on its own. The cloud adds the values Modbus does not have, and each battery pack as its
  own device with its health, cycles, temperatures and firmware. A system with
  the cloud loads even when the inverter does not answer over Modbus, and
  reloads with Modbus once it answers.
- Each value that both sources report is one entity: house, battery and grid
  power, state of charge, inverter voltage and grid frequency. It shows Modbus
  while Modbus has a current reading and the cloud otherwise, and its `source`
  attribute says which. The battery energy totals keep the source the system
  was set up with.
- The new options are Extended connection (MQTT), Live power values (MQTT) and
  Max schedule power. With MQTT on, you can change the app's backup reserve,
  surplus solar energy delivery and battery save mode, and LED brightness goes
  over MQTT when Modbus has no reading. The app's scheduled tasks become
  switches, with an Active schedule sensor and the actions `add_schedule`,
  `update_schedule` and `delete_schedule`.
- A dashboard card, "EF PowerOcean schedules", shows and edits the scheduled
  tasks.
- The new Battery Mode **Schedules** runs the app's schedules while Modbus
  Control is on. A Charge schedule charges at its power; a Use battery schedule, a
  gap or a switched-off schedule runs Automatic. It is selected when Modbus
  Control is switched on with the cloud set up. Its list comes from MQTT, else
  the cloud's regular data, else a copy saved on disk. Control Status shows the
  schedule and the source.
- Reconfigure can add the cloud to a system set up with Modbus, or a Modbus
  address to a system set up through the cloud, keeping every entity's ID,
  name, labels and area.
- Diagnostics have a cloud part. Every serial number is replaced and the
  EcoFlow account is left out.

### Changed from EF-PowerOcean-TcpModbus

- Modbus Control always starts off when Home Assistant starts or the entry
  loads.
- Modbus Control cannot be switched on while EF-PowerOcean-TcpModbus, or a
  second entry of this integration, has its own Modbus Control on for the same
  address, and is switched off if that one's control comes on later.
- Switching Modbus Control off, unloading the entry or stopping Home Assistant
  releases the command first (the default method and setpoint 0), so the
  inverter does not carry on with it for up to 60 seconds.
- Every control word is checked for the off-grid and shutdown bits before it is
  written.
- The power setpoint is read back on every poll. If the inverter holds a
  different value on two polls in a row, Modbus Control is switched off and the
  log says what it held.
- Commands go out one at a time, and a clock that steps back no longer stalls
  the control timers.
- Entities and the device are keyed by the inverter's serial number.
- Single-phase inverters no longer get L2 and L3 voltage and current sensors,
  which always read 0. On the PowerOcean Single Phase, the PV string 3 sensors
  start disabled.
- The German translation and `scripts/control_feature_scan.py`, which wrote to
  the inverter outside the integration, are removed.
- The integration has its own icon.

### Changed from powerocean_single_phase

- MQTT asks for the schedules and the live power stream once its connection is
  up, and again after a reconnect. Before, the first requests could be lost.
- A command is refused while MQTT is reconnecting. Before, it was held and sent
  on reconnect, long after Home Assistant had reported it failed.
- MQTT stops when Home Assistant stops.
- The MQTT connection now logs: a dropped connection is a warning, and connects,
  commands and replies are logged at debug level. The user ID in paho's debug
  log is masked.
- The EcoFlow user ID no longer shows in the log. paho named its thread after
  the MQTT client ID, which ends in the user ID; the thread now has its own
  name.
- A schedule the system reports with a time past 23:59 is no longer shown as a
  switch. It still counts toward the 6 schedules and for overlaps, and the log
  warns about it once.
- Wi-Fi, Ethernet and 4G state show "Connected" or "Not connected" for the codes
  seen on a real system. The Heartbeat update time sensor is off by default.
