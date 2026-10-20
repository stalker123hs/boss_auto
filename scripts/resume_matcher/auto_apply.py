#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
自动投递模块：使用 DrissionPage 控制浏览器完成 BOSS 直聘投递操作

流程：
  1. 打开岗位详情 URL
  2. 点击「立即沟通」
  3. 如有弹窗，关闭弹窗
  4. 点击「继续沟通」
  5. 根据简历和岗位要求生成招呼语，输入聊天框
  6. 点击发送按钮
  7. 发送简历附件（可选，通过「发送图片」上传简历文件）
"""

import os
import sys
import json
import re
import time
import random
import functools
from typing import List, Dict, Any, Optional, Union

from .config import ResumeProfile, OUTPUT_DIR, get_latest_run_dir, create_run_dir
from .paths import apply_log_path
from .utils import ensure_output_dir
from .scoring import hr_activity_rank, hr_activity_sort_key

# 登录状态检测复用 boss_crawler.auth，不在本模块内嵌 copy（旧 copy 缺
# check_login_elements 回退，已漂移）。这里只把 scripts/ 塞进 sys.path，不能顶层
# import boss_crawler.auth：auth → resume_matcher.paths → 本包 __init__ → auto_apply
# 会构成循环引用（auth 在 import 中途拿不到 check_login_status）。真正的 import 延到
# 调用时，见下方 check_login_status 的懒加载包装。
_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

# 计时埋点：stage_timer 在 scripts/ 下（本模块是 scripts/resume_matcher/）。
# 导入失败一律退化成不计时，绝不影响投递。
try:
    import stage_timer
except ImportError:                                          # pragma: no cover
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    try:
        import stage_timer
    except ImportError:
        stage_timer = None

# 浏览器可执行文件定位：本模块是 resume_matcher 包，browser_finder 在 scripts/ 根，
# 同样靠把父目录塞进 sys.path（与上面 stage_timer 同款做法）。找不到也不碍事，
# 找不到浏览器时下方投递代码自然退化成「不显式 set_browser_path」。
_STAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # scripts/
if _STAGE_DIR not in sys.path:
    sys.path.insert(0, _STAGE_DIR)
try:
    from browser_finder import find_chrome_browser
except ImportError:                                          # pragma: no cover
    find_chrome_browser = None

# 尝试导入浏览器自动化库
try:
    from DrissionPage import WebPage, ChromiumOptions
    HAS_DRISSION = True
except ImportError:
    HAS_DRISSION = False


# ==================== XPath 常量 ====================

# 立即沟通按钮
XPATH_START_CHAT = (
    "//div[@class='btn-container']"
    "//a[@class='btn btn-startchat']"
    "[contains(text(),'立即沟通')]"
)

# BOSS 页面会随版本、AB 实验改变 class；保留精确 class 选择器作为首选，
# 同时用可见文字作回退，避免把有效的「立即沟通」误判为无法投递。
XPATH_START_CHAT_BY_TEXT = (
    "//*[self::a or self::button]"
    "[contains(normalize-space(.),'立即沟通')]"
)

# 继续沟通按钮（点击「立即沟通」后可能出现）
XPATH_CONTINUE_CHAT = (
    "//div[@class='btn-container']"
    "//a[@class='btn btn-startchat']"
    "[contains(text(),'继续沟通')]"
)

# 弹窗关闭按钮
XPATH_POPUP_CLOSE = "//i[@class='icon-close']"

# 聊天输入框
XPATH_CHAT_INPUT = "//div[@id='chat-input']"

# 发送按钮
XPATH_SEND_BUTTON = "//button[@type='send']"

# 备用：投递简历按钮
XPATH_DELIVER_RESUME = "//div[@class='btn-container']//a[contains(text(),'投递简历')]"

# 发送图片按钮（聊天工具栏）
XPATH_SEND_IMAGE = "//div[@aria-label='发送图片']"

# 图片上传后的 file input
CSS_FILE_INPUT = "css:input[type='file']"

# 备用：发送简历按钮（聊天面板中）
XPATH_SEND_RESUME = "//span[contains(text(),'发简历')]"


class ManualPlatformPrompt(Exception):
    """A platform warning requires the user's review; automation must stop here."""


class ManualPauseCounter:
    """Counts successful contacts in one process launch; a new instance starts at zero."""

    def __init__(self, thresholds):
        self.thresholds = sorted({int(n) for n in thresholds if int(n) > 0})
        self.applied = 0
        self.approved_through = 0

    def record(self, result):
        # A verified greeting is a completed contact even if resume attachment delivery
        # later becomes partial; that still consumes the platform's contact allowance.
        if result.get('status') == 'applied' or result.get('greeting_verified') is True:
            self.applied += 1

    def pending_threshold(self):
        return next((n for n in self.thresholds
                     if n <= self.applied and n > self.approved_through), None)

    def approve(self, threshold):
        self.approved_through = max(self.approved_through, int(threshold))

    def reached(self, limit):
        return bool(limit and self.applied >= int(limit))


# BOSS 直聘消息列表的预览框只显示前 15 个字（含标点）。HR 是在一屏几十条未读里扫
# 这 15 个字决定点不点开，所以它是标题、不是开场白 —— 「您好，我是XXX」开头等于把
# 整个预览框让给客套话，HR 划过去看不到任何有效信息。
#
# 分场景的前 15 字公式（实习看到岗时间、社招看成果数字、校招看学校奖项）在
# prompts/greeting.st 里，那是 AI 模式的口径。这里只做模板模式能做的那件机械事：
# 把事实顶到最前面，问候语挪到 15 字之后。改口径时两处一起改。
PREVIEW_LEN = 15

# 出现在开头就等于浪费预览框的词。
_WASTED_OPENERS = ('您好', '你好', '我是', '我对', '还招', '虽然', '尊敬的', '贵公司', '贵司')


def preview(text: str) -> str:
    """消息在 HR 列表里露出的那一截。"""
    return (text or '').strip().replace('\n', '')[:PREVIEW_LEN]


def has_wasted_preview(text: str) -> bool:
    """前 15 个字是不是被客套话占掉了。自检用，也给生成出来的招呼语做校验。"""
    return preview(text).startswith(_WASTED_OPENERS)


def _lead(profile: ResumeProfile, key_skills: List[str]) -> str:
    """拼前 15 个字：只用简历里确有的事实，按场景把最硬的一条顶到最前面。

    到岗日期 / 可实习时长 / 每周出勤这三样，是对方会照着安排工位和排期的**承诺**，
    简历没写就不能替用户猜一个 —— 猜错了是失信，不是文案问题。所以只在
    basic_info.availability 里明确给了才用，否则退到年限/学校/技能的公式。
    """
    avail = (profile.basic_info or {}).get('availability')
    if not isinstance(avail, dict):
        avail = {}
    can_start = str(avail.get('can_start') or '').strip()
    duration = str(avail.get('duration') or '').strip()
    days = str(avail.get('days_per_week') or '').strip()

    edu = profile.education or {}
    school = str(edu.get('school') or '').strip()
    degree = str(edu.get('degree') or '').strip()
    grad = str(edu.get('graduation_year') or '').strip()
    top = key_skills[0] if key_skills else ''

    # `or 0` 而非 get 的默认值：应届简历里 total_years 是显式 null（见本文件 142 行）
    try:
        years = int(float(profile.experience.get('total_years') or 0))
    except (TypeError, ValueError):
        years = 0

    # 实习岗：到岗时间 > 时长/出勤 > 学校。实习生技能大多差不多，HR 最怕招到随时
    # 跑路的人，谁能最快补上缺口谁占先 —— 学校只在极好时才值得占这 15 个字。
    if can_start:
        return f"【{can_start}到岗】{school or degree}"
    if duration or days:
        parts = []
        if duration:
            parts.append(f"可实习{duration}")
        if days:
            parts.append(f"周{days}")
        return "/".join(parts) + (f" {school}" if school and len("/".join(parts)) < 10 else "")

    if years > 0:                                  # 社招：年限 + 最匹配的那项技能
        lead = f"{years}年{top}经验" if top else f"{years}年工作经验"
    elif grad and school:                          # 校招：届别 + 学校学历
        lead = f"{grad[-2:]}届{school}{degree}"
    elif top:                                      # 兜底：技能顶上去，也比「您好」强
        lead = f"{top}方向"
    else:
        return ""

    # 预览框还有空位就再塞一项技能，但必须整个塞得进 15 个字 —— 被截断的半个技能名
    # （「/Pyt」）比留白更难读，那还不如让「，应聘「」自然占掉尾巴。
    if top and top not in lead and len(lead) + 1 + len(top) <= PREVIEW_LEN:
        lead = f"{lead}/{top}"
    return lead


def generate_greeting(
    profile: Optional[ResumeProfile] = None,
    job: Optional[Dict[str, Any]] = None,
    custom_greeting: Optional[str] = None
) -> str:
    """
    生成个性化招呼语。

    优先使用 custom_greeting（调用方提供的固定文本），
    其次根据 profile + job 模板生成。

    Args:
        profile: 简历结构化信息
        job: 岗位字典
        custom_greeting: 调用方提供的自定义招呼语

    Returns:
        招呼语字符串
    """
    if custom_greeting:
        return custom_greeting

    if profile is None or job is None:
        return _default_greeting(job)

    # 从 profile 和 job 中提取信息生成模板招呼语
    name = profile.basic_info.get('name', '')
    position = job.get('职位', '')
    company = job.get('公司', '')

    # 提取核心技能匹配
    # `or []` 逐项兜底：某类技能为空时值是 null 而非缺键，get 的默认值不生效，
    # None + list 直接 TypeError 带崩招呼语生成（同本文件 128 行的坑）
    skills = profile.skills or {}
    all_skills = (
        (skills.get('programming') or [])
        + (skills.get('frameworks') or [])
        + (skills.get('tools') or [])
        + (skills.get('other') or [])
    )

    # 提取关键技能词（取前 5 个最有辨识度的）
    key_skills = _pick_key_skills(all_skills, job)

    # 提取相关项目经验
    # 只看 projects：experience.companies 的 name 是公司名，
    # 套进下面「曾主导 XX」的句式会变成病句
    relevant_project = _pick_relevant_project(profile.projects, job)

    # 学历
    education = profile.education
    degree = education.get('degree', '')
    major = education.get('major', '')
    school = education.get('school', '')

    # 经验年限 —— 用 `or` 兜底：应届简历里 total_years 是 null 而非缺键，
    # get 的默认值不生效，`None > 0` 直接 TypeError（同 scoring.py:674 的坑）
    total_years = profile.experience.get('total_years') or 0
    exp_desc = f"{total_years}年" if total_years > 0 else "实习"

    # 构建招呼语
    lines = []

    # 开头 —— 事实顶到最前面，问候语跟在后面。顺序是有意的：这一行的前 15 个字
    # 就是 HR 在列表里唯一看得到的东西。
    lead = _lead(profile, key_skills)
    if lead:
        lines.append(f"{lead}，应聘「{position}」。您好，我是{name}。")
    else:
        lines.append(f"您好，我是{name}，应聘贵公司「{position}」。")

    # 学历 + 专业
    lines.append(f"我毕业于{school}，{degree}学历（{major}专业），有{exp_desc}开发经验。")

    # 技能匹配 —— 点名 JD 里的关键词，证明不是海投
    if key_skills:
        skills_str = "、".join(key_skills[:6])
        lines.append(f"技术栈方面，熟练掌握{skills_str}，与岗位要求高度匹配。")

    # 项目亮点
    if relevant_project:
        lines.append(f"曾主导{relevant_project}，具备实际落地经验。")

    # 收尾 —— 委婉请求发简历，留沟通空间。不用「期待您的回复！」：那句放在任何
    # 岗位上都成立，等于没说。
    lines.append("已附简历，方便看一下吗？")

    greeting = "\n".join(lines)

    # 如果超长则精简
    if len(greeting) > 500:
        greeting = _compact_greeting(greeting, name, position, key_skills)

    return greeting


def _pick_key_skills(all_skills: List[str], job: Dict[str, Any]) -> List[str]:
    """从技能列表中选出与岗位最相关的关键词

    排序依据（依次）：
      1. 技能标签命中 > 岗位职责正文命中 > 未命中
      2. 在 JD 中首次出现的位置越靠前，说明越是核心要求
      3. 简历中的原始顺序（稳定排序，简历自己的优先级）
    """
    tags = job.get('技能标签', '') or ''
    body = job.get('岗位要求和职责', '') or ''
    tags_lower = tags.lower()
    jd_lower = (tags + body).lower()

    scored = []
    for idx, skill in enumerate(all_skills):
        if not skill or len(skill) < 2:
            continue
        if len(skill) > 15 or skill.startswith("传统") or "开发" in skill:
            continue

        s = skill.lower()
        if s in tags_lower:
            score = 200          # 招聘方自己打的标签，最能代表岗位画像
        elif s in jd_lower:
            score = 100
        else:
            score = 0

        # 未命中的技能没有位置信息，排到最后
        pos = jd_lower.find(s) if score else len(jd_lower)
        scored.append((-score, pos, idx, skill))

    scored.sort()

    # 去重优先，取前 8 个
    seen = set()
    result = []
    for _, _, _, s in scored:
        if s.lower() not in seen:
            seen.add(s.lower())
            result.append(s)
        if len(result) >= 8:
            break

    return result


_TOKEN_RE = re.compile(r'[A-Za-z][A-Za-z0-9+#.]*|[一-龥]{2,}')


def _tokenize(text: str) -> List[str]:
    """把中英文混排文本切成可匹配的词元

    英文按连续字母数字串切；中文没有分词库，用 2-4 字滑动窗口，
    足以覆盖「向量检索」「推荐系统」这类技术名词。
    """
    tokens = []
    for m in _TOKEN_RE.finditer(text or ''):
        t = m.group()
        if t[0].isascii():
            if len(t) >= 2:
                tokens.append(t.lower())
        else:
            for n in (4, 3, 2):
                for i in range(len(t) - n + 1):
                    tokens.append(t[i:i + n])
    return tokens


def _pick_relevant_project(projects: List[Dict], job: Dict[str, Any]) -> Optional[str]:
    """选出与岗位最相关的项目名

    按命中的**不同词元数**打分，技术栈命中权重更高（技术栈是硬匹配，
    项目描述里的词更可能是巧合）。
    """
    jd = (
        (job.get('技能标签', '') or '')
        + (job.get('岗位要求和职责', '') or '')
        + (job.get('职位', '') or '')
    )
    jd_lower = jd.lower()

    best = None
    best_score = 0

    for proj in projects:
        if not isinstance(proj, dict):
            continue
        name = proj.get('name', '') or ''
        desc = proj.get('description', '') or ''
        tech_stack = proj.get('tech_stack', []) or []

        score = 0
        for token in set(_tokenize(name + ' ' + desc)):
            if token in jd_lower:
                score += 1
        for tech in tech_stack:
            if tech and str(tech).lower() in jd_lower:
                score += 3

        if score > best_score:
            best_score = score
            best = name

    return best


def _default_greeting(job: Optional[Dict[str, Any]] = None) -> str:
    """默认招呼语（连 profile 都没有时的兜底）

    没有简历事实可用，就把岗位名顶到最前面 —— 至少预览框里露出的是「应聘XXX」，
    HR 知道你在说哪个岗，而不是一句 15 个字的客套话。
    """
    position = job.get('职位', '该岗位') if job else '该岗位'
    return (
        f"应聘「{position}」，技术背景与岗位要求匹配。"
        f"您好，我看到贵公司这个岗位，已附简历，方便看一下吗？"
    )


def _compact_greeting(greeting: str, name: str, position: str, skills: List[str]) -> str:
    """精简招呼语到 300 字以内

    同样保持事实前置。原来这里硬编码了「AI应用项目落地经验」—— 那是从某次实跑里
    带出来的领域词，投任何非 AI 岗都会变成一句假话，已去掉。
    """
    skills_str = "、".join(skills[:4])
    head = f"熟练{skills_str}" if skills_str else "技术栈匹配"
    return (
        f"{head}，应聘「{position}」，与岗位要求高度匹配。"
        f"您好，我是{name}，已附简历，方便看一下吗？"
    )


def send_resume_attachment(
    dp: WebPage,
    resume_file_path: str,
    timeout: float = 10.0
) -> bool:
    """
    在聊天窗口中发送简历附件（图片/文件）。

    策略（按优先级尝试）：
        A. 先查找页面所有隐藏 <input type="file">，对匹配图片/PDF 的直接 input()
           → 绕过原生文件对话框
        B. 如果找不到合适的 file input，通过 JS 注入一个来接收文件
        C. 最后兜底：点击「发送图片」div + CDP 拦截文件对话框

    Args:
        dp: DrissionPage WebPage 实例（需已在聊天页面）
        resume_file_path: 简历文件路径（支持图片、PDF等）
        timeout: 操作超时

    Returns:
        是否成功发送
    """
    if not resume_file_path or not os.path.exists(resume_file_path):
        print(f"  ⚠ 简历文件不存在: {resume_file_path}")
        return False

    print(f"  📎 准备发送简历附件: {os.path.basename(resume_file_path)}")

    ext = os.path.splitext(resume_file_path)[1].lower()
    is_image = ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')

    # ── 送达校验基线：记录发送前的图片消息数量 ──
    # ⚠ 图片消息在 innerText 中不可见，只能数 img.message-image 元素。
    #   基线为 -1 表示读不到消息列表，此时无法校验，退化为「上传即返回」。
    baseline = _count_chat_images(dp) if is_image else -1
    can_verify = is_image and baseline >= 0
    if can_verify:
        print(f"  📐 校验基线: 当前 {baseline} 张图片消息")

    def _verify_sent() -> bool:
        """确认图片消息数量增加了 —— 这是唯一可信的送达证据"""
        if not can_verify:
            return True
        for _ in range(int(timeout)):
            time.sleep(1)
            if _count_chat_images(dp) > baseline:
                return True
        return False

    # ── 策略 A：直接找隐藏 file input（不点击按钮，绕过原生对话框）──
    # ⚠ 每次只投递一个 input 并立即校验，成功即停 ——
    #   历史上「往所有 file input 都塞文件」会导致重复发送。
    file_inputs = _find_all_file_inputs(dp)
    if file_inputs:
        print(f"  🔍 找到 {len(file_inputs)} 个 file input，逐个尝试并校验...")
        ordered = []
        fallback = []
        for fi in file_inputs:
            try:
                accept = (fi.attr('accept') or '').lower()
            except Exception:
                accept = ''
            matched = (
                (is_image and 'image' in accept)
                or (not is_image and ext.lstrip('.') in accept)
            )
            (ordered if matched else fallback).append((fi, accept))

        for fi, accept in ordered + fallback:
            try:
                fi.input(resume_file_path)
            except Exception as e:
                print(f"  ⚠ input(accept={accept[:30]!r}) 失败: {e}")
                continue

            if _verify_sent():
                after = _count_chat_images(dp) if can_verify else -1
                suffix = f"，图片消息 {baseline} → {after}" if can_verify else "（无法校验）"
                print(f"  ✅ 简历附件已送达 (策略A: file input){suffix}")
                return True
            print(f"  ⚠ 该 input 未产生新图片消息，尝试下一个")

    # ── 策略 B：JS 注入 file input → 设置文件 → 触发 change 事件 ──
    print(f"  🔄 尝试策略B: JS 注入 file input...")
    if _upload_via_js_injection(dp, resume_file_path):
        if _verify_sent():
            print(f"  ✅ 简历附件已送达 (策略B)")
            return True
        print(f"  ⚠ 策略B 未产生新图片消息")

    # ── 策略 C：点击「发送图片」div（兜底，会弹出原生对话框需手动处理）──
    print(f"  🔄 尝试策略C: 点击「发送图片」按钮...")
    img_btn = _find_element(dp, [
        XPATH_SEND_IMAGE,
        "css:[aria-label='发送图片']",
        "css:.icon-send-image",
    ], timeout=3)

    if not img_btn:
        print(f"  ⚠ 未找到「发送图片」按钮")
        return False

    try:
        # 点击前先尝试用 CDP 拦截文件对话框
        _auto_accept_file_dialog(dp, resume_file_path)
        img_btn.click()
        time.sleep(3)
        if _verify_sent():
            print(f"  ✅ 简历附件已送达 (策略C)")
            return True
        print(f"  ⚠ 策略C: 未确认送达，如弹出对话框请手动选择文件")
        return False
    except Exception as e:
        print(f"  ❌ 策略C失败: {e}")
        return False


def _find_all_file_inputs(dp: WebPage) -> list:
    """查找页面上所有 <input type='file'>（包括隐藏的），返回元素列表"""
    try:
        # 用 JS 查找所有 file input（绕过 Selenium 不可见限制）
        result = dp.run_js("""
            const inputs = document.querySelectorAll('input[type="file"]');
            return inputs.length;
        """)
        count = int(result) if result else 0

        if count == 0:
            return []

        # 逐个获取
        inputs = []
        for i in range(count):
            try:
                el = dp.ele(f'css:input[type="file"]', index=i + 1, timeout=1)
                if el:
                    inputs.append(el)
            except Exception:
                continue
        return inputs
    except Exception:
        return []


def _upload_via_js_injection(dp: WebPage, file_path: str) -> bool:
    """
    通过 JS 注入一个 file input 元素，
    用 DrissionPage 设置文件值，再派发 change 事件触发上传。
    """
    try:
        # 1. 注入隐藏的 file input 到 body
        injected_id = '__auto_apply_file_input__'
        dp.run_js(f"""
            if (!document.getElementById('{injected_id}')) {{
                const input = document.createElement('input');
                input.type = 'file';
                input.id = '{injected_id}';
                input.style.display = 'none';
                input.accept = 'image/*,.pdf';
                document.body.appendChild(input);

                // 监听 change：找到原生的上传组件并派发文件
                input.addEventListener('change', (e) => {{
                    const file = e.target.files[0];
                    if (!file) return;
                    // 尝试找到页面上的 paste/drop 区域并派发 DataTransfer
                    const dt = new DataTransfer();
                    dt.items.add(file);
                    const pasteEvent = new ClipboardEvent('paste', {{
                        clipboardData: dt, bubbles: true, cancelable: true
                    }});
                    document.dispatchEvent(pasteEvent);
                }});
            }}
        """)
        time.sleep(0.5)

        # 2. 找到注入的 input 并设置文件
        injected = dp.ele(f'#{injected_id}', timeout=2)
        if injected:
            injected.input(file_path)
            time.sleep(2)
            print(f"  ✅ 简历附件已上传 (策略B: JS 注入)")
            return True
    except Exception as e:
        print(f"  ⚠ 策略B异常: {e}")

    return False


def _resume_message_count(dp: WebPage) -> int:
    """统计当前聊天记录里的附件简历卡片；完全排除弹窗和导航栏。"""
    try:
        value = dp.run_js("""
            const root = document.querySelector(
              '[class*="chat-message-list"], [class*="message-list"], .chat-record, .chat-message'
            );
            if (!root) return -1;
            const nodes = Array.from(root.querySelectorAll('*')).filter((el) => {
              const text = (el.innerText || '').replace(/\\s+/g, '');
              if (!text.includes('点击预览附件简历')) return false;
              return !Array.from(el.children).some((child) =>
                (child.innerText || '').replace(/\\s+/g, '').includes('点击预览附件简历'));
            });
            return nodes.length;
        """)
        return int(value) if value is not None else -1
    except Exception:
        return -1


def send_existing_resume_attachment(
    dp: WebPage,
    resume_index: int = 0,
    timeout: float = 12.0,
) -> bool:
    return send_existing_resume_attachment_status(dp, resume_index, timeout) == 'sent'


def send_existing_resume_attachment_status(
    dp: WebPage,
    resume_index: int = 0,
    timeout: float = 12.0,
) -> str:
    """从 BOSS 账号已有的附件简历中选择一份并发送。

    不读取或改写简历内容。按钮和弹窗都按语义选择；如果页面结构无法确认，
    返回 False，由调用方记为部分成功，避免把未送达误报成成功。
    """
    baseline = _resume_message_count(dp)
    button = None
    for _ in range(4):
        button = _find_element(dp, [
            "css:[ka='geek_chat_send_resume']",
            "css:[aria-label*='简历']",
            "css:[title*='简历']",
            "xpath://div[contains(@class,'toolbar-btn') and normalize-space(.)='发简历']",
            "xpath://span[contains(normalize-space(.),'发简历')]",
            "xpath://div[contains(@class,'toolbar') and (@aria-label='发送简历' or @title='发送简历')]",
        ], timeout=2)
        if button:
            break
        time.sleep(2)
    if not button:
        print("  ⚠ 未找到聊天工具栏里的简历按钮")
        return 'failed'

    try:
        button_class = (button.attr('class') or '').lower()
        button_hint = (button.attr('aria-label') or '').strip()
    except Exception:
        button_class, button_hint = '', ''
    if 'unable' in button_class or '双方回复后可用' in button_hint:
        print("  ℹ 平台暂不允许发送附件简历：双方回复后才可用")
        return 'pending_reply'

    try:
        button.click()
        time.sleep(1)
    except Exception as exc:
        print(f"  ⚠ 打开附件简历选择框失败: {exc}")
        return 'failed'

    # 有的页面直接弹出确认框，有的页面先让用户选择账号里的一份附件简历。
    try:
        items = dp.eles('css:.resume-list li', timeout=2) or []
    except Exception:
        items = []
    if items:
        index = min(max(int(resume_index), 0), len(items) - 1)
        try:
            items[index].click()
            time.sleep(0.5)
        except Exception as exc:
            print(f"  ⚠ 选择第 {index + 1} 份附件简历失败: {exc}")
            return 'failed'

    confirm = _find_element(dp, [
        'css:.panel-resume .btn-sure-v2',
        'css:.resume-list .btn-confirm',
        "xpath://button[contains(normalize-space(.),'发送')]",
        "xpath://button[contains(normalize-space(.),'确认')]",
        "xpath://a[contains(@class,'btn') and contains(normalize-space(.),'发送')]",
    ], timeout=3)
    if confirm:
        try:
            confirm.click()
        except Exception as exc:
            print(f"  ⚠ 确认发送附件简历失败: {exc}")
            return 'failed'

    # 以聊天记录新增附件简历气泡或明确成功提示作为送达证据。
    end = time.time() + timeout
    while time.time() < end:
        now = _resume_message_count(dp)
        if baseline >= 0 and now > baseline:
            return 'sent'
        try:
            success = dp.run_js("""
                return Array.from(document.querySelectorAll('*')).some((el) => {
                  const s = getComputedStyle(el), r = el.getBoundingClientRect();
                  const visible = s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
                  const text = (el.innerText || '').replace(/\\s+/g, '');
                  return visible && (text === '简历已发送' || text === '附件简历发送成功');
                });
            """)
            if bool(success):
                return 'sent'
        except Exception:
            pass
        time.sleep(1)
    # 已明确点过确认，但 BOSS 可能只先向对方发送“查看简历”的请求，
    # 要等对方同意后才显示附件卡片。这个状态不应误报为附件已送达。
    if confirm:
        print("  ℹ 已确认发送简历请求，等待对方确认")
        return 'requested'
    return 'failed'


def _auto_accept_file_dialog(dp: WebPage, file_path: str) -> None:
    """通过 CDP 设置文件对话框自动选择文件"""
    try:
        cdp = dp._control_browser
        if cdp:
            cdp.Page.setInterceptFileChooserDialog({'enabled': True})
            # 注册回调：当对话框打开时自动选择文件
            # 注意：这是预先设置，下次文件对话框打开时自动生效
            dp.set.upload_files(file_path)
    except Exception:
        pass  # 静默失败，回退到手动


def _ensure_login(dp: WebPage, max_wait: int = 300) -> bool:
    """
    确保浏览器已登录 BOSS 直聘。

    1. 先快速检测是否已登录
    2. 未登录则进入轮询等待（每 3s 刷新），最长 max_wait 秒

    Returns:
        True 已登录，False 超时
    """
    # 先导航到首页检查
    dp.get('https://www.zhipin.com/web/geek/recommend')
    time.sleep(2)

    if check_login_status(dp):
        print(f"  [LOGIN_OK] 检测到已登录")
        return True

    print(f"  [需要登录] 请在浏览器窗口中完成登录...")
    print(f"  [需要登录] 请完成登录；脚本只检测状态，不会反复刷新页面。")

    elapsed = 0
    interval = 3
    while elapsed < max_wait:
        mins = elapsed // 60
        secs = elapsed % 60
        if elapsed > 0:
            print(f"  [等待登录] {mins}m{secs}s / {max_wait//60}m ...")
        time.sleep(interval)
        elapsed += interval

        if check_login_status(dp):
            print(f"  [LOGIN_OK] 检测到登录成功！（耗时 {elapsed}s）")
            return True

    print(f"  [LOGIN_FAIL] 登录超时（{max_wait}s）")
    return False


def check_login_status(page) -> bool:
    """检测 BOSS 直聘登录状态，委托给 boss_crawler.auth（与 crawler 共用同一份判定）。

    模块加载期 auto_apply ←→ boss_crawler.auth 循环引用（auth 经 resume_matcher.paths
    触发本包 __init__ 再回到 auto_apply），所以这里懒加载，第一次调用时才 import。
    """
    from boss_crawler.auth import check_login_status as _impl
    return _impl(page)


def _security_challenge_present(dp: WebPage) -> bool:
    """只检测安全验证，不尝试关闭、刷新或绕过。"""
    try:
        url = (dp.url or '').lower()
        if 'security-check' in url or '_security_check' in url:
            return True
    except Exception:
        pass
    try:
        return bool(dp.eles(
            'css:.geetest_panel, css:.geetest_panel_box, css:[class*="captcha"]',
            timeout=1,
        ))
    except Exception:
        return False


def _wait_for_security_clear(dp: WebPage, timeout: int = 600) -> bool:
    """验证出现时暂停，等待用户在当前浏览器手动完成。"""
    if not _security_challenge_present(dp):
        return True
    print(f"  [安全验证] 请在浏览器中手动完成，脚本暂停且不会刷新（最多 {timeout}s）")
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(2)
        if not _security_challenge_present(dp):
            print("  [安全验证] 已完成，继续")
            return True
    print("  [安全验证] 等待超时，本轮停止")
    return False


def auto_apply_jobs(*args, **kwargs):
    """`_auto_apply_jobs_impl` 的计时包装。

    只有显式传了 output_dir 才计时：impl 在 output_dir 为空时会自行定位最近运行目录
    或新建一个，在这里重复那套逻辑有可能多建一个空目录 —— 宁可不计时，也不让埋点
    产生副作用。

    崩了也要留下耗时：stage_timer.stage 会照抛异常但落盘 status=error。投递是整个
    流程里唯一不可逆的一步，「投到第几个崩的、崩之前跑了多久」是复盘时最要紧的信息。
    """
    run_dir = kwargs.get('output_dir')
    if run_dir is None and len(args) >= 7:
        run_dir = args[6]                    # 第 7 个位置参数就是 output_dir
    if not run_dir or stage_timer is None:
        return _auto_apply_jobs_impl(*args, **kwargs)
    with stage_timer.stage(run_dir, 'apply'):
        return _auto_apply_jobs_impl(*args, **kwargs)


def _auto_apply_jobs_impl(
    qualified_jobs: List[Dict[str, Any]],
    _profile: ResumeProfile,
    max_applications: int = 10,
    headless: bool = False,
    greetings: Optional[Dict[str, str]] = None,
    resume_file_path: Optional[Union[str, Dict[str, str]]] = None,
    output_dir: Optional[str] = None,
    use_existing_resume: bool = False,
    resume_index: int = 0,
    min_interval: float = 12.0,
    max_interval: float = 25.0,
    stop_on_partial: bool = False,
    start_index: int = 0,
    previous_results: Optional[List[Dict[str, Any]]] = None,
    manual_pause_thresholds: Optional[List[int]] = None,
    hard_stop_threshold: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    自动投递简历到匹配岗位。

    流程：
        1. 打开岗位详情页
        2. 点击「立即沟通」
        3. 关闭弹窗（如有）
        4. 点击「继续沟通」
        5. 输入招呼语 → 发送
        6. 发送简历附件（如提供 resume_file_path）

    Args:
        qualified_jobs: 符合条件的岗位列表
        _profile: 简历结构化信息（用于生成招呼语）
        max_applications: 最大投递数量
        headless: 是否无头模式
        greetings: 预生成的招呼语字典 {job_link: greeting_text}，优先使用
        resume_file_path: 简历文件路径（图片/PDF），用于「发送图片」上传附件。
                    传字符串则整批共用一个文件；传 {job_link: path} 则按岗位
                    各发各的（分岗位定制简历走这条）。
        output_dir: 输出目录（投递日志保存位置）。
                    默认自动定位最近运行目录或创建新目录。

    Returns:
        投递结果列表
    """
    # Windows 控制台默认 GBK，打印 emoji 会抛 UnicodeEncodeError；统一重配为 UTF-8。
    # check_artifacts.py / write_application_md.py 都这么干，本模块此前漏了，导致投递在
    # 打印 emoji 时崩掉一次。reconfigure 失败（如非标准 stdout）不阻断投递。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError, OSError):
            pass

    if not HAS_DRISSION:
        print("错误: DrissionPage 未安装，无法自动投递。pip install DrissionPage")
        return []

    if not qualified_jobs:
        print("没有可投递的岗位")
        return []

    # 按优先级排序：HR 活跃度优先，匹配分次之
    #
    # 活跃度作主键是有意的：到这一步的岗位都已通过硬门禁、又经用户在投递范围那道门禁
    # 手工确认，都是值得投的 —— 那么先投会回复的那个。未采集活跃度（无 -d）时全部折成 -1，
    # 排序自动退化成纯匹配分排序。
    #
    # 注意 max_applications 的截断发生在排序之后：当选中岗位数超过上限时，
    # 活跃度高但匹配分低的岗位会挤掉匹配分高但 HR 不活跃的岗位。
    # 若要改成匹配分优先、活跃度只做同分裁决，把前两个键对调即可。
    sorted_jobs = sorted(
        qualified_jobs,
        key=lambda j: (
            -hr_activity_sort_key(j),
            -j.get('match_score', 0),
            j.get('company', '')
        )
    )
    # max_applications 表示“确认成功数”上限。候选中遇到失效、已沟通或页面异常时
    # 继续看下一家，测试模式才能真正以 10 家成功为目标，而不是只尝试 10 条链接。
    jobs_to_apply = sorted_jobs
    start_index = max(0, min(int(start_index or 0), len(jobs_to_apply)))

    ranked = [hr_activity_rank(j) for j in jobs_to_apply]
    if any(r is not None for r in ranked):
        hr_order = '活跃度优先'
    else:
        hr_order = '活跃度未采集，按匹配分排序'

    print(f"\n{'='*60}")
    print(f"  🤖 自动投递模式")
    target_text = '全部候选' if not max_applications or max_applications <= 0 else f'{max_applications} 家成功'
    print(f"  目标岗位: {target_text}（候选 {len(jobs_to_apply)} 个）")
    if start_index:
        print(f"  断点续跑: 从第 {start_index + 1} 个岗位继续")
    print(f"  投递顺序: {hr_order}")
    print(f"  招呼语来源: {'固定模板（零 AI）' if greetings else '本地模板自动生成'}")
    if manual_pause_thresholds or hard_stop_threshold:
        print(f"  平台保护: 120 温馨提示自动暂停；{hard_stop_threshold or '无'} 家强制停止")
    print(f"{'='*60}")

    # 配置浏览器（复用 Chrome 用户数据，保留登录态）。
    # 浏览器可执行文件走 browser_finder 分层探测（env → PATH → 注册表 → 拼路径），
    # 探测不到就跳过 set_browser_path，交给 DrissionPage 默认逻辑。
    co = ChromiumOptions()
    if find_chrome_browser:
        chrome_path = find_chrome_browser()
        if chrome_path:
            co.set_browser_path(chrome_path)

    # 持久化 Chrome Profile（复用登录状态，位于 assets/chrome_user_data）
    user_data_dir = os.path.join(OUTPUT_DIR, 'chrome_user_data')
    os.makedirs(user_data_dir, exist_ok=True)
    co.set_argument(f'--user-data-dir={user_data_dir}')

    if headless:
        co.headless(True)

    dp = WebPage(chromium_options=co)
    results = list(previous_results or [])
    interrupted = False
    user_paused = False
    manual_pause_thresholds = sorted({int(n) for n in (manual_pause_thresholds or []) if int(n) > 0})
    hard_stop_threshold = max(0, int(hard_stop_threshold or 0))
    # New object on each process launch: previous_results from the checkpoint never enter this count.
    pause_counter = ManualPauseCounter(manual_pause_thresholds)

    if output_dir is None:
        output_dir = get_latest_run_dir() or create_run_dir()
    log_path = apply_log_path(output_dir)
    checkpoint_path = os.path.join(output_dir, 'state', 'apply_checkpoint.json')
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def save_progress(next_index: int, complete: bool = False):
        """每处理一条就原子写盘，断电/关闭窗口后仍可续跑。"""
        data = {
            'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'next_index': next_index,
            'total_jobs': len(jobs_to_apply),
            'complete': complete,
            # Informational only; the value is intentionally not restored on the next launch.
            'manual_gate_approved_through': pause_counter.approved_through,
            'results': results,
        }
        temp = checkpoint_path + '.tmp'
        with open(temp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp, checkpoint_path)

    def finish_result(result: Dict[str, Any], next_index: int):
        results.append(result)
        save_progress(next_index, complete=False)

    def wait_for_manual_gate(next_index: int) -> bool:
        """Pause at requested volume milestones; never auto-accept a BOSS warning."""
        nonlocal interrupted, user_paused
        threshold = pause_counter.pending_threshold()
        while threshold is not None:
            confirmed = pause_counter.applied
            print(f"\n  ⏸ 已确认联系 {confirmed} 家，达到人工检查点 {threshold} 家。")
            print("  请先检查 BOSS 页面；若有投递频率、次数或安全提示，请阅读并自行决定处理。")
            try:
                if not sys.stdin or not sys.stdin.isatty():
                    print("  当前没有可交互终端，已暂停并保存进度。")
                    user_paused = True
                    interrupted = True
                    save_progress(next_index, complete=False)
                    return False
                answer = input("  确认继续投递请输入 Y；停止请输入 N：").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = 'n'
            if answer not in ('y', 'yes', '是'):
                print("  已按人工选择停止；进度已保存。"); user_paused = True; interrupted = True
                save_progress(next_index, complete=False)
                return False
            # Do not navigate away from a BOSS 120/150, frequency, or safety dialog.
            # The person must clear it in the browser before this process can continue.
            try:
                _wait_for_platform_prompt_clear(dp)
            except ManualPlatformPrompt as exc:
                print(f"  ⏸ {exc}；进度已保存。")
                user_paused = True
                interrupted = True
                save_progress(next_index, complete=False)
                return False
            pause_counter.approve(threshold)
            save_progress(next_index, complete=False)
            threshold = pause_counter.pending_threshold()
        return True

    def stop_at_contact_cap(next_index: int) -> bool:
        """Stop at the configured BOSS contact cap; do not offer a bypass."""
        nonlocal interrupted, user_paused
        if not pause_counter.reached(hard_stop_threshold):
            return False
        print(f"\n  🛑 本次已确认联系 {pause_counter.applied} 家，达到 BOSS 联系上限 {hard_stop_threshold} 家。")
        print("  本次运行已停止并保存断点；请等待平台允许后再手动启动。")
        interrupted = True
        user_paused = True
        save_progress(next_index, complete=False)
        return True

    # ── 确保登录 ──
    if not _ensure_login(dp, max_wait=300):
        print("❌ 登录失败，无法继续投递")
        dp.quit()
        return results

    try:
        for i, job in enumerate(jobs_to_apply[start_index:], start=start_index):
            if not wait_for_manual_gate(i):
                break
            if max_applications and max_applications > 0:
                confirmed = sum(1 for item in results if item.get('status') == 'applied')
                if confirmed >= max_applications:
                    print(f"  ✅ 已达到 {max_applications} 家确认成功，停止本轮")
                    break
            position = job.get('职位', job.get('position', '未知'))
            company = job.get('公司', job.get('company', '未知'))
            link = job.get('link', '')

            print(f"\n--- [{i+1}/{len(jobs_to_apply)}] {company} - {position} ---")

            result = {
                'job': job,
                'status': 'unknown',
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'error': None,
                'greeting_used': None
            }

            if not link:
                result['status'] = 'skipped'
                result['error'] = '无岗位链接'
                print(f"  ⏭ 跳过: 无链接")
                finish_result(result, i + 1)
                continue

            try:
                # === 步骤 1：打开岗位详情页 ===
                print(f"  🌐 打开详情页...")
                dp.get(link)
                time.sleep(3)
                if not _wait_for_security_clear(dp):
                    result['status'] = 'error'
                    result['error'] = '安全验证等待超时'
                    finish_result(result, i + 1)
                    break
                # 页面加载时就出现的计数/频率/安全提示，必须先交给用户查看。
                _close_popup(dp)

                # BOSS 已显示「继续沟通」等状态时，代表当前账号此前已联系过该岗位。
                # 此时绝不点击按钮或发送新消息，避免重复打扰招聘方。
                contacted_label = _existing_contact_label(dp)
                if contacted_label:
                    result['status'] = 'skipped'
                    result['error'] = f'页面显示“{contacted_label}”，已沟通过'
                    print(f"  ⏭ 页面显示「{contacted_label}」，已沟通过，跳过")
                    finish_result(result, i + 1)
                    continue

                # === 步骤 2：点击「立即沟通」 ===
                print(f"  🔍 查找「立即沟通」按钮...")
                start_btn = _find_element(dp, [
                    XPATH_START_CHAT,
                    XPATH_START_CHAT_BY_TEXT,
                    XPATH_DELIVER_RESUME,
                    'css:.btn-startchat',
                ])

                if start_btn:
                    start_btn.click()
                    print(f"  ✅ 已点击「立即沟通」")
                    time.sleep(2)
                else:
                    result['status'] = 'skipped'
                    result['error'] = '未找到立即沟通按钮（可能已经沟通过或岗位失效）'
                    print(f"  ⏭ 未找到「立即沟通」，为避免重复联系而跳过")
                    finish_result(result, i + 1)
                    continue

                # === 步骤 3：关闭弹窗（如有） ===
                closed_popup = _close_popup(dp)
                if closed_popup:
                    print(f"  🧹 已关闭弹窗")
                    time.sleep(1)

                # === 步骤 4：点击「继续沟通」 ===
                print(f"  🔍 查找「继续沟通」按钮...")
                continue_btn = _find_element(dp, [
                    XPATH_CONTINUE_CHAT,
                    'css:.btn-startchat',
                ])

                if continue_btn:
                    continue_btn.click()
                    print(f"  ✅ 已点击「继续沟通」")
                    time.sleep(2)

                    # 再次关闭可能出现的弹窗
                    _close_popup(dp)
                else:
                    print(f"  ⚠ 未找到「继续沟通」，等待聊天窗口加载...")
                    # 可能聊天窗口已经在页面中，多等一会儿让它渲染
                    time.sleep(3)
                    _close_popup(dp)

                # 某些历史会话在岗位详情页仍显示“立即沟通”，只有进入聊天页才能看见。
                # 只要已有本人发出的任意一条消息或附件，就是历史投递；不重复发送。
                existing_outgoing = _existing_outgoing_message(dp)
                if existing_outgoing:
                    result['status'] = 'skipped'
                    result['error'] = '聊天页检测到本人已发送消息，历史已沟通'
                    print(f"  ⏭ 聊天页检测到本人已发送消息（{existing_outgoing[:80]}），跳过，不重复发送")
                    finish_result(result, i + 1)
                    continue

                # === 步骤 5：生成并输入招呼语 ===
                greeting = _get_greeting(job, _profile, greetings)
                result['greeting_used'] = greeting

                if greeting and _input_greeting(dp, greeting):
                    print(f"  📝 已输入招呼语")
                    time.sleep(0.5)

                    # === 步骤 6：发送（回车优先）+ 回读聊天记录校验 ===
                    if _click_send(dp, greeting):
                        print(f"  📤 消息已发送（已校验气泡）")
                        result['status'] = 'applied'
                        result['greeting_verified'] = True
                        print(f"  ✅ 投递+招呼完成！")

                        # === 步骤 7：发送简历附件 ===
                        attachment_required = use_existing_resume or bool(resume_file_path)
                        attachment_ok = not attachment_required
                        if use_existing_resume:
                            time.sleep(1)
                            attachment_status = send_existing_resume_attachment_status(
                                dp, resume_index=resume_index
                            )
                            result['attachment_status'] = attachment_status
                            attachment_ok = attachment_status in ('sent', 'requested', 'pending_reply')
                            result['attachment_sent'] = attachment_status == 'sent'
                        else:
                            job_resume = _get_resume_file(job, resume_file_path)
                            if job_resume:
                                time.sleep(1)
                                attachment_ok = send_resume_attachment(dp, job_resume)
                                result['attachment_sent'] = attachment_ok

                        if attachment_required:
                            if result.get('attachment_status') == 'sent':
                                print(f"  ✅ 简历附件已发送（已校验）！")
                            elif result.get('attachment_status') == 'requested':
                                print(f"  ✅ 已发送简历请求，等待对方确认")
                            elif result.get('attachment_status') == 'pending_reply':
                                print(f"  ✅ 已完成投递；附件需等对方回复后补发")
                            else:
                                result['status'] = 'partial'
                                result['error'] = '招呼语已发送，但附件简历未确认送达'
                                print(f"  ⚠ 附件简历未确认送达，本次只记为部分成功")
                    else:
                        result['status'] = 'partial'
                        result['greeting_verified'] = False
                        result['error'] = (
                            '招呼语已输入但聊天记录中未出现消息气泡 —— 未发送成功，'
                            '请在浏览器中手动按回车'
                        )
                        print(f"  ❌ 发送未通过校验，招呼语仍在输入框，请手动按回车")
                else:
                    result['status'] = 'no_chat'
                    result['greeting_verified'] = False
                    result['error'] = '招呼语无法输入聊天框（可能已投递或聊天窗口未开启）'
                    print(f"  ⚠ 无法输入招呼语")

            except ManualPlatformPrompt as e:
                result['status'] = 'paused'
                result['error'] = str(e)
                interrupted = True
                user_paused = True
                print(f"  ⏸ {e}；下次从当前岗位继续。")
                save_progress(i, complete=False)
                break
            except Exception as e:
                result['status'] = 'error'
                result['error'] = str(e)
                print(f"  ❌ 异常: {e}")

            finish_result(result, i + 1)
            pause_counter.record(result)

            if stop_at_contact_cap(i + 1):
                break

            if not wait_for_manual_gate(i + 1):
                break

            if stop_on_partial and result.get('status') == 'partial':
                print("  🛑 测试保护：出现部分成功，立即停止以便修复页面适配")
                break

            if max_applications and max_applications > 0:
                confirmed = sum(1 for item in results if item.get('status') == 'applied')
                if confirmed >= max_applications:
                    print(f"  ✅ 已达到 {max_applications} 家确认成功，停止本轮")
                    break

            # 间隔等待（模拟人类操作，3-8 秒随机）
            if i < len(jobs_to_apply) - 1:
                low = max(0.0, float(min_interval))
                high = max(low, float(max_interval))
                wait = random.uniform(low, high)
                print(f"  ⏳ 等待 {wait:.1f}s...")
                time.sleep(wait)

    except KeyboardInterrupt:
        interrupted = True
        print("\n  ⏸ 已停止；当前进度已保存，下次启动将从断点继续。")
    finally:
        dp.quit()

    # === 保存投递记录 ===
    applied_count = sum(1 for r in results if r['status'] == 'applied')
    partial_count = sum(1 for r in results if r['status'] == 'partial')
    failed_count = sum(1 for r in results if r['status'] in ('error', 'no_chat', 'no_button'))
    skipped_count = sum(1 for r in results if r['status'] == 'skipped')

    log_data = {
        'applied_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'complete': not interrupted,
        'total_attempts': len(results),
        'results': results,
        'summary': {
            'applied': applied_count,
            'partial': partial_count,
            'failed': failed_count,
            'skipped': skipped_count,
        }
    }

    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)
    if not interrupted:
        save_progress(len(jobs_to_apply), complete=True)

    # === 打印汇总 ===
    print(f"\n{'='*60}")
    print(f"  📊 投递汇总")
    print(f"  成功(含招呼): {applied_count}")
    print(f"  部分成功:      {partial_count}")
    print(f"  失败:          {failed_count}")
    print(f"  跳过:          {skipped_count}")
    print(f"  记录已保存:    {log_path}")
    print(f"{'='*60}")

    return results


# 让计时包装对外表现得和实现一致：inspect.signature / help() / IDE 提示都能看到
# 真实参数表，而不是 (*args, **kwargs)。放在这里是因为 wraps 需要实现已定义。
# 副作用：包装函数的 __doc__ 被换成实现的 —— 这是想要的，计时是实现细节，
# 调用方该看到的是投递流程的说明。
functools.update_wrapper(auto_apply_jobs, _auto_apply_jobs_impl)


# ==================== 内部辅助函数 ====================

def _find_element(dp: WebPage, selectors: List[str], timeout: float = 2.0):
    """
    按优先级尝试多个选择器查找元素。
    支持 XPath 和 CSS 选择器。
    """
    for selector in selectors:
        try:
            el = dp.ele(selector, timeout=timeout)
            if el:
                return el
        except Exception:
            continue
    return None


def _existing_contact_label(dp: WebPage) -> str:
    """返回页面明确显示的既有沟通状态；没有则返回空字符串。

    仅检查岗位操作按钮上的精确状态词，不读取普通职位描述，避免把 JD 中的
    “沟通能力”等文字误判成已经联系。此函数在点击「立即沟通」之前调用。
    """
    try:
        text = dp.run_js("""
            const selectors = [
              '.btn-container .btn-startchat',
              '.btn-startchat',
              '[class*="btn-container"] a',
              '[class*="job"] button'
            ];
            const items = [...new Set(selectors.flatMap(s => [...document.querySelectorAll(s)]))];
            return items.filter(el => {
              const r = el.getBoundingClientRect();
              const style = getComputedStyle(el);
              return r.width > 0 && r.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            }).map(el => (el.innerText || el.textContent || '').trim()).filter(Boolean).join('\\n');
        """) or ''
    except Exception:
        return ''
    normalized = _norm(str(text))
    for label in ('继续沟通', '已沟通', '已联系', '已投递'):
        if label in normalized:
            return label
    return ''


def _wait_for_platform_prompt_clear(dp: WebPage) -> None:
    """Require a person to resolve BOSS limit/security prompts before navigation continues."""
    warning = _platform_warning_text(dp)
    if not warning:
        return
    if _is_boss_daily_contact_cap(warning):
        print("  🛑 检测到 BOSS 当日 150 次沟通上限，本轮立即停止。")
        raise ManualPlatformPrompt("检测到 BOSS 当日沟通上限")
    print(f"  ⚠ 检测到 BOSS 提示：{warning[:240]}")
    print("  请在浏览器中阅读并自行处理；脚本不会替你确认或绕过平台限制。")
    while True:
        try:
            if not sys.stdin or not sys.stdin.isatty():
                raise ManualPlatformPrompt("检测到平台限制/安全提示，需人工处理")
            answer = input("  手动处理完后按回车复查；输入 q 停止：").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = 'q'
        if answer in ('q', 'quit', 'exit', 'n'):
            raise ManualPlatformPrompt("用户选择停止处理平台提示")
        warning = _platform_warning_text(dp)
        if not warning:
            print("  已确认页面提示消失，继续当前岗位。")
            return
        print(f"  页面提示仍在：{warning[:160]}")


def _close_popup(dp: WebPage) -> bool:
    """Close routine popups, but never close or bypass a platform warning."""
    _wait_for_platform_prompt_clear(dp)

    close_selectors = [
        XPATH_POPUP_CLOSE,
        'css:.icon-close',
        'css:.dialog-close',
        'css:.modal-close',
        'css:[class*="close"]',
    ]
    for selector in close_selectors:
        try:
            el = dp.ele(selector, timeout=1)
            if el:
                el.click()
                return True
        except Exception:
            continue
    return False


def _page_contact_limit_tip(text: str) -> str:
    """Return exact BOSS contact-limit reminders found in ordinary page text."""
    normalized = re.sub(r'\s+', '', text or '')
    match = re.search(r'(?:今天|今日)已与?\d+位?BOSS沟通.{0,80}还剩\d+次沟通机会', normalized, re.I)
    if not match:
        match = re.search(
            r'(?:已达到沟通上限|(?:今天|今日)已与?150位?BOSS沟通.{0,80}(?:休息一下|明天再来|沟通上限))',
            normalized,
            re.I,
        )
    return match.group(0) if match else ''


def _is_boss_daily_contact_cap(text: str) -> bool:
    """True only for BOSS's explicit 150-contact daily-cap reminder."""
    normalized = re.sub(r'\s+', '', text or '')
    return bool(re.search(
        r'(?:已达到沟通上限|(?:今天|今日)已与?150位?BOSS沟通.{0,80}(?:休息一下|明天再来|沟通上限))',
        normalized,
        re.I,
    ))


def _platform_warning_text(dp: WebPage) -> str:
    """Read platform warnings without treating ordinary job details as a warning.

    BOSS's 120-contact reminder is rendered as a lightweight ``温馨提示`` layer on
    some page versions. It has no stable dialog/modal class, so a strictly
    worded page-text fallback handles that one reminder. Generic warning terms
    are evaluated only inside visible overlay containers; job-description text
    must never pause a run.
    """
    try:
        text = dp.run_js("""
            const nodes = [...document.querySelectorAll(
              '[role="dialog"], [role="alert"], [class*="dialog"], [class*="modal"], '
              + '[class*="popup"], [class*="toast"], [class*="notice"], [class*="alert"], [class*="tip"]')];
            const overlays = nodes.filter(el => {
              const r = el.getBoundingClientRect();
              const s = getComputedStyle(el);
              return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
            }).map(el => (el.innerText || '').trim()).filter(Boolean).join('\\n');
            const pageText = document.body ? (document.body.innerText || '') : '';
            return JSON.stringify({
              overlays: overlays.slice(0, 4000),
              pageText: pageText.slice(0, 12000),
            });
        """) or ''
    except Exception:
        return ''

    # Older test doubles and previous DrissionPage versions can return a string
    # directly. Treat it as overlay text, never as broad page text.
    if isinstance(text, str):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {'overlays': text, 'pageText': ''}
    elif isinstance(text, dict):
        payload = text
    else:
        return ''

    overlays = str(payload.get('overlays', ''))
    # Only the two explicit BOSS contact-limit messages may stop this script.
    # In particular, never infer a warning from salary figures, job details, or
    # broad words such as "限制" / "继续" on a normal page.
    overlay_reminder = _page_contact_limit_tip(overlays)
    if overlay_reminder:
        return overlay_reminder
    reminder = _page_contact_limit_tip(str(payload.get('pageText', '')))
    if reminder:
        return reminder
    return ''


def _is_platform_warning_text(text: str) -> bool:
    """Compatibility helper: only the two explicit BOSS contact-limit messages."""
    return bool(_page_contact_limit_tip(text))


def _get_greeting(
    job: Dict[str, Any],
    profile: Optional[ResumeProfile],
    greetings: Optional[Dict[str, str]]
) -> Optional[str]:
    """获取招呼语：优先使用预生成的，否则模板生成"""
    if greetings:
        link = job.get('link', '')
        if link in greetings:
            return greetings[link]

    return generate_greeting(profile=profile, job=job)


def _get_resume_file(
    job: Dict[str, Any],
    resume_file_path: Optional[Union[str, Dict[str, str]]]
) -> Optional[str]:
    """取该岗位要发的简历附件：dict 按 job_link 取，字符串则整批共用"""
    if isinstance(resume_file_path, dict):
        return resume_file_path.get(job.get('link', ''))

    return resume_file_path


def _norm(text: str) -> str:
    """归一化：去掉所有空白，便于跨 DOM 渲染比对文本"""
    return re.sub(r'\s+', '', text or '')


def _greeting_probe(greeting: str, length: int = 14) -> str:
    """
    从招呼语中取一段稳定的特征串，用于回读聊天记录时判定是否真的发出去了。
    跳过开头的括号类标点（BOSS 渲染时可能改写），取正文前 length 个字符。
    """
    s = _norm(greeting)
    core = re.sub(r'^[【\[（(“"\'’‘]+', '', s)
    return core[:length] if len(core) >= length else core


def _read_chat_record(dp: WebPage) -> str:
    """读取当前聊天记录的纯文本。注意：图片消息在 innerText 中不可见。"""
    try:
        return dp.run_js("""
            const m = document.querySelector(
                '[class*="chat-message-list"], [class*="message-list"], .chat-record');
            return m ? m.innerText : '';
        """) or ''
    except Exception:
        return ''


def _existing_outgoing_message(dp: WebPage) -> str:
    """返回聊天记录中本人历史发送内容的摘要；没有则返回空字符串。

    仅识别右侧本人消息气泡或附件卡片。HR 回复、居中的系统卡片和空会话提示
    都不会触发跳过；只要有一条本人发送记录，调用方就不再发送新的招呼语。
    """
    try:
        value = dp.run_js("""
            const root = document.querySelector(
              '[class*="chat-message-list"], [class*="message-list"], .chat-record'
            );
            if (!root) return '';
            const visible = (el) => {
              const s = getComputedStyle(el), r = el.getBoundingClientRect();
              return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
            };
            const rootRect = root.getBoundingClientRect();
            const selectors = [
              '[class*="message-item"]', '[class*="chat-message"]',
              '[class*="message-bubble"]', '[class*="bubble"]', 'li'
            ];
            const nodes = [...new Set(selectors.flatMap(s => [...root.querySelectorAll(s)]))];
            const own = [];
            for (const el of nodes) {
              if (!visible(el)) continue;
              const r = el.getBoundingClientRect();
              if (r.width >= rootRect.width * 0.92 || r.height < 12) continue;
              const cls = String(el.className || '').toLowerCase();
              const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
              const hasAttachment = !!el.querySelector('img.message-image, [class*="resume"], [class*="file"], [class*="attachment"]');
              if (!text && !hasAttachment) continue;
              const onRight = r.left + r.width * 0.5 > rootRect.left + rootRect.width * 0.62;
              const markedSelf = /(?:^|[-_\\s])(self|mine|outgoing|send|right)(?:[-_\\s]|$)/.test(cls);
              if (!onRight && !markedSelf) continue;
              if (nodes.some(other => other !== el && el.contains(other) && visible(other) &&
                    (other.innerText || other.querySelector('img.message-image')))) continue;
              own.push(text || '本人已发送附件');
            }
            return own.join(' | ');
        """) or ''
    except Exception:
        return ''
    return _norm(str(value))


def _read_chat_input(dp: WebPage) -> str:
    """回读聊天输入框的当前内容，用于确认文字真的落进了框架 state"""
    try:
        return dp.run_js("""
            const el = document.querySelector('#chat-input, [contenteditable="true"], textarea');
            return el ? (el.innerText || el.value || '') : '';
        """) or ''
    except Exception:
        return ''


def _count_chat_images(dp: WebPage) -> int:
    """
    统计聊天记录中的图片消息数量。
    只数 img.message-image，排除界面图标（msg-blur）和头像。
    """
    try:
        n = dp.run_js("""
            const m = document.querySelector(
                '[class*="chat-message-list"], [class*="message-list"], .chat-record');
            if (!m) return -1;
            return m.querySelectorAll('img.message-image').length;
        """)
        return int(n) if n is not None else -1
    except Exception:
        return -1


def _input_greeting(dp: WebPage, greeting: str) -> bool:
    """
    在聊天输入框中输入招呼语，并**回读校验文字是否真的落地**。

    只使用 DrissionPage 的 .input()（底层走 CDP 真实输入事件，浏览器视为可信输入，
    BOSS 前端框架必然更新内部 state）。

    ⚠ 绝不要用 JS 直接赋值 el.innerText / el.value：
      BOSS 聊天框是框架受控组件，JS 赋值 + 派发合成事件不会更新框架 state，
      界面上看着有字但 model 为空，点发送会发出空内容 —— 这是历史上误报
      「投递成功」的根因（2026-08-13 实测确认）。
    """
    if not greeting:
        return False

    probe = _greeting_probe(greeting)

    input_selectors = [
        XPATH_CHAT_INPUT,
        'css:#chat-input',
        'css:div[contenteditable="true"]',
        'css:[contenteditable="true"]',
        'css:textarea',
        'css:.chat-input',
        'css:[placeholder*="消息"]',
        'css:[placeholder*="输入"]',
    ]

    for selector in input_selectors:
        el = None
        try:
            el = dp.ele(selector, timeout=2)
        except Exception:
            # 尝试在 iframe 中查找
            try:
                iframe = dp.get_frame(1)
                el = iframe.ele(selector, timeout=1) if iframe else None
            except Exception:
                el = None
        if not el:
            continue

        try:
            el.click()
            time.sleep(0.3)
            try:
                el.clear()
                time.sleep(0.2)
            except Exception:
                pass
            el.input(greeting)          # CDP 真实输入
            time.sleep(1.2)
        except Exception:
            continue

        # ── 回读校验：文字必须真的在输入框里 ──
        typed = _norm(_read_chat_input(dp))
        if probe and probe in typed:
            print(f"  ✓ 输入框已确认落地 {len(typed)} 字 ({selector})")
            return True
        print(f"  ⚠ {selector} 输入未落地（实际 {len(typed)} 字），换下一个选择器")

    return False



def _click_send(dp: WebPage, greeting: Optional[str] = None) -> bool:
    """
    发送聊天框内已输入的内容，并**回读聊天记录校验消息气泡是否出现**。

    实测（2026-08-13）：
      - `//button[@type='send']` 点击**无效**，不会真的发送；
      - 回车（Enter）**有效**。
    因此回车为主，按钮为备。

    Args:
        greeting: 已输入的招呼语。传入则回读校验，未在聊天记录中命中即返回 False；
                  不传则退化为「点了就算」的旧行为（不推荐）。

    Returns:
        True 仅代表消息气泡已确认出现在聊天记录中。
    """
    probe = _greeting_probe(greeting) if greeting else ''

    def _verify() -> bool:
        if not probe:
            return True          # 无法校验时不谎报，由调用方决定
        return probe in _norm(_read_chat_record(dp))

    # 已经发出去了（重试场景），直接返回
    if probe and _verify():
        return True

    # ── 方式 1：回车（实测有效）──
    try:
        el = dp.ele(XPATH_CHAT_INPUT, timeout=2) or dp.ele('css:[contenteditable="true"]', timeout=2)
        if el:
            el.click()
            time.sleep(0.3)
        dp.actions.type('\n')
        time.sleep(3)
        if _verify():
            print(f"  ✓ 发送已校验（回车）")
            return True
        print(f"  ⚠ 回车后未见消息气泡，尝试发送按钮")
    except Exception as e:
        print(f"  ⚠ 回车发送异常: {e}")

    # ── 方式 2：发送按钮（备用）──
    for selector in [
        XPATH_SEND_BUTTON,
        'css:button[type="send"]',
        'css:.btn-send',
        'css:.send-btn',
    ]:
        try:
            el = dp.ele(selector, timeout=2)
            if el:
                el.click()
                time.sleep(3)
                if _verify():
                    print(f"  ✓ 发送已校验（按钮 {selector}）")
                    return True
        except Exception:
            continue

    return False
