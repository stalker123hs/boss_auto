#!/usr/bin/env python
# -*- coding: utf-8 -*-
import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import embedded_auto as app


def test_keyword_or_and_ignored_requirements():
    root = Path(tempfile.mkdtemp())
    app.POST_DATA = root / "post"
    app.POST_DATA.mkdir()
    app.LEDGER = root / "ledger.jsonl"
    fields = [
        "link", "职位", "城市", "公司", "薪资", "经验", "学历",
        "技能标签", "岗位要求和职责", "已失效", "代招",
    ]
    rows = [
        {"link": "a", "职位": "嵌入式工程师", "城市": "上海", "公司": "甲公司",
         "薪资": "30-50K", "经验": "5-10年", "学历": "博士", "技能标签": "STM32"},
        {"link": "b", "职位": "机器人销售", "城市": "上海", "公司": "乙公司",
         "薪资": "8-12K", "经验": "不限", "学历": "不限", "技能标签": "机器人"},
        {"link": "c", "职位": "固件开发", "城市": "上海", "公司": "甲公司",
         "薪资": "1K", "经验": "10年", "学历": "本科", "技能标签": "固件"},
        {"link": "d", "职位": "行政", "城市": "杭州", "公司": "丙公司",
         "薪资": "8K", "经验": "不限", "学历": "不限", "技能标签": ""},
    ]
    for row in rows:
        for field in fields:
            row.setdefault(field, "")
    with (app.POST_DATA / "sample.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    config = app.load_config()
    config["collection_id"] = ""
    config["salary_min_k"] = None
    config["salary_max_k"] = None
    jobs = app.collect_candidates(config, {"上海"})
    assert len(jobs) == 1
    assert jobs[0]["公司"] == "甲公司"  # 同公司只留一个
    assert jobs[0]["学历"] == "博士"    # 不因学历过滤
    assert jobs[0]["经验"] == "5-10年" # 不因经验过滤
    assert jobs[0]["薪资"] == "30-50K" # 不因薪资过滤


def test_exclude_keywords_and_salary_range():
    root = Path(tempfile.mkdtemp())
    app.POST_DATA = root / "post"
    app.POST_DATA.mkdir()
    app.LEDGER = root / "ledger.jsonl"
    fields = ["link", "职位", "城市", "公司", "薪资", "经验", "技能标签", "岗位要求和职责", "已失效", "代招"]
    rows = [
        {"link": "keep", "职位": "嵌入式工程师", "城市": "上海", "公司": "甲", "薪资": "10-15K", "经验": "不限", "技能标签": "STM32"},
        {"link": "robot-test", "职位": "机器人测试工程师", "城市": "上海", "公司": "机器人公司", "薪资": "10-15K", "经验": "不限", "技能标签": "系统测试"},
        {"link": "intern-low", "职位": "嵌入式实习生", "城市": "上海", "公司": "低日薪公司", "薪资": "100-200元/天", "经验": "不限", "技能标签": "STM32"},
        {"link": "intern-starts-150", "职位": "嵌入式实习生", "城市": "上海", "公司": "起薪150公司", "薪资": "150-250元/天", "经验": "不限", "技能标签": "STM32"},
        {"link": "intern-good", "职位": "嵌入式实习生", "城市": "上海", "公司": "高日薪公司", "薪资": "300-400元/天", "经验": "不限", "技能标签": "STM32"},
        {"link": "teacher", "职位": "机器人教师", "城市": "上海", "公司": "乙", "薪资": "9-10K", "经验": "不限", "技能标签": "机器人"},
        {"link": "welding", "职位": "嵌入式焊接工程师", "城市": "上海", "公司": "焊接公司", "薪资": "9-10K", "经验": "不限", "技能标签": "STM32"},
        {"link": "collect", "职位": "嵌入式工程师", "城市": "上海", "公司": "丙", "薪资": "9-10K", "经验": "不限", "技能标签": "数据采集"},
        {"link": "low", "职位": "嵌入式工程师", "城市": "上海", "公司": "丁", "薪资": "5-7K", "经验": "不限", "技能标签": "STM32"},
        {"link": "unknown", "职位": "嵌入式工程师", "城市": "上海", "公司": "戊", "薪资": "面议", "经验": "不限", "技能标签": "STM32"},
    ]
    for row in rows:
        for field in fields:
            row.setdefault(field, "")
    with (app.POST_DATA / "sample.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    config = {
        "match_keywords": ["嵌入式", "机器人", "机器人测试", "系统测试"],
        "exclude_keywords": ["机器人教师，乐高教师，数据采集，教师，焊接"],
        "blocked_title_keywords": [], "blocked_title_allow_keywords": [],
        "salary_min_k": 8, "salary_max_k": 12, "salary_match_mode": "overlap",
        "internship_keywords": ["实习", "实习生"], "internship_daily_salary_min_yuan": 200,
        "entry_level_keywords": [], "candidate_limit": 0,
    }
    jobs = app.collect_candidates(config, {"上海"})
    assert {job["link"] for job in jobs} == {"keep", "robot-test", "intern-good"}
    assert app.salary_range_k("20-35K·14薪") == (20.0, 35.0)
    assert app.salary_range_k("面议") is None
    assert app.daily_salary_range_yuan("100-200元/天") == (100.0, 200.0)


def test_low_daily_salary_is_blocked_before_csv_write():
    from boss_crawler.crawler import _daily_salary_starts_at_most

    assert _daily_salary_starts_at_most("100-200元/天", 200)
    assert _daily_salary_starts_at_most("150-250元/天", 200)
    assert _daily_salary_starts_at_most("200-300元/天", 200)
    assert not _daily_salary_starts_at_most("250-300元/天", 200)
    assert not _daily_salary_starts_at_most("8-12K", 200)


def test_crawler_never_writes_more_than_count_limit():
    from boss_crawler.crawler import process_job_list

    class Writer:
        def __init__(self):
            self.rows = []

        def writerow(self, row):
            self.rows.append(row)

    jobs = [
        {"encryptJobId": str(i), "jobName": f"嵌入式{i}", "salaryDesc": "8-12K"}
        for i in range(15)
    ]
    writer = Writer()
    written, _, _ = process_job_list(jobs, "unused.csv", set(), writer, max_write=4)
    assert written == 4
    assert len(writer.rows) == 4


def test_crawler_skips_duplicate_companies_during_collection():
    from boss_crawler.crawler import process_job_list

    class Writer:
        def __init__(self):
            self.rows = []
        def writerow(self, row):
            self.rows.append(row)

    jobs = [
        {"encryptJobId": "one", "jobName": "嵌入式工程师", "brandName": "甲公司", "salaryDesc": "8-12K"},
        {"encryptJobId": "two", "jobName": "单片机工程师", "brandName": "甲公司", "salaryDesc": "8-12K"},
        {"encryptJobId": "three", "jobName": "机器人工程师", "brandName": "乙公司", "salaryDesc": "8-12K"},
    ]
    writer = Writer()
    written, skipped, _ = process_job_list(jobs, "unused.csv", set(), writer, seen_companies=set())
    assert written == 2
    assert skipped == 1
    assert [row["公司"] for row in writer.rows] == ["甲公司", "乙公司"]


def test_list_mode_subdirectory_csv_is_used_for_candidates():
    root = Path(tempfile.mkdtemp())
    old_post_data, old_ledger = app.POST_DATA, app.LEDGER
    try:
        app.POST_DATA = root / "post_data"
        target = app.POST_DATA / "电子_电气_通信" / "电子_硬件开发"
        target.mkdir(parents=True)
        app.LEDGER = root / "ledger.jsonl"
        fields = ["link", "职位", "城市", "公司", "薪资", "技能标签"]
        with (target / "嵌入式软件工程师_上海.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow({"link": "nested", "职位": "嵌入式软件工程师", "城市": "上海",
                             "公司": "甲", "薪资": "8-12K", "技能标签": "STM32"})
        config = {"match_keywords": ["嵌入式"], "exclude_keywords": [],
                  "salary_min_k": 8, "salary_max_k": 12, "salary_match_mode": "inside",
                  "entry_level_keywords": [], "internship_daily_salary_min_yuan": 200}
        assert [job["link"] for job in app.collect_candidates(config, {"上海"})] == ["nested"]
    finally:
        app.POST_DATA, app.LEDGER = old_post_data, old_ledger


def test_crawl_omits_empty_monthly_salary_boundary():
    config = app.load_config()
    config["salary_min_k"] = 6
    config["salary_max_k"] = None
    seen = []
    old_run = app.run_command
    old_ensure = app.ensure_data
    old_cleanup = app.remove_unwanted_collected_rows
    old_history = app.remove_already_contacted_collected_rows
    old_limit = app.enforce_test_collection_limit
    try:
        app.run_command = lambda command: seen.extend(command)
        app.ensure_data = lambda: None
        app.remove_unwanted_collected_rows = lambda _config: 0
        app.remove_already_contacted_collected_rows = lambda *_args: 0
        app.enforce_test_collection_limit = lambda *_args: 0
        app.crawl(config, production=False)
        assert "--write-monthly-salary-min" in seen
        assert seen[seen.index("--write-monthly-salary-min") + 1] == "6"
        assert "--write-monthly-salary-max" not in seen
    finally:
        app.run_command = old_run
        app.ensure_data = old_ensure
        app.remove_unwanted_collected_rows = old_cleanup
        app.remove_already_contacted_collected_rows = old_history
        app.enforce_test_collection_limit = old_limit


def test_collected_csv_is_cleaned_before_candidate_generation():
    root = Path(tempfile.mkdtemp())
    old_post_data = app.POST_DATA
    try:
        app.POST_DATA = root / "post"
        app.POST_DATA.mkdir()
        fields = ["link", "职位", "城市", "公司", "薪资", "技能标签", "岗位要求和职责", "经验"]
        rows = [
            {"link": "keep", "职位": "机器人测试工程师", "城市": "上海", "公司": "甲", "薪资": "10-15K", "技能标签": "系统测试"},
            {"link": "teacher", "职位": "机器人教师", "城市": "上海", "公司": "乙", "薪资": "10-15K", "技能标签": "机器人"},
            {"link": "low", "职位": "嵌入式实习生", "城市": "上海", "公司": "丙", "薪资": "100-200元/天", "技能标签": "STM32"},
        ]
        with (app.POST_DATA / "jobs.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        config = {
            "match_keywords": ["机器人", "嵌入式"],
            "exclude_keywords": ["教师", "焊接"],
            "internship_daily_salary_min_yuan": 200,
            "salary_min_k": 8, "salary_max_k": 12,
        }
        assert app.remove_unwanted_collected_rows(config) == 2
        with (app.POST_DATA / "jobs.csv").open(encoding="utf-8-sig", newline="") as handle:
            assert [row["link"] for row in csv.DictReader(handle)] == ["keep"]
    finally:
        app.POST_DATA = old_post_data


def test_strict_monthly_salary_range():
    config = {"salary_min_k": 8, "salary_max_k": 12, "salary_match_mode": "inside"}
    assert app.salary_matches({"薪资": "8-12K"}, config)
    assert not app.salary_matches({"薪资": "4-8K"}, config)
    assert not app.salary_matches({"薪资": "10-15K"}, config)


def test_test_and_production_candidate_limits_are_separate():
    config = {"test_candidate_limit": 10, "production_candidate_limit": 150}
    assert app.candidate_limit_for_mode(config, production=False) == 10
    assert app.candidate_limit_for_mode(config, production=True) == 150


def test_preflight_blocks_policy_violations_before_applying():
    config = {
        "match_keywords": ["嵌入式"],
        "exclude_keywords": ["教师", "培训师", "实训师", "焊接"],
        "title_exclude_keywords": ["忽略薪酬范围", "具体面议"],
        "salary_min_k": 8, "salary_max_k": 12, "salary_match_mode": "inside",
        "internship_daily_salary_min_yuan": 200,
    }
    good = {"职位": "嵌入式工程师", "薪资": "8-12K", "公司": "甲"}
    teacher = {"职位": "机器人教师", "薪资": "8-12K", "公司": "乙"}
    trainer = {"职位": "机器人培训师", "薪资": "8-12K", "公司": "己"}
    practical_trainer = {"职位": "机器人实训师", "薪资": "8-12K", "公司": "庚"}
    low_intern = {"职位": "嵌入式实习生", "薪资": "100-200元/天", "公司": "丙"}
    low_daily_without_intern = {"职位": "嵌入式工程师", "薪资": "100-150元/天", "公司": "戊"}
    salary_bait = {"职位": "嵌入式开发【忽略薪酬范围，具体面议】", "薪资": "8-12K", "公司": "丁"}
    assert app.candidate_policy_violations(good, config) == []
    assert "命中屏蔽词" in app.candidate_policy_violations(teacher, config)
    assert "命中屏蔽词" in app.candidate_policy_violations(trainer, config)
    assert "命中屏蔽词" in app.candidate_policy_violations(practical_trainer, config)
    assert "日薪不高于门槛" in app.candidate_policy_violations(low_intern, config)
    assert "日薪不高于门槛" in app.candidate_policy_violations(low_daily_without_intern, config)
    assert "标题声明薪资范围不适用" in app.candidate_policy_violations(salary_bait, config)
    assert not app.validate_application_batch([good, teacher], config, 2)
    assert not app.validate_application_batch([good], config, 2)


def test_existing_resume_requires_delivery_evidence():
    from resume_matcher import auto_apply as module

    class Element:
        def __init__(self, click=None):
            self._click = click

        def click(self):
            if self._click:
                self._click()

    class Page:
        def __init__(self):
            self.sent = False

        def ele(self, selector, timeout=0):
            if "geek_chat_send_resume" in selector:
                return Element()
            if "btn-sure-v2" in selector:
                return Element(lambda: setattr(self, "sent", True))
            return None

        def eles(self, selector, timeout=0):
            if selector == "css:.resume-list li":
                return [Element()]
            if self.sent and "点击预览附件简历" in selector:
                return [Element()]
            return []

        def run_js(self, script):
            if "chat-message-list" in script:
                return 1 if self.sent else 0
            return False

    old_sleep = module.time.sleep
    module.time.sleep = lambda _seconds: None
    try:
        assert module.send_existing_resume_attachment(Page(), resume_index=0, timeout=1)
    finally:
        module.time.sleep = old_sleep


def test_existing_contact_state_is_detected_before_clicking():
    from resume_matcher import auto_apply as module

    class Page:
        def __init__(self, text):
            self.text = text

        def run_js(self, _script):
            return self.text

    assert module._existing_contact_label(Page("继续沟通")) == "继续沟通"
    assert module._existing_contact_label(Page("已投递")) == "已投递"
    assert module._existing_contact_label(Page("沟通能力强，负责客户沟通")) == ""


def test_existing_chat_record_is_detected_before_greeting():
    from resume_matcher import auto_apply as module

    class Page:
        def __init__(self, record):
            self.record = record

        def run_js(self, _script):
            return self.record

    assert module._existing_conversation_text(Page("您好，我想应聘这个岗位")) == "您好，我想应聘这个岗位"
    assert module._existing_conversation_text(Page("暂无消息")) == ""
    assert module._existing_conversation_text(Page("")) == ""


def test_closed_list_jobs_are_not_collected():
    from boss_crawler.crawler import _is_closed_list_job

    assert _is_closed_list_job({"invalidStatus": 1})
    assert _is_closed_list_job({"jobStatusDesc": "职位已关闭"})
    assert _is_closed_list_job({"status": "停止招聘"})
    assert not _is_closed_list_job({"jobName": "嵌入式工程师", "invalidStatus": 0})


def test_fresh_crawl_discards_old_csv():
    root = Path(tempfile.mkdtemp())
    old_post_data = app.POST_DATA
    try:
        app.POST_DATA = root / "custom"
        app.POST_DATA.mkdir()
        (app.POST_DATA / "old.csv").write_text("link\nhttps://example.test\n", encoding="utf-8")
        discarded = app.discard_previous_csv()
        assert discarded == 1
        assert not (app.POST_DATA / "old.csv").exists()
    finally:
        app.POST_DATA = old_post_data


if __name__ == "__main__":
    test_keyword_or_and_ignored_requirements()
    test_exclude_keywords_and_salary_range()
    test_collected_csv_is_cleaned_before_candidate_generation()
    test_strict_monthly_salary_range()
    test_preflight_blocks_policy_violations_before_applying()
    test_existing_resume_requires_delivery_evidence()
    test_existing_contact_state_is_detected_before_clicking()
    test_existing_chat_record_is_detected_before_greeting()
    test_closed_list_jobs_are_not_collected()
    test_fresh_crawl_discards_old_csv()
    print("FILTER_TEST_OK")
