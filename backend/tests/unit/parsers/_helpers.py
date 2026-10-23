"""Fixture loading for parser tests."""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "huawei"
S5735 = "s5735_v200r019"
CE6881 = "ce6881_v200r005"
AR6120 = "ar6120_v300r021"
GENERATIONS = (S5735, CE6881)
ERROR_FIXTURES = sorted(p.name for p in (FIXTURES / "errors").glob("*.txt"))


def load(generation: str, filename: str) -> str:
    """Read ``tests/fixtures/huawei/<generation>/<filename>`` as UTF-8 text."""
    return (FIXTURES / generation / filename).read_text(encoding="utf-8")
