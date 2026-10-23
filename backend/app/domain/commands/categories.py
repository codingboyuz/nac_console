"""Command safety categories and classification results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class CommandCategory(StrEnum):
    SAFE = "SAFE"
    WARNING = "WARNING"
    DANGEROUS = "DANGEROUS"

    @property
    def rank(self) -> int:
        return {"SAFE": 0, "WARNING": 1, "DANGEROUS": 2}[self.value]


def worst(*categories: CommandCategory) -> CommandCategory:
    return max(categories, key=lambda c: c.rank, default=CommandCategory.SAFE)


@dataclass(frozen=True, slots=True)
class LineClassification:
    line: str
    category: CommandCategory
    reason: str
    view: str | None = None


@dataclass(frozen=True, slots=True)
class Classification:
    """Overall classification of one command or a configuration block."""

    category: CommandCategory
    reason: str
    lines: tuple[LineClassification, ...] = field(default_factory=tuple)

    @property
    def is_safe(self) -> bool:
        return self.category is CommandCategory.SAFE

    @property
    def is_dangerous(self) -> bool:
        return self.category is CommandCategory.DANGEROUS

    @classmethod
    def of_lines(cls, lines: list[LineClassification]) -> Classification:
        if not lines:
            return cls(CommandCategory.SAFE, "empty", ())
        top = worst(*(item.category for item in lines))
        reasons = [f"{i.line!r}: {i.reason}" for i in lines if i.category is top]
        return cls(top, "; ".join(reasons[:5]), tuple(lines))
