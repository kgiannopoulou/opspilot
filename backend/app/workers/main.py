"""Entry point: `python -m app.workers.main` (the `worker` service in docker-compose)."""

import signal
import threading

import redis
from prometheus_client import start_http_server

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.observability import REGISTRY, configure_logging
from app.workers.worker import Worker


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_format, settings.log_level)
    # Job counters and durations live in this process; Prometheus scrapes them here (the API exposes the rest).
    start_http_server(settings.worker_metrics_port, registry=REGISTRY)
    # Blocking reads need a socket timeout longer than the block time (the API's client uses 0.5 s).
    client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True, socket_timeout=30)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())  # finish the current job, then exit
    Worker(SessionLocal, client).run_forever(stop)


if __name__ == "__main__":
    main()
