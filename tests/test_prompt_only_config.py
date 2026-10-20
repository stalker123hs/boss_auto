import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import embedded_auto
import resume_matcher.auto_apply as auto_apply


def test_production_uses_only_boss_page_prompts(tmp_path, monkeypatch):
    captured = {}

    def fake_apply(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(auto_apply, "auto_apply_jobs", fake_apply)
    monkeypatch.setattr(embedded_auto, "record_successes", lambda _results: None)
    config = {
        "greeting": "您好",
        "resume_index": 0,
        "min_interval_seconds": 1,
        "max_interval_seconds": 1,
    }
    code = embedded_auto.apply_jobs(
        config,
        [{"link": "https://example.test/job", "公司": "测试公司", "职位": "嵌入式"}],
        mode="production",
        target=0,
        run_dir=tmp_path,
    )

    assert code == 0
    assert captured["manual_pause_thresholds"] == ()
    assert captured["hard_stop_threshold"] is None
