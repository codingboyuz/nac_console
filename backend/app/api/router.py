"""Top-level routers: public /api/v1 and network-restricted /internal/v1."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api import internal_radius
from app.api.deps import rate_limited
from app.api.v1 import (
    acl,
    arp,
    auth,
    backups,
    device_groups,
    devices,
    interfaces,
    jobs,
    mac,
    monitoring,
    nac,
    routing,
    stp,
    users,
    vlans,
)

api_router = APIRouter(
    prefix="/api/v1", dependencies=[Depends(rate_limited("global", "rate_limit_default"))]
)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(users.audit_router)
api_router.include_router(devices.router)
api_router.include_router(device_groups.router)
api_router.include_router(device_groups.cred_router)
for _module in (vlans, interfaces, mac, stp, acl, routing):
    api_router.include_router(_module.router)
api_router.include_router(jobs.router)
api_router.include_router(monitoring.router)
api_router.include_router(nac.router)
api_router.include_router(nac.radius_router)
api_router.include_router(backups.router)
api_router.include_router(backups.device_router)
api_router.include_router(arp.router)
api_router.include_router(arp.dhcp_router)

internal_router = APIRouter(prefix="/internal/v1")
internal_router.include_router(internal_radius.router)
