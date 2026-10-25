"""Entry point: `python -m app.workers.main` (the `worker` service in docker-compose)."""

import logging
import signal
import threading

import redis

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.workers.worker import Worker


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Blocking reads need a socket timeout longer than the block time (the API's client uses 0.5 s).
    client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True, socket_timeout=30)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())  # finish the current job, then exit
    Worker(SessionLocal, client).run_forever(stop)


if __name__ == "__main__":
    main()
