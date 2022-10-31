import logging
from .tools import format_bytes, calculate_checksum
from serial import Serial


class Lin:
    PID_TRANSPORTLAYER_MASTER2SLAVE = 0x3C
    PID_TRANSPORTLAYER_SLAVE2MASTER = 0x3D

    SERVICE_ID_MAPPING = {
        0xB0: "Assign NAD",
        0xB1: "Assign Frame Identifier",
        0xB2: "Read by Identifier",
        0xB3: "Conditional Change NAD",
        0xB4: "Data Dump",
        0xB5: "Assign NAD via Slave Node Position Detection",
        0xB6: "Save Configuration",
        0xB7: "Assign Frame Identifier Range",
    }

    SID_READ_BY_IDENTIFIER = 0xB2
    NODE_ADDRESS_BROADCAST = 0x7F

    transportlayer_response_buffer = None

    class ChecksumError(Exception):
        pass

    def __init__(self, protocol, debug=False):
        self.protocol = protocol
        self.log = logging.getLogger("inet.lin")

        # when requested, set logger to debug level
        self.log.setLevel(logging.DEBUG if debug else logging.INFO)

    def check_checksum(self, bytestring):
        if len(bytestring) == 0:
            raise self.ChecksumError("Checksum error (empty bytestring)")

        cs = calculate_checksum(bytestring[:-1])

        if not cs == bytestring[-1]:
            raise self.ChecksumError(
                f"Checksum error (received {bytestring[-1]:02x}, calculated {cs:02x})"
            )

    def check_pid_parity(self, byte):
        # lower six bits (ID0-ID5) are data, upper two bits are parity
        #
        # The parity bits are calculated as follows:
        #
        # P0 = ID0 ⊕ ID1 ⊕ ID2 ⊕ ID4
        # P1 = ! (ID1 ⊕ ID3 ⊕ ID4 ⊕ ID5)

        # Calculate P0 and P1
        p0 = (
            (byte & 0x01)
            ^ ((byte & 0x02) >> 1)
            ^ ((byte & 0x04) >> 2)
            ^ ((byte & 0x10) >> 4)
        )
        p1 = (
            not ((byte & 0x02) >> 1)
            ^ ((byte & 0x08) >> 3)
            ^ ((byte & 0x10) >> 4)
            ^ ((byte & 0x20) >> 5)
        )

        # Check if the received parity bits match the calculated ones
        if not (p0 == ((byte & 0x40) >> 6) and p1 == ((byte & 0x80) >> 7)):
            raise self.ChecksumError(f"Parity error (received {byte:02x})")

        # Only the lower bits are the actual PID
        return byte & 0x3F

    def parse_transportlayer_frame_header(self, databytes):
        # first byte is node address
        node_address_byte = databytes[0]
        node_address = (
            f"{node_address_byte:02x}"
            if node_address_byte != self.NODE_ADDRESS_BROADCAST
            else "broadcast"
        )
        self.log.debug(f"   node address: {node_address}")

        # upper four bit of pci indicate type of frame
        pci_identifier = databytes[1] >> 4

        frame_type = "reserved"
        expected_bytes = None
        sid = None
        payload = []

        if pci_identifier == 0x0:
            frame_type = "single"
            expected_bytes = (databytes[1] & 0x0F) - 1
            sid = databytes[2]
            payload = databytes[3 : 3 + expected_bytes]
            self.log.debug(f"   single frame, expected bytes: {expected_bytes}")

        elif pci_identifier == 0x1:
            # in this case, the lower 4 bits of the pci plus the consecutive byte indicate
            # total number of bytes
            frame_type = "first"
            expected_bytes = ((databytes[1] & 0x0F) << 8 | databytes[2]) - 1
            sid = databytes[3]
            payload = databytes[4:]
            self.log.debug(f"   first frame, expected bytes: {expected_bytes}")

        elif pci_identifier == 0x2:
            frame_type = "consecutive"
            payload = databytes[2:]
            self.log.debug(f"   consecutive frame no {databytes[1] & 0x0F}")

        self.log.debug(f"   payload: {format_bytes(payload)}")

        return node_address_byte, frame_type, expected_bytes, sid, payload

    def parse_transportlayer_master2slave(self, databytes):
        self.log.debug(f"TRANSPORTLAYER FRAME master → slave")

        (
            node_address_byte,
            frame_type,
            expected_bytes,
            sid,
            payload,
        ) = self.parse_transportlayer_frame_header(databytes)

        if sid is None:
            pass
        else:
            sid_text = self.SERVICE_ID_MAPPING.get(sid, f"unknown (0x{sid:02x})")
            self.log.debug(f"   service id: {sid_text}")

        if sid == self.SID_READ_BY_IDENTIFIER and (
            self.protocol.IDENTIFIER == payload[1:]
        ):
            self.log.debug(f"   → handled by protocol!")
            self.protocol.receive_read_by_identifier_request(
                self,
            )

        elif (
            node_address_byte == self.protocol.NODE_ADDRESS
            or node_address_byte == self.NODE_ADDRESS_BROADCAST
        ):
            self.log.debug(f"   → potentially handled by protocol!")
            self.protocol.receive_transportlayer_frame(
                self, frame_type, expected_bytes, sid, payload
            )

    def parse_transportlayer_slave2master(self, databytes):

        self.log.debug(f"TRANSPORTLAYER FRAME slave → master")

        (
            node_address_byte,
            frame_type,
            expected_bytes,
            rsid,
            payload,
        ) = self.parse_transportlayer_frame_header(databytes)

        if rsid is None:
            pass
        elif rsid == 0x7F:
            # negative response
            self.log.debug(f"   negative response, error code = {databytes[3]:02x}")
        else:
            sid_mapped = self.SERVICE_ID_MAPPING.get(
                rsid - 0x40, f"unknown (0x{rsid:02x})"
            )
            self.log.debug(f"   positive response to {sid_mapped}")

    def response_waiting(self):
        return self.transportlayer_response_buffer and len(
            self.transportlayer_response_buffer
        )

    def loop_serial(self, serial: Serial, active):
        # Read three first bytes first - then decide whether to receive more or answer the request
        line = serial.read(3)
        if len(line) < 3:
            return

        if line[0] != 0x00 or line[1] != 0x55:
            # not synced to bytestream, wait for 0x00 0x55
            self.log.debug(
                f"in < {line[0]:02x} {line[1]:02x} not a proper sync -wait for sync-"
            )
            return

        # check parity
        raw_pid = line[2]
        try:
            pid = self.check_pid_parity(raw_pid)
        except self.ChecksumError as e:
            self.log.debug(f"in < {format_bytes(line)} PID parity error")
            return

        # pid is only the lower 6 bits
        pid = raw_pid & 0x3F

        if (pid == Lin.PID_TRANSPORTLAYER_SLAVE2MASTER and self.response_waiting()) or (
            pid in self.protocol.ANSWER_TO_PIDS
        ):
            if active:
                self.log.debug(
                    f"in < {format_bytes(line)} → checking if answer required"
                )
                self._answer_active(serial, pid, raw_pid)
                return
            else:
                self.log.debug(
                    f"in < {format_bytes(line)} → not considering answer (read-only mode)"
                )
        else:
            line += serial.read(9)
            self.log.debug(f"in < {format_bytes(line)} → processing")
            self._read_passive(pid, line[2:])

    def _read_passive(self, pid, line):
        if len(line) < 2:
            self.log.debug("→ → skipping empty line")
            return

        # Calculate checksum
        try:
            # Frame identifiers 60 (0x3C) to 61 (0x3D) shall always use classic checksum.
            if pid in [
                self.PID_TRANSPORTLAYER_MASTER2SLAVE,
                self.PID_TRANSPORTLAYER_SLAVE2MASTER,
            ]:
                self.check_checksum(line[1:])
            # Other frames use extended checksum including pid
            else:
                self.check_checksum(line[0:])
        except self.ChecksumError as e:
            self.log.warning(f"→ → checksum error: {e}")
            return

        if pid == self.PID_TRANSPORTLAYER_MASTER2SLAVE:
            self.log.debug(f"→ → identified as transportlayer MASTER → SLAVE")
            self.parse_transportlayer_master2slave(line[1:-1])
        elif pid == self.PID_TRANSPORTLAYER_SLAVE2MASTER:
            self.log.debug(f"→ → identified as transportlayer SLAVE → MASTER")
            self.parse_transportlayer_slave2master(line[1:-1])
        else:
            res = self.protocol.handle_message(pid, line[1:-1])
            if res:
                self.log.debug(f"→ → called protocol to handle")
            else:
                self.log.debug(f"→ → not handled by protocol")

    def _answer_active(self, serial, pid, raw_pid) -> bool:
        answer = None
        if pid in self.protocol.ANSWER_TO_PIDS:
            answer = self.protocol.ANSWER_TO_PIDS[pid](self.protocol)
        elif pid == self.PID_TRANSPORTLAYER_SLAVE2MASTER:
            answer = self._answer_transportlayer_request()

        if answer:
            # Frame identifiers 60 (0x3C) to 61 (0x3D) shall always use classic checksum.
            if pid in [
                self.PID_TRANSPORTLAYER_MASTER2SLAVE,
                self.PID_TRANSPORTLAYER_SLAVE2MASTER,
            ]:
                self._send_answer(serial, answer)
            else:
                self._send_answer(serial, answer, pid_for_checksum=raw_pid)
            self.log.debug("→ → sent answer")
            return True
        else:
            self.log.debug("→ → no need to answer")
            return False

    def _send_answer(self, serial, databytes, pid_for_checksum=None):
        if not pid_for_checksum:
            cs = calculate_checksum(databytes)
        else:
            cs = calculate_checksum(bytes([pid_for_checksum]) + databytes)
        # time.sleep(0.0005)
        serial.write(databytes)
        serial.write(bytes([cs]))
        serial.flush()
        # read back my own answer
        # serial.read(len(databytes) + 1)
        serial.reset_input_buffer()
        self.log.debug("out > " + format_bytes(databytes + bytes([cs])))

    def prepare_transportlayer_response(self, messages):
        self.transportlayer_response_buffer = messages

    def _answer_transportlayer_request(self):
        if self.transportlayer_response_buffer and len(
            self.transportlayer_response_buffer
        ):
            return self.transportlayer_response_buffer.pop(0)
        else:
            # self.log.warning("No messages in transportlayer response buffer.")
            return None
