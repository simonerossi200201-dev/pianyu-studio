# -*- coding: utf-8 -*-
"""
Grok 全流程流水线 —— 建账 → 铺页 → 提交锚定 → 收货 → 验收
=========================================================

这个脚本存在的唯一理由：**下载下来的每个 mp4，必须百分之百是它文件名声称的那一段。**

════════ 为什么老流程会错乱（0826 那批 188 个文件错了 49 个） ════════

老路子是「先把 N 个标签页全提交掉，事后再回来一个个认领」：

    铺页(标题=v09-1of2) → 提交 → 【Grok 把标题冲成 "Imagine - Grok"】
                                → 事后扫标签页，靠标题/提示词猜这是哪一段
                                → 猜出来是谁，就把当前页面上的视频下载成谁

问题出在第二行那个方括号：**提交那一刻，身份信息就被页面自己抹掉了。**
之后无论用标题、提示词还是页面文本去"认回来"，都是在做考古，一定会错。
0826 那批的三份状态文件（_pipeline_state / _wave1_state / _wave1_harvest_real）
对 v04-1of2 给出了两个不同的 postId，就是考古出了两个互相矛盾的结论。

════════ 新流程凭什么不会错 ════════

核心只有一句话：**从铺页到落盘，一个标签页对象自始至终握在手里，中途不撒手。**

    铺页 → 拿到 page 对象 → 提交(还是这个 page) → 等生成(还是这个 page)
         → 读视频地址(还是这个 page) → 下载 → 写成台账里写死的文件名 → 才关页

身份不是"认出来"的，是"从来没丢过"的。标题被冲掉无所谓，提示词被清空也无所谓，
因为我们压根不需要靠它们认人。

在这条主线之外还加了四道独立的闸，任意一道不过就拒绝落盘：

  闸1 铺页校验    输入框文字与原文逐字一致 + 图片确已附上（沿用 grok_batch 的做法）
  闸2 锚点        提交后立刻抓 URL 里的 postId 当场落盘。抓不到 → 标记「锚点丢失」，
                  绝不猜。有了它，中途崩溃也能凭 postId 直接回到那一段的详情页。
  闸3 下载校验    下载地址必须含本段的 postId
                  （assets.grok.com/users/…/generated/<postId 前缀>…）。
                  不含 → 拒绝下载并报错。绝不"页面上有什么就下什么"。
  闸4 验收        落盘后回头核对（见下）。不过关不许进拼接。

════════ 验收（第 4 关）════════

  快检（每批必跑，秒级）
    · 台账里每一段都有文件，文件名与台账写死的一致
    · 全局 md5 唯一 —— 抓"两个文件其实是同一个视频"
    · 时长与台账档位相符（纯 python 读 mp4 的 mvhd，不依赖 ffprobe）
    · 首帧 pHash 必须与本段的源图 vNN 最接近 —— 抓"张冠李戴到别的组"
      （0826 那 49 个错乱，这一条全部能抓出来）
    · 下载记录里的地址含本段 postId

  强检（整批完工前必跑一次，慢）
    · 把每个视频转写成文字，与分段英文原文做全局 1:1 最优指派。
      语音是视频自己带的、伪造不了的证据，能抓出快检抓不到的
      「同组内 a/b 段互换」。
      转写用 AutoSub 已经装好的 faster-whisper（flow_config.json 的 asr_model）。

════════ 用法 ════════

    python grok_pipe.py 描述语 [文件夹] [--只看] [--重写]   # 先把 A 层写好
    python grok_pipe.py 建账   [文件夹]
    python grok_pipe.py 跑     [文件夹] [--波次 12] [--并发 4] [--从 1 --到 92]
    python grok_pipe.py 收货   [文件夹]          # 只补下已经生成好的
    python grok_pipe.py 验收   [文件夹] [--强检]
    python grok_pipe.py 补漏   [文件夹]
    python grok_pipe.py 认领   [文件夹] [--执行]   # 救火：给已经错乱的一批翻案

    双击 启动-Grok全流程.bat 也行（菜单里 1~7 一一对应）

台账 = 文件夹里的 Grok台账.json，**唯一真值**。不再有第二份状态文件。
"""

import argparse
import base64
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Windows 控制台：bat 里已经 chcp 65001，这里再把 Python 自己的 stdout 钉成 utf-8。
# 两边都设好，中文才不会变成问号或乱码。老 Python 没有 reconfigure，忽略即可。
for _st in (sys.stdout, sys.stderr):
    try:
        _st.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
IMAGINE_URL = "https://grok.com/imagine"
CDP_PORT = os.environ.get("GROK_CDP_PORT", "9222")
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"

# 状态机
S_PENDING = "待铺页"
S_PREPARED = "已铺页"
S_SUBMITTED = "已提交"
S_READY = "已生成"
S_DOWNLOADED = "已下载"
S_VERIFIED = "已验收"
S_LOST = "锚点丢失"
S_FAILED = "失败"

ORDER = [S_VERIFIED, S_DOWNLOADED, S_READY, S_SUBMITTED, S_PREPARED,
         S_PENDING, S_LOST, S_FAILED]


# ═══════════════ 基础工具 ═══════════════

_HERE = os.path.dirname(os.path.abspath(__file__))
DIAG_DIR = os.path.join(_HERE, "_现场")
LOG_FILE = os.path.join(_HERE, "跑批日志.txt")


def log(*a):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] " + " ".join(str(x) for x in a)
    print(line, flush=True)
    # 0828：控制台一关就什么都没了，出了事只能靠截图猜。日志落盘。
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


DIAG_JS = r"""() => {
  const ed = document.querySelector('div.tiptap');
  const btn = [...document.querySelectorAll('button,[role=button]')]
      .filter(e => e.offsetParent !== null)
      .map(e => ((e.textContent || '').replace(/\s+/g, ' ').trim()
                 || e.getAttribute('aria-label') || '')
                + (e.disabled || e.getAttribute('aria-disabled') === 'true' ? '(灰)' : ''))
      .filter(t => t).slice(0, 24);
  return {
    url: location.href,
    title: document.title,
    text: ed ? (ed.innerText || '') : null,
    editors: document.querySelectorAll('div.tiptap').length,
    blobImgs: [...document.querySelectorAll("img[src^='blob:']")].length,
    allImgs: [...document.querySelectorAll('img[src]')]
        .map(i => i.src.slice(0, 90)).slice(0, 8),
    fileInputs: [...document.querySelectorAll('input[type=file]')]
        .map(i => (i.files ? i.files.length : -1)),
    buttons: btn,
    bodyHead: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 260),
  };
}"""


def snapshot(page, tag, why=""):
    """出事时把这一页的现场原样存下来：一张截图 + 一条 json。
    截图和 json 都在 core\_现场\ 里，事后能直接看出到底是有字没图、
    有图没字，还是页面自己跑到别处去了。"""
    try:
        os.makedirs(DIAG_DIR, exist_ok=True)
    except Exception:
        return
    ts = datetime.now().strftime("%H%M%S")
    base = os.path.join(DIAG_DIR, f"{tag}-{ts}")
    rec = {"时刻": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "段": tag, "原因": why[:200]}
    try:
        rec.update(page.evaluate(DIAG_JS))
        if rec.get("text") is not None:
            rec["文字长度"] = len(norm_text(rec["text"]))
            rec["文字开头"] = norm_text(rec["text"])[:80]
            rec["文字结尾"] = norm_text(rec["text"])[-60:]
            rec.pop("text", None)
    except Exception as e:
        rec["读页失败"] = str(e)[:120]
    try:
        page.screenshot(path=base + ".png", full_page=False, timeout=8000)
        rec["截图"] = os.path.basename(base) + ".png"
    except Exception as e:
        rec["截图失败"] = str(e)[:100]
    try:
        with open(os.path.join(DIAG_DIR, "现场.jsonl"), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    log(f"    · 现场已留档 → core\\_现场\\{os.path.basename(base)}.png")


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    """原子写 + fsync。台账是唯一真值，断电也不能写坏。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def norm_text(s):
    return re.sub(r"\s+", " ", str(s)).strip()


def md5_of(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def text_fp(s):
    return hashlib.md5(norm_text(s).encode("utf-8")).hexdigest()[:12]


def ledger_path(folder):
    return os.path.join(folder, "Grok台账.json")


def video_dir(folder):
    d = os.path.join(folder, "视频输出")
    os.makedirs(d, exist_ok=True)
    return d


def resolve_image(folder, row):
    """拿到这一段的起始图。台账里的绝对路径找不到时，退回当天文件夹里按编号找。

    台账存的是建账那台机器上的绝对路径。跨机器（或跨挂载点）跑同一个台账时
    那条路径必然失效 —— 0827 实跑就是这么 WinError 3 的。
    起始图永远躺在当天文件夹里、永远叫 vNN.<图片后缀>，所以就地重算最可靠。
    """
    p = row.get("图片") or ""
    if p and os.path.exists(p):
        return p
    label = row.get("编号") or ""
    for ext in IMG_EXTS:
        q = os.path.join(folder, label + ext)
        if os.path.exists(q):
            return q
    raise Exception(f"找不到 {label} 的起始图（台账记的是 {p[:70]}，"
                    f"当天文件夹里也没有 {label}.*）")


def seg_letter(idx):
    """1→a … 26→z，27 起进位成 aa/ab/ac（26 进制，1 基）。

    老写法是 chr(ord('a') + idx - 1)：第 27 段会算出 '{'，第 28 段 '|' ——
    都是 Windows 文件名里的合法字符，所以不会报错，只会安静地生出
    「01{.mp4」这种名字，收货时对不上。0829 的 v02 已经切到 14 段，
    长文案改走 Normal 之后迟早会过 26。≤26 段时输出和以前一字不差。
    """
    out = ""
    n = int(idx)
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(ord('a') + r) + out
    return out


def out_name(num, idx, total):
    """01a.mp4 / 01b.mp4 / 01c.mp4；整组只有一段时是 01.mp4（与 0826 的既有约定一致）"""
    if total <= 1:
        return f"{num:02d}.mp4"
    return f"{num:02d}{seg_letter(idx)}.mp4"


# ═══════════════ 全局段号 s01（借长文案那套） ═══════════════
#
# 长文案那边一个组内的段是【全局连号】的：s001…s180，manifest 还强制校验
# "段号必须从 1 连号"。好处是查漏时一眼就看得出来 —— 缺 s017 就是缺 s017，
# 不用把每个编号的「共几段」逐个数一遍。
#
# Normal 这边原来只有【组内】段号：v01-3of12 + 文件名 01c.mp4。一批 43 段
# 分在 10 个编号里，想知道"到底还差哪几段"，得把 10 个编号各自的进度分别看一遍。
# 所以这里把全局号补上：整批按 (v 号, 第几段) 排一遍，s01…s43。
#
# 【重排策略】文案重切导致段数变化时（v05 从 4 段切成 5 段），整批重新编号，
# 保证永远连号 —— 缺号 == 缺段，这条判断规则不留例外。代价是重切之后后面的
# s 号会整体后移，所以 s 号【不进文件名】：视频文件仍然叫 01c.mp4，
# 重排不会造成任何改名。s 号只活在台账和清单里，是给人看的坐标。

def _vnum(it):
    return int(re.sub(r"\D", "", str(it.get("编号") or "")) or 0)


def sn_order(items):
    """整批按 (v 号, 第几段) 排出来的 key 列表 —— 全局段号的唯一定义。"""
    return sorted(items, key=lambda k: (_vnum(items[k]),
                                        int(items[k].get("第几段") or 0)))


def sn_map(items):
    """key → 全局段号。就地算，不依赖台账里有没有存过，老台账照样能用。"""
    return {k: i for i, k in enumerate(sn_order(items), 1)}


def assign_sn(items):
    """把全局段号写进台账每一条（人直接翻 json 也看得见）。返回 key→s。"""
    m = sn_map(items)
    for k, i in m.items():
        items[k]["全局段号"] = i
    return m


def sn_w(total):
    """位数：43 段用 s07 两位，过百才用三位。"""
    return 3 if total > 99 else 2


def fmt_sn(i, total):
    return "s%0*d" % (sn_w(total), int(i))


def sn_ranges(nums, total):
    """[13,14,15,17] → 's13–s15,s17'，缺一大片时不至于刷屏。"""
    nums = sorted(set(int(x) for x in nums))
    if not nums:
        return "无"
    out, a, b = [], nums[0], nums[0]
    for x in nums[1:]:
        if x == b + 1:
            b = x
            continue
        out.append((a, b))
        a = b = x
    out.append((a, b))
    return "，".join(fmt_sn(a, total) if a == b
                     else f"{fmt_sn(a, total)}–{fmt_sn(b, total)}"
                     for a, b in out)


# 进度格子里一个状态一个符号。「到手」= 文件已经在盘上（已下载/已验收），
# 其余一律算没到手 —— 已提交、已生成都还可能被吞掉。
_SN_OK = (S_DOWNLOADED, S_VERIFIED)
_SN_MARK = {S_VERIFIED: "✓", S_DOWNLOADED: "✓",
            S_READY: "⏳", S_SUBMITTED: "⏳",
            S_FAILED: "✗", S_LOST: "✗"}


def sn_grid(items, width=None):
    """按编号分行的进度格子。返回 (标题行, [行...], 未就绪的 s 号列表)。"""
    m = sn_map(items)
    total = len(items)
    rows, miss = [], []
    by_v = {}
    for k in sn_order(items):
        by_v.setdefault(items[k].get("编号"), []).append(k)
    for lab, keys in by_v.items():
        marks = ""
        for k in keys:
            st = items[k].get("状态")
            marks += _SN_MARK.get(st, "·")
            if st not in _SN_OK:
                miss.append(m[k])
        a, b = m[keys[0]], m[keys[-1]]
        span = (fmt_sn(a, total) if a == b
                else f"{fmt_sn(a, total)}–{fmt_sn(b, total)}")
        rows.append(f"  {lab}  {span:>9s}  {marks}")
    from collections import Counter
    c = Counter(v.get("状态") for v in items.values())
    head = (f"共 {total} 段　"
            + "　".join(f"{k} {c[k]}" for k in ORDER if c.get(k)))
    return head, rows, miss


# ═══════════════ mp4 时长（纯 python，不依赖 ffprobe） ═══════════════

def mp4_duration(path):
    """从 moov/mvhd 读时长（秒）。读不到返回 None。"""
    try:
        with open(path, "rb") as f:
            return _find_mvhd(f, 0, os.path.getsize(path))
    except Exception:
        return None


def _find_mvhd(f, start, end):
    pos = start
    while pos < end - 8:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            return None
        size = struct.unpack(">I", hdr[:4])[0]
        typ = hdr[4:8]
        head = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            head = 16
        elif size == 0:
            size = end - pos
        if size < head:
            return None
        if typ == b"mvhd":
            body = f.read(min(size - head, 120))
            ver = body[0]
            if ver == 1:
                scale, dur = struct.unpack(">IQ", body[20:32])
            else:
                scale, dur = struct.unpack(">II", body[12:20])
            return dur / scale if scale else None
        if typ in (b"moov", b"trak", b"mdia"):
            r = _find_mvhd(f, pos + head, pos + size)
            if r:
                return r
        pos += size
    return None


def is_real_mp4(data):
    return len(data) > 200000 and data[4:8] == b"ftyp"


# ═══════════════ 感知哈希（首帧 vs 源图） ═══════════════

def _dct2(a):
    import numpy as np
    n = a.shape[0]
    m = np.cos(np.pi * (2 * np.arange(n)[:, None] + 1)
               * np.arange(n)[None, :] / (2 * n))
    return m.T @ a @ m


def phash_image(path_or_array):
    """32x32 灰度 → DCT 低频 8x8 → 与中位数比较，得 63 位指纹"""
    import numpy as np
    from PIL import Image
    if isinstance(path_or_array, str):
        im = Image.open(path_or_array).convert("L").resize((32, 32),
                                                           Image.LANCZOS)
        a = np.asarray(im, dtype=np.float32)
    else:
        a = path_or_array
    d = _dct2(a)[:8, :8].flatten()[1:]
    return (d > np.median(d)).astype("uint8")


def phash_first_frame(video):
    """取视频第一帧的指纹。优先 cv2（venv 里有），退回 ffmpeg。"""
    import numpy as np
    try:
        import cv2
        cap = cv2.VideoCapture(video)
        ok, frame = cap.read()
        cap.release()
        if ok and frame is not None:
            g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            g = cv2.resize(g, (32, 32), interpolation=cv2.INTER_AREA)
            return phash_image(g.astype(np.float32))
    except Exception:
        pass
    ff = find_ffmpeg()
    if not ff:
        return None
    tmp = os.path.join(tempfile.gettempdir(), "_gp_frame.jpg")
    try:
        r = subprocess.run([ff, "-v", "error", "-y", "-i", video,
                            "-vframes", "1", "-vf", "scale=256:-1", tmp],
                           capture_output=True, timeout=60)
        if r.returncode == 0 and os.path.exists(tmp):
            return phash_image(tmp)
    except Exception:
        pass
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return None


def hamming(a, b):
    return int((a ^ b).sum())


def find_ffmpeg():
    cfg = load_json(os.path.join(SCRIPT_DIR, "flow_config.json"), {}) or {}
    for c in (cfg.get("ffmpeg"), shutil.which("ffmpeg")):
        if c and os.path.exists(c):
            return c
    return None


# ═══════════════ 建账 ═══════════════

def _pick_duration(seg_text):
    from grok_batch import pick_duration
    return pick_duration(seg_text)


def _collect(folder, v_from=None, v_to=None):
    """扫 vNN.txt + vNN.图片，切出每一段。与 grok_batch.collect_jobs 同一套规则。"""
    from grok_batch import collect_jobs
    return collect_jobs(folder, v_from, v_to)


def _build_prompts(folder, jobs):
    """组装每段实际要粘贴的完整提示词（A 层描述语 + B 层运镜卡 + 英文段）。

    与 grok_batch 走同一套 flow_shots API，运镜卡文件 vNN_shot.txt 两边共用，
    手工改过的镜头在这里同样生效。

    视频类型（表格 I 列 → 类型登记.json）在这里落地，只做一件事：
    **按类型强制改写描述语结尾的机读标签**。改完之后 cards_for / build_prompt
    自己就会挑对卡池、走对通道 —— 群体祷告写成【空镜|群像】，
    整条链路自动切到画外音 + 以人群为中心的运镜，不需要在下游再判断一次。
    类型是制作人在表格里定的，优先于 Gemini 看图猜的那个标签。
    没有 类型登记.json 就全部按普通祷告，和以前一字不差。
    """
    from desc_files import load_desc_map
    descs = load_desc_map(folder, log=lambda *a: None)
    try:
        import video_types as VT
        reg = VT.load_registry(folder)
    except Exception as e:
        log(f"  ! 读不到 video_types（{str(e)[:50]}），全部按默认类型")
        VT, reg = None, {}
    # 音色要读 sidecar 原文：load_desc_map 返回的正文已经把【音色】行剥掉了
    voices = VT.load_voice_map(folder) if VT else {}
    if VT and reg:
        from collections import Counter
        c = Counter(reg.get(lb) for lb, _i, _s in jobs if reg.get(lb))
        log("  类型登记：" + "，".join(f"{k}×{v}" for k, v in c.items())
            + f"（其余 {sum(1 for lb, _i, _s in jobs if not reg.get(lb))} 个走默认）")
    # 描述语体检。0829 起【没有类型登记.json 也要查】——
    # 原来这一段缩在 if reg 里面，而 0829 那批正是没有登记表的普通批次：
    # 10 条描述语全把一个坐着的访谈人物写成「被众人围在中间」、标签判成围绕，
    # 一路跑到底才发现，整批白烧额度。体检不该依赖制作人有没有填 I 列。
    if VT:
        _bad = VT.audit(folder, [lb for lb, _i, _s in jobs], log=log)
        if _bad:
            log("  ↑ 这些编号的提示词会自相矛盾（类型和写法对不上，"
                "或者标签写了围绕、正文里却根本没有那圈人）。")
            log("    请先用 描述语生成器.html 对它们重跑一次描述语，再来提交。")
            _build_prompts.冲突 = [x[0] for x in _bad]
        else:
            _build_prompts.冲突 = []
    else:
        _build_prompts.冲突 = []
    mode = (os.environ.get("GROK_SHOT_MODE") or "完整").strip()
    clean = build = None
    shots_lib = None
    try:
        from flow_shots import (parse_tag, strip_legacy_tail, defreeze,
                                load_shots, cards_for, build_prompt)

        def clean(desc):
            _eye, scene, _body = parse_tag(desc)
            return defreeze(strip_legacy_tail(desc), scene)

        if mode != "关闭":
            shots_lib = load_shots()
            if mode == "精简":
                shots_lib = dict(shots_lib)
                shots_lib["组装顺序"] = list(
                    shots_lib.get("组装顺序_精简") or shots_lib["组装顺序"])
            build = build_prompt
    except Exception as e:
        log(f"  ! 读不到 flow_shots（{str(e)[:60]}），只贴描述语 + 英文")

    out = {}
    for label, img, segs in jobs:
        raw = (descs.get(label) or "").strip()
        # 先按类型把标签盖上去，再往下走。顺序很重要：cards_for 和 build_prompt
        # 都是从描述语的标签里读 (眼睛, 场景) 的，标签一改，两边同时切换。
        if VT and raw:
            raw = VT.force_tag(raw, VT.type_of(folder, label, reg))
        voice = voices.get(label, "") if VT else ""
        desc = clean(raw) if (clean and raw) else raw
        cards = []
        if raw and build:
            cards, eye, scene, _m = cards_for(folder, label, len(segs),
                                              raw, shots_lib)
        for i, seg in enumerate(segs, 1):
            if raw and build:
                card = cards[i - 1] if 0 < i <= len(cards) else ""
                # seg_idx / n_segs：安静模式靠这两个算情绪弧的位置（起承转合）。
                # 老的 grok_batch 没传，所以 Grok 那边每一段情绪强度都一样，
                # 运镜又是全程同一张卡 —— 拼起来是一条平的直线。这里补上。
                # voice_lock：群像没有露脸的说话人，解说是谁的声音只能靠它锁。
                full = build(raw, card, eye, scene, seg, shots_lib,
                             seg_idx=i, n_segs=len(segs), voice_lock=voice)
            else:
                full = f"{desc}\n{seg}" if desc else seg
            out[f"{label}-{i}of{len(segs)}"] = full
    return out


ALL_MP4_JS = r"""() => {
  const re = /https:\/\/[^"'\\\s]+\.mp4[^"'\\\s]*/g;
  const out = new Set();
  for (const v of document.querySelectorAll('video')) {
    const s = v.currentSrc || v.src || ''; if (s) out.add(s);
    for (const so of v.querySelectorAll('source')) if (so.src) out.add(so.src);
  }
  // script 标签里往往埋着页面数据（出片地址常常先出现在这里，
  // 比 <video> 元素早，也不受"页面停在原图那一格"影响）
  for (const sc of document.querySelectorAll('script')) {
    const m = (sc.textContent || '').match(re); if (m) m.forEach(x => out.add(x));
  }
  const h = document.documentElement.innerHTML.match(re);
  if (h) h.forEach(x => out.add(x));
  return [...out];
}"""

BADGE_JS = r"""() => [...document.querySelectorAll('*')]
  .filter(e => e.children.length === 0 &&
               /^\d{1,2}:\d{2}$/.test((e.textContent||'').trim()))
  .map(e => (e.textContent||'').trim())"""


def _post_of(folder, key):
    """把 v21-1of2 这样的段号翻成 (postId, 会话Id)；本来就是 id 就原样返回。"""
    if not key:
        return None, None
    if re.fullmatch(r"[0-9a-fA-F][0-9a-fA-F\-]{20,}", key):
        return key, ""
    led = load_json(ledger_path(folder or ".")) or {}
    it = (led.get("条目") or {}).get(key) or {}
    return (it.get("postId") or key), it.get("会话Id", "")


def cmd_diagnose(folder=None, post=None, conv=None):
    """把页面本身交出来：地址、标题、截图、所有 mp4、缩略图角标、可点控件。

    带 post 参数时直接导到那一段的详情页去看；不带就扫当前所有 Grok 标签页。
    截图存到 当天文件夹/_诊断/ 下 —— 页面停在哪一格、是不是还在转圈、
    有没有报错，看一眼就知道，不必再从日志反推。
    """
    from playwright.sync_api import sync_playwright
    shot_dir = os.path.join(folder or ".", "_诊断")
    os.makedirs(shot_dir, exist_ok=True)
    with sync_playwright() as pw:
        try:
            br, ctx = connect(pw)
        except Exception as e:
            log(f"✗ 连不上 Chrome（端口 {CDP_PORT}）：{str(e)[:90]}")
            return
        try:
            pages = list(ctx.pages)
            if post:
                # 指定了 postId：新开一页导过去，看的就是这一段自己的详情页
                pg = ctx.new_page()
                try:
                    pg.goto(post if str(post).startswith("http")
                            else post_url(post, conv or ""),
                            wait_until="domcontentloaded", timeout=60000)
                    pg.wait_for_timeout(4000)
                except Exception as e:
                    log(f"✗ 导航失败：{str(e)[:90]}")
                pages = [pg]
            for i, pg in enumerate(pages, 1):
                try:
                    u = pg.url or ""
                except Exception:
                    continue
                if "grok.com" not in u:
                    continue
                log(f"\n── 标签页 {i}：{u[:110]}")
                try:
                    pg.bring_to_front()
                    pg.wait_for_timeout(400)
                    shot = os.path.join(
                        shot_dir, f"{(post or 'tab%d' % i)[:8]}.png")
                    pg.screenshot(path=shot, full_page=False)
                    log(f"   截图 → {shot}")
                except Exception as e:
                    log(f"   截图失败：{str(e)[:60]}")
                try:
                    log(f"   有 <video> 元素：{bool(pg.query_selector('video'))}")
                    log(f"   缩略图时长角标：{pg.evaluate(BADGE_JS)}")
                    log(f"   点缩略图结果：{nudge_to_video(pg)}")
                    allm = pg.evaluate(ALL_MP4_JS) or []
                    log(f"   页面上所有 mp4（{len(allm)} 条，完整地址）：")
                    for m2 in allm[:8]:
                        mark = "✓本段" if is_own_video(m2, post or "", bool(post)) else "✗"
                        log(f"     {mark} {m2}")
                    body = (pg.inner_text("body") or "")[:400]
                    log(f"   页面文字前 400 字：{body!r}")
                except Exception as e:
                    log(f"   取页面信息失败：{str(e)[:80]}")
                try:
                    log(f"   标题：{pg.title()}")
                except Exception:
                    pass
                try:
                    cands = pg.evaluate(SUBMIT_JS) or []
                except Exception as e:
                    log(f"   取控件失败：{str(e)[:70]}")
                    continue
                log(f"   可点控件 {len(cands)} 个：")
                for c in cands[:40]:
                    log("     text=%-28r x=%-5d y=%-5d %dx%d"
                        % (c.get("txt", "")[:28], int(c.get("x", 0)),
                           int(c.get("y", 0)), int(c.get("w", 0)),
                           int(c.get("h", 0))))
                try:
                    found = pg.evaluate(FIND_VIDEO_JS) or {}
                    strong, weak = _normalize_find(found)
                    vids = strong + [u for u in weak if u not in strong]
                    if vids:
                        log("   页面上的 mp4 地址：")
                        for v in vids[:6]:
                            tag = "strong" if v in strong else "weak"
                            own = is_own_video(
                                v, post or "", unique_strong=(len(strong) == 1 and v in strong))
                            log(f"     {tag} {'✓本段' if own else '✗外来'} {v[:100]}")
                except Exception:
                    pass
        finally:
            try:
                br.close()
            except Exception:
                pass


def _desc_missing(folder):
    """这个文件夹里还缺 vNN_desc.txt 的编号。"""
    try:
        from desc_files import list_v_images
    except Exception:
        return []
    return [lab for lab, _img, filled in list_v_images(folder) if not filled]


def cmd_desc(folder, dry=False, force=False, workers=4):
    """描述语（第 0 关）—— 把 A 层身份锁写到每张图旁边。

    借长文案那一套做法：不用人去开 描述语生成器.html 手点。提示词、类型指令
    （TYPE_DIRECTIVE）、机读标签规则统统从那个页面里取，所以页面改了这里跟着变，
    不存在两份规则对不上。跑的是独立的 desc Chrome（Chrome_Profile\\desc_chrome_profile），
    和 Grok 的调试端口井水不犯河水。

    有 类型登记.json 就按制作人定的类型切写法（群体祷告走群像版）；
    没有就整批按 Gemini 看图自己判 —— 也就是这一步存在之前的老行为。
    """
    import normal_desc as ND
    rec = ND.generate_folder(folder, log=log, dry=dry, force=force,
                             workers=workers)
    if dry:
        log(f"缺 sidecar 的有 {rec.get('n_missing', 0)} 个")
        return rec
    w, sk, fa = (rec.get("written") or [], rec.get("skipped") or [],
                 rec.get("failed") or [])
    log(f"描述语：新写 {len(w)} / 跳过 {len(sk)} / 失败 {len(fa)}")
    for f in fa:
        log(f"  ✗ {f.get('label')}：{f.get('error')}")
    return rec


def _ensure_desc(folder):
    """开跑之前先把描述语补齐。返回 False = 人选择中止。

    为什么放在这里：提示词的 A 层就是 vNN_desc.txt。缺了它，_build_prompts
    只能贴一段英文原文，运镜卡和音色全都挑不出来 —— 视频照样出，但出来的是
    废片，而且要跑完整批才看得出来。宁可开跑前多等这几分钟。
    """
    miss = _desc_missing(folder)
    if not miss:
        log("描述语：齐了，直接开跑。")
        return True
    log(f"描述语：还缺 {len(miss)} 个 —— {'、'.join(miss[:12])}"
        + ("…" if len(miss) > 12 else ""))
    try:
        cmd_desc(folder)
    except Exception as e:
        import traceback
        traceback.print_exc()
        log(f"✗ 描述语没跑成：{str(e)[:200]}")
    left = _desc_missing(folder)
    if not left:
        log("描述语：补齐了。")
        return True
    log(f"! 还有 {len(left)} 个没写出来：{'、'.join(left[:12])}"
        + ("…" if len(left) > 12 else ""))
    log("  这些编号会只带英文原文提交 —— 大概率是废片。")
    try:
        return input("  仍然开跑？(y = 跑, 回车 = 停下来先处理)：").strip().lower() == "y"
    except EOFError:
        return False


def cmd_types(folder, cfg_name=None):
    """扫表格 I 列 → 当天文件夹/类型登记.json。

    这一步是【描述语生成之前】跑的：描述语生成器.html 会读这个文件，
    按类型切换写法（群体祷告走群像版，一个字都不写主角）。
    """
    import video_types as VT
    cfg = {}
    for name in ([cfg_name] if cfg_name else
                 ["config.json", "config_grok2.json"]):
        c = load_json(os.path.join(SCRIPT_DIR, name), None)
        if c and c.get("sheet_url"):
            base = c.get("save_dir") or ""
            # 认准这个文件夹属于哪张分表：save_dir 是它的上级就是它
            if not cfg or os.path.abspath(folder).startswith(
                    os.path.abspath(base)):
                cfg = c
                if base and os.path.abspath(folder).startswith(
                        os.path.abspath(base)):
                    break
    if not cfg:
        log("✗ 找不到带 sheet_url 的分表配置")
        return
    log(f"扫表格 I 列（分表 {cfg.get('sheet_name', '?')}）→ {folder}")
    r = VT.scan_sheet(folder, cfg, log=log)
    if r is None:
        log("  · 没扫成。可以手工写 类型登记.json，格式："
            '{"v19": "群体祷告", "v20": "群体祷告"}')
        log("  · 可用类型：" + "、".join(VT.all_types()))


def cmd_ledger(folder, v_from=None, v_to=None, rebuild=False):
    """建账：铺页之前就把每一段的身份写死。已有台账只补新段，不动老状态。"""
    jobs = _collect(folder, v_from, v_to)
    if not jobs:
        log("✗ 这个文件夹里没有可用的 vNN.txt + vNN.图片")
        return None
    prompts = _build_prompts(folder, jobs)

    led = None if rebuild else load_json(ledger_path(folder))
    if not led:
        led = {"批次": os.path.basename(folder.rstrip("\\/")),
               "文件夹": os.path.abspath(folder),
               "视频目录": video_dir(folder),
               "建账时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "条目": {}, "事件": []}
    items = led["条目"]

    added = changed = 0
    for label, img, segs in jobs:
        num = int(label[1:])
        for i, seg in enumerate(segs, 1):
            key = f"{label}-{i}of{len(segs)}"
            full = prompts.get(key, seg)
            row = {
                "编号": label, "第几段": i, "共几段": len(segs),
                "输出文件名": out_name(num, i, len(segs)),
                "图片": os.path.abspath(img),
                "分段文案": norm_text(seg),
                "文案指纹": text_fp(seg),
                "提示词指纹": text_fp(full),
                "完整提示词": full,
                "时长": _pick_duration(seg),
                "状态": S_PENDING,
                "targetId": "", "postId": "", "会话Id": "",
                "视频URL": "", "已落盘路径": "", "文件md5": "",
                "备注": "", "更新时间": "",
            }
            old = items.get(key)
            if not old:
                items[key] = row
                added += 1
            else:
                # 文案变了（重切过）→ 这一段作废重来，避免拿旧视频冒充新文案
                if old.get("文案指纹") != row["文案指纹"]:
                    row["备注"] = "文案已变更，作废重来"
                    items[key] = row
                    changed += 1
                else:
                    # 图片路径每次建账都刷成【本机】的绝对路径：
                    # 台账可能是在别的机器上建的，那条路径在这里是死的。
                    for k in ("输出文件名", "图片", "完整提示词",
                              "提示词指纹", "时长"):
                        old[k] = row[k]
    # 段数变了（比如 3 段重切成 2 段）→ 删掉多出来的旧 key。
    #
    # 【只在本次范围内清理】0827 实跑踩过：--从 25 --到 25 时 jobs 里只有 v25，
    # 早先这里不分青红皂白地把"不在 jobs 里"的键全删了，一次试跑削掉了台账里
    # 另外 186 段的记录。范围参数只该限定【这次做谁】，不该动别人的档案。
    valid = {f"{lb}-{i}of{len(sg)}" for lb, _im, sg in jobs
             for i in range(1, len(sg) + 1)}
    in_range = {lb for lb, _im, _sg in jobs}
    stale = [k for k in items
             if k not in valid and items[k].get("编号") in in_range]
    for k in stale:
        items.pop(k)

    # ── 源图重复体检（0829 起）──
    # 0829 那批 10 行只用了 5 张不同的图：v03=v07=v09、v04=v06=v08、v05=v10
    # 三组逐字节相同。表格里这些行贴的是【同一个网盘文件的直链】——直链不走
    # used_files 那套查重（那只管文件夹挑图），所以下载器一声不吭地下了三遍。
    # 后果有两层：成片里 8 条建立在 3 张图上，剪到一起明显雷同；
    # 而且验收的首帧比对在这些编号之间天生分不出来。
    # 放在建账这一步报，是因为此时还一分钱额度都没花，改表格还来得及。
    try:
        import hashlib as _hl
        _by = {}
        for _k, _it in items.items():
            _g = _it.get("编号")
            if _g in _by or not os.path.exists(_it.get("图片") or ""):
                continue
            with open(_it["图片"], "rb") as _f:
                _by[_g] = _hl.md5(_f.read()).hexdigest()
        _same = {}
        for _g, _h in _by.items():
            _same.setdefault(_h, []).append(_g)
        _dup = [sorted(v) for v in _same.values() if len(v) > 1]
        if _dup:
            log("  ⚠ 源图重复：" + "；".join("=".join(x) for x in _dup)
                + " —— 这几行在表格里贴的是同一个文件，"
                  "成片会明显雷同；要换图就去表格改链接后重下这几行。")
    except Exception:
        pass

    # 全局段号：整批重排一次，保证 s 号永远从 1 连号（见 assign_sn 上方注释）
    m = assign_sn(items)
    led["建账时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json(ledger_path(folder), led)
    log(f"✓ 台账 {len(items)} 段（新增 {added}，因文案变更作废重来 {changed}，"
        f"清掉失效 {len(stale)}）→ {ledger_path(folder)}")
    if m:
        n = len(items)
        by_v = {}
        for k, i in m.items():
            by_v.setdefault(items[k].get("编号"), []).append(i)
        log(f"  全局段号 {fmt_sn(1, n)}–{fmt_sn(n, n)}："
            + "，".join(
                f"{lab} {fmt_sn(min(v), n)}–{fmt_sn(max(v), n)}"
                for lab, v in sorted(by_v.items())[:8])
            + ("…" if len(by_v) > 8 else ""))
    summary(led)
    return led


def summary(led):
    from collections import Counter
    c = Counter(v["状态"] for v in led["条目"].values())
    line = "　".join(f"{s} {c[s]}" for s in ORDER if c.get(s))
    log(f"  台账 {len(led['条目'])} 段：{line}")


def record(led, folder, key, **kw):
    """改一段状态并立刻落盘 —— 每一步都可断点续跑。"""
    it = led["条目"][key]
    it.update(kw)
    it["更新时间"] = datetime.now().strftime("%m-%d %H:%M:%S")
    led["事件"].append({"时": datetime.now().strftime("%m-%d %H:%M:%S"),
                        "段": key,
                        "状": kw.get("状态", it["状态"]),
                        "注": kw.get("备注", "")[:80]})
    led["事件"] = led["事件"][-4000:]
    save_json(ledger_path(folder), led)


# ═══════════════ 页面探测用的 JS ═══════════════

# 闸1：分级返回。strong = 页面上真实的 <video>/<source> 元素；
# weak = a[href] 和整页 innerHTML 正则扫出来的（SPA 预取 JSON、「最近创作」栏
# 里邻居 post 的地址都在这里）。probe 只认 strong，绝不降级用 weak。
FIND_VIDEO_JS = r"""() => {
  const uniq = (arr) => [...new Set((arr || []).filter(u => u && !String(u).startsWith('blob:')))];
  const pushSrc = (v, into) => {
    const s = v.currentSrc || v.src || '';
    if (s) into.push(s);
    for (const so of v.querySelectorAll('source')) if (so.src) into.push(so.src);
  };

  const videos = [...document.querySelectorAll('video')];
  const cx = (window.innerWidth || 0) / 2, cy = (window.innerHeight || 0) / 2;
  const vis = videos.map(v => {
    const r = v.getBoundingClientRect();
    const w = Math.max(0, Math.min(r.right, window.innerWidth || 0) - Math.max(r.left, 0));
    const h = Math.max(0, Math.min(r.bottom, window.innerHeight || 0) - Math.max(r.top, 0));
    const dx = r.left + r.width / 2 - cx, dy = r.top + r.height / 2 - cy;
    return {v, area: w * h, dist: dx * dx + dy * dy, w: r.width, h: r.height};
  }).filter(x => x.area > 0 && x.w > 8 && x.h > 8);
  vis.sort((a, b) => a.dist - b.dist);
  const hero = vis.length ? vis[0].v : (videos[0] || document.querySelector('video'));

  const looksLikePost = (el) => {
    if (!el || el === document.body || el === document.documentElement) return false;
    const tag = (el.tagName || '').toLowerCase();
    const cls = ((el.className || '') + ' ' + (el.id || '')).toString().toLowerCase();
    if (tag === 'article' || tag === 'main') return true;
    if (/post|imagine|media|player|content/.test(cls)) return true;
    return false;
  };
  let box = null;
  if (hero) {
    let p = hero.parentElement;
    while (p && p !== document.body) {
      if (looksLikePost(p)) { box = p; break; }
      p = p.parentElement;
    }
    if (!box) box = document.body;
  }
  const pool = box ? [...box.querySelectorAll('video')] : videos;
  const strong = [];
  for (const v of pool) pushSrc(v, strong);

  const weak = [];
  for (const a of document.querySelectorAll('a[href]'))
    if (/\.mp4(\?|$)/i.test(a.href)) weak.push(a.href);
  const m = document.documentElement.innerHTML
              .match(/https?:\/\/[^"'\s\\]+\.mp4[^"'\s\\]*/g);
  if (m) weak.push(...m);

  const strongU = uniq(strong);
  const weakU = uniq(weak).filter(u => !strongU.includes(u));
  return { strong: strongU, weak: weakU };
}"""

# 只有【本次生成出来的】那个视频算数。
#
# 0827 实跑：刚点完提交 1 秒，FIND_VIDEO_JS 最后那招"拿正则在整页 HTML 里捞 mp4"
# 就捞到了页面里嵌的 https://data.x.ai/grok-4-fast-side-by-side.mp4 —— Grok 自己的
# 宣传片。于是当场判成「已生成」并去下载，全靠闸3 比对 postId 才拦住。
# 拦在最后一道太晚了：探测这一步就不该认它。
#
# 0909 T0013：on_post_page 无条件 return True 会把页面上「最近创作」里邻居
# 的 mp4 当成自己的。on_post_page 只是加分项，最终仍须 url_matches_post
# 或闸1 判出的唯一 strong 候选（unique_strong，由 probe 显式传入）。
def is_own_video(url, post="", on_post_page=False, unique_strong=False):
    """这个地址是不是【本段】生成出来的视频。

    两条依据，任一成立即可：
      · unique_strong —— 闸1 在本页只看到 1 个 <video> 元素，probe 显式标上的。
      · url_matches_post —— 资源路径里带着 postId 前缀。

    on_post_page 不再单独放行。不管哪条成立，都必须先是【用户生成物】：
    宣传片 data.x.ai 这类一律不认。

    签名地址常常没有 .mp4 扩展名。主机是 assets.grok.com、或路径含 /generated/
    就够了，不再卡扩展名（规格 51）。
    """
    u = (url or "").lower()
    if not u.startswith("http"):
        return False
    if "data.x.ai" in u:
        return False          # 宣传片，不论有没有 .mp4
    if "assets.grok.com" not in u and "/generated/" not in u:
        return False          # 不是用户生成物的存放位置，一律不认
    if unique_strong:
        return True
    return url_matches_post(url, post)

IS_GENERATING_JS = r"""() => {
  const t = (document.body.innerText || '').toLowerCase();
  if (/generating|creating|正在生成|生成中/.test(t)) return true;
  return [...document.querySelectorAll('*')].some(
      e => e.children.length === 0 && /^\d{1,3}\s*%$/.test(
          (e.textContent || '').trim()));
}"""

IS_FAILED_JS = r"""() => {
  const t = document.body.innerText || '';
  const m = t.match(/失败|failed|error|unavailable|try again|rate limit|moderat/i);
  return m ? t.slice(Math.max(0, t.indexOf(m[0]) - 40),
                     t.indexOf(m[0]) + 90) : '';
}"""

# 被踢回首页的 toast。不要并进 IS_FAILED_JS：首页信息流里也可能出现这些词，
# 会把还在生成的段误判成失败。被踢只走 URL + 这条 toast。
BOUNCE_TEXT_RE = re.compile(
    r"post\s+(not found|non trovato|introuvable|nicht gefunden|no encontrado)"
    r"|未找到|找不到",
    re.I,
)
HOME_RE = re.compile(r"grok\.com/imagine/?(\?|$)", re.I)
BOUNCE_TEXT_JS = "() => (document.body && document.body.innerText) || ''"

SUBMIT_JS = r"""() => {
  const cands = [];
  for (const b of document.querySelectorAll(
        'button, [role=button], [type=submit]')) {
    if (b.disabled || b.getAttribute('aria-disabled') === 'true') continue;
    const r = b.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    const txt = ((b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '')
                 + ' ' + (b.title || '')).trim();
    cands.push({txt: txt.slice(0, 40), x: r.x, y: r.y,
                w: r.width, h: r.height});
  }
  return cands;
}"""


# ═══════════════ 铺页 ═══════════════

def target_id(ctx, page):
    """拿这个标签页在 CDP 里的唯一 id —— 中断重跑时靠它找回同一个页，
    不靠会被 Grok 冲掉的标题。"""
    try:
        s = ctx.new_cdp_session(page)
        info = s.send("Target.getTargetInfo")
        return (info.get("targetInfo") or {}).get("targetId", "")
    except Exception:
        return ""


# ═══════════════ 0828：页面会「变心」，铺完那一刻的校验不算数 ═══════════════
#
# 实跑记录（0828）：13 段全部「铺页」逐字校验通过，一分钟后
# 回来提交，框里的字全没了，只剩图片。脚本抱着一分钟前的校验结果照点不误，
# 于是 Grok 把那张图当成一条【图片帖】发了出去 —— post 页右边是
# Segments / Remove Background / Make Video，根本不是视频任务。
# v01、v02-1of2、v02-2of2、v03-1of3 四条额度就是这么烧掉的。
#
# 两条根因，缺一不可：
#   1) 图片是异步传的。blob: 缩略图是本地预览，贴上就有；真正传完要几秒到
#      十几秒。传完那一刻 Grok 会把输入区重挂一次，框里的字被连带清空 ——
#      而这时 verify_tab 早就过完了。
#   2) 铺页和提交之间隔着几十秒到几分钟（一波先铺满 12 页再挨个提交），
#      这中间没有任何复检，页面变成什么样没人知道。
#
# 对策三手一起上：
#   · 铺完页「静置再验」，字被冲掉就地补回去，连站住两轮才算数；
#   · 提交前再验一遍（ensure_intact），并且塞进 click_submit 的每一轮轮询里，
#     对不上就不点 —— 宁可这一段失败重铺，也不拿空框去烧额度；
#   · 整波「先铺完再提交」改成「即铺即提」，把这个窗口压到最短。

COMPOSER_JS = r"""() => {
  const ed = document.querySelector('div.tiptap');
  return {url: location.href,
          text: ed ? (ed.innerText || '') : null,
          hasImg: !!document.querySelector("img[src^='blob:']")};
}"""


def composer_state(page):
    """一次拿全：当前地址 / 框里的字 / 缩略图还在不在。读不到就当页面废了。"""
    try:
        return page.evaluate(COMPOSER_JS)
    except Exception as e:
        return {"url": "", "text": None, "hasImg": False, "err": str(e)[:60]}


def _same_text(a, b):
    return norm_text(a or "") == norm_text(b or "")


# ═══════════ 0828 探针实证：页面上有两个 input[type=file] ═══════════
#
#   #0  input.hidden < div.flex.min-w-0.flex-1 < div.mx-auto.flex.w-full …
#       ← 页面顶层那个「上传图片、发成一条图片帖」的入口
#   #1  input.hidden < form.w-full.text-base.flex < div.pointer-events-auto …
#       ← 输入框（composer）自己的附件入口
#
# set_input_files("input[type=file]") 抓的永远是第一个，也就是 #0 ——
# 于是一传图，Grok 就当场把它发成一条图片帖并跳到 /imagine/post/<id>，
# 老的「贴字 + 选 Video/720p/时长 + 提交」界面当场消失。上午那 4 条额度
# 就是在图片帖上按了发送烧掉的。
#
# 人手动把图拖进输入框不跳页 —— 因为那走的是 div.tiptap 的 drop 事件。
# 所以这里照着人手那条路走：构造真的 File 塞进 DataTransfer，
# 朝输入框依次派 dragenter / dragover / drop。
# 探针实测：图挂上、不跳页、Video/480p/720p/1080p/6s/10s/15s 全在、字贴得进去。

DROP_JS = r"""async ({b64, name, type, minCount}) => {
  const want = Math.max(1, minCount || 1);
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  const file = new File([arr], name, {type});
  const dt = new DataTransfer();
  dt.items.add(file);

  const ed = document.querySelector('div.tiptap');
  const targets = [];
  if (ed) {
    targets.push(ed);
    const form = ed.closest('form') || ed.parentElement;
    if (form) targets.push(form);
    if (form && form.parentElement) targets.push(form.parentElement);
  }
  targets.push(document.body);

  const fired = [];
  for (const el of targets) {
    for (const t of ['dragenter', 'dragover', 'drop'])
      el.dispatchEvent(new DragEvent(t, {bubbles: true, cancelable: true,
                                         dataTransfer: dt}));
    fired.push(el.tagName.toLowerCase() + '.'
      + ((el.className || '').toString().split(/\s+/).filter(Boolean)[0] || ''));
    await new Promise(r => setTimeout(r, 700));
    if (document.querySelectorAll("img[src^='blob:']").length >= want)
      return {ok: true, fired, n: document.querySelectorAll("img[src^='blob:']").length};
  }
  return {ok: false, fired, n: document.querySelectorAll("img[src^='blob:']").length};
}"""

_MIME = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
         ".bmp": "image/bmp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def attach_by_drop(page, img_path, min_count=1):
    """按人手拖拽那条路把图挂进输入框。挂不上就抛异常。

    min_count：接片第二张图时页面上已经有一张 blob，必须等到 ≥2 才算挂上，
    否则一看见第一张就提前返回，第二张其实没传。
    """
    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    mime = _MIME.get(os.path.splitext(img_path)[1].lower(), "image/jpeg")
    r = page.evaluate(DROP_JS, {"b64": b64,
                                "name": os.path.basename(img_path),
                                "type": mime,
                                "minCount": int(min_count or 1)})
    if not (r or {}).get("ok"):
        raise Exception(f"拖拽传图没挂上（派过 {(r or {}).get('fired')}，已有 {(r or {}).get('n')} 张）")
    return r


# 只盯输入框那一块的指纹。0828 探针实测：盯整页永远等不到安静 ——
# 首页底下的推荐流一直在变，45 秒上限每次都耗满。
FINGERPRINT_JS = r"""() => {
  const ed = document.querySelector('div.tiptap');
  const scope = (ed && (ed.closest('form') || ed.parentElement)) || document.body;
  return [location.href,
          scope.querySelectorAll('img').length,
          scope.querySelectorAll("img[src^='blob:']").length,
          ed ? ed.innerHTML.length : -1,
          scope.querySelectorAll('button').length,
          (scope.innerText || '').length].join('|');
}"""


def wait_quiet(page, quiet=3.0, timeout=60.0, tag=""):
    """等页面真的安静下来再动它。

    0828 的教训：图片是异步传的，blob: 缩略图是本地预览，一贴上就有，
    可真正传完还要好几秒 —— 传完那一刻 Grok 会把整个输入区重挂一次，
    框里的字被连带清空。原来在这儿傻等固定 3 秒，等得够不够全凭运气。
    现在改成盯着页面指纹（地址 / 图片数 / 编辑器内容长度 / 按钮数），
    连续 quiet 秒一动不动，才认为那次重挂已经过去了。
    """
    t0 = time.time()
    last, since = None, time.time()
    while time.time() - t0 < timeout:
        try:
            fp = page.evaluate(FINGERPRINT_JS)
        except Exception:
            fp = None
        now = time.time()
        if fp != last:
            last, since = fp, now
        elif now - since >= quiet:
            waited = now - t0
            if waited > 5:
                log(f"    · {tag} 等页面稳定花了 {waited:.0f} 秒"
                    f"（上传重挂确实是异步的）")
            return waited
        page.wait_for_timeout(400)
    log(f"    ! {tag} 等了 {timeout:.0f} 秒页面还在动，先往下走")
    return timeout


def type_prompt(page, text):
    """把提示词写进输入框：先清空再贴，避免补字时叠成两份。"""
    editor = page.locator("div.tiptap").first
    editor.click(timeout=8000)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.wait_for_timeout(150)
    page.keyboard.insert_text(text)
    page.wait_for_timeout(500)


def settle_text(page, text, rounds=4, gap=2200):
    """静置复验：贴完字别急着走，等几轮看它会不会被异步重挂冲掉。
    被冲掉就补回去；连续两轮都还在，才算真站住了。"""
    stable = 0
    for _ in range(rounds):
        page.wait_for_timeout(gap)
        st = composer_state(page)
        if POST_RE.search(st.get("url") or ""):
            raise Exception("页面自己跳到 post 页了：…" + (st.get("url") or "")[-38:])
        if st.get("text") is None:
            raise Exception("输入框不见了（页面被重挂）")
        if _same_text(st["text"], text):
            stable += 1
            if stable >= 2:
                return
            continue
        stable = 0
        type_prompt(page, text)
    st = composer_state(page)
    if not _same_text(st.get("text"), text):
        raise Exception("提示词站不住（补了几轮还是被冲掉）")


def ensure_intact(page, row):
    """提交前最后一道闸：切前台 → 对地址 → 对图 → 对字，不对就地补，
    补不回来就抛异常（这一页作废，宁可失败也不拿空框去点提交）。"""
    from grok_batch import set_params
    text = row["完整提示词"]
    try:
        page.bring_to_front()
    except Exception:
        pass
    page.wait_for_timeout(800)
    st = composer_state(page)
    if POST_RE.search(st.get("url") or ""):
        raise Exception("这一页已经跳到 post 页（…"
                        + (st.get("url") or "")[-38:] + "），不是能提交的铺页")
    if st.get("text") is None:
        raise Exception("输入框不见了" + ("：" + st["err"] if st.get("err") else ""))
    if not st.get("hasImg"):
        raise Exception("图片没了（缩略图不在了）")
    if _same_text(st["text"], text):
        _assert_duration(page, row)
        return True
    log(f"    ! 提交前发现框里的字被冲掉了"
        f"（现在只剩 {len(norm_text(st['text'] or ''))} 字符），就地补回去")
    set_params(page, row["时长"], log=log)
    type_prompt(page, text)
    settle_text(page, text)
    st = composer_state(page)
    if not _same_text(st.get("text"), text) or not st.get("hasImg"):
        snapshot(page, row.get("编号", "?") + "-" + str(row.get("第几段", "?")),
                 "补字之后还是对不上")
        raise Exception("补字之后还是对不上")
    _assert_duration(page, row)
    return True


def _assert_duration(page, row):
    """提交前最后确认时长档位 —— 错档的片子宁可不提交，也别烧额度。

    0904：这道闸以前不存在，于是 5 段想要 10s 的片子按 15s 出完、下完，
    到验收才被判"时长不符"，额度已经烧掉了。
    """
    want = str(row.get("时长") or "").strip()
    if not want:
        return
    from grok_batch import DUR_STRICT, read_duration, _dump_dur_probe
    cur, probe = read_duration(page)
    if cur == want:
        return
    _dump_dur_probe(probe, want, cur)
    if cur is None:
        log(f"    ⚠ 提交前读不回时长档位（想要 {want}）")
        if DUR_STRICT == "strict":
            raise Exception(f"提交前读不回时长档位（想要 {want}）")
        return
    snapshot(page, row.get("编号", "?") + "-" + str(row.get("第几段", "?")),
             f"时长档位不对：想要 {want}，页面上是 {cur}")
    raise Exception(f"时长档位不对：想要 {want}，页面上是 {cur}")


def prepare(ctx, row, folder=None):
    """开页 → 传图 → 等图真的落住 → 设参数 → 贴提示词 → 静置复验。
    失败关页抛异常。"""
    from grok_batch import pass_tos_gate, set_params, verify_tab
    img = resolve_image(folder or os.path.dirname(row.get("图片") or ""), row)
    text = row["完整提示词"]
    page = ctx.new_page()
    try:
        page.goto(IMAGINE_URL, wait_until="domcontentloaded", timeout=60000)
        pass_tos_gate(page)
        page.wait_for_selector("input[type=file]", state="attached",
                               timeout=30000)
        page.wait_for_selector("div.tiptap", state="visible", timeout=30000)
        # 画幅（9:16）只在没传图时给选 —— 传完参考图 Grok 就按图片比例走了，
        # 所以先设一遍，传完图再设一遍其余参数。
        set_params(page, row["时长"], log=log)
        last = row.get("尾帧") or ""
        if last and os.path.exists(last):
            # 接片双图。默认先 01_首帧_start 再 02_尾帧_end（写法④）。
            # 探针 40 若反序更好，row['接片上传顺序']='end_then_start'：先传终帧再传起帧。
            # 文件名不改；提示词是否跟着改由 pipeline.GROK_BRIDGE_FLIP_PROMPT 管。
            order = row.get("接片上传顺序") or "start_then_end"
            log("    · 双图接片（写法 %s）" % order)
            if order == "end_then_start":
                first_img, second_img = last, img
            else:
                first_img, second_img = img, last
            attach_by_drop(page, first_img, min_count=1)
            page.wait_for_function(
                "() => document.querySelectorAll(\"img[src^='blob:']\").length >= 1",
                timeout=100000)
            wait_quiet(page, quiet=2.0, timeout=100.0,
                       tag=f"{row.get('编号', '')}-{row.get('第几段', '')}-img1")
            attach_by_drop(page, second_img, min_count=2)
            page.wait_for_function(
                "() => document.querySelectorAll(\"img[src^='blob:']\").length >= 2",
                timeout=100000)
        else:
            attach_by_drop(page, img)     # ← 走人手拖拽那条路，不是 set_input_files
            page.wait_for_selector("img[src^='blob:']", state="attached",
                                   timeout=100000)
        # 缩略图只是本地预览，后面还有一次重挂。贴字必须等在那之后 ——
        # 等到页面自己不动了为止，别猜秒数。
        wait_quiet(page, quiet=3.0, timeout=100.0,
                   tag=f"{row.get('编号', '')}-{row.get('第几段', '')}")
        st = composer_state(page)
        if POST_RE.search(st.get("url") or ""):
            raise Exception("传完图页面自己跳到 post 页了")
        if not st.get("hasImg"):
            raise Exception("图片没挂住（缩略图又没了）")
        if last and os.path.exists(last):
            n_blob = page.evaluate("() => document.querySelectorAll(\"img[src^='blob:']\").length")
            if n_blob < 2:
                raise Exception("接片两张图没挂齐（现在 %s 张）" % n_blob)
        set_params(page, row["时长"], log=log)
        type_prompt(page, text)
        # 闸1：逐字一致 + 图片已附 + 时长档位对（0904 起时长也进这道闸）
        verify_tab(page, text, row["时长"])
        settle_text(page, text)       # 闸1.5：静置再验，防异步重挂把字冲掉
        return page
    except Exception as e:
        # 0828：以前这里二话不说 page.close()，于是一出事页签全没了，
        # 人回头去看只剩一片空白。现在先取证，再决定关不关。
        snapshot(page, row.get("编号", "?") + "-" + str(row.get("第几段", "?")),
                 f"铺页失败：{e}")
        global _KEEP_ONE
        if not _KEEP_ONE:
            _KEEP_ONE = True
            log("    · 这一页先留着不关，你可以直接去 Chrome 里看它现在长什么样")
            raise
        try:
            page.close()
        except Exception:
            pass
        raise


_KEEP_ONE = False


# ═══════════════ 提交 + 锚定 ═══════════════

POST_RE = re.compile(r"/imagine/post/([0-9a-fA-F][0-9a-fA-F\-]{7,})")


def click_submit(page, timeout=100, guard=None):
    """点提交。轮询等到按钮真的可点为止。

    0827 实跑：同一批两个标签页，v25-2of2 用 button:Submit 一击即中拿到 postId，
    v25-1of2 同一个选择器 3 秒超时。选择器没错，是【按钮还没就绪】——
    图片刚传完、文案刚贴上，按钮那一刻要么还 disabled 要么还没渲染出来。
    所以这里不再"四种点法各试一次就放弃"，而是在 timeout 秒内反复轮询，
    每一轮四种点法都试一遍，直到页面给出真的提交了的信号。
    """
    # 先把这一页切到前台再点。
    # 0827 首跑的时间线说明按钮是「要等」的：v25-1of2 铺完页 5 秒就去点，全灭；
    # v25-2of2 隔了 11 秒去点，一击即中。而一波开好几个标签页之后，
    # 要点的那个多半在后台 —— 后台标签页 Grok 常常不渲染/不激活按钮，
    # 光靠等是等不来的。切前台 + 轮询，两条一起才稳。
    try:
        page.bring_to_front()
        page.wait_for_timeout(600)
    except Exception:
        pass

    # 0908：这里原来只认英文（submit / generate / create）。Grok 的界面跟着账号
    # 语言走——有的账号界面是中文、有的是意大利文，那颗提交按钮还常常只有一个
    # 圆箭头图标、没有文字。所以：说法多语言化，并且第一轮点不中就先试键盘，
    # 别干等满 timeout 再兜底（键盘那条路 _generate_via_ui 生图时天天在用，很稳）。
    SUBMIT_WORDS = (r"submit|generate|create|send|"
                    r"invia|genera|crea|inviare|"
                    r"enviar|generar|envoyer|générer|senden|"
                    r"提交|生成|创建|发送|送出|產生")

    def locs():
        return [
            ("button:Submit", page.get_by_role(
                "button", name=re.compile(rf"^({SUBMIT_WORDS})$", re.I))),
            ("text:Submit", page.get_by_text(
                re.compile(rf"^({SUBMIT_WORDS})$", re.I), exact=False)),
            ("aria:send", page.locator(
                "button[aria-label*='ubmit'], button[aria-label*='enerate'],"
                " button[aria-label*='end'], button[aria-label*='nvia'],"
                " button[aria-label*='提交'], button[aria-label*='发送'],"
                " button[aria-label*='生成'], button[type=submit]")),
        ]

    # 2026-10-06（T0124 丢了 11 条）：点了以后 1.5 秒内页面还没跳，就记成「点了没反应」；
    # 下一轮先跑 guard，这时页面已经跳到 post 页 → guard 报「不是能提交的铺页」→ 整段判失败。
    # 其实 Grok 已经收下在出片（额度花了、片子成了孤儿）。所以：点过 / 按过之后，
    # 先看是不是已经跳到 post 页，是就当提交成功，绝不再跑 guard。
    acted = {"how": ""}

    def landed():
        if acted["how"] and submitted_signal(page):
            return acted["how"] + "(late)"
        return ""

    def wait_landed(total_ms=6000, step=500):
        """点完 / 按完以后多等一会儿看它跳不跳（T0124：Grok 常常 2 秒以上才跳），
        别 1.5 秒没动静就去点下一种——那样可能交出第二条。"""
        waited = 0
        while waited < total_ms:
            if submitted_signal(page):
                return True
            page.wait_for_timeout(step)
            waited += step
        return submitted_signal(page)

    def run_guard():
        """跑 guard；guard 报错时如果其实已经提交上了（guard 里那 0.8 秒页面刚好跳走），返回成功。"""
        if guard is None:
            return ""
        try:
            guard()
        except Exception:
            late = landed()
            if late:
                return late
            raise
        return ""

    def by_keyboard(tag):
        """键盘提交。语言无关，是图标按钮认不出来时最靠谱的一条路。
        按之前必须再对一遍页面（0828 就是这一步拿空框把 v01 发成了图片帖）。"""
        for keys in ("Enter", "Control+Enter"):
            try:
                late = landed() or run_guard()
                if late:
                    return late
                acted["how"] = f"{tag}:{keys.lower()}"
                page.keyboard.press(keys)
                if wait_landed():
                    return f"{tag}:{keys.lower()}"
            except Exception as e:
                tried[f"{tag}:{keys.lower()}"] = str(e)[:40]
        return ""

    tried, t0, rounds = {}, time.time(), 0
    while time.time() - t0 < timeout:
        # 0828：按钮可能要等十几秒才活过来，这十几秒里页面照样会被重挂把字
        # 冲掉。所以每一轮点之前都重新对一遍 —— guard 抛异常就干脆别点了。
        late = landed() or run_guard()
        if late:
            return late
        for name, loc in locs():
            try:
                late = landed()
                if late:
                    return late
                if loc.count() == 0:
                    tried[name] = "页面上没有"
                    continue
                el = loc.first
                if not el.is_enabled(timeout=1000):
                    tried[name] = "还是灰的"
                    continue
                acted["how"] = name
                el.click(timeout=2500)
            except Exception as e:
                tried[name] = str(e)[:40]
                continue
            if wait_landed():
                return name
            tried[name] = "点了没反应"
        # 一轮都没成：让页面再稳一会儿。刚上传完图片时按钮常常要几秒才活过来。
        page.wait_for_timeout(2000)
        rounds += 1
        if rounds == 1:
            # 第一轮就没点中，多半是按钮只有图标 / 说法不认识，直接上键盘。
            how = by_keyboard("early")
            if how:
                return how

    how = by_keyboard("last")
    if how:
        return how
    late = landed()
    if late:
        return late
    raise Exception(f"等了 {timeout} 秒仍点不动提交（"
                    + " | ".join(f"{k}={v}" for k, v in tried.items()) + "）")


def submitted_signal(page):
    """确认真的提交出去了：URL 变成 post 详情，或者页面开始转圈。"""
    try:
        if POST_RE.search(page.url or ""):
            return True
        return bool(page.evaluate(IS_GENERATING_JS))
    except Exception:
        return False


def post_url(post, conv=""):
    """拼详情页地址。

    conversation 这一段【不能省】：少了它 Grok 直接回
    「Post not found. Redirecting to imagine home...」把你踹回首页。
    """
    u = "https://grok.com/imagine/" + "post/" + str(post)
    return f"{u}?conversation={conv}" if conv else u


def capture_anchor(page, timeout=90, conv_wait=15):
    """闸2：提交后死盯 URL，抓 postId 和 conversation。抓不到返回空 —— 绝不猜。

    为什么抓到 postId 还要再等：Grok 先把地址改成 /imagine/post/<id>，
    过一会儿才补上 ?conversation=。抓到 postId 就返回的话，
    会话Id 十有八九是空的 —— 而少了它，之后就再也导不回这个页面。
    0827 实跑：18 段有 postId，只有 14 段有会话Id，缺的正好是卡住的那 4 段。
    """
    t0 = time.time()
    post = ""
    while time.time() - t0 < timeout:
        try:
            u = page.url or ""
            m = POST_RE.search(u)
            if m:
                post = m.group(1)
                cid = re.search(r"conversation=([0-9a-fA-F\-]+)", u)
                if cid:
                    return post, cid.group(1)
                # postId 有了、conversation 还没出现：再等一会儿
                if time.time() - t0 > conv_wait:
                    return post, ""
        except Exception:
            pass
        page.wait_for_timeout(400)
    return post, ""


# 缩略图里哪一格是视频：找带时长角标（0:15 这样）的那一格，点它的可点祖先。
# 用角标而不是 class 名 —— 角标是"这是个视频"的自然标记，Grok 改版也不容易失效。
CLICK_VIDEO_THUMB_JS = r"""() => {
  if (document.querySelector('video')) return 'already';
  const badge = [...document.querySelectorAll('*')].filter(e =>
      e.children.length === 0 && /^\d{1,2}:\d{2}$/.test((e.textContent||'').trim()));
  for (const b of badge.reverse()) {          // 视频那一格通常在后面
    let el = b;
    for (let i = 0; i < 8 && el; i++, el = el.parentElement) {
      const r = el.getBoundingClientRect();
      if (r.width > 30 && r.height > 30) {
        el.click();
        return 'clicked:' + (b.textContent||'').trim();
      }
    }
  }
  return 'nothing';
}"""


def nudge_to_video(page):
    """页面停在原图那一格时，点开视频那一格。返回做了什么，便于日志排查。"""
    try:
        r = page.evaluate(CLICK_VIDEO_THUMB_JS)
    except Exception:
        return "err"
    if str(r).startswith("clicked"):
        try:
            page.wait_for_timeout(1800)
        except Exception:
            pass
    return r


def _page_url(page) -> str:
    try:
        return page.url or ""
    except Exception:
        return ""


def is_imagine_home(url: str) -> bool:
    """落在 /imagine 根路径，或地址里根本没有 /post/。"""
    u = url or ""
    if HOME_RE.search(u):
        return True
    return "/post/" not in u


def _bounce_toast(page) -> str:
    """从页面正文抠「Post not found」这类 toast，抠不到返回空串。"""
    try:
        text = page.evaluate(BOUNCE_TEXT_JS) or ""
    except Exception:
        text = ""
    m = BOUNCE_TEXT_RE.search(text)
    if not m:
        return ""
    i = m.start()
    return " ".join(text[max(0, i - 8): i + 80].split())


def same_conv_post(url: str, conv: str) -> str:
    """地址是不是「同一会话下的另一个 post 详情页」。是就返回那个新 postId，否则 ""。

    2026-10-16 T0130 实测：Grok 出片后会把地址从「提交时的 postId（图）」改成
    「视频自己的 postId」，conversation 不变，页面上就是做好的 10s 视频
    （assets.grok.com/.../generated/<新postId>/...）。以前只认提交时那个 id，
    把这种正常改址当成「被踢回首页」，两轮后判失败、扔掉好片重交，失败率近一半。
    """
    u = url or ""
    c = str(conv or "").strip().lower()
    if not c or f"conversation={c}" not in u.lower():
        return ""
    m = POST_RE.search(u)
    return m.group(1) if m else ""


def goto_own_post(page, post, conv=""):
    """确保这一页停在本段自己的 post 详情页。

    返回三态：
      "on_post"  地址里有本段 postId，或同一会话下 Grok 改成的视频 postId
                 （后者记在 goto_own_post.redirected）
      "bounced"  导航了但被踢回首页（toast 记在 goto_own_post.last_toast）
      "stayed"   没会话 Id，原地没动

    【没有会话Id 就绝不主动导航】—— 裸地址 /imagine/post/<id> 会被 Grok
    判成 Post not found 踹回首页，等于亲手把一个本来好好的页面导进死路。
    这种时候留在原地读，反而更可能读到东西。
    """
    goto_own_post.last_toast = ""
    goto_own_post.redirected = ""
    if not post:
        return "stayed"
    u = _page_url(page)
    if post.lower() in u.lower():
        return "on_post"
    if not conv:
        return "stayed"       # 拼不出能打开的地址，老实待着
    alt = same_conv_post(u, conv)
    if alt:
        goto_own_post.redirected = alt
        return "on_post"
    try:
        page.goto(post_url(post, conv),
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
    except Exception:
        pass
    u = _page_url(page)
    if post.lower() in u.lower():
        return "on_post"
    alt = same_conv_post(u, conv)
    if alt:
        goto_own_post.redirected = alt
        return "on_post"
    toast = _bounce_toast(page)
    goto_own_post.last_toast = toast
    return "bounced"


goto_own_post.last_toast = ""
goto_own_post.redirected = ""


def _normalize_find(found):
    """FIND_VIDEO_JS 新返回 {strong, weak}；老数组一律当弱证据。"""
    if isinstance(found, dict):
        strong = [u for u in (found.get("strong") or []) if u]
        weak = [u for u in (found.get("weak") or []) if u]
        return strong, weak
    return [], list(found or [])


# 规格 51：probe 的 detail 要带上页面上每一个 <video>（含 blob:），
# 不能靠 FIND_VIDEO_JS——那条会把 blob 滤掉。这条 JS 只读，不改 FIND_VIDEO_JS。
ALL_VIDEO_JS = r"""() => [...document.querySelectorAll('video')].map(v => {
  const src = v.getAttribute('src') || v.src || '';
  const cs = v.currentSrc || '';
  const r = v.getBoundingClientRect();
  const dur = v.duration;
  return {
    src: src,
    currentSrc: cs,
    sources: [...v.querySelectorAll('source')].map(s => s.src || ''),
    readyState: v.readyState || 0,
    duration: (typeof dur === 'number' && isFinite(dur)) ? dur : 0,
    visible: r.width > 8 && r.height > 8,
  };
})"""


def probe(page, post="", seen=None, on_post_page=False, logfn=None):
    """这一页现在什么情况：('已生成', 地址, detail) / ('生成中','', detail)
    / ('失败', 原因, detail) / ('未知','', detail) / ('不在详情页','', detail)

    闸1：只认 strong（页面上的 <video> 元素）。恰好 1 个才算已生成；
    0 个或 ≥2 个都继续等。绝不降级去用 weak（innerHTML 正则 / a[href]）。
    不在本段详情页上时直接返回「不在详情页」，不去数首页信息流的 <video>。

    第三项 detail：{videos, strong, weak, why}，给上层打「看见 N 个 video 过不了闸」。
    """
    def _lg(msg):
        if logfn:
            try:
                logfn(msg)
            except Exception:
                pass
        try:
            log(msg)
        except Exception:
            pass

    detail = {"videos": [], "strong": [], "weak": [], "why": ""}

    if post:
        u = _page_url(page)
        if post.lower() not in u.lower():
            detail["why"] = "不在详情页"
            return "不在详情页", "", detail

    try:
        detail["videos"] = page.evaluate(ALL_VIDEO_JS) or []
    except Exception:
        detail["videos"] = []

    try:
        found = page.evaluate(FIND_VIDEO_JS) or {}
    except Exception:
        found = {}
    strong, weak = _normalize_find(found)
    # 去重保序
    uniq_strong, seen_s = [], set()
    for u in strong:
        if u not in seen_s:
            seen_s.add(u)
            uniq_strong.append(u)
    detail["strong"] = list(uniq_strong)
    detail["weak"] = list(weak)
    if seen is not None:
        # 把页面上所有 mp4 都交出去，等待超时时好说清楚到底看到了什么。
        seen.extend(uniq_strong)
        seen.extend(u for u in weak if u not in seen_s)

    if len(uniq_strong) >= 2:
        detail["why"] = "多个strong"
        _lg(f"⚠ 页面上有 {len(uniq_strong)} 个视频，判不出哪个是本段，继续等")
        return S_SUBMITTED, "", detail

    if len(uniq_strong) == 1:
        url = uniq_strong[0]
        # 唯一 strong 由 probe 显式标记，不再靠「我在自己页面上」去猜
        if is_own_video(url, post, on_post_page, unique_strong=True):
            detail["why"] = "unique_strong"
            return S_READY, url, detail
        # 唯一的那个是宣传片之类，当 0 个
        detail["why"] = "strong不过闸"
        return S_SUBMITTED, "", detail

    # 0 个 strong：绝不降级用 weak
    try:
        if page.evaluate(IS_GENERATING_JS):
            detail["why"] = "生成中"
            return S_SUBMITTED, "", detail
    except Exception:
        pass
    try:
        err = page.evaluate(IS_FAILED_JS) or ""
    except Exception:
        err = ""
    if err:
        detail["why"] = "页面报失败"
        return S_FAILED, norm_text(err)[:120], detail
    detail["why"] = "未知"
    return "未知", "", detail


# ═══════════════ 下载（闸3） ═══════════════

def url_matches_post(url, post):
    """下载地址必须含本段 postId。Grok 的资源地址形如
       https://assets.grok.com/users/<uid>/generated/<postId 前缀>…
       只要地址里出现的那一串是 postId 的前缀（或反之），就算对上。"""
    if not post:
        return True                      # 没锚点的段由上层单独处理
    u = url.lower()
    p = post.lower().replace("-", "")
    if post.lower() in u:
        return True
    for tok in re.findall(r"[0-9a-f][0-9a-f\-]{7,}", u):
        t = tok.replace("-", "")
        if len(t) >= 8 and (p.startswith(t) or t.startswith(p[:8])):
            return True
    return False


class NeedRedownload(Exception):
    """闸3 拒绝：地址既对不上 postId、也不是唯一 strong。该段 pending 重取，不算失败。"""


def download_one(ctx, row, dest, unique_strong=False):
    """用浏览器上下文发请求（自动带 cookie），落 .part 再改名，绝不半截文件。"""
    url = row["视频URL"]
    # 闸3。视频来源=="post页" 不再免检。两条依据任一成立即可放行：
    #   · url_matches_post(url, postId)
    #   · 调用方明确带了 unique_strong（闸1 判出的唯一 <video> 候选）
    unique = bool(unique_strong) or bool(row.get("unique_strong"))
    if not unique and not url_matches_post(url, row.get("postId")):
        raise NeedRedownload(
            f"下载地址不含本段 postId、也不是唯一 strong 候选，拒绝下载：{url[:90]}")
    r = ctx.request.get(url, timeout=180000)
    body = r.body()
    if not r.ok:
        raise Exception(f"HTTP {r.status} / {len(body)} 字节")
    if not is_real_mp4(body):
        # 53：海报/预览不是片子。不当成本段失败，让上层换地址再取。
        raise NeedRedownload(
            f"HTTP {r.status} / {len(body)} 字节 / 不是合法 mp4")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        old = md5_of(dest)
        if old == hashlib.md5(body).hexdigest():
            return len(body)
        junk = os.path.join(os.path.dirname(dest), "_被顶替")
        os.makedirs(junk, exist_ok=True)
        shutil.move(dest, os.path.join(
            junk, f"{datetime.now():%H%M%S}_{os.path.basename(dest)}"))
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dest)
    return len(body)


# ═══════════════ 主流程：一波一波地跑 ═══════════════

def connect(pw):
    br = pw.chromium.connect_over_cdp(CDP_URL, timeout=15000,
                                      no_defaults=True, is_local=True)
    if not br.contexts:
        raise RuntimeError("Chrome 里没有可用的上下文")
    return br, br.contexts[0]


def cmd_run(folder, wave=12, gen_max=4, v_from=None, v_to=None,
            gen_timeout=420, keep_tabs=False, force=False):
    """建账 → 一波一波：铺页 → 滚动提交 → 就地收货 → 关页 → 下一波 → 验收。

    为什么要一波一波、而且当波就下载完：
      标签页对象是身份的载体。当波开、当波提交、当波下载、当波关，
      身份从头到尾没离开过手里，也就不存在"事后认领"这一步 ——
      错乱是在那一步产生的。
      顺带还解决了另外两件事：Chrome 不会被上百个页面拖垮；
      同时在生成的不超过 gen_max 个，避开 Grok 静默丢弃多余请求的坑。
    """
    from playwright.sync_api import sync_playwright

    led = cmd_ledger(folder, v_from, v_to) or load_json(ledger_path(folder))
    if not led:
        return
    # 描述语和类型对不上就不许开跑 —— 这种提示词跑出来必穿帮，纯烧额度。
    conflict = getattr(_build_prompts, "冲突", [])
    if conflict and not force:
        log(f"✗ 拒绝开跑：{len(conflict)} 个编号的描述语有矛盾"
            f"（写法和类型对不上，或标签写了围绕但画面里没有那圈人）："
            f"{'、'.join(conflict[:10])}")
        log("  先用 描述语生成器.html 对这些图重跑描述语；")
        log("  真要带着矛盾硬跑，加 --强行。")
        return
    items = led["条目"]
    vdir = video_dir(folder)

    def keyorder(k):
        it = items[k]
        return (int(it["编号"][1:]), it["第几段"])

    def in_range(it):
        # --从/--到 以前只传给了建账，没用来过滤【这次跑哪些段】。
        # 上一版靠"清理失效键"误删别的段才碰巧只跑一个编号，那个 bug 修掉之后
        # 范围就失灵了 —— 0827 试跑 v25，结果从 v19 开始跑整批。
        num = int(it["编号"][1:])
        return (v_from is None or num >= v_from) and \
               (v_to is None or num <= v_to)

    todo = [k for k in sorted(items, key=keyorder)
            if in_range(items[k])
            and (items[k]["状态"] not in (S_DOWNLOADED, S_VERIFIED)
                 or not os.path.exists(
                     os.path.join(vdir, items[k]["输出文件名"])))]
    if v_from is not None or v_to is not None:
        log(f"  只做 v{v_from or 1:02d}~v{v_to or 99:02d} 这个范围")
    if not todo:
        log("· 台账里每一段都已落盘，直接进验收")
        return cmd_verify(folder)

    log(f"待办 {len(todo)} 段；每波 {wave} 段，同时生成不超过 {gen_max} 个")
    with sync_playwright() as pw:
        try:
            br, ctx = connect(pw)
        except Exception as e:
            log(f"✗ 连不上 Grok 的 Chrome（端口 {CDP_PORT}）：{str(e)[:90]}")
            log("  先用 观众席-Grok出片.bat / 0-登录Chrome.bat 把带调试端口的 Chrome 起起来")
            return
        try:
            for wi in range(0, len(todo), wave):
                chunk = todo[wi:wi + wave]
                log(f"── 第 {wi // wave + 1} 波：{chunk[0]} … {chunk[-1]}"
                    f"（{len(chunk)} 段）")
                _run_wave(ctx, led, folder, vdir, chunk, gen_max,
                          gen_timeout, keep_tabs)
        finally:
            try:
                br.close()
            except Exception:
                pass
    summary(led)
    cmd_verify(folder)


def _run_wave(ctx, led, folder, vdir, chunk, gen_max, gen_timeout, keep_tabs):
    items = led["条目"]
    pages = {}

    # ── 1+2) 即铺即提 + 就地收货 ──
    # 0828 之前是「整波先铺满 12 页，再回头挨个提交」。那中间隔着几十秒到
    # 几分钟，页面早被 Grok 重挂过（字被冲光），脚本却抱着一分钟前的校验
    # 结果照点不误。现在改成：腾出一个生成槽位才去铺一页，铺完立刻复检、
    # 立刻提交，窗口压到十几秒。
    pending = list(chunk)
    inflight = {}
    beat = {}          # 心跳：每段上次报进度时已等了多少秒
    reloaded = set()   # 已经自救刷新过的段，每段只刷一次
    t_start = time.time()
    while pending or inflight:
        while pending and len(inflight) < gen_max:
            key = pending.pop(0)
            row = items[key]
            pg = None
            for attempt in range(1, 4):
                try:
                    pg = prepare(ctx, row, folder)
                    break
                except Exception as e:
                    err = str(e)
                    if attempt == 3:
                        record(led, folder, key, 状态=S_FAILED,
                               备注=f"铺页失败：{err[:90]}")
                        log(f"  ✗ 铺页 {key} 放弃：{err[:70]}")
                    else:
                        log(f"  ↻ 铺页 {key} 第 {attempt} 次没成："
                            f"{err[:52]}，重来")
                        time.sleep(3 * attempt)
            if pg is None:
                continue
            pages[key] = pg
            record(led, folder, key, 状态=S_PREPARED,
                   targetId=target_id(ctx, pg), 备注="")
            log(f"  ✓ 铺页 {key} [{row['时长']}] {row['分段文案'][:34]}…")
            try:
                how = click_submit(pg, guard=lambda: ensure_intact(pg, row))
            except Exception as e:
                record(led, folder, key, 状态=S_FAILED,
                       备注=f"提交失败：{str(e)[:90]}")
                log(f"  ✗ 提交 {key}：{str(e)[:70]}")
                continue
            post, conv = capture_anchor(pg)            # 闸2
            if post:
                record(led, folder, key, 状态=S_SUBMITTED, postId=post,
                       会话Id=conv, 备注=f"提交方式 {how}")
                log(f"  ✓ 提交 {key} → post {post[:8]}…")
            else:
                record(led, folder, key, 状态=S_SUBMITTED,
                       备注=f"提交方式 {how}；未抓到 postId（闸3 将改用页面地址核验）")
                log(f"  ! 提交 {key}：没抓到 postId，继续盯生成")
            inflight[key] = time.time()

        for key in list(inflight):
            # 刚提交完的头 20 秒不探测。Grok 出片要一两分钟，这段时间页面上
            # 只可能是别的东西（宣传片、上一条的残留），探了只会误判。
            if time.time() - inflight[key] < 20:
                continue
            pg = pages[key]
            # 探测前切到前台。0827 实跑：一波两段都提交成功，只有最后被切到
            # 前台的那个下载成功，另一个死等到超时 —— Chrome 后台标签页里
            # Grok 根本不渲染 <video>，currentSrc 是空的，页面 HTML 里也没有
            # 那个 mp4 地址。所以人眼看着早生成完了，脚本却什么都读不到。
            try:
                pg.bring_to_front()
                pg.wait_for_timeout(500)
            except Exception:
                pass
            # 对一次地址：确保这一页就停在本段自己的 post 详情页上。
            # 在那儿读到的生成物，身份由地址保证，不必再猜资源 id 的命名规则。
            post_id = items[key].get("postId", "")
            conv = items[key].get("会话Id", "")
            # 锚点自愈：台账里记的锚点残缺（没有会话Id，本来就导不回去）时，
            # 从这一页当前的地址里取一个【完整的】锚点补上。
            # 只在残缺时才动，而且这一页始终是我们自己铺出来的那一页；
            # 认错了还有落盘后的首帧比对兜底（同组 0~4、异组 18~24）。
            if post_id and not conv:
                try:
                    u2 = pg.url or ""
                except Exception:
                    u2 = ""
                mp = POST_RE.search(u2)
                mc = re.search(r"conversation=([0-9a-fA-F\-]+)", u2)
                if mp and mc:
                    new_post, conv = mp.group(1), mc.group(1)
                    if new_post != post_id:
                        log(f"  ! {key} 锚点变了：{post_id[:8]}… → {new_post[:8]}…"
                            f"（提交时抓到的那个后来不作数了）")
                        record(led, folder, key, postId=new_post, 会话Id=conv,
                               备注=f"锚点自愈 {post_id[:8]}…→{new_post[:8]}…，"
                                    f"待首帧比对复核")
                        post_id = new_post
                    else:
                        record(led, folder, key, 会话Id=conv)
                        log(f"  + {key} 补到会话Id {conv[:8]}…")
            nav = goto_own_post(pg, post_id, conv)
            on_post = nav == "on_post"
            # 页面常停在原图那一格不自动切到视频（0827 截图实证）。
            # 停着的时候页面上一个 <video> 都没有，只能从 HTML 里捞地址；
            # 点开那一格，元素就出来了，也顺便确认片子真的出好了。
            nudged = nudge_to_video(pg) if on_post else "skip"
            seen = []
            st, info, *_ = probe(pg, post_id, seen, on_post)
            if st == S_READY:
                row = items[key]
                dest = os.path.join(vdir, row["输出文件名"])
                record(led, folder, key, 状态=S_READY, 视频URL=info,
                       视频来源=("post页" if on_post else "标签页"))
                items[key]["unique_strong"] = True
                try:
                    n = download_one(ctx, items[key], dest, unique_strong=True)   # 闸3
                    record(led, folder, key, 状态=S_DOWNLOADED,
                           已落盘路径=dest, 文件md5=md5_of(dest), 备注="")
                    log(f"  ⤓ {key} → {row['输出文件名']}（{n/1048576:.1f}MB）")
                    inflight.pop(key)
                except NeedRedownload as e:
                    record(led, folder, key, 状态=S_SUBMITTED,
                           备注=f"闸3拒绝、重取：{str(e)[:110]}")
                    log(f"  ↻ 下载 {key} 过不了闸，重取：{str(e)[:80]}")
                except Exception as e:
                    record(led, folder, key, 状态=S_FAILED,
                           备注=f"下载被拒/失败：{str(e)[:110]}")
                    log(f"  ✗ 下载 {key}：{str(e)[:80]}")
                    inflight.pop(key)
            elif st == S_FAILED:
                record(led, folder, key, 状态=S_FAILED, 备注=f"页面报错：{info}")
                log(f"  ✗ {key} 生成失败：{info[:60]}")
                inflight.pop(key)
            elif time.time() - inflight[key] > gen_timeout:
                why = "页面上一个生成物都没有 —— 多半被 Grok 静默丢弃"
                if seen:
                    # 完整打出来，别截断：截断了就看不出到底是命名规则不对，
                    # 还是根本是别的东西。
                    why = ("页面上这些 mp4 都不算本段的：\n      "
                           + "\n      ".join(seen[:4]))
                record(led, folder, key, 状态=S_LOST,
                       备注=f"等了 {gen_timeout} 秒仍无本段视频 —— {why}")
                log(f"  ✗ {key} 超时（{why}）")
                inflight.pop(key)
            else:
                # 心跳：默认要等 900 秒，一行不打的话跟死机没区别。
                waited = int(time.time() - inflight[key])
                if waited - beat.get(key, 0) >= 60:
                    beat[key] = waited
                    tail = ""
                    try:
                        mnow = POST_RE.search(pg.url or "")
                    except Exception:
                        mnow = None
                    if mnow and post_id and mnow.group(1) != post_id:
                        tail = (f"；⚠ 这一页现在停在 post/{mnow.group(1)[:8]}…，"
                                f"不是本段的 {post_id[:8]}… —— "
                                f"是不是手动点过这个 Chrome 窗口？")
                    elif seen:
                        tail = f"；页面有 mp4 但不属于本段 → {seen[0][:60]}"
                    elif nudged == "nothing":
                        tail = "；页面上还没有出片的缩略图"
                    elif str(nudged).startswith("clicked"):
                        tail = f"；已点开缩略图 {nudged.split(':', 1)[1]}"
                    log(f"  … {key} 已等 {waited}s（{st}）{tail}")
                # 自救：3 分钟还读不到本段视频就刷一次页面（每段只刷一次）。
                if waited > 180 and key not in reloaded:
                    reloaded.add(key)
                    log(f"  ↻ {key} 等太久，刷新一次页面再看")
                    try:
                        pg.reload(wait_until="domcontentloaded", timeout=60000)
                    except Exception:
                        pass
        if inflight:
            time.sleep(4)
        if time.time() - t_start > gen_timeout * 4:
            log("  ! 本波整体超时，剩余段留给下次补跑")
            break

    # ── 3) 关页：只关成功落盘的 ──
    # 0827 实跑踩过：v25-2of2 其实真的在生成，页一关，既没法接着等，
    # 人也没法去看一眼到底出了什么事。失败/超时的一律留着。
    if not keep_tabs:
        kept = 0
        for key, pg in pages.items():
            if items[key]["状态"] == S_DOWNLOADED:
                try:
                    pg.close()
                except Exception:
                    pass
            else:
                kept += 1
        if kept:
            log(f"  · 留下 {kept} 个没成功的标签页供查看，别手动关 —— "
                f"再跑一次「收货补缺」还能从它们身上把片子取回来")


# ═══════════════ 单独收货（补下已经生成好的） ═══════════════

SAVED_URL = "https://grok.com/library"

# 从仓库页面（https://grok.com/library）上把每一项抠出来：postId / conversation / 视频地址 / 缩略图。
# 新版 Grok 仓库直接渲染 <video src="https://assets.grok.com/users/.../generated/<postId>/generated_video.mp4#t=0.1">，
# 外层未必包着 <a href="/imagine/post/...">；所以同时扫 <video> 直链与 <a> 详情页链接。
GALLERY_JS = r"""() => {
  const items = [], seen = new Set();
  const VID_RE = /\/generated\/([0-9a-fA-F][0-9a-fA-F-]{7,})\/generated_video\.mp4/;
  const POST_RE = /\/imagine\/post\/([0-9a-fA-F][0-9a-fA-F-]{7,})(?:\?conversation=([0-9a-fA-F][0-9a-fA-F-]{7,}))?/;

  // 1) 直接扫页面上的 <video>（https://grok.com/library 主结构）
  for (const v of document.querySelectorAll('video')) {
    const raw = v.currentSrc || v.src || '';
    const clean = raw.split('#')[0];
    if (!clean || clean.startsWith('blob:')) continue;
    const mv = VID_RE.exec(clean);
    let post = mv ? mv[1] : '';
    let conv = '';
    const a = v.closest && v.closest('a[href*="/imagine/post/"]');
    if (a) {
      const mp = POST_RE.exec(a.getAttribute('href') || a.href || '');
      if (mp) {
        if (!post) post = mp[1];
        conv = mp[2] || '';
      }
    }
    if (!post || seen.has(post)) continue;
    seen.add(post);
    const imgs = [];
    if (v.poster) imgs.push(v.poster);
    items.push({post, conv, vids: [clean], imgs});
  }

  // 2) 兼容带 <a href="/imagine/post/..."> 的卡片结构
  for (const a of document.querySelectorAll('a[href*="/imagine/post/"]')) {
    const m = POST_RE.exec(a.getAttribute('href') || a.href || '');
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    let el = a, vids = new Set(), imgs = new Set();
    for (let i = 0; i < 5 && el; i++, el = el.parentElement) {
      el.querySelectorAll('video').forEach(v => {
        const s = (v.currentSrc || v.src || '').split('#')[0];
        if (s && !s.startsWith('blob:')) vids.add(s);
        v.querySelectorAll('source[src]').forEach(so => vids.add((so.src || '').split('#')[0]));
      });
      el.querySelectorAll('img[src]').forEach(im => imgs.add(im.src));
      if (vids.size) break;
    }
    items.push({post: m[1], conv: m[2] || '',
                vids: [...vids], imgs: [...imgs].slice(0, 2)});
  }

  // 3) 退路：卡片结构认不出来时，把整页里的 generated_video.mp4 和 post 链接捞干净
  if (!items.length) {
    const html = document.documentElement.innerHTML || '';
    for (const m of html.matchAll(/https:\/\/assets\.grok\.com\/[^\s"'<>]+?\/generated\/([0-9a-fA-F-]{36})\/generated_video\.mp4/g)) {
      if (seen.has(m[1])) continue;
      seen.add(m[1]);
      items.push({post: m[1], conv: '', vids: [m[0]], imgs: []});
    }
    for (const m of html.matchAll(/\/imagine\/post\/([0-9a-fA-F][0-9a-fA-F-]{7,})(?:\?conversation=([0-9a-fA-F][0-9a-fA-F-]{7,}))?/g)) {
      if (seen.has(m[1])) continue;
      seen.add(m[1]);
      items.push({post: m[1], conv: m[2] || '', vids: [], imgs: []});
    }
  }
  return items;
}"""


def scan_gallery(ctx, rounds=12, log=log):
    """打开仓库，滚到底，把每一项的 postId / conversation / 视频地址抠出来。

    仓库是无限滚动的，所以要滚几轮；连续两轮没新增就停，不做无谓的等待。
    返回 {postId: {"conv":…, "vids":[…], "imgs":[…]}}
    """
    pg = ctx.new_page()
    out = {}
    try:
        pg.goto(SAVED_URL, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(3500)
        dry = 0
        for r in range(rounds):
            try:
                items = pg.evaluate(GALLERY_JS) or []
            except Exception as e:
                log(f"  ! 读仓库失败：{str(e)[:70]}")
                break
            before = len(out)
            for it in items:
                p = it.get("post")
                if not p:
                    continue
                cur = out.setdefault(p, {"conv": "", "vids": [], "imgs": []})
                if it.get("conv"):
                    cur["conv"] = it["conv"]
                for v in it.get("vids") or []:
                    if v not in cur["vids"]:
                        cur["vids"].append(v)
                for im in it.get("imgs") or []:
                    if im not in cur["imgs"]:
                        cur["imgs"].append(im)
            grew = len(out) - before
            log(f"  · 仓库第 {r + 1} 轮：累计 {len(out)} 项（新增 {grew}）")
            dry = dry + 1 if grew == 0 else 0
            if dry >= 2:
                break
            try:
                pg.evaluate("""() => {
                  window.scrollBy(0, 3500);
                  for (const el of document.querySelectorAll('main, [class*="overflow"], div')) {
                    if (el.scrollHeight > el.clientHeight + 100) {
                      el.scrollTop += 3500;
                    }
                  }
                }""")
                pg.mouse.move(600, 500)
                pg.mouse.wheel(0, 4000)
            except Exception:
                pass
            pg.wait_for_timeout(2200)
    finally:
        try:
            pg.close()
        except Exception:
            pass
    return out


def cmd_gallery(folder):
    """扫仓库，落一份索引，并把台账里缺的 会话Id 按 postId 补齐。

    单独跑也有用：能一眼看出台账里哪些 postId 在仓库里【根本不存在】——
    那就是被 Grok 静默吞掉的，重发就是了，不用再干等。
    """
    from playwright.sync_api import sync_playwright
    led = load_json(ledger_path(folder))
    if not led:
        log("✗ 还没建账")
        return
    items = led["条目"]
    with sync_playwright() as pw:
        try:
            br, ctx = connect(pw)
        except Exception as e:
            log(f"✗ 连不上 Chrome（端口 {CDP_PORT}）：{str(e)[:90]}")
            return
        try:
            gal = scan_gallery(ctx)
        finally:
            try:
                br.close()
            except Exception:
                pass
    save_json(os.path.join(video_dir(folder), "_仓库索引.json"), gal)
    log(f"✓ 仓库 {len(gal)} 项 → {os.path.join(video_dir(folder), '_仓库索引.json')}")

    filled = hit = miss = 0
    missing = []
    for key, it in items.items():
        p = it.get("postId")
        if not p:
            continue
        g = gal.get(p)
        if not g:
            if it["状态"] not in (S_DOWNLOADED, S_VERIFIED):
                miss += 1
                missing.append(key)
            continue
        hit += 1
        if g.get("conv") and not it.get("会话Id"):
            it["会话Id"] = g["conv"]
            filled += 1
    save_json(ledger_path(folder), led)
    log(f"  台账里有 postId 的段：仓库里找到 {hit} 个，补回会话Id {filled} 个")
    if missing:
        log(f"  ⚠ 这 {len(missing)} 段提交过、但仓库里没有 —— "
            f"多半被 Grok 静默吞了，重发即可：{'、'.join(missing[:10])}")
    return gal


def cmd_harvest(folder, use_gallery=True):
    """凭台账里的 postId 直接开详情页取视频 —— 不依赖当时那个标签页还在。

    这是补救通道：主流程崩了、Chrome 被关了、机器重启了都能接着下。
    因为身份锚点（postId）在提交那一刻就已经落盘了。
    """
    from playwright.sync_api import sync_playwright
    led = load_json(ledger_path(folder))
    if not led:
        log("✗ 还没建账")
        return
    items = led["条目"]
    vdir = video_dir(folder)
    # 先扫一遍仓库：一个页面上摆着全部成品，比一段一段去开详情页结实得多，
    # 顺带还能把缺失的 会话Id 按 postId 补回来（那正是导不回详情页的原因）。
    if use_gallery:
        log("先扫一遍仓库 /imagine/saved …")
        try:
            cmd_gallery(folder)
            led = load_json(ledger_path(folder)) or led
            items = led["条目"]
        except Exception as e:
            log(f"  ! 扫仓库失败（{str(e)[:70]}），改走逐段详情页")
    gal = load_json(os.path.join(vdir, "_仓库索引.json"), {}) or {}
    todo = [k for k in sorted(items) if items[k]["状态"] not in
            (S_DOWNLOADED, S_VERIFIED)
            or not os.path.exists(os.path.join(vdir, items[k]["输出文件名"]))]
    # 只有 postId 还不够：没有会话Id 就拼不出能打开的详情页地址。
    todo = [k for k in todo if items[k].get("postId")
            and items[k].get("会话Id")]
    noanchor = [k for k in sorted(items)
                if not (items[k].get("postId") and items[k].get("会话Id"))
                and items[k]["状态"] not in (S_DOWNLOADED, S_VERIFIED)]
    if noanchor:
        log(f"! {len(noanchor)} 段锚点不全（缺 postId 或会话Id），收不了，"
            f"只能重发：{'、'.join(noanchor[:8])}"
            f"{'…' if len(noanchor) > 8 else ''}")
    if not todo:
        log("· 没有可凭锚点补下的段")
        return
    log(f"凭 postId 补下 {len(todo)} 段 → {vdir}")

    # 第一轮：仓库里直接就有视频地址的，不必开详情页。
    # 连接键是 postId —— 提交那一刻锚定的那个，精确匹配，不是推断。
    done_by_gal = []
    for key in list(todo):
        row = items[key]
        g = gal.get(row.get("postId") or "")
        vids = [v for v in (g or {}).get("vids", [])
                if is_own_video(v, row.get("postId") or "")]
        if not vids:
            continue
        dest = os.path.join(vdir, row["输出文件名"])
        try:
            row["视频URL"] = vids[0]
            row["视频来源"] = "仓库"
            from playwright.sync_api import sync_playwright as _sp
            with _sp() as _pw:
                _br, _ctx = connect(_pw)
                try:
                    nbytes = download_one(_ctx, row, dest)
                finally:
                    try:
                        _br.close()
                    except Exception:
                        pass
            record(led, folder, key, 状态=S_DOWNLOADED, 已落盘路径=dest,
                   文件md5=md5_of(dest), 视频URL=vids[0], 视频来源="仓库", 备注="")
            log(f"  ⤓ {key} → {row['输出文件名']}（{nbytes/1048576:.1f}MB，来自仓库）")
            todo.remove(key)
            done_by_gal.append(key)
        except Exception as e:
            log(f"  ! {key} 从仓库下载失败，改走详情页：{str(e)[:70]}")
    if done_by_gal:
        log(f"  仓库直接取回 {len(done_by_gal)} 段")
    if not todo:
        summary(led)
        return
    with sync_playwright() as pw:
        br, ctx = connect(pw)
        pg = ctx.new_page()
        try:
            for key in todo:
                row = items[key]
                try:
                    post = row["postId"]
                    conv = row.get("会话Id", "")
                    if not conv:
                        log(f"  ! {key} 没有会话Id，裸地址打不开 "
                            f"（Grok 会回 Post not found），只能重发")
                        record(led, folder, key, 状态=S_LOST,
                               备注="缺会话Id，导不回详情页，需重发")
                        continue
                    pg.goto(post_url(post, conv),
                            wait_until="domcontentloaded", timeout=60000)
                    try:
                        pg.bring_to_front()
                    except Exception:
                        pass
                    on_post = post.lower() in (pg.url or "").lower()
                    if not on_post:
                        log(f"  ! {key} 导过去之后地址不对：{(pg.url or '')[:90]}")
                    seen, nudged = [], ""
                    for _ in range(30):
                        nudged = nudge_to_video(pg)
                        seen = []
                        st, info, *_ = probe(pg, row.get("postId", ""), seen, on_post)
                        if st in (S_READY, S_FAILED):
                            break
                        pg.wait_for_timeout(3000)
                    if st != S_READY:
                        # 「没看到任何 mp4」和「看到了但不认」是完全不同的两种毛病，
                        # 一句「还没好」把它们混成一团，等于什么都没说。
                        detail = ("页面上一个 mp4 都没有" if not seen
                                  else "看到这些 mp4 但都不算本段的：" + "、".join(seen[:3]))
                        record(led, folder, key,
                               备注=f"补下时状态={st}；{detail[:200]}")
                        log(f"  − {key} 还没好（{st}）")
                        log(f"      地址：{(pg.url or '')[:100]}")
                        log(f"      缩略图：{nudged}")
                        log(f"      {detail[:300]}")
                        continue
                    record(led, folder, key, 状态=S_READY, 视频URL=info,
                           视频来源=("post页" if on_post else "标签页"))
                    dest = os.path.join(vdir, row["输出文件名"])
                    row["unique_strong"] = True
                    n = download_one(ctx, row, dest, unique_strong=True)
                    record(led, folder, key, 状态=S_DOWNLOADED, 已落盘路径=dest,
                           文件md5=md5_of(dest), 备注="")
                    log(f"  ⤓ {key} → {row['输出文件名']}（{n/1048576:.1f}MB）")
                except Exception as e:
                    record(led, folder, key, 备注=f"补下失败：{str(e)[:100]}")
                    log(f"  ✗ {key}：{str(e)[:80]}")
        finally:
            try:
                br.close()
            except Exception:
                pass
    summary(led)


# ═══════════════ 验收（闸4） ═══════════════

def cmd_verify(folder, full=False, phash_max=12):
    led = load_json(ledger_path(folder))
    if not led:
        log("✗ 还没建账")
        return False
    items = led["条目"]
    vdir = video_dir(folder)
    problems = []
    log(f"验收 {len(items)} 段 @ {vdir}")

    # 1) 齐不齐
    存在 = {}
    for key, it in items.items():
        p = os.path.join(vdir, it["输出文件名"])
        if os.path.exists(p) and os.path.getsize(p) > 200000:
            存在[key] = p
        else:
            problems.append((key, "缺文件", it["输出文件名"]))

    # 2) md5 唯一（抓"两个名字其实是同一个视频"）
    seen = {}
    for key, p in 存在.items():
        h = md5_of(p)
        items[key]["文件md5"] = h
        if h in seen:
            problems.append((key, "内容重复", f"与 {seen[h]} 是同一个文件"))
        else:
            seen[h] = key

    # 3) 时长对不对档
    for key, p in 存在.items():
        want = int(re.sub(r"\D", "", items[key]["时长"]) or 0)
        got = mp4_duration(p)
        if got and want and abs(got - want) > 2.0:
            problems.append((key, "时长不符",
                             f"档位 {want}s，实际 {got:.1f}s"))

    # 4) 首帧必须最像本段的源图（抓张冠李戴到别的组）
    try:
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401
        源图指纹 = {}
        for key, it in items.items():
            g = it["编号"]
            if g in 源图指纹:
                continue
            try:
                源图指纹[g] = phash_image(resolve_image(folder, it))
            except Exception:
                pass
        if 源图指纹:
            # ── 并列容差：源图重复时首帧比对【天生分不出来】 ──
            # 0829 实测：10 行只用了 5 张不同的图 —— v03=v07=v09、v04=v06=v08、
            # v05=v10 三组逐字节相同（md5 一样）。首帧到这几张图的距离当然一模一样，
            # 老写法只看 sorted 的第一名，同分时按 v 号先后取最小的那个，
            # 于是 v06/v07/v08/v09/v10 十一段全被报成「首帧对不上源图」——
            # 详情里两个距离都写着「距0」「距2」，自己就说明了是平局。
            # 所以判据改成【别人明显更像才算错】：差距要大于容差。
            #
            # 会不会漏掉真的张冠李戴？源图相同的两个编号之间，首帧本来就没有
            # 任何信息可以区分（画面一样），只有台词不一样 —— 那是 --强检
            # 转写比对该干的活，不受源图重复影响。这里硬报只会天天喊狼来了。
            TIE = 2
            groups, seen_g = [], set()
            for g in sorted(源图指纹):
                if g in seen_g:
                    continue
                same = [g2 for g2 in sorted(源图指纹)
                        if g2 != g and hamming(源图指纹[g], 源图指纹[g2]) <= TIE]
                if same:
                    groups.append([g] + same)
                    seen_g.update([g] + same)
            if groups:
                log("  · 源图重复（这些编号之间不做首帧判定，靠 --强检 的转写比对区分）："
                    + "；".join("=".join(x) for x in groups))
            for key, p in sorted(存在.items()):
                fp = phash_first_frame(p)
                if fp is None:
                    continue
                d = sorted((hamming(fp, v), g) for g, v in 源图指纹.items())
                mine = next((x for x in d if x[1] == items[key]["编号"]), None)
                if mine is None:
                    continue
                if d[0][0] < mine[0] - TIE:
                    problems.append(
                        (key, "首帧对不上源图",
                         f"最像 {d[0][1]}(距{d[0][0]})，"
                         f"本段是 {items[key]['编号']}(距{mine[0]})"))
                elif mine[0] > phash_max:
                    problems.append((key, "首帧偏离源图",
                                     f"距离 {mine[0]} > {phash_max}"))
        else:
            log("  · 找不到源图，跳过首帧比对")
    except ImportError:
        log("  · 缺 numpy/PIL，跳过首帧比对")

    # 4.5) 描述语写法和类型对不对得上（不影响已下载的文件，但会影响下一次重发）
    try:
        import video_types as VT
        for lab, why in VT.audit(folder, log=lambda *a: None):
            problems.append((lab, "描述语与类型不符", why[:80]))
    except Exception:
        pass

    # 5) 下载地址得含本段 postId
    for key, it in items.items():
        if it.get("视频URL") and it.get("postId") \
                and not url_matches_post(it["视频URL"], it["postId"]):
            problems.append((key, "地址与锚点不符", it["视频URL"][:70]))

    # 6) 强检：转写 × 原文，全局 1:1 指派
    if full:
        problems += verify_by_speech(folder, items, 存在)

    for key, p in 存在.items():
        if not any(x[0] == key for x in problems):
            items[key]["状态"] = S_VERIFIED
    save_json(ledger_path(folder), led)

    rpt = os.path.join(folder, "验收报告.md")
    lines = [f"# Grok 验收报告　{datetime.now():%Y-%m-%d %H:%M}", "",
             f"- 文件夹：`{folder}`",
             f"- 台账 {len(items)} 段，落盘 {len(存在)} 段",
             f"- 强检：{'已跑' if full else '未跑（加 --强检 开启）'}", ""]
    if problems:
        lines += [f"## ⚠ {len(problems)} 处不合格", "",
                  "| 段号 | 段 | 问题 | 详情 |", "|---|---|---|---|"]
        _m = sn_map(items)
        _n = len(items)
        for k, w, d in sorted(problems, key=lambda x: _m.get(x[0], 0)):
            lines.append(f"| {fmt_sn(_m.get(k, 0), _n)} | {k} | {w} | {d} |")
        缺 = [_m[k] for k, w, _d in problems if w == "缺文件" and k in _m]
        if 缺:
            lines += ["", f"**缺文件：{sn_ranges(缺, _n)}**"]
    else:
        lines.append("## ✅ 全部合格，可以进拼接")
    with open(rpt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    if problems:
        log(f"✗ 验收不合格 {len(problems)} 处 → {rpt}")
        _m = sn_map(items)
        _n = len(items)
        _缺 = [_m[k] for k, w, _d in problems if w == "缺文件" and k in _m]
        if _缺:
            log(f"    缺文件：{sn_ranges(_缺, _n)}")
        for k, w, d in problems[:15]:
            log(f"    {fmt_sn(_m.get(k, 0), _n):<5s} {k:<14s} {w:<12s} {d}")
        if len(problems) > 15:
            log(f"    …… 其余 {len(problems) - 15} 处见报告")
    else:
        log(f"✓ 验收全部合格（{len(存在)} 段）→ {rpt}")
    return not problems


def _asr_model():
    cfg = load_json(os.path.join(SCRIPT_DIR, "flow_config.json"), {}) or {}
    return cfg.get("asr_model", "")


def transcribe(paths):
    """转写（带缓存）。用 AutoSub 已经装好的 faster-whisper。"""
    from faster_whisper import WhisperModel
    mp = _asr_model()
    if not os.path.isdir(mp):
        raise RuntimeError(f"找不到语音模型 {mp}；用 启动-Flow自检.bat 那套环境跑，"
                           f"或改 flow_config.json 的 asr_model")
    try:
        model = WhisperModel(mp, device="cuda", compute_type="float16")
    except Exception:
        model = WhisperModel(mp, device="cpu", compute_type="int8")
    ff = find_ffmpeg() or "ffmpeg"
    out = {}
    wav = os.path.join(tempfile.gettempdir(), "_gp_asr.wav")
    for i, (key, p) in enumerate(sorted(paths.items()), 1):
        try:
            subprocess.run([ff, "-y", "-v", "error", "-i", p, "-vn",
                            "-ac", "1", "-ar", "16000", wav],
                           capture_output=True, timeout=120)
            segs, _ = model.transcribe(wav, language="en", beam_size=5,
                                       vad_filter=True)
            out[key] = " ".join(s.text.strip() for s in segs).strip()
        except Exception as e:
            out[key] = ""
            log(f"  ! {key} 转写失败：{str(e)[:60]}")
        if i % 20 == 0:
            log(f"  · 转写 {i}/{len(paths)}")
    try:
        os.remove(wav)
    except OSError:
        pass
    return out


def _words(s):
    return re.findall(r"[a-z0-9]+", str(s).lower())


def _sim(a, b):
    """词袋 F1 + 二元组加权 —— 对识别错字宽容，对"整段不是这段"敏感"""
    from collections import Counter
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0

    def f1(x, y):
        cx, cy = Counter(x), Counter(y)
        inter = sum((cx & cy).values())
        if not inter:
            return 0.0
        p, r = inter / len(x), inter / len(y)
        return 2 * p * r / (p + r)
    bg = lambda w: [f"{w[i]}_{w[i+1]}" for i in range(len(w) - 1)]
    return 0.5 * f1(wa, wb) + 0.5 * f1(bg(wa), bg(wb))


def verify_by_speech(folder, items, 存在):
    """把「视频里在说什么」和「台账说它该说什么」做全局 1:1 最优指派。

    为什么要全局指派而不是逐个打分：逐个打分只能说"像不像"，
    全局指派能直接回答"这 188 个文件的最优归属整体是什么" ——
    同组内 a/b 互换这种错，只有全局比对才抓得出来。
    """
    cache_p = os.path.join(video_dir(folder), "_转写缓存.json")
    cache = load_json(cache_p, {}) or {}
    need = {k: p for k, p in 存在.items()
            if cache.get(items[k]["输出文件名"], {}).get("md5")
            != items[k].get("文件md5")}
    if need:
        log(f"  · 强检：需转写 {len(need)} 段（共 {len(存在)}）")
        got = transcribe(need)
        for k, t in got.items():
            cache[items[k]["输出文件名"]] = {"md5": items[k].get("文件md5"),
                                             "text": t}
        save_json(cache_p, cache)
    keys = sorted(存在)
    hyp = [cache.get(items[k]["输出文件名"], {}).get("text", "") for k in keys]
    ref = [items[k]["分段文案"] for k in keys]
    n = len(keys)
    import numpy as np
    M = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            M[i, j] = _sim(hyp[i], ref[j])
    try:
        from scipy.optimize import linear_sum_assignment
        ri, ci = linear_sum_assignment(-M)
    except ImportError:
        ri = list(range(n))
        ci = _greedy_assign(M)
    bad = []
    for i, j in zip(ri, ci):
        if i == j:
            continue
        if M[i, j] - M[i, i] < 0.10:      # 差得不明显就不吵
            continue
        bad.append((keys[i], "转写指向别的段",
                    f"更像 {keys[j]}（{M[i, j]:.2f} vs 自己 {M[i, i]:.2f}）"))
    return bad


def _greedy_assign(M):
    import numpy as np
    n = M.shape[0]
    ci = [-1] * n
    used = set()
    for i in np.argsort(-M.max(axis=1)):
        order = np.argsort(-M[i])
        for j in order:
            if j not in used:
                ci[i] = int(j)
                used.add(int(j))
                break
    return ci


# ═══════════════ 认领：给已经错乱的一批视频翻案 ═══════════════

def cmd_reclaim(folder, apply=False, dup_dir="_重复待重发"):
    """对一批「已经下载但名字不可信」的视频，重新判定每个文件真正是哪一段。

    这是救火通道 —— 0826 那批 188 个文件就是靠它翻的案（改对 46 个，
    另揪出 3 段其实压根没生成，只是被同一段的重复渲染顶了名）。

    判据两条，互相独立、互为佐证：
      · 首帧 pHash 对源图 vNN —— 定「哪一组」。同一组的两段都是从同一张图起画的，
        所以这一条定组极准（0826 实测：对的时候距离 0~4，错的时候 18~24），
        但定不了组内的 a/b。
      · 转写对分段英文原文 —— 定「组内第几段」。语音是视频自己带的，改不了名也伪造不了。
    两条打分加权后做全局最优指派；再检查有没有「两个文件抢同一个槽位」——
    有，就说明那一段被重复渲染了，另一段其实是空的，必须重发，绝不能靠改名蒙混过去。

    默认只出报告不动文件；确认无误后加 --执行 才真改。
    """
    led = load_json(ledger_path(folder))
    vdir = video_dir(folder)
    if led:
        slots = [{"key": k, "grp": v["编号"], "name": v["输出文件名"],
                  "text": v["分段文案"], "img": v["图片"], "dur": v["时长"]}
                 for k, v in led["条目"].items()]
    else:
        log("· 没有台账，直接从 vNN.txt + vNN.图片 现推分段")
        slots = []
        for label, img, segs in _collect(folder):
            num = int(label[1:])
            for i, s in enumerate(segs, 1):
                slots.append({"key": f"{label}-{i}of{len(segs)}", "grp": label,
                              "name": out_name(num, i, len(segs)),
                              "text": norm_text(s), "img": img,
                              "dur": _pick_duration(s)})
    files = sorted(f for f in os.listdir(vdir)
                   if f.lower().endswith(".mp4")
                   and os.path.getsize(os.path.join(vdir, f)) > 200000)
    log(f"认领：{len(files)} 个文件 vs 台账 {len(slots)} 段")
    if not files or not slots:
        return

    grp_img = {}
    for s in slots:
        if s["grp"] in grp_img:
            continue
        try:
            grp_img[s["grp"]] = phash_image(
                resolve_image(folder, {"图片": s["img"], "编号": s["grp"]}))
        except Exception:
            pass
    frames = {}
    for f in files:
        frames[f] = phash_first_frame(os.path.join(vdir, f))

    hyp = transcribe({f: os.path.join(vdir, f) for f in files})

    best = {}
    for f in files:
        sc = []
        for s in slots:
            t = _sim(hyp.get(f, ""), s["text"])
            fr, gi = frames.get(f), grp_img.get(s["grp"])
            p = 0.5 if (fr is None or gi is None) \
                else max(0.0, 1.0 - hamming(fr, gi) / 16.0)
            sc.append((0.55 * t + 0.45 * p, s["name"], t, p, s["key"]))
        sc.sort(reverse=True)
        best[f] = sc[:2]

    from collections import defaultdict
    claim = defaultdict(list)
    for f in files:
        claim[best[f][0][1]].append(f)
    dup = {k: v for k, v in claim.items() if len(v) > 1}
    missing = [s["name"] for s in slots if s["name"] not in claim]

    # 抢同一个槽位时，分最高的那个留下，其余进 _重复待重发
    keep, quarantine = {}, []
    for name, fs in claim.items():
        fs = sorted(fs, key=lambda x: -best[x][0][0])
        keep[fs[0]] = name
        quarantine += fs[1:]

    lines = [f"# Grok 认领对照表　{datetime.now():%Y-%m-%d %H:%M}", "",
             f"- 文件夹：`{vdir}`",
             f"- 文件 {len(files)} 个，台账 {len(slots)} 段",
             f"- 需要改名 {sum(1 for f, n in keep.items() if f != n)} 个",
             f"- 重复渲染需隔离 {len(quarantine)} 个",
             f"- 无人认领、必须重发 {len(missing)} 段", ""]
    if missing:
        lines += ["## ⚠ 这些段其实没生成，必须重发", "",
                  "、".join(missing), "",
                  "（它们的名字之前被同一段的重复渲染占用了，改名救不回来）", ""]
    lines += ["## 对照表", "",
              "| 现名 | 判定为 | 动作 | 文本相似 | 首帧吻合 | 裕度 |",
              "|---|---|---|---|---|---|"]
    for f in files:
        b, s2 = best[f][0], best[f][1]
        act = ("隔离" if f in quarantine
               else ("保持" if keep.get(f) == f else "改名"))
        tgt = f"{b[1]}（重复)" if f in quarantine else keep.get(f, b[1])
        lines.append(f"| {f} | {tgt} | {act} | {b[2]:.2f} | {b[3]:.2f} "
                     f"| {b[0] - s2[0]:.3f} |")
    rpt = os.path.join(folder, "认领对照表.md")
    with open(rpt, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    log(f"✓ 对照表 → {rpt}")
    for name, fs in sorted(dup.items()):
        log(f"  ⚠ {name} 被 {len(fs)} 个文件同时指认：{'、'.join(fs)}")
    if missing:
        log(f"  ⚠ 必须重发 {len(missing)} 段：{'、'.join(missing)}")

    if not apply:
        log("· 只出了报告，没动文件。核对无误后加 --执行 再跑一次。")
        return

    # 改名前先把每个文件的指纹记下来，改完逐个核对，防止改串
    pre = {f: md5_of(os.path.join(vdir, f)) for f in files}
    moves = [(f, n) for f, n in keep.items() if f != n]
    for f, n in moves:                       # 两阶段，避开 A→B、B→A 的碰撞
        os.rename(os.path.join(vdir, f), os.path.join(vdir, "__tmp__" + n))
    for f, n in moves:
        os.rename(os.path.join(vdir, "__tmp__" + n), os.path.join(vdir, n))
    if quarantine:
        qd = os.path.join(vdir, dup_dir)
        os.makedirs(qd, exist_ok=True)
        for f in quarantine:
            shutil.move(os.path.join(vdir, f), os.path.join(qd, f))
    bad = 0
    for f, n in keep.items():
        if md5_of(os.path.join(vdir, n)) != pre[f]:
            bad += 1
            log(f"  ✗ 改名后内容对不上：{f} → {n}")
    for f in quarantine:
        if md5_of(os.path.join(vdir, dup_dir, f)) != pre[f]:
            bad += 1
    log(f"✓ 改名 {len(moves)}，隔离 {len(quarantine)}；"
        f"内容逐个核对{'全部一致' if not bad else f'有 {bad} 处不一致！'}")
    save_json(os.path.join(vdir, "_认领记录.json"),
              {"时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "改名": moves, "隔离": quarantine, "必须重发": missing,
               "改名前md5": pre})


# ═══════════════ 补漏清单 ═══════════════

def cmd_segmap(folder, write=True):
    """段号表：整批 s01…sNN 的进度格子，缺哪几段一眼看到。

    长文案那边靠 manifest 的连号校验做这件事；Normal 这边段号原来是【组内】的
    （v01-3of12 / 01c.mp4），一批 43 段分在 10 个编号里，想知道还差哪几段
    得把 10 个编号逐个数。这里把全局号补上，一行一个编号，一个格子一段。
    """
    led = load_json(ledger_path(folder))
    if not led:
        log("✗ 还没建账")
        return None
    items = led["条目"]
    if not items:
        log("台账是空的")
        return None
    head, rows, miss = sn_grid(items)
    n = len(items)
    log(f"段号表 {os.path.basename(folder.rstrip(chr(92) + '/'))}　{head}")
    for r in rows:
        log(r)
    log("  图例：✓已下载　⏳已提交/已生成　✗失败或锚点丢失　·还没开始")
    log(f"  未就绪：{sn_ranges(miss, n)}")
    if write:
        m = sn_map(items)
        L = [f"# 段号表　{os.path.basename(folder.rstrip(chr(92) + '/'))}　"
             f"{datetime.now():%Y-%m-%d %H:%M}", "",
             head, "",
             "```", *[r.strip('\n') for r in rows], "```", "",
             "图例：✓已下载　⏳已提交/已生成　✗失败或锚点丢失　·还没开始", "",
             f"**未就绪：{sn_ranges(miss, n)}**", "",
             "| 段号 | 台账键 | 输出文件名 | 状态 |", "|---|---|---|---|"]
        for k in sn_order(items):
            it = items[k]
            L.append(f"| {fmt_sn(m[k], n)} | {k} | `{it['输出文件名']}` "
                     f"| {it['状态']} |")
        p = os.path.join(folder, "段号表.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(L) + "\n")
        log(f"✓ → {p}")
    return miss


def cmd_missing(folder):
    led = load_json(ledger_path(folder))
    if not led:
        log("✗ 还没建账")
        return
    bad = [(k, v) for k, v in led["条目"].items()
           if v["状态"] in (S_LOST, S_FAILED, S_PENDING, S_PREPARED)]
    m = sn_map(led["条目"])
    n = len(led["条目"])
    head, rows, miss = sn_grid(led["条目"])
    p = os.path.join(folder, "待重发.md")
    L = [f"# Grok 待重发　{datetime.now():%Y-%m-%d %H:%M}", "",
         f"来源：`{folder}`", "", head, "",
         "```", *rows, "```", "",
         "图例：✓已下载　⏳已提交/已生成　✗失败或锚点丢失　·还没开始",
         f"**未就绪：{sn_ranges(miss, n)}**", ""]
    if not bad:
        L.append("**全部就绪，没有要重发的段。**")
    else:
        L.append(f"共 {len(bad)} 段。直接再跑一次 `python grok_pipe.py 跑` "
                 f"就会只补这些段（已下载的会自动跳过）。\n")
        L.append("**要重发的段：**　"
                 + sn_ranges([m[k] for k, _v in bad], n) + "\n")
        for k, v in sorted(bad, key=lambda kv: m.get(kv[0], 0)):
            L += [f"## {fmt_sn(m[k], n)} · {k}（{v['状态']}）", "",
                  f"- 输出文件名：`{v['输出文件名']}`",
                  f"- 起始图：`{v['图片']}`",
                  f"- 时长：{v['时长']}",
                  f"- 备注：{v.get('备注', '')}", "",
                  "**完整提示词：**", "", "```", v["完整提示词"], "```", ""]
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    log(f"✓ 待重发 {len(bad)} 段 → {p}")
    if bad:
        log("  " + sn_ranges([m[k] for k, _v in bad], n))


# ═══════════════ 入口 ═══════════════

def guess_folder():
    for cfgname in ("config_grok2.json", "config_assault_grok.json",
                    "config.json"):
        cfg = load_json(os.path.join(SCRIPT_DIR, cfgname), {}) or {}
        base = cfg.get("save_dir")
        if base and os.path.isdir(base):
            cands = [d for d in glob.glob(os.path.join(base, "[0-9]" * 4))
                     if os.path.isdir(d)]
            if cands:
                return max(cands)
    return None


def day_folders(sheet=None):
    """列出当前分表 save_dir 下所有四位数字的当天文件夹，新的在前。"""
    names = [os.environ.get("TABLE_CONFIG") or "", "config.json",
             "config_grok2.json"]
    if sheet == "grok2":
        names = ["config_grok2.json"] + names
    for name in names:
        if not name:
            continue
        cfg = load_json(os.path.join(SCRIPT_DIR, name), None)
        base = (cfg or {}).get("save_dir")
        if base and os.path.isdir(base):
            ds = [d for d in glob.glob(os.path.join(base, "[0-9]" * 4))
                  if os.path.isdir(d)]
            if ds:
                return sorted(ds, reverse=True)
    return []


def pick_day_folder(sheet=None):
    """让人选当天文件夹。

    以前是 bat 用 for/f 抓 dir 输出来猜「最大的四位数目录」—— 两个毛病：
    路径里有中文，for/f 在换过 chcp 的控制台里容易抓成乱码；
    而且今天新建了 0827 之后，来补 0826 的缺就会补进错的目录。
    改成 Python 列出来让人点一下，两个毛病一起没了。
    """
    ds = day_folders(sheet)
    if not ds:
        p = input("找不到当天文件夹，请粘贴路径：").strip().strip('"')
        return p or None
    print("\n当天文件夹：")
    for i, d in enumerate(ds[:9], 1):
        star = "  ← 最新" if i == 1 else ""
        print(f"  [{i}] {os.path.basename(d)}   {d}{star}")
    print("  [0] 手动粘贴别的路径")
    a = input("选哪个（直接回车 = 最新）：").strip()
    if not a:
        return ds[0]
    if a == "0":
        p = input("路径：").strip().strip('"')
        return p or None
    if a.isdigit() and 1 <= int(a) <= min(9, len(ds)):
        return ds[int(a) - 1]
    return ds[0]


MENU = """
============================================================
  Grok 全流程   账号={acc}  端口={port}  分表={sheet}
  文件夹：{folder}
  台账：{state}
------------------------------------------------------------
  [1] 建账        只登记身份，不碰浏览器
  [2] 跑全程      描述语+建账+铺页+提交锚定+收货+快检   ★ 平时按这个
  [3] 收货补缺    凭台账 postId 把已生成的补下来
  [4] 验收（快）  数量/唯一/时长/首帧对源图/锚点
  [5] 验收（强）  再加转写比对，抓同组 a/b 互换。进拼接前跑一次
  [6] 补漏清单    列出被吞掉的段，供重发
  [s] 段号表      整批 s01…sNN 的进度格子，缺哪几段一眼看到
  [7] 认领改名    救火：一批视频名字已不可信，重新判定真实归属
  [8] 扫类型      读表格 I 列 → 类型登记.json   ★ 开新批第一步
  [m] 描述语      给缺的图写 vNN_desc.txt（[2] 里会自动补，这里可单独跑）
  [9] 试跑一个    只做一个 v 号，验证提交/下载这条路通不通
  [g] 扫仓库      从 /imagine/saved 把全部成品索引一遍，补回缺失的会话Id
  [d] 诊断        列出 Grok 页面上所有能点的控件（提交点不动时用）
  [f] 换文件夹    [0] 退出
============================================================"""


def _state_line(folder):
    led = load_json(ledger_path(folder))
    if not led:
        return "还没建账"
    from collections import Counter
    c = Counter(v["状态"] for v in led["条目"].values())
    _head, _rows, _miss = sn_grid(led["条目"])
    line = f"{len(led['条目'])} 段　" + "　".join(
        f"{k} {c[k]}" for k in ORDER if c.get(k))
    if _miss:
        line += f"\n  未就绪：{sn_ranges(_miss, len(led['条目']))}"
    return line


def cmd_menu(folder=None):
    """交互菜单。菜单放在 Python 里而不是 bat 里，是因为 batch 文件一旦
    同时含有 chcp 65001 和中文，cmd 会按字节偏移错位读取，把中文当命令执行。"""
    acc = os.environ.get("GROK_ACCOUNT", "?")
    sheet = os.environ.get("GROK_SHEET", "grok")
    port = CDP_PORT
    while True:
        # 选出来的路径必须真的存在才往下走。第一版少了这一步，
        # 手滑输了个不存在的路径会带着它进菜单，接下来每一项都报错。
        while not folder or not os.path.isdir(folder):
            try:
                folder = pick_day_folder(sheet)
            except EOFError:
                return
            if folder is None:
                return
            if folder and not os.path.isdir(folder):
                print(f"✗ 这个路径不存在：{folder}")
                folder = None
        print(MENU.format(acc=acc, port=port, sheet=sheet, folder=folder,
                          state=_state_line(folder)))
        try:
            a = input("选：").strip().lower()
        except EOFError:
            return
        try:
            if a in ("0", "q", "exit"):
                return
            elif a == "f":
                folder = None
                continue
            elif a == "1":
                cmd_ledger(folder)
            elif a == "2":
                if not _ensure_desc(folder):
                    continue
                w = input("一波开几个标签页 [回车=10]：").strip() or "10"
                c = input("同时在生成最多几个 [回车=4]：").strip() or "4"
                cmd_run(folder, int(w), int(c))
            elif a == "3":
                cmd_harvest(folder)
                cmd_verify(folder)
            elif a == "4":
                cmd_verify(folder)
            elif a == "5":
                cmd_verify(folder, full=True)
            elif a == "6":
                cmd_missing(folder)
            elif a == "m":
                n = len(_desc_missing(folder))
                if not n:
                    print("这个文件夹的描述语已经齐了。")
                    if input("要全部重写吗？(y/回车=不用)：").strip().lower() == "y":
                        cmd_desc(folder, force=True)
                else:
                    print(f"缺 {n} 个，开始生成…")
                    cmd_desc(folder)
            elif a == "s":
                cmd_segmap(folder)
            elif a == "7":
                cmd_reclaim(folder, apply=False)
                if input("对照表核对无误？真的改名请输 y：").strip().lower() == "y":
                    cmd_reclaim(folder, apply=True)
            elif a == "8":
                cmd_types(folder)
                print("\n下一步：直接点 [m] 描述语（或 [2] 里会自动补）。")
                print("它会读刚落下的 类型登记.json，群体祷告的图走群像模板"
                      "（不写主角、画外音）—— 和 描述语生成器.html 同一套规则，"
                      "不必再开那个页面手点。")
                print("改过类型的编号要重写描述语：[m] 之前先删掉它们的 "
                      "vNN_desc.txt，或用 grok_pipe.py 描述语 <文件夹> --重写。")
            elif a == "9":
                v = input("试跑哪个 v 号（只填数字，如 25）：").strip()
                if v.isdigit():
                    cmd_run(folder, 4, 4, int(v), int(v))
            elif a == "g":
                cmd_gallery(folder)
            elif a == "d":
                k = input("诊断哪一段（如 v21-1of2，回车=扫所有标签页）：").strip()
                _p, _c = _post_of(folder, k) if k else (None, None)
                cmd_diagnose(folder, _p, _c)
            else:
                print("没这个选项")
        except KeyboardInterrupt:
            print("\n（已中断，进度都在台账里，下次接着跑）")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"\n✗ 出错了：{str(e)[:200]}")
        try:
            input("\n回车回菜单…")
        except EOFError:
            return


def main():
    ap = argparse.ArgumentParser(
        description="Grok 全流程：建账 → 铺页 → 提交锚定 → 收货 → 验收")
    ap.add_argument("动作", nargs="?", default="菜单",
                    choices=["菜单", "建账", "跑", "收货", "验收", "补漏",
                             "认领", "类型", "描述语", "诊断", "仓库", "段号表",
                             "menu", "ledger", "run", "harvest", "verify",
                             "missing", "reclaim", "types", "desc", "diagnose",
                             "gallery", "segmap"])
    ap.add_argument("文件夹", nargs="?", default=None)
    ap.add_argument("--波次", "--wave", type=int, default=12,
                    help="一波开几个标签页（默认 12）")
    ap.add_argument("--并发", "--conc", type=int, default=4,
                    help="同时在生成的最多几个（默认 4；Grok 超过会静默丢弃）")
    ap.add_argument("--从", "--from", dest="从", type=int, default=None)
    ap.add_argument("--到", "--to", dest="到", type=int, default=None)
    ap.add_argument("--等待", "--timeout", type=int, default=420,
                    help="一段最多等多少秒还没出视频就判丢弃（默认 420）。"
                         "Grok 出一条 10~15 秒的片子一般一两分钟，"
                         "7 分钟还没有基本就是被吞了")
    ap.add_argument("--强检", "--full", action="store_true",
                    help="验收时加上转写比对（慢，但能抓同组 a/b 互换）")
    ap.add_argument("--重建台账", "--rebuild", action="store_true")
    ap.add_argument("--留着标签页", "--keeptabs", action="store_true")
    ap.add_argument("--执行", "--apply", action="store_true",
                    help="认领时真的改名（默认只出对照表不动文件）")
    ap.add_argument("--段", "--post", default=None,
                    help="诊断某一段：可以写 postId，也可以写 v21-1of2 这样的段号")
    ap.add_argument("--只看", "--dry", dest="只看", action="store_true",
                    help="描述语：只列出缺 sidecar 的编号，不调 Gemini")
    ap.add_argument("--重写", "--redesc", dest="重写", action="store_true",
                    help="描述语：已有 sidecar 也重新生成")
    ap.add_argument("--强行", "--force", action="store_true",
                    help="描述语和类型对不上时仍然开跑（不建议：提示词会自相矛盾）")
    a = ap.parse_args()

    act = a.动作
    if act in ("菜单", "menu"):
        f = a.文件夹
        cmd_menu(os.path.abspath(f) if f and os.path.isdir(f) else None)
        return

    folder = a.文件夹 or guess_folder()
    if not folder or not os.path.isdir(folder):
        print("用法：python grok_pipe.py 跑 <文件夹>")
        sys.exit(1)
    folder = os.path.abspath(folder)
    log(f"文件夹：{folder}")
    if act in ("建账", "ledger"):
        cmd_ledger(folder, a.从, a.到, rebuild=a.重建台账)
    elif act in ("跑", "run"):
        cmd_run(folder, a.波次, a.并发, a.从, a.到, a.等待, a.留着标签页,
                force=a.强行)
    elif act in ("收货", "harvest"):
        cmd_harvest(folder)
        cmd_verify(folder, a.强检)
    elif act in ("验收", "verify"):
        ok = cmd_verify(folder, a.强检)
        sys.exit(0 if ok else 2)
    elif act in ("补漏", "missing"):
        cmd_missing(folder)
    elif act in ("段号表", "segmap"):
        cmd_segmap(folder)
    elif act in ("诊断2", "__never__"):
        pass
    elif act in ("认领", "reclaim"):
        cmd_reclaim(folder, apply=a.执行)
    elif act in ("类型", "types"):
        cmd_types(folder)
    elif act in ("描述语", "desc"):
        cmd_desc(folder, dry=a.只看, force=a.重写)
    elif act in ("仓库", "gallery"):
        cmd_gallery(folder)
    elif act in ("诊断", "diagnose"):
        _p, _c = _post_of(folder, a.段)
        cmd_diagnose(folder, _p, _c)


if __name__ == "__main__":
    main()
