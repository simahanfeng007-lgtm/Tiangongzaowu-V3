#!/usr/bin/env python3
"""Build and freeze the P11 formal-matrix task inputs (R1G pre-freeze).

Constructs 200 semantically distinct tasks (CORE 80 + LONG_TAIL 120) and
40 fault cases across the frozen twelve fault kinds, deterministically
from a category×object×requirement combinatorial space — every task has a
unique input digest and goal fingerprint by construction (no template
clones). This freezes the INPUT side of the formal matrix only: the
observations, model dimensions and metrics need the real four-model
credentials (R03) and remain BLOCKED; this file never claims acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs/p11-matrix/TASK_MATRIX_FROZEN_2026-09-20.json"
SCHEMA = "tiangong.p11.task-matrix-freeze.v1"

# Deterministic combinatorial space: every (category, object, requirement)
# triple is one semantically independent task — no template clones.
_CATEGORIES = (
    ("read", "读取并总结", 10),
    ("search", "在指定目录中查找", 10),
    ("convert", "把文件转换格式为", 9),
    ("write", "按要点创建文档", 10),
    ("inspect", "检查并报告质量", 9),
    ("organize", "整理归类归档", 8),
    ("compare", "对比两份内容并列出差异", 7),
    ("verify", "核验数据一致性", 7),
    ("extract", "从中提取关键信息", 8),
    ("summarize_thread", "梳理会话或记录时间线", 6),
)
_LONGTAIL_CATEGORIES = (
    ("niche_read", "读取少见的配置并解释含义", 8),
    ("multi_step", "分步骤完成组合任务", 9),
    ("repair", "发现并修复问题", 8),
    ("batch", "批量重命名或移动", 8),
    ("cross_check", "跨两个来源交叉核对", 8),
    ("archive", "打包归档并生成清单", 8),
    ("diff_review", "审阅变更并给出评审意见", 8),
    ("structure", "为散乱内容设计结构", 8),
    ("timeline", "重建事件先后顺序", 7),
    ("inventory", "盘点目录生成统计", 8),
    ("format_fix", "纠正格式问题", 7),
    ("guided_draft", "按规范起草草稿", 7),
    ("annotate", "添加批注或标记", 7),
    ("migrate_layout", "迁移到新布局", 8),
    ("decompose", "把大任务拆成子任务清单", 5),
    ("sanitize", "清理敏感信息后输出", 6),
)
_OBJECTS = (
    "季度财务汇总表", "项目周报草稿", "客户来信记录", "产品需求清单",
    "会议纪要初稿", "代码审查意见", "市场调研笔记", "设备巡检日志",
    "合同扫描件目录", "设计稿导出文件", "培训材料讲义", "供应商报价单",
    "工单处理记录", "实验数据表格", "活动策划方案", "人事入职材料",
    "运维告警快照", "课程作业集", "旅行行程单", "图书借阅清单",
    "库存盘点表", "问卷回收结果", "竞品分析报告", "维修保养记录",
    "稿件投稿包", "验收测试记录", "访谈整理稿", "白皮书草稿",
    "发布说明清单", "数据字典片段",
)
_REQUIREMENTS = (
    "按日期分组", "突出异常项", "生成双列对照", "附检查结论",
    "保留原始编号", "输出为简洁要点", "标注缺失字段", "按优先级排序",
    "给出下一步建议", "合并重复条目",
)
_FAULT_KINDS = (
    "source_revision_drift", "tool_unavailable", "misleading_experience",
    "permission_denied", "provider_unavailable", "schema_mismatch",
    "stale_manifest", "workspace_drift", "ambiguous_effect",
    "verifier_unavailable", "context_truncation", "interrupted_run",
)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _goal_of(task_text: str) -> str:
    return hashlib.sha256(
        ("goal:" + task_text).encode("utf-8")).hexdigest()


def _build_tasks() -> list[dict]:
    tasks = []
    serial = 0

    def emit(cohort: str, categories, target: int) -> None:
        nonlocal serial
        produced = 0
        for category, verb, weight in categories:
            for _ in range(weight):
                if produced >= target:
                    return
                obj = _OBJECTS[serial % len(_OBJECTS)]
                req = _REQUIREMENTS[(serial // len(_OBJECTS))
                                    % len(_REQUIREMENTS)]
                variant = serial // (len(_OBJECTS) * len(_REQUIREMENTS))
                subject = obj if variant == 0 else f"{obj}（第{variant + 1}批）"
                text = f"{verb}{subject}，{req}。"
                serial += 1
                produced += 1
                tasks.append({
                    "task_id": f"p11t_{serial:04d}",
                    "cohort": cohort,
                    "prompt": text,
                    "category": category,
                    "task_input_sha256": _digest(text),
                    "goal_fingerprint_sha256": _goal_of(text),
                    "acceptance_profile": {
                        "must_deliver": subject,
                        "must_satisfy": req,
                        "must_not_fabricate": True,
                    },
                    "acceptance_profile_sha256": _digest(
                        json.dumps({
                            "must_deliver": subject, "must_satisfy": req,
                            "must_not_fabricate": True},
                            ensure_ascii=False, sort_keys=True)),
                    "active_path": "DYNAMIC" if serial % 2 == 1 else "STATIC",
                    "active_model_profile_note":
                        "assigned at execution time from the four frozen "
                        "provider/model/revision profiles (R03)",
                })

    emit("CORE", _CATEGORIES, 80)
    emit("LONG_TAIL", _LONGTAIL_CATEGORIES, 120)
    return tasks


def _build_faults(tasks: list[dict]) -> list[dict]:
    faults = []
    per_kind, extra = divmod(40, len(_FAULT_KINDS))
    counts = {kind: per_kind + (1 if index < extra else 0)
              for index, kind in enumerate(_FAULT_KINDS)}
    cursor = 0
    for kind in _FAULT_KINDS:
        for _ in range(counts[kind]):
            host = tasks[cursor % len(tasks)]
            cursor += 1
            faults.append({
                "fault_case_id": f"p11f_{len(faults) + 1:03d}",
                "host_task_id": host["task_id"],
                "fault_kind": kind,
                "injection_point": "pre_registration"
                if "source" in kind or "manifest" in kind
                else "pre_dispatch" if "permission" in kind
                else "in_flight" if "ambiguous" in kind or "interrupted" in kind
                else "model_boundary",
                "expected_containment": "fail_closed_no_partial_effect",
            })
    return faults


def build() -> dict:
    tasks = _build_tasks()
    faults = _build_faults(tasks)
    core = sum(1 for t in tasks if t["cohort"] == "CORE")
    long_tail = len(tasks) - core
    return {
        "schema": SCHEMA,
        "summary": {
            "tasks_total": len(tasks), "core": core, "long_tail": long_tail,
            "fault_cases": len(faults),
            "fault_kinds": len(_FAULT_KINDS),
            "unique_input_digests": len({t["task_input_sha256"]
                                         for t in tasks}),
            "unique_goal_fingerprints": len({t["goal_fingerprint_sha256"]
                                             for t in tasks}),
        },
        "tasks": tasks,
        "fault_cases": faults,
        "honesty": (
            "This freezes the INPUT side of the P11 formal matrix only: "
            "200 tasks and 40 fault scenarios, deterministically built with "
            "unique digests. Observations, model dimensions, metrics and the "
            "formal verdict require the four distinct provider/model/revision "
            "credentials (R03), source sign-off (R04) and independent review "
            "(R06). This file is NOT acceptance and never becomes one by "
            "itself."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    matrix = build()
    rendered = json.dumps(matrix, ensure_ascii=False, indent=1) + "\n"
    if args.check:
        current = args.output.read_text(encoding="utf-8") \
            if args.output.is_file() else ""
        if current != rendered:
            print("task matrix drift: rebuild required", file=sys.stderr)
            return 1
        s = matrix["summary"]
        print(f"task matrix ok: {s['tasks_total']} tasks "
              f"({s['core']}+{s['long_tail']}), {s['fault_cases']} faults")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"task matrix written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
