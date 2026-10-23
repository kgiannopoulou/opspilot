"""The support lifecycle: OPEN → TRIAGED → INVESTIGATING → RESOLVED → CLOSED.

Kept as data so the API can tell the UI which buttons to show, and so the rules are testable without a database.
"""

from app.models.enums import TicketStatus as S

ALLOWED_TRANSITIONS: dict[S, frozenset[S]] = {
    S.OPEN: frozenset({S.TRIAGED, S.INVESTIGATING, S.CLOSED}),  # CLOSED from OPEN = duplicate / not a bug
    S.TRIAGED: frozenset({S.INVESTIGATING, S.RESOLVED, S.OPEN}),
    S.INVESTIGATING: frozenset({S.RESOLVED, S.TRIAGED}),
    S.RESOLVED: frozenset({S.CLOSED, S.INVESTIGATING}),  # RESOLVED → INVESTIGATING = reopen
    S.CLOSED: frozenset(),
}

# Transitions that must carry a written explanation.
REQUIRES_RESOLUTION = {(S.TRIAGED, S.RESOLVED), (S.INVESTIGATING, S.RESOLVED), (S.OPEN, S.CLOSED)}


def can_transition(current: S, target: S) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def allowed_transitions(current: S) -> list[S]:
    order = list(S)
    return sorted(ALLOWED_TRANSITIONS[current], key=order.index)
