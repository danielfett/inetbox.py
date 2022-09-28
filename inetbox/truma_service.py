from serial import Serial
from . import InetboxLINProtocol, InetboxApp, Lin
import miqro
from os import environ


class TrumaService(miqro.Service):
    SERVICE_NAME = "truma"
    LOOP_INTERVAL = 0.001

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.inetapp = InetboxApp("DEBUG_APP" in environ)
        self.inetprotocol = InetboxLINProtocol(
            self.inetapp, "DEBUG_PROTOCOL" in environ
        )
        self.serial = Serial("/dev/ttyS0", 9600, timeout=0.03)
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
                self.inetapp.get_all(), "control_status", only_if_changed=True
            )

        self.publish_json_keys(
            self.inetapp.display_status, "display_status", only_if_changed=True
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
