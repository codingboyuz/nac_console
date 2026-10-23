"""Parser for ``display transceiver verbose`` (S-series key/value and CE diagnostic table)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.network.parsers.common import clean_lines, none_if_dash, safe_interface, to_float

_BLOCK_RE = re.compile(r"^\s*(?P<intf>\S+)\s+transceiver information\s*:?\s*$", re.IGNORECASE)
_KV_RE = re.compile(r"^\s*(?P<key>[^:]+?)\s*:\s*(?P<val>.*?)\s*$")
_TABLE_COL_RE = re.compile(
    r"Temperature\s*\([^)]*\)|Voltage\s*\([^)]*\)|Bias Current\s*\([^)]*\)"
    r"|RX Power\s*\([^)]*\)|TX Power\s*\([^)]*\)",
    re.IGNORECASE,
)
_NUMBERS_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?(?:\s+-?\d+(?:\.\d+)?)+\s*$")

# Normalized key (letters only, lower-case) -> field name.
_KEYS = {
    "transceivertype": "transceiver_type",
    "wavelengthnm": "wavelength_nm",
    "wavelength": "wavelength_nm",
    "vendorname": "vendor",
    "currentrxpowerdbm": "rx_power_dbm",
    "rxpowerdbm": "rx_power_dbm",
    "currenttxpowerdbm": "tx_power_dbm",
    "txpowerdbm": "tx_power_dbm",
    "temperaturec": "temperature_c",
    "temperaturecelsius": "temperature_c",
    "temperature": "temperature_c",
    "biascurrentma": "bias_ma",
    "currentbiasma": "bias_ma",
    "supplyvoltagev": "voltage_v",
    "voltagev": "voltage_v",
}


@dataclass(frozen=True, slots=True)
class TransceiverInfo:
    """Optical module identity and DDM readings for one interface."""

    interface: str
    transceiver_type: str | None
    wavelength_nm: int | None
    vendor: str | None
    rx_power_dbm: float | None
    tx_power_dbm: float | None
    temperature_c: float | None
    bias_ma: float | None
    voltage_v: float | None


@dataclass(slots=True)
class _Block:
    interface: str
    values: dict[str, str] = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)

    def put(self, raw_key: str, value: str) -> None:
        name = _KEYS.get(re.sub(r"[^a-z]", "", raw_key.lower()))
        cleaned = none_if_dash(value)
        if name and cleaned is not None and name not in self.values:
            self.values[name] = cleaned

    def freeze(self) -> TransceiverInfo:
        def num(key: str) -> float | None:
            return to_float(self.values.get(key))

        wavelength = num("wavelength_nm")
        return TransceiverInfo(
            interface=self.interface,
            transceiver_type=self.values.get("transceiver_type"),
            wavelength_nm=int(wavelength) if wavelength is not None else None,
            vendor=self.values.get("vendor"),
            rx_power_dbm=num("rx_power_dbm"),
            tx_power_dbm=num("tx_power_dbm"),
            temperature_c=num("temperature_c"),
            bias_ma=num("bias_ma"),
            voltage_v=num("voltage_v"),
        )


def parse_transceivers(text: str | None) -> list[TransceiverInfo]:
    """Parse one :class:`TransceiverInfo` per ``<intf> transceiver information:`` block."""
    blocks: list[_Block] = []
    current: _Block | None = None
    for line in clean_lines(text):
        if start := _BLOCK_RE.match(line):
            name = safe_interface(start.group("intf")) or start.group("intf")
            current = _Block(interface=name)
            blocks.append(current)
            continue
        if current is None or not line.strip():
            continue
        columns = _TABLE_COL_RE.findall(line)
        if len(columns) >= 2 and ":" not in line:
            current.columns = columns
            continue
        if current.columns and _NUMBERS_RE.match(line):
            for column, value in zip(current.columns, line.split(), strict=False):
                current.put(column, value)
            current.columns = []
            continue
        if (kv := _KV_RE.match(line)) and "threshold" not in kv.group("key").lower():
            current.put(kv.group("key"), kv.group("val"))
    return [block.freeze() for block in blocks]
