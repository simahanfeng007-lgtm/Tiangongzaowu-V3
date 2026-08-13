from __future__ import annotations

from v3.gutong.shangxiawen import goujian_system_tishi
from v3.shenti_zhuangtai import ShentiZhuangtai


def test_daily_plan_progress_uses_authoritative_life_activity_ledger() -> None:
    prompt = goujian_system_tishi(ShentiZhuangtai(), "test soul")

    assert "日常计划" in prompt
    assert "life.activity.query" in prompt
    assert "relative_day=today/yesterday" in prompt
    assert "不要用文件搜索猜测计划" in prompt
    assert "不要用 life.body.state.query 代替活动台账" in prompt
