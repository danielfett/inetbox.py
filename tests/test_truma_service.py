"""Regression tests for the service layer."""

import logging
from datetime import datetime, timedelta
from time import monotonic

import miqro
import pytest

from conftest import call_handler, call_loop, deliver, published, published_errors
from inetbox.inetbox import InetboxApp
from test_inetbox import feed


@pytest.fixture(autouse=True)
def reset_commands():
    for command in InetboxApp.COMMANDS.values():
        command.can_send_updates = False
        command.updates_pending = False
    yield


# --------------------------------------------------------------- section 1


def test_the_framework_loop_step_is_left_alone(service):
    """miqro dispatches incoming messages in _loop_step.

    Replacing it without chaining is why no `set` command ever reached the
    CP Plus: the service stayed connected, kept publishing and looked healthy
    while discarding every command it received. The service paces itself in
    _wait_for_work instead.
    """
    assert type(service)._loop_step is miqro.Service._loop_step
    assert type(service)._wait_for_work is not miqro.Service._wait_for_work


def test_a_queued_mqtt_message_is_dispatched_by_one_loop_step(service):
    """The regression itself: enqueue like paho does, then run one step."""
    feed(service.inetapp, 0x33)
    deliver(service, "service/truma/set/target_temp_room", "19")

    assert service.updates_buffer == {}  # queued, not yet dispatched

    service._loop_step()

    assert service.updates_buffer["target_temp_room"] == "19"


def test_wait_for_work_pumps_the_serial_port(service, monkeypatch):
    calls = []
    monkeypatch.setattr(
        service.lin, "loop_serial", lambda serial, active: calls.append(active)
    )

    service._wait_for_work(0.0)

    assert calls == [True]


def test_wait_for_work_stays_on_the_bus_until_the_response_is_out(
    service, monkeypatch
):
    """The loops must not publish while a LIN response is waiting to be sent."""
    remaining = [3]

    def pump(serial, active):
        remaining[0] -= 1

    monkeypatch.setattr(service.lin, "loop_serial", pump)
    monkeypatch.setattr(service.lin, "response_waiting", lambda: remaining[0] > 0)

    service._wait_for_work(0.0)

    assert remaining[0] == 0


def test_wait_for_work_gives_up_on_a_response_nobody_collects(service, monkeypatch):
    """A master that stops polling must not stall MQTT dispatch."""
    monkeypatch.setattr(service, "RESPONSE_PUMP_MAX_SECONDS", 0.05)
    monkeypatch.setattr(service.lin, "loop_serial", lambda serial, active: None)
    monkeypatch.setattr(service.lin, "response_waiting", lambda: True)

    started = monotonic()
    service._wait_for_work(0.0)

    assert monotonic() - started < 1.0


# --------------------------------------------------------------- section 2


def test_set_reports_no_error_without_the_timer_buffer(service):
    """0x15 and 0x33 seen, 0x3D never - a room temperature set is fine."""
    feed(service.inetapp, 0x33)
    feed(service.inetapp, 0x15)

    call_handler(service, "handle_set_message", "19", "target_temp_room")

    assert service.updates_buffer["target_temp_room"] == "19"
    assert published_errors(service) == []


def test_set_reports_an_error_while_the_command_is_unknown(service):
    call_handler(service, "handle_set_message", "19", "target_temp_room")

    assert service.updates_buffer["target_temp_room"] == "19"
    assert len(published_errors(service)) == 1
    assert "has not sent the status buffer" in published_errors(service)[0]


def test_update_status_waits_for_the_commit_not_for_the_cp_plus(service):
    feed(service.inetapp, 0x33)
    call_handler(service, "handle_set_message", "19", "target_temp_room")

    call_loop(service, "send_update_status")

    _, status = published(service)[-1]
    assert status == "waiting for commit"


# --------------------------------------------------------------- section 3


def test_slow_cp_plus_does_not_kill_the_process(service):
    feed(service.inetapp, 0x33)
    service.inetapp.set_status("target_temp_room", "19")
    service.started_commit_updates = datetime.now() - timedelta(seconds=400)

    call_loop(service, "commit_updates")  # must not raise SystemExit

    assert service.inetapp.updates_to_send == {}
    assert not service.inetapp.updates_pending()
    assert service.started_commit_updates is None
    assert any("gave up on pending updates" in e for e in published_errors(service))


def test_the_wait_is_not_given_up_on_early(service):
    service.started_commit_updates = datetime.now() - timedelta(seconds=100)
    service.inetapp.updates_to_send = {"target_temp_room": 2920}

    call_loop(service, "commit_updates")

    assert service.inetapp.updates_to_send  # still waiting, nothing discarded
    assert published_errors(service) == []


def test_the_clock_stops_once_the_buffer_was_uploaded(service):
    """The CP Plus echoing our buffer back is a bonus, not the contract."""
    feed(service.inetapp, 0x33)
    service.started_commit_updates = datetime.now()
    InetboxApp.COMMAND_STATUS.updates_pending = True  # upload not confirmed yet
    assert service.inetapp.updates_to_send == {}

    call_loop(service, "send_update_status")

    assert service.started_commit_updates is None
    _, status = published(service)[-1]
    assert status == "waiting for Truma"


# --------------------------------------------------------------- section 6


def test_invalid_values_are_not_queued(service):
    feed(service.inetapp, 0x33)

    call_handler(service, "handle_set_message", "very warm", "target_temp_room")
    call_handler(service, "handle_set_message", "25:00:00", "wall_time")
    call_handler(service, "handle_set_message", "sideways", "heating_mode")
    call_handler(service, "handle_set_message", "maybe", "mode")

    assert service.updates_buffer == {}
    assert service.last_update_buffer_change is None
    assert len(published_errors(service)) == 4
    assert all("not applied" in e for e in published_errors(service))


def test_a_valid_wall_time_is_split_into_its_parts(service):
    feed(service.inetapp, 0x15)

    call_handler(service, "handle_set_message", "07:08:09", "wall_time")

    assert service.updates_buffer == {
        "wall_time_hours": "07",
        "wall_time_minutes": "08",
        "wall_time_seconds": "09",
    }


def test_turning_the_heating_on_restores_the_temperature(service):
    feed(service.inetapp, 0x33)
    service.last_target_temp_room = "21"

    call_handler(service, "handle_set_message", "heat", "mode")

    assert service.updates_buffer["target_temp_room"] == "21"
    assert service.updates_buffer["heating_mode"] == service.truma_default_heating_mode
    assert "mode" not in service.updates_buffer


def test_a_low_temperature_turns_the_heating_off(service):
    feed(service.inetapp, 0x33)

    call_handler(service, "handle_set_message", "3", "target_temp_room")

    assert service.updates_buffer["target_temp_room"] == "0"
    assert service.updates_buffer["heating_mode"] == "off"


def test_a_command_arriving_during_a_commit_is_not_lost(service):
    """The buffer swap used to drop anything that arrived mid-commit."""
    feed(service.inetapp, 0x33)
    call_handler(service, "handle_set_message", "19", "target_temp_room")
    service.last_update_buffer_change = datetime.now() - timedelta(seconds=10)

    original_set_status = service.inetapp.set_status
    arrived = []

    def set_status_with_a_concurrent_message(key, value):
        if not arrived:
            arrived.append(True)
            call_handler(
                service, "handle_set_message", "high", "target_temp_water"
            )
        return original_set_status(key, value)

    service.inetapp.set_status = set_status_with_a_concurrent_message
    call_loop(service, "commit_updates")

    assert service.updates_buffer == {"target_temp_water": "high"}
    assert service.last_update_buffer_change is not None


def test_a_concurrent_overwrite_of_the_same_key_is_kept(service):
    feed(service.inetapp, 0x33)
    call_handler(service, "handle_set_message", "19", "target_temp_room")
    service.last_update_buffer_change = datetime.now() - timedelta(seconds=10)

    original_set_status = service.inetapp.set_status

    def set_status_with_a_concurrent_message(key, value):
        service.updates_buffer["target_temp_room"] = "23"
        service.inetapp.set_status = original_set_status
        return original_set_status(key, value)

    service.inetapp.set_status = set_status_with_a_concurrent_message
    call_loop(service, "commit_updates")

    assert service.updates_buffer["target_temp_room"] == "23"


def test_a_failing_key_is_reported_with_its_topic(service):
    feed(service.inetapp, 0x33)
    service.updates_buffer["nonsense"] = "1"
    service.last_update_buffer_change = datetime.now() - timedelta(seconds=10)

    call_loop(service, "commit_updates")

    errors = published_errors(service)
    assert len(errors) == 1
    assert "nonsense" in errors[0]
    assert "discarded" in errors[0]
    assert service.updates_buffer == {}


# -------------------------------------------------------------- section 10


def test_service_state_is_per_instance(service):
    assert "updates_buffer" in vars(service)
    assert "started_commit_updates" in vars(service)
    assert "frost_protection_heating_status_before" in vars(service)


# ---------------------------------------------------------------- timing


def test_a_short_bus_silence_does_not_trip_the_watchdogs(service, monkeypatch):
    """12s of standby silence is normal and must not reopen or disconnect."""
    feed(service.inetapp, 0x33)
    monkeypatch.setattr(service.lin, "seconds_since_last_rx", lambda: 12.0)
    monkeypatch.setattr(
        service.inetapp, "seconds_since_status_update", lambda: 12.0
    )

    assert service.cp_plus_in_contact() is True
    assert service._serial_is_alive() is True
    assert service.serial.is_open is True


def test_a_long_bus_silence_reopens_the_port(service, monkeypatch):
    monkeypatch.setattr(service.lin, "seconds_since_last_rx", lambda: 300.0)
    old_serial = service.serial

    assert service._serial_is_alive() is True

    assert service.serial is not old_serial
    assert old_serial.is_open is False


# ------------------------------------------------------------- end to end


def test_a_set_command_travels_all_the_way_to_an_upload(service, caplog):
    """MQTT set -> buffer -> commit -> 0x18 announcement -> 0xBA upload."""
    from test_inetbox import DummyLin

    feed(service.inetapp, 0x33)

    call_handler(service, "handle_set_message", "21", "target_temp_room")
    assert published_errors(service) == []

    service.last_update_buffer_change = datetime.now() - timedelta(seconds=10)
    call_loop(service, "commit_updates")
    assert service.updates_buffer == {}
    assert service.inetapp.updates_to_send  # queued for the CP Plus

    # the CP Plus polls PID 0x18 and is told that we have something
    assert service.inetprotocol.answer_to_d8_message()[0] == 0xFF

    # ... and comes to collect it
    lin = DummyLin()
    service.inetprotocol._complete_transportlayer_request(lin, 0xBA, b"")
    assert len(lin.responses) == 1

    assert service.inetapp.updates_to_send == {}
    assert service.inetprotocol.answer_to_d8_message()[0] == 0xFE

    # the upload has happened, so the service stops counting towards the
    # give-up timeout even before the CP Plus echoes the buffer back
    call_loop(service, "send_update_status")
    assert service.started_commit_updates is None


def test_the_uploaded_buffer_carries_the_requested_temperature(service):
    """The value the user asked for must reach the wire unchanged."""
    from test_inetbox import DummyLin

    feed(service.inetapp, 0x33)
    service.inetapp.set_status("target_temp_room", "21")

    lin = DummyLin()
    service.inetprotocol._complete_transportlayer_request(lin, 0xBA, b"")

    # frames carry send_buffer[2:8], [8:14], [14:20], ... after a two byte
    # header each; target_temp_room sits at send_buffer[14:16]
    frames = lin.responses[0]
    assert frames[3][2:4] == bytes([0x7C, 0x0B])  # (21 + 273) * 10, little endian


def test_a_set_command_reaches_the_cp_plus_over_the_framework_loop(service):
    """The whole path, driven only by miqro's own loop.

    Nothing here calls a handler or a loop directly: the message is enqueued
    the way paho's network thread does it, and _loop_step has to do the rest.
    """
    feed(service.inetapp, 0x33)
    deliver(service, "service/truma/set/target_temp_room", "19")

    deadline = monotonic() + 5.0
    while not service.inetapp.updates_to_send and monotonic() < deadline:
        service._loop_step()

    assert service.inetapp.updates_to_send == {
        "target_temp_room": 2920,
        "heating_mode": 1,
    }
    assert published_errors(service) == []
    assert service.healthy()
    assert service.failure_count == 0


def test_starting_the_service_does_not_warn_about_the_loop(service, recwarn):
    service._warn_if_loop_step_overridden()

    assert [w for w in recwarn if issubclass(w.category, DeprecationWarning)] == []


def test_an_old_miqro_is_refused_at_startup(service, monkeypatch, tmp_path):
    """Silently never reading the bus would be worse than not starting."""
    monkeypatch.delattr(miqro.Service, "_wait_for_work")

    with pytest.raises(RuntimeError, match="_wait_for_work"):
        type(service)()
