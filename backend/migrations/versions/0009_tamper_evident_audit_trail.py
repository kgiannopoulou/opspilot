"""tamper-evident audit trail: hash chain and append-only enforcement

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-26 09:10:00.000000

Every audit row gets a position in one global chain (`chain_seq`) and `hash = sha256(prev_hash | row fields)`,
computed by a trigger so no application code path can skip it. Inserts take a transaction-scoped advisory lock, so
the chain order is the commit order even with concurrent writers. UPDATE, DELETE and TRUNCATE are refused unless the
session explicitly enables maintenance mode (the demo seed backdates history and then re-chains). The actor foreign
key becomes ON DELETE RESTRICT: a user with audit history can be deactivated (soft delete), never erased.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GENESIS = "0" * 64

FUNCTIONS = f"""
CREATE FUNCTION audit_hash(prev text, seq bigint, occurred timestamptz, actor bigint, action text, entity_type text,
                           entity_id text, changes jsonb, ip inet) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT encode(sha256(convert_to(concat_ws('|',
        prev, seq::text, to_char(occurred AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        coalesce(actor::text, ''), action, entity_type, entity_id, changes::text, coalesce(host(ip), '')
    ), 'UTF8')), 'hex')
$$;

CREATE FUNCTION audit_events_chain() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    previous text;
BEGIN
    -- One writer at a time extends the chain; the lock is released when the transaction ends.
    PERFORM pg_advisory_xact_lock(hashtext('audit_events_chain'));
    SELECT hash INTO previous FROM audit_events ORDER BY chain_seq DESC LIMIT 1;
    NEW.prev_hash := coalesce(previous, '{GENESIS}');
    NEW.chain_seq := nextval('audit_events_chain_seq_seq');
    NEW.occurred_at := coalesce(NEW.occurred_at, now());
    NEW.changes := coalesce(NEW.changes, '{{}}'::jsonb);
    NEW.hash := audit_hash(NEW.prev_hash, NEW.chain_seq, NEW.occurred_at, NEW.actor_id, NEW.action,
                           NEW.entity_type, NEW.entity_id, NEW.changes, NEW.ip_address);
    RETURN NEW;
END $$;

CREATE FUNCTION audit_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('opspilot.audit_maintenance', true) = 'on' THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    RAISE EXCEPTION 'audit_events is append-only (% refused)', TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE FUNCTION audit_rechain() RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    r record;
    previous text := '{GENESIS}';
    n bigint := 0;
BEGIN
    IF current_setting('opspilot.audit_maintenance', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION 'audit_rechain() requires maintenance mode' USING ERRCODE = 'insufficient_privilege';
    END IF;
    FOR r IN SELECT * FROM audit_events ORDER BY chain_seq LOOP
        UPDATE audit_events
           SET prev_hash = previous,
               hash = audit_hash(previous, r.chain_seq, r.occurred_at, r.actor_id, r.action, r.entity_type,
                                 r.entity_id, r.changes, r.ip_address)
         WHERE id = r.id
     RETURNING hash INTO previous;
        n := n + 1;
    END LOOP;
    RETURN n;
END $$;
"""

TRIGGERS = """
CREATE TRIGGER audit_events_chain BEFORE INSERT ON audit_events FOR EACH ROW EXECUTE FUNCTION audit_events_chain();
CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();
CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_immutable();
"""


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("chain_seq", sa.BigInteger(), nullable=True))
    op.add_column("audit_events", sa.Column("prev_hash", sa.String(length=64), nullable=True))
    op.add_column("audit_events", sa.Column("hash", sa.String(length=64), nullable=True))
    # Owned by the column, so TRUNCATE ... RESTART IDENTITY (the seed's reset) restarts the chain too.
    op.execute("CREATE SEQUENCE audit_events_chain_seq_seq OWNED BY audit_events.chain_seq")
    op.execute(FUNCTIONS)
    # Existing rows join the chain in id order.
    op.execute(
        "UPDATE audit_events a SET chain_seq = o.n "
        "FROM (SELECT id, row_number() OVER (ORDER BY id) AS n FROM audit_events) o WHERE a.id = o.id"
    )
    op.execute("SELECT setval('audit_events_chain_seq_seq', greatest((SELECT max(chain_seq) FROM audit_events), 1), "
               "(SELECT count(*) > 0 FROM audit_events))")  # fmt: skip
    op.execute("SET LOCAL opspilot.audit_maintenance = 'on'")
    op.execute("SELECT audit_rechain()")
    op.execute("SET LOCAL opspilot.audit_maintenance = 'off'")
    op.create_unique_constraint(op.f("uq_audit_events_chain_seq"), "audit_events", ["chain_seq"])
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"])
    op.drop_constraint(op.f("fk_audit_events_actor_id_users"), "audit_events", type_="foreignkey")
    op.create_foreign_key(op.f("fk_audit_events_actor_id_users"), "audit_events", "users", ["actor_id"], ["id"],
                          ondelete="RESTRICT")  # fmt: skip
    op.execute(TRIGGERS)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER audit_events_immutable ON audit_events")
    op.execute("DROP TRIGGER audit_events_chain ON audit_events")
    op.drop_constraint(op.f("fk_audit_events_actor_id_users"), "audit_events", type_="foreignkey")
    op.create_foreign_key(op.f("fk_audit_events_actor_id_users"), "audit_events", "users", ["actor_id"], ["id"],
                          ondelete="SET NULL")  # fmt: skip
    op.drop_index(op.f("ix_audit_events_action"), table_name="audit_events")
    op.drop_constraint(op.f("uq_audit_events_chain_seq"), "audit_events", type_="unique")
    op.drop_column("audit_events", "hash")
    op.drop_column("audit_events", "prev_hash")
    op.drop_column("audit_events", "chain_seq")
    op.execute("DROP FUNCTION audit_rechain()")
    op.execute("DROP FUNCTION audit_events_immutable()")
    op.execute("DROP FUNCTION audit_events_chain()")
    op.execute("DROP FUNCTION audit_hash(text, bigint, timestamptz, bigint, text, text, text, jsonb, inet)")
