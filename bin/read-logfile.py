import io
import argparse
import sys
from inetbox import *
import logging


class BufferAsSerial:
    """Minimal serial.Serial stand-in over an in-memory buffer.

    Lin.loop_serial sizes its read from serial.in_waiting, which a plain
    BytesIO does not offer.
    """

    def __init__(self, data):
        self._buffer = io.BytesIO(data)
        self.in_waiting = len(data)

    def read(self, size):
        data = self._buffer.read(size)
        self.in_waiting -= len(data)
        return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # can be called with file to read from, or reads froms serial
    parser.add_argument("file", help="file to read from")
    # allow to configure first and last data byte position
    parser.add_argument("--first", help="first data byte position", type=int, default=1)
    parser.add_argument(
        "--last", help="end of data bytes position", type=int, default=-2
    )
    args = parser.parse_args()

    # enable logging with colored output
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.DEBUG,
        format="%(asctime)s %(name)5s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # logging.getLogger().addHandler(logging.StreamHandler())

    inetapp = InetboxApp(True, "none")
    inetprotocol = InetboxLINProtocol(inetapp, True)
    lin = Lin(inetprotocol, True)

    with open(args.file, "r") as f:
        for line in f:
            line = line.strip()
            if line == "":
                continue
            line_parts = line.split()
            data_bytes = bytes(int(x, 16) for x in line_parts[args.first : args.last])
            # simulate serial input, and keep feeding it to the LIN layer
            # until it has consumed the whole frame
            fake_serial = BufferAsSerial(bytes([0x00, 0x55]) + data_bytes)
            while fake_serial.in_waiting:
                lin.loop_serial(fake_serial, False)
