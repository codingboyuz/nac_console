"""Composition root: wires settings, DB, Redis and services once per process."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.crypto import CredentialVault
from app.core.database import Database
from app.core.rate_limit import RateLimiter
from app.core.security import TokenService
from app.network.factory import DeviceConnectionFactory
from app.services.acl_service import AclService
from app.services.arp_service import ArpService
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.change_service import ChangeService
from app.services.collection_service import CollectionService
from app.services.command_service import CommandService
from app.services.confirmation_service import ConfirmationService
from app.services.connection_service import DeviceConnector
from app.services.credential_service import CredentialService
from app.services.device_service import DeviceService
from app.services.interface_service import InterfaceService
from app.services.mac_service import MacService
from app.services.routing_service import RoutingService
from app.services.stp_service import StpService
from app.services.user_service import UserService
from app.services.vlan_service import VlanService


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    db: Database
    redis: Redis
    rate_limiter: RateLimiter
    tokens: TokenService
    audit: AuditService
    auth: AuthService
    users: UserService
    vault: CredentialVault
    credentials: CredentialService
    devices: DeviceService
    factory: DeviceConnectionFactory
    connector: DeviceConnector
    confirmations: ConfirmationService
    commands: CommandService
    collection: CollectionService
    changes: ChangeService
    vlans: VlanService
    interfaces: InterfaceService
    macs: MacService
    arp: ArpService
    stp: StpService
    acls: AclService
    routing: RoutingService

    @classmethod
    def build(cls, settings: Settings, db: Database, redis: Redis) -> ServiceContainer:
        tokens = TokenService(settings)
        audit = AuditService(db)
        auth = AuthService(settings, db, redis, tokens, audit)
        vault = CredentialVault(settings.credential_keys, settings.credential_active_key_id)
        credentials = CredentialService(db, vault)
        devices = DeviceService(db)
        factory = DeviceConnectionFactory(max_sessions_per_device=settings.device_max_sessions)
        connector = DeviceConnector(settings, db, credentials, factory, audit)
        confirmations = ConfirmationService(redis, settings.confirmation_token_ttl_seconds)
        collection = CollectionService(db, connector)
        changes = ChangeService(settings, redis, devices, connector, confirmations, audit)
        return cls(
            settings=settings,
            db=db,
            redis=redis,
            rate_limiter=RateLimiter(redis),
            tokens=tokens,
            audit=audit,
            auth=auth,
            users=UserService(db, auth),
            vault=vault,
            credentials=credentials,
            devices=devices,
            factory=factory,
            connector=connector,
            confirmations=confirmations,
            commands=CommandService(devices, connector, confirmations, audit),
            collection=collection,
            changes=changes,
            vlans=VlanService(devices, collection, changes),
            interfaces=InterfaceService(db, devices, collection, changes),
            macs=MacService(db, devices, collection, changes),
            arp=ArpService(db, devices, collection, changes),
            stp=StpService(devices, collection, changes),
            acls=AclService(db, devices, collection, changes),
            routing=RoutingService(db, devices, collection, changes),
        )

    async def aclose(self) -> None:
        """Release resources owned by services (connection pools etc.)."""
