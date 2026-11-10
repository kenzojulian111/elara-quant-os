"""create runtime settings and trade exchange sync columns

Revision ID: 20260225_000001
Revises:
Create Date: 2026-02-25 03:30:00.000000
"""

from alembic import op
import logging
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260225_000001"
down_revision = None
branch_labels = None
depends_on = None
log = logging.getLogger("alembic.runtime_settings_exchange_sync")


def _has_table(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col.get("name") == column_name for col in inspector.get_columns(table_name))


def _has_unique_constraint(inspector, table_name: str, constraint_name: str) -> bool:
    for item in inspector.get_unique_constraints(table_name):
        if item.get("name") == constraint_name:
            return True
    return False


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    for item in inspector.get_indexes(table_name):
        if item.get("name") == index_name:
            return True
    return False


def _is_sqlite(bind) -> bool:
    return str(bind.dialect.name or "").lower() == "sqlite"


def _run_schema_step(description: str, action) -> None:
    try:
        action()
    except Exception:
        log.exception("Migration step failed: %s", description)
        raise


def _skip_sqlite_step(description: str) -> None:
    log.warning("Skipping SQLite-incompatible migration step: %s", description)


def _drop_column(bind, table_name: str, column_name: str) -> None:
    description = f"drop column {table_name}.{column_name}"
    if _is_sqlite(bind):
        _skip_sqlite_step(description)
        return
    _run_schema_step(description, lambda: op.drop_column(table_name, column_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _has_table(inspector, "runtime_settings"):
        op.create_table(
            "runtime_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("scope", sa.String(length=64), nullable=False, server_default="trading"),
            sa.Column("key", sa.String(length=128), nullable=False),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column("value_type", sa.String(length=24), nullable=False, server_default="str"),
            sa.Column("updated_by", sa.String(length=120), nullable=False, server_default="system"),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("scope", "key", name="uq_runtime_scope_key"),
        )

    inspector = inspect(bind)
    if _has_table(inspector, "trades"):
        if not _has_column(inspector, "trades", "exchange_order_id"):
            op.add_column("trades", sa.Column("exchange_order_id", sa.String(length=128), nullable=True))
        if not _has_column(inspector, "trades", "exchange_order_status"):
            op.add_column(
                "trades",
                sa.Column("exchange_order_status", sa.String(length=32), nullable=True, server_default="closed"),
            )
        if not _has_column(inspector, "trades", "exchange_order_last_sync"):
            op.add_column("trades", sa.Column("exchange_order_last_sync", sa.DateTime(), nullable=True))

    inspector = inspect(bind)
    if _has_table(inspector, "runtime_settings") and not _has_unique_constraint(
        inspector, "runtime_settings", "uq_runtime_scope_key"
    ):
        if _is_sqlite(bind):
            _skip_sqlite_step("add unique constraint uq_runtime_scope_key on runtime_settings")
        else:
            _run_schema_step(
                "add unique constraint uq_runtime_scope_key on runtime_settings",
                lambda: op.create_unique_constraint("uq_runtime_scope_key", "runtime_settings", ["scope", "key"]),
            )

    inspector = inspect(bind)
    if _has_table(inspector, "command_logs"):
        if not _has_index(inspector, "command_logs", "ix_command_logs_timestamp"):
            _run_schema_step(
                "create index ix_command_logs_timestamp",
                lambda: op.create_index("ix_command_logs_timestamp", "command_logs", ["timestamp"], unique=False),
            )
        if not _has_index(inspector, "command_logs", "ix_command_logs_intent_timestamp"):
            _run_schema_step(
                "create index ix_command_logs_intent_timestamp",
                lambda: op.create_index(
                    "ix_command_logs_intent_timestamp",
                    "command_logs",
                    ["intent", "timestamp"],
                    unique=False,
                ),
            )

    inspector = inspect(bind)
    if _has_table(inspector, "runtime_settings"):
        if not _has_index(inspector, "runtime_settings", "ix_runtime_settings_scope_updated"):
            _run_schema_step(
                "create index ix_runtime_settings_scope_updated",
                lambda: op.create_index(
                    "ix_runtime_settings_scope_updated",
                    "runtime_settings",
                    ["scope", "updated_at"],
                    unique=False,
                ),
            )

    inspector = inspect(bind)
    if _has_table(inspector, "trades"):
        if not _has_index(inspector, "trades", "ix_trades_symbol_entry_time"):
            _run_schema_step(
                "create index ix_trades_symbol_entry_time",
                lambda: op.create_index("ix_trades_symbol_entry_time", "trades", ["symbol", "entry_time"], unique=False),
            )
        if not _has_index(inspector, "trades", "ix_trades_status_entry_time"):
            _run_schema_step(
                "create index ix_trades_status_entry_time",
                lambda: op.create_index("ix_trades_status_entry_time", "trades", ["status", "entry_time"], unique=False),
            )
        if not _has_index(inspector, "trades", "ix_trades_exchange_order_status"):
            _run_schema_step(
                "create index ix_trades_exchange_order_status",
                lambda: op.create_index("ix_trades_exchange_order_status", "trades", ["exchange_order_status"], unique=False),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _has_table(inspector, "trades"):
        if _has_index(inspector, "trades", "ix_trades_exchange_order_status"):
            _run_schema_step(
                "drop index ix_trades_exchange_order_status",
                lambda: op.drop_index("ix_trades_exchange_order_status", table_name="trades"),
            )
        if _has_index(inspector, "trades", "ix_trades_status_entry_time"):
            _run_schema_step(
                "drop index ix_trades_status_entry_time",
                lambda: op.drop_index("ix_trades_status_entry_time", table_name="trades"),
            )
        if _has_index(inspector, "trades", "ix_trades_symbol_entry_time"):
            _run_schema_step(
                "drop index ix_trades_symbol_entry_time",
                lambda: op.drop_index("ix_trades_symbol_entry_time", table_name="trades"),
            )

    inspector = inspect(bind)
    if _has_table(inspector, "runtime_settings"):
        if _has_index(inspector, "runtime_settings", "ix_runtime_settings_scope_updated"):
            _run_schema_step(
                "drop index ix_runtime_settings_scope_updated",
                lambda: op.drop_index("ix_runtime_settings_scope_updated", table_name="runtime_settings"),
            )

    inspector = inspect(bind)
    if _has_table(inspector, "command_logs"):
        if _has_index(inspector, "command_logs", "ix_command_logs_intent_timestamp"):
            _run_schema_step(
                "drop index ix_command_logs_intent_timestamp",
                lambda: op.drop_index("ix_command_logs_intent_timestamp", table_name="command_logs"),
            )
        if _has_index(inspector, "command_logs", "ix_command_logs_timestamp"):
            _run_schema_step(
                "drop index ix_command_logs_timestamp",
                lambda: op.drop_index("ix_command_logs_timestamp", table_name="command_logs"),
            )

    if _has_table(inspector, "trades"):
        if _has_column(inspector, "trades", "exchange_order_last_sync"):
            _drop_column(bind, "trades", "exchange_order_last_sync")
        if _has_column(inspector, "trades", "exchange_order_status"):
            _drop_column(bind, "trades", "exchange_order_status")
        if _has_column(inspector, "trades", "exchange_order_id"):
            _drop_column(bind, "trades", "exchange_order_id")

    inspector = inspect(bind)
    if _has_table(inspector, "runtime_settings"):
        _run_schema_step("drop table runtime_settings", lambda: op.drop_table("runtime_settings"))

