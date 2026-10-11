"""Timer status buffer (CP Plus -> iNet Box, type 18 3D) and timer write buffer
(iNet Box -> CP Plus, type 11 3C).

The test vectors are real bus data, not built from the command definitions:
* C3_*: CP Plus C3.00.00 with a Combi 6 H5.00.00, captured 2026-10-04 (LIN sniffer
  on the bus while an emulated iNet Box exchanged these exact buffers with the panel;
  the write was acknowledged with error 0 and the re-read timer matched it).
* ESPHOME_*: one of the buffers quoted in Fabian-Schmidt/esphome-truma_inetbox
  (TrumaiNetBoxApp.cpp, timer 08:00-09:00 active).
"""

import pytest

from inetbox import InetboxApp, InetboxLINProtocol, Lin
from inetbox.tools import calculate_checksum


def h(text):
    return bytes.fromhex(text)


# Status buffers as InetboxApp.process_status_buffer_update() receives them (the
# transport payload after "00 1F"): preamble, length, type, counter, checksum, body.
C3_TIMER_5C_BOOST_ACTIVE = h(
    "00 1e 00 00 22 ff ff ff 54 01 18 3d 00 15"
    "dc 0a 0a 00 00 00 3a 0c 00 00 01 01 00 00 00 00 00 00 00 01 00 01 00 05"
)  # 5 C, boost, water 40, gas, active, 01:00-05:00
C3_TIMER_BEFORE_WRITE = h(
    "00 1e 00 00 22 ff ff ff 54 01 18 3d 00 1b"
    "dc 0a 01 00 00 00 3a 0c 00 00 01 01 00 00 00 00 00 00 00 00 02 03 00 05"
)  # 5 C, eco, water 40, gas, inactive, 03:02-05:00
C3_TIMER_INACTIVE_EMPTY = h(
    "00 1e 00 00 22 ff ff ff 54 01 18 3d 00 53"
    "00 00 00 00 00 00 00 00 00 00 01 01 00 00 00 00 00 00 00 00 00 00 00 00"
)
ESPHOME_TIMER_ECO = h(
    "00 1e 00 00 22 ff ff ff 54 01 18 3d 00 1d"
    "18 0b 01 00 00 00 00 00 00 00 01 01 00 00 00 00 00 00 00 01 00 08 00 09"
)  # 11 C, eco, water off, gas, active, 08:00-09:00

# Write buffer our emulated box uploaded (counter 0x12) and the CP Plus C3.00.00
# acknowledged with error 0; the timer re-read afterwards showed exactly these values:
# 18 C, eco, water 60, gas, active, 02:00-03:30.
C3_TIMER_WRITE_18C_ACTIVE = h(
    "00 1e 00 00 22 ff ff ff 54 01 11 3c 12 ab"
    "5e 0b 01 00 00 00 02 0d 00 00 01 01 01 00 02 1e 03"
)
# Heater status buffer (14 33) from the same panel, to check it is unaffected.
C3_HEATER_STATUS = h(
    "00 1e 00 00 22 ff ff ff 54 01 14 33 00 3c"
    "00 00 00 00 00 00 00 00 00 00 01 01 68 0b a6 0b 00 00 00 00 00 00 00 00"
)


def timer_window(app):
    return tuple(
        app.get_status(key)
        for key in (
            "timer_start_hours",
            "timer_start_minutes",
            "timer_stop_hours",
            "timer_stop_minutes",
        )
    )


@pytest.fixture
def app():
    app = InetboxApp(False, "none")
    # InetboxApp keeps its state in class attributes, reset it for every test
    app.status = {"_command_counter": 0}
    app.updates_to_send = {}
    app.display_status = {}
    for command in InetboxApp.COMMANDS.values():
        command.can_send_updates = False
        command.updates_pending = False
    return app


def test_timer_buffer_sizes():
    command = InetboxApp.COMMAND_TIMER
    assert command.read_len == 24
    assert command.write_len == 17
    assert command.cid_write == 0x3C


def test_c3_active_timer_is_parsed(app):
    app.process_status_buffer_update(C3_TIMER_5C_BOOST_ACTIVE)

    assert app.get_status("timer_target_temp_room") == "5"
    assert app.get_status("timer_heating_mode") == "boost"
    assert app.get_status("timer_target_temp_water") == "40"
    assert app.get_status("timer_el_power_level") == "0"
    assert app.get_status("timer_energy_mix") == "gas"
    assert app.get_status("timer_active")
    assert timer_window(app) == (1, 0, 5, 0)
    # the service calls get_all(), so every key must have a conversion
    app.get_all()


def test_esphome_example_is_parsed(app):
    app.process_status_buffer_update(ESPHOME_TIMER_ECO)

    assert app.get_status("timer_target_temp_room") == "11"
    assert app.get_status("timer_heating_mode") == "eco"
    assert app.get_status("timer_target_temp_water") == "0"
    assert app.get_status("timer_active")
    assert timer_window(app) == (8, 0, 9, 0)


def test_inactive_empty_timer_is_parsed(app):
    app.process_status_buffer_update(C3_TIMER_INACTIVE_EMPTY)

    assert not app.get_status("timer_active")
    assert app.get_status("timer_target_temp_room") == "0"
    assert app.get_status("timer_energy_mix") == "gas"


def test_heater_status_is_unaffected(app):
    app.process_status_buffer_update(C3_HEATER_STATUS)

    assert app.get_status("current_temp_room") == "25.2"
    assert app.get_status("current_temp_water") == "19"
    assert app.get_status("energy_mix") == "gas"
    assert InetboxApp.COMMAND_STATUS.can_send_updates
    assert not InetboxApp.COMMAND_TIMER.can_send_updates


def test_timer_write_matches_the_buffer_the_panel_accepted(app):
    app.process_status_buffer_update(C3_TIMER_BEFORE_WRITE)
    app.status["_command_counter"] = 0x11
    app.set_status("timer_target_temp_room", "18")
    app.set_status("timer_heating_mode", "eco")
    app.set_status("timer_target_temp_water", "60")
    app.set_status("timer_active", 1)
    app.set_status("timer_start_hours", 2)
    app.set_status("timer_start_minutes", 0)
    app.set_status("timer_stop_hours", 3)
    app.set_status("timer_stop_minutes", 30)

    assert app._get_status_buffer_for_writing() == C3_TIMER_WRITE_18C_ACTIVE
    assert app.updates_to_send == {}


class DummySerial:
    def __init__(self):
        self.rx = b""
        self.tx = bytearray()

    def read(self, size=1):
        out, self.rx = self.rx[:size], self.rx[size:]
        return out

    def write(self, data):
        self.tx += data

    def flush(self):
        pass

    def reset_input_buffer(self):
        self.rx = b""


def test_timer_download_through_lin_does_not_raise(app):
    """The CP Plus downloads 18 3D as a BB transfer (7 frames). With the 27-byte
    layout this raised bitstruct.Error out of Lin.loop_serial."""
    lin = Lin(InetboxLINProtocol(app, False), False)
    lin.transportlayer_response_buffer = []
    payload = h("00 1f") + C3_TIMER_5C_BOOST_ACTIVE
    payload += bytes(40 - len(payload))
    frames = [bytes([0x03, 0x10, 0x29, 0xBB]) + payload[0:4]]
    for i in range(6):
        frames.append(bytes([0x03, 0x21 + i]) + payload[4 + 6 * i : 10 + 6 * i])

    serial = DummySerial()
    for data in frames:
        serial.rx = bytes([0x00, 0x55, 0x3C]) + data + bytes([calculate_checksum(data)])
        lin.loop_serial(serial, True)

    assert lin.response_waiting()  # the FB acknowledgement is queued
    assert app.get_status("timer_active")
    assert app.get_status("timer_stop_hours") == 5
