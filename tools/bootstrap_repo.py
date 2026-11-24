#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性引导脚本：建仓库 -> 推送 main 与全部 tag -> 为每个 tag 建 Release 并上传 exe。

用法（PowerShell）：
    $env:GITHUB_TOKEN="ghp_xxx"; python tools\\bootstrap_repo.py
    python tools\\bootstrap_repo.py --dry-run          只看会做什么
    python tools\\bootstrap_repo.py --skip-releases    只推代码

幂等：仓库已存在就跳过创建；release 已存在就跳过；tag 已推送会报 up-to-date。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent            # CS2-Workbench
BASE = ROOT.parent                                       # trainingpanel
OWNER = "liangyangming18-cmd"
REPO = "cs_trainingpanel"
DESC = "CS2 训练工作台 — 人机强化 / 换肤 / 一键部署（Windows 11 风格本地面板）"
TAGS = ["v1.0.1", "v1.0.2", "v1.0.3", "v1.0.4"]
API = "https://api.github.com"


# --------------------------------------------------------------------- GitHub
def token() -> str:
    t = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if not t:
        sys.exit("缺少 GITHUB_TOKEN 环境变量")
    return t


def gh(path: str, method: str = "GET", body: dict | None = None, token_: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        headers={
            "Authorization": f"Bearer {token_ or token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cs2-workbench-publish",
            **({"Content-Type": "application/json"} if data else {}),
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        return {"__status": e.code, "__error": detail}


def ok(res: dict) -> bool:
    return "__status" not in res


# ------------------------------------------------------------------------ git
def run(args: list[str], env_extra: dict | None = None, dry: bool = False) -> tuple[int, str]:
    if dry:
        print("  [dry] git " + " ".join(args))
        return 0, ""
    env = {**os.environ, **(env_extra or {})}
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, env=env)
    out = (p.stdout + p.stderr).strip()
    return p.returncode, out


def git_env_with_token(tok: str) -> tuple[dict, Path]:
    """用临时 credential 文件喂 token，避免 token 出现在命令行参数里。"""
    fd, name = tempfile.mkstemp(prefix="ghcred-", suffix=".txt")
    os.close(fd)
    path = Path(name)
    path.write_text(
        f"https://x-access-token:{tok}@github.com\n", encoding="utf-8"
    )
    return {"GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": f"store --file={path.as_posix()}",
            "GIT_CONFIG_KEY_1": "credential.useHttpPath",
            "GIT_CONFIG_VALUE_1": "true"}, path


# -------------------------------------------------------------------- release
def upload_asset(upload_url: str, tok: str, file: Path) -> str:
    url = upload_url.replace("{?name,label}", "") + "?name=" + urllib.parse.quote(file.name)
    size = file.stat().st_size
    req = urllib.request.Request(
        url,
        data=file.read_bytes(),
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/octet-stream",
            "Content-Length": str(size),
            "User-Agent": "cs2-workbench-publish",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read()).get("browser_download_url", "")


def release_notes(tag: str) -> str:
    cand = BASE / f"trainingpanel_{tag}" / "VERSION.txt"
    if cand.exists():
        return cand.read_text(encoding="utf-8", errors="replace")[:60000]
    return f"CS2 Workbench {tag}"


def find_exe(tag: str) -> Path | None:
    d = BASE / f"trainingpanel_{tag}"
    if not d.is_dir():
        return None
    exes = sorted(d.glob("*.exe"), key=lambda p: p.stat().st_size, reverse=True)
    return exes[0] if exes else None


# ----------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-releases", action="store_true")
    ap.add_argument("--only-tags", default="", help="逗号分隔，只处理这些 tag 的 Release")
    a = ap.parse_args()

    tok = token()
    dry = a.dry_run

    # 1) 身份 + 仓库
    me = gh("/user")
    if not ok(me):
        sys.exit(f"token 无效：{me}")
    print(f"账号：{me['login']}")

    repo = gh(f"/repos/{OWNER}/{REPO}")
    if ok(repo):
        print(f"仓库已存在：{repo['html_url']}（private={repo['private']}）")
    elif repo.get("__status") == 404:
        print(f"仓库不存在，创建 {OWNER}/{REPO} …")
        if dry:
            print("  [dry] POST /user/repos")
        else:
            made = gh("/user/repos", "POST", {
                "name": REPO, "description": DESC, "private": False,
                "has_issues": True, "has_wiki": False, "auto_init": False,
            })
            if not ok(made):
                sys.exit(
                    "建仓库失败：%s\n"
                    "→ 说明这个 token 没有建库权限。请改用 classic token（勾 repo），"
                    "或去 https://github.com/new 手建一个空仓库 %s（不要勾 README）。"
                    % (made.get("__error"), REPO)
                )
            print("  已创建：", made["html_url"])
    else:
        sys.exit(f"读取仓库失败：{repo}")

    # 2) 推送
    env, cred = git_env_with_token(tok)
    try:
        url = f"https://github.com/{OWNER}/{REPO}.git"
        print("推送 main …")
        rc, out = run(["push", url, "refs/heads/main:refs/heads/main", "--force"], env, dry)
        print("  " + (out.splitlines()[-1] if out else "ok"))
        if rc:
            sys.exit(f"推送 main 失败：{out}")

        print("推送 tags …")
        rc, out = run(["push", url, "--tags", "--force"], env, dry)
        print("  " + (out.splitlines()[-1] if out else "ok"))
        if rc:
            sys.exit(f"推送 tags 失败：{out}")
    finally:
        cred.unlink(missing_ok=True)

    # 3) Releases
    if a.skip_releases:
        print("跳过 Release")
        return 0

    tags = [t.strip() for t in a.only_tags.split(",") if t.strip()] or TAGS
    for tag in tags:
        exe = find_exe(tag)
        print(f"\n[{tag}] exe={exe.name if exe else '无'}")
        if dry:
            print("  [dry] 创建/复用 Release 并上传")
            continue
        rel = gh(f"/repos/{OWNER}/{REPO}/releases/tags/{tag}")
        if not ok(rel):
            body = {
                "tag_name": tag,
                "name": f"CS2 Workbench {tag}",
                "body": release_notes(tag),
                "draft": False,
                "prerelease": False,
            }
            rel = gh(f"/repos/{OWNER}/{REPO}/releases", "POST", body)
            if not ok(rel):
                print("  建 Release 失败：", rel.get("__error"))
                continue
            print("  Release 已创建")
        else:
            print("  Release 已存在，复用")

        if not exe:
            continue
        have = {x["name"] for x in (rel.get("assets") or [])}
        if exe.name in have:
            print(f"  附件已存在，跳过：{exe.name}")
            continue
        try:
            url = upload_asset(rel["upload_url"], tok, exe)
            print(f"  已上传 {exe.name} → {url}")
        except Exception as e:  # noqa: BLE001
            print(f"  上传失败：{e}")

    print("\n完成：", f"https://github.com/{OWNER}/{REPO}/releases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
