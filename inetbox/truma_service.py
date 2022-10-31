from serial import Serial
from . import InetboxLINProtocol, InetboxApp, Lin
import miqro
from os import environ
from datetime import timedelta
import logging
import logging.handlers


class TrumaService(miqro.Service):
    SERVICE_NAME = "truma"
    LOOP_INTERVAL = 0.001
    VALUE_UPDATE_MAX_INTERVAL = timedelta(minutes=2)

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

    @miqro.loop(seconds=3)
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
        try:
            self.inetapp.set_status(topic, msg)
        except Exception as e:
            self.log.exception(e)
            # send via mqtt
            self.publish("error", str(e))


def run():
    miqro.run(TrumaService)


if __name__ == "__main__":
    run()
