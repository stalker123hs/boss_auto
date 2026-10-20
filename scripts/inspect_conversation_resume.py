#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""读取最近一轮第一家聊天中与简历有关的可见节点。"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from DrissionPage import ChromiumOptions, WebPage
from browser_finder import find_chrome_browser

log = sorted((ROOT / "assets" / "embedded_runs").glob("*/state/apply_log.json"), reverse=True)[0]
job = json.loads(log.read_text(encoding="utf-8"))["results"][0]["job"]
co = ChromiumOptions()
browser = find_chrome_browser()
if browser:
    co.set_browser_path(browser)
co.set_argument(f"--user-data-dir={ROOT / 'assets' / 'chrome_user_data'}")
page = WebPage(chromium_options=co)
try:
    page.get(job["link"])
    time.sleep(2)
    btn = page.ele("xpath://a[contains(normalize-space(.),'继续沟通')]", timeout=2)
    if btn:
        btn.click(); time.sleep(2)
    result = page.run_js("""
      const visible = (el) => { const s=getComputedStyle(el),r=el.getBoundingClientRect();
        return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0; };
      const nodes=Array.from(document.querySelectorAll('*')).filter((el)=>
        visible(el) && (el.innerText||'').includes('简历') &&
        !Array.from(el.children).some((c)=>(c.innerText||'').includes('简历'))
      ).map((el)=>({tag:el.tagName,text:(el.innerText||'').trim(),cls:String(el.className||''),
                    parent:String(el.parentElement?.className||''),html:el.outerHTML.slice(0,1200)}));
      const roots=Array.from(document.querySelectorAll('[class*="message"],.chat-record')).filter(visible)
        .map((el)=>({cls:String(el.className||''),text:(el.innerText||'').trim().slice(-2000)})).slice(-30);
      return {nodes,roots};
    """)
    out = ROOT / "assets" / "conversation_resume_diagnostic.json"
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    print(out)
finally:
    page.quit()
