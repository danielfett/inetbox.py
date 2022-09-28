

```

mosquitto_pub -t 'service/truma/set/target_temp_room' -m '10'; mosquitto_pub -t 'service/truma/set/heating_mode' -m 'off'

```

heating_mode: off/eco/high

target_temp_water: 40/60/200

el_power_level: 0/900/1800

energy_mix: none/gas/electricity/mix

error: Any error messages