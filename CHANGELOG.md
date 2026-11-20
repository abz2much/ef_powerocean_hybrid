# Changelog

## [0.3.0] - Unreleased

### Added

- The EcoFlow cloud HTTP API, response parser and anonymiser from
  powerocean_single_phase v1.0.1 (commit `45287c9`), with their tests. Not
  used by the integration yet.
- A real PowerOcean Single Phase (cloud model 86) response as a test fixture,
  anonymised, with energy totals rounded and the schedule and 5-minute
  history removed.
- The entity catalogue for the PowerOcean Single Phase: one entity per value,
  each with its Modbus and cloud source. Not used by the integration yet.
- A cloud connection beside Modbus in one entry (set up in the entry's data for
  now; the setup form follows). The cloud is read every 10 seconds. If the
  cloud is down, or rejects the login, the entry still loads, Modbus keeps
  working, and a rejected login asks to sign in again.
- The cloud's own values of a PowerOcean Single Phase as sensors, on the
  inverter's device, and each battery pack as its own device under it. They keep
  the cloud's units and signs. Packs that appear later are added.
- Setup asks how to reach the system: Modbus, with the EcoFlow cloud as an
  option for a PowerOcean Single Phase, or the cloud alone. The cloud login is
  checked against the inverter's serial. If the cloud later rejects the login,
  Home Assistant asks to sign in again.
- A system set up through the cloud alone has the cloud's sensors only; the
  battery action is refused with a clear message, as it needs Modbus.
- With the EcoFlow cloud set up, the system loads even when the inverter does
  not answer over Modbus at start: the cloud's values carry on, and the entry
  reloads with Modbus as soon as the inverter answers (checked every 30 seconds).
  Without the cloud, setup retries as before.
- The EcoFlow app's MQTT connection, from powerocean_single_phase v1.0.1, as
  the option "Extended connection (MQTT)", off by default. It reads the
  firmware of the modules: the smart meter's shows on its device page, the
  others (such as the EMS) in the diagnostics. If it fails to start, it stays
  off until the entry is reloaded. If the cloud was down when Home Assistant
  started, it starts once with the cloud's first successful update.
- The option "Live power values (MQTT)", off by default: asks the system for its
  power values every 20 seconds, as the EcoFlow app does while it is open.
  House, battery and grid power then come from it about every 5 seconds
  whenever Modbus has no current reading, and so do the cloud's own solar
  power, PV strings and energy flows. A dual installation's stream is ignored,
  as in powerocean_single_phase.
- With the MQTT option: the app's settings "Backup reserve", "Surplus solar
  energy delivery" and "Battery save mode". The system confirms each change;
  without a confirmation within 10 seconds Home Assistant shows an error.
- With the MQTT option: the app's scheduled tasks. A switch per schedule, named
  after what it does (for example "Charge 02:00-06:00 3000 W"), an "Active
  schedule" sensor, and the actions `add_schedule`, `update_schedule` and
  `delete_schedule`. The list is read when MQTT connects and every minute, so
  a change made in the app shows within a minute. The same checks as the
  EcoFlow app: at most 6 schedules, no overlaps, times in steps of 30 minutes.
- The MQTT connection logs a disconnect as a warning, a connect, each command
  sent and each reply with its number (debug), and paho's own log; a command
  the system does not confirm is a warning that says whether MQTT was still
  connected. A message that cannot be read is logged instead of stopping
  paho's thread.
- A dashboard card for the scheduled tasks, served with the integration ("EF
  PowerOcean schedules" in the card picker): a bar of today's schedules, the
  list with on/off switches, and a form to add, change and delete them that
  checks the rules as you type and sends only on Save. The form closes at
  once; the card says what is being done and, if the system refuses it, why. The Active schedule
  sensor tells the card the max schedule power (attribute
  `max_schedule_power`, not recorded).
- The option "Max schedule power (W)": the highest power a schedule may use.
  Without it, schedules cannot be added or changed. There is no default.
- The battery packs' firmware from the cloud on their device pages.
- Diagnostics have a cloud part, as powerocean_single_phase gives it: the
  connection state, whether MQTT is on, the modules' firmware and the last
  cloud response, with every serial replaced by a placeholder.

- From EF-PowerOcean-TcpModbus v2.10.0:
  - A repairs issue when the inverter has not answered over Modbus for 5
    minutes, naming whether nothing answers on the port or the port takes
    connections but sends no Modbus replies (as after some firmware updates).
    With the EcoFlow cloud set up, the 30-second checks while the cloud carries
    the system keep counting, so the issue also shows then. Setup and
    Reconfigure name a silent inverter too.
  - A logbook entry and an event when the inverter's firmware changes, for the
    inverter's device.
  - Reading checks per reading: frozen readings are judged one by one, a
    stalled inverter raises a repairs issue, a Reading Check diagnostic sensor
    shows each finding, and Coordinator Status shows "Readings stalled".
    Readings found wrong come from the EcoFlow cloud where it is connected.
  - The option "Alert verbosity" (Modbus only): from Essential, the default, up
    to Debug, which raises every warning and error the integration logs,
    including the cloud's and MQTT's. Each alert can be ignored. The Modbus
    client counts its writes and reconnects for it.
  - The option "Calculation of grid energy" (Modbus only), and a repairs issue
    that offers it when the inverter's grid energy registers stay at 0 for a
    day while Grid Power shows energy flowing.

### Changed

- Its own icon: a battery joined to the house (local Modbus) and the cloud,
  instead of the icon copied from EF-PowerOcean-TcpModbus.
- The version in the manifest is 0.3.0.
- Entities and the device are keyed by the inverter's serial instead of the
  config entry ID.
- Setting up a PowerOcean Single Phase is refused if the inverter reports a
  different model over Modbus.
- Diagnostics leave out the EcoFlow account and the cloud serial as well.
- House, battery and grid power, state of charge, inverter voltage and grid
  frequency of a PowerOcean Single Phase with the cloud are one entity each:
  Modbus while it has a current, trusted reading, otherwise the cloud's value
  if Home Assistant saw it reported within 60 seconds (120 for voltage and
  frequency); otherwise unavailable. Their `source` attribute (not recorded)
  says which. Their entity IDs stay the same.
- The battery energy totals stay on one source, fixed when the system is first
  set up: Modbus, or the cloud for a system set up through the cloud alone
  ("Battery charged total (cloud)", "Battery discharged total (cloud)"). Cloud
  totals never go down and keep their last value while the cloud is away;
  Modbus totals keep theirs while the system runs on the cloud.
- A lifetime total whose first reading on a new installation is 0 shows its
  last known value, or unknown, until a real value follows, instead of 0.
- A system with the EcoFlow cloud keeps PowerOcean Single Phase as its model in
  the options. A system on the cloud alone has the three MQTT options only.
- Reconfigure adds a Modbus address to a system set up through the cloud alone,
  after checking the inverter's serial and model.
- The cloud is only added when the inverter's serial could be read over Modbus.
- Reconfigure can add the EcoFlow cloud to a PowerOcean Single Phase set up with
  Modbus only, with the same login, serial and model checks as setup. Every
  entity keeps its ID, name, labels and area.
- An entry keyed by address that later reads the inverter's serial moves its
  entities and device to the serial, keeping their IDs, labels and areas.
- A cloud value that is text where a number or time belongs (such as "FALSE")
  shows as unavailable instead of raising an error.
- While running on the cloud, the entry reloads only when this inverter answers
  over Modbus, not when any device at the address accepts a connection.
- Adding an inverter that is already set up through the cloud alone asks you to
  use Reconfigure instead.
- The LED brightness is one number: it reads and writes over Modbus while
  Modbus has a current reading, otherwise over the cloud (needs the MQTT
  option). On a system set up through the cloud alone it exists only with the
  MQTT option.
- The cloud's text values show as powerocean_single_phase shows them: in
  lower case, with a readable name ("Charging", "Self-powered", "Running")
  for the pack state, charge state, BMS run state and module state, and the
  inverter's operating mode, run state, feed-in mode, online and grid state.
- The cloud sensors powerocean_single_phase has switched off by default are
  there too, switched off: Wi-Fi, Ethernet and 4G state, system name, and four
  values of an energy management report (feed power cap, PCL power base,
  current SOC, start state), whose meaning is unclear.
- Wi-Fi, Ethernet and 4G state show "Connected" for code 0 and "Not
  connected" for code 2, as seen on a real system; other codes show as the
  number. powerocean_single_phase shows only the number.
- The Heartbeat update time sensor is off by default, unlike in
  powerocean_single_phase: its change every 30 seconds filled the activity
  log.
- The cloud's sensors are on or off by default as in powerocean_single_phase:
  active power L1, PV insulation resistance, online, and each pack's BMS run
  state, module state, balance state and update time are now on; each pack's
  highest and lowest MOSFET temperature are now off. Entities already in Home
  Assistant keep their setting.
- With the MQTT option, the backup reserve is one number: it shows the Modbus
  reading (register 40536) while Modbus has a current one, otherwise the
  cloud's, and is always written over MQTT. The Modbus "App Backup Reserve"
  sensor, which shows the same value, is removed then, and comes back when
  MQTT is turned off.
- The cloud's state of charge comes from the EMS change report, as in
  powerocean_single_phase, and counts as current while the cloud's updates
  succeed (up to 60 seconds after the last one).
- The cloud's battery energy totals may rise only as fast as the battery's
  power limit allows, as the Modbus totals do, instead of a fixed 10 kW: the
  Modbus limits with Modbus, otherwise limits sized from the packs the cloud
  reports.
- The inverter's firmware comes from Modbus and is shown on the inverter's
  device only; it no longer overwrites the firmware of other devices.

### Control safeguards

- Modbus Control always starts off when Home Assistant starts or the entry
  loads; switch it on yourself. Before, it came back as it was, and switching
  it off was saved only 30 seconds later, so a crash in that time turned it
  back on. An old "Modbus Control" setting in the entry's options is ignored.
- Modbus Control cannot be switched on while EF-PowerOcean-TcpModbus, or a
  second entry of this integration, is loaded for the same inverter address:
  two controllers would fight over it. If one is loaded while control is on,
  the next poll switches control off, with a warning in the log.
- The heartbeat that keeps the inverter following Modbus commands now beats only
  while the control logic (guards, expiry, modes) has run on a poll within two
  poll intervals plus 30 seconds. Before, it beat on its own timer whatever
  happened to the polls, so a bug that stopped the logic would leave the
  inverter on its last command, such as charging from the grid, for as long as
  Home Assistant ran. Now the inverter hands back to its app settings within
  60 seconds, with a warning in the log.

### Fixed

- With Modbus Control off, a reconnect no longer makes the next poll write a
  control command to the inverter. An inverter that left the write unanswered
  dropped the connection each time. From upstream EF-PowerOcean-TcpModbus
  `e399e89` (v2.10.0).
- MQTT asks for the schedules, and with live power the energy stream, once its
  connection is up, and again after a reconnect. Before, the first requests
  went out before the connection was up and were lost, with a warning, so the
  schedules showed only a minute later. While MQTT reconnects, the timed
  requests wait instead of warning each time.
- MQTT now stops when Home Assistant stops. Before, a message arriving during
  the shutdown logged "Event loop is closed".
- A disconnect the integration asked for (an unload or Home Assistant's stop)
  is logged at debug level, not as a warning; a dropped connection still is.
- A command is refused at once while MQTT is reconnecting, with a message to
  try again in a minute. Before, paho held it and sent it on reconnect, long
  after Home Assistant reported it failed: a backup reserve change could then
  set back a charge limit changed in the app meanwhile, and a retried schedule
  was added twice.
- A schedule the system reports with a time past 23:59 is still not shown, but
  its id stays taken, it counts toward the 6 schedules, and a new schedule from
  its start on counts as overlapping it. It is warned about once, not with
  every list read.
- The schedule card says why Save waits while another change is being sent,
  and a form opened meanwhile can save once that change is done. The card's
  editor no longer closes its open list with every update.
- Diagnostics show whether MQTT is connected now (`mqtt_connected`), beside
  whether it was started.
- The text of the option "Live power values (MQTT)" says that house, grid and
  battery power come from it only while Modbus has no current reading.
- The battery SOC from the cloud counts only while the system still checks in
  (its heartbeat time changes), as voltage and frequency already do. Before,
  it counted while the cloud answered, which keeps the last SOC of a system
  that went quiet. After a restart, a system without Modbus shows its SOC once
  the heartbeat has changed once, within about two minutes.
- An unload before the cloud's first answer no longer leaves the wait for it
  behind.
- The EcoFlow account's user ID no longer shows in the log. paho named its
  network thread after the MQTT client ID, which ends in the user ID, and Home
  Assistant prints the thread name with every log line from that thread; the
  thread is now named "ef_powerocean_hybrid MQTT". In paho's debug log the user
  ID in the client ID is masked.

## [0.2.0] - Unreleased

### Changed

- Single-phase inverters (PowerOcean Single Phase, Ocean 2 Single Phase, Ocean
  2 Plus Single Phase) no longer get the L2 and L3 voltage and current
  sensors, for the inverter and the grid. They always read 0 there.
- On the PowerOcean Single Phase, the PV string 3 sensors start disabled,
  because they read 0 on a unit with two strings. They can be enabled.

### Removed

- The German translation. The integration is in English only.
- `scripts/control_feature_scan.py`, which writes to the inverter outside the
  integration, including the feed-in settings.

## [0.1.0] - Unreleased

### Added

- First version: [EF-PowerOcean-TcpModbus](https://github.com/MaxGrmm/EF-PowerOcean-TcpModbus)
  by MaxGrmm at commit `431a806` (v2.9.5 with the entity availability fix
  #219), renamed to the domain `ef_powerocean_hybrid` so it can run beside
  the original. Its storage, events, actions and control test reports use
  the new name.
- Home Assistant 2026.9 or newer is required.

### Changed

- A lost connection is retried after 0, 5, 10 and 20 seconds, then every 30
  seconds instead of every 2 minutes. On the connection Home Assistant shares,
  requests time out after 3 seconds instead of 10. From upstream commit
  `8569232`.

### Fixed

- On the connection Home Assistant shares, a request that times out now drops
  the connection, and the next poll opens a new one. Before, a connection
  whose packets a firewall dropped stayed open without answers. From upstream
  commit `8569232`.
