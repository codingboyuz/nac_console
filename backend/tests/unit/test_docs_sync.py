"""Docs that must track code: the RBAC matrix in SECURITY.md."""

from __future__ import annotations

from pathlib import Path

from app.core.rbac import permission_matrix_markdown

DOCS = Path(__file__).resolve().parents[2] / "docs"


def test_security_md_rbac_matrix_in_sync() -> None:
    text = (DOCS / "SECURITY.md").read_text(encoding="utf-8")
    block = text.split("<!-- RBAC:BEGIN -->")[1].split("<!-- RBAC:END -->")[0].strip()
    assert block == permission_matrix_markdown()


def test_required_docs_exist() -> None:
    for name in (
        "README.md",
        "ARCHITECTURE.md",
        "API.md",
        "SECURITY.md",
        "DEVELOPMENT.md",
        "DEPLOYMENT.md",
        "ASSUMPTIONS.md",
        "PROGRESS.md",
        "HARDWARE_VALIDATION.md",
        "adr/0001-radius-architecture.md",
    ):
        assert (DOCS / name).is_file(), name
