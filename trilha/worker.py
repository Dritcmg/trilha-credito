import argparse
import logging
import os
import signal
import sys
import threading

from trilha.db import database
from trilha.service import process_one


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    engine, factory = database(os.environ["DATABASE_URL"])
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        while not stop.is_set():
            try:
                worked = process_one(factory)
            except Exception:
                # No raw SQL, exception, body or credential is logged.
                logging.error('{"event":"worker_attempt_failed"}')
                if args.once:
                    sys.exit(1)
                worked = False
            if args.once:
                break
            if not worked:
                stop.wait(0.5)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
