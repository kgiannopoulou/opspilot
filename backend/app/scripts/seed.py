"""Load the ShopStream demo environment.

    python -m app.scripts.seed            # no-op if data already exists
    python -m app.scripts.seed --reset    # wipe and reload (timestamps are relative to "now")

What gets loaded:
  * roles + a five-person support team, the service catalogue and its dependency graph
  * 60 days of resolved incident history with linked (closed) tickets — trends, MTTR, similar incidents
  * the last 6 hours of simulated logs and metrics with three live incidents injected, plus the ShopStream
    orders/payments/password-reset rows those logs describe (the read-only sandbox for investigation queries)
  * the customer tickets those live incidents caused, plus an everyday backlog
  * the knowledge base (runbooks, troubleshooting guides, architecture notes)
  * completed investigations for two of the live incidents; the newest one is left for you to investigate
  * AI triage suggestions for open tickets: incident tickets accepted, one corrected, the rest awaiting review
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import Base, Incident, IncidentTicket, Role, Service, Ticket, TicketEvent, User
from app.models.enums import IncidentStatus, Priority, Severity, TicketCategory, TicketSource, TicketStatus
from app.repositories.observability import bulk_insert_app_data, bulk_insert_logs, bulk_insert_metrics
from app.repositories.tickets import TicketRepository
from app.schemas.ticket import CommentCreate, TicketCreate, TicketTransition
from app.services.classification import ClassificationService
from app.services.incidents import IncidentService
from app.services.investigations import InvestigationService
from app.services.knowledge import load_knowledge_base
from app.services.tickets import TicketService
from simulator.app_data import derive_app_data
from simulator.engine import ScheduledIncident, simulate
from simulator.incidents.scenarios import SCENARIOS
from simulator.topology import SERVICES

DEMO_PASSWORD = "opspilot-demo"  # documented in the README; every demo account uses it

# Tables whose rows come from migrations (reference data) and must survive a reset.
REFERENCE_TABLES = {"roles"}

TEAM = [
    ("maria@opspilot.dev", "Maria Papadopoulou", "engineer"),
    ("alex@opspilot.dev", "Alex Chen", "engineer"),
    ("sam@opspilot.dev", "Sam Rivera", "support_agent"),
    ("nikos@opspilot.dev", "Nikos Georgiou", "admin"),
    ("viewer@opspilot.dev", "Dana Viewer", "viewer"),
]


@dataclass(frozen=True)
class LiveIncident:
    scenario: str
    started_minutes_ago: int
    duration_minutes: int | None  # None = still ongoing
    status: IncidentStatus
    investigated: bool  # pre-run the investigation engine so the demo opens with results


LIVE_INCIDENTS = [
    LiveIncident("db_pool_exhaustion", 150, 40, IncidentStatus.MONITORING, investigated=True),
    LiveIncident("smtp_auth_failure", 18, None, IncidentStatus.INVESTIGATING, investigated=False),
    LiveIncident("payment_gateway_timeout", 34, None, IncidentStatus.INVESTIGATING, investigated=True),
]


@dataclass(frozen=True)
class PastIncident:
    title: str
    service: str
    severity: Severity
    signature: str
    root_cause: str


PAYMENT_GATEWAY_HISTORY = PastIncident(
    "Checkout payments failing — gateway timeouts",
    "payment-service",
    Severity.SEV1,
    "GatewayTimeoutException",
    "Payment gateway (stripe) regional degradation pushed p95 above the 5 s client timeout. Mitigated by "
    "raising the timeout to 10 s with idempotent retries; provider confirmed and fixed the degradation.",
)
POOL_HISTORY = PastIncident(
    "Merchant dashboard timing out",
    "order-service",
    Severity.SEV2,
    "QueuePool limit",
    "Revenue report job ran an unindexed GROUP BY on order_items and held connections until the pool was "
    "exhausted. Added index on order_items(order_id) and moved the job to the read replica.",
)
OTHER_HISTORY = [
    PastIncident(
        "Login latency spike",
        "auth-service",
        Severity.SEV3,
        "bcrypt cost",
        "bcrypt cost factor raised from 10 to 14 in a config change; reverted to 12.",
    ),
    PastIncident(
        "Webhook delivery backlog",
        "notification-service",
        Severity.SEV3,
        "QueueBacklog",
        "Consumer crash-looped on a malformed payload; added dead-letter queue.",
    ),
    PastIncident(
        "Elevated card declines",
        "payment-service",
        Severity.SEV2,
        "CardDeclined",
        "Issuer-side outage for one card network; no action on our side.",
    ),
    PastIncident(
        "Order confirmation e-mails delayed",
        "notification-service",
        Severity.SEV3,
        "SMTP 421",
        "SMTP relay rate limit reached during campaign; raised plan limits.",
    ),
    PastIncident(
        "Primary database failover",
        "postgres-primary",
        Severity.SEV1,
        "connection reset",
        "Hardware failure on primary host; automatic failover took 4 minutes.",
    ),
    PastIncident(
        "Sessions invalidated after deploy",
        "auth-service",
        Severity.SEV2,
        "InvalidSignatureError",
        "Signing key rotated without overlap window; restored previous key as secondary.",
    ),
    PastIncident(
        "Checkout lock contention",
        "order-service",
        Severity.SEV2,
        "LockNotAvailable",
        "Flash sale caused row-lock contention on inventory; switched to SKIP LOCKED reservation.",
    ),
    PastIncident(
        "Disk usage alert on primary DB",
        "postgres-primary",
        Severity.SEV4,
        "disk_usage",
        "WAL archiving stalled; fixed archive_command credentials.",
    ),
]

DURATION_MINUTES = {Severity.SEV1: (25, 120), Severity.SEV2: (40, 240), Severity.SEV3: (60, 480),
                    Severity.SEV4: (120, 1440)}  # fmt: skip

BACKLOG = [
    ("Invoice PDF shows wrong VAT rate", "Greek merchants see 23% applied to reduced-rate goods.", "P3",
     "order-service", TicketStatus.TRIAGED),
    ("Webhook retries arrive out of order", "order.updated delivered before order.created for m_207.", "P3",
     "notification-service", TicketStatus.INVESTIGATING),
    ("MFA code e-mail delayed by ~2 minutes", "Reported by 3 merchants this morning.", "P3", "auth-service",
     TicketStatus.OPEN),
    ("Refund button greyed out for partial refunds", "Partial refunds on split shipments cannot be issued.", "P2",
     "payment-service", TicketStatus.RESOLVED),
    ("Typo in password reset e-mail", "'Pasword' in the subject line.", "P4", "notification-service",
     TicketStatus.CLOSED),
    ("Export to CSV times out for large stores", "Stores with >50k orders get a 504 on export.", "P3",
     "order-service", TicketStatus.OPEN),
]  # fmt: skip

SEVERITY_TO_PRIORITY = {Severity.SEV1: Priority.P1, Severity.SEV2: Priority.P2, Severity.SEV3: Priority.P3,
                        Severity.SEV4: Priority.P4}  # fmt: skip


def reset(db: Session) -> None:
    tables = ", ".join(
        f'"{t.schema}"."{t.name}"' if t.schema else f'"{t.name}"'
        for t in Base.metadata.sorted_tables
        if t.name not in REFERENCE_TABLES
    )
    db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    db.execute(text("ALTER SEQUENCE ticket_number_seq RESTART"))
    db.execute(text("ALTER SEQUENCE incident_number_seq RESTART"))
    db.commit()


def seed_catalog(db: Session) -> tuple[dict[str, User], dict[str, Service]]:
    roles = {r.name: r for r in db.scalars(select(Role))}
    password_hash = hash_password(DEMO_PASSWORD)
    users = {
        email: User(email=email, full_name=name, role_id=roles[role].id, password_hash=password_hash)
        for email, name, role in TEAM
    }
    services = {
        s.name: Service(
            name=s.name, display_name=s.display_name, tier=s.tier, owner_team=s.owner_team, description=s.description
        )
        for s in SERVICES.values()
    }
    for spec in SERVICES.values():
        services[spec.name].dependencies = [services[d] for d in spec.dependencies]
    db.add_all([*users.values(), *services.values()])
    db.commit()
    return users, services


def seed_history(db: Session, now: datetime, users: dict[str, User], services: dict[str, Service]) -> None:
    """Resolved incidents over the last 60 days, each with the closed customer tickets that reported it."""
    rng = random.Random(7)
    engineers = [users["maria@opspilot.dev"], users["alex@opspilot.dev"]]
    reporter = users["sam@opspilot.dev"]
    tickets = TicketRepository(db)

    plan: list[tuple[datetime, PastIncident]] = [
        (now - timedelta(days=41, hours=5), PAYMENT_GATEWAY_HISTORY),
        (now - timedelta(days=23, hours=2), POOL_HISTORY),
    ]
    for _ in range(30):
        plan.append((now - timedelta(days=rng.uniform(1, 60)), rng.choice(OTHER_HISTORY)))
    plan.sort(key=lambda item: item[0])

    for started, past in plan:
        started = started.replace(second=0, microsecond=0)
        resolved = started + timedelta(minutes=rng.randint(*DURATION_MINUTES[past.severity]))
        incident = Incident(
            number=f"INC-{db.scalar(select(func.nextval('incident_number_seq')))}",
            title=past.title,
            summary=past.root_cause.split(".")[0] + ".",
            severity=past.severity,
            status=IncidentStatus.RESOLVED,
            primary_service_id=services[past.service].id,
            commander_id=rng.choice(engineers).id,
            root_cause=past.root_cause,
            signature=past.signature,
            started_at=started,
            detected_at=started + timedelta(minutes=rng.randint(1, 8)),
            resolved_at=resolved,
            created_at=started,
        )
        incident.affected_services = [services[past.service]]
        db.add(incident)
        db.flush()
        for _ in range(rng.randint(1, 3)):
            created = started + timedelta(minutes=rng.randint(2, 20))
            ticket = Ticket(
                number=tickets.next_number(),
                title=f"Customer report: {past.title.lower()}",
                description="Reported via support chat.",
                priority=SEVERITY_TO_PRIORITY[past.severity],
                status=TicketStatus.CLOSED,
                reporter_id=reporter.id,
                assignee_id=incident.commander_id,
                service_id=services[past.service].id,
                resolution=past.root_cause,
                created_at=created,
                triaged_at=created + timedelta(minutes=5),
                resolved_at=resolved,
                closed_at=resolved + timedelta(hours=2),
            )
            db.add(ticket)
            db.flush()
            db.add(IncidentTicket(incident_id=incident.id, ticket_id=ticket.id, linked_at=created))

    # Everyday tickets that never became incidents: a steady background volume for the trend charts.
    for day in range(60, 0, -1):
        for _ in range(rng.randint(2, 6)):
            created = (now - timedelta(days=day, minutes=rng.randint(0, 1439))).replace(microsecond=0)
            svc = rng.choice(list(services.values()))
            db.add(
                Ticket(
                    number=tickets.next_number(),
                    title=rng.choice(
                        [
                            "How do I export orders?",
                            "Question about payout schedule",
                            "Cannot update store logo",
                            "Customer did not receive receipt",
                            "API key rotation question",
                        ]
                    ),  # fmt: skip
                    description="Handled by first-line support.",
                    priority=rng.choice([Priority.P3, Priority.P4]),
                    status=TicketStatus.CLOSED,
                    source=TicketSource.EMAIL,
                    reporter_id=reporter.id,
                    service_id=svc.id,
                    resolution="Answered.",
                    created_at=created,
                    closed_at=created + timedelta(hours=rng.randint(1, 30)),
                )
            )
    db.commit()


def seed_live(db: Session, now: datetime, users: dict[str, User], services: dict[str, Service]) -> None:
    start = now - timedelta(hours=6)
    scheduled = [
        ScheduledIncident(
            live.scenario,
            now - timedelta(minutes=live.started_minutes_ago),
            timedelta(minutes=live.duration_minutes or live.started_minutes_ago),
        )
        for live in LIVE_INCIDENTS
    ]
    result = simulate(start, now, scheduled, seed=42)
    service_ids = {name: svc.id for name, svc in services.items()}
    bulk_insert_logs(db, result.logs, service_ids)
    bulk_insert_metrics(db, result.metrics, service_ids)
    bulk_insert_app_data(db, derive_app_data(result.logs))  # the ShopStream tables investigation queries read
    db.commit()

    tickets, incidents = TicketService(db), IncidentService(db)
    sam, maria = users["sam@opspilot.dev"], users["maria@opspilot.dev"]
    for live, sched in zip(LIVE_INCIDENTS, scheduled, strict=True):
        scenario = SCENARIOS[live.scenario]
        numbers = []
        for i, report in enumerate(scenario.reports):
            ticket = tickets.create(
                TicketCreate(
                    title=report.title,
                    description=report.description,
                    priority=Priority(report.priority),
                    service=scenario.symptom_service,
                    labels=["customer-impact"],
                ),
                sam,
            )
            tickets.assign(ticket.number, maria.id, sam)
            tickets.transition(ticket.number, TicketTransition(status=TicketStatus.TRIAGED), maria)
            tickets.transition(ticket.number, TicketTransition(status=TicketStatus.INVESTIGATING), maria)
            tickets.comment(ticket.number, CommentCreate(body="Linked to the active incident.", internal=True), maria)
            # Customers report a few minutes after onset; the ticket's timeline follows from there.
            _backdate(db, ticket, sched.start + timedelta(minutes=4 + 3 * i))
            numbers.append(ticket.number)

        affected = sorted({scenario.symptom_service, scenario.root_cause_service})
        incident = incidents.declare(
            title=scenario.title,
            severity=Severity(scenario.severity),
            primary_service=scenario.symptom_service,
            affected_services=affected,
            started_at=sched.start,
            actor=maria,
            commander=maria,
            summary=scenario.reports[0].description,
            signature=None,  # unknown until investigated
            ticket_numbers=numbers,
        )
        if live.status != IncidentStatus.INVESTIGATING:
            incident.status = live.status
            db.commit()
        if live.investigated:
            InvestigationService(db).run(incident, maria, now=now)


def _backdate(db: Session, ticket: Ticket, created: datetime) -> None:
    ticket.created_at = created
    ticket.triaged_at = created + timedelta(minutes=2) if ticket.triaged_at else None
    events = db.scalars(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id).order_by(TicketEvent.id))
    for n, event in enumerate(list(events)):
        event.created_at = created + timedelta(minutes=n)
    db.execute(update(Ticket).where(Ticket.id == ticket.id).values(updated_at=created + timedelta(minutes=5)))
    db.commit()


def seed(db: Session, now: datetime | None = None) -> None:
    now = (now or datetime.now(UTC)).replace(second=0, microsecond=0)
    users, services = seed_catalog(db)
    seed_history(db, now, users, services)
    for email, *_ in TEAM:
        db.refresh(users[email])
    load_knowledge_base(db)
    seed_live(db, now, users, services)
    seed_backlog(db, users)
    seed_triage(db, users)


def seed_backlog(db: Session, users: dict[str, User]) -> None:
    tickets = TicketService(db)
    sam, alex = users["sam@opspilot.dev"], users["alex@opspilot.dev"]
    paths = {
        TicketStatus.OPEN: [],
        TicketStatus.TRIAGED: [TicketStatus.TRIAGED],
        TicketStatus.INVESTIGATING: [TicketStatus.TRIAGED, TicketStatus.INVESTIGATING],
        TicketStatus.RESOLVED: [TicketStatus.INVESTIGATING, TicketStatus.RESOLVED],
        TicketStatus.CLOSED: [TicketStatus.CLOSED],
    }
    for title, description, priority, service, final in BACKLOG:
        ticket = tickets.create(
            TicketCreate(
                title=title, description=description, priority=Priority(priority), service=service,
                source=TicketSource.EMAIL,
            ),
            sam,
        )  # fmt: skip
        if paths[final]:
            tickets.assign(ticket.number, alex.id, sam)
        for status in paths[final]:
            needs_note = status in {TicketStatus.RESOLVED, TicketStatus.CLOSED}
            note = "Handled — see ticket history." if needs_note else None
            tickets.transition(ticket.number, TicketTransition(status=status, resolution=note), alex)


def seed_triage(db: Session, users: dict[str, User]) -> None:
    """Classify every ticket still in the queue, and show both outcomes of human review."""
    classifier = ClassificationService(db)
    maria, sam = users["maria@opspilot.dev"], users["sam@opspilot.dev"]
    linked = set(db.scalars(select(IncidentTicket.ticket_id)))
    open_tickets = db.scalars(select(Ticket).where(Ticket.status != TicketStatus.CLOSED).order_by(Ticket.id))
    for ticket in list(open_tickets):
        suggestion = classifier.classify(ticket)
        if ticket.id in linked:
            classifier.review(ticket, suggestion.id, "accept", maria)
        elif ticket.title.startswith("MFA code"):
            classifier.review(ticket, suggestion.id, "modify", sam, category=TicketCategory.NOTIFICATIONS,
                              priority=Priority.P3, note="Single relay, a few merchants: P3")  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="truncate all data tables first")
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.reset:
            reset(db)
        elif db.scalar(select(func.count()).select_from(User)):
            print("seed: data already present, skipping (use --reset to reload)")
            return
        seed(db)
        counts = {
            model.__tablename__: db.scalar(select(func.count()).select_from(model)) for model in (Ticket, Incident)
        }
        print(f"seed: loaded {counts}")


if __name__ == "__main__":
    main()
