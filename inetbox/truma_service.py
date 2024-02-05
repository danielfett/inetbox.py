from serial import Serial
from . import InetboxLINProtocol, InetboxApp, Lin
import miqro
from os import environ
from datetime import timedelta, datetime
import logging
import logging.handlers


class TrumaService(miqro.Service):
    SERVICE_NAME = "truma"
    LOOP_INTERVAL = 0.001
    VALUE_UPDATE_MAX_INTERVAL = timedelta(minutes=2)

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
        
    def _on_connect(self, *args, **kwargs):
        super()._on_connect(*args, **kwargs)
        
        try:
            self.log.info(f"Trying to send home assistant discovery messages...")
            from .haconfig import haconfig
            haconfig.del_ha_autoconfig(self) 
            haconfig.set_ha_autoconfig(self)
        except ImportError:
            self.log.warning(f"no module `haconfig` for discovery message to home assistant loaded")

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
        if not self.inetapp.can_send_updates:
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

    @miqro.loop(seconds=0.1)
    def send_update_status(self):
        if self.last_update_buffer_change is not None:
            if not self.inetapp.can_send_updates:
                status = "waiting_for_cp_plus"
            else:
                status = "waiting_commit"
        elif self.inetapp.updates_to_send:
            status = "waiting_truma"
        else:
            status = "idle"
        self.publish("update_status", status, only_if_changed=timedelta(seconds=60))

    @miqro.loop(seconds=0.1)
    def send_cp_plus_status(self):
        if self.inetapp.can_send_updates:
            status = "online"
        else:
            status = "waiting"

        self.publish("cp_plus_status", status, only_if_changed=timedelta(seconds=60))


def run():
    miqro.run(TrumaService)


if __name__ == "__main__":
    run()
