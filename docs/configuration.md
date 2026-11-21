# Configuration

## Setup

When you add the integration, you choose how Home Assistant reaches the system.

**Modbus TCP** connects to the inverter on your local network. You enter its IP
address and port (502 by default), and then the settings below. For a
PowerOcean Single Phase, you are then offered the EcoFlow cloud as well. Signing
in adds it; the login is checked against the serial number the inverter reports
over Modbus.

**EcoFlow cloud only** is for a PowerOcean Single Phase without Modbus. You sign
in with the EcoFlow account that has the inverter and enter its serial number.
Such a system has the cloud's sensors only. Battery control needs Modbus.

| Field                      | Default                | What it is for                                                                                                                            |
| -------------------------- | ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Inverter model             | PowerOcean Three Phase | The model the integration uses. The model the inverter reports fills it in.                                                               |
| Battery module count       | 0                      | The number of battery modules (0 to 12). It sets the battery power limits until the inverter reports its capacity.                        |
| Maximum solar power        | 12 kW                  | Your installed solar power. Readings above it are rejected.                                                                               |
| Maximum grid power         | 15 kW                  | The most your grid connection can import or export, set by your main fuse, not by the inverter. It also caps Import from grid.            |
| Poll interval              | 5 s                    | How often Modbus is read, from 2 to 30 seconds.                                                                                           |
| Calculation of solar power | Off                    | For inverters whose solar power register reads 0. The solar power is then the sum of the strings.                                         |
| Calculation of grid energy | Off                    | For inverters whose grid import and export totals stay at 0. They are then calculated from Grid Power, within a few percent of the meter. |

If the cloud later rejects the login, for example after a password change, Home
Assistant asks you to sign in again. The Modbus side keeps working meanwhile.

## Reconfigure

The entry's menu has **Reconfigure**. It changes the inverter's IP address or
port, for example after the inverter got a new address. The new address is
tested first, and refused if a different inverter answers on it.

For a PowerOcean Single Phase, Reconfigure can also add the EcoFlow cloud to a
system set up with Modbus alone, or a Modbus address to a system set up through
the cloud alone. Every entity keeps its ID, name, labels and area.

## Options

The **Configure** page has the setup fields above and these options.

**Alert verbosity** sets how much the integration raises under **Settings >
Repairs**. Essential, the default, raises only what you must act on, such as an
inverter that stopped updating. Readings also raises readings that cannot be
trusted, Detailed adds the health of the connection, and Debug adds every
warning and error the integration logs. Each alert can be ignored, and going
back to Essential forgets the ignored ones.

**Battery Reserve** says whether the inverter or the integration keeps the
battery reserve. See [Battery control](battery-control.md#guards).

**Correct energy totals** is a collapsed section for a lifetime total that the
inverter reset. It sets a total to the value you enter, such as the one EcoFlow's
portal shows under its historical data, by adding an offset to what the inverter
reports. Leave the totals that are right as they are. To remove a correction,
enter the value of the total's (Device) sensor. A value lower than the total
shown asks you to confirm first, because Home Assistant records a drop as
negative energy.

A system with the EcoFlow cloud has three more options, all off or empty at
first:

| Option                     | What it does                                                                                                                                                                                                                        |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Extended connection (MQTT) | Connects to the EcoFlow cloud the way the EcoFlow app does. Everything in the next section needs it. If it fails to start, it stays off until you reload the entry.                                                                 |
| Live power values (MQTT)   | Asks the system for its power values every 20 seconds, as the app does while it is open. Solar and the energy flows then update about every 5 seconds, and so do house, grid and battery power while Modbus has no current reading. |
| Max schedule power (W)     | The highest power a scheduled task may use. Without it, schedules cannot be added or changed. There is no default.                                                                                                                  |

## What the MQTT connection adds

With **Extended connection (MQTT)** on, the integration has the app's settings
**Backup reserve**, **Surplus solar energy delivery** and **Battery save mode**.
The system confirms each change. If no confirmation arrives within 10 seconds,
Home Assistant shows an error. A change cannot be sent while MQTT is
reconnecting; try again a minute later.

The backup reserve shows the inverter's Modbus reading while Modbus has a
current one, and the cloud's value otherwise. It is always changed over MQTT.
While MQTT is on it replaces the Modbus sensor **App Backup Reserve**, which
shows the same value and returns when MQTT is turned off.

**LED brightness** is one number too. On a system with Modbus, it is read and
written over Modbus while Modbus has a current reading, and over MQTT otherwise.

Each of the app's scheduled tasks becomes a switch. See
[Scheduled tasks](schedules.md).

Turning MQTT off removes these entities, except LED brightness on a system with
Modbus.

## Entities from two sources

On a PowerOcean Single Phase with both Modbus and the cloud, these values are a
single entity each: house, battery and grid power, state of charge, inverter
voltage and grid frequency. They show the Modbus reading while it is current.
Otherwise they show the cloud's value if the cloud reported it within the last
60 seconds (120 seconds for voltage and frequency), and otherwise they are
unavailable. The `source` attribute says which source is shown.

The battery charged and discharged totals keep one source. It is Modbus for a
system set up with Modbus, and the cloud for a system set up through the cloud
alone, where their names end in "(cloud)". They never switch source, never go
down, and keep their last value while their source is away.

The inverter's firmware version comes from Modbus. The firmware of the EMS and
the other modules is in the diagnostics, and each battery pack's firmware is on
its device page.
