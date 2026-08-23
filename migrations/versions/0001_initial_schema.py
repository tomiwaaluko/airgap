"""Create the Airgap PostgreSQL persistence schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create the frozen tables, request indexes, and audit immutability trigger."""
    op.create_table(
        "requests",
        sa.Column("id", sa.CHAR(length=8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("tool_args", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("risk_class", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("dial_at_decision", sa.SmallInteger(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_requests_created_at_desc", "requests", [sa.text("created_at DESC")]
    )
    op.create_index(
        "ix_requests_tool_name_verdict", "requests", ["tool_name", "verdict"]
    )

    op.create_table(
        "warden_assessments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.CHAR(length=8), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("risk_class", sa.Text(), nullable=False),
        sa.Column("reversible", sa.Boolean(), nullable=False),
        sa.Column("blast_radius", sa.Text(), nullable=False),
        sa.Column("injection_suspected", sa.Boolean(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column(
            "tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "audit_log",
        sa.Column("seq", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("request_id", sa.CHAR(length=8), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prev_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("row_hash", sa.CHAR(length=64), nullable=False),
        sa.PrimaryKeyConstraint("seq"),
    )
    op.execute(
        """
        CREATE FUNCTION audit_log_reject_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_append_only
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_reject_mutation();
        """
    )

    op.create_table(
        "policies",
        sa.Column("tool_pattern", sa.Text(), nullable=False),
        sa.Column("min_dial", sa.SmallInteger(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "relay_gated",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "dwell_s", sa.SmallInteger(), server_default=sa.text("60"), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("tool_pattern"),
    )


def downgrade() -> None:
    """Remove every object introduced by this initial migration."""
    op.drop_table("policies")
    op.execute("DROP TRIGGER audit_log_append_only ON audit_log")
    op.execute("DROP FUNCTION audit_log_reject_mutation")
    op.drop_table("audit_log")
    op.drop_table("warden_assessments")
    op.drop_index("ix_requests_tool_name_verdict", table_name="requests")
    op.drop_index("ix_requests_created_at_desc", table_name="requests")
    op.drop_table("requests")
