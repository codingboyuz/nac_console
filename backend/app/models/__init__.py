"""SQLAlchemy models. Importing this package registers every table on Base.metadata."""

from app.models.acl import Acl, AclRule, Route
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.device import Credential, Device, DeviceGroup, SshHostKey
from app.models.job import Job, JobDeviceResult, Setting
from app.models.network_state import ArpEntry, DhcpBinding, Interface, MacEntry, MacMove, Vlan
from app.models.user import RefreshToken, User

__all__ = [
    "Acl",
    "AclRule",
    "ArpEntry",
    "AuditLog",
    "Base",
    "Credential",
    "Device",
    "DeviceGroup",
    "DhcpBinding",
    "Interface",
    "Job",
    "JobDeviceResult",
    "MacEntry",
    "MacMove",
    "RefreshToken",
    "Route",
    "Setting",
    "SshHostKey",
    "User",
    "Vlan",
]
