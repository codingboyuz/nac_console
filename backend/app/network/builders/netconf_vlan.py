"""NETCONF payload builders for VLANs (huawei-vlan YANG). XML is built with ElementTree, so
operator text is always escaped - never string-concatenated.

VRP-VERSION-DEPENDENT: namespace ``urn:huawei:yang:huawei-vlan`` is the CE (VRP V8) module;
S-series V200R NETCONF uses the legacy ``http://www.huawei.com/netconf/vrp`` schema, which is
selected through ``LEGACY_NS`` when the ModelRegistry reports that namespace.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET  # nosec B405

from app.domain.vlans import validate_vlan_id

NC_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
VLAN_NS = "urn:huawei:yang:huawei-vlan"
LEGACY_NS = "http://www.huawei.com/netconf/vrp"


def _config_root() -> ET.Element:
    ET.register_namespace("nc", NC_NS)
    return ET.Element(f"{{{NC_NS}}}config")


def _vlans_container(root: ET.Element, namespace: str) -> ET.Element:
    vlan = ET.SubElement(root, f"{{{namespace}}}vlan")
    return ET.SubElement(vlan, f"{{{namespace}}}vlans")


def vlan_create_xml(vlans: dict[int, str | None], *, namespace: str = VLAN_NS) -> str:
    """Merge-create VLANs with optional names."""
    root = _config_root()
    container = _vlans_container(root, namespace)
    for vid in sorted(vlans):
        entry = ET.SubElement(container, f"{{{namespace}}}vlan")
        ET.SubElement(entry, f"{{{namespace}}}id").text = str(validate_vlan_id(vid))
        name = vlans[vid]
        if name:
            ET.SubElement(entry, f"{{{namespace}}}name").text = name[:31]
    return ET.tostring(root, encoding="unicode")


def vlan_delete_xml(vlan_ids: list[int], *, namespace: str = VLAN_NS) -> str:
    root = _config_root()
    container = _vlans_container(root, namespace)
    for vid in sorted(set(vlan_ids)):
        entry = ET.SubElement(
            container, f"{{{namespace}}}vlan", {f"{{{NC_NS}}}operation": "delete"}
        )
        ET.SubElement(entry, f"{{{namespace}}}id").text = str(validate_vlan_id(vid))
    return ET.tostring(root, encoding="unicode")


def vlan_filter_xml(*, namespace: str = VLAN_NS) -> str:
    """Subtree filter selecting the VLAN list."""
    vlan = ET.Element(f"{{{namespace}}}vlan")
    ET.SubElement(vlan, f"{{{namespace}}}vlans")
    return ET.tostring(vlan, encoding="unicode")
