from datetime import datetime
from decimal import Decimal
import logging
from .lin import Lin
from .tools import format_bytes, calculate_checksum
from . import conversions as cnv
import bitstruct
from dataclasses import dataclass


class InetboxLINProtocol:
    NODE_ADDRESS = 0x03
    IDENTIFIER = bytes([0x17, 0x46, 0x00, 0x1F])

    transportlayer_received_request_sid = None
    transportlayer_received_request_payload = None
    transportlayer_received_request_expected_bytes = None

    def __init__(self, app, debug=False):
        self.app = app
        self.log = logging.getLogger("inet.protocol")
        # when requested, set logger to debug level
        self.log.setLevel(logging.DEBUG if debug else logging.INFO)

    def receive_transportlayer_frame(
        self, lin: Lin, frame_type, expected_bytes, sid, payload
    ):
        if (
            frame_type == "single"
            and sid == 0xB9
            and payload[0:2] == self.IDENTIFIER[2:]
        ):
            # This request is probably a heartbeat request or similar.
            # Expected answer is just a 0x00 byte.
            # We here add all frames that will be sent to the response buffer.
            self.log.debug("Received heartbeat request.")
            lin.prepare_transportlayer_response(
                [bytes([self.NODE_ADDRESS, 0x02, 0xF9, 0x00, 0xFF, 0xFF, 0xFF, 0xFF])]
            )
        elif (
            frame_type == "single"
            and sid == 0xB0
            and payload.startswith(self.IDENTIFIER)
        ):
            self.log.debug("Received request to assign network address.")
            # Assign NAD request - has to be answered, empty payload
            if payload[-1] != self.NODE_ADDRESS:
                raise Exception(
                    f"CP Plus tried to give us a new node address {payload[:-1]} vs {self.NODE_ADDRESS}- while valid in the LIN protocol, not implemented here."
                )
            lin.prepare_transportlayer_response(
                [bytes([self.NODE_ADDRESS, 0x01, 0xF0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])]
            )
            self.log.info("Initialization complete. Inetbox registered.")
        elif (
            frame_type == "first"
            and sid in [0xBA, 0xBB]
            and payload[0:2] == self.IDENTIFIER[2:]
        ):
            # This is the start of a multi-message transportlayer request.
            self.transportlayer_received_request_sid = sid
            self.transportlayer_received_request_payload = payload[2:]
            self.transportlayer_received_request_expected_bytes = expected_bytes - 2
            self.log.debug("Received first frame of data download or upload")

        elif (
            frame_type == "consecutive" and self.transportlayer_received_request_payload
        ):
            self.transportlayer_received_request_payload += payload
            assert self.transportlayer_received_request_expected_bytes
            self.log.debug(
                f"Received new data for data download or upload, now at {len(self.transportlayer_received_request_payload)} bytes"
            )
            # when all bytes have been received, call function to parse the request
            if (
                len(self.transportlayer_received_request_payload)
                >= self.transportlayer_received_request_expected_bytes
            ):
                self.log.debug(
                    f"Received all data for data download or upload, now at {len(self.transportlayer_received_request_payload)} bytes"
                )
                self._complete_transportlayer_request(
                    lin,
                    self.transportlayer_received_request_sid,
                    self.transportlayer_received_request_payload,
                )
                self.transportlayer_received_request_payload = None
        else:
            # self.log.warning("No idea how to answer this message.")
            pass

    def _complete_transportlayer_request(self, lin: Lin, sid, request_payload):
        if sid == 0xBB:
            self.log.info("Received status data from CP Plus")
            lin.prepare_transportlayer_response(
                [bytes([self.NODE_ADDRESS, 0x01, 0xFB, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])]
            )

            # if request_payload != self.status_buffer:
            #    with open("trumalogs/status_buffer.log", "a") as f:
            #        # write current time plus serialised status buffer
            #        f.write(
            #            f"{datetime.now().isoformat()} {format_bytes(request_payload)}\n"
            #        )
            #    self.status_buffer = request_payload

            self.app.process_status_buffer_update(request_payload)

        elif sid == 0xBA:
            self.log.debug("Received request for data upload: %s", request_payload)

            send_buffer = self.app._get_status_buffer_for_writing()

            if send_buffer is None:
                self.log.debug("Not responding, waiting for status message first!")
                return

            # pad the buffer with zeros
            send_buffer += bytes(38 - len(send_buffer))

            lin.prepare_transportlayer_response(
                [
                    bytes(
                        [self.NODE_ADDRESS, 0x10, 0x29, 0xFA, 0x00, 0x1F, 0x00, 0x1E]
                    ),
                    bytes([self.NODE_ADDRESS, 0x21]) + send_buffer[2:8],
                    bytes([self.NODE_ADDRESS, 0x22]) + send_buffer[8:14],
                    bytes([self.NODE_ADDRESS, 0x23]) + send_buffer[14:20],
                    bytes([self.NODE_ADDRESS, 0x24]) + send_buffer[20:26],
                    bytes([self.NODE_ADDRESS, 0x25]) + send_buffer[26:32],
                    bytes([self.NODE_ADDRESS, 0x26]) + send_buffer[32:38],
                ]
            )

            self.log.info("Uploading new status data.")

    def receive_read_by_identifier_request(self, lin: Lin):
        self.log.debug("Received read by identifier request.")
        lin.prepare_transportlayer_response(
            [bytes([self.NODE_ADDRESS, 0x06, 0xF2]) + self.IDENTIFIER + bytes([0x00])]
        )

    def answer_to_d8_message(self):
        self.log.debug(
            f"Responding to 08 message (updates_to_send={self.app.updates_to_send})!"
        )
        return bytes(
            [
                # FE: app waits for updates from CP Plus
                # FF: app has an update ready for CP plus
                0xFF if self.app.updates_to_send else 0xFE,
                0xFF,
                0xFF,
                0xFF,
                0xFF,
                0xFF,
                0xFF,
                0xFF,
            ]
        )

    def handle_message(self, pid, databytes):
        return self.app.handle_message(pid, databytes)

    ANSWER_TO_PIDS = {0x18: answer_to_d8_message}


@dataclass
class TrumaCommand:
    cid: int
    read_len: int
    write_len: int
    bitstruct: bitstruct.CompiledFormat

    def parse(self, byte_data):
        return self.bitstruct.unpack(byte_data)

    def pack(self, data):
        return self.bitstruct.pack(data)[: self.write_len]

    @property
    def cid_write(self):
        return self.cid - 1


class InetboxApp:

    ENERGY_MIX_MAPPING = {
        0x00: "electricity",
        0xFA: "gas/mix",
    }
    ENERGY_MODE_MAPPING = {
        0x00: "gas",
        0x09: "mix/electricity 1",
        0x12: "mix/electricity 2",
    }
    ENERGY_MODE_2_MAPPING = {
        0x1: "Gas",
        0x2: "Electricity",
        0x3: "Gas/Electricity",
    }
    VENT_MODE_MAPPING = {
        0x00: "Off",
        0xB: "Eco",
        0xD: "High",
        0x1: "Vent 1",
        0x2: "Vent 2",
        0x3: "Vent 3",
        0x4: "Vent 4",
        0x5: "Vent 5",
        0x6: "Vent 6",
        0x7: "Vent 7",
        0x8: "Vent 8",
        0x9: "Vent 9",
        0xA: "Vent 10",
    }
    VENT_OR_OPERATING_STATUS = {
        0x01: "off",
        0x22: "on + airvent",
        0x02: "on",
        0x31: "error (?)",
        0x32: "fatal error",
        0x21: "airvent (?)",
    }
    CP_PLUS_DISPLAY_STATUS_MAPPING = {
        0xF0: "heating on",
        0x20: "standby ac on",
        0x00: "standby ac off",
        0xD0: "error",
        0x70: "fatal error",
        0x50: "boiler on",
        0x40: "boiler off",
    }
    HEATING_STATUS_MAPPING = {
        0x10: "boiler eco done",
        0x11: "boiler eco heating",
        0x30: "boiler hot done",
        0x31: "boiler hot heating",
    }
    HEATING_STATUS_2_MAPPING = {
        0x04: "normal",
        0x05: "error",
        0xFF: "fatal error (?)",
        0xFE: "normal (?)",
    }

    STATUS_BUFFER_PREAMBLE = bytes(
        [0x00, 0x1E, 0x00, 0x00, 0x22, 0xFF, 0xFF, 0xFF, 0x54, 0x01]
    )

    COMMAND_STATUS = TrumaCommand(
        cid=0x33,  # when receiving, sending is that -1
        read_len=0x14,
        write_len=0x0C,
        bitstruct=bitstruct.compile(
            ">u16u8u8u16u16u16u8u8u16u16u8r16u8<",
            names=[
                "target_temp_room",
                "heating_mode",
                "_recv_status_u3",
                "el_power_level",
                "target_temp_water",
                "el_power_level",
                "energy_mix",
                "energy_mix",
                "current_temp_water",
                "current_temp_room",
                "operating_status",
                "error_code",
                "_recv_status_u10",
            ],
        ),
    )

    COMMAND_TIMER = TrumaCommand(
        cid=0x3D,
        read_len=0x18,
        write_len=0x10,
        bitstruct=bitstruct.compile(
            ">u16u8u8u8u8u16u8u8u8u8u16u16u8u8u8u8u8u8u8u8<",
            names=[
                "timer_target_temp_room",
                "timer_heating_mode",
                "_timer_unknown1",
                "timer_el_power_level",
                "_timer_unknown5",
                "timer_target_temp_water",
                "_timer_unknown6",
                "_timer_unknown7",
                "_timer_unknown8",
                "_timer_unknown9",
                "_timer_unknown10",
                "_timer_unknown11",
                "_timer_unknown10",
                "_timer_unknown11",
                "_timer_unknown12",
                "_timer_unknown13",
                "_timer_unknown14",
                "_timer_unknown15",
                "_timer_unknown16",
                "_timer_unknown17",
                "timer_active",
                "timer_start_minutes",
                "timer_start_hours",
                "timer_stop_minutes",
                "timer_stop_hours",
            ],
        ),
    )

    COMMAND_TIME = TrumaCommand(
        cid=0x15,
        read_len=0x0A,
        write_len=0x08,
        bitstruct=bitstruct.compile(
            ">u8u8u8u8u8u8u8u8u8u8<",
            names=[
                "wall_time_hours",
                "wall_time_minutes",
                "wall_time_seconds",
                "_time_display1",
                "_time_display2",
                "_time_display3",
                "clock_mode",
                "clock_source",
                "_time_display4",
                "_time_display5",
            ],
        ),
    )

    COMMANDS = {
        0x33: COMMAND_STATUS,
        0x3D: COMMAND_TIMER,
        0x15: COMMAND_TIME,
    }

    STATUS_BUFFER_COMMAND_ID_COMMAND_COUNTER = 0x0D
    """"
    "STATUS_BUFFER_COMMAND_ID_STATUS = 0x33
    STATUS_BUFFER_COMMAND_ID_TIMER = 0x3D  # when receiving, sending is that -1
    STATUS_BUFFER_COMMAND_ID_TIME = 0x15  # when receiving, sending is that -1

    STATUS_BUFFER_TYPES = {
        STATUS_BUFFER_COMMAND_ID_STATUS: {
            "read_len": 0x14,
            "write_len": 0x0C,
            "bitstruct": bitstruct.compile(
                ">u16u8u8u16u16u16u8u8u16u16u8r16u8<",
                names=[
                    "target_temp_room",
                    "heating_mode",
                    "_recv_status_u3",
                    "el_power_level",
                    "target_temp_water",
                    "el_power_level",  # appears twice, we assume that it is the same
                    "energy_mix",
                    "energy_mix",  # appears twice, we assume that it is the same
                    "current_temp_water",
                    "current_temp_room",
                    "operating_status",
                    "error_code",
                    "_recv_status_u10",
                ],
            ),
        },
        STATUS_BUFFER_COMMAND_ID_TIMER: {
            "id": 0x3D,  # when receiving, sending is that -1
            "read_len": 0x18,
            "write_len": 0x10,
            "bitstruct": bitstruct.compile(
                ">u16u8u8u8u8u16u8u8u8u8u16u16u8u8u8u8u8u8u8u8<",
                names=[
                    "timer_target_temp_room",
                    "timer_heating_mode",
                    "_timer_unknown1",
                    "timer_el_power_level",
                    "_timer_unknown5",
                    "timer_target_temp_water",
                    "_timer_unknown6",
                    "_timer_unknown7",
                    "_timer_unknown8",
                    "_timer_unknown9",
                    "_timer_unknown10",
                    "_timer_unknown11",
                    "_timer_unknown10",
                    "_timer_unknown11",
                    "_timer_unknown12",
                    "_timer_unknown13",
                    "_timer_unknown14",
                    "_timer_unknown15",
                    "_timer_unknown16",
                    "_timer_unknown17",
                    "timer_active",
                    "timer_start_minutes",
                    "timer_start_hours",
                    "timer_stop_minutes",
                    "timer_stop_hours",
                ],
            ),
        },
        STATUS_BUFFER_COMMAND_ID_TIME: {
            # raw: 15 20 00 01 01 00 00 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00, 15/20 is the wall time
            "id": 0x15,  # when receiving, sending is that -1
            "read_len": 0x0A,
            "write_len": 0x08,  # guessed
            "bitstruct": bitstruct.compile(
                ">u8u8u8u8u8u8u8u8u8u8u<",
                names=[
                    "wall_time_hours",
                    "wall_time_minutes",
                    "wall_time_seconds",
                    "_time_display1",
                    "_time_display2",
                    "_time_display3",
                    "clock_mode",
                    "clock_source",
                    "_time_display4",
                    "_time_display5",
                ],
            )
        },
    }
    """

    STATUS_CONVERSION_FUNCTIONS = {  # pair for reading from buffer and writing to buffer, None if writing not allowed
        "target_temp_room": (
            cnv.temp_code_to_string,
            cnv.string_to_temp_code,
        ),
        "heating_mode": (
            cnv.heating_mode_to_string,
            cnv.string_to_heating_mode,
        ),
        "target_temp_water": (
            cnv.temp_code_to_string,
            cnv.string_to_temp_code,
        ),
        "el_power_level": (
            cnv.el_power_code_to_string,
            cnv.string_to_el_power_code,
        ),
        "energy_mix": (
            cnv.energy_mix_code_to_string,
            cnv.string_to_energy_mix_code,
        ),
        "current_temp_room": (cnv.temp_code_to_string, None),
        "current_temp_water": (cnv.temp_code_to_string, None),
        "operating_status": (cnv.operating_status_to_string, None),
        "error_code": (cnv.error_code_to_string, None),
        "timer_target_temp_room": (
            cnv.temp_code_to_string,
            cnv.string_to_temp_code,
        ),
        "timer_target_temp_water": (
            cnv.temp_code_to_string,
            cnv.string_to_temp_code,
        ),
        "timer_active": (
            cnv.bool_to_int,
            cnv.int_to_bool,
        ),
        "timer_start_minutes": (int, int),
        "timer_start_hours": (int, int),
        "timer_stop_minutes": (int, int),
        "timer_stop_hours": (int, int),
        "wall_time_hours": (int, int),
        "wall_time_minutes": (int, int),
        "wall_time_seconds": (int, int),
        "clock_mode": (cnv.clock_mode_to_string, cnv.string_to_clock_mode),
        "clock_source": (cnv.clock_source_to_string, cnv.string_to_clock_source),
    }

    STATUS_HEADER_CHECKSUM_START = 8

    status = {"_command_counter": 128}

    status_updated = False

    updates_to_send = {}

    can_send_updates = False

    display_status = {}

    def __init__(self, debug):
        self.log = logging.getLogger("inet.app")
        # when requested, set logger to debug level
        self.log.setLevel(logging.DEBUG if debug else logging.INFO)

    def map_or_debug(self, mapping, value):
        if value in mapping:
            return mapping[value]
        else:
            return f"unknown value {value:02x}"

    def handle_message(self, pid, databytes):
        try:
            # call the relevant function for the pid, if it exists ...
            {
                0x20: self.parse_command_status,
                0x21: self.parse_status_1,
                0x22: self.parse_status_2,
            }[pid](databytes)
            return True
        except KeyError:
            # ... or exit with false
            return False

    def parse_command_status(self, databytes):
        data = {
            "target_temp_room": cnv.temp_code_to_decimal(
                databytes[0] | (databytes[1] & 0x0F) << 8
            ),
            "target_temp_water": cnv.temp_code_to_decimal(
                databytes[2] << 4 | (databytes[1] & 0xF0) >> 4
            ),
            "energy_mix": self.map_or_debug(self.ENERGY_MIX_MAPPING, databytes[3]),
            "energy_mode": self.map_or_debug(self.ENERGY_MODE_MAPPING, databytes[4]),
            "energy_mode_2": self.map_or_debug(
                self.ENERGY_MODE_2_MAPPING,
                databytes[5] & 0x0F,
            ),
            "vent_mode": self.map_or_debug(self.VENT_MODE_MAPPING, databytes[5] >> 4),
            "pid_20_unknown_byte_6": hex(databytes[6]),
            "pid_20_unknown_byte_7": hex(databytes[7]),
        }

        self.display_status.update(data)

    def parse_status_1(self, databytes):
        data = {
            "current_temp_room": cnv.temp_code_to_decimal(
                databytes[0] | (databytes[1] & 0x0F) << 8
            ),
            "current_temp_water": cnv.temp_code_to_decimal(
                databytes[2] << 4 | (databytes[1] & 0xF0) >> 4
            ),
            "pid_21_unknown_byte_3": hex(databytes[3]),
            "pid_21_unknown_byte_4": hex(databytes[4]),
            "vent_or_something_status": self.map_or_debug(
                self.VENT_OR_OPERATING_STATUS,
                databytes[5],
            ),
            "pid_21_unknown_byte_6": hex(databytes[6]),
            "pid_21_unknown_byte_7": hex(databytes[7]),
        }

        self.display_status.update(data)

    def parse_status_2(self, databytes):
        data = {
            "voltage": str(
                (Decimal(databytes[0]) / Decimal(10)).quantize(Decimal("0.1"))
            ),
            "cp_plus_display_status": self.map_or_debug(
                self.CP_PLUS_DISPLAY_STATUS_MAPPING,
                databytes[1],
            ),
            "heating_status": self.map_or_debug(
                self.HEATING_STATUS_MAPPING, databytes[2]
            ),
            "heating_status_2": self.map_or_debug(
                self.HEATING_STATUS_2_MAPPING, databytes[3]
            ),
            "pid_22_unknown_byte_4": hex(databytes[4]),
            "pid_22_unknown_byte_5": hex(databytes[5]),
            "pid_22_unknown_byte_6": hex(databytes[6]),
            "pid_22_unknown_byte_7": hex(databytes[7]),
        }

        self.display_status.update(data)

    def process_status_buffer_update(self, status_buffer):
        self.log.debug(f"Status data: {format_bytes(status_buffer)}")
        # Example: 00 1e 00 00 22 ff ff ff 54 01 14 33 00 3c 00 00 00 00 00 00 00 00 00 00 01 01 68 0b a6 0b 00 00 00 00 00 00 00 00
        #          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ preamble (len = 10 bytes)
        #                                        ^^ length
        #                                           ^^ type of message
        #                                              ^^ command counter
        #                                                 ^^ checksum

        if not status_buffer.startswith(self.STATUS_BUFFER_PREAMBLE):
            self.log.error(
                f"Status buffer does not start with preamble, expected {self.STATUS_BUFFER_PREAMBLE}, got {status_buffer[:len(self.STATUS_BUFFER_PREAMBLE)]}"
            )
            return

        # after the preamble, there's a two-byte header defining the length and type of buffer
        header = (command_len, command_id) = status_buffer[
            len(self.STATUS_BUFFER_PREAMBLE) : len(self.STATUS_BUFFER_PREAMBLE) + 2
        ]

        # after that, there's a command counter and a checksum
        command_counter = status_buffer[len(self.STATUS_BUFFER_PREAMBLE) + 2]
        checksum = status_buffer[len(self.STATUS_BUFFER_PREAMBLE) + 3]

        # todo: check the checksum

        # type 0d is special: it only updates the command counter
        if command_id == self.STATUS_BUFFER_COMMAND_ID_COMMAND_COUNTER:
            self.log.info(f"Received command counter update, now: {command_counter}")
            self.status["_command_counter"] = command_counter
            return

        # get status buffer info for header
        try:
            command = self.COMMANDS[command_id]
        except KeyError:
            self.log.warning(f"Unknown status buffer type {header}")
            return

        # parse status buffer, starting after the header
        parsed_status_buffer = command.parse(
            status_buffer[len(self.STATUS_BUFFER_PREAMBLE) + 4 :]
        )

        # if any of the values is new, set self.status_updated to True, ignore underscore keys
        self.status_updated = True
        self.can_send_updates = True
        self.status.update(parsed_status_buffer)

        # log
        self.log.info(
            f"Received status buffer update for {header}: {parsed_status_buffer}"
        )

    def _get_status_buffer_for_writing(self):
        # right now, we only send this one type of buffer
        command = self.COMMAND_STATUS

        if not self.updates_to_send:
            self.log.debug("No updates to send.")
            return None

        # increase output message counter
        self.status["_command_counter"] = (self.status["_command_counter"] + 1) % 0xFF

        # get current status buffer contents as dict
        try:
            binary_buffer_contents = command.pack(
                {**self.status, **self.updates_to_send}
            )
        except bitstruct.Error:
            # not all required data in status buffer yet
            self.can_send_updates = False
            return None

        # calculate checksum
        checksum = calculate_checksum(
            self.STATUS_BUFFER_PREAMBLE[self.STATUS_HEADER_CHECKSUM_START :]
            + bytes(
                [command.write_len, command.cid_write, self.status["_command_counter"]]
            )
            + binary_buffer_contents
        )

        self.updates_to_send = {}

        output = (
            self.STATUS_BUFFER_PREAMBLE
            + bytes(
                [
                    command.write_len,
                    command.cid_write,
                    self.status["_command_counter"],
                    checksum,
                ],
            )
            + binary_buffer_contents
        )
        self.log.debug(f"Sending status buffer: {format_bytes(output)}")
        return output

    def get_status(self, key, default=None):
        # return the respective key from self.status, if it exists, and apply the conversion function
        if key not in self.status:
            if default is not None:
                return default
            raise KeyError
        if key.startswith("_"):
            return f"unknown - {self.status[key]} = {hex(self.status[key])}"
        if key not in self.STATUS_CONVERSION_FUNCTIONS:
            raise Exception(
                f"Conversion function not defined - is this key ({key}) defined?"
            )
        if self.STATUS_CONVERSION_FUNCTIONS[key][0] is None:
            raise Exception(
                f"Conversion function not defined - is this key ({key}) readable?"
            )
        return self.STATUS_CONVERSION_FUNCTIONS[key][0](self.status[key])

    def set_status(self, key, value):
        # set the respective key in self.status, if it exists, and apply the conversion function
        if key.startswith("_"):
            self.log.debug(f"Setting unknown {key} to {value}")
            self.updates_to_send[key] = value
            return
        if key not in self.STATUS_CONVERSION_FUNCTIONS:
            raise Exception(
                f"Conversion function not defined - is this key ({key}) defined?"
            )
        if self.STATUS_CONVERSION_FUNCTIONS[key][1] is None:
            raise Exception(
                f"Conversion function not defined - is this key ({key}) writable?"
            )
        self.log.debug(f"Setting {key} to {value}")
        self.updates_to_send[key] = self.STATUS_CONVERSION_FUNCTIONS[key][1](value)

    def get_all(self):
        self.status_updated = False
        return {key: self.get_status(key) for key in self.status}
