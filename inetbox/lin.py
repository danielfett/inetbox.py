import logging
import time
from .tools import format_bytes, calculate_checksum
from serial import Serial


class Lin:
    PID_TRANSPORTLAYER_MASTER2SLAVE = 0x3C
    PID_TRANSPORTLAYER_SLAVE2MASTER = 0x3D

    # A quiet LIN bus and a serial port that has silently stopped delivering
    # bytes are indistinguishable from in here - read() just keeps returning
    # b"" in both cases, without ever raising. The only thing that separates
    # them is how long the silence lasts, so keep track of that and complain
    # about it; the caller decides what to do about it (see
    # seconds_since_last_rx).
    SILENCE_WARN_SECONDS = 5.0
    SILENCE_REPEAT_SECONDS = 60.0

    # How often to summarise bytes that could not be synchronized to a frame.
    # Reported separately from the silence above so that "nothing arrives" and
    # "only garbage arrives" can be told apart in the log.
    DISCARD_REPORT_SECONDS = 10.0

    # The break condition preceding every sync byte reaches us as one or more
    # 0x00 bytes; how many depends on the UART/driver and the master's break
    # length. Used both to recognise the next frame's header and to keep the
    # break out of the discarded-byte statistics.
    MAX_BREAK_BYTES = 4

    # A queued transport-layer response is only meaningful for the poll it was
    # prepared for. If no poll collects it within this long, the master has
    # moved on and sending it later would answer the wrong request - and,
    # because the caller suspends its other work while a response is pending,
    # a response that is never collected would stall the whole service.
    RESPONSE_MAX_AGE_SECONDS = 5.0

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

    class ChecksumError(Exception):
        pass

    def __init__(self, protocol, debug=False):
        self.protocol = protocol
        self.log = logging.getLogger("inet.lin")

        # persistent receive buffer used to resync to the LIN byte stream;
        # kept across calls to loop_serial so a short/partial read never
        # loses bytes and misalignment can be recovered one byte at a time
        self._rx_buffer = bytearray()

        # instance attribute (not a class attribute!) so multiple Lin
        # instances never share the same queued-response state
        self.transportlayer_response_buffer = []

        # receive watchdog state, see SILENCE_WARN_SECONDS
        self._last_rx_time = time.monotonic()
        self._silence_logged_at = None

        # bytes dropped by the resync logic since _discard_window_start
        self._discarded_bytes = 0
        self._discard_window_start = time.monotonic()

        # when the response buffer last made progress, see
        # RESPONSE_MAX_AGE_SECONDS
        self._response_progress_at = time.monotonic()

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

    def seconds_since_last_rx(self):
        """Seconds since the last byte was read from the serial port.

        The caller uses this to decide whether the port itself has stopped
        working: a port whose device silently went away keeps a perfectly
        valid file descriptor, never signals readable and never raises, so
        the elapsed silence is the only symptom available.
        """
        return time.monotonic() - self._last_rx_time

    def reset_receive_state(self):
        """Drop all buffered receive state and restart the watchdog.

        Called after the serial port has been reopened. Anything still
        buffered belongs to a frame that is long gone, and a queued
        transport-layer response would end up answering the wrong poll, so
        none of it may survive the reconnect.
        """
        self._rx_buffer.clear()
        self.transportlayer_response_buffer.clear()
        self.protocol.reset_transportlayer_state()
        self._last_rx_time = time.monotonic()
        self._silence_logged_at = None
        self._discarded_bytes = 0
        self._discard_window_start = time.monotonic()
        self._response_progress_at = time.monotonic()

    def _check_silence(self):
        silence = self.seconds_since_last_rx()
        if silence < self.SILENCE_WARN_SECONDS:
            return

        now = time.monotonic()
        if (
            self._silence_logged_at is not None
            and now - self._silence_logged_at < self.SILENCE_REPEAT_SECONDS
        ):
            return

        self._silence_logged_at = now
        self.log.warning(
            f"no data received from the LIN bus for {silence:.1f}s - either the "
            f"bus is quiet or the serial port has stopped delivering data"
        )

    def _count_discarded(self, data):
        """Account for bytes dropped while resyncing, ignoring the break.

        Every frame is preceded by a break, so dropping a short run of
        leading 0x00 bytes is the normal course of events and must not be
        reported: a healthy bus splits frames across reads all the time,
        which would otherwise produce a continuous stream of complaints and
        drown out the case this is meant to surface. Anything beyond a
        plausible break - a line stuck dominant, or actual garbage - still
        counts.
        """
        break_bytes = 0
        while break_bytes < len(data) and data[break_bytes] == 0x00:
            break_bytes += 1

        self._discarded_bytes += len(data) - min(break_bytes, self.MAX_BREAK_BYTES)

    def _report_discarded_bytes(self):
        now = time.monotonic()
        if not self._discarded_bytes:
            # nothing dropped yet - the reporting window starts at the first
            # discarded byte, not at the last report
            self._discard_window_start = now
            return

        elapsed = now - self._discard_window_start
        if elapsed < self.DISCARD_REPORT_SECONDS:
            return

        self.log.warning(
            f"discarded {self._discarded_bytes} byte(s) in {elapsed:.1f}s that "
            f"could not be synchronized to a LIN frame"
        )
        self._discarded_bytes = 0
        self._discard_window_start = now

    def _expire_stale_responses(self):
        if not self.response_waiting():
            return
        age = time.monotonic() - self._response_progress_at
        if age < self.RESPONSE_MAX_AGE_SECONDS:
            return

        self.log.warning(
            f"dropping {len(self.transportlayer_response_buffer)} queued "
            f"transport-layer response(s) that were not collected for {age:.1f}s"
        )
        self.transportlayer_response_buffer.clear()
        self._response_progress_at = time.monotonic()

    def loop_serial(self, serial: Serial, active):
        self._report_discarded_bytes()
        self._expire_stale_responses()

        # First, try to make progress with whatever is already buffered
        # from a previous call, without touching the serial port at all.
        # This handles the case where several LIN frames queued up in the
        # UART FIFO while this process was briefly busy elsewhere: if a
        # complete frame - or one we must actively answer - is already
        # sitting in the buffer, it gets handled immediately instead of
        # first going through a read that could block.
        if self._process_buffer(serial, active):
            return

        # Nothing in the buffer is resolvable yet - block (up to the
        # configured timeout) waiting for more data. This blocking read is
        # also what paces this otherwise-unthrottled polling loop
        # (TrumaService's own loop has no sleep of its own), so a genuine
        # "need more bytes" case must actually wait here rather than spin:
        # skipping this wait whenever the buffer is merely non-empty - as
        # opposed to actionable - previously caused a tight, unpaced busy
        # loop (100% CPU, MQTT starved) whenever a header's payload never
        # fully arrived (e.g. real bus noise or the master going quiet).
        chunk = serial.read(max(serial.in_waiting, 1))
        if chunk:
            if self._silence_logged_at is not None:
                # report the recovery too - otherwise a gap that healed by
                # itself is indistinguishable in the log from one that never
                # did, and the length of the gap is the interesting part
                self.log.warning(
                    f"LIN bus data resumed after {self.seconds_since_last_rx():.1f}s "
                    f"of silence"
                )
                self._silence_logged_at = None
            self._last_rx_time = time.monotonic()
            self._rx_buffer.extend(chunk)
            self._process_buffer(serial, active)
        else:
            self._check_silence()

    def _process_buffer(self, serial: Serial, active) -> bool:
        """Try to resolve the next outcome from self._rx_buffer alone.

        Returns True if it made progress (processed/dropped something) and
        the caller does not need to wait for more data. Returns False if
        the buffer doesn't yet contain enough to decide anything, meaning
        the caller should block waiting for more bytes.
        """
        while True:
            sync_index = self._rx_buffer.find(0x55)
            if sync_index == -1:
                # no sync byte anywhere in the buffer - it's all noise
                self._count_discarded(self._rx_buffer)
                self._rx_buffer.clear()
                return False

            if len(self._rx_buffer) < sync_index + 2:
                # sync byte found, but the PID byte hasn't arrived yet
                self._count_discarded(self._rx_buffer[:sync_index])
                del self._rx_buffer[:sync_index]
                return False

            raw_pid = self._rx_buffer[sync_index + 1]
            try:
                pid = self.check_pid_parity(raw_pid)
            except self.ChecksumError:
                # coincidental 0x55 in noise/data - drop just that byte and
                # keep scanning the rest of the buffer for a real sync byte
                self._count_discarded(self._rx_buffer[: sync_index + 1])
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
            return True

        # The transport-layer "slave -> master" poll (and, defensively, its
        # "master -> slave" counterpart) may legitimately go unanswered -
        # neither we nor the real slave always have data queued. When that
        # happens the master sends no data field at all and proceeds
        # straight to the next frame's break+sync. If the bytes right after
        # the PID look like a break+sync pair, that's the next frame's
        # header, not this frame's payload - drop this (unanswered) header
        # instead of misreading the next frame as data. The break condition
        # doesn't always collapse to exactly one 0x00 byte on the wire (it
        # depends on the UART/driver and the master's exact break length),
        # so tolerate a short run of leading zero bytes before the 0x55.
        if pid in (
            self.PID_TRANSPORTLAYER_MASTER2SLAVE,
            self.PID_TRANSPORTLAYER_SLAVE2MASTER,
        ):
            probe = sync_index + 2
            probe_limit = min(len(self._rx_buffer), probe + self.MAX_BREAK_BYTES)
            while probe < probe_limit and self._rx_buffer[probe] == 0x00:
                probe += 1
            if (
                probe > sync_index + 2
                and probe < len(self._rx_buffer)
                and self._rx_buffer[probe] == 0x55
            ):
                self.log.debug(
                    f"in < pid {pid:02x} header went unanswered (no data field) → dropping"
                )
                del self._rx_buffer[: sync_index + 2]
                return True

        # full frame: PID + up to 9 more bytes (payload + checksum)
        frame_end = sync_index + 2 + 9
        if len(self._rx_buffer) < frame_end:
            # header confirmed, but the payload hasn't fully arrived yet -
            # keep the confirmed header in the buffer and let the caller
            # wait for more data
            self._count_discarded(self._rx_buffer[:sync_index])
            del self._rx_buffer[:sync_index]
            return False

        line = bytes([raw_pid]) + bytes(self._rx_buffer[sync_index + 2 : frame_end])
        # anything ahead of the sync byte is dropped along with the frame -
        # normally just the break, but account for it so that junk sitting
        # between otherwise valid frames does not go unnoticed
        self._count_discarded(self._rx_buffer[:sync_index])
        del self._rx_buffer[:frame_end]

        self.log.debug(f"in < {format_bytes(bytes([0x55]) + line)} → processing")
        self._read_passive(pid, line)
        return True

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
        # single write() call so the response goes out as one contiguous
        # transmission - on a USB-serial adapter, separate write() calls can
        # become separate USB transfers with a small gap between them,
        # which risks making the response look malformed on the bus
        serial.write(databytes + bytes([cs]))
        serial.flush()
        # read back my own answer
        # serial.read(len(databytes) + 1)
        serial.reset_input_buffer()
        self.log.debug("out > " + format_bytes(databytes + bytes([cs])))

    def prepare_transportlayer_response(self, messages):
        if not self.response_waiting():
            self._response_progress_at = time.monotonic()
        self.transportlayer_response_buffer += messages

    def _answer_transportlayer_request(self):
        if self.response_waiting():
            # the queue is being drained as intended - restart the age clock
            self._response_progress_at = time.monotonic()
            return self.transportlayer_response_buffer.pop(0)
        else:
            # self.log.warning("No messages in transportlayer response buffer.")
            return None
