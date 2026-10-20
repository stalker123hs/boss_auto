#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""面向嵌入式/机器人岗位的零 LLM 自动采集与投递入口。"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

CONFIG_PATH = ROOT / "embedded_config.json"
# 原生岗位列表模式会把 CSV 写入职位分类子目录；不能只读 custom，
# 否则“采集成功但候选为 0”。所有采集与清理都以 post_data 根目录为准。
POST_DATA = ROOT / "assets" / "post_data"
RUNS = ROOT / "assets" / "embedded_runs"
LEDGER = ROOT / "assets" / "embedded_applied.jsonl"


def load_config(config_path=None):
    """读取岗位配置；未指定时使用当前目录的 embedded_config.json。"""
    path = Path(config_path) if config_path else CONFIG_PATH
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    # 运行时标识：不同岗位配置不会误恢复彼此的未完成任务。
    config["_profile_id"] = str(config.get("profile_id") or path.name)
    return config


def collection_dir(config=None):
    """当前配置专属的采集目录；不同岗位配置绝不混用 CSV。"""
    raw = str((config or {}).get("collection_id") or "").strip()
    if not raw:
        return POST_DATA
    name = re.sub(r'[^0-9A-Za-z._-]+', '_', raw).strip('._') or "default"
    return POST_DATA / name


def truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "是", "已失效"}


def keyword_list(value):
    """将配置中的关键词列表规范化；兼容一项内用逗号或换行分隔的写法。"""
    raw_values = value if isinstance(value, list) else [value]
    items = []
    for raw in raw_values:
        items.extend(re.split(r"[,，;；\n\r]+", str(raw or "")))
    return [item.strip().lower() for item in items if item.strip()]


def normalized_company(job):
    return str(job.get("公司") or job.get("company") or "").strip().lower()


def job_search_text(job):
    """用于岗位筛选的完整文本，覆盖标题、标签、描述和经验字段。"""
    return " ".join([
        str(job.get("职位") or ""),
        str(job.get("技能标签") or ""),
        str(job.get("岗位要求和职责") or ""),
        str(job.get("经验") or ""),
    ]).lower()


def title_and_skill_text(job):
    """研发方向准入只看职位名和技能标签，不拿公司/JD 的泛词放行。"""
    return " ".join([
        str(job.get("职位") or job.get("position") or ""),
        str(job.get("技能标签") or job.get("skills") or ""),
    ]).lower()


def meets_research_gate(job, config):
    """职位名或技能标签必须明确显示研发/开发方向。未配置时兼容旧配置。"""
    gate_words = keyword_list(config.get("research_gate_keywords", []))
    return not gate_words or any(word in title_and_skill_text(job) for word in gate_words)


def is_hard_blocked_title(job, config):
    """教师、销售、运营等非研发标题不可被“机器人/嵌入式”关键词覆盖。"""
    hard_blocks = keyword_list(config.get("hard_blocked_title_keywords", []))
    title = str(job.get("职位") or job.get("position") or "").lower()
    return bool(hard_blocks and any(word in title for word in hard_blocks))


def salary_range_k(value):
    """解析常见的 BOSS 月薪 K 字段，无法识别时返回 None。"""
    text = str(value or "").lower().replace(" ", "")
    ranged = re.search(r"(\d+(?:\.\d+)?)[-~至到](\d+(?:\.\d+)?)k", text)
    if ranged:
        low, high = float(ranged.group(1)), float(ranged.group(2))
        return min(low, high), max(low, high)
    single = re.search(r"(\d+(?:\.\d+)?)k", text)
    if single:
        amount = float(single.group(1))
        return amount, amount
    return None


def daily_salary_range_yuan(value):
    """解析日薪（例如 100-200元/天、300/天）；非日薪返回 None。"""
    text = str(value or "").lower().replace(" ", "")
    if not any(marker in text for marker in ("元/天", "元/日", "/天", "/日")):
        return None
    ranged = re.search(r"(\d+(?:\.\d+)?)[-~至到](\d+(?:\.\d+)?)", text)
    if ranged:
        low, high = float(ranged.group(1)), float(ranged.group(2))
        return min(low, high), max(low, high)
    single = re.search(r"(\d+(?:\.\d+)?)", text)
    if single:
        amount = float(single.group(1))
        return amount, amount
    return None


def is_low_paid_internship(job, text, config):
    """拦截日薪起薪不高于配置阈值的岗位，不依赖职位是否写明“实习”。

    阈值为 200 时，100-200、150-250、200-300 元/天都会被拦截。
    """
    limit = config.get("internship_daily_salary_min_yuan")
    if limit in (None, ""):
        return False
    pay = daily_salary_range_yuan(job.get("薪资") or job.get("salary"))
    return pay is not None and pay[0] <= float(limit)


def candidate_policy_violations(job, config):
    """返回岗位违反当前投递规则的原因；空列表才允许进入自动投递。"""
    text = job_search_text(job)
    title = str(job.get("职位") or job.get("position") or "").lower()
    keywords = keyword_list(config.get("match_keywords", []))
    exclude_keywords = keyword_list(config.get("exclude_keywords", []))
    title_exclude_keywords = keyword_list(config.get("title_exclude_keywords", []))
    blocked_titles = keyword_list(config.get("blocked_title_keywords", []))
    allowed_blocked_titles = keyword_list(config.get("blocked_title_allow_keywords", []))
    is_daily_paid = daily_salary_range_yuan(job.get("薪资") or job.get("salary")) is not None
    reasons = []
    if truthy(job.get("已失效")) or truthy(job.get("代招")):
        reasons.append("岗位失效或代招")
    if keywords and not any(word in text for word in keywords):
        reasons.append("未命中目标关键词")
    if not meets_research_gate(job, config):
        reasons.append("职位名和技能标签未命中研发准入词")
    if is_hard_blocked_title(job, config):
        reasons.append("命中非研发硬屏蔽职位类型")
    if any(word in text for word in exclude_keywords):
        reasons.append("命中屏蔽词")
    if any(word in title for word in title_exclude_keywords):
        reasons.append("标题声明薪资范围不适用")
    if is_low_paid_internship(job, text, config):
        reasons.append("日薪不高于门槛")
    if (blocked_titles and any(word in title for word in blocked_titles)
            and not any(word in title for word in allowed_blocked_titles)):
        reasons.append("命中屏蔽职位类型")
    if not is_daily_paid and not salary_matches(job, config):
        reasons.append("月薪不在目标区间")
    return reasons


def validate_application_batch(jobs, config, target):
    """投递前由脚本验收候选；前 target 家任一违规都拒绝启动浏览器。"""
    if target > 0 and len(jobs) < target:
        print(f"投递前自动验收失败：仅有 {len(jobs)} 家合格候选，少于测试目标 {target} 家。")
        return False
    count = len(jobs) if target <= 0 else min(len(jobs), target)
    rejected = []
    for index, job in enumerate(jobs[:count], 1):
        reasons = candidate_policy_violations(job, config)
        verdict = "通过" if not reasons else f"拒绝：{'；'.join(reasons)}"
        print(f"  验收 [{index}] {job.get('公司') or job.get('company')} | "
              f"{job.get('职位') or job.get('position')} | "
              f"{job.get('薪资') or job.get('salary')} | "
              f"命中：{','.join(job.get('matched_keywords') or [])} | {verdict}")
        if reasons:
            rejected.append((index, job, reasons))
    if rejected:
        print("投递前自动验收失败：以下候选不符合当前配置，已阻止投递。")
        for index, job, reasons in rejected:
            print(f"  [{index}] {job.get('公司') or job.get('company')} - "
                  f"{job.get('职位') or job.get('position')}: {'；'.join(reasons)}")
        return False
    print(f"投递前自动验收通过：前 {count} 家均符合关键词、屏蔽词和薪资规则。")
    return True


def remove_unwanted_collected_rows(config):
    """爬虫写完 CSV 后立即清理，保证原始 CSV 与当前岗位配置一致。"""
    match_keywords = keyword_list(config.get("match_keywords", []))
    exclude_keywords = keyword_list(config.get("exclude_keywords", []))
    title_exclude_keywords = keyword_list(config.get("title_exclude_keywords", []))
    blocked_titles = keyword_list(config.get("blocked_title_keywords", []))
    hard_blocked_titles = keyword_list(config.get("hard_blocked_title_keywords", []))
    allowed_blocked_titles = keyword_list(config.get("blocked_title_allow_keywords", []))
    removed = 0
    for path in sorted(collection_dir(config).rglob("*.csv")):
        if "_details" in path.name:
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fields = reader.fieldnames
                rows = list(reader)
        except (OSError, UnicodeError):
            continue
        if not fields:
            continue
        kept = []
        for job in rows:
            text = job_search_text(job)
            title = str(job.get("职位") or "").lower()
            is_daily_paid = daily_salary_range_yuan(job.get("薪资") or job.get("salary")) is not None
            title_is_blocked = (
                blocked_titles
                and any(word in title for word in blocked_titles)
                and not any(word in title for word in allowed_blocked_titles)
            )
            title_is_hard_blocked = bool(
                hard_blocked_titles and any(word in title for word in hard_blocked_titles)
            )
            unwanted = bool(match_keywords) and not any(word in text for word in match_keywords)
            unwanted = unwanted or not meets_research_gate(job, config)
            unwanted = unwanted or truthy(job.get("已失效")) or truthy(job.get("代招"))
            unwanted = unwanted or any(word in text for word in exclude_keywords)
            unwanted = unwanted or any(word in title for word in title_exclude_keywords)
            unwanted = unwanted or is_low_paid_internship(job, text, config)
            unwanted = unwanted or title_is_blocked
            unwanted = unwanted or title_is_hard_blocked
            if not is_daily_paid and not salary_matches(job, config):
                unwanted = True
            if unwanted:
                removed += 1
            else:
                kept.append(job)
        if len(kept) != len(rows):
            temporary = path.with_suffix(".csv.tmp")
            with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(kept)
            temporary.replace(path)
    if removed:
        print(f"采集后已清理 {removed} 条屏蔽或低日薪实习岗位。")
    return removed


def salary_matches(job, config):
    """按配置筛选月薪 K 区间；没有设置区间时保持旧版不过滤薪资。"""
    wanted_low = config.get("salary_min_k")
    wanted_high = config.get("salary_max_k")
    if wanted_low in (None, "") and wanted_high in (None, ""):
        return True
    actual = salary_range_k(job.get("薪资") or job.get("salary"))
    if actual is None:
        return False
    low, high = actual
    if wanted_low not in (None, ""):
        wanted_low = float(wanted_low)
    else:
        wanted_low = float("-inf")
    if wanted_high not in (None, ""):
        wanted_high = float(wanted_high)
    else:
        wanted_high = float("inf")
    if str(config.get("salary_match_mode", "overlap")).lower() == "inside":
        return low >= wanted_low and high <= wanted_high
    return high >= wanted_low and low <= wanted_high


def load_ledger():
    links, companies = set(), set()
    if not LEDGER.exists():
        return links, companies
    with LEDGER.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except (ValueError, TypeError):
                continue
            if item.get("link"):
                links.add(item["link"])
            if item.get("company"):
                companies.add(str(item["company"]).strip().lower())
    return links, companies


def candidate_limit_for_mode(config, production):
    """测试只保留测试数量，正式运行才使用正式候选上限。"""
    key = "production_candidate_limit" if production else "test_candidate_limit"
    fallback = config.get("candidate_limit", 0)
    return int(config.get(key, fallback) or 0)


def collect_candidates(config, cities, limit=None):
    """按关键词、排除词和可选薪资区间筛选；学历、经验不参与。"""
    keywords = keyword_list(config["match_keywords"])
    exclude_keywords = keyword_list(config.get("exclude_keywords", []))
    blocked_titles = keyword_list(config.get(
        "blocked_title_keywords",
        ["销售", "客服", "运营", "人事", "行政", "财务", "采购", "市场"],
    ))
    hard_blocked_titles = keyword_list(config.get("hard_blocked_title_keywords", []))
    allowed_blocked_titles = keyword_list(config.get(
        "blocked_title_allow_keywords",
        ["嵌入式", "固件", "单片机", "mcu", "stm32", "驱动", "bsp", "控制", "开发", "工程师", "算法"],
    ))
    applied_links, _applied_companies = load_ledger()
    by_link = {}
    entry_terms = keyword_list(config.get("entry_level_keywords", []))

    for path in sorted(collection_dir(config).rglob("*.csv")):
        if "_details" in path.name:
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, UnicodeError):
            continue
        for job in rows:
            if cities and str(job.get("城市") or "").strip() not in cities:
                continue
            if truthy(job.get("已失效")) or truthy(job.get("代招")):
                continue
            link = str(job.get("link") or "").strip()
            company = normalized_company(job)
            # 历史账本只排除同一职位链接。不能因为曾联系过该公司另一个岗位，
            # 就把该公司的新岗位提前清掉；进入详情页后还会再检查“继续沟通”。
            if not link or not company or link in applied_links:
                continue
            title = str(job.get("职位") or "").strip()
            text = job_search_text(job)
            matched = [kw for kw in keywords if kw in text]
            if not matched:
                continue
            if not meets_research_gate(job, config):
                continue
            if hard_blocked_titles and any(word in title.lower() for word in hard_blocked_titles):
                continue
            if any(word in text for word in exclude_keywords):
                continue
            if is_low_paid_internship(job, text, config):
                continue
            # 日薪高于实习门槛的岗位不适用月薪 K 区间筛选。
            is_daily_paid = daily_salary_range_yuan(job.get("薪资") or job.get("salary")) is not None
            if not is_daily_paid and not salary_matches(job, config):
                continue
            title_lower = title.lower()
            if (blocked_titles and any(word in title_lower for word in blocked_titles)
                    and not any(word in title_lower for word in allowed_blocked_titles)):
                continue
            job["matched_keywords"] = matched
            job["match_score"] = len(matched) * 10
            entry_hits = [term for term in entry_terms if term in text]
            job["entry_level_keywords"] = entry_hits
            job["entry_level_priority"] = bool(entry_hits)
            # 职位名直接写明实习/校招的优先级最高，其次才是描述或经验字段命中。
            job["entry_level_score"] = len(entry_hits) + sum(
                5 for term in entry_hits if term in title_lower
            )
            old = by_link.get(link)
            if old is None or len(matched) > len(old.get("matched_keywords", [])):
                by_link[link] = job

    # 同一家公司本轮只投一个岗位，避免重复联系。
    unique = []
    seen_companies = set()
    ordered = sorted(
        by_link.values(),
        key=lambda j: (
            -int(j.get("entry_level_priority", False)),
            -int(j.get("entry_level_score", 0)),
            -j["match_score"],
            j.get("公司", ""),
        ),
    )
    for job in ordered:
        company = normalized_company(job)
        if company in seen_companies:
            continue
        seen_companies.add(company)
        unique.append(job)
    limit = int(config.get("candidate_limit", 0) or 0) if limit is None else int(limit or 0)
    return unique[:limit] if limit > 0 else unique


def run_command(parts):
    shown = subprocess.list2cmdline([str(p) for p in parts])
    print(f"\n执行：{shown}")
    completed = subprocess.run([str(p) for p in parts], cwd=str(ROOT))
    if completed.returncode:
        raise SystemExit(completed.returncode)


def ensure_data():
    if (ROOT / "assets" / "weizhi.json").exists():
        return
    print("首次运行需要获取 BOSS 城市数据。")
    run_command([sys.executable, SCRIPTS / "stages" / "boss_post_interactive.py", "--update-data"])


def discard_previous_csv(config=None):
    """删除旧采集 CSV，避免“重新采集”混入已关闭的历史岗位。"""
    directory = collection_dir(config)
    if not directory.exists():
        return None
    files = list(directory.rglob("*.csv"))
    if not files:
        return None
    locked = []
    for path in files:
        for attempt in range(5):
            try:
                path.unlink()
                break
            except PermissionError:
                if attempt == 4:
                    locked.append(path.name)
                else:
                    time.sleep(1)
    if locked:
        names = "、".join(locked)
        raise RuntimeError(f"无法重新采集：以下旧 CSV 正被占用，请关闭占用它们的程序后重试：{names}")
    print(f"已删除 {len(files)} 个旧采集文件，将重新采集岗位。")
    return len(files)


def clear_collection(config=None):
    """只清当前配置的采集 CSV，不碰投递账本和断点。"""
    removed = discard_previous_csv(config)
    directory = collection_dir(config)
    if removed is None:
        print(f"当前没有可删除的采集 CSV：{directory}")
    else:
        print(f"已清空当前采集数据：{directory}")
    print("已沟通过的岗位账本和未完成投递断点均已保留。")
    return 0


def enforce_test_collection_limit(limit, config=None):
    """测试采集完成后全局只保留前 limit 个不同公司，作为最后一道硬限制。"""
    kept = 0
    companies = set()
    for path in sorted(collection_dir(config).rglob("*.csv")):
        if "_details" in path.name:
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields, rows = reader.fieldnames, list(reader)
        if not fields:
            continue
        accepted = []
        for row in rows:
            company = normalized_company(row)
            if kept >= limit or not company or company in companies:
                continue
            companies.add(company)
            accepted.append(row)
            kept += 1
        temporary = path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(accepted)
        temporary.replace(path)
    print(f"测试采集全局上限：保留 {kept}/{limit} 条不同公司岗位。")
    return kept


def remove_already_contacted_collected_rows(config=None):
    """采集测试阶段就移除历史沟通过的职位链接，避免 10 条里混入不可投岗位。"""
    links, _ = load_ledger()
    removed = 0
    for path in collection_dir(config).rglob("*.csv"):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields, rows = reader.fieldnames, list(reader)
        kept = [row for row in rows if str(row.get("link") or "").strip() not in links]
        removed += len(rows) - len(kept)
        if len(kept) != len(rows):
            temporary = path.with_suffix(".csv.tmp")
            with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(kept)
            temporary.replace(path)
    if removed:
        print(f"采集阶段跳过历史已沟通职位 {removed} 条。")
    return removed


def crawl(config, production, fresh=False):
    if fresh and config.get("fresh_crawl_discard_old_csv", True):
        discard_previous_csv(config)
    ensure_data()
    cities = config["production_cities" if production else "test_cities"]
    count = config[
        "production_crawl_count_per_query_city" if production
        else "test_crawl_count_per_query_city"
    ]
    raw_excludes = keyword_list(config.get("exclude_keywords", []))
    raw_excludes += keyword_list(config.get("blocked_title_keywords", []))
    crawl_mode = str(config.get("crawl_mode") or "custom")
    crawl_positions = config.get("crawl_positions") or config["search_queries"]
    command = [
        sys.executable,
        SCRIPTS / "stages" / "boss_post_interactive.py",
        "-m", crawl_mode,
        "-p", ",".join(crawl_positions),
        "-c", ",".join(cities),
        "-n", str(count),
        "--total-count", str(config.get("test_collect_fetch_total", config.get("test_collect_total", 10)) if not production else 0),
        "--exclude-daily-salary-at-most", str(config.get("internship_daily_salary_min_yuan", 200)),
        "--write-match-keywords", ",".join(keyword_list(config.get("match_keywords", []))),
        "--write-exclude-keywords", ",".join(raw_excludes),
        "-y",
        "--output-dir", str(collection_dir(config)),
    ]
    # 空的薪资边界不传给 argparse；str(None) 会变成 "None"，无法解析为 float。
    minimum_salary = config.get("salary_min_k")
    maximum_salary = config.get("salary_max_k")
    if minimum_salary not in (None, ""):
        command.extend(["--write-monthly-salary-min", str(minimum_salary)])
    if maximum_salary not in (None, ""):
        command.extend(["--write-monthly-salary-max", str(maximum_salary)])
    run_command(command)
    if not production:
        remove_already_contacted_collected_rows(config)
        enforce_test_collection_limit(int(config.get("test_collect_total", 10)), config)
    remove_unwanted_collected_rows(config)
    return cities


def make_run_dir(mode):
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = RUNS / f"{stamp}_{mode}"
    (run_dir / "state").mkdir(parents=True, exist_ok=False)
    return run_dir


def save_plan(run_dir, jobs, config, mode):
    entry_count = sum(1 for job in jobs if job.get("entry_level_priority"))
    plan = {
        "mode": mode,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "profile_id": config.get("_profile_id"),
        "profile_name": config.get("profile_name", "嵌入式 / 机器人"),
        "rules": config.get("profile_description", "任一关键词命中；不检查学历、经验、薪资"),
        "greeting": config["greeting"],
        "entry_level_count": entry_count,
        "candidate_limit": len(jobs),
        "jobs": jobs,
    }
    path = run_dir / "state" / "candidate_plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def find_resumable_run(mode, profile_id):
    """寻找最近一个未完成的同模式任务。"""
    if not RUNS.exists():
        return None, 0, []
    for run_dir in sorted(RUNS.glob(f"*_{mode}"), reverse=True):
        plan_path = run_dir / "state" / "candidate_plan.json"
        checkpoint_path = run_dir / "state" / "apply_checkpoint.json"
        log_path = run_dir / "state" / "apply_log.json"
        # 兼容升级前的旧日志：旧版本只有正常跑完才会生成 apply_log，
        # 因此没有 complete 字段的旧日志也代表该轮已经结束。
        if log_path.exists():
            try:
                old_log = json.loads(log_path.read_text(encoding="utf-8"))
                if old_log.get("complete", True):
                    continue
            except (OSError, ValueError, TypeError):
                pass
        if not plan_path.exists() or not checkpoint_path.exists():
            continue
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if checkpoint.get("complete"):
                continue
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            saved_profile = plan.get("profile_id")
            # 升级前默认嵌入式计划没有 profile_id，仍允许默认配置继续；
            # 自定义配置只能恢复由同一个配置文件创建的任务。
            if saved_profile != profile_id and not (saved_profile is None and profile_id == CONFIG_PATH.name):
                continue
            return run_dir, int(checkpoint.get("next_index", 0)), checkpoint.get("results") or []
        except (OSError, ValueError, TypeError):
            continue
    return None, 0, []


def record_successes(results):
    records = {}
    if LEDGER.exists():
        with LEDGER.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    old = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if old.get("link"):
                    records[old["link"]] = old
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    for result in results:
        if result.get("status") != "applied":
            continue
        job = result.get("job") or {}
        link = job.get("link")
        if not link:
            continue
        records[link] = {
            "at": result.get("timestamp"),
            "link": link,
            "company": job.get("公司") or job.get("company"),
            "position": job.get("职位") or job.get("position"),
            "attachment_status": result.get("attachment_status", records.get(link, {}).get("attachment_status", "unknown")),
        }
    temp = LEDGER.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for item in records.values():
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    temp.replace(LEDGER)


def apply_jobs(config, jobs, mode, target, run_dir=None, start_index=0, previous_results=None):
    from resume_matcher.auto_apply import auto_apply_jobs
    from resume_matcher.config import ResumeProfile

    if run_dir is None:
        run_dir = make_run_dir(mode)
        plan_path = save_plan(run_dir, jobs, config, mode)
    else:
        plan_path = run_dir / "state" / "candidate_plan.json"
    print(f"候选计划：{plan_path}")
    print(f"候选公司：{len(jobs)}；成功目标：{'全部' if target <= 0 else target}")
    if not jobs:
        print("没有可投的新公司。请先运行采集，或检查 assets/post_data/custom 下的 CSV。")
        return 1
    if not validate_application_batch(jobs, config, target):
        return 2

    greetings = {job["link"]: config["greeting"] for job in jobs}
    results = auto_apply_jobs(
        qualified_jobs=jobs,
        _profile=ResumeProfile(),
        max_applications=target,
        headless=False,
        greetings=greetings,
        resume_file_path=None,
        output_dir=str(run_dir),
        use_existing_resume=True,
        resume_index=int(config.get("resume_index", 0)),
        min_interval=float(config.get("min_interval_seconds", 15)),
        max_interval=float(config.get("max_interval_seconds", 30)),
        stop_on_partial=(mode == "test"),
        start_index=start_index,
        previous_results=previous_results,
        # Pause/stop only when BOSS itself shows one of its two explicit
        # 120-contact or 150-contact messages; never on an internal counter.
        manual_pause_thresholds=(),
        hard_stop_threshold=None,
    )
    record_successes(results)
    success = sum(1 for item in results if item.get("status") == "applied")
    print(f"本轮确认成功：{success}")
    if target > 0 and success < target:
        print(f"未达到测试目标 {target} 家；不会打包为正式版。")
        return 3
    return 0


def show_plan(config, cities, production):
    jobs = collect_candidates(config, set(cities), candidate_limit_for_mode(config, production))
    target = config["production_success_target" if production else "test_success_target"]
    run_dir = make_run_dir("production-plan" if production else "test-plan")
    path = save_plan(run_dir, jobs, config, "plan")
    print(f"候选公司 {len(jobs)} 家，计划已写入：{path}")
    entry_count = sum(1 for job in jobs if job.get("entry_level_priority"))
    wanted = int(config.get("entry_level_priority_minimum", 0) or 0)
    print(f"其中实习/校招/应届/经验不限优先岗位：{entry_count} 家（目标至少 {wanted} 家）")
    if wanted and entry_count < wanted:
        print(f"警告：实际只采到 {entry_count} 家入门友好岗位，没有用普通岗位冒充前 {wanted} 家。")
    print(f"成功目标：{'全部新公司' if target <= 0 else str(target) + ' 家'}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="BOSS 岗位自动投递（零 LLM token）")
    parser.add_argument("mode", choices=["check", "login", "clear-collection", "crawl-test", "crawl-production", "plan-test", "plan-production", "verify-test", "verify-production", "test", "production"])
    parser.add_argument("--skip-crawl", action="store_true", help="直接使用已经采集的 CSV")
    parser.add_argument("--target", type=int, help="覆盖本轮成功目标；0 表示全部新候选")
    parser.add_argument("--config", help="岗位配置 JSON 路径；默认 embedded_config.json")
    parser.add_argument("--new-run", action="store_true", help="不恢复旧进度，重新采集并创建新任务")
    parser.add_argument("--cities", help="仅本次覆盖采集城市，逗号分隔；不会修改 JSON 配置")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.cities:
        override_cities = [name.strip() for name in args.cities.split(",") if name.strip()]
        if not override_cities:
            parser.error("--cities 至少要有一个城市")
        config["production_cities" if "production" in args.mode else "test_cities"] = override_cities

    if args.mode == "check":
        from DrissionPage import ChromiumOptions  # noqa: F401
        from browser_finder import find_chrome_browser
        browser = find_chrome_browser()
        print("[检查通过] Python、DrissionPage、配置文件均可用。")
        print(f"[浏览器] {browser or '由 DrissionPage 自动查找'}")
        print("[说明] 正常启动时会使用程序自己的浏览器窗口和登录状态。")
        return 0

    if args.mode == "login":
        from boss_crawler.auth import ensure_login
        return 0 if ensure_login() else 1

    if args.mode == "clear-collection":
        return clear_collection(config)

    if args.mode in {"test", "production"} and not args.new_run:
        resume_dir, start_index, previous_results = find_resumable_run(args.mode, config["_profile_id"])
        if resume_dir:
            plan = json.loads((resume_dir / "state" / "candidate_plan.json").read_text(encoding="utf-8"))
            jobs = plan.get("jobs") or []
            target = int(config["production_success_target" if args.mode == "production" else "test_success_target"])
            if args.target is not None:
                target = args.target
            print(f"发现未完成任务：{resume_dir.name}")
            print(f"跳过重新采集，从第 {start_index + 1}/{len(jobs)} 个岗位继续。")
            return apply_jobs(config, jobs, args.mode, target, resume_dir, start_index,
                              previous_results)

    production = "production" in args.mode
    cities = config["production_cities" if production else "test_cities"]
    if args.mode.startswith("crawl-"):
        crawl(config, production, fresh=args.new_run)
        return 0
    if args.mode in {"test", "production"} and not args.skip_crawl:
        cities = crawl(config, production, fresh=args.new_run)

    jobs = collect_candidates(config, set(cities), candidate_limit_for_mode(config, production))
    if args.mode.startswith("plan-"):
        return show_plan(config, cities, production)

    target = int(config["production_success_target" if production else "test_success_target"])
    if args.target is not None:
        target = args.target
    if args.mode.startswith("verify-"):
        # 只做脚本自检：不启动浏览器、不点击 BOSS 页面、不产生任何沟通。
        return 0 if validate_application_batch(jobs, config, target) else 2
    return apply_jobs(config, jobs, args.mode, target)


if __name__ == "__main__":
    raise SystemExit(main())
