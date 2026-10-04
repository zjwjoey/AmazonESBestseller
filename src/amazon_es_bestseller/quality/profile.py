"""Human-readable execution profiles."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROFILE_NAME = "stable-research"


def profile_path() -> Path:
    return Path(__file__).resolve().parents[3] / "configs" / "profiles" / "stable_research.json"


def load_stable_research_profile() -> dict[str, Any]:
    path = profile_path()
    return json.loads(path.read_text(encoding="utf-8"))


def execution_plan_text() -> str:
    return "\n".join([
        "================================",
        "本次运行路线",
        "================================",
        "运行模式：稳定研究版",
        "榜单采集：V1",
        "详情采集：V1",
        "质量审查：开启",
        "榜单身份审查：开启",
        "排名完整性审查：开启",
        "访问证据审查：开启",
        "详情身份审查：开启",
        "离线重放：开启",
        "详情结构审查：开启",
        "字段闭环审查：开启",
        "类目来源审查：开启",
        "ACP补全：关闭",
        "Ranking V2采集：关闭",
        "Detail V2采集：关闭",
        "翻译：否",
        "Excel导出：否/根据后续阶段",
        "================================",
    ])
