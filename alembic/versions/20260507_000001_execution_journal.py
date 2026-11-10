"""add persistent trade execution journal

Revision ID: 20260507_000001
Revises: b8be9926f06c
Create Date: 2026-05-07 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260507_000001"
down_revision = "b8be9926f06c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_journal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.String(length=128), nullable=False),
        sa.Column("command_id", sa.String(length=128), nullable=True),
        sa.Column("decision_id", sa.String(length=128), nullable=True),
        sa.Column("client_order_id", sa.String(length=128), nullable=True),
        sa.Column("cmd", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_order_id", name="uq_execution_journal_client_order_id"),
        sa.UniqueConstraint("execution_id", name="uq_execution_journal_execution_id"),
    )
    with op.batch_alter_table("execution_journal", schema=None) as batch_op:
        batch_op.create_index("ix_execution_journal_command_id", ["command_id"], unique=False)
        batch_op.create_index("ix_execution_journal_decision_id", ["decision_id"], unique=False)
        batch_op.create_index("ix_execution_journal_status_created", ["status", "created_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("execution_journal", schema=None) as batch_op:
        batch_op.drop_index("ix_execution_journal_status_created")
        batch_op.drop_index("ix_execution_journal_decision_id")
        batch_op.drop_index("ix_execution_journal_command_id")
    op.drop_table("execution_journal")
