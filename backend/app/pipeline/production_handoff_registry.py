"""产出确认与子版本交接的共享写入边界。

转写任务的「确认产出」与转写版本的「子版本登记」原本各自直接写各自的表，
中途中断、引擎重跑更正、他人并行登记时，子版本可能错绑到另一份正文。
本登记处把两类写入收敛到同一把锁、同一份产出指纹之下：

- 每次确认产出生成一条带指纹的确认记录，指纹由任务编号与行数/置信度/耗时派生；
  引擎重跑产出不同正文时，旧确认记录标记为「已更正」并保留更正缘由。
- 子版本登记分两步：先开放一条「待续办」交接（中断点），续办时重新核对
  交接指纹与当前最新确认产出是否一致，不一致只登记拒绝缘由、绝不落库，
  保证任何版本行都不会错绑另一份正文。

登记处不接触 store，只承载边界状态与并发串行化；store 行的写入由领域服务
在 ``locked()`` 临界区内完成，保证「核对 + 写入」整体原子。
"""
from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterator


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def production_digest(
    task_id: str, line_count: int, avg_confidence: float, elapsed_seconds: int,
) -> str:
    """按任务编号与产出指标派生正文指纹：同一份正文指纹稳定，不同正文指纹不同。"""
    raw = f"{task_id}|{line_count}|{avg_confidence}|{elapsed_seconds}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


@dataclass
class ProductionRecord:
    """一次产出确认：同一任务每次确认递增序号，旧记录被引擎更正后保留缘由。"""

    seq: int
    task_id: str
    line_count: int
    avg_confidence: float
    elapsed_seconds: int
    digest: str
    status: str = "已确认"
    correction_reason: str = ""
    confirmed_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "确认序号": self.seq,
            "关联任务": self.task_id,
            "输出文本行数": str(self.line_count),
            "平均置信度": str(self.avg_confidence),
            "转写耗时": str(self.elapsed_seconds),
            "产出指纹": self.digest,
            "确认状态": self.status,
            "更正原因": self.correction_reason or "-",
            "确认时间": self.confirmed_at,
        }


@dataclass
class HandoffAttempt:
    """一条子版本交接记录：开放后处于待续办，续办核对通过才落库，否则留下拒绝缘由。"""

    handoff_id: str
    task_id: str
    version_id: str
    version_no: str
    parent_version_no: str
    change_lines: int
    summary: str
    digest: str
    status: str = "待续办"
    committed_version_id: str = ""
    last_reject_reason: str = ""
    rejections: list[dict[str, str]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self, can_continue: bool = False) -> dict[str, Any]:
        return {
            "交接编号": self.handoff_id,
            "关联任务": self.task_id,
            "版本编号": self.version_id,
            "版本号": self.version_no,
            "父版本号": self.parent_version_no,
            "变更行数": str(self.change_lines),
            "变更摘要": self.summary,
            "产出指纹": self.digest,
            "交接状态": self.status,
            "可续办": "是" if can_continue else "否",
            "拒绝缘由": self.last_reject_reason or "-",
            "拒绝次数": str(len(self.rejections)),
            "落库版本编号": self.committed_version_id or "-",
            "创建时间": self.created_at,
            "更新时间": self.updated_at,
        }


class ProductionHandoffRegistry:
    """产出确认与子版本交接的共享写入边界（进程内串行化）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, list[ProductionRecord]] = {}
        self._current: dict[str, ProductionRecord] = {}
        self._handoffs: list[HandoffAttempt] = []

    @contextmanager
    def locked(self) -> Iterator[None]:
        """暴露临界区：领域服务在同一把锁内完成核对与 store 行写入。"""
        with self._lock:
            yield

    def record_production(
        self, task_id: str, line_count: int, avg_confidence: float,
        elapsed_seconds: int,
    ) -> ProductionRecord:
        """登记一次产出确认；正文变化时把上一条确认更正为历史记录。"""
        with self._lock:
            digest = production_digest(task_id, line_count, avg_confidence, elapsed_seconds)
            current = self._current.get(task_id)
            if current is not None and current.digest == digest:
                return current
            history = self._records.setdefault(task_id, [])
            if current is not None:
                current.status = "已更正"
                current.correction_reason = (
                    f"引擎重跑更正：行数 {current.line_count}→{line_count}，"
                    f"平均置信度 {current.avg_confidence}→{avg_confidence}，"
                    f"转写耗时 {current.elapsed_seconds}→{elapsed_seconds}"
                )
            record = ProductionRecord(
                seq=len(history) + 1,
                task_id=task_id,
                line_count=line_count,
                avg_confidence=avg_confidence,
                elapsed_seconds=elapsed_seconds,
                digest=digest,
            )
            history.append(record)
            self._current[task_id] = record
            return record

    def current_production(self, task_id: str) -> ProductionRecord | None:
        with self._lock:
            return self._current.get(task_id)

    def list_productions(self, task_id: str) -> list[ProductionRecord]:
        with self._lock:
            return list(self._records.get(task_id, []))

    def has_production(self, task_id: str, digest: str) -> bool:
        with self._lock:
            return any(r.digest == digest for r in self._records.get(task_id, []))

    def open_handoff(
        self, task_id: str, version_id: str, version_no: str,
        parent_version_no: str, change_lines: int, summary: str, digest: str,
    ) -> tuple[HandoffAttempt | None, str]:
        """开放一条子版本交接（写入中断点）：指纹必须来自该任务已确认的正文。"""
        with self._lock:
            if not self.has_production(task_id, digest):
                return None, (
                    f"任务 {task_id} 不存在指纹 {digest} 的确认产出，"
                    "子版本不能绑定未确认正文"
                )
            if any(h.version_id == version_id for h in self._handoffs):
                return None, f"版本编号 {version_id} 已存在"
            handoff_id = f"HD-{len(self._handoffs) + 1:04d}"
            attempt = HandoffAttempt(
                handoff_id=handoff_id,
                task_id=task_id,
                version_id=version_id,
                version_no=version_no,
                parent_version_no=parent_version_no,
                change_lines=change_lines,
                summary=summary,
                digest=digest,
            )
            self._handoffs.append(attempt)
            return attempt, f"交接 {handoff_id} 已登记，等待续办落库"

    def get_handoff(self, handoff_id: str) -> HandoffAttempt | None:
        with self._lock:
            return next((h for h in self._handoffs if h.handoff_id == handoff_id), None)

    def list_handoffs(self, task_id: str | None = None) -> list[HandoffAttempt]:
        with self._lock:
            if task_id is None:
                return list(self._handoffs)
            return [h for h in self._handoffs if h.task_id == task_id]

    def _is_committed_no(self, task_id: str, version_no: str) -> bool:
        return any(
            h.task_id == task_id and h.version_no == version_no and h.status == "已落库"
            for h in self._handoffs
        )

    def can_continue(self, attempt: HandoffAttempt) -> bool:
        """当前最新确认产出仍是交接所依据的正文、且版本号未被占用时才可续办。"""
        if attempt.status != "待续办":
            return False
        current = self._current.get(attempt.task_id)
        if current is None or current.digest != attempt.digest:
            return False
        return not self._is_committed_no(attempt.task_id, attempt.version_no)

    def resume_handoff(
        self, handoff_id: str, digest: str | None,
        commit_callback: Callable[[HandoffAttempt], dict[str, Any]],
    ) -> tuple[bool, str, HandoffAttempt | None, dict[str, Any] | None]:
        """续办交接：核对指纹一致后在同一临界区内调用回调落库版本行。

        核对不通过时只追加拒绝缘由并保持「待续办」，绝不调用落库回调，
        从而保证任何产出都不会错绑另一份正文。
        """
        with self._lock:
            attempt = next(
                (h for h in self._handoffs if h.handoff_id == handoff_id), None,
            )
            if attempt is None:
                return False, f"交接 {handoff_id} 不存在", None, None
            if attempt.status != "待续办":
                return False, f"交接 {handoff_id} 状态为{attempt.status}，不可续办", attempt, None

            current = self._current.get(attempt.task_id)
            current_digest = current.digest if current is not None else "无"
            requested = digest or attempt.digest
            if requested != attempt.digest or current_digest != attempt.digest:
                reason = (
                    f"产出指纹不一致：交接依据指纹 {attempt.digest}，"
                    f"当前最新确认产出指纹 {current_digest}，"
                    "拒绝把另一份正文绑定到该子版本"
                )
                self._append_rejection(attempt, reason)
                return False, reason, attempt, None

            if self._is_committed_no(attempt.task_id, attempt.version_no):
                reason = (
                    f"任务 {attempt.task_id} 已存在版本号 {attempt.version_no} 的落库记录，"
                    "不能重复绑定"
                )
                self._append_rejection(attempt, reason)
                return False, reason, attempt, None

            entry = commit_callback(attempt)
            attempt.status = "已落库"
            attempt.committed_version_id = str(entry.get("版本编号", attempt.version_id))
            attempt.updated_at = _now()
            return True, f"交接 {handoff_id} 已续办并落库版本 {attempt.committed_version_id}", attempt, entry

    def reject_handoff(
        self, handoff_id: str, reason: str,
    ) -> tuple[bool, str, HandoffAttempt | None]:
        """显式拒绝交接：关闭待续办记录并留存缘由。"""
        with self._lock:
            attempt = next(
                (h for h in self._handoffs if h.handoff_id == handoff_id), None,
            )
            if attempt is None:
                return False, f"交接 {handoff_id} 不存在", None
            if attempt.status != "待续办":
                return False, f"交接 {handoff_id} 状态为{attempt.status}，不可拒绝", attempt
            attempt.status = "已拒绝"
            attempt.last_reject_reason = reason or "未填写拒绝缘由"
            attempt.updated_at = _now()
            return True, f"交接 {handoff_id} 已拒绝", attempt

    def _append_rejection(self, attempt: HandoffAttempt, reason: str) -> None:
        attempt.rejections.append({"缘由": reason, "时间": _now()})
        attempt.last_reject_reason = reason
        attempt.updated_at = _now()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "productions": {
                    task_id: [r.to_dict() for r in records]
                    for task_id, records in self._records.items()
                },
                "handoffs": [
                    h.to_dict(can_continue=self.can_continue(h)) for h in self._handoffs
                ],
            }


# 领域服务共享的单例边界：所有产出确认与子版本交接写入都经过同一把锁。
production_handoff_registry = ProductionHandoffRegistry()
