#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""重访最近投递的聊天：能发送附件时发送，受限时记录等待回复。"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import embedded_auto
from DrissionPage import ChromiumOptions, WebPage
from browser_finder import find_chrome_browser
from resume_matcher.auto_apply import send_existing_resume_attachment_status


def main():
    logs = sorted((ROOT / "assets" / "embedded_runs").glob("*/state/apply_log.json"), reverse=True)
    if not logs:
        raise SystemExit("没有投递日志")
    log_data = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in logs]
    pending = {}
    for _path, data in log_data:
        for result in data.get("results", []):
            if result.get("greeting_verified") and result.get("attachment_status") != "sent":
                job = result.get("job") or {}
                if job.get("link"):
                    pending[job["link"]] = job
    co = ChromiumOptions()
    browser = find_chrome_browser()
    if browser:
        co.set_browser_path(browser)
    co.set_argument(f"--user-data-dir={ROOT / 'assets' / 'chrome_user_data'}")
    page = WebPage(chromium_options=co)
    report = []
    try:
        updates = {}
        for job in pending.values():
            page.get(job.get("link"))
            time.sleep(2)
            button = page.ele("xpath://a[contains(normalize-space(.),'继续沟通')]", timeout=2)
            if button:
                button.click()
                time.sleep(2)
            status = send_existing_resume_attachment_status(page, resume_index=0, timeout=8)
            updates[job["link"]] = status
            item = {"公司": job.get("公司"), "附件状态": status}
            report.append(item)
            print(json.dumps(item, ensure_ascii=False))
            time.sleep(3)
    finally:
        page.quit()

    all_results = []
    for log_path, data in log_data:
        for result in data.get("results", []):
            link = (result.get("job") or {}).get("link")
            if link in updates:
                result["status"] = "applied"
                result["attachment_status"] = updates[link]
                result["attachment_sent"] = updates[link] == "sent"
                result["error"] = None
        summary = data.setdefault("summary", {})
        summary["applied"] = sum(1 for r in data.get("results", []) if r.get("status") == "applied")
        summary["partial"] = sum(1 for r in data.get("results", []) if r.get("status") == "partial")
        summary["failed"] = sum(1 for r in data.get("results", []) if r.get("status") in ("error", "no_chat", "no_button"))
        summary["skipped"] = sum(1 for r in data.get("results", []) if r.get("status") == "skipped")
        log_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        all_results.extend(data.get("results", []))
    embedded_auto.record_successes(all_results)
    out = ROOT / "assets" / "attachment_repair_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"修复报告：{out}")


if __name__ == "__main__":
    main()
