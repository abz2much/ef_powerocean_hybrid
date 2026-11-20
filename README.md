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
- **MQTT (PowerOcean Single Phase with the cloud):** the EcoFlow app's
  connection, as in powerocean_single_phase: live power values, the app's
  settings and its scheduled tasks. Off until you turn it on in the options.

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

## Options

For a system with the EcoFlow cloud, the options have three more fields, as in
powerocean_single_phase. All are off or empty at first.

- **Extended connection (MQTT):** connects to the EcoFlow cloud like the EcoFlow
  app. Needed for everything below. If it fails to start, it stays off until
  you reload the entry.
- **Live power values (MQTT):** asks the system for its power values every 20
  seconds, as the app does while it is open. House, battery and grid power then
  update about every 5 seconds while Modbus has no current reading; the cloud's
  own solar power, PV strings and energy flows update as often. Not for a dual
  installation, as in powerocean_single_phase.
- **Max schedule power (W):** the highest power a scheduled task may use.
  Without it, schedules cannot be added or changed.

With MQTT on you get:

- **Settings:** Backup reserve, Surplus solar energy delivery and Battery save
  mode, as in the app. The system confirms each change; without a
  confirmation Home Assistant shows an error.
- **LED brightness:** one number. With Modbus it reads and writes over Modbus
  while Modbus has a current reading, otherwise over MQTT.
- **Scheduled tasks:** a switch per schedule, named after what it does, and an
  "Active schedule" sensor. The actions `add_schedule` (aimed at the inverter
  device), `update_schedule` and `delete_schedule` (aimed at a schedule
  switch) change them. The system does not report changes, so the list is read
  every minute: a change made in the EcoFlow app shows within a minute. As in
  the app: at most 6 schedules, no overlaps, times in steps of 30 minutes.

Turning MQTT off removes these entities, apart from the LED brightness on a
system with Modbus.

The inverter's firmware version comes from Modbus. A system set up through the
cloud alone shows none on the inverter's device; the firmware of the EMS and
the other modules is in the diagnostics.

## Moving from powerocean_single_phase

Not ready yet; the integration is still in development. When it is:

1. Note the entity IDs your dashboards, automations and scripts use.
2. Remove `powerocean_single_phase` before setting up this integration. Entity
   IDs are made from the device name, and Home Assistant adds `_2` to any ID
   that is still taken. Schedule switches are named `schedule_<id>` after the
   device name, as in `powerocean_single_phase`.
3. Give the inverter's device the same name it had, so the IDs match.
4. Change automations and scripts that call `powerocean_single_phase.add_schedule`,
   `update_schedule` or `delete_schedule` to the `ef_powerocean_hybrid` actions.
5. Turn on the same options (MQTT, live power, max schedule power).

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
