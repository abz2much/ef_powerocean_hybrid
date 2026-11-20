# EF PowerOcean Hybrid

A Home Assistant custom integration for EcoFlow PowerOcean inverters. It reads
and controls them over local Modbus TCP, and for the PowerOcean Single Phase it
can also read the EcoFlow cloud. Domain `ef_powerocean_hybrid`.

## Status

Early development. Do not install it yet.

- **Modbus:** the local Modbus integration it is based on, under a new name, for
  every model that integration supports. It still contains that integration's
  battery control, now with safeguards (see below). Control has not been tested
  on a PowerOcean Single Phase yet: do not switch Modbus Control on until the
  supervised checks on the inverter are done.
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

With Modbus, the options are those of EF-PowerOcean-TcpModbus, among them:

- **Calculation of grid energy:** for inverters whose grid import and export
  registers stay at 0 while Grid Power reads right. Grid import and export are
  then calculated from Grid Power, an estimate within a few percent of the
  meter. Off by default; a repairs issue offers it when the registers have read
  0 for a day while Grid Power showed energy flowing.
- **Alert verbosity:** how much is raised under Settings > Repairs. Essential
  (the default) raises only what you must act on. Readings adds readings that
  cannot be trusted, Detailed adds the connection's health, and Debug adds
  every warning and error the integration logs, the cloud's and MQTT's
  included. Each alert can be ignored; going back to Essential forgets the
  ignored ones.

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
- **Backup reserve:** one number. It shows the Modbus reading while Modbus has
  a current one, otherwise the cloud's, and is always changed over MQTT. It
  replaces the Modbus "App Backup Reserve" sensor, which shows the same value
  and returns when MQTT is turned off.
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

## Schedule card

The integration comes with a dashboard card for the scheduled tasks. Edit a
dashboard, choose **Add card** and pick **EF PowerOcean schedules**.

- A bar shows today's schedules over 24 hours (Use battery in green, Charge in
  blue; the one in use is outlined). Below is every schedule with its on/off
  switch; schedules that do not run today are faded.
- Tap a schedule to change or delete it, or **+ Add** for a new one. The form
  checks the rules as you type (no overlaps, at most 6, 30-minute steps, power
  up to Max schedule power) and sends the whole schedule only when you press
  **Save**. If the system refuses it, the form stays open with the reason.
- It needs the Extended connection (MQTT). Adding and changing also need Max
  schedule power in the options.
- After an update, reload the browser page (or clear the app's cache) if the
  card still looks old.

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

## Modbus Control safeguards

- Modbus Control always starts off when Home Assistant starts.
- It cannot be switched on while EF-PowerOcean-TcpModbus, or a second entry of
  this integration, has its own Modbus Control on for the same inverter
  address, and it is switched off here if that one's control comes on later.
  With its control off, the other integration only reads and is left alone. The address is compared as entered: an IP address
  in one and a host name in the other are not recognised as the same inverter.
- The heartbeat that keeps the inverter following commands beats only while the
  control logic runs on recent polls; otherwise the inverter returns to its app
  settings within 60 seconds.
- Switching off, unloading or stopping Home Assistant first releases the
  command (setpoint 0, default method); switching on first clears what the
  inverter held.
- Every control word is checked for the off-grid and shutdown bits, and the
  setpoint is read back each poll: if the inverter holds something else on two
  polls in a row, control is switched off and the log says why.
- Commands go out one at a time, and a clock that steps back does not stall
  the control timers.

## Troubleshooting

- **Repairs shows "The inverter stopped answering Modbus" or "is not
  reachable":** the inverter has not answered over Modbus for at least 5
  minutes. "Stopped answering" means the port takes connections but sends no
  replies; "not reachable" means nothing answers on the port. Check that the
  inverter is powered and has the same IP address, and use **Reconfigure** if
  it moved. In a few cases a firmware update has switched Modbus TCP off: ask
  your installer to enable it again in the EcoFlow Pro app. With the EcoFlow
  cloud set up, the cloud's values stand in meanwhile. The issue clears with
  the next good read.
- **Repairs shows "Modbus is disabled on the inverter":** the inverter answers,
  but every value reads 0. Ask your installer to enable Modbus TCP.
- **Repairs shows "stopped updating its readings":** the inverter answers, but
  most readings stand still. Restart the inverter as its manual describes.

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
