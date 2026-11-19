# EF PowerOcean Hybrid

A Home Assistant custom integration for the EcoFlow PowerOcean Single Phase.
It will read and control the system over local Modbus TCP and over the
EcoFlow cloud. Domain `ef_powerocean_hybrid`.

## Status

Early development. Do not install it yet.

This version is the local Modbus integration it is based on, under a new
name. It still contains that integration's battery control, and the
safeguards this integration needs before control can be used are not built
yet. Cloud support is not added yet.

## Requirements

- Home Assistant 2026.9 or newer.
- Modbus TCP switched on for the inverter in the EcoFlow Pro app.

## Credits

- Based on [EF-PowerOcean-TcpModbus](https://github.com/MaxGrmm/EF-PowerOcean-TcpModbus)
  by MaxGrmm (MIT licence). See `NOTICE` for the exact base.
- `EcoFlow_PowerOcean_Modbus.md` is MaxGrmm's register reference, kept from
  that project.

## Licence

MIT. See `LICENSE`.
