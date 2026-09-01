"""Regression tests for the inetbox protocol layer."""

import logging

import bitstruct
import pytest

from inetbox.inetbox import InetboxApp, InetboxLINProtocol, TrumaCommand
from inetbox.tools import calculate_checksum

PREAMBLE = InetboxApp.STATUS_BUFFER_PREAMBLE


def make_status_buffer(cid, data, counter=0):
    """Build a status buffer exactly as the CP Plus sends it."""
    header = bytes([len(data), cid, counter])
    checksum = calculate_checksum(
        PREAMBLE[InetboxApp.STATUS_HEADER_CHECKSUM_START :] + header + data
    )
    return PREAMBLE + header + bytes([checksum]) + data


def feed(app, cid, data=None, counter=0):
    """Let the app receive one status buffer of the given type."""
    command = InetboxApp.COMMANDS[cid]
    if data is None:
        data = bytes(command.read_len)
    app.process_status_buffer_update(make_status_buffer(cid, data, counter))


@pytest.fixture
def app():
    app = InetboxApp(True, "en")
    # can_send_updates / updates_pending live on the shared command objects
    for command in InetboxApp.COMMANDS.values():
        command.can_send_updates = False
        command.updates_pending = False
    return app


# --------------------------------------------------------------- section 2


def test_can_send_updates_does_not_require_the_timer_buffer(app):
    """A CP Plus that never sends 0x3D must not block status writes."""
    feed(app, 0x33)
    feed(app, 0x15)

    assert app.can_send_updates(["target_temp_room"]) is True
    assert app.can_send_updates(["wall_time_hours"]) is True
    assert app.can_send_updates() is True

    # the timer command itself is genuinely not writable yet
    assert app.can_send_updates(["timer_active"]) is False


def test_can_send_updates_ignores_underscore_keys(app):
    feed(app, 0x33)
    assert app.can_send_updates(["_command_counter", "target_temp_room"]) is True


def test_can_send_updates_is_false_before_any_buffer(app):
    assert app.can_send_updates() is False
    assert app.can_send_updates(["target_temp_room"]) is False


def test_every_writable_key_belongs_to_a_command(app):
    """No key may fall through can_send_updates' "unknown command" branch."""
    writable = [
        key
        for key, (_read, write) in InetboxApp.STATUS_CONVERSION_FUNCTIONS.items()
        if write is not None
    ]
    assert writable
    for key in writable:
        assert app._command_for_key(key) is not None, key


# --------------------------------------------------------------- section 4


def test_pack_failure_does_not_latch_can_send_updates(app):
    """One incomplete pack must not disable the command for good."""
    command = InetboxApp.COMMAND_STATUS
    command.can_send_updates = True

    assert command.pack({"target_temp_room": 2920}) is None
    assert command.can_send_updates is True
    assert command.updates_pending is False

    complete = {name: 0 for name in command.attributes_rw}
    assert command.pack(complete) is not None
    assert command.updates_pending is True


def test_failed_pack_is_reported(app, caplog):
    command = InetboxApp.COMMAND_STATUS
    command.can_send_updates = True
    with caplog.at_level(logging.WARNING, logger="inet.app"):
        assert command.pack({"target_temp_room": 2920}) is None
    assert "Cannot pack cid 33" in caplog.text
    assert "heating_mode" in caplog.text


# --------------------------------------------------------------- section 5


class DummyLin:
    def __init__(self):
        self.responses = []

    def prepare_transportlayer_response(self, frames):
        self.responses.append(frames)


def test_upload_request_without_a_buildable_buffer_warns(app, caplog):
    """The 0xBA non-answer livelocks - it must not do so silently."""
    InetboxApp.COMMAND_STATUS.can_send_updates = True
    # armed, but the status buffer has none of the other values yet
    app.updates_to_send = {"target_temp_room": 2920}

    protocol = InetboxLINProtocol(app, debug=True)
    lin = DummyLin()
    with caplog.at_level(logging.WARNING, logger="inet.protocol"):
        protocol._complete_transportlayer_request(lin, 0xBA, b"")

    assert lin.responses == []
    assert "could not build a buffer" in caplog.text
    # the queued update is still there, which is exactly why this repeats
    assert app.updates_to_send


def test_upload_request_with_nothing_queued_is_quiet(app, caplog):
    protocol = InetboxLINProtocol(app, debug=True)
    lin = DummyLin()
    with caplog.at_level(logging.WARNING, logger="inet.protocol"):
        protocol._complete_transportlayer_request(lin, 0xBA, b"")

    assert lin.responses == []
    assert caplog.records == []


def test_upload_request_sends_the_buffer(app):
    feed(app, 0x33)
    app.set_status("target_temp_room", "19")

    protocol = InetboxLINProtocol(app, debug=True)
    lin = DummyLin()
    protocol._complete_transportlayer_request(lin, 0xBA, b"")

    assert len(lin.responses) == 1
    assert len(lin.responses[0]) == 7
    assert app.updates_to_send == {}
    assert InetboxApp.COMMAND_STATUS.updates_pending is True


# --------------------------------------------------------------- section 8


def test_timer_buffer_survives_a_round_trip():
    """Every offset of the 0x3D buffer must be preserved by unpack/pack.

    The field list used to repeat two names, so two positions collapsed onto
    one value and both were written back from it.
    """
    command = InetboxApp.COMMAND_TIMER
    assert command.read_len == command.write_len == 27

    # distinct value at every offset, and within the ranges the conversions use
    data = bytes(range(1, command.read_len + 1))
    unpacked = command.bitstruct_read.unpack(data)
    assert command.bitstruct_write.pack(unpacked) == data


def test_status_buffer_writable_part_survives_a_round_trip():
    command = InetboxApp.COMMAND_STATUS
    data = bytes(range(1, command.read_len + 1))
    unpacked = command.bitstruct_read.unpack(data)
    packed = command.bitstruct_write.pack(unpacked)
    # el_power_level and energy_mix are mirrored on purpose, so only the first
    # occurrence of each survives - everything else must round-trip
    assert packed[0:4] == data[0:4]
    assert packed[6:8] == data[6:8]


def test_accidentally_repeated_field_names_are_rejected():
    with pytest.raises(ValueError, match="repeats the writable field name"):
        TrumaCommand(0x99, [("a", "u8"), ("b", "u8"), ("a", "u8")])


def test_deliberately_repeated_field_names_are_allowed():
    command = TrumaCommand(
        0x99,
        [("a", "u8"), ("b", "u8"), ("a", "u8")],
        duplicate_attributes_ok=("a",),
    )
    assert command.attributes_rw.count("a") == 2


# --------------------------------------------------------------- section 9


def test_command_counter_starts_at_zero(app):
    assert app.status["_command_counter"] == 0


def test_command_counter_is_synced_by_an_0d_buffer(app):
    app.process_status_buffer_update(make_status_buffer(0x0D, b"", counter=0x42))
    assert app.status["_command_counter"] == 0x42
    assert app._command_counter_synced is True


# -------------------------------------------------------------- section 10


def test_state_is_not_shared_between_instances():
    first = InetboxApp(True, "en")
    second = InetboxApp(True, "en")
    first.updates_to_send["target_temp_room"] = 2920
    first.status["marker"] = 1
    first.display_status["marker"] = 1
    assert second.updates_to_send == {}
    assert "marker" not in second.status
    assert second.display_status == {}


# -------------------------------------------------------------- section 11


def test_foreign_read_by_identifier_is_not_reported_as_unknown(app, caplog):
    protocol = InetboxLINProtocol(app, debug=True)
    with caplog.at_level(logging.DEBUG, logger="inet.protocol"):
        protocol.receive_transportlayer_frame(
            DummyLin(), "single", 6, 0xB2, bytes([0x00, 0x17, 0x46, 0x40, 0x03])
        )
    assert "No idea how to answer" not in caplog.text
    assert "not for us" in caplog.text


def test_pid_18_answer_reports_pending_data(app, caplog):
    protocol = InetboxLINProtocol(app, debug=True)
    assert protocol.answer_to_d8_message()[0] == 0xFE

    feed(app, 0x33)
    app.set_status("target_temp_room", "19")
    with caplog.at_level(logging.INFO, logger="inet.protocol"):
        assert protocol.answer_to_d8_message()[0] == 0xFF
    assert "we have data" in caplog.text
