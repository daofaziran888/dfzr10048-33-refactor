"""转写版本落库领域服务：把转写结果冻结成带父版本号的版本记录，产出版本记录数据集供字幕包引用与纠错回路消费。

写入边界：子版本登记不再直接写版本表，而是先在 ``production_handoff_registry``
开放一条待续办交接（写入中断点）；续办时由边界核对交接指纹与任务当前最新
确认产出一致后，本服务才在同一临界区内创建版本行。指纹不一致只会留下
拒绝缘由，版本行不会落库，因此任何产出都不能错绑另一份正文。
"""
from __future__ import annotations

from typing import Any

from app.pipeline.production_handoff_registry import (
    production_handoff_registry,
)
from app.services.base import BaseModuleService
from app.store import store


class TranscriptVersionService(BaseModuleService):
    MODULE = "transcript_version"
    ENTITY = "版本记录"
    OPERATION_TERM = "落库"
    LIST_FIELDS = ["版本编号", "关联任务", "版本号", "父版本号", "变更行数", "变更摘要", "生效时间", "被引用次数"]
    REQUIRED_FIELDS = ["版本编号", "关联任务", "版本号"]
    STATUS_ORDER = ["草稿", "过渡中", "已生效", "已废止"]
    ACTION_RULES = {"启动过渡": "过渡中", "生效版本": "已生效", "废止版本": "已废止"}
    NEGATIVE_ACTIONS = ["废止版本"]

    def create_version(
        self, version_id: str, task_id: str, version_no: str,
        parent: str, change_lines: int, summary: str,
    ) -> tuple[dict[str, Any] | None, str]:
        """创建新转写版本（原有登记入口，行为与字段保持不变）。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("版本编号") == version_id:
                return None, f"版本编号 {version_id} 已存在"
        entry = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "版本编号": version_id, "关联任务": task_id,
            "版本号": version_no, "父版本号": parent,
            "变更行数": str(change_lines), "变更摘要": summary,
            "生效时间": "-", "被引用次数": "0",
            "status": "草稿", "pending": True, "abnormal": False,
        }
        rows.append(entry)
        return entry, f"版本 {version_id} 已创建"

    def open_version_handoff(
        self, version_id: str, task_id: str, version_no: str,
        parent: str, change_lines: int, summary: str, digest: str,
    ) -> tuple[dict[str, Any] | None, str]:
        """开放子版本交接：先登记写入中断点，不落版本行。

        指纹必须来自该任务一次已确认的产出，否则边界直接拒绝，
        防止未确认正文被登记成子版本。
        """
        attempt, message = production_handoff_registry.open_handoff(
            task_id=task_id, version_id=version_id, version_no=version_no,
            parent_version_no=parent, change_lines=change_lines,
            summary=summary, digest=digest,
        )
        if attempt is None:
            return None, message
        return attempt.to_dict(can_continue=production_handoff_registry.can_continue(attempt)), message

    def resume_version_handoff(
        self, handoff_id: str, digest: str | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """续办交接：核对指纹一致后在边界临界区内落库版本行，否则只留拒绝缘由。"""
        def _commit(attempt: Any) -> dict[str, Any]:
            entry, _ = self.create_version(
                version_id=attempt.version_id,
                task_id=attempt.task_id,
                version_no=attempt.version_no,
                parent=attempt.parent_version_no,
                change_lines=attempt.change_lines,
                summary=attempt.summary,
            )
            # 边界已保证版本号/编号未占用；落库行记下绑定的产出指纹以便追溯。
            entry["_产出指纹"] = attempt.digest
            return entry

        ok, message, attempt, entry = production_handoff_registry.resume_handoff(
            handoff_id, digest, _commit,
        )
        if attempt is None:
            return False, message, None
        return ok, message, entry

    def reject_version_handoff(
        self, handoff_id: str, reason: str,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """显式拒绝交接：关闭待续办记录并留存缘由，不落任何版本行。"""
        ok, message, attempt = production_handoff_registry.reject_handoff(handoff_id, reason)
        if attempt is None:
            return False, message, None
        return ok, message, attempt.to_dict(
            can_continue=production_handoff_registry.can_continue(attempt),
        )

    def list_handoffs(self, task_id: str | None = None) -> list[dict[str, Any]]:
        """读取交接记录（版本页核对用）：可看哪次可继续及拒绝缘由。"""
        return [
            attempt.to_dict(can_continue=production_handoff_registry.can_continue(attempt))
            for attempt in production_handoff_registry.list_handoffs(task_id)
        ]

    def get_effective_version(self, task_id: str) -> dict[str, Any] | None:
        """获取指定任务的当前生效版本。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("关联任务") == task_id and r.get("status") == "已生效":
                return r
        return None

    def is_referenced(self, version_id: str) -> bool:
        """检查版本是否被字幕包引用。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("版本编号") == version_id:
                return int(r.get("被引用次数", "0")) > 0
        return False

    def increment_ref_count(self, version_id: str) -> tuple[bool, str]:
        """增加版本的被引用次数。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("版本编号") == version_id:
                current = int(r.get("被引用次数", "0"))
                r["被引用次数"] = str(current + 1)
                return True, f"版本 {version_id} 引用次数: {current + 1}"
        return False, f"版本 {version_id} 不存在"

    def get_versions_by_task(self, task_id: str) -> list[dict[str, Any]]:
        """获取指定任务的全部版本记录，按版本号排序。"""
        rows = store.rows(self.MODULE)
        vers = [r for r in rows if r.get("关联任务") == task_id]
        vers.sort(key=lambda r: r.get("版本号", "v0"))
        return vers
