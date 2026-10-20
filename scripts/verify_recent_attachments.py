#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""只读回查最近一轮每个聊天中是否存在附件简历卡片。"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from DrissionPage import ChromiumOptions, WebPage
from browser_finder import find_chrome_browser
from resume_matcher.auto_apply import _resume_message_count


def main():
    logs = sorted((ROOT / "assets" / "embedded_runs").glob("*/state/apply_log.json"), reverse=True)
    if not logs:
        raise SystemExit("没有投递日志")
    data = json.loads(logs[0].read_text(encoding="utf-8"))
    co = ChromiumOptions()
    browser = find_chrome_browser()
    if browser:
        co.set_browser_path(browser)
    co.set_argument(f"--user-data-dir={ROOT / 'assets' / 'chrome_user_data'}")
    page = WebPage(chromium_options=co)
    report = []
    try:
        for result in data.get("results", []):
            job = result.get("job") or {}
            page.get(job.get("link"))
            time.sleep(2)
            button = page.ele("xpath://a[contains(normalize-space(.),'继续沟通')]", timeout=2)
            if button:
                button.click()
                time.sleep(2)
            count = _resume_message_count(page)
            item = {"公司": job.get("公司"), "职位": job.get("职位"), "status": result.get("status"), "附件卡片数": count}
            report.append(item)
            print(json.dumps(item, ensure_ascii=False))
    finally:
        page.quit()
    out = ROOT / "assets" / "recent_attachment_verification.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果：{out}")


if __name__ == "__main__":
    main()
