# Moving from another integration

This integration can replace EF-PowerOcean-TcpModbus (domain
`ef_powerocean_tcpmodbus`) and powerocean_single_phase. It is a separate
integration with its own domain, so Home Assistant creates new entities and
devices for it. Entity IDs are made from the device name, and Home Assistant
adds `_2` to an ID that is still taken. Remove the old integration before you
set up this one if you want the same IDs.

## From EF-PowerOcean-TcpModbus

1. Note the entity IDs your dashboards, automations, scripts and the Energy
   dashboard use.
2. Turn off Modbus Control in the old integration, then remove it.
3. Set up this integration with the same IP address and port, and give the
   inverter's device the name it had, so the entity IDs match.
4. Set the options again, and the Battery Mode powers, Charge Limit and Battery
   Reserve if you used them.
5. Change automations and scripts from the `ef_powerocean_tcpmodbus` actions and
   events to the `ef_powerocean_hybrid` ones, for example
   `ef_powerocean_hybrid.set_battery_command` and
   `ef_powerocean_hybrid_command_expired`.

If you keep both integrations for a while, only one of them may have Modbus
Control on. This integration refuses to turn it on while the other has it on for
the same address. With its control off, the other integration only reads and
can stay.

## From powerocean_single_phase

1. Note the entity IDs your dashboards, automations, scripts and the Energy
   dashboard use.
2. Remove powerocean_single_phase.
3. Set up this integration. Choose Modbus TCP and add the EcoFlow cloud, or
   choose EcoFlow cloud only.
4. Give the inverter's device the name it had. The schedule switches
   (`schedule_<id>`) and the Active schedule sensor then keep their entity IDs.
   The sensors get new entity IDs, made from their names rather than the
   cloud's keys, so every dashboard, automation and the Energy dashboard that
   uses a sensor must be pointed at the new one.
5. Turn on the same options: Extended connection (MQTT), Live power values and
   Max schedule power.
6. Change automations and scripts that call
   `powerocean_single_phase.add_schedule`, `update_schedule` or
   `delete_schedule` to the `ef_powerocean_hybrid` actions.

## After moving

Check each dashboard, automation and the Energy dashboard against the IDs you
noted. Where Modbus and the cloud report the same value, such as house power or
the state of charge, this integration has one entity instead of two. A
dashboard or automation that used either of the old entities needs that one.
