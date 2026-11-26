"""STP/RSTP/MSTP status and guarded settings."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.domain.identity import Principal, RequestContext
from app.network.parsers import StpGlobal, StpPortBrief
from app.network.platforms import Q, adapter_of
from app.services.change_service import ChangeOptions, ChangeResult
from app.services.vlan_service import L2Base


@dataclass(frozen=True, slots=True)
class StpStatus:
    global_info: StpGlobal
    ports: list[StpPortBrief]
    is_root: bool | None
    loop_suspected: bool


class StpService(L2Base):
    async def status(self, principal: Principal, device_id: uuid.UUID) -> StpStatus:
        device = await self._device(principal, device_id)
        glob_out = await self._collection.query(device, Q.STP_GLOBAL, principal)
        brief_out = await self._collection.query(device, Q.STP_BRIEF, principal)
        info = adapter_of(device).parsers.parse_stp_global(glob_out)
        ports = adapter_of(device).parsers.parse_stp_brief(brief_out)
        is_root = None
        if info.bridge_id and info.root_id:
            is_root = (
                info.bridge_id.split(".")[-1] == info.root_id.split(".")[-1].split("/")[0].strip()
            )
        # Many topology changes or several forwarding root ports hint at a loop.
        root_ports = [
            p
            for p in ports
            if p.role.upper().startswith("ROOT") and p.state.upper() == "FORWARDING"
        ]
        loop = (info.tc_count or 0) > 1000 or len({p.instance for p in root_ports}) < len(
            root_ports
        )
        return StpStatus(info, ports, is_root, loop)

    async def configure(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        mode: str | None,
        priority: int | None,
        instance: int,
        options: ChangeOptions,
    ) -> ChangeResult:
        device = await self._device(principal, device_id)
        return await self._apply(
            principal,
            ctx,
            device,
            adapter_of(device).l2.stp_settings(mode, priority, instance),
            options,
        )

    async def edge_port(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        interface: str,
        enabled: bool,
        options: ChangeOptions,
    ) -> ChangeResult:
        device = await self._device(principal, device_id)
        return await self._apply(
            principal, ctx, device, adapter_of(device).l2.stp_edge_port(interface, enabled), options
        )
