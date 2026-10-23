"""full schema: roles, incidents, observability, knowledge base, investigations, audit

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23 10:18:10.569837
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ROLES = [
    (1, "admin", "Manage users, roles and the service catalogue; full access."),
    (2, "engineer", "Investigate and resolve incidents; manage tickets."),
    (3, "support_agent", "Create and update tickets; read incidents."),
    (4, "viewer", "Read-only access to dashboards, tickets and incidents."),
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SEQUENCE incident_number_seq START WITH 101")
    op.create_table(
        "roles",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("name", name=op.f("uq_roles_name")),
    )
    roles = sa.table("roles", sa.column("id", sa.SmallInteger), sa.column("name"), sa.column("description"))
    op.bulk_insert(roles, [{"id": i, "name": n, "description": d} for i, n, d in ROLES])
    op.create_table(
        "application_logs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "level",
            sa.Enum(
                "DEBUG", "INFO", "WARN", "ERROR", name="log_level", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["service_id"], ["services.id"], name=op.f("fk_application_logs_service_id_services"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_logs")),
    )
    op.create_index(
        "ix_application_logs_problems",
        "application_logs",
        ["service_id", "ts"],
        unique=False,
        postgresql_where=sa.text("level IN ('WARN', 'ERROR')"),
    )
    op.create_index(
        "ix_application_logs_service_ts", "application_logs", ["service_id", sa.literal_column("ts DESC")], unique=False
    )
    op.create_index(
        "ix_application_logs_trace_id",
        "application_logs",
        ["trace_id"],
        unique=False,
        postgresql_where=sa.text("trace_id IS NOT NULL"),
    )
    op.create_index("ix_application_logs_ts_brin", "application_logs", ["ts"], unique=False, postgresql_using="brin")
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "runbook",
                "troubleshooting",
                "architecture",
                name="document_category",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("service_id", sa.BigInteger(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String(length=40)), server_default="{}", nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_knowledge_documents_service_id_services"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_documents")),
        sa.UniqueConstraint("slug", name=op.f("uq_knowledge_documents_slug")),
    )
    op.create_index(op.f("ix_knowledge_documents_service_id"), "knowledge_documents", ["service_id"], unique=False)
    op.create_index(
        "ix_knowledge_documents_tags", "knowledge_documents", ["tags"], unique=False, postgresql_using="gin"
    )
    op.create_table(
        "metric_samples",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["service_id"], ["services.id"], name=op.f("fk_metric_samples_service_id_services"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_samples")),
        sa.UniqueConstraint("service_id", "name", "ts", name="uq_metric_samples_series_ts"),
    )
    op.create_table(
        "service_dependencies",
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("depends_on_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("service_id <> depends_on_id", name=op.f("ck_service_dependencies_no_self_dependency")),
        sa.ForeignKeyConstraint(
            ["depends_on_id"],
            ["services.id"],
            name=op.f("fk_service_dependencies_depends_on_id_services"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_service_dependencies_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("service_id", "depends_on_id", name=op.f("pk_service_dependencies")),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_audit_events_actor_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        "ix_audit_events_actor_occurred",
        "audit_events",
        ["actor_id", sa.literal_column("occurred_at DESC")],
        unique=False,
    )
    op.create_index("ix_audit_events_entity", "audit_events", ["entity_type", "entity_id"], unique=False)
    op.create_index("ix_audit_events_occurred", "audit_events", [sa.literal_column("occurred_at DESC")], unique=False)
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            name=op.f("fk_document_chunks_document_id_knowledge_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_index"),
    )
    op.create_index(
        "ix_document_chunks_embedding_hnsw",
        "document_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_table(
        "incidents",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "SEV1", "SEV2", "SEV3", "SEV4", name="severity", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "INVESTIGATING",
                "IDENTIFIED",
                "MONITORING",
                "RESOLVED",
                name="incident_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="INVESTIGATING",
            nullable=False,
        ),
        sa.Column("primary_service_id", sa.BigInteger(), nullable=True),
        sa.Column("commander_id", sa.BigInteger(), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("signature", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["commander_id"], ["users.id"], name=op.f("fk_incidents_commander_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["primary_service_id"],
            ["services.id"],
            name=op.f("fk_incidents_primary_service_id_services"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incidents")),
        sa.UniqueConstraint("number", name=op.f("uq_incidents_number")),
    )
    op.create_index("ix_incidents_primary_service_id", "incidents", ["primary_service_id"], unique=False)
    op.create_index(op.f("ix_incidents_signature"), "incidents", ["signature"], unique=False)
    op.create_index("ix_incidents_started_at", "incidents", ["started_at"], unique=False)
    op.create_index("ix_incidents_status_severity", "incidents", ["status", "severity"], unique=False)
    op.create_table(
        "ai_investigations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_by_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "RUNNING",
                "COMPLETED",
                "FAILED",
                name="investigation_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="RUNNING",
            nullable=False,
        ),
        sa.Column("engine", sa.String(length=40), nullable=False),
        sa.Column("hypothesis_key", sa.String(length=64), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3, asdecimal=False), nullable=True),
        sa.Column("recommended_steps", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("suggested_sql", sa.Text(), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1", name=op.f("ck_ai_investigations_confidence_range")
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_ai_investigations_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name=op.f("fk_ai_investigations_requested_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_investigations")),
    )
    op.create_index(
        "ix_ai_investigations_incident_created",
        "ai_investigations",
        ["incident_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "incident_services",
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_services_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"], ["services.id"], name=op.f("fk_incident_services_service_id_services"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("incident_id", "service_id", name=op.f("pk_incident_services")),
    )
    op.create_table(
        "incident_tickets",
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("linked_by_id", sa.BigInteger(), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_tickets_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["linked_by_id"], ["users.id"], name=op.f("fk_incident_tickets_linked_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_incident_tickets_ticket_id_tickets"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("incident_id", "ticket_id", name=op.f("pk_incident_tickets")),
        sa.UniqueConstraint("ticket_id", name=op.f("uq_incident_tickets_ticket_id")),
    )
    op.create_table(
        "ai_evidence",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("investigation_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "LOG_PATTERN",
                "METRIC_DELTA",
                "DEPENDENCY_HEALTH",
                "SIMILAR_INCIDENT",
                "DOCUMENT",
                "CHANGE_EVENT",
                name="evidence_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "stance",
            sa.Enum(
                "SUPPORTS",
                "CONTRADICTS",
                "CONTEXT",
                name="evidence_stance",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("weight", sa.Double(), server_default="0", nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("related_incident_id", sa.BigInteger(), nullable=True),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            name=op.f("fk_ai_evidence_document_id_knowledge_documents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["ai_investigations.id"],
            name=op.f("fk_ai_evidence_investigation_id_ai_investigations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["related_incident_id"],
            ["incidents.id"],
            name=op.f("fk_ai_evidence_related_incident_id_incidents"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_evidence")),
    )
    op.create_index(
        "ix_ai_evidence_investigation_position", "ai_evidence", ["investigation_id", "position"], unique=False
    )
    # Existing users become support agents; the column is made NOT NULL once every row has a role.
    op.add_column("users", sa.Column("role_id", sa.SmallInteger(), nullable=True))
    op.execute("UPDATE users SET role_id = 3")
    op.alter_column("users", "role_id", nullable=False)
    op.create_index(op.f("ix_users_role_id"), "users", ["role_id"], unique=False)
    op.create_foreign_key(op.f("fk_users_role_id_roles"), "users", "roles", ["role_id"], ["id"], ondelete="RESTRICT")


def downgrade() -> None:
    op.drop_constraint(op.f("fk_users_role_id_roles"), "users", type_="foreignkey")
    op.drop_index(op.f("ix_users_role_id"), table_name="users")
    op.drop_column("users", "role_id")
    op.drop_index("ix_ai_evidence_investigation_position", table_name="ai_evidence")
    op.drop_table("ai_evidence")
    op.drop_table("incident_tickets")
    op.drop_table("incident_services")
    op.drop_index("ix_ai_investigations_incident_created", table_name="ai_investigations")
    op.drop_table("ai_investigations")
    op.drop_index("ix_incidents_status_severity", table_name="incidents")
    op.drop_index("ix_incidents_started_at", table_name="incidents")
    op.drop_index(op.f("ix_incidents_signature"), table_name="incidents")
    op.drop_index("ix_incidents_primary_service_id", table_name="incidents")
    op.drop_table("incidents")
    op.drop_index(
        "ix_document_chunks_embedding_hnsw",
        table_name="document_chunks",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("document_chunks")
    op.drop_index("ix_audit_events_occurred", table_name="audit_events")
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_occurred", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("service_dependencies")
    op.drop_table("metric_samples")
    op.drop_index("ix_knowledge_documents_tags", table_name="knowledge_documents", postgresql_using="gin")
    op.drop_index(op.f("ix_knowledge_documents_service_id"), table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
    op.drop_index("ix_application_logs_ts_brin", table_name="application_logs", postgresql_using="brin")
    op.drop_index(
        "ix_application_logs_trace_id", table_name="application_logs", postgresql_where=sa.text("trace_id IS NOT NULL")
    )
    op.drop_index("ix_application_logs_service_ts", table_name="application_logs")
    op.drop_index(
        "ix_application_logs_problems",
        table_name="application_logs",
        postgresql_where=sa.text("level IN ('WARN', 'ERROR')"),
    )
    op.drop_table("application_logs")
    op.drop_table("roles")
    op.execute("DROP SEQUENCE incident_number_seq")
