"""Load demo data: the ShopStream service catalogue, a support team and a realistic ticket queue.

python -m app.scripts.seed            # no-op if data already exists
python -m app.scripts.seed --reset    # wipe and reload
"""

from __future__ import annotations

import argparse

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import Base, Service, Ticket, User
from app.models.enums import Priority, TicketSource, TicketStatus
from app.schemas.ticket import CommentCreate, TicketCreate, TicketTransition
from app.services.tickets import TicketService
from simulator.incidents.scenarios import SCENARIOS
from simulator.topology import SERVICES

TEAM = [
    ("maria@opspilot.dev", "Maria Papadopoulou"),
    ("alex@opspilot.dev", "Alex Chen"),
    ("sam@opspilot.dev", "Sam Rivera"),
    ("nikos@opspilot.dev", "Nikos Georgiou"),
    ("viewer@opspilot.dev", "Dana Viewer"),
]

# Everyday tickets that are not part of an incident: (title, description, priority, service, final status)
BACKLOG = [
    (
        "Invoice PDF shows wrong VAT rate",
        "Greek merchants see 23% applied to reduced-rate goods.",
        "P3",
        "order-service",
        TicketStatus.TRIAGED,
    ),
    (
        "Webhook retries arrive out of order",
        "order.updated delivered before order.created for m_207.",
        "P3",
        "notification-service",
        TicketStatus.INVESTIGATING,
    ),
    (
        "MFA code e-mail delayed by ~2 minutes",
        "Reported by 3 merchants this morning.",
        "P3",
        "auth-service",
        TicketStatus.OPEN,
    ),
    (
        "Refund button greyed out for partial refunds",
        "Partial refunds on split shipments cannot be issued.",
        "P2",
        "payment-service",
        TicketStatus.RESOLVED,
    ),
    (
        "Typo in password reset e-mail",
        "'Pasword' in the subject line.",
        "P4",
        "notification-service",
        TicketStatus.CLOSED,
    ),
    (
        "Export to CSV times out for large stores",
        "Stores with >50k orders get a 504 on export.",
        "P3",
        "order-service",
        TicketStatus.OPEN,
    ),
]


def reset(db: Session) -> None:
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    db.execute(text("ALTER SEQUENCE ticket_number_seq RESTART"))
    db.commit()


def seed(db: Session) -> None:
    users = {email: User(email=email, full_name=name) for email, name in TEAM}
    db.add_all(users.values())
    db.add_all(
        Service(
            name=s.name,
            display_name=s.display_name,
            tier=s.tier,
            owner_team=s.owner_team,
            description=s.description,
        )
        for s in SERVICES.values()
    )
    db.commit()

    tickets = TicketService(db)
    sam, maria, alex = users["sam@opspilot.dev"], users["maria@opspilot.dev"], users["alex@opspilot.dev"]

    for scenario in SCENARIOS.values():
        for report in scenario.reports:
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
            tickets.comment(ticket.number, CommentCreate(body="Looking into it now.", internal=True), maria)

    for title, description, priority, service, final in BACKLOG:
        ticket = tickets.create(
            TicketCreate(
                title=title,
                description=description,
                priority=Priority(priority),
                service=service,
                source=TicketSource.EMAIL,
            ),
            sam,
        )
        path = {
            TicketStatus.OPEN: [],
            TicketStatus.TRIAGED: [TicketStatus.TRIAGED],
            TicketStatus.INVESTIGATING: [TicketStatus.TRIAGED, TicketStatus.INVESTIGATING],
            TicketStatus.RESOLVED: [TicketStatus.INVESTIGATING, TicketStatus.RESOLVED],
            TicketStatus.CLOSED: [TicketStatus.CLOSED],
        }[final]
        if path:
            tickets.assign(ticket.number, alex.id, sam)
        for status in path:
            resolution = (
                "Handled — see ticket history." if status in {TicketStatus.RESOLVED, TicketStatus.CLOSED} else None
            )
            tickets.transition(ticket.number, TicketTransition(status=status, resolution=resolution), alex)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="truncate all tables first")
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.reset:
            reset(db)
        elif db.scalar(select(func.count()).select_from(Ticket)):
            print("seed: data already present, skipping (use --reset to reload)")
            return
        seed(db)
        print(f"seed: {db.scalar(select(func.count()).select_from(Ticket))} tickets loaded")


if __name__ == "__main__":
    main()
