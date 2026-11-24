#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把工作台打包成单个 exe（CS2Workbench.exe）

用法：python tools/build_exe.py
产物：dist/CS2Workbench.exe

exe 自带 web/、data/ 与模组包 payload.zip，第一次运行会把模组解到
exe 同目录的 mods/ 下，因此不再需要旁边的 CS2BotImprover 文件夹。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
MODS_PARENT = ROOT.parent

# 打进包里的模组（不含 Panel.exe、旧面板备份）
BOT = MODS_PARENT / "CS2BotImprover"
INV = MODS_PARENT / "InventorySimulator-v3.5.2"

ICON_SIZE = 256


def make_icon(path: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        print("没有 Pillow，跳过图标")
        return
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (8, 8, 10, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, ICON_SIZE - 11, ICON_SIZE - 11], outline=(255, 255, 255, 255), width=6)
    font = None
    for candidate in ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf"):
        try:
            font = ImageFont.truetype(candidate, 104)
            break
        except Exception:
            continue
    text = "CS2"
    if font is None:
        draw.text((46, 70), text, fill=(255, 255, 255, 255))
    else:
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(((ICON_SIZE - (box[2] - box[0])) / 2 - box[0],
                   (ICON_SIZE - (box[3] - box[1])) / 2 - box[1] - 6),
                  text, font=font, fill=(255, 255, 255, 255))
    img.save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"图标 → {path}")


def make_payload(path: Path) -> Path:
    needed = [BOT / "addons", BOT / "backup", BOT / "cfg", BOT / "overrides",
              INV / "addons"]
    missing = [p for p in needed if not p.exists()]
    if missing:
        raise SystemExit(f"缺少模组内容：{missing}")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for item in needed:
            for file in item.rglob("*"):
                if not file.is_file():
                    continue
                if file.suffix.lower() in (".pdb",):
                    continue
                # 保留 CS2BotImprover/… 与 InventorySimulator-v3.5.2/… 的层级
                rel = file.relative_to(MODS_PARENT)
                zf.write(file, rel.as_posix())
    size = path.stat().st_size / 1048576
    print(f"模组包 → {path}（{size:.1f} MB）")
    return path


def build() -> None:
    BUILD.mkdir(exist_ok=True)
    icon = BUILD / "app.ico"
    make_icon(icon)
    payload = make_payload(BUILD / "payload.zip")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--noconsole",
        "--name", "CS2Workbench",
        "--distpath", str(ROOT),
        "--workpath", str(BUILD / "work"),
        "--specpath", str(BUILD),
        "--add-data", f"{ROOT / 'web'};web",
        "--add-data", f"{ROOT / 'data'};data",
        "--add-data", f"{payload};.",
    ]
    if icon.exists():
        cmd += ["--icon", str(icon)]
    cmd.append(str(ROOT / "server.py"))

    print("运行：" + " ".join(cmd))
    subprocess.run(cmd, check=True)
    exe = ROOT / "CS2Workbench.exe"
    if exe.exists():
        print(f"\n完成 → {exe}（{exe.stat().st_size / 1048576:.1f} MB）")
    else:
        raise SystemExit("打包失败，没有生成 exe")


if __name__ == "__main__":
    build()
