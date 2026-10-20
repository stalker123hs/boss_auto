#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""打开最近一条部分成功岗位，只读取聊天工具栏结构。"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from DrissionPage import ChromiumOptions, WebPage
from browser_finder import find_chrome_browser


def latest_partial():
    logs = sorted((ROOT / "assets" / "embedded_runs").glob("*/state/apply_log.json"), reverse=True)
    for path in logs:
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in reversed(data.get("results", [])):
            if item.get("status") == "partial":
                return item, path
    raise SystemExit("没有部分成功记录")


def main():
    item, log_path = latest_partial()
    link = (item.get("job") or {}).get("link")
    print(f"诊断记录：{log_path}")
    print(f"岗位链接：{link}")
    co = ChromiumOptions()
    browser = find_chrome_browser()
    if browser:
        co.set_browser_path(browser)
    co.set_argument(f"--user-data-dir={ROOT / 'assets' / 'chrome_user_data'}")
    page = WebPage(chromium_options=co)
    try:
        page.get(link)
        time.sleep(3)
        for selector in [
            "xpath://a[contains(normalize-space(.),'继续沟通')]",
            "xpath://a[contains(normalize-space(.),'立即沟通')]",
            "css:.btn-startchat",
        ]:
            element = page.ele(selector, timeout=1)
            if element:
                text = (element.text or "").strip()
                print(f"打开聊天：{text or selector}")
                element.click()
                time.sleep(3)
                break
        data = page.run_js("""
            return Array.from(document.querySelectorAll('button,a,div,span,i')).map((el) => ({
              tag: el.tagName,
              text: (el.innerText || '').trim().slice(0, 80),
              title: el.getAttribute('title') || '',
              aria: el.getAttribute('aria-label') || '',
              ka: el.getAttribute('ka') || '',
              cls: String(el.className || '').slice(0, 160)
            })).filter((x) => {
              const s = (x.text + ' ' + x.title + ' ' + x.aria + ' ' + x.ka + ' ' + x.cls).toLowerCase();
              return s.includes('简历') || s.includes('resume') || s.includes('toolbar');
            }).slice(0, 120);
        """) or []
        out = ROOT / "assets" / "resume_toolbar_diagnostic.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"找到 {len(data)} 个相关元素，已保存：{out}")
        for row in data[:30]:
            print(json.dumps(row, ensure_ascii=False))

        resume_btn = page.ele(
            "xpath://div[contains(@class,'toolbar-btn') and normalize-space(.)='发简历']",
            timeout=2,
        )
        if resume_btn:
            print("找到可见的发简历按钮，打开选择框做结构诊断（不确认发送）")
            resume_btn.click()
            time.sleep(2)
        detail = page.run_js("""
            const visible = (el) => {
              if (!el) return false;
              const s = getComputedStyle(el), r = el.getBoundingClientRect();
              return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
            };
            const dialog = Array.from(document.querySelectorAll(
              '.upload-resume-dialog,.panel-resume,[class*="resume-dialog"],[class*="resume-panel"]'
            )).find(visible);
            const exact = Array.from(document.querySelectorAll('*')).filter((el) =>
              visible(el) && ['发简历','上传附件简历','没有附件简历','点击预览附件简历']
                .some((t) => (el.innerText || '').trim() === t)
            ).map((el) => ({tag:el.tagName, text:(el.innerText||'').trim(), cls:String(el.className||''),
                            title:el.getAttribute('title')||'', aria:el.getAttribute('aria-label')||''}));
            const files = Array.from(document.querySelectorAll('input[type="file"]')).map((el) => ({
              accept:el.accept || '', cls:String(el.className||''), visible:visible(el)
            }));
            return {url:location.href, dialogText:dialog ? dialog.innerText : '',
                    dialogHtml:dialog ? dialog.outerHTML.slice(0,20000) : '', exact, files};
        """)
        detail_out = ROOT / "assets" / "resume_dialog_diagnostic.json"
        detail_out.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in detail.items() if k != 'dialogHtml'}, ensure_ascii=False, indent=2))
        print(f"弹窗结构已保存：{detail_out}")
    finally:
        page.quit()


if __name__ == "__main__":
    main()
