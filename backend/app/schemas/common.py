"""Shared API schemas: pagination envelope, problem details, job acceptance."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Base for request/response bodies: strict unknown-field rejection on input."""

    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=True)


class PageResponse[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class ProblemDetail(BaseModel):
    """RFC 9457 problem+json envelope used for every error response."""

    type: str = Field(examples=["urn:huawei-nac:error:not_found"])
    title: str
    status: int
    detail: str
    code: str = Field(examples=["not_found"])
    message: str
    instance: str | None = None
    request_id: str | None = None
    details: Any = None


class JobAccepted(BaseModel):
    job_id: uuid.UUID
    status: str = "PENDING"
    location: str


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ProblemDetail, "content": {"application/problem+json": {}}}
    for code in (400, 401, 403, 404, 409, 422, 423, 429)
}


def page_of[M: BaseModel](page: Any, model: type[M]) -> PageResponse[M]:
    """Convert a repository Page of ORM rows into a typed response envelope."""
    return PageResponse[M](
        items=[model.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
