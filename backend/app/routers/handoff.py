"""产出确认与子版本交接专用接口。

顺序调用与原按钮不变：用户仍在转写任务页点「确认产出」写入正文，
再到转写版本页核对子版本交接记录。这里只承载共享写入边界上的领域操作，
通用资源的查询/登记/动作仍走 resource 路由。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.pipeline.production_handoff_registry import production_handoff_registry
from app.schemas import (
    ActionResult,
    HandoffOpenPayload,
    HandoffRejectPayload,
    HandoffResumePayload,
    ProductionConfirmPayload,
)
from app.services import service_for
from app.services.transcribe_task import TranscribeTaskService
from app.services.transcript_version import TranscriptVersionService

router = APIRouter(prefix="/api", tags=["产出确认与版本交接"])


def _task_service() -> TranscribeTaskService:
    service = service_for("transcribe_task")
    assert isinstance(service, TranscribeTaskService)
    return service


def _version_service() -> TranscriptVersionService:
    service = service_for("transcript_version")
    assert isinstance(service, TranscriptVersionService)
    return service


@router.post("/transcribe-task/{task_id}/productions", response_model=ActionResult)
def confirm_production(task_id: str, payload: ProductionConfirmPayload) -> ActionResult:
    """任务页「确认产出」：行数、平均置信度、耗时及任务不存在行为与旧实现一致。"""
    ok, message, entry = _task_service().produce_transcription(
        task_id, payload.line_count, payload.avg_confidence, payload.elapsed_seconds,
    )
    return ActionResult(ok=ok, message=message, entry=entry)


@router.get("/transcribe-task/{task_id}/productions")
def list_productions(task_id: str) -> dict[str, Any]:
    """读取任务的全部确认记录，含被引擎更正的旧确认及其更正缘由。"""
    current = _task_service().current_production(task_id)
    return {
        "task_id": task_id,
        "current": current,
        "items": _task_service().list_productions(task_id),
    }


@router.get("/transcribe-task/{task_id}/version-handoffs")
def list_task_handoffs(task_id: str) -> dict[str, Any]:
    """任务页续办视角：看该任务哪次交接可以继续、拒绝缘由是什么。"""
    items = _version_service().list_handoffs(task_id)
    return {"task_id": task_id, "items": items, "total": len(items)}


@router.post("/transcribe-task/{task_id}/version-handoffs", response_model=ActionResult)
def open_handoff(task_id: str, payload: HandoffOpenPayload) -> ActionResult:
    """版本页「登记子版本」：依据已确认产出的指纹开放待续办交接（中断点）。"""
    entry, message = _version_service().open_version_handoff(
        version_id=payload.version_id,
        task_id=task_id,
        version_no=payload.version_no,
        parent=payload.parent,
        change_lines=payload.change_lines,
        summary=payload.summary,
        digest=payload.digest,
    )
    return ActionResult(ok=entry is not None, message=message, entry=entry)


@router.post("/version-handoffs/{handoff_id}/resume", response_model=ActionResult)
def resume_handoff(handoff_id: str, payload: HandoffResumePayload) -> ActionResult:
    """续办交接：指纹与当前最新确认产出一致才落库，否则只登记拒绝缘由。"""
    ok, message, entry = _version_service().resume_version_handoff(
        handoff_id, payload.digest,
    )
    return ActionResult(ok=ok, message=message, entry=entry)


@router.post("/version-handoffs/{handoff_id}/reject", response_model=ActionResult)
def reject_handoff(handoff_id: str, payload: HandoffRejectPayload) -> ActionResult:
    """显式拒绝交接：关闭待续办记录并留存缘由。"""
    ok, message, entry = _version_service().reject_version_handoff(
        handoff_id, payload.reason,
    )
    return ActionResult(ok=ok, message=message, entry=entry)


@router.get("/version-handoffs")
def list_all_handoffs(task_id: str | None = None) -> dict[str, Any]:
    """版本页核对视角：全部子版本交接记录（可按任务过滤）。"""
    items = _version_service().list_handoffs(task_id)
    return {"items": items, "total": len(items)}


@router.get("/production-boundary/snapshot")
def boundary_snapshot() -> dict[str, Any]:
    """共享写入边界的完整快照，供审计与联调查看确认记录与交接记录。"""
    return production_handoff_registry.snapshot()
