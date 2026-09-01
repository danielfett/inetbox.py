"""Test fixtures for the Truma service.

The service is built for real - miqro's Service.__init__, the loop table and
the MQTT handler table all matter for the regressions covered here - using
miqro's own test doubles for the broker and a fake for the serial port.
"""

import logging

import pytest
import yaml

import miqro
from miqro.test.tools import DummyMessage, DummyMQTTClient
from inetbox import truma_service


class FakeSerial:
    """A serial port that is open and permanently quiet."""

    def __init__(self, *args, **kwargs):
        self.is_open = True
        self.in_waiting = 0
        self.written = bytearray()

    def read(self, size=1):
        return b""

    def write(self, data):
        self.written += data
        return len(data)

    def close(self):
        self.is_open = False


@pytest.fixture
def service(tmp_path, monkeypatch):
    config = {
        "broker": {"host": "localhost"},
        "services": {
            "truma": {
                "serial_device": str(tmp_path / "ttyFAKE"),
                # TRANSLATIONS_HA_SENSOR_NAMES has no entry for the default
                # language "none", so create_ha_sensors needs a real one
                "language": "en",
            }
        },
    }
    config_file = tmp_path / "miqro.yml"
    config_file.write_text(yaml.dump(config))

    # Device registration appends to the class-level ha_devices list - keep it
    # from leaking between tests.
    monkeypatch.setattr(truma_service.TrumaService, "ha_devices", [])
    monkeypatch.setattr(truma_service.TrumaService, "ha_entities", [])
    monkeypatch.setattr(truma_service, "Serial", FakeSerial)

    svc = truma_service.TrumaService(
        add_config_file_path=str(config_file),
        log_level=logging.DEBUG,
        mqtt_client_cls=DummyMQTTClient,
    )
    return svc


def published(service):
    """Everything the service has published so far."""
    return list(service.mqtt_client.message_queue)


def published_errors(service):
    """Payloads the service published on its `error` topic."""
    return [
        payload for topic, payload in published(service) if topic.endswith("/error")
    ]


def deliver(service, topic, payload):
    """Hand a message to the service the way paho's network thread would.

    Deliberately does *not* dispatch it: what drains the queue is the point of
    several of these tests. miqro's own `send()` helper drains for you.
    """
    client = service.mqtt_client
    client.ensure_connected()
    client.on_message(client, None, DummyMessage(topic, payload))


def call_loop(service, name):
    """Call a @miqro.loop-decorated method.

    The decorator leaves a descriptor-less object on the class, so the method
    cannot be reached through the instance.
    """
    return getattr(type(service), name).fn(service)


def call_handler(service, name, *args, **kwargs):
    """Call a @miqro.handle-decorated method, see call_loop."""
    return getattr(type(service), name).fn(service, *args, **kwargs)
