from serial import Serial
from . import (
    InetboxLINProtocol,
    InetboxApp,
    Lin,
    TRANSLATIONS_HA_SENSOR_NAMES,
    TRANSLATIONS_STATES,
)
import miqro
from miqro import ha_sensors
from os import environ
from datetime import timedelta, datetime
import logging
import logging.handlers
from dateutil.tz import gettz
import sys


class TrumaService(miqro.Service):
    SERVICE_NAME = "truma"
    LOOP_INTERVAL = 0.001
    VALUE_UPDATE_MAX_INTERVAL = timedelta(minutes=2)

    TRUMA_MIN_TEMP = 5
    TRUMA_DEFAULT_TEMP = 5
    TRUMA_MAX_TIMEDELTA = timedelta(minutes=1)
    MAX_UPDATE_WAIT = timedelta(seconds=60)

    updates_buffer = {}
    last_update_buffer_change = None
    started_commit_updates = None
    last_target_temp_room = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.lang = self.service_config.get("language", "none")

        self.create_ha_sensors()

        # Debug options either from environment (command line) or configuration file
        debug_app = self.service_config.get("debug_app", "DEBUG_APP" in environ)
        debug_lin = self.service_config.get("debug_lin", "DEBUG_LIN" in environ)
        debug_protocol = self.service_config.get(
            "debug_protocol", "DEBUG_PROTOCOL" in environ
        )
        self.truma_default_heating_mode = self.service_config.get(
            "default_heating_mode",
            TRANSLATIONS_STATES[self.lang]["heating_mode"][1],  # eco
        )
        if (
            self.truma_default_heating_mode
            not in TRANSLATIONS_STATES[self.lang]["heating_mode"].values()
        ):
            raise ValueError(
                f"Invalid default heating mode: {self.truma_default_heating_mode}"
            )

        self.truma_default_target_temp_room = self.service_config.get(
            "default_target_temp_room", self.TRUMA_DEFAULT_TEMP
        )

        # Allow setting a log directory from environment (command line) or configuration file.
        log_dir = self.service_config.get("log_dir", environ.get("LOG_DIR", None))
        # If activated, all logs from the inet. hierarchy will be written there.
        if log_dir:
            logger = logging.getLogger("inet")
            logger.setLevel(logging.DEBUG)
            # Rotate log files every day, keep 7 days of logs.
            handler = logging.handlers.TimedRotatingFileHandler(
                log_dir + "/inet.log", when="midnight", backupCount=7
            )
            handler.setLevel(logging.DEBUG)
            formatter = logging.Formatter(
                "%(asctime)s\t%(name)s\t%(levelname)s\t%(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        self.updates_buffer_time = timedelta(
            seconds=self.service_config.get("updates_buffer_time", 1)
        )

        self.inetapp = InetboxApp(debug_app, self.lang)
        self.inetprotocol = InetboxLINProtocol(self.inetapp, debug_protocol)
        serial_device = self.service_config.get("serial_device", "/dev/serial0")
        baudrate = self.service_config.get("baudrate", 9600)
        timeout = self.service_config.get("timeout", 0.03)
        self.log.info(f"Opening serial device {serial_device} in exclusive mode")
        self.serial = Serial(serial_device, baudrate, timeout=timeout, exclusive=True)
        self.lin = Lin(self.inetprotocol, debug_lin)

    def _loop_step(self):
        assert self.LOOPS is not None

        if not self.lin.response_waiting():
            for loop in self.LOOPS:
                loop.run_if_needed(self)

        self.lin.loop_serial(self.serial, True)

    @miqro.loop(seconds=0.5)
    def send_status(self):
        if self.inetapp.status_updated:
            data = self.inetapp.get_all()

            # add synthetic on/off switch for heating - simply based on temperature setting
            if "target_temp_room" in data:
                # update last_target_temp_room to restore it if the heating is turned off
                self.last_target_temp_room = data["target_temp_room"]

                # create synthetic on/off switch
                target_temp_room = int(data["target_temp_room"])
                if target_temp_room >= self.TRUMA_MIN_TEMP:
                    data["mode"] = "heat"
                else:
                    data["mode"] = "off"

            self.publish_json_keys(
                data,
                "control_status",
                only_if_changed=self.VALUE_UPDATE_MAX_INTERVAL,
            )

        self.publish_json_keys(
            self.inetapp.display_status,
            "display_status",
            only_if_changed=self.VALUE_UPDATE_MAX_INTERVAL,
        )

    @miqro.handle("set/#")
    def handle_set_message(self, msg, topic):
        self.log.info(f"Received set message {msg} on topic {topic}")
        # Instead of pushing updates to inetapp immediately, we collect them and
        # send them all at once. This is to avoid sending multiple updates to the
        # same value in a short time frame and also helps with values that depend
        # on each other, e.g., the heating mode and heating temperature.
        self.updates_buffer[topic] = msg
        self.last_update_buffer_change = datetime.now()

        # we need to work with the translated values for the heating mode
        _off = TRANSLATIONS_STATES[self.lang]["heating_mode"][0]
        _eco = TRANSLATIONS_STATES[self.lang]["heating_mode"][1]
        _boost = TRANSLATIONS_STATES[self.lang]["heating_mode"][10]

        # Synthetic on/off switch
        if topic == "mode":
            if msg == "heat":
                # Restore last target temperature room or set to default if not available
                if (
                    self.last_target_temp_room is not None
                    and int(self.last_target_temp_room) >= self.TRUMA_MIN_TEMP
                ):
                    self.updates_buffer["target_temp_room"] = self.last_target_temp_room
                else:
                    self.updates_buffer["target_temp_room"] = str(
                        self.truma_default_target_temp_room
                    )
                # Set heating mode to default
                self.updates_buffer["heating_mode"] = self.truma_default_heating_mode
                self.log.info("Turning heating on")
            elif msg == "off":
                self.updates_buffer["heating_mode"] = _off
                self.updates_buffer["target_temp_room"] = (
                    "0"  # Truma cannot heat below 5°C
                )
                self.log.info("Turning heating off")
            else:
                self.log.error(
                    f"Invalid mode value {msg}. Only 'heat' and 'off' are allowed."
                )
            # No further processing needed
            del self.updates_buffer[topic]
            return

        # Sanity check / automation for the dependency between room temperature and heating mode
        if topic == "target_temp_room":  # Only react to changes in the room temperature
            try:
                target_temp = int(float(msg))
                self.updates_buffer[topic] = str(target_temp)  # store as integer string
            except ValueError:
                self.log.error("Invalid target temperature value")
                return

            # Implement automatism to set the heating mode to "eco" if it is off when a temperature > 5°C is set.
            if target_temp >= self.TRUMA_MIN_TEMP:  # If it is desired to heat the room
                if (
                    "heating_mode" in self.updates_buffer
                    and self.updates_buffer["heating_mode"] == _off
                ) or (
                    "heating_mode" not in self.updates_buffer
                    and self.inetapp.get_status("heating_mode", _off) == _off
                ):
                    self.updates_buffer["heating_mode"] = (
                        self.truma_default_heating_mode
                    )
                    self.log.info(
                        "Setting heating mode to default heating mode as a temperature > 5°C was set"
                    )
                else:
                    self.log.info(
                        f"Heating mode is already set to '{self.updates_buffer.get('heating_mode', '???')}' (in updates) or '{self.inetapp.get_status('heating_mode', '???')}' (in last truma status), no change necessary"
                    )

            # The other way round: If the target temperature is set to lower than 5°C, turn off the heating.
            else:
                self.updates_buffer["heating_mode"] = _off
                self.updates_buffer["target_temp_room"] = (
                    "0"  # Truma cannot heat below 5°C
                )
                self.log.info(
                    "Turning off heating as temperature was set to 5°C or lower"
                )

        # Similar for heating mode
        elif topic == "heating_mode":
            # And if the heating mode is turned off, set the target temperature to 0°C.
            if msg == _off:
                self.updates_buffer["target_temp_room"] = "0"
                self.log.info(
                    "Setting target temperature to 0°C as heating was turned off"
                )
            # If the heating mode is set to "eco" or "boost", set the target temperature to 18°C.
            elif msg in [_eco, _boost]:
                if (
                    "target_temp_room" in self.updates_buffer
                    and int(self.updates_buffer["target_temp_room"])
                    < self.TRUMA_MIN_TEMP
                ) or (
                    "target_temp_room" not in self.updates_buffer
                    and int(self.inetapp.get_status("target_temp_room", "0"))
                    < self.TRUMA_MIN_TEMP
                ):
                    self.updates_buffer["target_temp_room"] = str(
                        self.truma_default_target_temp_room
                    )
                    self.log.info(
                        "Setting target temperature to the default temperature as heating mode was set to 'eco' or 'boost'"
                    )
                else:
                    self.log.info(
                        "Target temperature is already set to a value > 5°C, no change necessary"
                    )
            else:
                self.log.error(
                    f"Invalid heating mode value {msg}. Only '{_off}', '{_eco}' and '{_boost}' are allowed."
                )

        # parse date/time for clock setting
        elif topic == "wall_time":
            try:
                hours, minutes, seconds = msg.split(":")
            except ValueError:
                self.log.error("Invalid time format (expected HH:MM:SS format)")
                return

            if not hours.isdigit() or not minutes.isdigit() or not seconds.isdigit():
                self.log.error(
                    "Invalid time format (expected HH:MM:SS format) - non-numeric values found"
                )
                return

            if (
                int(hours) > 23
                or int(hours) < 0
                or int(minutes) > 59
                or int(minutes) < 0
                or int(seconds) > 59
                or int(seconds) < 0
            ):
                self.log.error(
                    "Invalid time format (expected HH:MM:SS format) - values out of range"
                )
                return

            del self.updates_buffer[topic]
            self.updates_buffer["wall_time_hours"] = hours
            self.updates_buffer["wall_time_minutes"] = minutes
            self.updates_buffer["wall_time_seconds"] = seconds

        if not self.inetapp.can_send_updates:
            msg = "Cannot send updates to inetapp, no status received from CP Plus yet. Changes will be delayed until status received."
            self.log.error(msg)
            self.publish("error", msg)

    @miqro.loop(seconds=0.1)
    def commit_updates(self):
        # exit the application if it takes too long to commit updates
        if self.started_commit_updates is not None:
            if datetime.now() - self.started_commit_updates > self.MAX_UPDATE_WAIT:
                self.log.exception(
                    "Taking too long to commit updates, resetting inetapp"
                )
                sys.exit(1)

        if self.last_update_buffer_change is None:
            return
        if datetime.now() - self.last_update_buffer_change < self.updates_buffer_time:
            return

        self.log.info(f"Committing updates {self.updates_buffer}")
        self.started_commit_updates = datetime.now()
        for topic, msg in self.updates_buffer.items():
            try:
                self.inetapp.set_status(topic, msg)
            except Exception as e:
                self.log.exception(e)
                # send via mqtt
                self.publish("error", str(e))

        self.updates_buffer = {}
        self.last_update_buffer_change = None

    @miqro.loop(seconds=0.3)
    def send_update_status(self):
        _ = TRANSLATIONS_STATES[self.lang]["update_status"]
        if self.last_update_buffer_change is not None:
            if not self.inetapp.can_send_updates():
                status = _["waiting_for_cp_plus"]
            else:
                status = _["waiting_commit"]
        elif self.inetapp.updates_to_send:
            status = _["waiting_truma"]
        elif self.inetapp.updates_pending():
            status = _["waiting_truma"]
        else:
            status = _["idle"]
            self.started_commit_updates = None
        self.publish("update_status", status, only_if_changed=timedelta(seconds=60))

    @miqro.loop(seconds=0.3)
    def send_cp_plus_status(self):
        _ = TRANSLATIONS_STATES[self.lang]["cp_plus_status"]
        if self.inetapp.can_send_updates:
            status = _["online"]
        else:
            status = _["waiting"]

        self.publish("cp_plus_status", status, only_if_changed=timedelta(seconds=60))

    @miqro.handle("update_time")
    def handle_update_time(self, msg):
        self.set_time()

    @miqro.loop(hours=24)
    def set_time_24hour(self):
        if not self.service_config.get("set_time", False):
            return
        if not self.inetapp.COMMAND_TIME.can_send_updates:
            return

        self.set_time()

    def set_time(self):
        current_time = datetime.now()

        if not self.service_config.get("timezone_override", None):
            self.log.info(
                f"Setting time to {current_time} (no timezone override configured in settings)"
            )
        else:
            tz = gettz(self.service_config["timezone_override"])
            current_time = current_time.astimezone(tz)
            self.log.info(
                f"Setting time to {current_time} (timezone override activated in settings)"
            )
        # only set the time when the currently set time deviates more than a minute
        current_hours = int(current_time.hour)
        current_minutes = int(current_time.minute)
        current_seconds = int(current_time.second)
        if (
            abs(
                current_hours
                - int(self.inetapp.get_status("wall_time_hours", current_hours))
            )
            > self.TRUMA_MAX_TIMEDELTA.seconds / 3600
            or abs(
                current_minutes
                - int(self.inetapp.get_status("wall_time_minutes", current_minutes))
            )
            > self.TRUMA_MAX_TIMEDELTA.seconds / 60
            or abs(
                current_seconds
                - int(self.inetapp.get_status("wall_time_seconds", current_seconds))
            )
            > self.TRUMA_MAX_TIMEDELTA.seconds
        ):

            self.inetapp.set_status("wall_time_hours", str(current_hours))
            self.inetapp.set_status("wall_time_minutes", str(current_minutes))
            self.inetapp.set_status("wall_time_seconds", str(current_seconds))
        else:
            self.log.info("Time is already up to date, no need to set it")

    def create_ha_sensors(self):
        _ = lambda s: TRANSLATIONS_HA_SENSOR_NAMES[self.lang].get(s, s)

        dev = ha_sensors.Device(
            self,
            name=_("Truma Device"),
            manufacturer="Truma",
        )

        temp_climate_controller = ha_sensors.ClimateController(
            dev,
            name=_("Room Temperature"),
            current_temperature_topic_postfix="control_status/current_temp_room",
            initial=0,
            min_temp=0,
            max_temp=30,
            precision="1.0",
            temperature_unit="C",
            temperature_command_topic_postfix="set/target_temp_room",
            temperature_state_topic_postfix="control_status/target_temp_room",
            icon="mdi:radiator",
            fan_mode_command_topic_postfix="set/heating_mode",
            fan_mode_state_topic_postfix="control_status/heating_mode",
            fan_modes=list(TRANSLATIONS_STATES[self.lang]["heating_mode"].values()),
            modes=["off", "heat"],  # only off and heat modes
            mode_command_topic_postfix="set/mode",
            mode_state_topic_postfix="control_status/mode",
        )

        wall_time = ha_sensors.Text(
            dev,
            name=_("Time"),
            min=8,
            max=8,
            pattern="^[012][0-9]:[0-5][0-9]:[0-5][0-9]$",
            state_topic_postfix="control_status/wall_time",
            command_topic_postfix="set/wall_time",
            icon="mdi:clock-outline",
            enabled_by_default=False,
        )

        update_status = ha_sensors.Sensor(
            dev,
            name=_("Update Status"),
            state_topic_postfix="update_status",
            icon="mdi:progress-clock",
        )

        operating_status = ha_sensors.Sensor(
            dev,
            name=_("Operating Status"),
            state_topic_postfix="control_status/operating_status",
            icon="mdi:information-outline",
        )

        cp_plus_status = ha_sensors.Sensor(
            dev,
            name=_("CP Plus Status"),
            state_topic_postfix="cp_plus_status",
            icon="mdi:server-network",
            enabled_by_default=False,
        )

        set_time = ha_sensors.Button(
            dev,
            name=_("Set time from system time"),
            command_topic_postfix="update_time",
            icon="mdi:clock-check-outline",
            enabled_by_default=False,
        )

        target_temp_water = ha_sensors.Select(
            dev,
            name=_("Water Heater"),
            state_topic_postfix="control_status/target_temp_water",
            command_topic_postfix="set/target_temp_water",
            options=list(TRANSLATIONS_STATES[self.lang]["target_temp_water"].values()),
            icon="mdi:water-boiler",
        )

        energy_mix = ha_sensors.Select(
            dev,
            name=_("Energy Mix"),
            state_topic_postfix="control_status/energy_mix",
            command_topic_postfix="set/energy_mix",
            options=list(TRANSLATIONS_STATES[self.lang]["energy_mix"].values()),
        )

        el_power_level = ha_sensors.Select(
            dev,
            name=_("Electricity Power Level"),
            state_topic_postfix="control_status/el_power_level",
            command_topic_postfix="set/el_power_level",
            options=list(TRANSLATIONS_STATES[self.lang]["el_power_level"].values()),
        )

        current_temp_water = ha_sensors.Sensor(
            dev,
            name=_("Water Temperature"),
            state_topic_postfix="control_status/current_temp_water",
            unit_of_measurement="°C",
            icon="mdi:thermometer-water",
            device_class="temperature",
        )

        error_code = ha_sensors.Sensor(
            dev,
            name=_("Error Code"),
            state_topic_postfix="control_status/error_code",
            icon="mdi:alert-circle-outline",
        )

        voltage = ha_sensors.Sensor(
            dev,
            name=_("Supply Voltage"),
            state_topic_postfix="display_status/voltage",
            unit_of_measurement="V",
            icon="mdi:flash",
            device_class="voltage",
            suggested_display_precision=1,
            state_class="measurement",
        )


def run():
    miqro.run(TrumaService)


if __name__ == "__main__":
    run()
