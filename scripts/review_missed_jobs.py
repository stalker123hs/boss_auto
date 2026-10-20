# coding: utf-8
from __future__ import annotations
import argparse, csv, json, re, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
import embedded_auto as app

def _read_json(path: Path) -> Any:
    try: return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError): return None

def load_history(ledger_path: Path, runs_dir: Path):
    contacted_links, contacted_companies, latest = set(), set(), {}
    if ledger_path.exists():
        with ledger_path.open("r", encoding="utf-8-sig", newline="") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                link = str(row.get("link", "")).strip()
                company = str(row.get("company", "")).strip()
                if link:
                    contacted_links.add(link)
                if company:
                    contacted_companies.add(company)
    files = []
    if runs_dir.exists():
        files = list(runs_dir.rglob("apply_log.json")) + list(runs_dir.rglob("apply_checkpoint.json"))
        files.sort(key=lambda path: path.stat().st_mtime)
    for path in files:
        data = _read_json(path)
        rows = data.get("results", []) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            job = row.get("job") if isinstance(row.get("job"), dict) else row
            link = str(job.get("link", row.get("link", ""))).strip()
            if not link:
                continue
            status = str(row.get("status", row.get("result", ""))).lower()
            if status in {"applied", "success", "sent"} or row.get("greeting_verified") is True:
                contacted_links.add(link)
            latest[link] = row
    return contacted_links, contacted_companies, latest


def _get(row, *names):
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip(): return str(value).strip()
    return ""


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "是", "已失效"}


def classify_job(row):
    """Return a conservative recommendation for a planned-but-unconfirmed job."""
    error = str(row.get("_previous_error", ""))
    status = str(row.get("_previous_status", "")).lower()
    closed_words = ("职位已关闭", "职位已失效", "岗位已关闭", "岗位已失效", "职位不存在", "岗位不存在", "已下架")
    ambiguous_words = ("可能已经沟通过或岗位失效", "可能已投递或聊天窗口未开启")
    if _truthy(row.get("已失效")) or any(word in error for word in closed_words):
        return "已确认关闭", "不投递"
    if any(word in error for word in ambiguous_words):
        return "疑似关闭或已沟通", "人工核查"
    if status in {"no_chat", "skipped"}:
        return "未确认可沟通", "人工核查"
    return "未确认发出", "可重新尝试"

def _matches(row, keywords):
    names=(chr(0x804c)+chr(0x4f4d),chr(0x6280)+chr(0x80fd)+chr(0x6807)+chr(0x7b7e),chr(0x5c97)+chr(0x4f4d)+chr(0x8981)+chr(0x6c42)+chr(0x548c)+chr(0x804c)+chr(0x8d23))
    haystack=" ".join(str(row.get(k,"")) for k in (names[0],"position","title",names[1],names[2]))
    return [kw for kw in keywords if kw.casefold() in haystack.casefold()]

def collect_missed_jobs(config, post_data: Path, ledger_path: Path, runs_dir: Path):
    # Only jobs persisted in a production candidate_plan are in scope.
    # Raw crawl CSVs and plan-only/test runs are deliberately ignored.
    contacted_links, _, history = load_history(ledger_path, runs_dir)
    jobs_by_link, sources, excluded = {}, defaultdict(set), defaultdict(int)
    plans = sorted(runs_dir.rglob("candidate_plan.json")) if runs_dir.exists() else []
    production_plans = []
    for plan_path in plans:
        plan = _read_json(plan_path)
        if not isinstance(plan, dict) or str(plan.get("mode", "")).lower() != "production":
            excluded["non_production_plan"] += 1
            continue
        production_plans.append(plan_path)
        rows = plan.get("jobs", [])
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            link = str(row.get("link", "")).strip()
            if not link:
                excluded["missing_link"] += 1
                continue
            if link in contacted_links:
                excluded["confirmed_contact"] += 1
                continue
            previous = history.get(link, {})
            row["_previous_status"] = str(previous.get("status", previous.get("result", "")))
            row["_previous_error"] = str(previous.get("error", previous.get("message", "")))
            row["_run"] = plan_path.parent.parent.name
            row["_matched_keywords"] = row.get("matched_keywords", [])
            row["_source"] = plan_path.parent.parent.name
            row["_state"], row["_recommendation"] = classify_job(row)
            jobs_by_link.setdefault(link, row)
            sources[link].add(plan_path.parent.parent.name)
    jobs = list(jobs_by_link.values())
    # Keep distinct planned job links even when multiple openings share a company.
    jobs.sort(key=lambda row: (row.get("entry_level_priority", False), row.get("match_score", 0)), reverse=True)
    for row in jobs:
        row["_sources"] = sorted(sources[row["link"]])
    return jobs, {
        "production_plans": len(production_plans),
        "unique_candidates": len(jobs),
        "excluded": dict(excluded),
        "previously_attempted_candidates": sum(bool(row["_previous_status"]) for row in jobs),
        "retryable_candidates": sum(row["_recommendation"] == "可重新尝试" for row in jobs),
        "confirmed_closed": sum(row["_recommendation"] == "不投递" for row in jobs),
        "manual_review": sum(row["_recommendation"] == "人工核查" for row in jobs),
    }


def write_report(path: Path,jobs,selected_links=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    fields=["编号","选择投递","处理状态","建议","漏投原因","上次状态","上次错误","采集来源","城市","公司","职位","薪资","经验","学历","匹配关键词","link"]
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader()
        for i,row in enumerate(jobs,1):
            status=row.get("_previous_status","")
            writer.writerow({"编号":i,"选择投递":"是" if selected_links is not None and row.get("link") in selected_links else "","处理状态":row.get("_state",""),"建议":row.get("_recommendation",""),"漏投原因":"之前尝试未确认成功" if status else "采集过但投递记录中未找到成功联系","上次状态":status,"上次错误":row.get("_previous_error",""),"采集来源":"、".join(row.get("_sources",[row.get("_source","")])),"城市":_get(row,"城市","city"),"公司":_get(row,"公司","company"),"职位":_get(row,"职位","position","title"),"薪资":_get(row,"薪资","salary"),"经验":_get(row,"经验","experience"),"学历":_get(row,"学历","education"),"匹配关键词":"、".join(row.get("_matched_keywords",[])),"link":row.get("link","")})

def parse_page_selection(command,page_ids,selected):
    value=command.strip().upper()
    if value in {"N","P","D","Q","A"}:
        if value=="A": selected.update(page_ids)
        return value
    for item in value.split(","):
        item=item.strip()
        if not item: continue
        remove=item.startswith("!")
        if remove: item=item[1:].strip()
        match=re.fullmatch(r"(\d+)(?:-(\d+))?",item)
        if not match: continue
        start,end=int(match.group(1)),int(match.group(2) or match.group(1))
        for n in range(min(start,end),max(start,end)+1):
            if n in page_ids:
                if remove: selected.discard(n)
                else: selected.add(n)
    return ""

def interactive_select(jobs,page_size=30):
    selected,page=set(),0; pages=max(1,(len(jobs)+page_size-1)//page_size)
    while True:
        start,end=page*page_size,min(len(jobs),(page+1)*page_size)
        print(f"\n第 {page+1}/{pages} 页：输入编号/范围(如 1-5,8)，A全选本页，!编号取消，N/P翻页，D完成，Q退出")
        for index in range(start,end):
            row,n=jobs[index],index+1; mark="[x]" if n in selected else "[ ]"
            print(f"{mark} {n}. {_get(row,'公司','company')} | {_get(row,'职位','position')} | {_get(row,'薪资','salary')} | {_get(row,'城市','city')} | {row.get('_previous_status','')}")
        result=parse_page_selection(input("选择> "),list(range(start+1,end+1)),selected)
        if result=="Q": return set()
        if result=="D": return selected
        if result=="N": page=min(page+1,pages-1)
        elif result=="P": page=max(page-1,0)

def main():
    parser=argparse.ArgumentParser(description="筛出采集过但未确认投递成功的岗位，供人工选择。")
    parser.add_argument("--scan-only",action="store_true",help="只扫描并生成清单，不进入选择/投递流程")
    args=parser.parse_args(); config=app.load_config()
    post_data,ledger,runs=ROOT/"assets/post_data/custom",ROOT/"assets/embedded_applied.jsonl",ROOT/"assets/embedded_runs"
    jobs,summary=collect_missed_jobs(config,post_data,ledger,runs)
    report=runs/f"missed_jobs_{datetime.now():%Y%m%d_%H%M%S}.csv"; write_report(report,jobs)
    print(f"Production plan rounds: {summary['production_plans']}; pending: {len(jobs)}; retryable: {summary['retryable_candidates']}; confirmed closed: {summary['confirmed_closed']}; manual review: {summary['manual_review']}\nReport: {report}")
    retryable_jobs = [row for row in jobs if row["_recommendation"] == "可重新尝试"]
    if args.scan_only or not retryable_jobs: return 0
    print("Only retryable jobs are shown below. Closed and uncertain jobs stay in the CSV and cannot be selected.")
    selected_ids=interactive_select(retryable_jobs)
    selected_jobs=[retryable_jobs[n-1] for n in sorted(selected_ids) if 1<=n<=len(retryable_jobs)]
    if not selected_jobs: print("没有选择岗位，未投递。"); return 0
    write_report(report,jobs,{row["link"] for row in selected_jobs})
    print(f"\n已选择 {len(selected_jobs)} 个岗位；清单已更新：{report}")
    print("确认这批岗位并开始使用当前已登录的 BOSS 窗口投递，请输入 APPLY。其他输入均取消。")
    if input("> ").strip()!="APPLY": print("已取消，未投递。"); return 0
    app.apply_jobs(config,selected_jobs,mode="production",target=0); return 0

if __name__=="__main__": raise SystemExit(main())
