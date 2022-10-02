from serial import Serial
from . import InetboxLINProtocol, InetboxApp, Lin
import miqro
from os import environ
from datetime import timedelta


class TrumaService(miqro.Service):
    SERVICE_NAME = "truma"
    LOOP_INTERVAL = 0.001
    VALUE_UPDATE_MAX_INTERVAL = timedelta(minutes=2)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.inetapp = InetboxApp("DEBUG_APP" in environ)
        self.inetprotocol = InetboxLINProtocol(
            self.inetapp, "DEBUG_PROTOCOL" in environ
        )
        serial_device = self.service_config.get("serial_device", "/dev/serial0")
        self.serial = Serial(serial_device, 9600, timeout=0.03)
        self.log.info(f"Using serial device {serial_device}")
        self.lin = Lin(self.inetprotocol, "DEBUG_LIN" in environ)

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
                self.inetapp.get_all(), "control_status", only_if_changed=self.VALUE_UPDATE_MAX_INTERVAL
            )

        self.publish_json_keys(
            self.inetapp.display_status, "display_status", only_if_changed=self.VALUE_UPDATE_MAX_INTERVAL
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