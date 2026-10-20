import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from resume_matcher.auto_apply import (
    ManualPauseCounter,
    ManualPlatformPrompt,
    _close_popup,
    _is_boss_daily_contact_cap,
    _is_platform_warning_text,
    _platform_warning_text,
    _wait_for_platform_prompt_clear,
)


def test_pause_counter_uses_a_120_review_gate_and_a_150_hard_cap():
    counter = ManualPauseCounter((120,))
    assert counter.pending_threshold() is None

    for _ in range(119):
        counter.record({"status": "applied"})
    for _ in range(30):
        counter.record({"status": "skipped"})
    assert counter.pending_threshold() is None

    counter.record({"status": "applied"})
    assert counter.applied == 120
    assert counter.pending_threshold() == 120
    assert not counter.reached(150)
    counter.approve(120)

    for _ in range(29):
        counter.record({"status": "applied"})
    assert counter.pending_threshold() is None
    counter.record({"status": "applied"})
    assert counter.applied == 150
    assert counter.reached(150)

    counter.record({"status": "partial", "greeting_verified": True})
    assert counter.applied == 151

    # A new script launch creates a new counter; old checkpoint results are not loaded into it.
    next_launch = ManualPauseCounter((120,))
    assert next_launch.applied == 0
    assert next_launch.pending_threshold() is None


def test_platform_count_prompts_are_detected_but_benign_dialogs_are_not():
    assert _is_platform_warning_text("温馨提示：您今天已与120位BOSS沟通，还剩30次沟通机会哦")
    assert _is_platform_warning_text("您已达到沟通上限，您今天已与150位BOSS沟通，休息一下，明天再来吧～")
    assert not _is_platform_warning_text("已发送120份简历，是否继续投递？")
    assert not _is_platform_warning_text("本次沟通达到150次，请确认是否继续")
    assert not _is_platform_warning_text("当前已沟通120份")
    assert not _is_platform_warning_text("操作过于频繁，请稍后再试")
    assert not _is_platform_warning_text("欢迎使用BOSS直聘")


def test_platform_prompt_is_never_closed_automatically(monkeypatch):
    class Element:
        clicked = False

        def click(self):
            self.clicked = True

    class Page:
        def __init__(self):
            self.element = Element()

        def run_js(self, _script):
            return "您今天已与120位BOSS沟通，还剩30次沟通机会哦"

        def ele(self, *_args, **_kwargs):
            return self.element

    page = Page()
    try:
        _close_popup(page)
    except ManualPlatformPrompt:
        pass
    else:
        raise AssertionError("platform prompt should pause for a person")
    assert not page.element.clicked


def test_120_warm_tip_is_detected_from_page_text_fallback():
    class Page:
        def run_js(self, _script):
            return json.dumps({
                "overlays": "",
                "pageText": "嵌入式工程师\n温馨提示\n您今天已与120位BOSS沟通，还剩30次沟通机会哦\n好",
            })

    assert "还剩30次沟通机会" in _platform_warning_text(Page())


def test_150_daily_cap_stops_instead_of_waiting_for_confirmation():
    cap_text = "您已达到沟通上限，您今天已与150位BOSS沟通，休息一下，明天再来吧～"
    assert _is_boss_daily_contact_cap(cap_text)

    class Page:
        def run_js(self, _script):
            return json.dumps({"overlays": "", "pageText": cap_text})

    with pytest.raises(ManualPlatformPrompt, match="当日沟通上限"):
        _wait_for_platform_prompt_clear(Page())


def test_job_detail_page_text_never_triggers_a_platform_pause():
    class Page:
        def run_js(self, _script):
            return json.dumps({
                "overlays": "",
                "pageText": "首页 职位 公司 校园 招聘中 嵌入式软件工程师 投递截止时间 2025-12-31 工作时间不限",
            })

    assert _platform_warning_text(Page()) == ""


def test_salary_150_and_continue_chat_never_trigger_a_platform_pause():
    """Regression for a BOSS page root accidentally selected as a tip overlay."""
    class Page:
        def run_js(self, _script):
            return json.dumps({
                "overlays": "首页 职位 公司 校园 招聘中 系统验证实习生（手术机器人） "
                            "150-250元/天 上海 继续沟通 新增附件简历 职位描述",
                "pageText": "",
            })

    assert _platform_warning_text(Page()) == ""
