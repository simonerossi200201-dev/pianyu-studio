# -*- coding: utf-8 -*-
"""从老流水线 longtik_images.py 抽出的 sidecar 解析。

原文件依赖 表格自动下载器 / longtik_manifest，整份拷进来会在 import 时炸掉。
parse_sidecar_text / parse_voice_line / TAG_RE / VOICE_RE 与源码同一套规则。
"""
from __future__ import annotations

import re

# 与 longtik_manifest 同一组白名单
EYES = {"闭眼", "睁眼", "空镜"}
SCENES = {"常规", "舞台", "车内", "围绕"}
ROLES = {"主体", "空镜"}

# 本期本地解析（含空镜）。flow_shots.parse_tag 还不认空镜，第 4 期才改白名单。
TAG_RE = re.compile(
    r"【\s*(闭眼|睁眼|空镜)\s*[|｜]\s*(常规|舞台|车内|围绕)\s*】\s*$")
VOICE_RE = re.compile(r"【音色】\s*(.+?)\s*$")


def parse_voice_line(line):
    """【音色】族裔|性别|年龄|音区|口音 → dict。对不上五段则 {}。"""
    s = (line or "").strip()
    m = VOICE_RE.search(s)
    if not m:
        return {}
    bits = [x.strip() for x in m.group(1).split("|")]
    if len(bits) < 5:
        return {}
    return {
        "ethnic": bits[0],
        "gender": bits[1],
        "age": bits[2],
        "tone": bits[3],
        "accent": bits[4],
    }


def parse_sidecar_text(raw):
    """拆 sidecar：desc（去标签）/ eye / scene / role / voice / missing。"""
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return {
            "desc": "", "eye": "闭眼", "scene": "常规", "role": "主体",
            "voice": {}, "missing": True, "raw": "",
        }
    lines = text.split("\n")
    voice = {}
    body_lines = []
    for ln in lines:
        if VOICE_RE.search(ln.strip()):
            voice = parse_voice_line(ln) or voice
        else:
            body_lines.append(ln)
    body = "\n".join(body_lines).strip()
    eye, scene = "闭眼", "常规"
    m = TAG_RE.search(body)
    if m:
        eye, scene = m.group(1), m.group(2)
        desc = TAG_RE.sub("", body).strip()
    else:
        desc = body
        if re.search(r"画面中没有人在对着镜头说话|观众席|一片人", desc) and \
           not re.search(r"一位.{0,8}(男|女)|主角", desc):
            eye = "空镜"
        elif re.search(r"睁着眼|睁开眼|睁着双眼", desc) and \
                not re.search(r"紧闭双眼|闭着眼|双眼紧闭", desc):
            eye = "睁眼"
        if re.search(r"车内|车里|驾驶|方向盘", desc):
            scene = "车内"
        elif re.search(r"围成一圈|围在中间|一圈人|簇拥", desc):
            scene = "围绕"
        elif re.search(r"舞台|讲台|观众席|会场", desc):
            scene = "舞台"
    if eye not in EYES:
        eye = "闭眼"
    if scene not in SCENES:
        scene = "常规"
    role = "空镜" if eye == "空镜" else "主体"
    if role not in ROLES:
        role = "主体"
    if eye == "空镜":
        voice = {}
    return {
        "desc": desc, "eye": eye, "scene": scene, "role": role,
        "voice": voice, "missing": False, "raw": text,
    }
