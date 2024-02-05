from inetbox.truma_service import TrumaService

# taken from https://github.com/mc0110/inetbox2mqtt/blob/02fe46d5580c5325a71b8abd299130c9802581ad/src/main.py:
rel_no = "2.6.5"

# taken from https://github.com/mc0110/inetbox2mqtt/blob/6d9241d90906d3619b7ae39a729453872a695e64/src/main1.py:

topic_root      = TrumaService.SERVICE_NAME
Pub_Prefix      = 'service/' + topic_root + '/control_status/' 

# Auto-discovery-function of home-assistant (HA)
HA_MODEL  = 'inetbox'
HA_SWV    = 'V03'
HA_STOPIC = 'service/' + topic_root + '/control_status/'
HA_CTOPIC = 'service/' + topic_root + '/set/'

HA_CONFIG = {
    "alive":                 ['homeassistant/binary_sensor/' + topic_root + '/alive/config', '{"name": "' + topic_root + '_alive", "model": "' + HA_MODEL + '", "sw_version": "' + HA_SWV + '", "device_class": "running", "state_topic": "' + HA_STOPIC + 'alive"}'],
    "release":               ['homeassistant/sensor/release/config', '{"name": "' + topic_root + '_release", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "state_topic": "' + HA_STOPIC + 'release"}'],
    "current_temp_room":     ['homeassistant/sensor/current_temp_room/config', '{"name": "' + topic_root + '_current_temp_room", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "device_class": "temperature", "unit_of_measurement": "°C", "state_topic": "' + HA_STOPIC + 'current_temp_room"}'],
    "current_temp_water":    ['homeassistant/sensor/current_temp_water/config', '{"name": "' + topic_root + '_current_temp_water", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "device_class": "temperature", "unit_of_measurement": "°C", "state_topic": "' + HA_STOPIC + 'current_temp_water"}'],
    "target_temp_room":      ['homeassistant/sensor/target_temp_room/config', '{"name": "' + topic_root + '_target_temp_room", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "device_class": "temperature", "unit_of_measurement": "°C", "state_topic": "' + HA_STOPIC + 'target_temp_room"}'],
    "target_temp_aircon":    ['homeassistant/sensor/target_temp_aircon/config', '{"name": "' + topic_root + '_target_temp_aircon", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "device_class": "temperature", "unit_of_measurement": "°C", "state_topic": "' + HA_STOPIC + 'target_temp_aircon"}'],
    "target_temp_water":     ['homeassistant/sensor/target_temp_water/config', '{"name": "' + topic_root + '_target_temp_water", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "device_class": "temperature", "unit_of_measurement": "°C", "state_topic": "' + HA_STOPIC + 'target_temp_water"}'],
    "energy_mix":            ['homeassistant/sensor/energy_mix/config', '{"name": "' + topic_root + '_energy_mix", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "state_topic": "' + HA_STOPIC + 'energy_mix"}'],
    "el_power_level":        ['homeassistant/sensor/el_level/config', '{"name": "' + topic_root + '_el_power_level", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "state_topic": "' + HA_STOPIC + 'el_power_level"}'],
    "heating_mode":          ['homeassistant/sensor/heating_mode/config', '{"name": "' + topic_root + '_heating_mode", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "state_topic": "' + HA_STOPIC + 'heating_mode"}'],
    "operating_status":      ['homeassistant/sensor/operating_status/config', '{"name": "' + topic_root + '_operating_status", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "state_topic": "' + HA_STOPIC + 'operating_status"}'],
    "aircon_operating_mode": ['homeassistant/sensor/aircon_operating_mode/config', '{"name": "' + topic_root + '_aircon_operating_mode", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "state_topic": "' + HA_STOPIC + 'aircon_operating_mode"}'],
    "aircon_vent_mode":      ['homeassistant/sensor/aircon_vent_mode/config', '{"name": "' + topic_root + '_aircon_vent_mode", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "state_topic": "' + HA_STOPIC + 'aircon_vent_mode"}'],
    "operating_status":      ['homeassistant/sensor/operating_status/config', '{"name": "' + topic_root + '_operating_status", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "state_topic": "' + HA_STOPIC + 'operating_status"}'],
    "error_code":            ['homeassistant/sensor/error_code/config', '{"name": "' + topic_root + '_error_code", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "state_topic": "' + HA_STOPIC + 'error_code"}'],
    "clock":                 ['homeassistant/sensor/clock/config', '{"name": "' + topic_root + '_clock", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "state_topic": "' + HA_STOPIC + 'clock"}'],
    "set_target_temp_room":  ['homeassistant/select/target_temp_room/config', '{"name": "' + topic_root + '_set_roomtemp", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "command_topic": "' + HA_CTOPIC + 'target_temp_room", "options": ["0", "10", "15", "18", "20", "21", "22"] }'],
    "set_target_temp_aircon":['homeassistant/select/target_temp_aircon/config', '{"name": "' + topic_root + '_set_aircontemp", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "command_topic": "' + HA_CTOPIC + 'target_temp_aircon", "options": ["16", "18", "20", "22", "24", "26", "28"] }'],
    "set_target_temp_water": ['homeassistant/select/target_temp_water/config', '{"name": "' + topic_root + '_set_warmwater", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "command_topic": "' + HA_CTOPIC + 'target_temp_water", "options": ["0", "40", "60", "200"] }'],
    "set_heating_mode":      ['homeassistant/select/heating_mode/config', '{"name": "' + topic_root + '_set_heating_mode", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "command_topic": "' + HA_CTOPIC + 'heating_mode", "options": ["off", "eco", "high"] }'],
    "set_aircon_mode":       ['homeassistant/select/aircon_mode/config', '{"name": "' + topic_root + '_set_aircon_mode", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "command_topic": "' + HA_CTOPIC + 'aircon_operating_mode", "options": ["off", "vent", "cool", "hot", "auto"] }'],
    "set_vent_mode":         ['homeassistant/select/vent_mode/config', '{"name": "' + topic_root + '_set_vent_mode", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "command_topic": "' + HA_CTOPIC + 'aircon_vent_mode", "options": ["low", "mid", "high", "night", "auto"] }'],
    "set_energy_mix":        ['homeassistant/select/energy_mix/config', '{"name": "' + topic_root + '_set_energy_mix", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "command_topic": "' + HA_CTOPIC + 'energy_mix", "options": ["none", "gas", "electricity", "mix"] }'],
    "set_el_power_level":    ['homeassistant/select/el_power_level/config', '{"name": "' + topic_root + '_set_el_power_level", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "command_topic": "' + HA_CTOPIC + 'el_power_level", "options": ["0", "900", "1800"] }'],
    "set_reboot":            ['homeassistant/select/set_reboot/config', '{"name": "' + topic_root + '_set_reboot", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "command_topic": "' + HA_CTOPIC + 'reboot", "options": ["0", "1"] }'],
    "set_os_run":            ['homeassistant/select/set_os_run/config', '{"name": "' + topic_root + '_set_os_run", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "command_topic": "' + HA_CTOPIC + 'os_run", "options": ["0", "1"] }'],
    "ota_update":            ['homeassistant/select/ota_update/config', '{"name": "' + topic_root + '_ota_update", "model": "' + HA_MODEL + '", "sw_version":"' + HA_SWV + '", "command_topic": "' + HA_CTOPIC + 'ota_update", "options": ["0", "1"] }'],
}

def del_ha_autoconfig(c):
    for i in HA_CONFIG.keys():
        try:
            c.publish(HA_CONFIG[i][0], "{}", qos=1, global_=True, only_if_changed=False)
        except:
            c.log.debug("Publishing error in del_ha_autoconfig")
    c.log.info("del ha_autoconfig completed")
        
# HA auto discovery: define all auto config entities         
def set_ha_autoconfig(c):
    for i in HA_CONFIG.keys():
        try:
            c.publish(HA_CONFIG[i][0], HA_CONFIG[i][1], qos=1, global_=True, only_if_changed=False)
        except:
            c.log.debug("Publishing error in set_ha_autoconfig")
    c.publish(Pub_Prefix + "release", rel_no, retain=True, qos=1, global_=True, only_if_changed=False)
    c.log.info("set ha_autoconfig completed")
