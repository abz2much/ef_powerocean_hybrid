# EF PowerOcean Hybrid

A Home Assistant custom integration for EcoFlow PowerOcean inverters. It reads
and controls them over local Modbus TCP, and for the PowerOcean Single Phase it
can also read the EcoFlow cloud. Domain `ef_powerocean_hybrid`.

## Status

Early development. Do not install it yet.

- **Modbus:** the local Modbus integration it is based on, under a new name, for
  every model that integration supports. It still contains that integration's
  battery control; the safeguards this integration needs before control can be
  used are not built yet.
- **EcoFlow cloud (PowerOcean Single Phase only):** set up beside Modbus or on its
  own. The cloud adds the values Modbus does not have, such as each battery
  pack's state of health, cycles and temperatures. If the inverter does not
  answer over Modbus at start, the cloud keeps the system running until it does.
- **One entity per value:** house, battery and grid power, state of charge,
  inverter voltage and grid frequency are one entity each. They show Modbus
  while it has a current reading, otherwise the cloud's, if the cloud has
  reported it within the last minute (two for voltage and frequency, judged on
  Home Assistant's clock); otherwise they are unavailable. The `source`
  attribute says which.
- **Energy totals keep one source:** the battery charged and discharged totals
  come from Modbus if the system was set up with Modbus, otherwise from the
  cloud ("(cloud)" in their name). They never switch source, never go down, and
  keep their last value while their source is away.
- **Not yet:** the cloud's live power, settings and scheduled tasks over MQTT.

## Setting up

Add the integration and choose how Home Assistant reaches the system:

- **Modbus TCP:** the inverter's address on the local network. For a PowerOcean
  Single Phase you can then add the EcoFlow cloud, by signing in with the
  EcoFlow account that has the inverter.
- **EcoFlow cloud only:** your EcoFlow account and the inverter's serial number
  (PowerOcean Single Phase only).

If the EcoFlow cloud later rejects the login, for example after a password
change, Home Assistant asks you to sign in again; the Modbus side keeps working.
A system set up through the cloud alone can get its Modbus address later through
**Reconfigure**.

## Requirements

- Home Assistant 2026.9 or newer.
- For Modbus: Modbus TCP switched on for the inverter in the EcoFlow Pro app.

## Credits

- Based on [EF-PowerOcean-TcpModbus](https://github.com/MaxGrmm/EF-PowerOcean-TcpModbus)
  by MaxGrmm (MIT licence).
- The cloud code comes from [powerocean_single_phase](https://github.com/abz2much/powerocean_single_phase)
  by abz2much, itself based on [powerocean](https://github.com/niltrip/powerocean)
  by niltrip (MIT licence).
- `EcoFlow_PowerOcean_Modbus.md` is MaxGrmm's register reference, kept from that
  project.
- See `NOTICE` for the exact versions used.

## Licence

MIT. See `LICENSE`.
