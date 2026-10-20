import csv,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import review_missed_jobs as review

def test_scan_uses_only_production_plan_and_excludes_verified_contacts(tmp_path):
    post=tmp_path/"post"; post.mkdir()
    runs=tmp_path/"runs"; state=runs/"production-a"/"state"; state.mkdir(parents=True)
    ledger=tmp_path/"ledger.jsonl"
    (state/"apply_log.json").write_text(json.dumps({"results":[
        {"job":{"link":"https://x/b"},"status":"partial","greeting_verified":True},
        {"job":{"link":"https://x/c"},"status":"error","error":"timeout"}]}),encoding="utf-8")
    ledger.write_text(json.dumps({"link":"https://x/a","company":"Alpha"})+"\n",encoding="utf-8")
    k_title=chr(0x804c)+chr(0x4f4d)
    k_city=chr(0x57ce)+chr(0x5e02)
    k_company=chr(0x516c)+chr(0x53f8)
    plan={"mode":"production","jobs":[
        {"link":"https://x/a",k_title:"Embedded",k_city:"Shanghai",k_company:"Alpha"},
        {"link":"https://x/b",k_title:"Robot firmware",k_city:"Shanghai",k_company:"Beta"},
        {"link":"https://x/c",k_title:"MCU engineer",k_city:"Shanghai",k_company:"Gamma"},
        {"link":"https://x/d",k_title:"Embedded assistant",k_city:"Shanghai",k_company:"Gamma"}]}
    (state/"candidate_plan.json").write_text(json.dumps(plan),encoding="utf-8")
    test_state=runs/"test-b"/"state"; test_state.mkdir(parents=True)
    (test_state/"candidate_plan.json").write_text(json.dumps({"mode":"test","jobs":[{"link":"https://x/test"}]}),encoding="utf-8")
    with (post/"crawled.csv").open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.writer(f);w.writerow(["link",k_title]);w.writerow(["https://x/crawl-only","Embedded"])
    jobs,summary=review.collect_missed_jobs({},post,ledger,runs)
    assert {row["link"] for row in jobs}=={"https://x/c","https://x/d"}
    assert next(row for row in jobs if row["link"]=="https://x/c")["_previous_status"]=="error"
    assert summary["production_plans"]==1
    assert summary["previously_attempted_candidates"]==1

def test_selection_ranges_and_deselect():
    selected=set();page=list(range(1,6))
    review.parse_page_selection("1-3,5",page,selected);assert selected=={1,2,3,5}
    review.parse_page_selection("!2",page,selected);assert selected=={1,3,5}
    review.parse_page_selection("A",page,selected);assert selected=={1,2,3,4,5}


def test_closed_and_ambiguous_jobs_are_not_retryable():
    state, recommendation = review.classify_job({"已失效": "是"})
    assert (state, recommendation) == ("已确认关闭", "不投递")
    state, recommendation = review.classify_job({"_previous_error": "未找到立即沟通按钮（可能已经沟通过或岗位失效）"})
    assert (state, recommendation) == ("疑似关闭或已沟通", "人工核查")
    state, recommendation = review.classify_job({"_previous_error": "页面连接已断开"})
    assert (state, recommendation) == ("未确认发出", "可重新尝试")

