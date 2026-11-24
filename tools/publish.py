#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把当前版本发布到 GitHub：提交代码 → 打 tag → 推分支和 tag → 建 Release 并上传 exe

用法：
    set GITHUB_TOKEN=ghp_xxx
    python tools/publish.py                 # 打包 + 发布当前 APP_VERSION
    python tools/publish.py --no-build      # 不重新打包，直接发布已有 exe
    python tools/publish.py --notes-file release-notes.md
    python tools/publish.py --dry-run       # 只打印将要做什么

仓库：liangyangming18-cmd/cs_trainingpanel
token 需要 repo 权限（classic PAT）或 Contents + Administration: write（fine-grained）。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "liangyangming18-cmd/cs_trainingpanel"
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"


def app_version() -> str:
    text = (ROOT / "server.py").read_text(encoding="utf-8")
    found = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    if not found:
        raise SystemExit("server.py 里找不到 APP_VERSION")
    return found.group(1)


def run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=check, text=True,
                          capture_output=capture, encoding="utf-8", errors="replace")


def git(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return run(["git", *args], check=check, capture=capture)


def api(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "CS2Workbench-publish",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", "ignore")
    return json.loads(body) if body.strip() else {}


def upload_asset(upload_url: str, token: str, file: Path) -> dict:
    """Release 附件走 uploads.github.com，必须用原始字节 POST，不能带 Content-Type: json。"""
    ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    size = file.stat().st_size
    data = file.read_bytes()
    url = f"{upload_url.split('{')[0]}?name={urllib.parse.quote(file.name)}"
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "CS2Workbench-publish",
        "Content-Type": ctype,
        "Content-Length": str(size),
    })
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    ap.add_argument("--no-build", action="store_true", help="跳过打包")
    ap.add_argument("--notes-file", default="", help="Release 说明文件（Markdown）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ver = app_version()
    tag = f"v{ver}"
    exe = ROOT / f"CS2 Workbench {tag}.exe"
    print(f"版本 {ver} → tag {tag}")
    print(f"exe  {exe}")

    if args.dry_run:
        print("\n[dry-run] 将执行：build → commit → tag → push → release + 上传 exe")
        return 0

    token = args.token.strip()
    if not token:
        print("缺少 token：设置环境变量 GITHUB_TOKEN，或用 --token 传入", file=sys.stderr)
        return 2

    # 1) 打包
    if not args.no_build:
        run([sys.executable, "tools/build_exe.py"])
    if not exe.exists():
        print(f"找不到 {exe.name}，先跑 tools/build_exe.py", file=sys.stderr)
        return 2

    # 2) 提交
    git("add", "-A")
    status = git("status", "--porcelain", capture=True).stdout.strip()
    if status:
        git("commit", "-m", f"release: {tag}")
    else:
        print("工作区干净，跳过提交")

    # 3) tag
    has_tag = git("tag", "--list", tag, capture=True).stdout.strip()
    if not has_tag:
        git("tag", "-a", tag, "-m", f"CS2 Workbench {tag}")

    # 4) 推送
    git("push", "origin", "HEAD")
    git("push", "origin", tag)

    # 5) Release
    notes = ""
    if args.notes_file and Path(args.notes_file).is_file():
        notes = Path(args.notes_file).read_text(encoding="utf-8")
    else:
        for cand in (ROOT.parent / f"trainingpanel_{tag}" / "VERSION.txt", ROOT / "VERSION.txt"):
            if cand.is_file():
                notes = cand.read_text(encoding="utf-8")
                break
    notes = notes or f"CS2 Workbench {tag}"

    try:
        rel = api("GET", f"/repos/{REPO}/releases/tags/{tag}", token)
        print(f"Release {tag} 已存在，直接复用：{rel.get('html_url')}")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        rel = api("POST", f"/repos/{REPO}/releases", token, {
            "tag_name": tag,
            "name": f"CS2 Workbench {tag}",
            "body": notes,
            "draft": False,
            "prerelease": False,
        })
        print(f"已创建 Release：{rel.get('html_url')}")

    # 6) 上传 exe（已存在同名附件则跳过）
    names = {a.get("name") for a in rel.get("assets") or []}
    if exe.name in names:
        print(f"附件已存在，跳过上传：{exe.name}")
    else:
        print(f"上传 {exe.name}（{exe.stat().st_size / 1048576:.1f} MB，可能要一会儿）…")
        started = time.time()
        asset = upload_asset(rel["upload_url"], token, exe)
        print(f"上传完成：{asset.get('browser_download_url')}（{time.time() - started:.0f}s）")

    print(f"\n完成：{rel.get('html_url')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
