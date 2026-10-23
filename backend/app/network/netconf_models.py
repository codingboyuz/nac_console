"""NETCONF capability parsing and YANG ModelRegistry (never assume uniform YANG support).

The registry is built from the <hello> capabilities of each device and cached in
``device.capabilities['netconf']``. Feature code asks the registry which module set to use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

BASE_CAPS = {
    "candidate": "urn:ietf:params:netconf:capability:candidate:1.0",
    "confirmed_commit": "urn:ietf:params:netconf:capability:confirmed-commit:1.",
    "validate": "urn:ietf:params:netconf:capability:validate:1.",
    "rollback_on_error": "urn:ietf:params:netconf:capability:rollback-on-error:1.0",
    "startup": "urn:ietf:params:netconf:capability:startup:1.0",
    "writable_running": "urn:ietf:params:netconf:capability:writable-running:1.0",
    "xpath": "urn:ietf:params:netconf:capability:xpath:1.0",
}

# Feature -> candidate YANG modules in order of preference.
# VRP-VERSION-DEPENDENT: CE (VRP V8) uses urn:huawei:yang:huawei-*; S-series V200R0xx with
# NETCONF uses the legacy schema "http://www.huawei.com/netconf/vrp" (module 'huawei-vlan' too).
FEATURE_MODULES: dict[str, tuple[str, ...]] = {
    "vlan": ("huawei-vlan", "openconfig-vlan"),
    "interfaces": ("huawei-ifm", "ietf-interfaces", "openconfig-interfaces"),
    "acl": ("huawei-acl", "ietf-access-control-list"),
    "routing": ("huawei-routing", "ietf-routing"),
    "ospf": ("huawei-ospfv2", "ietf-ospf"),
    "bgp": ("huawei-bgp", "openconfig-bgp"),
    "stp": ("huawei-mstp",),
    "system": ("huawei-system", "ietf-system"),
}


@dataclass(frozen=True, slots=True)
class YangModule:
    name: str
    namespace: str
    revision: str | None = None


@dataclass(slots=True)
class ModelRegistry:
    capabilities: list[str] = field(default_factory=list)
    modules: dict[str, YangModule] = field(default_factory=dict)
    features: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def from_capabilities(cls, caps: list[str]) -> ModelRegistry:
        registry = cls(capabilities=sorted(set(caps)))
        for cap in caps:
            for feature, prefix in BASE_CAPS.items():
                if cap.startswith(prefix):
                    registry.features[feature] = True
            module = _module_from_capability(cap)
            if module is not None:
                registry.modules[module.name] = module
        for feature in BASE_CAPS:
            registry.features.setdefault(feature, False)
        return registry

    def module_for(self, feature: str) -> YangModule | None:
        """Preferred module supported by this device for a feature, or None."""
        for name in FEATURE_MODULES.get(feature, ()):
            if name in self.modules:
                return self.modules[name]
        return None

    def supports(self, feature: str) -> bool:
        return self.features.get(feature, False) or self.module_for(feature) is not None

    @property
    def edit_target(self) -> str:
        """'candidate' when supported (CE), otherwise 'running' if writable."""
        if self.features.get("candidate"):
            return "candidate"
        return "running"

    def to_dict(self) -> dict[str, Any]:
        return {
            "features": dict(sorted(self.features.items())),
            "modules": {
                m.name: {"ns": m.namespace, "rev": m.revision} for m in self.modules.values()
            },
            "edit_target": self.edit_target,
            "feature_modules": {
                feature: (mod.name if (mod := self.module_for(feature)) else None)
                for feature in FEATURE_MODULES
            },
        }


def _module_from_capability(cap: str) -> YangModule | None:
    """Parse 'urn:huawei:yang:huawei-vlan?module=huawei-vlan&revision=2019-04-24'."""
    if "module=" not in cap:
        return None
    parsed = urlparse(cap)
    query = parse_qs(parsed.query)
    names = query.get("module")
    if not names:
        return None
    namespace = cap.split("?", 1)[0]
    revisions = query.get("revision")
    revision = revisions[0] if revisions else None
    return YangModule(names[0], namespace, revision)
