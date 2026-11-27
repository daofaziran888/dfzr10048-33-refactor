"""产出确认与子版本交接共享写入边界的领域测试。

覆盖主场景：
- 任务 A 先确认 4 行，登记子版本 V2 时中断（只开放待续办交接，版本行不落库）；
- 旧单被接手后引擎更正为 6 行，旧确认保留为带缘由的历史记录；
- 他人依据同一父版本登记另一子版；
- 回任务页续办时指纹不一致被拒绝并留缘由，任何版本行都不错绑另一份正文；
- 页面可查哪次可以继续、拒绝缘由是什么；
- 父版本与已发字幕包仍按原字句读取；
- 正常任务确认一次并比对旧回执字段（行数/置信度/耗时/不存在行为）。
"""
from __future__ import annotations

import pytest

from app.pipeline.production_handoff_registry import (
    ProductionHandoffRegistry,
    production_digest,
    production_handoff_registry,
)
from app.services.transcribe_task import TranscribeTaskService
from app.services.transcript_version import TranscriptVersionService
from app.store import store

TASK_A = "TRCB-HND-A"
TASK_B = "TRCB-HND-B"
TASK_MISSING = "TRCB-HND-NONE"
PARENT_V1 = "v1"
_HANDOFF_TASKS = (TASK_A, TASK_B)
_HANDOFF_VERSION_IDS = (
    "TV-A-V2", "TV-A-V2-OTHER", "TV-A-REJ", "TV-A-OK",
    "TV-A-BAD", "TV-A-DUP", "TV-SINGLE", "TV-B-V2", "TV-LEGACY",
)


def _seed_tasks() -> None:
    rows = store.rows("transcribe_task")
    existing = {r.get("任务编号") for r in rows}
    for task_id in _HANDOFF_TASKS:
        if task_id in existing:
            continue
        rows.append({
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "status": "待转写", "pending": True, "abnormal": False,
            "任务编号": task_id, "关联分片": f"SHRD-{task_id[-1:]}",
            "转写引擎": "WhisperASR-Large-v3", "语言设定": "zh-CN",
            "输出文本行数": "0", "平均置信度": "0.0",
            "转写耗时": "0", "任务批次": "BATCH-HANDOFF",
        })


@pytest.fixture(autouse=True)
def _reset_handoff_boundary() -> None:
    """每个用例前清空共享边界与本模块写入的任务/版本行，避免单例残留串用例。"""
    production_handoff_registry._records.clear()
    production_handoff_registry._current.clear()
    production_handoff_registry._handoffs.clear()
    task_rows = store.rows("transcribe_task")
    for row in [r for r in task_rows if r.get("任务编号") in _HANDOFF_TASKS]:
        row["输出文本行数"] = "0"
        row["平均置信度"] = "0.0"
        row["转写耗时"] = "0"
        row["status"] = "待转写"
        row["pending"] = True
        row.pop("_产出指纹", None)
    version_rows = store.rows("transcript_version")
    for row in [r for r in version_rows if r.get("版本编号") in _HANDOFF_VERSION_IDS]:
        version_rows.remove(row)
    yield
    production_handoff_registry._records.clear()
    production_handoff_registry._current.clear()
    production_handoff_registry._handoffs.clear()


class TestProductionConfirmationCompatibility:
    """旧回执字段与任务不存在行为必须原样保留。"""

    def setup_method(self) -> None:
        _seed_tasks()
        self.task_svc = TranscribeTaskService()

    def test_normal_confirm_keeps_legacy_receipt_fields(self):
        ok, msg, entry = self.task_svc.produce_transcription(TASK_B, 7, 0.91, 210)
        assert ok is True
        assert msg == f"任务 {TASK_B} 已产出 7 行"
        # 旧回执字段：行数、平均置信度、耗时逐字保留
        assert entry["输出文本行数"] == "7"
        assert entry["平均置信度"] == "0.91"
        assert entry["转写耗时"] == "210"
        assert entry["status"] == "已产出"
        assert entry["pending"] is False

    def test_confirm_missing_task_keeps_legacy_behavior(self):
        ok, msg, entry = self.task_svc.produce_transcription(TASK_MISSING, 3, 0.8, 60)
        assert ok is False
        assert entry is None
        assert msg == f"任务 {TASK_MISSING} 不存在"

    def test_digest_stable_for_same_body_and_changes_with_body(self):
        d1 = production_digest("T", 4, 0.82, 120)
        d2 = production_digest("T", 4, 0.82, 120)
        d3 = production_digest("T", 6, 0.95, 140)
        assert d1 == d2
        assert d1 != d3
        assert len(d1) == 12

    def test_engine_correction_marks_old_confirmation_keeps_reason(self):
        self.task_svc.produce_transcription(TASK_B, 4, 0.82, 120)
        self.task_svc.produce_transcription(TASK_B, 6, 0.95, 140)
        records = self.task_svc.list_productions(TASK_B)
        assert [r["确认状态"] for r in records] == ["已更正", "已确认"]
        old = records[0]
        assert "引擎重跑更正" in old["更正原因"]
        assert "4→6" in old["更正原因"]
        current = self.task_svc.current_production(TASK_B)
        assert current["输出文本行数"] == "6"
        assert current["确认状态"] == "已确认"


class TestHandoffScenarioTaskA:
    """任务 A：确认 4 行 → V2 中断 → 引擎更正 6 行 → 他人并行登记 → 续办被拒。"""

    def setup_method(self) -> None:
        _seed_tasks()
        self.task_svc = TranscribeTaskService()
        self.version_svc = TranscriptVersionService()

    def _versions_of(self, task_id: str) -> list[dict]:
        return [
            r for r in store.rows("transcript_version")
            if r.get("关联任务") == task_id
        ]

    def test_full_interrupt_correction_parallel_and_reject_flow(self):
        # 1) 任务 A 确认 4 行
        ok, _, _ = self.task_svc.produce_transcription(TASK_A, 4, 0.82, 120)
        assert ok
        digest4 = self.task_svc.current_production(TASK_A)["产出指纹"]

        # 2) 写子版本 V2 时中断：只开放待续办交接，版本行不落库
        entry, msg = self.version_svc.open_version_handoff(
            "TV-A-V2", TASK_A, PARENT_V1, PARENT_V1, 4, "中断前登记的子版", digest4,
        )
        assert entry is not None
        handoff_id = entry["交接编号"]
        assert entry["交接状态"] == "待续办"
        assert entry["可续办"] == "是"
        assert self._versions_of(TASK_A) == []

        # 3) 旧单被接手，引擎更正为 6 行
        ok, msg6, row = self.task_svc.produce_transcription(TASK_A, 6, 0.95, 140)
        assert ok
        assert msg6 == f"任务 {TASK_A} 已产出 6 行"
        assert row["输出文本行数"] == "6"
        digest6 = self.task_svc.current_production(TASK_A)["产出指纹"]
        assert digest6 != digest4

        # 中断交接此刻已不可续办，页面可直接看到
        listed = self.version_svc.list_handoffs(TASK_A)
        assert listed[0]["可续办"] == "否"

        # 4) 另有人依据同一父版本登记子版（基于新正文）
        other, _ = self.version_svc.open_version_handoff(
            "TV-A-V2-OTHER", TASK_A, "v2", PARENT_V1, 6, "他人依据同一父版本登记", digest6,
        )
        assert other is not None
        other_id = other["交接编号"]

        # 两条交接共存，各自指纹不同、互不串绑
        handoffs = self.version_svc.list_handoffs(TASK_A)
        assert len(handoffs) == 2
        assert {h["产出指纹"] for h in handoffs} == {digest4, digest6}
        assert all(h["父版本号"] == PARENT_V1 for h in handoffs)

        # 5) 回任务页续办旧交接：指纹不一致必须拒绝，且版本行不得落库
        ok, reason, committed = self.version_svc.resume_version_handoff(handoff_id)
        assert ok is False
        assert committed is None
        assert "产出指纹不一致" in reason
        assert digest4 in reason and digest6 in reason
        assert self._versions_of(TASK_A) == []

        # 拒绝缘由在版本页可查，且仍是待续办（用户可处理而非被静默丢弃）
        rejected = next(h for h in self.version_svc.list_handoffs(TASK_A)
                        if h["交接编号"] == handoff_id)
        assert rejected["交接状态"] == "待续办"
        assert rejected["拒绝次数"] == "1"
        assert rejected["拒绝缘由"] == reason
        assert rejected["可续办"] == "否"

        # 6) 他人的交接依据当前正文，续办成功并落库，不与旧正文错绑
        ok, msg, entry = self.version_svc.resume_version_handoff(other_id)
        assert ok
        assert "已续办并落库" in msg
        versions = self._versions_of(TASK_A)
        assert len(versions) == 1
        assert versions[0]["版本编号"] == "TV-A-V2-OTHER"
        assert versions[0]["_产出指纹"] == digest6

        # 7) 旧交接再续办仍被拒（版本号已占用 + 指纹不符），版本行依旧只有一条
        ok, reason2, _ = self.version_svc.resume_version_handoff(handoff_id)
        assert ok is False
        assert reason2 != ""
        assert len(self._versions_of(TASK_A)) == 1

        # 已落库交接不可再续办
        ok, reason3, _ = self.version_svc.resume_version_handoff(other_id)
        assert ok is False
        assert "不可续办" in reason3

    def test_explicit_reject_closes_handoff_with_reason(self):
        self.task_svc.produce_transcription(TASK_A, 4, 0.82, 120)
        digest = self.task_svc.current_production(TASK_A)["产出指纹"]
        entry, _ = self.version_svc.open_version_handoff(
            "TV-A-REJ", TASK_A, "v8", PARENT_V1, 4, "待拒绝", digest,
        )
        handoff_id = entry["交接编号"]
        ok, msg, view = self.version_svc.reject_version_handoff(handoff_id, "正文已过期，废弃该交接")
        assert ok
        assert view["交接状态"] == "已拒绝"
        assert "正文已过期" in view["拒绝缘由"]
        # 拒绝不落版本行，且拒绝后不能再续办
        assert self._versions_of(TASK_A) == []
        ok2, _, _ = self.version_svc.resume_version_handoff(handoff_id)
        assert ok2 is False

    def test_resume_with_matching_digest_commits_once(self):
        self.task_svc.produce_transcription(TASK_A, 4, 0.82, 120)
        digest = self.task_svc.current_production(TASK_A)["产出指纹"]
        entry, _ = self.version_svc.open_version_handoff(
            "TV-A-OK", TASK_A, "v5", PARENT_V1, 4, "正常续办", digest,
        )
        ok, msg, committed = self.version_svc.resume_version_handoff(
            entry["交接编号"], digest,
        )
        assert ok
        assert committed["版本编号"] == "TV-A-OK"
        assert committed["关联任务"] == TASK_A
        assert committed["父版本号"] == PARENT_V1
        # 落库行字句保持旧字段
        assert committed["生效时间"] == "-"
        assert committed["被引用次数"] == "0"
        assert committed["status"] == "草稿"

    def test_open_handoff_requires_confirmed_digest(self):
        self.task_svc.produce_transcription(TASK_A, 4, 0.82, 120)
        ok_entry, message = self.version_svc.open_version_handoff(
            "TV-A-BAD", TASK_A, "v6", PARENT_V1, 4, "未确认指纹", "deadbeefdead",
        )
        assert ok_entry is None
        assert "未确认正文" in message
        assert self._versions_of(TASK_A) == []

    def test_duplicate_version_id_rejected_at_boundary(self):
        self.task_svc.produce_transcription(TASK_A, 4, 0.82, 120)
        digest = self.task_svc.current_production(TASK_A)["产出指纹"]
        first, _ = self.version_svc.open_version_handoff(
            "TV-A-DUP", TASK_A, "v7", PARENT_V1, 4, "首次", digest,
        )
        assert first is not None
        second, message = self.version_svc.open_version_handoff(
            "TV-A-DUP", TASK_A, "v7", PARENT_V1, 4, "重复", digest,
        )
        assert second is None
        assert "已存在" in message


class TestRegistryBoundaries:
    """登记处自身规则：锁、单例、快照。"""

    def test_registry_correction_and_handoff_lifecycle(self):
        reg = ProductionHandoffRegistry()
        rec1 = reg.record_production("T1", 4, 0.8, 100)
        rec2 = reg.record_production("T1", 6, 0.9, 120)
        assert rec1.status == "已更正"
        assert rec2.status == "已确认"
        assert reg.current_production("T1").digest == rec2.digest

        attempt, _ = reg.open_handoff(
            "T1", "V-1", "v2", "v1", 4, "s", rec1.digest,
        )
        assert reg.can_continue(attempt) is False
        ok, reason, _, entry = reg.resume_handoff(attempt.handoff_id, None, lambda h: {})
        assert ok is False and entry is None
        assert "产出指纹不一致" in reason

    def test_unknown_handoff_resume_and_reject(self):
        reg = ProductionHandoffRegistry()
        ok, msg, attempt, entry = reg.resume_handoff("HD-9999", None, lambda h: {})
        assert ok is False and attempt is None and entry is None
        assert "不存在" in msg
        ok2, msg2, attempt2 = reg.reject_handoff("HD-9999", "x")
        assert ok2 is False and attempt2 is None

    def test_singleton_registry_shared_by_both_services(self):
        from app.pipeline.production_handoff_registry import (
            production_handoff_registry as singleton,
        )
        from app.services import service_for
        _seed_tasks()
        svc = TranscribeTaskService()
        svc.produce_transcription(TASK_A, 4, 0.82, 120)
        digest = svc.current_production(TASK_A)["产出指纹"]
        version_svc = service_for("transcript_version")
        version_svc.open_version_handoff(
            "TV-SINGLE", TASK_A, "v9", "v1", 4, "单例边界", digest,
        )
        snap = singleton.snapshot()
        assert TASK_A in snap["productions"]
        assert any(h["版本编号"] == "TV-SINGLE" for h in snap["handoffs"])

    def test_locked_context_serializes_check_and_write(self):
        reg = ProductionHandoffRegistry()
        with reg.locked():
            rec = reg.record_production("T2", 1, 0.5, 10)
        assert rec.digest

    def test_same_body_reconfirm_is_idempotent(self):
        reg = ProductionHandoffRegistry()
        first = reg.record_production("T3", 5, 0.9, 90)
        second = reg.record_production("T3", 5, 0.9, 90)
        assert first is second
        assert len(reg.list_productions("T3")) == 1


class TestParentVersionAndPackRemainReadable:
    """父版本及其已发字幕包仍按以前的字句读取，重构不改写旧数据。"""

    def test_seed_parent_version_and_pack_fields_unchanged(self):
        version_rows = store.rows("transcript_version")
        parent = next(r for r in version_rows if r["版本编号"] == "TRSV-0001")
        assert parent["版本号"] == "v1"
        assert parent["父版本号"] == "-"
        assert parent["变更行数"] == "45"
        assert parent["变更摘要"] == "初始转写产出"
        assert parent["status"] == "草稿"

        packs = store.rows("subtitle_pack")
        assert packs, "种子字幕包不应为空"
        before = {p["字幕包编号"]: dict(p) for p in packs}

        # 交接流程跑完后，已发字幕包字段一字节不变
        _seed_tasks()
        task_svc = TranscribeTaskService()
        version_svc = TranscriptVersionService()
        task_svc.produce_transcription(TASK_B, 4, 0.8, 80)
        digest = task_svc.current_production(TASK_B)["产出指纹"]
        entry, _ = version_svc.open_version_handoff(
            "TV-B-V2", TASK_B, "v2", "v1", 4, "不影响字幕包", digest,
        )
        version_svc.resume_version_handoff(entry["交接编号"])
        for pack_id, old in before.items():
            now = next(p for p in store.rows("subtitle_pack") if p["字幕包编号"] == pack_id)
            assert now == old

    def test_legacy_create_version_signature_and_duplicate_rule_unchanged(self):
        svc = TranscriptVersionService()
        entry, msg = svc.create_version(
            "TV-LEGACY", TASK_B, "v99", "v1", 5, "旧入口登记",
        )
        assert entry is not None and entry["版本号"] == "v99"
        dup, msg2 = svc.create_version(
            "TV-LEGACY", TASK_B, "v100", "v1", 5, "重复编号",
        )
        assert dup is None
        assert msg2 == "版本编号 TV-LEGACY 已存在"
