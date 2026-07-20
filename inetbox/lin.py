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

    transportlayer_response_buffer = []

    class ChecksumError(Exception):
        pass

    def __init__(self, protocol, debug=False):
        self.protocol = protocol
        self.log = logging.getLogger("inet.lin")

        # persistent receive buffer used to resync to the LIN byte stream;
        # kept across calls to loop_serial so a short/partial read never
        # loses bytes and misalignment can be recovered one byte at a time
        self._rx_buffer = bytearray()

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
        return len(self.transportlayer_response_buffer) > 0

    def loop_serial(self, serial: Serial, active):
        # Pull in whatever bytes are currently available (or wait up to the
        # configured timeout for at least one) and append them to a
        # persistent buffer. Using a persistent buffer instead of one-shot
        # fixed-size reads means a short/partial read never silently drops
        # bytes, and resync after a misalignment can happen one byte at a
        # time instead of in fixed 3-byte jumps.
        chunk = serial.read(max(serial.in_waiting, 1))
        if chunk:
            self._rx_buffer.extend(chunk)

        while True:
            sync_index = self._rx_buffer.find(0x55)
            if sync_index == -1:
                # no sync byte anywhere in the buffer - it's all noise
                self._rx_buffer.clear()
                return

            if len(self._rx_buffer) < sync_index + 2:
                # sync byte found, but the PID byte hasn't arrived yet
                del self._rx_buffer[:sync_index]
                return

            raw_pid = self._rx_buffer[sync_index + 1]
            try:
                pid = self.check_pid_parity(raw_pid)
            except self.ChecksumError:
                # coincidental 0x55 in noise/data - drop just that byte and
                # keep scanning the rest of the buffer for a real sync byte
                del self._rx_buffer[: sync_index + 1]
                continue

            # sync byte + parity-valid PID found - treat as synced. The
            # preceding break byte is a nice-to-have confirmation, not a
            # hard requirement (its exact byte count on the wire depends on
            # the UART/driver), so it's only logged, not enforced.
            if sync_index > 0 and self._rx_buffer[sync_index - 1] != 0x00:
                self.log.debug(
                    f"in < resynced on sync+PID parity without a preceding "
                    f"break byte (saw {self._rx_buffer[sync_index - 1]:02x})"
                )
            break

        if (pid == Lin.PID_TRANSPORTLAYER_SLAVE2MASTER and self.response_waiting()) or (
            pid in self.protocol.ANSWER_TO_PIDS
        ):
            # header only - consume up to and including the PID byte
            del self._rx_buffer[: sync_index + 2]

            if active:
                self.log.debug(
                    f"in < {format_bytes(bytes([0x55, raw_pid]))} → checking if answer required"
                )
                answered = self._answer_active(serial, pid, raw_pid)
                if answered:
                    # the port's input buffer was reset as part of sending
                    # the answer - the shadow buffer must follow suit
                    self._rx_buffer.clear()
            else:
                self.log.debug(
                    f"in < {format_bytes(bytes([0x55, raw_pid]))} → not considering answer (read-only mode)"
                )
            return

        # full frame: PID + up to 9 more bytes (payload + checksum)
        frame_end = sync_index + 2 + 9
        if len(self._rx_buffer) < frame_end:
            # header confirmed, but the payload hasn't fully arrived yet -
            # keep the confirmed header in the buffer and retry next call
            del self._rx_buffer[:sync_index]
            return

        line = bytes([raw_pid]) + bytes(self._rx_buffer[sync_index + 2 : frame_end])
        del self._rx_buffer[:frame_end]

        self.log.debug(f"in < {format_bytes(bytes([0x55]) + line)} → processing")
        self._read_passive(pid, line)

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
            self.log.warning(
                f"→ → checksum error on pid {pid:02x} ({len(line)} bytes "
                f"incl. pid): {e} - frame was {format_bytes(line)}"
            )
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
        self.transportlayer_response_buffer += messages

    def _answer_transportlayer_request(self):
        if self.response_waiting():
            return self.transportlayer_response_buffer.pop(0)
        else:
            # self.log.warning("No messages in transportlayer response buffer.")
            return None
