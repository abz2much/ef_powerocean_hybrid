# Troubleshooting

## Setup fails with "Connection failed"

Home Assistant cannot open a Modbus TCP connection to the address you entered.
Check that the IP address is the inverter's, for example in your router's list
of devices, and that the port is 502. Home Assistant and the inverter must be on
the same network, or the networks between them must allow port 502. Modbus TCP
is off on a new inverter, so ask your installer to confirm that it is enabled.

If the form says the inverter takes connections but sends no Modbus replies,
the address is right, but Modbus is not answering. In a few cases a firmware
update has switched Modbus TCP off, and your installer can enable it again.

## Repairs shows "The inverter stopped answering Modbus" or "is not reachable"

The inverter has not answered over Modbus for at least 5 minutes. "Stopped
answering" means the port takes connections but sends no replies. "Not
reachable" means nothing answers on the port. Check that the inverter is
powered and still has the same IP address, and use **Reconfigure** if it moved.
If the inverter updated its firmware recently, ask your installer to enable
Modbus TCP again in the EcoFlow Pro app.

With the EcoFlow cloud set up, the cloud's values stand in meanwhile. The issue
clears with the next good read.

## Repairs shows "Modbus is disabled on the inverter"

The inverter answers, but every value reads 0, which is how it behaves with
Modbus disabled. Ask your installer to enable Modbus TCP in the EcoFlow Pro app.
The issue clears once readings arrive.

## Repairs shows "A different inverter answers"

The address now belongs to another device, usually because the inverter got a
new IP address from your router. Nothing is read or written until this is
fixed. Use **Reconfigure** to enter the inverter's current address, and reserve
that address for it in your router.

## Repairs shows "stopped updating its readings"

The inverter answers, but most of its readings stand still. Restart the inverter
as its manual describes.

## Repairs shows "Energy totals have been held for over a day"

The inverter reports a lifetime total that the integration does not consider
plausible, usually because the inverter reset its counter. The integration has
held the last good value for more than 24 hours. The issue offers two fixes.
**Keep Home Assistant's totals** carries each total on from its value in Home
Assistant, so the Energy dashboard continues without a jump. **Switch to the
inverter's totals** takes the inverter's values, and the Energy dashboard
records the difference in one step. If you think the values are wrong, choose
neither and attach the diagnostics download to a bug report.

## Repairs shows "Grid energy is not counted"

The inverter's grid import and export totals have read 0 for a day while Grid
Power showed energy flowing. The issue offers to turn on **Calculation of grid
energy**, which calculates the totals from Grid Power instead.

## Home Assistant asks you to sign in to EcoFlow again

The EcoFlow cloud rejected the login, for example after a password change.
Enter the account's current password. The Modbus side keeps working meanwhile.

## A setting or schedule change fails

Changes to the app's settings and schedules go over MQTT. A change is refused
while MQTT is reconnecting; try again a minute later. If the system does not
confirm a change within 10 seconds, Home Assistant shows an error; check the
value in the app before you try again. An occasional error from the EcoFlow cloud, such as an
HTTP 504, is on EcoFlow's side and passes by itself.

## A battery mode has no effect

Check **Control Status**. It says when Modbus Control is off, and when a guard,
a full or empty battery or the export limit stops a command. After a firmware
update, run the [control test](battery-control.md#testing-the-controls-after-a-firmware-update)
to see which control methods the inverter still follows.

## The schedule card looks old after an update

Reload the browser page, or clear the Home Assistant app's cache.

## Debug logging and diagnostics

To turn on debug logging, open **Settings > Devices & services > EF PowerOcean
Hybrid** and choose **Enable debug logging**. When you report a problem, attach
the log and the diagnostics download from the device page. The diagnostics
replace serial numbers with placeholders and leave out the EcoFlow account, but
check them for anything you would rather not share.

Report problems at
[github.com/abz2much/ef_powerocean_hybrid/issues](https://github.com/abz2much/ef_powerocean_hybrid/issues).
