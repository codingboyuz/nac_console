"""ACL management: live view, rule-level deploys, rule CRUD, application, persistence."""

from __future__ import annotations

import uuid
from dataclasses import replace

from sqlalchemy import delete, select

from app.core.database import Database
from app.core.exceptions import NotFound, ValidationFailed
from app.domain.acl import AclKind, AclRuleSpec, AclValidationError, PortMatch, kind_for_number
from app.domain.identity import Principal, RequestContext
from app.models.acl import Acl, AclRule
from app.models.device import Device
from app.network.builders.cli_acl import AclDefinition, Direction, next_rule_id
from app.network.builders.plan import ConfigPlan
from app.network.parsers.acl import ParsedAcl, management_acls
from app.network.platforms import Q, adapter_of
from app.repositories.uow import UnitOfWork
from app.services.change_service import ChangeOptions, ChangeResult, ChangeService
from app.services.collection_service import CollectionService
from app.services.device_service import DeviceService
from app.services.vlan_service import L2Base


def _port(data: dict[str, int | str] | None) -> PortMatch | None:
    if not data:
        return None
    end = int(data["end"]) if data.get("end") else None
    return PortMatch(str(data["op"]), int(data["start"]), end)  # type: ignore[arg-type]


def _port_dict(port: PortMatch | None) -> dict[str, int | str] | None:
    return (
        None
        if port is None
        else {"op": port.op, "start": port.start, **({"end": port.end} if port.end else {})}
    )


def definition_from_row(row: Acl) -> AclDefinition:
    rules = tuple(
        AclRuleSpec(
            rule_id=r.rule_id,
            action=r.action,  # type: ignore[arg-type]
            protocol=r.protocol,  # type: ignore[arg-type]
            source=r.source,
            destination=r.destination,
            source_port=_port(r.source_port),
            destination_port=_port(r.destination_port),
            source_mac=r.source_mac,
            destination_mac=r.destination_mac,
            vlan=r.vlan,
            log=r.log,
        )
        for r in row.rules
    )
    return AclDefinition(row.kind, row.number, row.name, row.ipv6, row.description, rules)


class AclService(L2Base):
    def __init__(
        self,
        db: Database,
        devices: DeviceService,
        collection: CollectionService,
        changes: ChangeService,
    ) -> None:
        super().__init__(devices, collection, changes)
        self._db = db

    async def live(self, principal: Principal, device_id: uuid.UUID) -> list[ParsedAcl]:
        device = await self._device(principal, device_id)
        return adapter_of(device).parsers.parse_acls(
            await self._collection.query(device, Q.ACLS, principal)
        )

    async def stored(self, principal: Principal, device_id: uuid.UUID) -> list[Acl]:
        await self._device(principal, device_id)
        async with UnitOfWork(self._db) as uow:
            rows = await uow.session.scalars(
                select(Acl).where(Acl.device_id == device_id).order_by(Acl.identifier)
            )
            return list(rows.all())

    async def _mgmt_acls(self, principal: Principal, device: Device) -> frozenset[str]:
        return management_acls(await self._collection.query(device, Q.RUNNING_CONFIG, principal))

    async def _current(
        self, principal: Principal, device: Device, identifier: str
    ) -> ParsedAcl | None:
        out = await self._collection.query(device, Q.ACL, principal, identifier=str(identifier))
        found = [
            a
            for a in adapter_of(device).parsers.parse_acls(out)
            if identifier in {str(a.number), a.name}
        ]
        return found[0] if found else None

    async def deploy(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        definition: AclDefinition,
        options: ChangeOptions,
    ) -> ChangeResult:
        try:
            if (
                definition.number is not None
                and kind_for_number(definition.number) is not definition.kind
            ):
                raise ValidationFailed("ACL number does not match its kind")
            device = await self._device(principal, device_id)
            current = await self._current(principal, device, definition.identifier)
            plan = adapter_of(device).acl.plan_acl(
                definition, current, await self._mgmt_acls(principal, device)
            )
        except AclValidationError as exc:
            raise ValidationFailed(str(exc)) from exc
        result = await self._apply(principal, ctx, device, plan, options)
        if result.status in {"applied", "noop"}:
            await self._persist(device.id, definition)
        return result

    async def upsert_rule(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        identifier: str,
        rule: AclRuleSpec | None,
        *,
        remove_rule_id: int | None = None,
        options: ChangeOptions,
    ) -> ChangeResult:
        """Add/modify (rule_id 0 = auto-assign next id, step 5) or remove one rule."""
        base = await self._definition(principal, device_id, identifier)
        rules = {r.rule_id: r for r in base.rules}
        if remove_rule_id is not None:
            if remove_rule_id not in rules:
                raise NotFound(f"rule {remove_rule_id} not found")
            rules.pop(remove_rule_id)
        elif rule is not None:
            rid = rule.rule_id or next_rule_id(set(rules))
            rules[rid] = replace(rule, rule_id=rid)
        return await self.deploy(
            principal,
            ctx,
            device_id,
            replace(base, rules=tuple(sorted(rules.values(), key=lambda r: r.rule_id))),
            options,
        )

    async def _definition(
        self, principal: Principal, device_id: uuid.UUID, identifier: str
    ) -> AclDefinition:
        for row in await self.stored(principal, device_id):
            if row.identifier == identifier:
                return definition_from_row(row)
        raise NotFound(f"ACL {identifier} is not managed here; deploy it first")

    async def delete(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        identifier: str,
        options: ChangeOptions,
    ) -> ChangeResult:
        definition = await self._definition(principal, device_id, identifier)
        device = await self._device(principal, device_id)
        plan = adapter_of(device).acl.delete_acl(
            definition, await self._mgmt_acls(principal, device)
        )
        result = await self._apply(principal, ctx, device, plan, options)
        if result.status == "applied":
            async with UnitOfWork(self._db) as uow:
                row = await uow.session.scalar(
                    select(Acl).where(Acl.device_id == device_id, Acl.identifier == identifier)
                )
                if row is not None:
                    await uow.session.delete(row)
                    await uow.commit()
        return result

    async def bind(
        self,
        principal: Principal,
        ctx: RequestContext,
        device_id: uuid.UUID,
        identifier: str,
        direction: Direction,
        interface: str | None,
        *,
        remove: bool,
        options: ChangeOptions,
    ) -> ChangeResult:
        definition = await self._definition(principal, device_id, identifier)
        device = await self._device(principal, device_id)
        try:
            plan: ConfigPlan = adapter_of(device).acl.apply_acl(
                device.family,
                identifier,
                direction,
                interface=interface,
                remove=remove,
                ipv6=definition.ipv6,
                mgmt_acls=await self._mgmt_acls(principal, device),
            )
        except AclValidationError as exc:
            raise ValidationFailed(str(exc)) from exc
        result = await self._apply(principal, ctx, device, plan, options)
        if result.status == "applied":
            await self._record_binding(device.id, identifier, direction, interface, remove)
        return result

    async def _persist(self, device_id: uuid.UUID, d: AclDefinition) -> None:
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.scalar(
                select(Acl).where(Acl.device_id == device_id, Acl.identifier == d.identifier)
            )
            if row is None:
                row = Acl(device_id=device_id, identifier=d.identifier, applied_to=[])
                uow.session.add(row)
            row.number, row.name, row.kind, row.ipv6, row.description = (
                d.number,
                d.name,
                d.kind,
                d.ipv6,
                d.description,
            )
            await uow.session.flush()
            # Replace rules with SQL (not via the relationship) so the unique (acl_id, rule_id)
            # constraint never sees old and new rows at the same time.
            await uow.session.execute(delete(AclRule).where(AclRule.acl_id == row.id))
            uow.session.add_all(
                AclRule(
                    acl_id=row.id,
                    rule_id=r.rule_id,
                    action=r.action,
                    protocol=r.protocol,
                    source=r.source,
                    destination=r.destination,
                    source_port=_port_dict(r.source_port),
                    destination_port=_port_dict(r.destination_port),
                    source_mac=r.source_mac,
                    destination_mac=r.destination_mac,
                    vlan=r.vlan,
                    log=r.log,
                )
                for r in d.rules
            )
            await uow.commit()

    async def _record_binding(
        self,
        device_id: uuid.UUID,
        identifier: str,
        direction: str,
        interface: str | None,
        remove: bool,
    ) -> None:
        async with UnitOfWork(self._db) as uow:
            row = await uow.session.scalar(
                select(Acl).where(Acl.device_id == device_id, Acl.identifier == identifier)
            )
            if row is None:
                return
            entry = {"interface": interface, "direction": direction}
            bindings = [b for b in row.applied_to if b != entry]
            row.applied_to = bindings if remove else [*bindings, entry]
            await uow.commit()


__all__ = ["AclKind", "AclService"]
