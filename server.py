#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CS2 训练工作台 · 本地服务端

把两套模组整合成一个可操作的工作台：
  · CS2-Bot-Improver      —— 强化人机 AI（离线 -insecure 人机对打）
  · CS2 InventorySimulator —— 服务器端换肤（本地 inventories.json，无需联网）

仅依赖 Python 标准库，不需要 pip 安装任何东西。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
RES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
WEB_DIR = RES_DIR / "web"
DATA_DIR = RES_DIR / "data"

# 窗口版 exe 没有控制台，print 在 stdout 为 None 时会炸
if sys.stdout is None or sys.stderr is None:
    class _Sink:
        def write(self, *_args):
            return 0

        def flush(self):
            pass

    if sys.stdout is None:
        sys.stdout = _Sink()  # type: ignore[assignment]
    if sys.stderr is None:
        sys.stderr = _Sink()  # type: ignore[assignment]

CS2_DIRNAME = "Counter-Strike Global Offensive"

# 命令行开关：计划任务用「--takeover --no-browser」接管已有实例并静默运行
_ARGS = [a.lower() for a in sys.argv[1:]]
TAKEOVER = "--takeover" in _ARGS
if "--no-browser" in _ARGS:
    os.environ["WB_NO_BROWSER"] = "1"


def _writable_dir(preferred: Path) -> Path:
    """优先用 exe 所在目录（便携），不可写时退到 %LOCALAPPDATA%。"""
    for candidate in (preferred, Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CS2Workbench"):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return candidate
        except Exception:
            continue
    return preferred


HOME_DIR = _writable_dir(APP_DIR)
STATE_PATH = HOME_DIR / "state.json"
BACKUP_DIR = HOME_DIR / "backup"
LEDGER_PATH = HOME_DIR / "ledger.json"
LEDGER_FILES = HOME_DIR / "ledger" / "files"


def _unpack_payload() -> Path | None:
    """把打进 exe 的模组包解到本地（只解一次）。"""
    archive = RES_DIR / "payload.zip"
    if not archive.exists():
        return None
    target = HOME_DIR / "mods"
    stamp = target / ".payload-version"
    version = f"{archive.stat().st_size}-{int(archive.stat().st_mtime)}"
    if stamp.exists() and stamp.read_text(encoding="utf-8", errors="ignore").strip() == version:
        return target
    try:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target)
        stamp.write_text(version, encoding="utf-8")
        print(f"模组包已释放到 {target}")
        return target
    except Exception as exc:  # noqa: BLE001
        print(f"释放模组包失败：{exc}")
        return None


INV_REL = Path("addons") / "counterstrikesharp" / "plugins" / "InventorySimulator" / "InventorySimulator.dll"


def _candidate_bases() -> list[Path]:
    """模组包可能放在哪：你自己的工作目录优先。"""
    out = []
    for base in (APP_DIR.parent.parent, APP_DIR.parent, HOME_DIR, APP_DIR):
        if base not in out:
            out.append(base)
    return out


def _find_inv_pkg() -> Path | None:
    """找换肤插件包（目录名/层级都不固定，按结构认）。"""
    best: Path | None = None
    for base in _candidate_bases():
        for cand in sorted(base.glob("InventorySimulator*")):
            if cand.is_dir() and (cand / INV_REL).exists():
                if "3.5.2" in cand.name:
                    return cand
                best = best or cand
    if best:
        return best
    # 有些包是 plugins/<版本>/addons/... 的嵌套结构
    for base in _candidate_bases():
        for cand in sorted(base.glob("InventorySimulator*")):
            if not cand.is_dir():
                continue
            for dll in cand.rglob("InventorySimulator.dll"):
                if "backup" in str(dll).lower() or len(dll.parents) < 5:
                    continue
                root = dll.parents[4]
                if (root / INV_REL).exists():
                    return root
    return None


def _resolve_mods_root() -> Path:
    """模组根目录：优先用你自己维护的那份（Panel.exe 改的就是它）。"""
    env = os.environ.get("WB_MODS_DIR", "").strip()
    if env and Path(env).is_dir():
        return Path(env)
    for base in _candidate_bases():
        if (base / "CS2BotImprover").is_dir():
            return base
    if not FROZEN and (APP_DIR / "CS2BotImprover").is_dir():
        return APP_DIR
    unpacked = _unpack_payload()
    if unpacked and (unpacked / "CS2BotImprover").is_dir():
        return unpacked
    return APP_DIR.parent


MODS_ROOT = _resolve_mods_root()
PKG_BOT = MODS_ROOT / "CS2BotImprover"
PKG_INV = _find_inv_pkg() or (MODS_ROOT / "InventorySimulator-v3.5.2")


def bot_improver_dir(state: dict | None = None) -> Path | None:
    """定位 CS2BotImprover 的原始包目录（优先用户自己那份，其次部署用的那份）。"""
    candidates: list[Path] = []
    if state:
        raw = str(state.get("botDir") or "").strip().strip('"')
        if raw:
            candidates.append(Path(raw))
    for base in (APP_DIR.parent.parent, MODS_ROOT.parent, MODS_ROOT,
                 APP_DIR.parent, APP_DIR):
        candidates.append(base / "CS2BotImprover")
    candidates.append(PKG_BOT)

    seen = set()
    for cand in candidates:
        try:
            key = str(cand.resolve()).lower()
        except Exception:  # noqa: BLE001
            key = str(cand).lower()
        if key in seen:
            continue
        seen.add(key)
        if cand.is_dir():
            return cand
    return None


def _open_in_explorer(path: Path) -> None:
    """在资源管理器里打开目录，窗口版 exe 没有控制台也照样能用。"""
    if os.name != "nt":
        webbrowser.open(path.as_uri())
        return
    try:
        os.startfile(str(path))  # noqa: S606
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        subprocess.Popen(["explorer", str(path)], close_fds=True)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"无法打开资源管理器：{exc}") from exc


# 在线库存站点（InventorySimulator 的官方站点，插件可直接连）
SITE_URL = "https://inventory.cstrike.app"

# 换肤模式下需要停用的机器人插件
BOT_PLUGINS = [
    "BotAI", "BotAimImprover", "BotBuy", "BotControllerImpl",
    "BotRandomizer", "BotState", "NadeSystem", "RoundDamageRecap",
]

# 这些插件对应的原生模块未适配 CS2 1.42（进图后崩在 tier0），装完/自检时统一禁用
BROKEN_PLUGIN_ENTRIES = ("BotHider.vdf", "BotHiderImpl")

MODES = {
    "offline": {
        "label": "离线全功能",
        "desc": "强化 AI 插件 + 换肤，需要 -insecure；要求插件已适配当前 CS2",
        "gameinfo": "withbots",
    },
    "boost": {
        "label": "强化人机（免插件）",
        "desc": "只挂 botprofile 覆盖，不加载任何插件；游戏更新后依然可用",
        "gameinfo": "boost",
    },
    "skin": {
        "label": "仅换肤",
        "desc": "保留原版机器人，只开换肤插件",
        "gameinfo": "skin",
    },
    "online": {
        "label": "在线原版",
        "desc": "恢复官方匹配，不加 -insecure",
        "gameinfo": "online",
    },
}

GAMEMODES = {
    "competitive": {"label": "竞技 5v5", "gt": 0, "gm": 1, "cfg": "gamemode_competitive_offline"},
    "casual": {"label": "休闲", "gt": 0, "gm": 0, "cfg": "gamemode_casual"},
    "deathmatch": {"label": "死斗", "gt": 1, "gm": 2, "cfg": "gamemode_deathmatch"},
    "armsrace": {"label": "军备竞赛", "gt": 1, "gm": 0, "cfg": "gamemode_armsrace"},
    "rush": {"label": "冲点 Rush", "gt": 0, "gm": 1, "cfg": "gamemode_rush_offline"},
}

MAPS = [
    ("de_mirage", "荒漠迷城"), ("de_dust2", "炙热沙城 II"), ("de_inferno", "炼狱小镇"),
    ("de_ancient", "远古遗迹"), ("de_anubis", "阿努比斯"), ("de_nuke", "核子危机"),
    ("de_overpass", "死亡游乐园"), ("de_train", "列车停放站"), ("de_vertigo", "殒命大厦"),
    ("cs_office", "办公室"), ("cs_italy", "意大利"), ("de_cache", "死城之谜"),
]

DIFFICULTY_LABEL = {"Low": "简单", "Medium": "中等", "High": "困难"}

DEFAULT_STATE = {
    "schema": 1,
    "cs2Root": "",
    "steamExe": "",
    "steamId": "",
    "mode": "offline",
    "difficulty": "Medium",
    "aim": "mixed",
    "nades": "normal",
    "match": {
        "gamemode": "competitive",
        "map": "de_mirage",
        "preset": "normal",
        "loadout": "random",
        "botQuota": 10,
        "botDifficulty": 4,
        "botGrenades": False,
        "ecoLimit": 2800,
        "c4timer": 40,
        "autoMap": True,
        "cheats": True,
        "friendlyFire": True,
        "roundTime": 1.92,
        "freezeTime": 3,
        "startMoney": 800,
        "warmupTime": 0,
        "hostname": "CS2 WORKBENCH",
        "extraArgs": "",
        "applyPluginCmds": True,
    },
    "invsim": {
        "url": SITE_URL,
        "ws": True,
        "immediately": True,
        "loadout": True,
        "fallbackTeam": True,
        "spray": False,
        "stattrakIgnoreBots": False,
    },
    "skinSource": "site",
    "siteCache": {"at": "", "counts": {}, "steamId": ""},
    "inventory": {"2": {}, "3": {}},
    "guard": {"broken": False, "at": "", "module": "", "offset": "", "code": "",
              "suspended": False, "suspendedAt": "", "lastLaunch": "", "note": ""},
    "log": [],
}

_lock = threading.Lock()
RUNTIME_PORT = 8787
_last_served = 0.0
START_TS = time.time()


# --------------------------------------------------------------------------
# 状态读写
# --------------------------------------------------------------------------

def load_state() -> dict:
    state = json.loads(json.dumps(DEFAULT_STATE))
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            for key, value in data.items():
                if isinstance(value, dict) and isinstance(state.get(key), dict):
                    state[key].update(value)
                else:
                    state[key] = value
        except Exception:
            pass
    return state


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def log(state: dict, msg: str, level: str = "info") -> None:
    entry = {"t": time.strftime("%H:%M:%S"), "level": level, "msg": msg}
    state.setdefault("log", []).insert(0, entry)
    del state["log"][200:]
    print(f"[{entry['t']}] {msg}")


# --------------------------------------------------------------------------
# 路径探测
# --------------------------------------------------------------------------

def steam_dir() -> Path | None:
    candidates = []
    if os.name == "nt":
        try:
            import winreg

            for hive, key in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            ):
                try:
                    with winreg.OpenKey(hive, key) as handle:
                        for value in ("SteamPath", "InstallPath"):
                            try:
                                candidates.append(Path(winreg.QueryValueEx(handle, value)[0]))
                            except OSError:
                                pass
                except OSError:
                    pass
        except Exception:
            pass
    for drive in "CDEFGH":
        candidates += [Path(f"{drive}:/Steam"), Path(f"{drive}:/Program Files (x86)/Steam"),
                       Path(f"{drive}:/Program Files/Steam")]
    for path in candidates:
        try:
            if path and (path / "steamapps").is_dir():
                return path.resolve()
        except Exception:
            continue
    return None


def library_folders(steam: Path) -> list[Path]:
    libs = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        text = vdf.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r'"path"\s+"([^"]+)"', text):
            p = Path(match.group(1).replace("\\\\", "\\"))
            if p not in libs:
                libs.append(p)
    return libs


def detect_cs2(steam: Path | None) -> Path | None:
    if not steam:
        return None
    for lib in library_folders(steam):
        root = lib / "steamapps" / "common" / CS2_DIRNAME
        if (root / "game" / "bin" / "win64" / "cs2.exe").exists():
            return root.resolve()
    return None


def resolve_paths(state: dict) -> dict:
    steam = Path(state["steamExe"]).parent if state.get("steamExe") else steam_dir()
    root = Path(state["cs2Root"]) if state.get("cs2Root") else None
    if root and not (root / "game" / "bin" / "win64" / "cs2.exe").exists():
        root = None
    if root is None:
        root = detect_cs2(steam)
    if root:
        state["cs2Root"] = str(root)
    steam_exe = None
    if steam:
        candidate = steam / "steam.exe"
        if candidate.exists():
            steam_exe = candidate
            state["steamExe"] = str(candidate)
    return {
        "cs2Root": root,
        "csgo": (root / "game" / "csgo") if root else None,
        "exe": (root / "game" / "bin" / "win64" / "cs2.exe") if root else None,
        "steam": steam,
        "steamExe": steam_exe,
    }


def detect_steam_id(steam: Path | None) -> str:
    if not steam:
        return ""
    vdf = steam / "config" / "loginusers.vdf"
    if not vdf.exists():
        return ""
    text = vdf.read_text(encoding="utf-8", errors="ignore")
    blocks = re.findall(r'"(7656119\d{10})"\s*\{(.*?)\}', text, re.S)
    for sid, body in blocks:
        if re.search(r'"AutoLogin"\s+"1"', body):
            return sid
    return blocks[0][0] if blocks else ""


# --------------------------------------------------------------------------
# 健康检查
# --------------------------------------------------------------------------

def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def sha(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def plugin_version(deps: Path) -> str:
    text = read_text(deps)
    match = re.search(r'"CounterStrikeSharp\.API/([\d.]+)"', text)
    return match.group(1) if match else ""


def health(state: dict, paths: dict) -> dict:
    csgo: Path | None = paths["csgo"]
    result = {
        "found": bool(csgo and csgo.is_dir()),
        "metamod": False,
        "cssharp": False,
        "cssharpVersion": "",
        "plugins": [],
        "botPluginsDisabled": False,
        "gameinfo": "unknown",
        "overrides": "",
        "difficultyVpk": "",
        "invsim": False,
        "inventories": False,
        "cfg": False,
        "pkgBot": PKG_BOT.is_dir(),
        "pkgInv": PKG_INV.is_dir(),
    }
    if not csgo or not csgo.is_dir():
        return result

    result["metamod"] = (csgo / "addons" / "metamod" / "bin").is_dir() or (
        csgo / "addons" / "metamod_x64.vdf").exists()
    cssharp = csgo / "addons" / "counterstrikesharp"
    result["cssharp"] = (cssharp / "bin").is_dir()
    if (cssharp / "api" / "CounterStrikeSharp.API.deps.json").exists():
        result["cssharpVersion"] = plugin_version(cssharp / "api" / "CounterStrikeSharp.API.deps.json")
    result["cssharpAdapted"] = bool(result["cssharpVersion"]) and \
        ver_tuple(result["cssharpVersion"]) >= ver_tuple(MIN_CSS_VERSION)
    result["botHider"] = (csgo / "addons" / "metamod" / "BotHider.vdf").exists()

    plugins_dir = cssharp / "plugins"
    if plugins_dir.is_dir():
        result["plugins"] = sorted(p.name for p in plugins_dir.iterdir()
                                   if p.is_dir() and not p.name.endswith(".off"))
    off_dir = cssharp / "plugins_off"
    result["botPluginsDisabled"] = off_dir.is_dir() and any(off_dir.iterdir())

    gameinfo = read_text(csgo / "gameinfo.gi")
    has_meta = "csgo/addons/metamod" in gameinfo
    has_override = "csgo/overrides/botprofile.vpk" in gameinfo
    if has_meta and has_override:
        result["gameinfo"] = "offline"
    elif has_meta:
        result["gameinfo"] = "skin"
    elif has_override:
        result["gameinfo"] = "boost"
    elif (state.get("guard") or {}).get("suspended"):
        result["gameinfo"] = "suspended"
    elif gameinfo:
        result["gameinfo"] = "online"

    vpk_dir = csgo / "overrides"
    active = vpk_dir / "botprofile.vpk"
    if active.exists():
        digest = sha(active)
        for level in ("Low", "Medium", "High"):
            candidate = vpk_dir / level / "botprofile.vpk"
            if candidate.exists() and sha(candidate) == digest:
                result["difficultyVpk"] = level
                break
        if not result["difficultyVpk"]:
            result["difficultyVpk"] = "custom"
    result["overrides"] = str(vpk_dir) if vpk_dir.exists() else ""

    result["invsim"] = (plugins_dir / "InventorySimulator" / "InventorySimulator.dll").exists() or (
        off_dir / "InventorySimulator" / "InventorySimulator.dll").exists()
    payload = build_inventory(state)
    result["inventories"] = bool(
        payload["tWeapons"] or payload["ctWeapons"] or payload["knives"] or payload["gloves"])
    result["cfg"] = (csgo / "cfg" / "wb_match.cfg").exists()
    result["skinSource"] = state.get("skinSource", "site")
    result["ledger"] = ledger_summary(csgo)
    result["clean"] = verify_clean(paths)
    return result


# --------------------------------------------------------------------------
# 部署
# --------------------------------------------------------------------------

CORE_DIRS = {"bin", "api", "dotnet", "shared", "lang", "source"}

# 这些目录/文件完全由模组创建，一键还原时整体移除
OWNED_ENTRIES = [
    "addons/metamod",
    "addons/metamod.vdf",
    "addons/metamod_x64.vdf",
    "addons/metamod.vpk",
    "addons/counterstrikesharp",
    "overrides",
    "backup",
]


def version_tuple(text: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", text or "")) or (0,)


def package_cssharp_version() -> str:
    return plugin_version(PKG_BOT / "addons" / "counterstrikesharp" / "api" / "CounterStrikeSharp.API.deps.json")


def installed_cssharp_version(csgo: Path) -> str:
    return plugin_version(csgo / "addons" / "counterstrikesharp" / "api" / "CounterStrikeSharp.API.deps.json")


def safe_join(root: Path, rel: str) -> Path | None:
    """只允许 root 内的相对路径，防止误删。"""
    if not rel or rel.startswith(("/", "\\")) or ".." in Path(rel).parts or re.match(r"^[A-Za-z]:", rel):
        return None
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


class Ledger:
    """记录部署时新建 / 改写的文件，用于精确回滚与统计。"""

    def __init__(self, csgo: Path):
        self.csgo = csgo
        raw = load_ledger()
        if raw.get("csgo") != str(csgo):
            raw = {"csgo": str(csgo), "created": [], "modified": [], "dirs": [], "installedAt": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.data = raw
        self.created = set(raw.get("created", []))
        self.dirs = set(raw.get("dirs", []))
        self.modified = {m["rel"]: m for m in raw.get("modified", []) if isinstance(m, dict) and m.get("rel")}

    def rel(self, path: Path) -> str:
        return path.relative_to(self.csgo).as_posix()

    def makedirs(self, target: Path) -> None:
        missing = []
        cur = target
        while not cur.exists() and cur != self.csgo and self.csgo in cur.parents:
            missing.append(cur)
            cur = cur.parent
        for folder in reversed(missing):
            folder.mkdir(parents=True, exist_ok=True)
            try:
                self.dirs.add(self.rel(folder))
            except ValueError:
                pass

    def put(self, src: Path, dst: Path) -> None:
        self.makedirs(dst.parent)
        if dst.exists():
            rel = self.rel(dst)
            if rel not in self.modified:
                backup = LEDGER_FILES / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, backup)
                self.modified[rel] = {"rel": rel, "backup": str(backup), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        else:
            self.created.add(self.rel(dst))
        shutil.copy2(src, dst)

    def put_text(self, dst: Path, text: str, newline: str = "\n") -> None:
        self.makedirs(dst.parent)
        if dst.exists():
            rel = self.rel(dst)
            if rel not in self.modified:
                backup = LEDGER_FILES / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, backup)
                self.modified[rel] = {"rel": rel, "backup": str(backup), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        else:
            self.created.add(self.rel(dst))
        body = text.replace("\r\n", "\n")
        if newline != "\n":
            body = body.replace("\n", newline)
        dst.write_bytes(body.encode("utf-8"))

    def copy_tree(self, src: Path, dst: Path) -> int:
        count = 0
        for item in src.rglob("*"):
            if item.is_dir():
                continue
            self.put(item, dst / item.relative_to(src))
            count += 1
        return count

    def track_tree(self, root: Path) -> None:
        """目录已存在（例如之前手动装过），只登记不复制。"""
        if not root.exists():
            return
        for item in root.rglob("*"):
            try:
                rel = self.rel(item)
            except ValueError:
                continue
            if "logs" in Path(rel).parts:
                continue
            if item.is_dir():
                self.dirs.add(rel)
            else:
                self.created.add(rel)

    def save(self) -> None:
        self.data["created"] = sorted(self.created)
        self.data["dirs"] = sorted(self.dirs)
        self.data["modified"] = list(self.modified.values())
        self.data["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_ledger(self.data)


def load_ledger() -> dict:
    if LEDGER_PATH.exists():
        try:
            return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_ledger(data: dict) -> None:
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LEDGER_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, LEDGER_PATH)
    except Exception:
        pass


def ledger_summary(csgo: Path | None) -> dict:
    data = load_ledger()
    if not csgo or data.get("csgo") != str(csgo):
        return {"count": 0, "sizeMb": 0, "installedAt": "", "owned": 0, "groups": []}
    created = data.get("created", [])
    size = 0
    base = Path(data["csgo"])
    groups: dict[str, dict] = {}
    for rel in created:
        parts = Path(rel).parts
        if len(parts) >= 3 and parts[0] == "addons" and parts[1] == "counterstrikesharp":
            key = "/".join(parts[:3])
        elif len(parts) >= 2:
            key = "/".join(parts[:2])
        else:
            key = parts[0] if parts else "?"
        bucket = groups.setdefault(key, {"path": key, "files": 0, "bytes": 0})
        bucket["files"] += 1
        try:
            p = base / rel
            if p.is_file():
                n = p.stat().st_size
                size += n
                bucket["bytes"] += n
        except OSError:
            pass
    ordered = sorted(groups.values(), key=lambda g: -g["files"])
    for bucket in ordered:
        bucket["sizeMb"] = round(bucket["bytes"] / 1048576, 1)
        bucket.pop("bytes", None)
    # 模组目录里的旧文件本来就会随目录整体删除，只有游戏本体文件需要逐个还原
    restored = [m["rel"] for m in data.get("modified", [])
                if isinstance(m, dict) and m.get("rel")
                and not m["rel"].startswith(("addons/", "overrides/"))]
    return {
        "count": len(created),
        "sizeMb": round(size / 1048576, 1),
        "installedAt": data.get("installedAt", ""),
        "owned": sum(1 for e in OWNED_ENTRIES if (csgo / e).exists()),
        "groups": ordered,
        "modifiedCount": len(restored),
        "modified": restored[:6],
    }


def deploy_bot_package(state: dict, csgo: Path, ledger: Ledger) -> int:
    copied = 0
    upgrade_core = version_tuple(package_cssharp_version()) > version_tuple(installed_cssharp_version(csgo))
    if upgrade_core:
        current = installed_cssharp_version(csgo) or "unknown"
        backup = BACKUP_DIR / f"cssharp_{current}"
        core_src = csgo / "addons" / "counterstrikesharp"
        if core_src.is_dir() and not backup.exists():
            try:
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copytree(core_src, backup, dirs_exist_ok=True)
                log(state, f"已备份旧版 CSSharp 核心 → backup/cssharp_{current}")
            except Exception as exc:  # noqa: BLE001
                log(state, f"备份 CSSharp 失败（继续）：{exc}", "error")
        log(state, f"更新 CounterStrikeSharp 核心 {current} → {package_cssharp_version()}")
    for item in sorted(PKG_BOT.iterdir()):
        if item.name in ("Panel.exe", "backup", "gameinfo.gi"):
            continue
        if item.is_file():
            ledger.put(item, csgo / item.name)
            copied += 1
            continue
        if item.name != "addons":
            if item.name == "cfg":
                # cfg 里既有模组默认值，也有 Panel / 工作台调过的值：
                # 已经存在的一律保留，只补缺的文件，避免重新安装把调参清掉
                kept = 0
                for cfg_file in sorted(item.rglob("*")):
                    if cfg_file.is_dir():
                        continue
                    dst = csgo / "cfg" / cfg_file.relative_to(item)
                    if dst.exists():
                        kept += 1
                        continue
                    ledger.put(cfg_file, dst)
                    copied += 1
                if kept:
                    log(state, f"保留 {kept} 个已存在的 cfg（不覆盖你自己的调参）")
                continue
            copied += ledger.copy_tree(item, csgo / item.name)
            continue
        addons_dst = csgo / "addons"
        for sub in sorted(item.iterdir()):
            if sub.is_file():
                ledger.put(sub, addons_dst / sub.name)
                copied += 1
                continue
            if sub.name != "counterstrikesharp":
                copied += ledger.copy_tree(sub, addons_dst / sub.name)
                continue
            for core in sorted(sub.iterdir()):
                dst = addons_dst / "counterstrikesharp" / core.name
                if core.is_file():
                    if not dst.exists():
                        ledger.put(core, dst)
                        copied += 1
                    continue
                # CSSharp 核心：新版本才整体替换，否则跳过，避免降级
                if core.name in CORE_DIRS and dst.exists() and not upgrade_core:
                    continue
                copied += ledger.copy_tree(core, dst)
    return copied


def install(state: dict, paths: dict) -> dict:
    csgo: Path | None = paths["csgo"]
    if not csgo:
        raise RuntimeError("没有找到 CS2 安装目录，请先在「部署」页手动指定")

    if not PKG_BOT.is_dir():
        raise RuntimeError(f"缺少模组目录：{PKG_BOT}")
    if not PKG_INV.is_dir():
        raise RuntimeError(f"缺少模组目录：{PKG_INV}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER_FILES.mkdir(parents=True, exist_ok=True)

    # 首次部署前保存原始 gameinfo.gi
    original = BACKUP_DIR / "gameinfo.original.gi"
    if not original.exists() and (csgo / "gameinfo.gi").exists():
        shutil.copy2(csgo / "gameinfo.gi", original)
        log(state, "已备份原始 gameinfo.gi")

    ledger = Ledger(csgo)
    # 之前手动装过的部分也登记进来，保证一键还原能清干净
    for entry in ("addons/metamod", "addons/counterstrikesharp", "overrides"):
        ledger.track_tree(csgo / entry)

    copied = deploy_bot_package(state, csgo, ledger)

    # CS2 1.42 实测：BotHider 原生插件会在进图 20 多秒后崩在 tier0.dll+0xbc62c，
    # 装完顺手把它和它的 C# 桥接禁用掉（保留文件，改后缀即可随时恢复）。
    hider_vdf = csgo / "addons" / "metamod" / "BotHider.vdf"
    if hider_vdf.exists():
        try:
            hider_vdf.rename(hider_vdf.with_name("BotHider.vdf.disabled"))
            log(state, "已禁用 BotHider 原生插件（未适配 CS2 1.42）")
        except OSError:
            pass
    plugins_root = csgo / "addons" / "counterstrikesharp" / "plugins"
    for name in ("BotHiderImpl", "BotHiderImpl.off"):
        target = plugins_root / name
        if name.endswith("Impl") and target.is_dir():
            try:
                target.rename(plugins_root / "BotHiderImpl.off")
            except OSError:
                pass

    # 换肤插件：只并入插件本体与 gamedata，不动 CSSharp 核心
    inv_plugin = PKG_INV / "addons" / "counterstrikesharp" / "plugins" / "InventorySimulator"
    if inv_plugin.is_dir():
        copied += ledger.copy_tree(inv_plugin, csgo / "addons" / "counterstrikesharp" / "plugins" / "InventorySimulator")
    inv_gamedata = PKG_INV / "addons" / "counterstrikesharp" / "gamedata" / "inventory-simulator.json"
    if inv_gamedata.exists():
        ledger.put(inv_gamedata, csgo / "addons" / "counterstrikesharp" / "gamedata" / "inventory-simulator.json")
        copied += 1

    # 备份目录（用于在线/离线切换）
    for name in ("Online", "WithBots"):
        src = PKG_BOT / "backup" / name / "gameinfo.gi"
        if src.exists():
            ledger.put(src, csgo / "backup" / name / "gameinfo.gi")

    ledger.save()
    log(state, f"部署完成，写入 {copied} 个文件")
    sanitize_invsim(csgo, state)
    return {"copied": copied, "ledger": ledger_summary(csgo)}


def repair_plugins(state: dict, paths: dict) -> dict:
    """把已装的 CounterStrikeSharp 核心升级到包内的适配版，并禁用会崩的 BotHider。

    只替换核心（bin/api/shared/lang/source/gamedata.json），不动 plugins 与 dotnet，
    因此换肤/强化插件和用户配置都不会丢。
    """
    csgo: Path | None = paths.get("csgo")
    if not csgo or not csgo.is_dir():
        raise RuntimeError("没有找到 CS2 安装目录")
    src = PKG_BOT / "addons" / "counterstrikesharp"
    if not src.is_dir():
        raise RuntimeError(f"模组包不完整：{src}")
    dst = csgo / "addons" / "counterstrikesharp"
    ledger = Ledger(csgo)
    copied = 0
    for sub in ("bin", "api", "shared", "lang", "source"):
        s = src / sub
        if not s.is_dir():
            continue
        for item in s.rglob("*"):
            if item.is_file():
                ledger.put(item, dst / item.relative_to(src))
                copied += 1
    gd = src / "gamedata" / "gamedata.json"
    if gd.exists():
        ledger.put(gd, dst / "gamedata" / "gamedata.json")
        copied += 1

    meta = csgo / "addons" / "metamod"
    meta.mkdir(parents=True, exist_ok=True)
    for name in ("counterstrikesharp.vdf", "BotController.vdf", "BotVision.vdf"):
        s = PKG_BOT / "addons" / "metamod" / name
        if s.exists() and not (meta / name).exists():
            ledger.put(s, meta / name)
            copied += 1

    hider = meta / "BotHider.vdf"
    if hider.exists():
        try:
            hider.rename(hider.with_name("BotHider.vdf.disabled"))
        except OSError:
            pass
    plugins_root = dst / "plugins"
    impl = plugins_root / "BotHiderImpl"
    if impl.is_dir():
        try:
            impl.rename(plugins_root / "BotHiderImpl.off")
        except OSError:
            pass

    ledger.save()
    version = installed_cssharp_version(csgo)
    log(state, f"插件兼容修复完成：CounterStrikeSharp {version}，写入 {copied} 个文件")
    return {"copied": copied, "cssharp": version,
            "adapted": ver_tuple(version) >= ver_tuple(MIN_CSS_VERSION)}


def uninstall(state: dict, paths: dict) -> dict:
    """一键还原：把 game/csgo 里所有模组相关内容删掉，恢复官方 gameinfo。"""
    csgo: Path | None = paths["csgo"]
    if not csgo:
        raise RuntimeError("没有找到 CS2 安装目录")
    if not (csgo / "gameinfo.gi").exists():
        raise RuntimeError("找不到 game/csgo/gameinfo.gi，目录可能不对")

    ledger = Ledger(csgo)
    # 还原前先把官方 gameinfo.gi 读进内存——稍后 backup/ 目录会被整体删掉
    restore_bytes = b""
    for candidate in (csgo / "backup" / "Online" / "gameinfo.gi",
                      PKG_BOT / "backup" / "Online" / "gameinfo.gi",
                      BACKUP_DIR / "gameinfo.original.gi",
                      LEDGER_FILES / "gameinfo.gi"):
        try:
            if candidate.is_file() and candidate.stat().st_size > 1000:
                raw = candidate.read_bytes()
                if b"addons/metamod" not in raw:
                    restore_bytes = raw
                    break
        except OSError:
            continue

    # 先确保插件处于启用位置（否则 plugins_off 里的文件会漏掉）
    try:
        if (csgo / "addons" / "counterstrikesharp" / "plugins_off").is_dir():
            set_plugins_enabled(state, paths, True)
    except Exception:
        pass

    removed_files = 0
    freed = 0
    for entry in list(OWNED_ENTRIES) + ["addons/counterstrikesharp/plugins_off"]:
        target = safe_join(csgo, entry)
        if not target or not target.exists():
            continue
        try:
            if target.is_dir():
                for item in target.rglob("*"):
                    if item.is_file():
                        removed_files += 1
                        try:
                            freed += item.stat().st_size
                        except OSError:
                            pass
                shutil.rmtree(target)
            else:
                removed_files += 1
                freed += target.stat().st_size
                target.unlink()
        except Exception as exc:  # noqa: BLE001
            log(state, f"删除 {entry} 失败：{exc}", "error")

    # 工作台写入的 cfg（wb_ 前缀一定是我们的）
    for cfg in list((csgo / "cfg").glob("wb_*.cfg")):
        try:
            freed += cfg.stat().st_size
            cfg.unlink()
            removed_files += 1
        except OSError:
            pass
    for cfg in list((csgo / "cfg").glob("gamemode_*_server.cfg")):
        try:
            if "CS2 WORKBENCH" in read_text(cfg)[:800]:
                freed += cfg.stat().st_size
                cfg.unlink()
                removed_files += 1
        except OSError:
            pass

    # 还原被模组覆盖的游戏本体文件（cfg/gamemode_*.cfg 等）
    restored_files = 0
    for rec in load_ledger().get("modified", []):
        rel = rec.get("rel") if isinstance(rec, dict) else ""
        backup = Path(rec.get("backup", "")) if isinstance(rec, dict) else None
        if not rel or rel.startswith(("addons/", "overrides/", "backup/")):
            continue
        name = Path(rel).name
        if rel.startswith("cfg/") and (name.startswith("wb_") or name.endswith("_server.cfg")):
            continue
        dst = safe_join(csgo, rel)
        if not dst or not backup or not backup.is_file():
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, dst)
            restored_files += 1
        except OSError as exc:
            log(state, f"还原 {rel} 失败：{exc}", "error")

    # 还原 gameinfo.gi（用前面读出来的官方内容）
    restored = False
    if restore_bytes:
        try:
            (csgo / "gameinfo.gi").write_bytes(restore_bytes)
            restored = True
        except OSError as exc:
            log(state, f"还原 gameinfo.gi 失败：{exc}", "error")
    if not restored:
        log(state, "没有找到原始 gameinfo.gi 备份，请用 Steam「验证文件完整性」修复", "error")
    else:
        log(state, "gameinfo.gi 已还原为无 metamod 挂载的官方版本")

    # addons 下还有 Metamod 插件（BotController / BotHider / BotVision…），
    # 按台账判断是否是我们写入的，是就一并清掉
    addons = csgo / "addons"
    created_set = set(load_ledger().get("created", []))
    if addons.is_dir() and created_set:
        for child in list(addons.iterdir()):
            rel = child.relative_to(csgo).as_posix()
            ours = any(item == rel or item.startswith(rel + "/") for item in created_set)
            if not ours:
                continue
            try:
                if child.is_dir():
                    for item in child.rglob("*"):
                        if item.is_file():
                            removed_files += 1
                            freed += item.stat().st_size
                    shutil.rmtree(child)
                else:
                    removed_files += 1
                    freed += child.stat().st_size
                    child.unlink()
            except Exception as exc:  # noqa: BLE001
                log(state, f"删除 {rel} 失败：{exc}", "error")

    # 清空 addons 目录（如果只剩空壳）
    if addons.is_dir():
        for leftover in list(addons.iterdir()):
            try:
                if leftover.is_dir() and not any(leftover.rglob("*")):
                    leftover.rmdir()
            except OSError:
                pass
        try:
            addons.rmdir()
        except OSError:
            pass

    state["mode"] = "online"
    state.setdefault("guard", {}).update({"broken": False, "suspended": False, "note": ""})
    ledger.save()
    leftovers = [e for e in OWNED_ENTRIES if (csgo / e).exists()]
    log(state, f"已还原官方状态：删除 {removed_files} 个模组文件（约 {freed / 1048576:.1f} MB），还原 {restored_files} 个本体文件")
    return {
        "removedFiles": removed_files,
        "restoredFiles": restored_files,
        "freedMb": round(freed / 1048576, 1),
        "restoredGameinfo": restored,
        "leftovers": leftovers,
    }


def verify_clean(paths: dict) -> dict:
    csgo: Path | None = paths["csgo"]
    if not csgo:
        return {"clean": False, "issues": ["未找到 CS2 目录"]}
    issues = []
    gameinfo = csgo / "gameinfo.gi"
    if not gameinfo.exists():
        issues.append("gameinfo.gi 缺失")
    else:
        text = read_text(gameinfo)
        if "addons/metamod" in text:
            issues.append("gameinfo.gi 仍挂载 metamod")
        if "csgo/overrides/" in text:
            issues.append("gameinfo.gi 仍引用 overrides")
    for entry in OWNED_ENTRIES:
        if (csgo / entry).exists():
            issues.append(f"仍存在 {entry}")
    for cfg in (csgo / "cfg").glob("wb_*.cfg"):
        issues.append(f"仍存在 cfg/{cfg.name}")
    return {"clean": not issues, "issues": issues}


# --------------------------------------------------------------------------
# 模式 / 难度
# --------------------------------------------------------------------------

def gameinfo_text(kind: str) -> str:
    if kind == "online":
        # 优先用模组自带的「在线原版」模板（保证不含 metamod 挂载）
        text = read_text(PKG_BOT / "backup" / "Online" / "gameinfo.gi")
        if not text:
            text = read_text(BACKUP_DIR / "gameinfo.original.gi")
    else:
        text = read_text(PKG_BOT / "backup" / "WithBots" / "gameinfo.gi")
    if not text:
        raise RuntimeError("找不到 gameinfo.gi 模板（请先重新部署模组）")
    if kind == "skin":
        text = re.sub(r"[ \t]*Game[ \t]+csgo/overrides/botprofile\.vpk[ \t]*\r?\n(?:[ \t]*\r?\n)?", "", text)
    elif kind == "boost":
        # 免插件强化：保留 botprofile.vpk 覆盖，只摘掉 metamod 挂载。
        # botprofile.db 与 scripts/ai/* 由引擎的虚拟文件系统直接读取，不需要任何插件。
        text = re.sub(r"[ \t]*Game[ \t]+csgo/addons/metamod[ \t]*\r?\n(?:[ \t]*\r?\n)?", "", text)
    return text


def set_plugins_enabled(state: dict, paths: dict, enabled: bool) -> None:
    cssharp = paths["csgo"] / "addons" / "counterstrikesharp"
    plugins = cssharp / "plugins"
    parked = cssharp / "plugins_off"
    plugins.mkdir(parents=True, exist_ok=True)
    if enabled:
        if parked.is_dir():
            for item in list(parked.iterdir()):
                target = plugins / item.name
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                shutil.move(str(item), str(target))
            try:
                parked.rmdir()
            except OSError:
                pass
            log(state, "已重新启用机器人相关插件")
    else:
        parked.mkdir(parents=True, exist_ok=True)
        moved = 0
        for name in BOT_PLUGINS:
            src = plugins / name
            if src.exists():
                shutil.move(str(src), str(parked / name))
                moved += 1
        if moved:
            log(state, f"已停用 {moved} 个机器人插件（仅换肤模式）")


def set_mode(state: dict, paths: dict, mode: str) -> None:
    if mode not in MODES:
        raise RuntimeError(f"未知模式：{mode}")
    csgo = paths["csgo"]
    if not csgo:
        raise RuntimeError("没有找到 CS2 目录")
    kind = MODES[mode]["gameinfo"]
    ledger = Ledger(csgo)
    ledger.put_text(csgo / "gameinfo.gi", gameinfo_text(kind))
    ledger.save()
    if mode == "skin":
        set_plugins_enabled(state, paths, False)
    elif mode == "boost":
        # 免插件模式：插件文件原样留在盘上（不删不移），只是不挂载 → 永远不会被加载
        pass
    else:
        set_plugins_enabled(state, paths, True)
    if mode in ("offline", "boost"):
        guard = state.setdefault("guard", {})
        if guard.get("suspended"):
            guard["suspended"] = False
            guard["note"] = ""
    state["mode"] = mode
    log(state, f"已切换模式：{MODES[mode]['label']}")


def set_difficulty(state: dict, paths: dict, level: str) -> None:
    if level not in ("Low", "Medium", "High"):
        raise RuntimeError("难度只能是 Low / Medium / High")
    csgo = paths["csgo"]
    src = csgo / "overrides" / level / "botprofile.vpk"
    if not src.exists():
        src = PKG_BOT / "overrides" / level / "botprofile.vpk"
    if not src.exists():
        raise RuntimeError(f"缺少难度文件：overrides/{level}/botprofile.vpk")
    ledger = Ledger(csgo)
    ledger.put(src, csgo / "overrides" / "botprofile.vpk")
    ledger.save()
    state["difficulty"] = level
    log(state, f"人机难度 → {DIFFICULTY_LABEL[level]}")


# --------------------------------------------------------------------------
# 配置生成
# --------------------------------------------------------------------------

def cfg_body(state: dict, include_plugin_cmds: bool = True) -> str:
    m = state["match"]
    inv = state["invsim"]
    lines = [
        "// ============================================================",
        "//  CS2 WORKBENCH 自动生成，请勿手动修改",
        "//  改完设置回到工作台点「写入配置」即可覆盖本文件",
        "// ============================================================",
        "",
        "// ---- 服务器 ----",
        f'hostname "{m.get("hostname", "CS2 WORKBENCH")}"',
        "sv_lan 1",
        f"sv_cheats {1 if m.get('cheats', True) else 0}",
        "",
        "// ---- 回合规则 ----",
        "mp_autoteambalance 0",
        "mp_limitteams 0",
        "mp_autokick 0",
        f"mp_friendlyfire {1 if m.get('friendlyFire') else 0}",
        f"mp_roundtime {m.get('roundTime', 1.92)}",
        f"mp_roundtime_defuse {m.get('roundTime', 1.92)}",
        f"mp_freezetime {m.get('freezeTime', 3)}",
        f"mp_startmoney {m.get('startMoney', 800)}",
        "mp_maxmoney 16000",
        f"mp_c4timer {m.get('c4timer', 40)}",
        f"mp_warmuptime {m.get('warmupTime', 0)}",
        "mp_warmup_pausetimer 1",
        "mp_do_warmup_period 0",
        "",
        "// ---- 人机 ----",
        f"bot_quota {m.get('botQuota', 10)}",
        "bot_quota_mode normal",
        "// bot_difficulty 由上面的「预设内联」块写入（5 + 按 bot 单独算），这里不覆盖",
        "sv_auto_adjust_bot_difficulty 0",
        "sv_compute_per_bot_difficulty 1",
        "bot_autodifficulty_threshold_high 20.0",
        "bot_autodifficulty_threshold_low -20.0",
        "bot_stop 0",
        "bot_dont_shoot 0",
        f"bot_allow_grenades {1 if m.get('botGrenades') else 0}",
        f"bot_eco_limit {m.get('ecoLimit', 2800)}",
        "bot_defer_to_human_goals 0",
        "bot_defer_to_human_items 0",
        "// 机器人买什么（与 CS2BotImprover/cfg 里的预设保持一致）",
        f'bot_loadout {BOT_LOADOUTS.get(str(m.get("loadout") or "random"), BOT_LOADOUTS["random"])["line"]}',
        "",
        "// ---- 机器人 AI（BotAimImprover / NadeSystem 读取）----",
        f"bot_aim {state.get('aim', 'mixed')}",
        f"bot_nades {state.get('nades', 'normal')}",
        "",
        "// ---- Inventory Simulator（换肤）----",
    ]
    if state.get("skinSource") == "site":
        lines += [
            f"// 直接连官网 {SITE_URL}，按当前 Steam 账号读取已保存的库存",
            '// 在网站上改完皮肤，"!ws" 即可刷新',
            f'invsim_url "{SITE_URL}"',
        ]
    else:
        lines += [
            "// 使用工作台本地库存（离线可用），游戏内 !ws 立即生效",
            f'invsim_url "http://127.0.0.1:{RUNTIME_PORT}"',
        ]
    lines += [
        'invsim_file ""',
        f"invsim_ws_enabled {1 if inv.get('ws', True) else 0}",
        f"invsim_ws_immediately {1 if inv.get('immediately', True) else 0}",
        f"invsim_loadout_enabled {1 if inv.get('loadout', True) else 0}",
        f"invsim_fallback_team {1 if inv.get('fallbackTeam', True) else 0}",
        f"invsim_spray_enabled {1 if inv.get('spray', False) else 0}",
        f"invsim_stattrak_ignore_bots {1 if inv.get('stattrakIgnoreBots', False) else 0}",
        "invsim_persist_inventory 1",
        "invsim_require_inventory 0",
        "",
    ]
    if state.get("mode") == "boost":
        # 免插件模式：插件专属 cvar 一条都不写，否则控制台满屏 Unknown command
        kept: list[str] = []
        skipping = False
        for line in lines:
            if line.startswith("// ---- Inventory Simulator") or "BotAimImprover" in line or "机器人买什么" in line:
                skipping = True
                continue
            if skipping:
                continue
            if line.startswith(("bot_loadout", "bot_aim", "bot_nades")):
                continue
            kept.append(line)
        kept += [
            "// 免插件强化模式：机器人强度由 overrides/botprofile.vpk 决定",
            "// （botprofile.db + scripts/ai 行为树，引擎直接读取，不需要插件）",
            "",
        ]
        lines = kept
    return "\n".join(lines)


def preset_inline_lines(state: dict, preset: str) -> list[str]:
    """读模组预设 cfg 的原文，用于内联兜底。

    为什么需要内联：`exec my_bot_normal_config` 只有在引擎能解析到文件、且这行没被
    任何字符污染时才会执行。曾经把它写成 `exec xxx  // 中文注释` 的同一行，
    CS2 把整行当注释丢掉，结果 bot_difficulty / custom_bot_difficulty 全都没生效，
    进游戏就是原版人机。现在：exec 单独一行 + 预设内容直接内联，两层保证。
    """
    name = BOT_PRESETS[preset]["file"]
    for base in (bot_cfg_dir(state), PKG_BOT / "cfg"):
        if not base:
            continue
        path = base / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
        return [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("//")]
    return []


def preset_block(state: dict, preset: str) -> list[str]:
    """内联预设（放在工作台自己的设置之前，这样界面上的选择仍然后压生效）。"""
    if state.get("mode") == "boost":
        return []
    lines = preset_inline_lines(state, preset)
    if not lines:
        return []
    name = BOT_PRESETS[preset]["file"]
    return ["", f"// ---- 机器人预设内联（{name}）：即使 exec 没跑，这些 cvar 也一定生效 ----", *lines, ""]


def match_cfg_text(state: dict) -> str:
    body = cfg_body(state)
    mode = state["match"]["gamemode"]
    preset = active_preset(state)
    head = ["// 启动时执行", ""]
    if mode in GAMEMODES and GAMEMODES[mode]["cfg"]:
        head.append(f"exec {GAMEMODES[mode]['cfg']}")
    if state.get("mode") != "boost":
        # 注释必须单独一行：exec 后面跟 // 会让整行被当成注释
        head.append(f"exec {BOT_PRESETS[preset]['file'][:-4]}")
        head.append(f"// 机器人预设：{BOT_PRESETS[preset]['label']}")
    head.append("")
    parts = ["\n".join(head).rstrip("\n"), "\n".join(preset_block(state, preset)).strip("\n"), body]
    return "\n\n".join(p for p in parts if p) + "\n"


def server_cfg_text(state: dict) -> str:
    mode = state["match"]["gamemode"]
    preset = active_preset(state)
    head = [
        "// 每次加载地图后执行（覆盖 gamemode_*.cfg 中的默认值）",
        "",
    ]
    if mode in GAMEMODES and GAMEMODES[mode]["cfg"]:
        head.append(f"exec {GAMEMODES[mode]['cfg']}")
    if state.get("mode") != "boost":
        head.append(f"exec {BOT_PRESETS[preset]['file'][:-4]}")
        head.append(f"// CS2BotImprover 预设：{BOT_PRESETS[preset]['label']}")
    head.append("")
    parts = ["\n".join(head).rstrip("\n"), "\n".join(preset_block(state, preset)).strip("\n"), cfg_body(state)]
    return "\n\n".join(p for p in parts if p) + "\n"


def sanitize_invsim(csgo: Path, state: dict) -> None:
    """插件加载时会读取 invsim_file；客户端此时全局变量尚未初始化，
    若该文件存在会导致 InventorySimulator 加载失败。库存改由本地接口提供，
    所以这里确保默认路径下没有这个文件。"""
    legacy = csgo / "addons" / "counterstrikesharp" / "configs" / "plugins" / "InventorySimulator" / "inventories.json"
    if legacy.exists():
        try:
            legacy.unlink()
            log(state, "已移除遗留的 inventories.json（改用本地接口提供库存）")
        except OSError:
            pass


def write_cfgs(state: dict, paths: dict) -> dict:
    csgo = paths["csgo"]
    if not csgo:
        raise RuntimeError("没有找到 CS2 目录")
    sanitize_invsim(csgo, state)
    cfg_dir = csgo / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    mode = state["match"]["gamemode"]

    files = {}
    files["wb_match.cfg"] = match_cfg_text(state)
    files[f"gamemode_{mode}_server.cfg"] = server_cfg_text(state)
    files["wb_inmatch.cfg"] = "\n".join([
        "// 进入地图后可在控制台 exec wb_inmatch 应用机器人 AI 设置",
        f"bot_aim {state.get('aim', 'mixed')}",
        f"bot_nades {state.get('nades', 'normal')}",
        "",
    ])
    ledger = Ledger(csgo)
    for name, text in files.items():
        ledger.put_text(cfg_dir / name, text)
    ledger.save()
    log(state, f"已写入配置：wb_match.cfg / gamemode_{mode}_server.cfg")
    # 顺手把插件自己的预设 cfg 也改成一致的（两边都写，避免下次部署把它覆盖回旧值）
    try:
        sync = sync_bot_cfg(state, paths)
        if sync.get("ok"):
            files[BOT_PRESETS[active_preset(state)]["file"]] = "（原地更新，不是整体覆盖）"
    except Exception as exc:  # noqa: BLE001
        log(state, f"同步插件预设失败：{exc}", "error")
    return {"files": sorted(files)}


# --------------------------------------------------------------------------
# 与 CS2BotImprover 插件自己的 cfg 同步
# 插件真正的机器人强度来自它自己的 my_bot_*_config.cfg（Panel.exe 改的也是它），
# 工作台只负责把界面上那几项「翻译」成 cvar 写回去，其余行一个都不动。
# --------------------------------------------------------------------------

BOT_PRESETS = {
    "normal": {"label": "常规强化", "file": "my_bot_normal_config.cfg",
               "desc": "默认的强化 AI：高视野、无拉枪延迟、单点爆头"},
    "ffa": {"label": "自由混战", "file": "my_bot_ffa_config.cfg",
            "desc": "个人死斗式 bot，随机买枪、见人就冲"},
    "rush": {"label": "冲锋 Rush", "file": "my_bot_rush_config.cfg",
             "desc": "Rush 行为树，bot 会整队冲点"},
}

BOT_LOADOUTS = {
    "random": {"label": "随机购买", "line": '""'},
    "armor": {"label": "全甲", "line": "vesthelm defuser"},
    "rifle": {"label": "全甲步枪", "line": "ak47 vesthelm defuser"},
    "sniper": {"label": "全甲狙击", "line": "awp vesthelm defuser"},
    "pistol": {"label": "全甲手枪", "line": "p250 vesthelm defuser"},
}

# 工作台接管的 cvar（其余全部原样保留）
MANAGED_CVARS = (
    "sv_cheats", "mp_friendlyfire", "mp_roundtime", "mp_roundtime_defuse",
    "mp_freezetime", "mp_startmoney", "mp_c4timer", "bot_quota",
    "bot_allow_grenades", "bot_eco_limit", "bot_loadout", "bot_aim", "bot_nades",
)


def active_preset(state: dict) -> str:
    preset = str(state.get("match", {}).get("preset") or "normal")
    return preset if preset in BOT_PRESETS else "normal"


def bot_cfg_dir(state: dict) -> Path | None:
    bd = bot_improver_dir(state)
    if bd and (bd / "cfg").is_dir():
        return bd / "cfg"
    if (PKG_BOT / "cfg").is_dir():
        return PKG_BOT / "cfg"
    return None


def bot_cfg_paths(state: dict, paths: dict, preset: str | None = None) -> tuple[Path | None, Path | None]:
    """(模组目录里的那份, 游戏 cfg 里的那份)"""
    name = BOT_PRESETS[preset or active_preset(state)]["file"]
    mod_dir = bot_cfg_dir(state)
    csgo = paths.get("csgo")
    return (mod_dir / name if mod_dir else None,
            csgo / "cfg" / name if csgo else None)


def managed_cvars(state: dict) -> dict:
    """把界面上那几项翻译成插件认识的行。"""
    m = state.get("match", {})
    loadout = BOT_LOADOUTS.get(str(m.get("loadout") or "random"), BOT_LOADOUTS["random"])
    return {
        "sv_cheats": "1" if m.get("cheats", True) else "0",
        "mp_friendlyfire": "1" if m.get("friendlyFire") else "0",
        "mp_roundtime": str(m.get("roundTime", 1.92)),
        "mp_roundtime_defuse": str(m.get("roundTime", 1.92)),
        "mp_freezetime": str(m.get("freezeTime", 3)),
        "mp_startmoney": str(m.get("startMoney", 800)),
        "mp_c4timer": str(m.get("c4timer", 40)),
        "bot_quota": str(m.get("botQuota", 10)),
        "bot_allow_grenades": "1" if m.get("botGrenades") else "0",
        "bot_eco_limit": str(m.get("ecoLimit", 2800)),
        "bot_loadout": loadout["line"],
        "bot_aim": str(state.get("aim", "mixed")),
        "bot_nades": str(state.get("nades", "normal")),
    }


def set_cvar(text: str, key: str, value: str) -> str:
    """原地替换一行 cvar；文件里没有就追加。注释与其它行原样保留。"""
    pattern = re.compile(rf"^([ \t]*){re.escape(key)}([ \t]+)(.*)$", re.I | re.M)

    def _sub(match: re.Match) -> str:
        return f"{match.group(1)}{key} {value}"

    if pattern.search(text):
        return pattern.sub(_sub, text)
    tail = "" if text.endswith("\n") or not text else "\n"
    return f"{text}{tail}{key} {value}\n"


def _read_cfg(path: Path) -> tuple[str, str]:
    """读 cfg 并记住它的换行风格，写回时原样还原（免得整个文件看起来都被改了）。"""
    raw = path.read_bytes()
    text = raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    return text, ("\r\n" if b"\r\n" in raw else "\n")


def _backup_cfg_once(path: Path) -> None:
    """第一次改动模组原文件前留一份，后悔了能换回去。"""
    target = HOME_DIR / "botcfg-backup" / path.name
    if target.exists():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    except OSError:
        pass


def read_bot_cfg(state: dict, paths: dict, preset: str | None = None) -> dict:
    """读当前预设文件里的实际值（游戏里的那份优先）。"""
    mod_path, game_path = bot_cfg_paths(state, paths, preset)
    text = ""
    source = ""
    for candidate in (game_path, mod_path, (PKG_BOT / "cfg" / BOT_PRESETS[preset or active_preset(state)]["file"])):
        if candidate and candidate.exists():
            text = read_text(candidate)
            if text:
                source = str(candidate)
                break
    values: dict[str, str] = {}
    for key in MANAGED_CVARS:
        match = re.search(rf"^[ \t]*{re.escape(key)}[ \t]+(.*)$", text, re.I | re.M)
        if match:
            values[key] = match.group(1).strip()
    return {"values": values, "source": source, "exists": bool(text)}


def sync_bot_cfg(state: dict, paths: dict, preset: str | None = None) -> dict:
    """把界面设置写回插件 cfg：模组目录 + 游戏 cfg 两份保持一致。"""
    preset = preset or active_preset(state)
    mod_path, game_path = bot_cfg_paths(state, paths, preset)
    fallback = PKG_BOT / "cfg" / BOT_PRESETS[preset]["file"]
    base = ""
    newline = "\n"
    for candidate in (game_path, mod_path, fallback):
        if candidate and candidate.exists():
            base, newline = _read_cfg(candidate)
            if base.strip():
                break
    if not base.strip():
        return {"ok": False, "error": f"找不到预设文件：{BOT_PRESETS[preset]['file']}", "files": []}

    text = base
    for key, value in managed_cvars(state).items():
        text = set_cvar(text, key, value)

    written: list[str] = []
    if mod_path and mod_path.parent.is_dir():
        try:
            current = mod_path.read_bytes().decode("utf-8", "replace") if mod_path.exists() else ""
            if current.replace("\r\n", "\n") != text:
                if mod_path.exists():
                    _backup_cfg_once(mod_path)
                mod_path.write_bytes(text.replace("\n", newline).encode("utf-8"))
                written.append(str(mod_path))
        except OSError as exc:
            log(state, f"写入模组配置失败：{exc}", "error")
    if game_path and paths.get("csgo"):
        try:
            unchanged = game_path.exists() and read_text(game_path).strip() == text.strip()
        except OSError:
            unchanged = False
        if not unchanged:
            ledger = Ledger(paths["csgo"])
            ledger.put_text(game_path, text, newline=newline)
            ledger.save()
            written.append(str(game_path))
    state["botSync"] = {"at": time.strftime("%H:%M:%S"), "preset": preset,
                        "files": written, "newline": "CRLF" if newline == "\r\n" else "LF"}
    return {"ok": True, "preset": preset, "files": written,
            "values": managed_cvars(state)}


def hydrate_from_bot_cfg(state: dict, paths: dict) -> dict:
    """开机时把插件 cfg 里的真实值读回 state，界面显示的就是游戏里生效的。"""
    data = read_bot_cfg(state, paths)
    values = data["values"]
    if not values:
        return state
    m = state.setdefault("match", {})
    mapping = {
        "mp_roundtime": ("roundTime", float), "mp_roundtime_defuse": ("roundTime", float),
        "mp_freezetime": ("freezeTime", int), "mp_startmoney": ("startMoney", int),
        "mp_c4timer": ("c4timer", int), "bot_quota": ("botQuota", int),
        "bot_eco_limit": ("ecoLimit", int),
    }
    for key, (field, cast) in mapping.items():
        raw = values.get(key)
        if not raw:
            continue
        try:
            m[field] = cast(float(raw))
        except (TypeError, ValueError):
            continue
    if "mp_friendlyfire" in values:
        m["friendlyFire"] = values["mp_friendlyfire"].startswith("1")
    if "bot_allow_grenades" in values:
        m["botGrenades"] = values["bot_allow_grenades"].startswith("1")
    if "sv_cheats" in values:
        m["cheats"] = values["sv_cheats"].startswith("1")
    line = values.get("bot_loadout")
    if line is not None:
        for key, item in BOT_LOADOUTS.items():
            if item["line"].strip('"') == line.strip('"'):
                m["loadout"] = key
                break
    if values.get("bot_aim") in ("head", "mixed", "body"):
        state["aim"] = values["bot_aim"]
    if values.get("bot_nades") in ("off", "less", "normal", "more", "max"):
        state["nades"] = values["bot_nades"]
    return state


# --------------------------------------------------------------------------
# 自检 / 自愈：每次开机、每次启动游戏前都过一遍
# 换肤和强化人机依赖的是「gameinfo 挂载 + addons 文件 + 插件目录 + 难度 vpk」，
# 任何一样被 CS2 校验、模组 Panel、或误操作改回去，这里都会重新补齐。
# --------------------------------------------------------------------------

def _ensure_gameinfo(state: dict, paths: dict, ledger: Ledger, fixed: list, missing: list) -> None:
    csgo = paths["csgo"]
    kind = MODES.get(state.get("mode", "offline"), MODES["offline"])["gameinfo"]
    if kind == "boost":
        # 免插件强化：必须是「有 overrides 覆盖、无 metamod」，且不受隔离状态影响
        target = csgo / "gameinfo.gi"
        text = read_text(target)
        if "csgo/addons/metamod" in text or "csgo/overrides/botprofile.vpk" not in text:
            try:
                ledger.put_text(target, gameinfo_text("boost"))
                fixed.append("gameinfo 切到免插件强化挂载")
            except Exception as exc:  # noqa: BLE001
                missing.append(f"gameinfo 模板（{exc}）")
        return
    if (state.get("guard") or {}).get("suspended"):
        # 隔离状态：用户/守护主动摘掉了模组挂载，自检不要再装回去
        return
    target = csgo / "gameinfo.gi"
    text = read_text(target)
    has_meta = "csgo/addons/metamod" in text
    if kind in ("withbots", "skin"):
        need = not has_meta or (kind == "withbots" and "csgo/overrides/botprofile.vpk" not in text)
        if need:
            try:
                ledger.put_text(target, gameinfo_text(kind))
                fixed.append("gameinfo 恢复模组挂载")
            except Exception as exc:  # noqa: BLE001
                missing.append(f"gameinfo 模板（{exc}）")
    elif kind == "online" and has_meta:
        # 在线原版：不主动改，交给「恢复正常在线模式」按钮
        pass


def _ensure_addons(state: dict, paths: dict, ledger: Ledger, fixed: list, missing: list) -> None:
    csgo = paths["csgo"]
    addons = csgo / "addons"
    if not (addons / "metamod" / "bin").is_dir() and (PKG_BOT / "addons" / "metamod").is_dir():
        ledger.copy_tree(PKG_BOT / "addons" / "metamod", addons / "metamod")
        fixed.append("重新部署 Metamod")
    for vdf in ("metamod.vdf", "metamod_x64.vdf"):
        src = PKG_BOT / "addons" / vdf
        if src.exists() and not (addons / vdf).exists():
            ledger.put(src, addons / vdf)
            fixed.append(f"补齐 {vdf}")
    cssharp = addons / "counterstrikesharp"
    if not (cssharp / "bin").is_dir():
        core = PKG_BOT / "addons" / "counterstrikesharp"
        if core.is_dir():
            ledger.copy_tree(core, cssharp)
            fixed.append("重新部署 CounterStrikeSharp 核心")
        else:
            missing.append("CounterStrikeSharp 核心")


def _ensure_plugins(state: dict, paths: dict, ledger: Ledger, fixed: list, missing: list) -> None:
    cssharp = paths["csgo"] / "addons" / "counterstrikesharp"
    plugins = cssharp / "plugins"
    parked = cssharp / "plugins_off"
    plugins.mkdir(parents=True, exist_ok=True)
    wanted = list(BOT_PLUGINS) + ["InventorySimulator"]
    for name in wanted:
        dst = plugins / name
        if dst.is_dir():
            continue
        off = parked / name
        if off.is_dir():
            try:
                shutil.move(str(off), str(dst))
                fixed.append(f"启用插件 {name}")
                continue
            except OSError:
                pass
        src = PKG_BOT / "addons" / "counterstrikesharp" / "plugins" / name
        if not src.is_dir() and PKG_INV:
            src = PKG_INV / "addons" / "counterstrikesharp" / "plugins" / name
        if src.is_dir():
            ledger.copy_tree(src, dst)
            fixed.append(f"补齐插件 {name}")
        else:
            missing.append(f"插件 {name}")
    if parked.is_dir() and not any(parked.iterdir()):
        try:
            parked.rmdir()
        except OSError:
            pass
    # BotHider 的原生模块未适配当前 CS2，留着会被 CSSharp 加载并拖崩游戏
    impl = plugins / "BotHiderImpl"
    if impl.is_dir():
        try:
            if (plugins / "BotHiderImpl.off").is_dir():
                shutil.rmtree(impl)
            else:
                impl.rename(plugins / "BotHiderImpl.off")
            fixed.append("已禁用 BotHiderImpl")
        except OSError:
            pass


def _ensure_difficulty(state: dict, paths: dict, ledger: Ledger, fixed: list, missing: list) -> None:
    level = state.get("difficulty", "Medium")
    if state.get("mode") not in ("offline", "boost") or level not in DIFFICULTY_LABEL:
        return
    vpk_dir = paths["csgo"] / "overrides"
    active = vpk_dir / "botprofile.vpk"
    target = vpk_dir / level / "botprofile.vpk"
    if not target.exists() and (PKG_BOT / "overrides" / level / "botprofile.vpk").exists():
        ledger.put(PKG_BOT / "overrides" / level / "botprofile.vpk", target)
        fixed.append(f"补齐难度文件 {level}")
    try:
        if target.exists() and (not active.exists() or sha(active) != sha(target)):
            ledger.put(target, active)
            fixed.append(f"难度档位校准为 {DIFFICULTY_LABEL[level]}")
    except OSError:
        pass


def _ensure_plugins_compat(state: dict, paths: dict, ledger: Ledger, fixed: list, missing: list) -> None:
    """CounterStrikeSharp 低于适配版就从包内升级；顺手禁用会崩的 BotHider。"""
    csgo = paths["csgo"]
    css = csgo / "addons" / "counterstrikesharp"
    if not css.is_dir():
        return
    installed = installed_cssharp_version(csgo)
    pkg = package_cssharp_version()
    if installed and pkg and ver_tuple(installed) < ver_tuple(MIN_CSS_VERSION) \
            and ver_tuple(pkg) >= ver_tuple(MIN_CSS_VERSION):
        src = PKG_BOT / "addons" / "counterstrikesharp"
        for sub in ("bin", "api", "shared", "lang", "source"):
            folder = src / sub
            if not folder.is_dir():
                continue
            for item in folder.rglob("*"):
                if item.is_file():
                    ledger.put(item, css / item.relative_to(src))
        gd = src / "gamedata" / "gamedata.json"
        if gd.exists():
            ledger.put(gd, css / "gamedata" / "gamedata.json")
        fixed.append(f"CounterStrikeSharp {installed} → {pkg}")
    elif installed and ver_tuple(installed) < ver_tuple(MIN_CSS_VERSION) and not missing:
        missing.append(f"CounterStrikeSharp {installed} 低于适配版 {MIN_CSS_VERSION}")

    meta = csgo / "addons" / "metamod"
    hider = meta / "BotHider.vdf"
    if hider.exists():
        try:
            hider.rename(hider.with_name("BotHider.vdf.disabled"))
            fixed.append("已禁用 BotHider")
        except OSError:
            pass
    impl = css / "plugins" / "BotHiderImpl"
    if impl.is_dir():
        try:
            impl.rename(css / "plugins" / "BotHiderImpl.off")
        except OSError:
            pass

    for name in ("counterstrikesharp.vdf", "BotController.vdf", "BotVision.vdf"):
        s = PKG_BOT / "addons" / "metamod" / name
        if s.exists() and not (meta / name).exists():
            ledger.put(s, meta / name)
            fixed.append(name)


def ensure_ready(state: dict, paths: dict, repair: bool = True, quiet: bool = False) -> dict:
    """环境自检（可自动修复）。返回 fixed / missing，界面上直接能看到修了什么。"""
    csgo = paths.get("csgo")
    if not csgo or not csgo.is_dir():
        return {"ok": False, "fixed": [], "missing": ["CS2 目录"], "at": time.strftime("%H:%M:%S")}
    fixed: list[str] = []
    missing: list[str] = []
    ledger = Ledger(csgo)
    steps = (_ensure_gameinfo, _ensure_addons, _ensure_plugins, _ensure_plugins_compat, _ensure_difficulty)
    for step in steps:
        try:
            step(state, paths, ledger, fixed, missing)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{step.__name__}: {exc}")
    if fixed:
        ledger.save()
    try:
        sync = sync_bot_cfg(state, paths)
        if sync.get("ok") and not (csgo / "cfg" / "wb_match.cfg").exists():
            write_cfgs(state, paths)
    except Exception as exc:  # noqa: BLE001
        missing.append(f"cfg 同步: {exc}")
    result = {"ok": not missing, "fixed": fixed, "missing": missing,
              "at": time.strftime("%H:%M:%S")}
    state["selfCheck"] = result
    if fixed and not quiet:
        log(state, "环境自检已修复：" + "、".join(fixed))
    elif missing and not quiet:
        log(state, "环境自检发现问题：" + "、".join(missing), "error")
    return result


# --------------------------------------------------------------------------
# 库存（换肤）
# --------------------------------------------------------------------------

def item_payload(entry: dict, def_index: int, wear_range: tuple[float, float] | None = None) -> dict | None:
    kit = entry.get("paint")
    if not kit:
        return None
    wear = entry.get("wear")
    wear = 0.0 if wear is None else float(wear)
    if wear_range:
        wear = min(max(wear, wear_range[0]), wear_range[1])
    wear = min(max(wear, 0.0), 1.0)
    payload = {"def": int(def_index), "paint": int(kit), "wear": round(wear, 4)}
    seed = entry.get("seed")
    if seed not in (None, ""):
        payload["seed"] = int(seed)
    stattrak = entry.get("stattrak")
    if stattrak not in (None, ""):
        payload["stattrak"] = int(stattrak)
    nametag = (entry.get("nametag") or "").strip()
    if nametag:
        payload["nametag"] = nametag
    return payload


def build_inventory(state: dict) -> dict:
    model = state.get("inventory", {})
    teams = model.get("2", {}) or {}
    teams_ct = model.get("3", {}) or {}
    payload = {
        "agents": {},
        "collectible": None,
        "ctWeapons": {},
        "gloves": {},
        "graffiti": None,
        "knives": {},
        "loadout": None,
        "musicKit": None,
        "pet": None,
        "tWeapons": {},
    }
    by_team = {"2": teams, "3": teams_ct}
    names = {"2": "tWeapons", "3": "ctWeapons"}
    for team, data in by_team.items():
        knife = data.get("knife") or {}
        if knife.get("def"):
            item = item_payload(knife, knife["def"])
            if item:
                payload["knives"][team] = item
        gloves = data.get("gloves") or {}
        if gloves.get("def"):
            item = item_payload(gloves, gloves["def"])
            if item:
                payload["gloves"][team] = item
        for def_index, entry in (data.get("weapons") or {}).items():
            item = item_payload(entry, int(def_index))
            if item:
                payload[names[team]][str(def_index)] = item
    return payload


def save_inventory(state: dict, paths: dict) -> dict:
    csgo = paths["csgo"]
    if not csgo:
        raise RuntimeError("没有找到 CS2 目录")
    steam_id = (state.get("steamId") or "").strip()
    if not re.fullmatch(r"7656119\d{10}", steam_id):
        raise RuntimeError("SteamID64 不正确（应为 7656119 开头的 17 位数字）")

    inventory = build_inventory(state)
    payload = {steam_id: inventory}

    # 注意：插件的 invsim_file 只在插件加载瞬间读取，而客户端此时全局变量还没初始化，
    # 直接放 inventories.json 会让 InventorySimulator 加载失败。所以默认不放这个文件，
    # 改由本地 HTTP 接口按官方 API 的格式提供库存。
    legacy = csgo / "addons" / "counterstrikesharp" / "configs" / "plugins" / "InventorySimulator" / "inventories.json"
    if legacy.exists():
        try:
            legacy.unlink()
            log(state, "已移除 inventories.json（改由本地接口提供库存）")
        except OSError:
            pass

    export_dir = HOME_DIR / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "inventories.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (HOME_DIR / "inventory.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    counts = {
        "t": len(inventory["tWeapons"]),
        "ct": len(inventory["ctWeapons"]),
        "knife": len(inventory["knives"]),
        "gloves": len(inventory["gloves"]),
    }
    tail = "游戏内 !ws 立即生效" if state.get("skinSource") != "site" else "（当前皮肤源是官网，本地库存仅作备份）"
    log(state, f"库存已保存（T {counts['t']} / CT {counts['ct']} / 刀 {counts['knife']} / 手套 {counts['gloves']}），{tail}")
    return {"path": str(export_dir / "inventories.json"), "payload": payload, "counts": counts}


# --------------------------------------------------------------------------
# 官网库存（inventory.cstrike.app）
# --------------------------------------------------------------------------

def site_equipped(steam_id: str, timeout: int = 15) -> dict:
    url = f"{SITE_URL}/api/equipped/v5/{steam_id}.json"
    request = urllib.request.Request(url, headers={
        "User-Agent": "CS2Workbench/1.1 (+local)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"官网返回的不是 JSON：{exc}") from exc
    return data if isinstance(data, dict) else {}


def _clean_number(value):
    if value in (None, "", -1, "-1"):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def model_from_equipped(data: dict) -> dict:
    """把官网 EquippedV5Response 转成工作台的编辑模型。"""
    model = {"2": {"weapons": {}}, "3": {"weapons": {}}}
    for team, key in (("2", "tWeapons"), ("3", "ctWeapons")):
        for def_index, item in (data.get(key) or {}).items():
            if not isinstance(item, dict) or not item.get("paint"):
                continue
            entry = {"paint": int(item["paint"])}
            if item.get("wear") is not None:
                entry["wear"] = round(float(item["wear"]), 4)
            for field in ("seed", "stattrak"):
                value = _clean_number(item.get(field))
                if value is not None:
                    entry[field] = value
            if item.get("nametag"):
                entry["nametag"] = str(item["nametag"])
            model[team]["weapons"][str(def_index)] = entry

        knife = (data.get("knives") or {}).get(team)
        if isinstance(knife, dict) and knife.get("def") and knife.get("paint"):
            entry = {"def": int(knife["def"]), "paint": int(knife["paint"])}
            if knife.get("wear") is not None:
                entry["wear"] = round(float(knife["wear"]), 4)
            if knife.get("nametag"):
                entry["nametag"] = str(knife["nametag"])
            model[team]["knife"] = entry

        gloves = (data.get("gloves") or {}).get(team)
        if isinstance(gloves, dict) and gloves.get("def") and gloves.get("paint"):
            entry = {"def": int(gloves["def"]), "paint": int(gloves["paint"])}
            if gloves.get("wear") is not None:
                entry["wear"] = round(float(gloves["wear"]), 4)
            model[team]["gloves"] = entry
    return model


def inventory_counts(model: dict) -> dict:
    return {
        "t": len((model.get("2") or {}).get("weapons") or {}),
        "ct": len((model.get("3") or {}).get("weapons") or {}),
        "knife": sum(1 for t in ("2", "3") if (model.get(t) or {}).get("knife")),
        "gloves": sum(1 for t in ("2", "3") if (model.get(t) or {}).get("gloves")),
    }


def pull_site_inventory(state: dict, steam_id: str) -> dict:
    if not re.fullmatch(r"7656119\d{10}", steam_id or ""):
        raise RuntimeError("SteamID64 不正确（7656119 开头的 17 位数字）")
    try:
        data = site_equipped(steam_id)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"连接 inventory.cstrike.app 失败：{exc}") from exc

    model = model_from_equipped(data)
    counts = inventory_counts(model)
    total = counts["t"] + counts["ct"] + counts["knife"] + counts["gloves"]
    if not total:
        raise RuntimeError("这个账号在 inventory.cstrike.app 上没有已保存的库存（先在网站上配好并保存）")

    state["inventory"] = model
    state["siteCache"] = {
        "at": time.strftime("%H:%M:%S"),
        "steamId": steam_id,
        "counts": counts,
    }
    extras = {
        "agents": len(data.get("agents") or {}),
        "collectible": 1 if data.get("collectible") else 0,
        "musicKit": 1 if data.get("musicKit") else 0,
        "stickers": sum(len(item.get("stickers") or []) for key in ("tWeapons", "ctWeapons")
                        for item in (data.get(key) or {}).values() if isinstance(item, dict)),
    }
    log(state, f"已从官网拉取库存：T {counts['t']} / CT {counts['ct']} / 刀 {counts['knife']} / 手套 {counts['gloves']}")
    return {"inventory": model, "counts": counts, "extras": extras, "steamId": steam_id}


# --------------------------------------------------------------------------
# 启动
# --------------------------------------------------------------------------

def build_launch_args(state: dict, paths: dict) -> list[str]:
    m = state["match"]
    suspended = bool((state.get("guard") or {}).get("suspended")) and state.get("mode") != "boost"
    args = ["-insecure", "-console", "-novid"]
    if not suspended:
        # 隔离模式（模组已挂起）下不 exec 模组 cfg，免得满屏 unknown command；
        # 免插件强化模式要 exec，bot_quota / bot_allow_grenades 这些是官方 cvar
        args += ["+exec", "wb_match.cfg"]
    if m.get("gamemode") in GAMEMODES:
        mode = GAMEMODES[m["gamemode"]]
        args += ["+game_type", str(mode["gt"]), "+game_mode", str(mode["gm"])]
    if m.get("autoMap") and m.get("map"):
        args += ["+map", m["map"]]
    extra = (m.get("extraArgs") or "").strip()
    if extra:
        args += extra.split()
    return args


def launch(state: dict, paths: dict, use_steam: bool = True) -> dict:
    if state.get("mode") == "online":
        raise RuntimeError("当前是「在线原版」模式，请先切到「强化人机（免插件）」或「离线全功能」")
    guard = state.setdefault("guard", {})
    auto_boost = False
    report = compat_report(state, paths)
    if guard.get("suspended") and report.get("adapted"):
        # 插件已升级到适配版，之前的隔离状态不再需要
        guard["suspended"] = False
        guard["broken"] = False
        guard["note"] = ""
        log(state, "检测到 CounterStrikeSharp 已升级到适配版，已自动解除模组隔离")
    if state.get("mode") == "offline":
        # 只有插件版本确实低于适配要求时才降级；适配版（>= 1.0.381）直接跑完整模组
        if report.get("status") == "broken" and not report.get("adapted"):
            set_mode(state, paths, "boost")
            auto_boost = True
            log(state, "完整模组与当前 CS2 不兼容，已自动切到「强化人机（免插件）」，"
                       "机器人手感仍按所选难度覆盖生效", "error")
    if guard.get("suspended") and state.get("mode") != "boost":
        # 模组被挂起（上次崩溃后自动隔离）：不再做模组自检，直接按原版开局
        write_cfgs(state, paths)
        args = build_launch_args(state, paths)
        result = _spawn_game(paths, args, use_steam)
        log(state, "模组处于隔离状态，按原版启动：" + " ".join(args))
        start_guard_watch(state)
        return result
    # 开局前先自检：gameinfo 挂载、插件目录、难度 vpk 有问题就先补好，
    # 免得点了「启动」进游戏却是原版 bot + 没皮肤
    check = ensure_ready(state, paths, repair=True, quiet=True)
    if check.get("missing") and state.get("mode") != "boost":
        raise RuntimeError("环境不完整：" + "、".join(check["missing"]) + "；请先点「安装 / 更新模组」")
    write_cfgs(state, paths)
    if state["mode"] in ("offline", "boost") and state.get("difficulty"):
        vpk = paths["csgo"] / "overrides" / "botprofile.vpk"
        level = paths["csgo"] / "overrides" / state["difficulty"] / "botprofile.vpk"
        if level.exists() and (not vpk.exists() or sha(vpk) != sha(level)):
            set_difficulty(state, paths, state["difficulty"])

    args = build_launch_args(state, paths)
    result = _spawn_game(paths, args, use_steam)
    result["autoBoost"] = auto_boost
    log(state, "已启动 CS2：" + " ".join(args))
    start_guard_watch(state)
    return result


def _spawn_game(paths: dict, args: list[str], use_steam: bool = True) -> dict:
    if use_steam and paths.get("steamExe"):
        cmd = [str(paths["steamExe"]), "-applaunch", "730"] + args
        cwd = str(paths["steamExe"].parent)
    else:
        cmd = [str(paths["exe"])] + args
        cwd = str(paths["exe"].parent)
    subprocess.Popen(cmd, cwd=cwd, close_fds=True)
    return {"command": cmd}


# --------------------------------------------------------------------------
# 版本兼容性：游戏版本 vs 模组构建时间
# --------------------------------------------------------------------------

APP_VERSION = "1.0.5"

# CS2 1.42.0.1（2026-11-23）之后，只有 CSSharp 1.0.381+ 能稳定加载插件；
# 低版本会在进图后 15-25 秒崩在 tier0.dll+0xbc62c。
MIN_CSS_VERSION = "1.0.381"


def ver_tuple(text: str) -> tuple:
    """把 1.0.381 这类版本号变成可比较的元组。"""
    parts = []
    for chunk in str(text or "").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or [0])

_MONTHS = {m: i + 1 for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"))}


def _parse_version_date(text: str) -> str:
    """把 'Nov 23 2026' 之类转成 2026-11-23，失败返回空串。"""
    parts = (text or "").replace(",", " ").split()
    if len(parts) >= 3 and parts[0] in _MONTHS:
        try:
            return f"{int(parts[2]):04d}-{_MONTHS[parts[0]]:02d}-{int(parts[1]):02d}"
        except ValueError:
            return ""
    return ""


def _read_kv(path: Path) -> dict:
    data: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            # VDF（appmanifest_730.acf）是 "键"  "值"，steam.inf 是 键=值
            vdf = re.match(r'\s*"([^"]+)"\s+"([^"]*)"', line)
            if vdf:
                data[vdf.group(1)] = vdf.group(2)
                continue
            if "=" in line and not line.strip().startswith("//"):
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip()
    except OSError:
        pass
    return data


def game_build(paths: dict) -> dict:
    csgo = paths.get("csgo")
    if not csgo:
        return {"version": "", "patch": "", "date": "", "build": "", "label": "未找到 CS2"}
    inf = _read_kv(csgo / "steam.inf")
    patch = inf.get("PatchVersion", "")
    date = _parse_version_date(inf.get("VersionDate", ""))
    build_id = ""
    try:
        manifest = csgo.parents[3] / "appmanifest_730.acf"
        build_id = _read_kv(manifest).get("buildid", "")
    except Exception:
        build_id = ""
    label = patch or inf.get("ClientVersion", "") or "未知版本"
    return {"version": label, "patch": patch, "date": date, "build": build_id,
            "label": f"{label}" + (f"（{date}）" if date else "")}


def _file_version(path: Path) -> str:
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buf):
            return ""
        ptr = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return ""

        class Fixed(ctypes.Structure):
            _fields_ = [("sig", wintypes.DWORD), ("struc", wintypes.DWORD),
                        ("ms", wintypes.DWORD), ("ls", wintypes.DWORD),
                        ("pms", wintypes.DWORD), ("pls", wintypes.DWORD),
                        ("mask", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("os", wintypes.DWORD), ("type", wintypes.DWORD),
                        ("sub", wintypes.DWORD), ("dms", wintypes.DWORD), ("dls", wintypes.DWORD)]
        info = ctypes.cast(ptr, ctypes.POINTER(Fixed)).contents
        numeric = f"{info.ms >> 16}.{info.ms & 0xFFFF}.{info.ls >> 16}.{info.ls & 0xFFFF}"
        # 版本字符串（2.0.0-dev+1490 这种）在 StringFileInfo 里，优先用它
        for lang in ("040904b0", "000004b0", "040904e4"):
            sub = "\\StringFileInfo\\" + lang + "\\ProductVersion"
            if not ctypes.windll.version.VerQueryValueW(buf, sub, ctypes.byref(ptr), ctypes.byref(length)):
                continue
            text = ctypes.wstring_at(ctypes.cast(ptr, ctypes.c_void_p).value) or ""
            text = text.strip().strip("\x00")
            if text:
                return text
        return numeric
    except Exception:
        return ""


def mod_build() -> dict:
    newest = 0.0
    try:
        for item in (PKG_BOT / "addons").rglob("*.dll"):
            try:
                newest = max(newest, item.stat().st_mtime)
            except OSError:
                pass
    except OSError:
        pass
    date = time.strftime("%Y-%m-%d", time.localtime(newest)) if newest else ""
    return {
        "date": date,
        "cssharp": package_cssharp_version(),
        "metamod": _file_version(PKG_BOT / "addons" / "metamod" / "bin" / "win64" / "metamod.2.cs2.dll"),
        "dir": str(PKG_BOT),
    }


def compat_report(state: dict, paths: dict) -> dict:
    """游戏版本 / 模组构建时间 / 已装插件版本，三合一的兼容性结论。"""
    game = game_build(paths)
    mod = mod_build()
    guard = state.get("guard") or {}
    csgo = paths.get("csgo")
    installed = installed_cssharp_version(csgo) if csgo else ""
    pkg_css = package_cssharp_version()
    adapted = bool(installed) and ver_tuple(installed) >= ver_tuple(MIN_CSS_VERSION)
    broken = bool(guard.get("broken"))
    stale = bool(game.get("date") and mod.get("date") and game["date"] > mod["date"])
    hider = bool(csgo and (csgo / "addons" / "metamod" / "BotHider.vdf").exists())

    if adapted:
        status = "ok"
        title = f"已适配 CS2 {game.get('version') or '当前版本'}"
        detail = (f"CounterStrikeSharp {installed} 已适配新版 CS2，"
                  "换肤（InventorySimulator）与强化 AI 插件都能正常加载。")
        if hider:
            status = "warn"
            title = "已适配，但残留 BotHider"
            detail = ("BotHider 原生插件未适配 CS2 1.42，会导致进图后崩溃。"
                      "点「修复插件兼容」或重新安装模组即可自动禁用。")
    elif broken:
        status = "broken"
        title = "模组与当前 CS2 版本不兼容"
        detail = (f"上次启动崩溃在 {guard.get('module') or '未知模块'}"
                  f"{(' + ' + str(guard.get('offset'))) if guard.get('offset') else ''}。"
                  f"游戏已更新到 {game['label']}，当前 CounterStrikeSharp "
                  f"{installed or '未知'} 低于适配所需的 {MIN_CSS_VERSION}，"
                  "点「更新换肤/插件」安装适配版。")
    elif stale:
        status = "stale"
        title = "游戏比模组新，插件版本可能偏旧"
        detail = (f"游戏 {game['label']} 晚于模组构建日期 {mod.get('date')}。"
                  f"若进图闪退，把 CounterStrikeSharp 更新到 {MIN_CSS_VERSION} 或更高。")
    else:
        status = "ok"
        title = "版本匹配"
        detail = f"游戏 {game['label']}，模组构建于 {mod.get('date') or '未知'}，时间上兼容。"
    return {
        "status": status, "title": title, "detail": detail,
        "game": game, "mod": mod,
        "installedCss": installed,
        "pkgCss": pkg_css,
        "minCss": MIN_CSS_VERSION,
        "adapted": adapted,
        "botHider": hider,
        "suspended": bool(guard.get("suspended")),
        "brokenAt": guard.get("at", ""),
        "lastLaunch": guard.get("lastLaunch", ""),
        "app": APP_VERSION,
    }


# --------------------------------------------------------------------------
# 启动崩溃看护：崩了就自动把模组摘下来，保证游戏永远打得开
# --------------------------------------------------------------------------

_guard_running = False


def _cs2_running() -> bool:
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq cs2.exe", "/NH"],
                             capture_output=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return b"cs2.exe" in out.stdout.lower()
    except Exception:
        return False


def _crash_since(when: float) -> dict:
    """查事件日志里 since 之后 cs2.exe 的 APPCRASH 记录（崩溃模块 / 异常码 / 偏移）。"""
    if os.name != "nt":
        return {}
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(when - 5))
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        f"$s=[datetime]::ParseExact('{stamp}','yyyy-MM-ddTHH:mm:ss',$null);"
        "Get-WinEvent -FilterHashtable @{LogName='Application';StartTime=$s;Id=1000} "
        "-ErrorAction SilentlyContinue | Where-Object {$_.Message -match 'cs2\\.exe'} "
        "| Select-Object -First 1 -ExpandProperty Message"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        text = out.stdout.decode("utf-8", "ignore")
    except Exception:
        return {}
    if "cs2.exe" not in text:
        return {}

    def pick(pattern: str) -> str:
        found = re.search(pattern, text)
        return found.group(1) if found else ""

    def short_hex(value: str) -> str:
        """0x00000000000bc62c → 0xbc62c，日志里不占地方。"""
        if not value.lower().startswith("0x"):
            return value
        digits = value[2:].lstrip("0") or "0"
        return "0x" + digits.lower()

    return {
        "module": pick(r"(?:错误模块名称|Faulting module name)\s*[:：]\s*([^\s,，|]+)"),
        "code": pick(r"(?:异常代码|Exception code)\s*[:：]\s*(0x[0-9a-fA-F]+)"),
        "offset": short_hex(pick(r"(?:错误偏移量|Fault offset)\s*[:：]\s*(0x[0-9a-fA-F]+)")),
    }


def suspend_mods(state: dict, paths: dict, reason: str = "") -> dict:
    """把 metamod 挂载摘掉（模组文件全部保留），让游戏回到能启动的原版状态。"""
    csgo = paths.get("csgo")
    if not csgo:
        raise RuntimeError("没有找到 CS2 安装目录")
    target = csgo / "gameinfo.gi"
    try:
        text = gameinfo_text("online")
    except Exception:
        text = read_text(BACKUP_DIR / "gameinfo.original.gi")
    if not text or "addons/metamod" in text:
        raise RuntimeError("找不到不含 metamod 的官方 gameinfo 模板，请用 Steam「验证文件完整性」修复")
    newline = "\n"
    try:
        if "\r\n" in target.read_bytes().decode("utf-8", "ignore"):
            newline = "\r\n"
    except OSError:
        pass
    ledger = Ledger(csgo)
    ledger.put_text(target, text, newline=newline)
    ledger.save()
    guard = state.setdefault("guard", {})
    guard["suspended"] = True
    guard["suspendedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if reason:
        guard["note"] = reason
    log(state, "已临时摘掉模组挂载（模组文件都还在，可一键装回），现在按原版启动")
    return {"suspended": True}


def resume_mods(state: dict, paths: dict) -> dict:
    guard = state.setdefault("guard", {})
    guard["suspended"] = False
    guard["broken"] = False
    guard["note"] = ""
    if state.get("mode") == "boost":
        state["mode"] = "offline"
        log(state, "已切回「离线全功能」，准备重新挂载插件")
    check = ensure_ready(state, paths, repair=True, quiet=True)
    log(state, "已恢复模组挂载" + ("，自检通过" if check.get("ok") else "，自检仍有问题（见日志）"),
        "info" if check.get("ok") else "error")
    return {"suspended": False, "selfCheck": check}


def start_guard_watch(state: dict) -> None:
    global _guard_running
    if _guard_running or os.name != "nt":
        return
    _guard_running = True
    threading.Thread(target=_guard_loop, args=(time.time(),), daemon=True).start()


def _guard_loop(since: float) -> None:
    global _guard_running
    try:
        deadline = time.time() + 90
        appeared = False
        while time.time() < deadline:
            if _cs2_running():
                appeared = True
                break
            time.sleep(2)
        if not appeared:
            return
        # 进程起来后看 150 秒：熬过去就算正常启动（后续崩不崩不在守护范围）
        alive = True
        deadline = time.time() + 150
        while time.time() < deadline:
            time.sleep(3)
            if not _cs2_running():
                alive = False
                break
        if alive:
            with _lock:
                state = load_state()
                state.setdefault("guard", {})["lastLaunch"] = time.strftime("%Y-%m-%d %H:%M:%S")
                save_state(state)
            return
        crash = _crash_since(since)
        with _lock:
            state = load_state()
            guard = state.setdefault("guard", {})
            if not crash:
                # 正常退出（进菜单后自己关掉）或者事件日志没记录：不改判定
                save_state(state)
                return
            guard.update({
                "broken": True,
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "module": crash.get("module", ""),
                "offset": crash.get("offset", ""),
                "code": crash.get("code", ""),
                "lastLaunch": "崩溃",
            })
            log(state, "CS2 启动崩溃：" + (guard["module"] or "未知模块")
                + " " + (guard["code"] or "") + " " + (guard["offset"] or "")
                + " → 当前游戏版本与模组不兼容", "error")
            if not guard.get("suspended"):
                try:
                    suspend_mods(state, resolve_paths(state), reason="启动崩溃后自动隔离")
                except Exception as exc:  # noqa: BLE001
                    log(state, f"自动隔离模组失败：{exc}", "error")
            save_state(state)
    finally:
        _guard_running = False


def check_mod_update() -> dict:
    """查上游最新版本，用来判断「等模组适配」有没有新东西可装。"""
    page = "https://github.com/ed0ard/CS2-Bot-Improver/releases"
    req = urllib.request.Request(
        "https://api.github.com/repos/ed0ard/CS2-Bot-Improver/releases/latest",
        headers={"User-Agent": f"CS2Workbench/{APP_VERSION}", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"查询失败：{exc}", "page": page}
    published = (data.get("published_at") or "")[:10]
    local = mod_build().get("date", "")
    return {
        "ok": True,
        "latest": data.get("tag_name", ""),
        "published": published,
        "local": local,
        "newer": bool(published and local and published > local),
        "page": data.get("html_url") or page,
        "notes": (data.get("body") or "").strip()[:600],
    }


# --------------------------------------------------------------------------
# 工作台自身的版本更新 / 回退（读自己的 GitHub 仓库 Release）
# --------------------------------------------------------------------------

APP_REPO = "liangyangming18-cmd/cs_trainingpanel"
APP_REPO_URL = f"https://github.com/{APP_REPO}"
APP_RELEASES_URL = f"{APP_REPO_URL}/releases"
ALLOWED_OPEN_HOSTS = ("github.com", "inventory.cstrike.app")


def _releases() -> list[dict]:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{APP_REPO}/releases?per_page=30",
        headers={"User-Agent": f"CS2Workbench/{APP_VERSION}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    return data if isinstance(data, list) else []


def app_update_check() -> dict:
    """读仓库 Release 列表：判断有没有新版、以及可以回退到哪些旧版。"""
    base = {"current": APP_VERSION, "repo": APP_REPO_URL, "page": APP_RELEASES_URL}
    try:
        raw = _releases()
    except Exception as exc:  # noqa: BLE001
        return {**base, "ok": False, "error": f"查询失败：{exc}", "versions": []}

    items: list[dict] = []
    for rel in raw:
        tag = str(rel.get("tag_name") or "").strip()
        if not tag or rel.get("draft"):
            continue
        assets = [{
            "name": a.get("name") or "",
            "size": int(a.get("size") or 0),
            "url": a.get("browser_download_url") or "",
        } for a in rel.get("assets") or []]
        items.append({
            "tag": tag,
            "version": tag.lstrip("vV"),
            "name": rel.get("name") or tag,
            "published": (rel.get("published_at") or "")[:10],
            "page": rel.get("html_url") or APP_RELEASES_URL,
            "prerelease": bool(rel.get("prerelease")),
            "notes": (rel.get("body") or "").strip()[:1500],
            "assets": assets,
        })
    if not items:
        return {**base, "ok": True, "latest": None, "newer": False, "versions": []}

    items.sort(key=lambda x: ver_tuple(x["version"]), reverse=True)
    latest = items[0]
    cur = ver_tuple(APP_VERSION)
    for item in items:
        vt = ver_tuple(item["version"])
        item["relation"] = "newer" if vt > cur else ("current" if vt == cur else "older")
    return {
        **base,
        "ok": True,
        "latest": latest,
        "newer": ver_tuple(latest["version"]) > cur,
        "versions": items,
        "page": latest["page"],
    }


def open_external(url: str) -> str:
    """只允许打开白名单里的站点，避免被页面上随便一段字符串拿去做系统调用。"""
    parts = urllib.parse.urlparse(str(url or ""))
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise RuntimeError("只支持 http/https 链接")
    host = parts.netloc.split(":")[0].lower()
    if not any(host == h or host.endswith("." + h) for h in ALLOWED_OPEN_HOSTS):
        raise RuntimeError(f"不允许打开这个站点：{host}")
    webbrowser.open(url)
    return url




# --------------------------------------------------------------------------
# 常驻运行：启动文件夹快捷方式 + 由资源管理器拉起独立进程
# （不用 schtasks —— 普通用户就能做，也不需要管理员权限）
# --------------------------------------------------------------------------

STARTUP_LNK = "CS2 Workbench.lnk"
LAUNCH_FLAGS = HOME_DIR / "launch.flags"
_persist_cache: dict = {"at": 0.0, "data": {}}


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _startup_link() -> Path:
    return _startup_dir() / STARTUP_LNK


def _launch_target() -> tuple[str, str]:
    """(目标程序, 参数) —— 桌面/启动文件夹快捷方式与 shell 启动共用。"""
    if FROZEN:
        return str(Path(sys.executable).resolve()), "--no-browser"
    pyw = Path(sys.executable).with_name("pythonw.exe")
    interp = pyw if pyw.exists() else Path(sys.executable)
    return str(interp), f'"{APP_DIR / "server.py"}" --no-browser'


def _make_lnk(lnk: Path, target: str, args: str, workdir: str, icon: str, desc: str) -> None:
    def q(value) -> str:
        return str(value).replace("'", "''")

    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        "$s.TargetPath='{target}';"
        "$s.Arguments='{args}';"
        "$s.WorkingDirectory='{cwd}';"
        "$s.IconLocation='{icon},0';"
        "$s.Description='{desc}';"
        "$s.Save()"
    ).format(lnk=q(lnk), target=q(target), args=q(args), cwd=q(workdir),
             icon=q(icon), desc=q(desc))
    lnk.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=40,
        )
        code, out = proc.returncode, (proc.stdout or b"") + (proc.stderr or b"")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"调用 PowerShell 失败：{exc}") from exc
    if code != 0 or not lnk.exists():
        raise RuntimeError("创建快捷方式失败：" + out.decode("utf-8", "ignore")[:200])


def persist_status(force: bool = False) -> dict:
    now = time.time()
    if not force and now - _persist_cache["at"] < 5 and _persist_cache["data"]:
        return _persist_cache["data"]
    link = _startup_link()
    data = {
        "installed": link.exists(),
        "link": str(link),
        "startupDir": str(_startup_dir()),
        "exe": _launch_target()[0],
    }
    _persist_cache.update({"at": now, "data": data})
    return data


def _spawn_independent() -> None:
    """让资源管理器把 exe 拉起来 —— 父进程是 explorer，脱离当前进程树。"""
    LAUNCH_FLAGS.write_text("takeover=1\nnobrowser=1\n", encoding="utf-8")
    target, _args = _launch_target()
    if FROZEN:
        subprocess.Popen(["explorer.exe", target], close_fds=True)
    else:
        subprocess.Popen([sys.executable, str(APP_DIR / "server.py"), "--takeover", "--no-browser"],
                         cwd=str(APP_DIR), close_fds=True)


def persist_action(state: dict, action: str) -> dict:
    action = (action or "").strip().lower()

    if action == "install":
        target, args = _launch_target()
        _make_lnk(_startup_link(), target, args, str(APP_DIR), target,
                  "CS2 Workbench（-insecure 人机 + 换肤）")
        log(state, "已放入启动文件夹：登录 Windows 后自动在后台启动，不依赖终端或任何宿主程序")
        _spawn_independent()
        log(state, "正在把服务交给独立进程接管（约 2 秒）")
        _persist_cache["at"] = 0.0
        return persist_status(force=True)

    if action in ("remove", "uninstall", "disable", "delete"):
        link = _startup_link()
        if link.exists():
            try:
                link.unlink()
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"删除快捷方式失败：{exc}") from exc
        log(state, "已移除开机自启（当前运行的服务不受影响）")
        _persist_cache["at"] = 0.0
        return persist_status(force=True)

    raise RuntimeError("未知的常驻操作")


def _desktop_dir() -> Path:
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    for cand in (home / "Desktop", home / "OneDrive" / "Desktop", home / "桌面"):
        if cand.is_dir():
            return cand
    return home / "Desktop"


def make_shortcut(state: dict, paths: dict) -> dict:
    """在桌面放一个「CS2 人机对局」快捷方式：不经过工作台也能带 -insecure 开局。"""
    if state.get("mode") == "online":
        raise RuntimeError("当前是「在线原版」模式，快捷方式会被 Steam 拒绝启动，请先切到离线人机")
    write_cfgs(state, paths)
    steam_exe = paths.get("steamExe")
    if not steam_exe:
        raise RuntimeError("没有找到 Steam 安装位置")

    args = ["-applaunch", "730"] + build_launch_args(state, paths)
    lnk = _desktop_dir() / "CS2 人机对局.lnk"
    icon = paths["exe"] if paths.get("exe") else steam_exe

    _make_lnk(lnk, str(steam_exe), subprocess.list2cmdline(args),
              str(Path(steam_exe).parent), str(icon),
              "CS2 人机对局（-insecure，强化 AI + 换肤）")
    log(state, f"已在桌面创建快捷方式：{lnk.name}")
    return {"path": str(lnk), "args": build_launch_args(state, paths)}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "CS2Workbench/" + APP_VERSION

    def log_message(self, fmt, *args):  # noqa: A003
        pass

    # -- helpers -------------------------------------------------------
    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, path: Path):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(path.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    # -- routes --------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        if route in ("/", "/index.html"):
            self.send_static(WEB_DIR / "index.html")
            return
        if route.startswith("/data/"):
            self.send_static(DATA_DIR / route[len("/data/"):])
            return
        if route.startswith("/web/"):
            self.send_static(WEB_DIR / route[len("/web/"):])
            return
        if route.startswith("/api/equipped/v5/"):
            # InventorySimulator 以「官方 API」的方式向本地工作台取库存，
            # 这样插件走的是它自己的正常流程（连接时拉取、!ws 刷新），完全离线。
            state = load_state()
            match = re.match(r"/api/equipped/v5/(\d+)\.json", route)
            steam_id = match.group(1) if match else ""
            payload = build_inventory(state)
            has_any = bool(payload["tWeapons"] or payload["ctWeapons"] or payload["knives"] or payload["gloves"])
            if not has_any or (state.get("steamId") and steam_id != state["steamId"]):
                payload = {"agents": {}, "collectible": None, "ctWeapons": {}, "gloves": {},
                           "graffiti": None, "knives": {}, "loadout": None, "musicKit": None,
                           "pet": None, "tWeapons": {}}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            global _last_served
            now = time.time()
            if now - _last_served > 30 and has_any:
                _last_served = now
                with _lock:
                    st = load_state()
                    st["lastServedAt"] = time.strftime("%H:%M:%S")
                    log(st, f"已向客户端下发库存（{steam_id}）")
                    save_state(st)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if route == "/api/ping":
            self.send_json({
                "ok": True,
                "app": "CS2Workbench",
                "pid": os.getpid(),
                "port": RUNTIME_PORT,
                "uptime": int(time.time() - START_TS),
                "home": str(HOME_DIR),
            })
            return
        if route == "/api/state":
            try:
                data = self.api_state()
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": f"状态读取失败：{exc}"})
                return
            data["ok"] = True
            self.send_json(data)
            return
        self.send_static(WEB_DIR / route.lstrip("/"))

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        if route == "/api/shutdown":
            self.read_json()
            self.send_json({"ok": True})
            threading.Timer(0.4, self.server.shutdown).start()
            return
        if route in ("/api/increment-item-stattrak", "/api/consume-item-spray", "/api/sign-in"):
            # 插件在离线模式下仍会回写 StatTrak / 喷漆消耗，这里直接吞掉，保持日志干净
            self.read_json()
            self.send_json({"ok": True})
            return
        payload = self.read_json()
        try:
            with _lock:
                state = load_state()
                paths = resolve_paths(state)
                if not state.get("steamId"):
                    state["steamId"] = detect_steam_id(paths["steam"])
                result = self.api_action(route, payload, state, paths)
                save_state(state)
            self.send_json({"ok": True, "result": result, "state": self.api_state()})
        except Exception as exc:  # noqa: BLE001
            with _lock:
                state = load_state()
                log(state, f"操作失败：{exc}", "error")
                save_state(state)
            self.send_json({"ok": False, "error": str(exc),
                            "detail": traceback.format_exc().splitlines()[-3:]}, 400)

    # -- api -----------------------------------------------------------
    def api_state(self) -> dict:
        state = load_state()
        paths = resolve_paths(state)
        detected_id = ""
        if not state.get("steamId"):
            detected_id = detect_steam_id(paths["steam"])
        return {
            "paths": {
                "cs2Root": str(paths["cs2Root"]) if paths["cs2Root"] else "",
                "csgo": str(paths["csgo"]) if paths["csgo"] else "",
                "exe": str(paths["exe"]) if paths["exe"] else "",
                "steam": str(paths["steam"]) if paths["steam"] else "",
                "steamExe": str(paths["steamExe"]) if paths["steamExe"] else "",
            },
            "health": health(state, paths),
            "state": {("%s" % k): v for k, v in state.items() if not k.startswith("_")},
            "detectedSteamId": detected_id,
            "meta": {
                "modes": MODES,
                "gamemodes": GAMEMODES,
                "maps": MAPS,
                "difficulty": DIFFICULTY_LABEL,
                "presets": BOT_PRESETS,
                "loadouts": BOT_LOADOUTS,
                "botcfg": read_bot_cfg(state, paths),
                "botSync": state.get("botSync") or {},
                "selfCheck": state.get("selfCheck") or {},
                "pkgBot": str(PKG_BOT),
                "pkgInv": str(PKG_INV),
                "botDir": str(bot_improver_dir(state) or ""),
                "persist": persist_status(),
                "compat": compat_report(state, paths),
                "app": APP_VERSION,
                "repo": APP_REPO_URL,
                "releases": APP_RELEASES_URL,
                "site": SITE_URL,
                "frozen": FROZEN,
                "home": str(HOME_DIR),
            },
        }

    def api_action(self, route, payload, state, paths):
        if route == "/api/detect":
            state["cs2Root"] = str(paths["cs2Root"]) if paths["cs2Root"] else ""
            state["steamId"] = detect_steam_id(paths["steam"]) or state.get("steamId", "")
            if not paths["cs2Root"]:
                raise RuntimeError("自动探测失败，请手动填写 CS2 安装目录")
            log(state, f"已定位 CS2：{paths['cs2Root']}")
            return {"cs2Root": str(paths["cs2Root"]), "steamId": state["steamId"]}

        if route == "/api/path":
            root = Path(str(payload.get("cs2Root", "")).strip().strip('"'))
            if not (root / "game" / "bin" / "win64" / "cs2.exe").exists():
                raise RuntimeError("该目录下没有 game/bin/win64/cs2.exe")
            state["cs2Root"] = str(root)
            log(state, f"CS2 目录已设为 {root}")
            return {"cs2Root": str(root)}

        if route == "/api/install":
            guard = state.setdefault("guard", {})
            guard.update({"broken": False, "suspended": False, "note": ""})
            result = install(state, paths)
            # 装完立刻把状态对齐：gameinfo 挂载 + 难度 vpk + 插件 cfg
            result["selfCheck"] = ensure_ready(state, paths, quiet=True)
            if state.get("mode") == "offline":
                set_difficulty(state, paths, state.get("difficulty", "Medium"))
            hydrate_from_bot_cfg(state, paths)
            write_cfgs(state, paths)
            return result

        if route == "/api/plugins/repair":
            guard = state.setdefault("guard", {})
            result = repair_plugins(state, paths)
            if result.get("adapted"):
                guard.update({"broken": False, "suspended": False, "note": ""})
            result["selfCheck"] = ensure_ready(state, paths, repair=True, quiet=True)
            write_cfgs(state, paths)
            return result

        if route == "/api/uninstall":
            return uninstall(state, paths)

        if route == "/api/verify":
            result = verify_clean(paths)
            log(state, "环境校验：" + ("干净，可以安全进入在线对局" if result["clean"] else "仍有残留 → " + "；".join(result["issues"])))
            return result

        if route == "/api/repair":
            result = ensure_ready(state, paths, repair=True)
            write_cfgs(state, paths)
            log(state, "一键自检：" + ("已全部修复（" + "、".join(result["fixed"]) + "）"
                                       if result["fixed"] else
                                       ("环境正常" if result["ok"] else "仍有问题 → " + "、".join(result["missing"]))),
                "info" if result["ok"] else "error")
            return result

        if route == "/api/source":
            source = payload.get("source", "site")
            if source not in ("site", "local"):
                raise RuntimeError("皮肤来源只能是 site / local")
            state["skinSource"] = source
            write_cfgs(state, paths)
            log(state, "皮肤来源 → " + ("inventory.cstrike.app（官网实时）" if source == "site" else "工作台本地库存"))
            return {"skinSource": source}

        if route == "/api/site/pull":
            steam_id = str(payload.get("steamId") or state.get("steamId") or "").strip()
            if steam_id:
                state["steamId"] = steam_id
            return pull_site_inventory(state, steam_id)

        if route == "/api/site/save-local":
            # 把当前编辑的库存另存为本地来源（不写入官网）
            state["skinSource"] = "local"
            write_cfgs(state, paths)
            return save_inventory(state, paths)

        if route == "/api/mode":
            set_mode(state, paths, payload.get("mode", "offline"))
            if state["mode"] in ("offline", "boost"):
                set_difficulty(state, paths, state.get("difficulty", "Medium"))
                write_cfgs(state, paths)
            return {"mode": state["mode"]}

        if route == "/api/difficulty":
            set_difficulty(state, paths, payload.get("level", "Medium"))
            return {"difficulty": state["difficulty"]}

        if route == "/api/match":
            match = payload.get("match") or {}
            state["match"].update({k: v for k, v in match.items() if k in DEFAULT_STATE["match"]})
            for key in ("aim", "nades"):
                if key in payload:
                    state[key] = payload[key]
            write_cfgs(state, paths)
            return {"match": state["match"], "cfg": match_cfg_text(state).splitlines(),
                    "botcfg": read_bot_cfg(state, paths)}

        if route == "/api/botcfg":
            action = str(payload.get("action") or "save")
            if action == "read":
                hydrate_from_bot_cfg(state, paths)
                return {"match": state["match"], "aim": state.get("aim"), "nades": state.get("nades"),
                        "botcfg": read_bot_cfg(state, paths)}
            if "preset" in payload:
                preset = str(payload["preset"])
                if preset not in BOT_PRESETS:
                    raise RuntimeError(f"未知预设：{preset}")
                state["match"]["preset"] = preset
            for key in ("loadout", "botGrenades", "ecoLimit", "c4timer", "botQuota"):
                if key in payload:
                    state["match"][key] = payload[key]
            for key in ("aim", "nades"):
                if key in payload:
                    state[key] = payload[key]
            sync = sync_bot_cfg(state, paths)
            write_cfgs(state, paths)
            log(state, f"已同步插件预设：{BOT_PRESETS[active_preset(state)]['label']}")
            return {**sync, "match": state["match"], "aim": state.get("aim"),
                    "nades": state.get("nades"), "botcfg": read_bot_cfg(state, paths)}

        if route == "/api/invsim":
            state["invsim"].update({k: v for k, v in (payload.get("invsim") or {}).items()
                                    if k in DEFAULT_STATE["invsim"]})
            write_cfgs(state, paths)
            return {"invsim": state["invsim"]}

        if route == "/api/inventory":
            if "inventory" in payload:
                state["inventory"] = payload["inventory"]
            if "steamId" in payload:
                state["steamId"] = str(payload["steamId"]).strip()
            return save_inventory(state, paths)

        if route == "/api/inventory/preview":
            if "inventory" in payload:
                state["inventory"] = payload["inventory"]
            return {"payload": {state.get("steamId", "<steamid>"): build_inventory(state)}}

        if route == "/api/launch":
            if "match" in payload:
                state["match"].update(payload["match"])
            return launch(state, paths, use_steam=bool(payload.get("useSteam", True)))

        if route == "/api/safe-launch":
            # 原版启动：摘掉 metamod 挂载（模组文件保留），保证一定进得去
            if "match" in payload:
                state["match"].update(payload["match"])
            suspend_mods(state, paths, reason="手动切到原版启动")
            return launch(state, paths, use_steam=bool(payload.get("useSteam", True)))

        if route == "/api/mods/resume":
            return resume_mods(state, paths)

        if route == "/api/mods/suspend":
            return suspend_mods(state, paths, reason="手动隔离模组")

        if route == "/api/update/check":
            return app_update_check()

        if route == "/api/open-url":
            return {"url": open_external(payload.get("url", ""))}

        if route == "/api/compat/check":
            result = check_mod_update()
            if result.get("ok"):
                log(state, f"上游最新版本 {result['latest']}（{result['published']}），"
                    + ("有更新可装 → " if result.get("newer") else "暂无更新，") + result["page"])
            else:
                log(state, result.get("error", "查询更新失败"), "error")
            return result

        if route == "/api/preset":
            # 用目录随机生成一套皮肤
            preset = payload.get("preset") or {}
            rarity = preset.get("rarity", "")
            catalog = json.loads((DATA_DIR / "catalog.json").read_text(encoding="utf-8"))
            import random

            rng = random.Random(preset.get("seed") or None)
            order = {"covert": 5, "classified": 4, "restricted": 3, "milSpec": 2, "industrial": 1}
            min_rank = order.get(rarity, 0)
            inventory = {"2": {"weapons": {}}, "3": {"weapons": {}}}
            for team in ("2", "3"):
                for weapon in catalog["weapons"]:
                    pool = [p for p in weapon["paints"] if order.get(p["rarity"], 0) >= min_rank]
                    if not pool:
                        continue
                    paint = rng.choice(pool)
                    inventory[team]["weapons"][str(weapon["def"])] = {
                        "paint": paint["kit"],
                        "wear": round(rng.uniform(max(0.0, paint["wearMin"]), min(0.6, paint["wearMax"])), 3),
                    }
                knife = rng.choice(catalog["knives"])
                finish = rng.choice(knife["finishes"])
                inventory[team]["knife"] = {"def": knife["def"], "paint": finish["kit"], "wear": 0.02}
                glove = rng.choice([g for g in catalog["gloves"] if g["finishes"]])
                gfinish = rng.choice(glove["finishes"])
                inventory[team]["gloves"] = {"def": glove["def"], "paint": gfinish["kit"], "wear": 0.12}
            state["inventory"] = inventory
            log(state, "已生成随机皮肤套装（未写入磁盘，记得点保存）")
            return {"inventory": inventory}

        if route == "/api/open":
            target = payload.get("target", "csgo")
            csgo = paths["csgo"]
            table = {
                "csgo": csgo,
                "overrides": csgo / "overrides" if csgo else None,
                "inventory": csgo / "addons" / "counterstrikesharp" / "configs" / "plugins" / "InventorySimulator" if csgo else None,
                "botcfg": csgo / "addons" / "counterstrikesharp" / "configs" / "plugins" if csgo else None,
                "botcfgdir": bot_cfg_dir(state),
                "workbench": HOME_DIR,
                "mods": MODS_ROOT,
                "bot": bot_improver_dir(state),
                "startup": _startup_dir(),
            }
            path = table.get(target)
            if target == "bot" and not path:
                raise RuntimeError("找不到 CS2BotImprover 目录，请确认它还在 Downloads 下")
            if not path or not Path(path).exists():
                raise RuntimeError("目录不存在")
            _open_in_explorer(Path(path))
            log(state, f"已在资源管理器中打开：{path}")
            return {"path": str(path)}

        if route == "/api/persist":
            return persist_action(state, payload.get("action", ""))

        if route == "/api/shortcut":
            return make_shortcut(state, paths)

        if route == "/api/site/open":
            webbrowser.open(SITE_URL)
            return {"url": SITE_URL}

        if route == "/api/log/clear":
            state["log"] = []
            return {}

        raise RuntimeError(f"未知接口：{route}")


def existing_instance() -> int | None:
    """已有实例在跑就直接复用，避免开一堆进程。"""
    for port in range(8787, 8800):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=0.6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("app") == "CS2Workbench":
                return port
        except Exception:
            continue
    return None


def _wait_for_instance(timeout: float = 15.0) -> int | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        port = existing_instance()
        if port:
            return port
        time.sleep(0.3)
    return None


def _shutdown_instance(port: int) -> None:
    """请已经占着端口的老实例自己退出（常驻任务接管时用）。"""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/shutdown",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=3).read()
    except Exception:  # noqa: BLE001
        pass


_MUTEX_HANDLE = None


def _acquire_instance_lock(timeout: float = 10.0) -> bool:
    """Windows 命名互斥量：保证同一时刻只有一个进程在监听。

    只靠端口不行 —— SO_REUSEADDR 在 Windows 上允许多个进程同时绑 8787，
    请求会随机落到老进程上（会出现「接口忽然不认了」这种怪现象）。
    """
    global _MUTEX_HANDLE
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)

    deadline = time.time() + timeout
    while True:
        handle = kernel32.CreateMutexW(None, True, "Local\\CS2Workbench-Instance")
        err = ctypes.get_last_error()
        if handle:
            if err != 183:  # ERROR_ALREADY_EXISTS
                _MUTEX_HANDLE = handle
                return True
            kernel32.CloseHandle(handle)
        elif err not in (183,):
            return True  # 系统不给建，不拿它当阻塞条件
        if time.time() >= deadline:
            return False
        time.sleep(0.4)


def _relaunch_detached() -> bool:
    """把自己以「脱离父进程」的方式重新拉起（仅在设了 WB_DETACH=1 时用）。

    从终端 / 批处理里启动时，宿主窗口一关，Windows 会把整棵进程树收掉，
    浏览器里就只剩 Failed to fetch。这里用 DETACHED_PROCESS +
    CREATE_BREAKAWAY_FROM_JOB 另起一个独立进程，宿主关掉也不影响服务。

    双击启动（父进程是 explorer）本来就是独立进程，所以默认不走这条路，
    免得 onefile 解包两次、白等十秒。
    """
    if os.name != "nt" or not FROZEN:
        return False
    if os.environ.get("WB_DETACHED") == "1" or os.environ.get("WB_DETACH") != "1":
        return False

    env = dict(os.environ)
    env["WB_DETACHED"] = "1"
    env["WB_NO_BROWSER"] = "1"  # 由父进程负责打开浏览器

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000
    for flags in (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
                  DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP):
        try:
            subprocess.Popen(
                [sys.executable],
                creationflags=flags,
                env=env,
                cwd=str(APP_DIR),
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            continue
    return False


def main() -> None:
    global RUNTIME_PORT, TAKEOVER

    # 由「设为常驻」触发的独立进程：接管现有端口并静默运行
    if LAUNCH_FLAGS.exists():
        try:
            flags = LAUNCH_FLAGS.read_text(encoding="utf-8", errors="ignore")
            LAUNCH_FLAGS.unlink()
            if "takeover=1" in flags:
                TAKEOVER = True
            if "nobrowser=1" in flags:
                os.environ["WB_NO_BROWSER"] = "1"
        except Exception:  # noqa: BLE001
            pass

    already = existing_instance()
    if already:
        if TAKEOVER:
            # 常驻任务接管：先让老实例退出（留 1.2 秒把发起方的响应写完）
            time.sleep(1.2)
            _shutdown_instance(already)
            for _ in range(80):
                if not existing_instance():
                    break
                time.sleep(0.25)
        else:
            url = f"http://127.0.0.1:{already}/"
            print(f"工作台已在运行：{url}")
            if not os.environ.get("WB_NO_BROWSER"):
                webbrowser.open(url)
            # 窗口版 exe：不要留一个空进程
            if FROZEN:
                time.sleep(0.2)
                return

    if _relaunch_detached():
        port = _wait_for_instance(15.0)
        if port:
            print(f"工作台已在后台运行：http://127.0.0.1:{port}/")
            if not os.environ.get("WB_NO_BROWSER"):
                webbrowser.open(f"http://127.0.0.1:{port}/")
            return
        print("后台实例启动超时，改为在当前进程内启动")

    # 抢独占锁：拿不到说明另一个进程还活着，别抢端口
    locked = _acquire_instance_lock(10.0)
    if not locked:
        port = _wait_for_instance(6.0)
        if port:
            print(f"已有实例在运行：http://127.0.0.1:{port}/")
            if not os.environ.get("WB_NO_BROWSER"):
                webbrowser.open(f"http://127.0.0.1:{port}/")
            return
        # 锁被占着却没人应答（上次被强杀留下的僵尸锁）：
        # 绝不静默退出，换一组端口照样把界面跑起来
        print("独占锁被占用但服务无响应，改在备用端口启动")

    start_port = 8787 if locked else 8800
    port = start_port
    httpd = None
    for candidate in range(start_port, start_port + 13):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        print("端口被占用，无法启动")
        return

    httpd.daemon_threads = True
    RUNTIME_PORT = port
    url = f"http://127.0.0.1:{port}/"

    # 开机先自检 + 对齐配置：关掉再打开也不会出现「进游戏没皮肤、bot 还是原版」
    try:
        state = load_state()
        paths = resolve_paths(state)
        if paths["csgo"]:
            hydrate_from_bot_cfg(state, paths)
            ensure_ready(state, paths, repair=True)
            write_cfgs(state, paths)
        save_state(state)
    except Exception:
        pass

    print("=" * 56)
    print("  CS2 训练工作台已启动")
    print(f"  {url}")
    if FROZEN:
        print("  独立进程运行，关闭宿主程序不会影响服务")
        print("  在页面点「退出工作台」即可停止")
    else:
        print("  Ctrl+C 停止服务")
    print("=" * 56)

    threading.Timer(
        0.6,
        lambda: None if os.environ.get("WB_NO_BROWSER") else webbrowser.open(url),
    ).start()

    # 单次异常不致命：记录后重建监听继续服务，避免页面直接变 Failed to fetch
    while True:
        try:
            httpd.serve_forever(poll_interval=0.5)
            break
        except KeyboardInterrupt:
            print("\n已停止")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"服务异常：{exc}，2 秒后自动恢复")
            time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        detail = traceback.format_exc()
        try:
            (HOME_DIR / "startup-error.log").write_text(detail, encoding="utf-8")
        except Exception:
            pass
        if FROZEN and os.name == "nt":
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
                    0, detail[-1200:], "CS2 Workbench 启动失败", 0x10)
            except Exception:
                pass
        else:
            print(detail)
