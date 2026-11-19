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
- The cloud parser now also reads the system SOC from the energy stream report.
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

### Changed

- Entities and the device are keyed by the inverter's serial instead of the
  config entry ID.
- Setting up a PowerOcean Single Phase is refused if the inverter reports a
  different model over Modbus.
- Diagnostics leave out the EcoFlow account and the cloud serial as well.
- A system with the EcoFlow cloud keeps PowerOcean Single Phase as its model in
  the options; a system on the cloud alone has no options yet.
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
