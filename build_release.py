#!/usr/bin/env python
# -*- coding: utf-8 -*-
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parent
target = root.parent / "BOSS嵌入式自动投递_直接使用版.zip"
top_files = {
    "embedded_auto.py", "embedded_config.json", "requirements.txt", "README.md",
    "SKILL.md", "THIRD_PARTY_NOTICE.md", "使用说明_嵌入式自动投递.md",
    "START_BOSS_AUTO.cmd", "SEND_PENDING_RESUMES.cmd", "REVIEW_MISSED_JOBS.cmd",
}
excluded_scripts = {
    "diagnose_resume_toolbar.py", "inspect_conversation_resume.py",
    "verify_recent_attachments.py",
}
with ZipFile(target, "w", ZIP_DEFLATED) as archive:
    for name in sorted(top_files):
        path = root / name
        archive.write(path, f"boss-embedded-auto/{name}")
    for path in sorted((root / "scripts").rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.name in excluded_scripts:
            continue
        rel = path.relative_to(root).as_posix()
        archive.write(path, f"boss-embedded-auto/{rel}")
print(target)
