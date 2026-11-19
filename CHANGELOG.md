# Changelog

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
