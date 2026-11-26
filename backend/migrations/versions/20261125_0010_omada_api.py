"""api device protocol and omada_api credential kind (TP-Link Omada Controller)

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-25 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = {
    ("devices", "protocol", "device_protocol"): ("ssh", "telnet", "netconf", "snmp"),
    ("credentials", "kind", "credential_kind"): ("cli", "snmp_v2c", "snmp_v3"),
}
NEW = {
    ("devices", "protocol", "device_protocol"): ("ssh", "telnet", "netconf", "snmp", "api"),
    ("credentials", "kind", "credential_kind"): ("cli", "snmp_v2c", "snmp_v3", "omada_api"),
}


def _enum(values: tuple[str, ...], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def _swap(
    target: dict[tuple[str, str, str], tuple[str, ...]],
    source: dict[tuple[str, str, str], tuple[str, ...]],
) -> None:
    bind = op.get_bind()
    for (table, column, name), values in target.items():
        if bind.dialect.name == "postgresql":
            rows = bind.execute(
                sa.text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) "
                    "AND contype = 'c' AND pg_get_constraintdef(oid) LIKE :col"
                ),
                {"t": table, "col": f"%{column}%"},
            ).scalars()
            for conname in rows:
                op.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{conname}"')
            allowed = ", ".join(f"'{v}'" for v in values)
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_{name} "
                f"CHECK ({column} IN ({allowed}))"
            )
        else:
            with op.batch_alter_table(table, recreate="always") as batch:
                batch.alter_column(
                    column,
                    type_=_enum(values, name),
                    existing_type=_enum(source[(table, column, name)], name),
                    existing_nullable=False,
                )


def upgrade() -> None:
    _swap(NEW, OLD)


def downgrade() -> None:
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT (SELECT count(*) FROM devices WHERE protocol = 'api') + "
                "(SELECT count(*) FROM credentials WHERE kind = 'omada_api')"
            )
        )
        .scalar()
    )
    if count:
        raise RuntimeError("Omada API devices/credentials exist; remove them before downgrading")
    _swap(OLD, NEW)
