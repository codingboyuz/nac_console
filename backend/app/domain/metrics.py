"""Counter arithmetic for SNMP polling: 32/64-bit wrap handling and rate computation."""

from __future__ import annotations

from dataclasses import dataclass

MAX32 = 2**32
MAX64 = 2**64


def counter_delta(previous: int | None, current: int | None, *, bits: int) -> int | None:
    """Delta between two counter samples, handling a single wrap.

    Returns None when a value is missing or when the jump looks like a counter reset (device
    reboot / interface re-creation): a "wrap" larger than half the counter range is treated as
    a reset instead of producing a bogus huge rate.
    """
    if previous is None or current is None:
        return None
    if current >= previous:
        return current - previous
    modulus = MAX64 if bits == 64 else MAX32
    wrapped = modulus - previous + current
    return wrapped if wrapped < modulus // 2 else None


def rate_per_second(delta: int | None, seconds: float) -> float | None:
    if delta is None or seconds <= 0:
        return None
    return delta / seconds


@dataclass(frozen=True, slots=True)
class InterfaceCounters:
    in_octets: int | None
    out_octets: int | None
    in_errors: int | None
    out_errors: int | None
    in_discards: int | None
    out_discards: int | None
    counter_bits: int = 64


@dataclass(frozen=True, slots=True)
class InterfaceRates:
    in_bps: float | None
    out_bps: float | None
    in_errors_ps: float | None
    out_errors_ps: float | None
    in_discards_ps: float | None
    out_discards_ps: float | None


def interface_rates(
    prev: InterfaceCounters, curr: InterfaceCounters, seconds: float
) -> InterfaceRates:
    """Bits/s for octet counters and events/s for error/discard counters (always 32-bit)."""
    bits = min(prev.counter_bits, curr.counter_bits)

    def octets(a: int | None, b: int | None) -> float | None:
        rate = rate_per_second(counter_delta(a, b, bits=bits), seconds)
        return None if rate is None else rate * 8

    def events(a: int | None, b: int | None) -> float | None:
        return rate_per_second(counter_delta(a, b, bits=32), seconds)

    return InterfaceRates(
        in_bps=octets(prev.in_octets, curr.in_octets),
        out_bps=octets(prev.out_octets, curr.out_octets),
        in_errors_ps=events(prev.in_errors, curr.in_errors),
        out_errors_ps=events(prev.out_errors, curr.out_errors),
        in_discards_ps=events(prev.in_discards, curr.in_discards),
        out_discards_ps=events(prev.out_discards, curr.out_discards),
    )
