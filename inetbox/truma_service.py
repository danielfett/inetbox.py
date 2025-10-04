from serial import Serial
from . import InetboxLINProtocol, InetboxApp, Lin
import miqro
from os import environ
from datetime import timedelta, datetime
import logging
import logging.handlers
from dateutil.tz import gettz


class TrumaService(miqro.Service):
    SERVICE_NAME = "truma"
    LOOP_INTERVAL = 0.001
    VALUE_UPDATE_MAX_INTERVAL = timedelta(minutes=2)

    TRUMA_MIN_TEMP = 5
    TRUMA_DEFAULT_TEMP = 5
    TRUMA_DEFAULT_MODE = "eco"
    TRUMA_MAX_TIMEDELTA = timedelta(minutes=1)

    updates_buffer = {}
    last_update_buffer_change = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Debug options either from environment (command line) or configuration file
        debug_app = self.service_config.get("debug_app", "DEBUG_APP" in environ)
        debug_lin = self.service_config.get("debug_lin", "DEBUG_LIN" in environ)
        debug_protocol = self.service_config.get(
            "debug_protocol", "DEBUG_PROTOCOL" in environ
        )
        self.truma_default_heating_mode = self.service_config.get(
            "default_heating_mode", self.TRUMA_DEFAULT_MODE
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

        self.inetapp = InetboxApp(debug_app)
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
            self.publish_json_keys(
                self.inetapp.get_all(),
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

        # Sanity check / automation for the dependency between room temperature and heating mode
        if topic == "target_temp_room":  # Only react to changes in the room temperature
            try:
                target_temp = int(msg)
            except ValueError:
                self.log.error("Invalid target temperature value")
                return

            # Implement automatism to set the heating mode to "eco" if it is off when a temperature > 5°C is set.
            if target_temp >= self.TRUMA_MIN_TEMP:  # If it is desired to heat the room
                if (
                    "heating_mode" in self.updates_buffer
                    and self.updates_buffer["heating_mode"] == "off"
                ) or (
                    "heating_mode" not in self.updates_buffer
                    and self.inetapp.get_status("heating_mode", "off") == "off"
                ):
                    self.updates_buffer["heating_mode"] = (
                        self.truma_default_heating_mode
                    )
                    self.log.info(
                        "Setting heating mode to 'eco' as a temperature > 5°C was set"
                    )
                else:
                    self.log.info(
                        "Heating mode is already set to 'eco' or 'boost', no change necessary"
                    )

            # The other way round: If the target temperature is set to lower than 5°C, turn off the heating.
            else:
                self.updates_buffer["heating_mode"] = "off"
                self.updates_buffer["target_temp_room"] = (
                    "0"  # Truma cannot heat below 5°C
                )
                self.log.info(
                    "Turning off heating as temperature was set to 5°C or lower"
                )

        # Similar for heating mode
        elif topic == "heating_mode":
            # And if the heating mode is turned off, set the target temperature to 0°C.
            if msg == "off":
                self.updates_buffer["target_temp_room"] = "0"
                self.log.info(
                    "Setting target temperature to 0°C as heating was turned off"
                )
            # If the heating mode is set to "eco" or "boost", set the target temperature to 18°C.
            elif msg in ["eco", "boost"]:
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
                    f"Invalid heating mode value {msg}. Only 'off', 'eco' and 'boost' are allowed."
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
        if self.last_update_buffer_change is None:
            return
        if datetime.now() - self.last_update_buffer_change < self.updates_buffer_time:
            return

        self.log.info(f"Committing updates {self.updates_buffer}")
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
        if self.last_update_buffer_change is not None:
            if not self.inetapp.can_send_updates():
                status = "waiting_for_cp_plus"
            else:
                status = "waiting_commit"
        elif self.inetapp.updates_to_send:
            status = "waiting_truma"
        elif self.inetapp.updates_pending():
            status = "waiting_truma"
        else:
            status = "idle"
        self.publish("update_status", status, only_if_changed=timedelta(seconds=60))

    @miqro.loop(seconds=0.3)
    def send_cp_plus_status(self):
        if self.inetapp.can_send_updates:
            status = "online"
        else:
            status = "waiting"

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
        if abs(current_hours - int(self.inetapp.get_status("wall_time_hours", current_hours))) > self.TRUMA_MAX_TIMEDELTA.seconds/3600 or \
           abs(current_minutes - int(self.inetapp.get_status("wall_time_minutes", current_minutes))) > self.TRUMA_MAX_TIMEDELTA.seconds/60 or \
           abs(current_seconds - int(self.inetapp.get_status("wall_time_seconds", current_seconds))) > self.TRUMA_MAX_TIMEDELTA.seconds:

            self.inetapp.set_status("wall_time_hours", str(current_hours))
            self.inetapp.set_status("wall_time_minutes", str(current_minutes))
            self.inetapp.set_status("wall_time_seconds", str(current_seconds))
        else:
            self.log.info("Time is already up to date, no need to set it")


def run():
    miqro.run(TrumaService)


if __name__ == "__main__":
    run()
