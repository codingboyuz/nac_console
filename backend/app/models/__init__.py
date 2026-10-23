"""SQLAlchemy models. Importing this package registers every table on Base.metadata."""

from app.models.acl import Acl, AclRule, Route
from app.models.audit import AuditLog
from app.models.backup import Backup
from app.models.base import Base
from app.models.device import Credential, Device, DeviceGroup, SshHostKey
from app.models.job import Job, JobDeviceResult, Setting
from app.models.monitoring import (
    Alert,
    AlertDelivery,
    AlertRule,
    DiscoveredDevice,
    DiscoveryRun,
    MaintenanceWindow,
    MetricSample,
)
from app.models.nac import Endpoint, NacPolicy, NacPolicyVersion, NacSession, RadiusServer
from app.models.network_state import ArpEntry, DhcpBinding, Interface, MacEntry, MacMove, Vlan
from app.models.user import RefreshToken, User

__all__ = [
    "Acl",
    "AclRule",
    "Alert",
    "AlertDelivery",
    "AlertRule",
    "ArpEntry",
    "AuditLog",
    "Backup",
    "Base",
    "Credential",
    "Device",
    "DeviceGroup",
    "DhcpBinding",
    "DiscoveredDevice",
    "DiscoveryRun",
    "Endpoint",
    "Interface",
    "Job",
    "JobDeviceResult",
    "MacEntry",
    "MacMove",
    "MaintenanceWindow",
    "MetricSample",
    "NacPolicy",
    "NacPolicyVersion",
    "NacSession",
    "RadiusServer",
    "RefreshToken",
    "Route",
    "Setting",
    "SshHostKey",
    "User",
    "Vlan",
]
