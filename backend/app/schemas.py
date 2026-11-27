"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None


class ProductionConfirmPayload(BaseModel):
    line_count: int
    avg_confidence: float
    elapsed_seconds: int


class HandoffOpenPayload(BaseModel):
    version_id: str
    version_no: str
    parent: str = "-"
    change_lines: int
    summary: str = ""
    digest: str


class HandoffResumePayload(BaseModel):
    digest: str | None = None


class HandoffRejectPayload(BaseModel):
    reason: str
