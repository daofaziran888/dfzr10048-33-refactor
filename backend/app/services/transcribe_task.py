"""转写任务转换领域服务：对转码完成的分片执行语音转文本转换，产出转写产出数据集供语音分段与置信复核消费。

写入边界：确认产出（produce_transcription）与转写版本的子版本登记共用
``production_handoff_registry``，本服务只在边界临界区内回写任务行，
保证子版本永远绑定到一次已确认产出的指纹，而不是另一份正文。
"""
from __future__ import annotations

from typing import Any

from app.pipeline.production_handoff_registry import (
    production_handoff_registry,
)
from app.services.base import BaseModuleService
from app.store import store


class TranscribeTaskService(BaseModuleService):
    MODULE = "transcribe_task"
    ENTITY = "转写任务"
    OPERATION_TERM = "转换"
    LIST_FIELDS = ["任务编号", "关联分片", "转写引擎", "语言设定", "输出文本行数", "平均置信度", "转写耗时", "任务批次"]
    REQUIRED_FIELDS = ["任务编号", "关联分片", "转写引擎"]
    STATUS_ORDER = ["待转写", "转写中", "已产出", "产出异常"]
    ACTION_RULES = {"启动转写": "转写中", "确认产出": "已产出", "登记异常": "产出异常"}
    NEGATIVE_ACTIONS = []

    def produce_transcription(
        self, task_id: str, line_count: int, avg_confidence: float,
        elapsed_seconds: int,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """记录转写产出：更新文本行数、置信度与产出状态。

        行为与旧实现保持一致：任务不存在时返回 (False, "任务 X 不存在", None)；
        存在时返回 (True, "任务 X 已产出 N 行", 任务行副本)。
        实际写入收敛到共享边界，旧确认产出在引擎更正时保留为带缘由的历史记录。
        """
        rows = store.rows(self.MODULE)
        target = next((r for r in rows if r.get("任务编号") == task_id), None)
        if target is None:
            return False, f"任务 {task_id} 不存在", None

        with production_handoff_registry.locked():
            record = production_handoff_registry.record_production(
                task_id, line_count, avg_confidence, elapsed_seconds,
            )
            target["输出文本行数"] = str(line_count)
            target["平均置信度"] = str(avg_confidence)
            target["转写耗时"] = str(elapsed_seconds)
            target["status"] = "已产出"
            target["pending"] = False
            target["_产出指纹"] = record.digest
            return True, f"任务 {task_id} 已产出 {line_count} 行", dict(target)

    def list_productions(self, task_id: str) -> list[dict[str, Any]]:
        """读取任务的全部确认记录（含被引擎更正的旧确认与拒绝/续办所依据的指纹）。"""
        return [r.to_dict() for r in production_handoff_registry.list_productions(task_id)]

    def current_production(self, task_id: str) -> dict[str, Any] | None:
        """读取任务当前最新确认产出；未确认过时返回 None。"""
        record = production_handoff_registry.current_production(task_id)
        return record.to_dict() if record is not None else None

    def get_by_batch(self, batch_id: str) -> list[dict[str, Any]]:
        """获取指定批次的所有转写任务。"""
        rows = store.rows(self.MODULE)
        return [r for r in rows if r.get("任务批次") == batch_id]

    def batch_avg_confidence(self, batch_id: str) -> float:
        """计算指定批次的平均置信度。"""
        tasks = self.get_by_batch(batch_id)
        confs = [
            float(t.get("平均置信度", "0"))
            for t in tasks
            if t.get("平均置信度") and float(t.get("平均置信度", "0")) > 0
        ]
        return sum(confs) / len(confs) if confs else 0.0

    def get_low_confidence_tasks(self, threshold: float) -> list[dict[str, Any]]:
        """获取低于指定置信度阈值的已产出任务。"""
        rows = store.rows(self.MODULE)
        return [
            r for r in rows
            if r.get("status") == "已产出"
            and float(r.get("平均置信度", "0")) < threshold
        ]
