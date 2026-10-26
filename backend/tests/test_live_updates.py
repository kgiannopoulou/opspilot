"""Server-Sent Events for live investigation progress (ADR 004)."""

import threading
import time

from app.models import Incident
from app.services.investigations import InvestigationService
from app.services.live_updates import sse
from tests.conftest import as_user


def _events(response) -> list[tuple[str, str]]:
    """(event, data) pairs from a text/event-stream body; comments and retry fields skipped."""
    events = []
    for block in "".join(response.iter_text()).split("\n\n"):
        lines = [line for line in block.splitlines() if ": " in line and not line.startswith(":")]
        fields = dict(line.split(": ", 1) for line in lines)
        if "event" in fields:
            events.append((fields["event"], fields.get("data", "")))
    return events


def test_sse_framing():
    assert sse("progress", '{"a": 1}', 3) == 'event: progress\nid: 3\ndata: {"a": 1}\n\n'
    assert sse("done", "line1\nline2") == "event: done\ndata: line1\ndata: line2\n\n"


def test_finished_investigation_streams_its_state_then_done(client, demo, db):
    users, incidents = demo
    incident = db.query(Incident).filter_by(number=incidents["Payment checkout timeout"]).one()
    run = InvestigationService(db).run(incident, None)
    url = f"/api/v1/incidents/{incident.number}/investigations/{run.id}/events"

    with client.stream("GET", url, headers=as_user(users["viewer"])) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        events = _events(response)
    assert [e for e, _ in events] == ["progress", "done"]
    assert '"status":"COMPLETED"' in events[0][1]


def test_stream_pushes_each_stage_until_the_worker_finishes(client, demo, db, session_factory):
    users, incidents = demo
    incident = db.query(Incident).filter_by(number=incidents["Payment checkout timeout"]).one()
    run = InvestigationService(db).request(incident, users["maria"])

    def worker() -> None:
        time.sleep(0.5)
        with session_factory() as session:
            service = InvestigationService(session)
            service.execute(service.get(session.get(Incident, incident.id), run.id))

    thread = threading.Thread(target=worker)
    thread.start()
    url = f"/api/v1/incidents/{incident.number}/investigations/{run.id}/events"
    with client.stream("GET", url, headers=as_user(users["maria"])) as response:
        events = _events(response)
    thread.join()

    statuses = [data.split('"status":"')[1].split('"')[0] for event, data in events if event == "progress"]
    assert statuses[0] == "QUEUED" and statuses[-1] == "COMPLETED"
    assert "RUNNING" in statuses  # intermediate stages were pushed, not just the end state
    assert events[-1][0] == "done"


def test_stream_requires_authentication_and_a_real_investigation(client, demo):
    users, incidents = demo
    number = incidents["Payment checkout timeout"]
    assert client.get(f"/api/v1/incidents/{number}/investigations/1/events").status_code == 401
    missing = client.get(f"/api/v1/incidents/{number}/investigations/999999/events", headers=as_user(users["viewer"]))
    assert missing.status_code == 404  # resolved before the stream opens
