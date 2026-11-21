# Battery control

Battery control works over Modbus and comes from
[EF-PowerOcean-TcpModbus](https://github.com/MaxGrmm/EF-PowerOcean-TcpModbus).
This integration adds the Schedules mode and a few safeguards. Much of this page
follows that project's documentation.

## Modbus Control

Battery control is off until you turn on the **Modbus Control** switch. While it
is on, the EcoFlow app cannot control the system. When you turn it off, the
inverter goes back to the app within about 60 seconds, and Control Status shows
**Handing back to the app** until then.

Modbus Control always starts off when Home Assistant starts or the integration
loads, so you switch it on yourself each time.

## Battery modes

The **Battery Mode** select chooses what the battery does.

| Mode               | What happens                                                                      |
| ------------------ | --------------------------------------------------------------------------------- |
| Automatic          | Normal self-consumption, as the app runs it                                       |
| Hold battery       | The battery stays idle, and surplus solar is exported                             |
| Charge battery     | The battery charges at the set power, from the grid if solar falls short          |
| Discharge battery  | The battery discharges at the set power, and the rest is exported                 |
| Export to grid     | The grid export stays at the set power, and the battery covers the difference     |
| Import from grid   | The grid import stays at the set power, and the battery takes what is left        |
| Export solar first | Surplus solar is exported up to the Solar Export Limit before the battery charges |
| Schedules          | The battery follows the EcoFlow app's scheduled tasks (see below)                 |

Each mode's power has its own number entity, and only the selected mode's power
is sent. Charge or Discharge at 0 W holds the battery.

EcoFlow does not offer a hard battery power limit over Modbus, so the battery
does not always match the set power exactly. It can drift a few hundred watts,
and Control Status counts a battery within 500 W, or 15 % of the set power if
that is more, as on target.

## Schedules

The Schedules mode needs Modbus and the EcoFlow cloud set up together. While
Modbus Control is on, the inverter does not run the app's
[scheduled tasks](schedules.md) itself, so Home Assistant runs them:

- During a Charge schedule, the battery charges at the schedule's power. The
  Charge battery power you set for that mode is kept as it is.
- During a Use battery schedule, the battery runs Automatic at any power. Modbus
  on these inverters has no way to cap the discharge.
- Between schedules, or during a schedule that is switched off, the battery runs
  Automatic.

Switching Modbus Control on selects Schedules on a system with the cloud.
Choosing another mode, or sending `set_battery_command` with another mode,
stops following the schedules. A command with an expiry that is sent while
Schedules is selected returns to Schedules when it runs out.

The schedule list comes from MQTT while MQTT is connected. Otherwise it comes
from the cloud's regular data, and otherwise from a copy saved on disk, so
Schedules keeps working while the cloud is away. A schedule is not run if Home
Assistant cannot be sure when it should run: if its repeat or type is unknown,
if its start or end is past 23:59, or if it repeats on chosen days and the list
came from the cloud's regular data, which has no reliable days. The log warns
once about each one.

Control Status shows `following_schedules` (whether Schedules is in charge),
`schedule` (the schedule running now), `schedule_source` (`mqtt`, `http` or
`saved`) and `schedules_not_run`.

If Home Assistant stops, the inverter runs the same schedules itself within
about 60 seconds.

## Guards

Two number entities keep the battery within limits in every mode:

| Guard           | Stops the battery from       | Off at |
| --------------- | ---------------------------- | ------ |
| Charge Limit    | charging above this level    | 100 %  |
| Battery Reserve | discharging below this level | 0 %    |

The guards only ever stop the battery; they never reverse it. In Automatic, the
battery can still power the house under the Charge Limit and still charge from
solar under the Battery Reserve. A guard releases once the charge has moved 5 %
back. The inverter has no charge limit setting, so while a guard is on, the
integration runs self-consumption itself and blocks the forbidden direction.
The battery may move the wrong way for a few seconds before it is caught.

The Battery Reserve is native or emulated, as its `implementation` attribute
shows:

- A **native** reserve (PowerOcean Three Phase) is the backup reserve of the
  EcoFlow app, written to the inverter, which keeps it whatever runs it.
- An **emulated** reserve (every other model) is kept by the integration while
  Modbus Control is on. The app's own backup reserve still applies whenever the
  integration is not in control, and in Automatic the inverter stops
  discharging at the app's reserve by itself.

**Effective Battery Reserve** shows which reserve holds right now. With an
emulated reserve, the switch **Charge to Battery Reserve** also charges the
battery from the grid up to the reserve, at the Charge battery power, as the
app's backup reserve does.

## Control Status

| Control Status                           | Meaning                                                                     |
| ---------------------------------------- | --------------------------------------------------------------------------- |
| Modbus Control off                       | Modbus Control is off, or control was lost                                  |
| Handing back to the app                  | Modbus Control was turned off, and the app takes over                       |
| Automatic                                | The inverter runs normal self-consumption                                   |
| Active                                   | The selected mode is working                                                |
| Ramping                                  | The inverter has not reached the target yet                                 |
| Limited by inverter                      | The inverter goes the other way or past the target                          |
| Charge limit reached                     | The Charge Limit is stopping the battery                                    |
| Battery reserve reached                  | The Battery Reserve is stopping the battery                                 |
| Charging to battery reserve              | Charge to Battery Reserve is charging up to it                              |
| Below solar export limit                 | Export solar first is exporting the whole surplus                           |
| Battery full, hold not needed            | A hold is selected, but the battery is full, so the inverter runs Automatic |
| Battery full, target unreachable         | The target needs the battery to charge, but it is full                      |
| Battery empty, target unreachable        | The target needs the battery to discharge, but it is empty                  |
| Export limit reached, target unreachable | The target needs more export than the export limit lets out                 |
| Not accepted by inverter                 | The inverter does not report the command, even after resending it           |
| Off-grid                                 | Grid outage; control steps aside until the grid is back                     |
| Battery disconnected                     | The battery dropped off; control steps aside until it is back               |
| Readings inconsistent                    | The readings contradict each other; control steps aside                     |
| Inverter fault                           | The inverter reports a fault or a stop                                      |
| Control test                             | The control test has the inverter; the mode resumes after it                |

Every poll is checked for readings that cannot be trusted, such as voltages
that stopped changing or a state of charge that drops to 0 % at once. While a
check fails, control hands the inverter its own self-consumption, and the
**Reading Check** diagnostic sensor says which check failed. A reading that a
check shows to be wrong is unavailable until it makes sense again.

## Commanding the battery from automations

The `set_battery_command` action sets the mode, its power and the Charge Limit
in one step:

```yaml
action: ef_powerocean_hybrid.set_battery_command
data:
  device_id: <the inverter device>
  mode: charge_battery
  power: 3000
  charge_limit_soc: 80
  expire_in: 900
```

- `power` is needed for Charge, Discharge, Export and Import. For Export solar
  first it is optional and sets the Solar Export Limit. With `mode: schedules`,
  power, charge limit and expiry are ignored.
- With `expire_in` (60 to 86400 seconds), the mode goes back to Automatic, or to
  Schedules if Schedules was selected before, unless the command is sent again
  in time. Control Status shows the deadline as `expires_at`, and the
  `ef_powerocean_hybrid_command_expired` event fires when it passes. Its
  `returned_to` field says which mode followed. Choosing a mode by hand cancels
  the expiry.
- Only `mode: automatic` works while Modbus Control is off.

Run one controller at a time, as the action does not arbitrate between them.

## Testing the controls after a firmware update

EcoFlow firmware updates can change which control methods the inverter follows.
The `run_control_test` action checks each method in both directions and writes
a report:

```yaml
action: ef_powerocean_hybrid.run_control_test
data:
  device_id: <the inverter device>
  confirm: true
  power: 1500
```

The test takes about 10 minutes. The battery charges and discharges briefly at
the test power, and power flows to and from the grid. At the end, the inverter
goes back to the EcoFlow app for a minute, and then to Modbus Control if it was
on. Follow the **Control Test** diagnostic sensor, or wait for the notification.
The report is in the diagnostics download, and in the file named in the
sensor's `report_file` attribute. `cancel_control_test` stops a run.

A run is most useful with the battery well above its reserve and below 94 %.

## Safeguards

This integration adds these safeguards to the control code it is based on:

- Modbus Control cannot be switched on while EF-PowerOcean-TcpModbus, or a
  second entry of this integration, has its own Modbus Control on for the same
  inverter address. If that one's control comes on later, control is switched
  off here. An integration with its control off only reads and is left alone.
  The address is compared as entered, so an IP address in one and a host name
  in the other are not recognised as the same inverter.
- Switching Modbus Control off, unloading the integration or stopping Home
  Assistant first releases the command, with the default method and a setpoint
  of 0. The inverter then does not carry on with the last command for up to 60
  seconds.
- Every control word is checked before it is written, and one that would switch
  the system off-grid or shut it down is refused.
- The power setpoint is read back on every poll. If the inverter holds a
  different value on two polls in a row, Modbus Control is switched off and the
  log says what the inverter held.
- Commands go out one at a time, so a poll and an action cannot mix their
  writes.
- If the clock steps back, the control timers keep running instead of waiting
  for the clock to catch up.
