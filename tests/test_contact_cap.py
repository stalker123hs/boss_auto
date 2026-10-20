import builtins
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import resume_matcher.auto_apply as auto_apply


class _Browser:
    def __init__(self, *args, **kwargs):
        self.visited = []
        self.quit_called = False

    def get(self, url):
        self.visited.append(url)

    def quit(self):
        self.quit_called = True


class _InteractiveStdin:
    def isatty(self):
        return True


class _Button:
    def click(self):
        pass


def test_120_requires_one_human_confirmation_and_150_stops(tmp_path, monkeypatch):
    """Exercise the real loop without launching a browser or sending messages."""
    browser = _Browser()
    answers = []

    monkeypatch.setattr(auto_apply, "HAS_DRISSION", True)
    monkeypatch.setattr(auto_apply, "WebPage", lambda **kwargs: browser)
    monkeypatch.setattr(auto_apply, "_ensure_login", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(auto_apply, "_security_challenge_present", lambda *_args: False)
    monkeypatch.setattr(auto_apply, "_wait_for_security_clear", lambda *_args: True)
    monkeypatch.setattr(auto_apply, "_close_popup", lambda *_args: False)
    monkeypatch.setattr(auto_apply, "_wait_for_platform_prompt_clear", lambda *_args: None)
    monkeypatch.setattr(auto_apply, "_find_element", lambda *_args, **_kwargs: _Button())
    monkeypatch.setattr(auto_apply, "_input_greeting", lambda *_args: True)
    monkeypatch.setattr(auto_apply, "_click_send", lambda *_args: True)
    monkeypatch.setattr(auto_apply.time, "sleep", lambda *_args: None)
    monkeypatch.setattr(auto_apply.random, "uniform", lambda *_args: 0)
    monkeypatch.setattr(auto_apply.sys, "stdin", _InteractiveStdin())

    def answer(_prompt):
        answers.append(_prompt)
        return "Y"

    monkeypatch.setattr(builtins, "input", answer)
    jobs = [
        {"link": f"https://example.test/{n}", "公司": f"公司{n}", "职位": "嵌入式工程师"}
        for n in range(151)
    ]
    greetings = {job["link"]: "您好" for job in jobs}

    results = auto_apply._auto_apply_jobs_impl(
        qualified_jobs=jobs,
        _profile=object(),
        max_applications=0,
        greetings=greetings,
        output_dir=str(tmp_path),
        min_interval=0,
        max_interval=0,
        manual_pause_thresholds=(120,),
        hard_stop_threshold=150,
    )

    assert len(results) == 150
    assert all(row["status"] == "applied" for row in results)
    assert len(answers) == 1
    assert len(browser.visited) == 150
    assert browser.quit_called
    checkpoint = json.loads((tmp_path / "state" / "apply_checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["complete"] is False
    assert checkpoint["next_index"] == 150
