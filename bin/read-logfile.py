import io
import argparse
import sys
from inetbox import Lin
from inetbox import InetBox

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # can be called with file to read from, or reads froms serial
    parser.add_argument("file", nargs="?", help="file to read from")
    # whether we try to answer messages
    parser.add_argument(
        "--active", action="store_true", help="play an active role on the bus"
    )
    # whether to log lin messages
    parser.add_argument("--log-lin", action="store_true", help="log lin messages")
    # whether to log inet box messages
    parser.add_argument(
        "--log-inet", action="store_true", help="log inet box protocol messages"
    )

    parser.add_argument(
        "--log-inet-data", action="store_true", help="log data acquired"
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

    inet = InetBox(args.log_inet, args.log_inet_data)

    if args.file:
        lin = Lin(inet, args.log_lin)
        with open(args.file, "r") as f:
            for line in f:
                line = line.strip()
                if line == "":
                    continue
                line_parts = line.split()
                data_bytes = bytes(int(x, 16) for x in line_parts[1:-2])
                # create BytesIO buffer to simulate serial input
                with io.BytesIO(bytes([0x00, 0x55]) + data_bytes) as f:
                    lin.loop_serial(f, False)

    else:
        ser = Serial("/dev/ttyS0", 9600, timeout=0.1)
        lin = Lin(inet, args.log_lin)
        while True:
            lin.loop_serial(ser, args.active)
