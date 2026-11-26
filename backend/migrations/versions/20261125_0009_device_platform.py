"""device platform (multi-vendor: Huawei, Cisco, TP-Link)

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-25 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORMS = (
    "huawei_vrp",
    "huawei_vrpv8",
    "cisco_ios",
    "cisco_nxos",
    "cisco_smb",
    "tplink_jetstream",
    "tplink_omada",
)


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(
            sa.Column(
                "platform",
                sa.Enum(
                    *PLATFORMS,
                    name="device_platform",
                    native_enum=False,
                    create_constraint=True,
                    length=32,
                ),
                nullable=False,
                server_default="huawei_vrp",
            )
        )
    with op.batch_alter_table("backups") as batch:
        batch.add_column(sa.Column("platform", sa.String(length=32), nullable=True))
    # Existing inventory is Huawei only: CloudEngine runs VRP V8, everything else VRP V5.
    op.execute("UPDATE devices SET platform = 'huawei_vrpv8' WHERE family = 'CE'")


def downgrade() -> None:
    # Without the column, non-Huawei devices would silently be driven as Huawei: refuse.
    count = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM devices WHERE platform NOT LIKE 'huawei%'"))
        .scalar()
    )
    if count:
        raise RuntimeError(
            f"{count} non-Huawei device(s) exist; remove them before downgrading below 0009"
        )
    with op.batch_alter_table("backups") as batch:
        batch.drop_column("platform")
    with op.batch_alter_table("devices") as batch:
        if op.get_bind().dialect.name == "sqlite":
            # The batch copy would otherwise keep the CHECK that references the dropped column.
            batch.drop_constraint(op.f("ck_devices_device_platform"), type_="check")
        batch.drop_column("platform")
