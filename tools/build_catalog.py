#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成工作台使用的物品目录 data/catalog.json

数据来源：CS2BotImprover/addons/counterstrikesharp/plugins/BotRandomizer/cosmetic_catalog.json
（该目录来自 ianlucas/cs2-lib，含义是「游戏内真实存在的 defindex + paintKit 组合」，
所以这里生成的每个编号都能被 InventorySimulator 正确识别）

名称表：武器/刀具的中文名、热门皮肤的常用名、手套系列与配色的官方名。
没有收录名称的皮肤只显示编号 —— 编号才是真正生效的值，名称只是标注。
"""

import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT.parent / "CS2BotImprover" / "addons" / "counterstrikesharp" / "plugins" / "BotRandomizer" / "cosmetic_catalog.json"
OUT = ROOT / "data" / "catalog.json"

WEAPONS = {
    1: ("沙漠之鹰", "Desert Eagle", "手枪"),
    2: ("双持贝瑞塔", "Dual Berettas", "手枪"),
    3: ("FN57", "Five-SeveN", "手枪"),
    4: ("格洛克 18", "Glock-18", "手枪"),
    7: ("AK-47", "AK-47", "步枪"),
    8: ("AUG", "AUG", "步枪"),
    9: ("AWP", "AWP", "步枪"),
    10: ("法玛斯", "FAMAS", "步枪"),
    11: ("G3SG1", "G3SG1", "步枪"),
    13: ("加利尔 AR", "Galil AR", "步枪"),
    14: ("M249", "M249", "机枪"),
    16: ("M4A4", "M4A4", "步枪"),
    17: ("MAC-10", "MAC-10", "冲锋枪"),
    19: ("P90", "P90", "冲锋枪"),
    23: ("MP5-SD", "MP5-SD", "冲锋枪"),
    24: ("UMP-45", "UMP-45", "冲锋枪"),
    25: ("XM1014", "XM1014", "霰弹枪"),
    26: ("PP-野牛", "PP-Bizon", "冲锋枪"),
    27: ("MAG-7", "MAG-7", "霰弹枪"),
    28: ("内格夫", "Negev", "机枪"),
    29: ("截短霰弹枪", "Sawed-Off", "霰弹枪"),
    30: ("Tec-9", "Tec-9", "手枪"),
    31: ("电击枪", "Zeus x27", "装备"),
    32: ("P2000", "P2000", "手枪"),
    33: ("MP7", "MP7", "冲锋枪"),
    34: ("MP9", "MP9", "冲锋枪"),
    35: ("新星", "Nova", "霰弹枪"),
    36: ("P250", "P250", "手枪"),
    38: ("SCAR-20", "SCAR-20", "步枪"),
    39: ("SG 553", "SG 553", "步枪"),
    40: ("SSG 08", "SSG 08", "步枪"),
    60: ("M4A1-S", "M4A1-S", "步枪"),
    61: ("USP-S", "USP-S", "手枪"),
    63: ("CZ75-Auto", "CZ75-Auto", "手枪"),
    64: ("R8 左轮", "R8 Revolver", "手枪"),
}

KNIVES = {
    500: ("刺刀", "Bayonet"),
    503: ("经典折刀", "Classic Knife"),
    505: ("折叠刀", "Flip Knife"),
    506: ("穿肠刀", "Gut Knife"),
    507: ("爪子刀", "Karambit"),
    508: ("M9 刺刀", "M9 Bayonet"),
    509: ("猎杀者匕首", "Huntsman Knife"),
    512: ("折刀", "Falchion Knife"),
    514: ("鲍伊猎刀", "Bowie Knife"),
    515: ("蝴蝶刀", "Butterfly Knife"),
    516: ("暗影双匕", "Shadow Daggers"),
    517: ("系绳匕首", "Paracord Knife"),
    518: ("求生匕首", "Survival Knife"),
    519: ("熊刀", "Ursus Knife"),
    520: ("纳瓦哈刀", "Navaja Knife"),
    521: ("短剑", "Stiletto Knife"),
    522: ("锯齿爪刀", "Talon Knife"),
    523: ("游牧者匕首", "Nomad Knife"),
    525: ("骷髅匕首", "Skeleton Knife"),
    526: ("廓尔喀刀", "Kukri Knife"),
}

GLOVES = {
    4725: ("狂牙手套", "Broken Fang Gloves", {
        10085: "翡翠 Jade", 10086: "黄带 Yellow-banded",
        10087: "针尖 Needle Point", 10088: "疯狂 Unhinged"}),
    5027: ("血猎手套", "Bloodhound Gloves", {
        10006: "焦黑 Charred", 10007: "蛇咬 Snakebite",
        10008: "青铜 Bronzed", 10039: "游击队 Guerrilla"}),
    5030: ("运动手套", "Sport Gloves", {
        10018: "超导体 Superconductor", 10019: "干旱 Arid",
        10037: "潘多拉之盒 Pandora's Box", 10038: "树篱迷宫 Hedge Maze",
        10045: "两栖 Amphibious", 10046: "青铜形态 Bronze Morph",
        10047: "欧米伽 Omega", 10048: "迈阿密风云 Vice",
        10073: "弹弓 Slingshot", 10074: "大猎物 Big Game",
        10075: "猩红头巾 Scarlet Shamagh", 10076: "夜行 Nocts"}),
    5031: ("驾驶手套", "Driver Gloves", {
        10013: "月之编织 Lunar Weave", 10015: "车队 Convoy",
        10016: "绯红编织 Crimson Weave", 10040: "菱背蛇 Diamondback",
        10041: "王蛇 King Snake", 10042: "帝王格纹 Imperial Plaid",
        10043: "超车 Overtake", 10044: "赛车绿 Racing Green",
        10069: "红胡子 Rezan the Red", 10070: "雪豹 Snow Leopard",
        10071: "美洲豹女王 Queen Jaguar", 10072: "黑领带 Black Tie"}),
    5032: ("裹手", "Hand Wraps", {
        10009: "皮革 Leather", 10010: "云杉 DDPAT Spruce DDPAT",
        10021: "屠夫 Slaughter", 10036: "荒地 Badlands",
        10053: "钴蓝骷髅 Cobalt Skulls", 10054: "叠印 Overprint",
        10055: "胶带 Duct Tape", 10056: "林地 Arboreal",
        10081: "沙漠头巾 Desert Shamagh", 10082: "长颈鹿 Giraffe",
        10083: "蟒蛇 Constrictor", 10084: "警告 CAUTION!"}),
    5033: ("摩托手套", "Moto Gloves", {
        10024: "日蚀 Eclipse", 10026: "薄荷 Spearmint",
        10027: "轰! Boom!", 10028: "清凉薄荷 Cool Mint",
        10049: "砰! POW!", 10050: "乌龟 Turtle",
        10051: "运输 Transport", 10052: "多边形 Polygon",
        10077: "终点线 Finish Line", 10078: "烟熏 Smoke Out!",
        10079: "血压 Blood Pressure", 10080: "第三突击队 3rd Commando Company"}),
    5034: ("专业手套", "Specialist Gloves", {
        10030: "森林 DDPAT Forest DDPAT", 10033: "绯红和服 Crimson Kimono",
        10034: "翡翠之网 Emerald Web", 10035: "基石 Foundation",
        10061: "深红之网 Crimson Web", 10062: "鹿弹 Buckshot",
        10063: "渐变 Fade", 10064: "大亨 Mogul",
        10065: "大理石渐变 Marble Fade", 10066: "少校 Lt. Commander",
        10067: "猛虎出击 Tiger Strike", 10068: "外勤特工 Field Agent"}),
    5035: ("九头蛇手套", "Hydra Gloves", {
        10057: "翡翠 Emerald", 10058: "红树林 Mangrove",
        10059: "响尾蛇 Rattler", 10060: "表面淬火 Case Hardened"}),
}

# 热门皮肤的常用名。只收录编号确定的条目：名称仅作标注，编号才是生效值。
PAINT_NAMES = {
    7: {180: "火蛇 Fire Serpent", 282: "红线 Redline", 302: "火神 Vulcan",
        801: "二西莫夫 Asiimov"},
    9: {279: "二西莫夫 Asiimov", 344: "巨龙传说 Dragon Lore",
        475: "野兽咆哮 Hyper Beast"},
    16: {255: "二西莫夫 Asiimov", 309: "咆哮 Howl"},
    1: {37: "烈焰 Blaze"},
    4: {38: "渐变之色 Fade"},
    36: {551: "二西莫夫 Asiimov"},
    19: {359: "二西莫夫 Asiimov"},
}

RARITY_CN = {
    "industrial": "工业级",
    "milSpec": "军规级",
    "restricted": "受限",
    "classified": "保密",
    "covert": "隐秘",
    "contraband": "违禁",
    "rare": "罕见",
}


def main():
    data = json.loads(SRC.read_text(encoding="utf-8"))

    weapons = []
    for item in data["weapons"]:
        def_index = item["defIndex"]
        if def_index not in WEAPONS:
            continue
        cn, en, group = WEAPONS[def_index]
        names = PAINT_NAMES.get(def_index, {})
        paints = []
        for paint in item["paints"]:
            kit = paint["paintKit"]
            paints.append({
                "kit": kit,
                "name": names.get(kit, ""),
                "rarity": paint["rarity"],
                "rarityCn": RARITY_CN.get(paint["rarity"], paint["rarity"]),
                "wearMin": round(float(paint["wearMin"]), 4),
                "wearMax": round(float(paint["wearMax"]), 4),
            })
        paints.sort(key=lambda p: (p["name"] == "", -len(p["name"]), p["kit"]))
        weapons.append({
            "def": def_index,
            "key": item["designerName"],
            "nameCn": cn,
            "nameEn": en,
            "group": group,
            "paints": paints,
        })

    knives = []
    for item in data["knives"]:
        def_index = item["defIndex"]
        cn, en = KNIVES.get(def_index, (f"刀 {def_index}", f"Knife {def_index}"))
        finishes = [{
            "kit": p["paintKit"],
            "name": p.get("finish", ""),
            "wearMin": round(float(p["wearMin"]), 4),
            "wearMax": round(float(p["wearMax"]), 4),
        } for p in item["paints"]]
        finishes.sort(key=lambda f: f["name"])
        knives.append({
            "def": def_index, "nameCn": cn, "nameEn": en, "finishes": finishes,
        })

    gloves = []
    for def_index, (cn, en, names) in GLOVES.items():
        wear = {g["paintKit"]: g for g in data["gloves"] if g["defIndex"] == def_index}
        finishes = [{
            "kit": kit,
            "name": name,
            "wearMin": round(float(wear[kit]["wearMin"]), 4),
            "wearMax": round(float(wear[kit]["wearMax"]), 4),
        } for kit, name in names.items() if kit in wear]
        gloves.append({
            "def": def_index, "nameCn": cn, "nameEn": en, "finishes": finishes,
        })

    out = {
        "meta": {
            "source": data.get("source", {}),
            "weaponCount": len(weapons),
            "knifeCount": len(knives),
            "gloveCount": len(gloves),
        },
        "weapons": weapons,
        "knives": knives,
        "gloves": gloves,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"weapons={len(weapons)} knives={len(knives)} gloves={len(gloves)} -> {OUT}")


if __name__ == "__main__":
    main()
