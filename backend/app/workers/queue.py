"""A small, explicit job queue on Redis Streams.

    XADD opspilot:jobs  {job_id, type}         producer (API)
    XREADGROUP workers  > COUNT n               each message goes to exactly one worker of the group
    XACK                                        after the job finished (successfully or not)
    XAUTOCLAIM idle > 60 s                      a worker that died mid-job: its messages are taken over
    ZADD opspilot:jobs:delayed  due → message   retries with exponential backoff
    XADD opspilot:jobs:dead                     out of attempts: kept for inspection

Messages carry only the job id. The job itself (payload, status, attempts, error) is a row in PostgreSQL
(background_jobs), so Redis can be flushed without losing track of work. Why not Celery/RQ: the semantics fit on
one screen, there is nothing to configure, and RQ's forking worker does not run on Windows.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import redis

STREAM = "opspilot:jobs"
GROUP = "workers"
DELAYED = "opspilot:jobs:delayed"
DEAD = "opspilot:jobs:dead"
STALE_AFTER_MS = 60_000
MAX_STREAM_LENGTH = 10_000  # approximate trim; history lives in PostgreSQL


@dataclass(frozen=True)
class Message:
    id: str
    job_id: str
    type: str


class JobQueue:
    def __init__(self, client: redis.Redis) -> None:
        self.client = client

    def ensure_group(self) -> None:
        try:
            self.client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def enqueue(self, job_id: str, type_: str) -> str:
        return self.client.xadd(STREAM, {"job_id": job_id, "type": type_}, maxlen=MAX_STREAM_LENGTH, approximate=True)

    def retry_later(self, job_id: str, type_: str, delay_seconds: float) -> None:
        self.client.zadd(DELAYED, {json.dumps({"job_id": job_id, "type": type_}): time.time() + delay_seconds})

    def promote_due(self) -> int:
        """Move retries whose backoff has elapsed back onto the stream."""
        moved = 0
        for raw in self.client.zrangebyscore(DELAYED, "-inf", time.time()):
            if self.client.zrem(DELAYED, raw):  # only the worker that removed it re-enqueues it
                item = json.loads(raw)
                self.enqueue(item["job_id"], item["type"])
                moved += 1
        return moved

    def read(self, consumer: str, count: int = 1, block_ms: int | None = None) -> list[Message]:
        self.ensure_group()
        response = self.client.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=count, block=block_ms)
        return [Message(mid, f["job_id"], f["type"]) for _, entries in response or [] for mid, f in entries]

    def reclaim_stale(self, consumer: str, min_idle_ms: int = STALE_AFTER_MS) -> list[Message]:
        self.ensure_group()
        _, entries, *_ = self.client.xautoclaim(STREAM, GROUP, consumer, min_idle_time=min_idle_ms, count=10)
        return [Message(mid, f["job_id"], f["type"]) for mid, f in entries if f]

    def ack(self, message_id: str) -> None:
        self.client.xack(STREAM, GROUP, message_id)

    def dead_letter(self, job_id: str, type_: str, error: str) -> None:
        self.client.xadd(DEAD, {"job_id": job_id, "type": type_, "error": error[:500]}, maxlen=1_000, approximate=True)

    def stats(self) -> dict[str, int]:
        self.ensure_group()
        pending = self.client.xpending(STREAM, GROUP)
        groups = {g["name"]: g for g in self.client.xinfo_groups(STREAM)}
        return {
            "waiting": int(groups.get(GROUP, {}).get("lag") or 0),
            "in_progress": int(pending.get("pending", 0)),
            "delayed": int(self.client.zcard(DELAYED)),
            "dead": int(self.client.xlen(DEAD)) if self.client.exists(DEAD) else 0,
        }
