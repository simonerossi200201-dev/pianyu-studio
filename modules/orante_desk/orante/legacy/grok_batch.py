# -*- coding: utf-8 -*-
"""
Grok Imagine 批量准备工具（自愈版 + 开头描述语 + 多窗口并发）
===================================
把当天文件夹里的 vNN 图片 + vNN.txt 分段文案，自动铺进 grok.com/imagine：
每一段 = 开一个新标签页 → 上传对应图片 → 粘贴【开头描述语 + 该段文案】→
自动设置 Video / 720p / 时长(6s/10s/15s 按段智能匹配) / 9:16 竖屏。

时长智能匹配：
- 用与拆分工具同一套"有效长度"算法（字符数 + 标点停顿等效）测每段，
  按 170有效字符≈10秒 折算，自动点能容纳该段的最短时长，语速始终自然
- 6s ≤~112 ＜ 10s ≤~187 ＜ 15s（含 10% 容差，与拆分容差一致）

开头描述语（A 层身份锁）：
- 读图片旁边的 vNN_desc.txt（描述语生成器写到图片旁），不再去表格 I 列
- 旧批次没有 sidecar 时退回 _desc_cache.json
- 某个 v 号两边都没有描述语时，该 v 号只贴英文并提示

镜头运动（B 层运镜卡，与 Flow 完全同一套）：
- 复用 flow_shots 的 cards_for + build_prompt，和 flow_batch 逐字一致：
  A 层身份锁（人物是谁、睁眼闭眼、场景）全程不变，
  B 层运镜卡逐段不同（首段推近建立 → 中段环绕/横移/特写轮换 → 末段拉开收束），
  一段之内只有一个连续镜头，段与段之间才换手法
- 运镜卡文件 当天文件夹/vXX_shot.txt 与 Flow 共用同一份：手工改过的镜头两边都生效；
  分段数变了（接力按 Grok 规则重切）会自动重算，不会串镜头
- 三档开关 grok_shot_mode（config*.json，环境变量 GROK_SHOT_MODE 优先）：
  完整（默认，约 800 字符）／精简（约 470 字符）／关闭（退回只贴描述语）

多窗口并发：
- WORKERS 个线程同时开标签页（默认 3），互不干扰
- 台账写入加锁，逐段落盘，中断后重跑即恢复

可靠性机制（不重复、不遗漏、可恢复）：
- 断网自动等待恢复（每 20 秒探测），恢复后继续
- 每段最多重试 5 次，失败的半成品标签页自动关闭
- 成功判定前先校验：输入框文字与原文逐字一致 + 图片确已附上
- grok_done.json 台账：每段成功立即记录（含文案指纹）；
  重跑时标签页还开着的跳过，缺的自动补，中断后重跑即恢复
- 旧台账（只记英文指纹）依然认，不会把之前做完的重做一遍

用法：双击 启动-Grok批量.bat
"""

import glob
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CDP_PORT = os.environ.get("GROK_CDP_PORT", "9222")   # 由 bat 按所选账号注入
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
IMAGINE_URL = "https://grok.com/imagine"
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
# 每个账号独立台账，互不干扰（Stefano 沿用原 grok_done.json）
DONE_FILE = os.path.join(SCRIPT_DIR,
                         os.environ.get("GROK_DONE_FILE", "grok_done.json"))
# TABLE_CONFIG 环境变量由启动器的分表菜单设置（config.json / config_grok2.json）
CONFIG_FILE = os.path.join(SCRIPT_DIR,
                           os.environ.get("TABLE_CONFIG", "config.json"))

CLICK_VIDEO = True    # 自动点 Video 模式
CLICK_720P = True     # 自动点 720p 清晰度
SMART_DURATION = True  # 按段智能选 6s/10s/15s；False = 固定点 FALLBACK_DUR
FALLBACK_DUR = "10s"  # 关闭智能匹配或识别失败时使用的固定时长
DUR_RATE = 17.0       # 每秒可容纳的有效字符数（170 ÷ 10s）
DUR_TOLERANCE = 0.10  # 时长判定容差（与拆分工具的 10% 一致）
DUR_CHOICES = (6, 10, 15)  # Grok 页面提供的时长档位（秒）
# 时长档位分界（有效长度）。留空 = 按 DUR_RATE 折算（≤112→6s ≤187→10s 其余15s）。
# 环境变量 GROK_DUR_THRESHOLDS="150,250" 可覆盖：≤150→6s ≤250→10s 其余→15s
# 接力救援的「文件夹认领」模式会自动注入这个变量。
DUR_THRESHOLDS = os.environ.get("GROK_DUR_THRESHOLDS", "").strip()
ASPECT = "9:16"       # 画幅比例（竖屏），不想自动设就改成 ""
TAB_DELAY = 0.5       # 每个标签页之间的间隔秒数（每个线程各自遵守）
UPLOAD_WAIT = 4.0     # 上传图片的最长等待秒数（缩略图出现即继续，不再傻等）
MAX_RETRY = 5         # 每段最大重试次数
NET_PROBE_WAIT = 20   # 断网时的探测间隔秒数
WORKERS = 3           # 同时开标签页的线程数（1 = 恢复原来的串行模式）

# ---- 时长回读（0904 加）----
# 原先 set_params 点时长按钮是"点了就算数"：点不到就 except: pass 静默跳过，
# 页面停在 Grok 上次用的档位（这东西是粘的，新标签页会继承上一次的选择）。
# 0904 那批 28 段里，5 段想要 10s 的全部拿回 15s —— 而 verify_tab 只校验文案
# 和图片，唯独不校验时长，所以这几页照样被判"铺页成功"提交出去，烧完额度、
# 下载完，一直到验收才发现。历史日志里还有反方向的一条（想要 15s 拿回 10s），
# 同一个病。现在：点完回读页面确认，切不过去就重试，重试完还不对就抛异常，
# 由 prepare_with_retry / grok_pipe 的外层重试整页 —— 宁可这一段失败，
# 也不拿错档位的片子去烧额度。
DUR_LABELS = tuple(f"{s}s" for s in DUR_CHOICES)
DUR_SET_RETRY = 3     # 时长切换的重试次数
# 读不回选中态时怎么办："warn" = 存证据 + 警告后放行（默认，不打断出片）；
# "strict" = 一样抛异常。环境变量 GROK_DUR_STRICT=strict 可切。
DUR_STRICT = os.environ.get("GROK_DUR_STRICT", "warn").strip().lower()
DUR_DEBUG_FILE = os.path.join(SCRIPT_DIR, "grok_dur_probe.json")
_ACTIVE_STATES = ("active", "on", "checked", "selected", "true")

PRINT_LOCK = threading.Lock()


class DurationError(Exception):
    """时长档位没切过去（或读不出来且要求严格）。"""


def tprint(*args, **kwargs):
    with PRINT_LOCK:
        print(*args, **kwargs)


# ---------------- 通用 ----------------

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def norm_text(s):
    return re.sub(r"\s+", " ", s).strip()


def seg_hash(s):
    return hashlib.md5(norm_text(s).encode("utf-8")).hexdigest()[:12]


# ---------------- 时长智能匹配 ----------------
# 与 表格自动下载器.py 的"有效长度"完全同一套算法：
# 字符数 + 标点停顿等效（逗号≈10、句号≈5），数字内部的 3.5 / 1,000 不算停顿

_SENT_PUNCT = set(".!?…。！？")
_COMMA_PUNCT = set(",;:，、；：")
_DIGIT_PUNCT_RE = re.compile(r"(?<=\d)[.,:](?=\d)")
PAUSE_COMMA = 10
PAUSE_SENT = 5


def eff_len(text):
    """段落的有效长度：字符数 + 标点停顿等效字符数"""
    t = norm_text(text)
    w = len(t)
    for ch in _DIGIT_PUNCT_RE.sub("", t):
        if ch in _SENT_PUNCT:
            w += PAUSE_SENT
        elif ch in _COMMA_PUNCT:
            w += PAUSE_COMMA
    return w


def _parse_thresholds(s):
    """把 "150,250" 解析成 [150.0, 250.0]；格式不对返回 None"""
    try:
        v = [float(x) for x in s.replace("，", ",").split(",") if x.strip()]
    except ValueError:
        return None
    return v if len(v) == len(DUR_CHOICES) - 1 else None


def pick_duration(seg_text):
    """选能容纳该段的最短时长档位（含容差），都装不下就用最长档。

    默认按 DUR_RATE 折算；若设了 GROK_DUR_THRESHOLDS（如 "150,250"），
    则改用固定分界：有效长度 < 150 → 6s，≤ 250 → 10s，其余 → 15s。
    """
    if not SMART_DURATION:
        return FALLBACK_DUR
    L = eff_len(seg_text)
    cut = _parse_thresholds(DUR_THRESHOLDS) if DUR_THRESHOLDS else None
    if cut:
        # cut[i] = 第 i 档与第 i+1 档的分界；等于分界值时归上一档（更保险）
        if L < cut[0]:
            return f"{DUR_CHOICES[0]}s"
        for i in range(1, len(cut)):
            if L <= cut[i]:
                return f"{DUR_CHOICES[i]}s"
        return f"{DUR_CHOICES[-1]}s"
    for sec in DUR_CHOICES:
        if L <= sec * DUR_RATE * (1 + DUR_TOLERANCE):
            return f"{sec}s"
    return f"{max(DUR_CHOICES)}s"


def check_online():
    """探测外网连通性（有任何 HTTP 响应都算在线）"""
    import urllib.error
    try:
        urllib.request.urlopen("https://grok.com", timeout=10)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def wait_online():
    if check_online():
        return
    tprint(f"  ⚠ 网络不可用，每 {NET_PROBE_WAIT} 秒自动重试，恢复后继续…")
    while not check_online():
        time.sleep(NET_PROBE_WAIT)
    tprint("  ✓ 网络已恢复")


# ---------------- 开头描述语（图片旁 vNN_desc.txt） ----------------

def fetch_desc_map(folder):
    """读图片旁边的 vNN_desc.txt。旧批次退回 _desc_cache.json。"""
    from desc_files import load_desc_map
    return load_desc_map(folder, log=tprint)


# ---------------- 任务收集 ----------------

def find_folder():
    """只在当前分表配置的 save_dir 里找（Grok2 有独立目录，不互相拿错）"""
    tcfg = load_json(CONFIG_FILE, {})
    base = tcfg.get("save_dir") or SCRIPT_DIR
    if os.path.isdir(base):
        cands = [d for d in glob.glob(os.path.join(base, "[0-9]" * 4))
                 if os.path.isdir(d)]
        if cands:
            return max(cands)
    return None


def collect_jobs(folder, v_from=None, v_to=None):
    jobs = []
    txts = [t for t in glob.glob(os.path.join(folder, "v*.txt"))
            if re.fullmatch(r"[vV]\d+",
                            os.path.splitext(os.path.basename(t))[0])]
    # 按 v 号数值排序（纯文件名排序会把 v100 排到 v11 前面）
    for txt in sorted(txts,
                      key=lambda t: int(os.path.basename(t)[1:].split(".")[0])):
        label = os.path.splitext(os.path.basename(txt))[0]
        num = int(label[1:])
        if v_from and num < v_from:
            continue
        if v_to and num > v_to:
            continue
        img = None
        for ext in IMG_EXTS:
            p = os.path.join(folder, label + ext)
            if os.path.exists(p):
                img = p
                break
        if not img:
            print(f"  ! {label}：找不到对应图片，跳过")
            continue
        with open(txt, "r", encoding="utf-8") as f:
            content = f.read()
        segs = [s.strip() for s in re.split(r"\n\s*\n", content) if s.strip()]
        if not segs:
            print(f"  ! {label}：txt 为空，跳过")
            continue
        jobs.append((label, img, segs))
    return jobs


# ---------------- 页面操作 ----------------

def pass_tos_gate(page):
    """条款页：2026-09 按钮是「知道了」，不是第一个 button（那是「跳至主要内容」）。"""
    labels = ("知道了", "Got it", "I understand", "I agree", "Accept", "Agree")
    for _ in range(10):
        url = ""
        try:
            url = page.url or ""
        except Exception:
            url = ""
        hit = False
        for lab in labels:
            try:
                loc = page.get_by_role("button", name=lab, exact=False).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=3000)
                    hit = True
                    break
            except Exception:
                continue
        if "tos-gate" not in url:
            if hit:
                try:
                    page.wait_for_timeout(800)
                except Exception:
                    pass
            return
        if not hit:
            try:
                page.locator("button").first.click(timeout=3000)
            except Exception:
                pass
        page.wait_for_timeout(1500)


# 把 6s/10s/15s 三个档位按钮各自的"长相"抓回来。
# 不写死 Grok 的 class 名：先认无障碍属性（aria-pressed / data-state 之类），
# 认不出来就比三个按钮的长相 —— 选中的那个必然和另外两个不一样。
# Grok 改版换了 class 名这法子照样活着。
_DUR_PROBE_JS = r"""(labels) => {
  const norm = s => (s || '').replace(/\s+/g, '');
  const leaves = [...document.querySelectorAll('*')].filter(
    e => e.children.length === 0);
  const out = [];
  for (const label of labels) {
    let node = null;
    for (const el of leaves) {
      if (norm(el.textContent) !== label) continue;
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) { node = el; break; }
    }
    if (!node) { out.push({label: label, found: false}); continue; }
    let btn = node;
    for (let i = 0; i < 6 && btn && btn !== document.body; i++) {
      const role = btn.getAttribute ? btn.getAttribute('role') : null;
      if (btn.tagName === 'BUTTON' || role === 'button' || role === 'radio' ||
          role === 'tab' || role === 'option' || role === 'menuitemradio') break;
      btn = btn.parentElement;
    }
    if (!btn || btn === document.body) btn = node;
    const cs = getComputedStyle(btn);
    const cls = typeof btn.className === 'string'
      ? btn.className : ((btn.className && btn.className.baseVal) || '');
    out.push({
      label: label, found: true, tag: btn.tagName, cls: cls,
      pressed: btn.getAttribute('aria-pressed'),
      checked: btn.getAttribute('aria-checked'),
      selected: btn.getAttribute('aria-selected'),
      state: btn.getAttribute('data-state'),
      bg: cs.backgroundColor, fg: cs.color, bd: cs.borderColor,
      op: cs.opacity, fw: cs.fontWeight
    });
  }
  return out;
}"""


def read_duration(page, labels=None):
    """读回页面上此刻选中的时长档位。

    返回 (label 或 None, probe)。None = 读不出来，不等于"没选中"。
    判定顺序：先认无障碍属性，再认"三个里长得不一样的那个"。
    """
    labels = list(labels or DUR_LABELS)
    try:
        probe = page.evaluate(_DUR_PROBE_JS, labels)
    except Exception as e:
        return None, [{"err": str(e)[:160]}]
    found = [p for p in probe if p.get("found")]
    if not found:
        return None, probe
    if len(found) == 1:
        # 0905：新版 Grok 把 6s/10s/15s 收进了下拉菜单，工具栏上只留当前档位
        # 那一颗（data-state=closed）。所以"页面上只认出一个档位"本身就是答案 ——
        # 它就是此刻选中的那个，不是"读不回来"。旧版三颗并排时不会走到这里。
        return found[0]["label"], probe
    for p in found:
        vals = [str(p.get(k) or "").lower()
                for k in ("pressed", "checked", "selected", "state")]
        if any(v in _ACTIVE_STATES for v in vals):
            return p["label"], probe
    # 长相比对：某一项属性上，正好一个与众不同 → 那个就是选中的
    for key in ("cls", "bg", "fg", "bd", "fw"):
        vals = [p.get(key) for p in found]
        if len(set(vals)) != 2:
            continue
        odd = [v for v in set(vals) if vals.count(v) == 1]
        if len(odd) == 1:
            return found[vals.index(odd[0])]["label"], probe
    return None, probe


def _dump_dur_probe(probe, want, got):
    """读不回来 / 对不上时留一份现场，方便照着它把判定收紧。"""
    try:
        rec = {"时间": time.strftime("%Y-%m-%d %H:%M:%S"),
               "想要": want, "读到": got, "探针": probe}
        old = load_json(DUR_DEBUG_FILE, [])
        if not isinstance(old, list):
            old = [old]
        old.append(rec)
        save_json(DUR_DEBUG_FILE, old[-50:])
    except Exception:
        pass


def _click_duration(page, want):
    """点中 want 这个时长档位。

    0905：新版 Grok 把三个档位收进了下拉菜单，工具栏上只剩当前那一颗。
    所以先直接点（旧版三颗并排、或菜单已经展开时这一下就成）；点不到就先点
    工具栏上那颗当前档位把菜单展开，再点想要的那个。
    """
    try:
        page.get_by_text(want, exact=True).first.click(timeout=1500)
        return
    except Exception:
        pass
    for lab in DUR_LABELS:
        if lab == want:
            continue
        try:
            page.get_by_text(lab, exact=True).first.click(timeout=1200)
            page.wait_for_timeout(350)
            break
        except Exception:
            continue
    try:
        page.get_by_text(want, exact=True).first.click(timeout=2500)
        return
    except Exception:
        pass
    # 0908：中文/意大利文界面上档位可能写成「6秒」「6 s」。前面几下都没点中时
    # 再按这些写法试一遍——纯粹是补刀，点中了就不会走到这里。
    n = re.sub(r"[^\d]", "", str(want))
    for alt in (f"{n} s", f"{n}秒", f"{n} 秒", f"{n}s", n):
        if not n or alt == want:
            continue
        try:
            page.get_by_text(alt, exact=True).first.click(timeout=900)
            return
        except Exception:
            continue
    page.get_by_text(want, exact=True).first.click(timeout=2500)   # 让它照旧抛错


def set_duration(page, duration, log=None):
    """把时长切到 duration，并且回读确认真的切过去了。

    切成功返回 True；读不回选中态且 DUR_STRICT != strict 时返回 False（放行，
    但留证据）；确实切不过去就抛 DurationError。
    """
    say = log or (lambda *a: None)
    want = str(duration).strip()
    probe = None
    for i in range(1, DUR_SET_RETRY + 1):
        cur, probe = read_duration(page)
        if cur == want:
            return True
        try:
            _click_duration(page, want)
        except Exception as e:
            say(f"    · 时长 {want} 点不到（第 {i} 次）：{str(e)[:70]}")
        page.wait_for_timeout(350)
        cur, probe = read_duration(page)
        if cur == want:
            return True
    cur, probe = read_duration(page)
    if cur == want:
        return True
    _dump_dur_probe(probe, want, cur)
    if cur is None:
        say(f"    ⚠ 时长读不回来（想要 {want}）——现场已存 {DUR_DEBUG_FILE}")
        if DUR_STRICT == "strict":
            raise DurationError(f"时长读不回来（想要 {want}）")
        return False
    raise DurationError(f"时长没切过去：想要 {want}，页面上是 {cur}")


# 模式按钮的各国说法。Grok 的界面跟着账号语言走：中文是「图片/视频」，
# 意大利文是 Immagine/Video。0908 之前这里只认英文的 "Video"，
# 中文界面上永远点不中，而且失败是 except: pass 悄悄咽掉的——
# 于是刚生完图停在「图片模式」的输入框就那么原样提交，视频一条都没出来。
VIDEO_WORDS = ("Video", "视频", "視頻", "Vidéo", "Vídeo", "ビデオ", "동영상")
IMAGE_WORDS = ("Image", "Immagine", "图片", "图像", "圖片", "Imagen", "画像")


def _mode_is_video(page) -> bool:
    """当前选中的模式是不是视频。读不出来返回 False（宁可再点一次）。"""
    try:
        radios = page.get_by_role("radio")
        for i in range(min(radios.count(), 6)):
            r = radios.nth(i)
            try:
                checked = r.get_attribute("aria-checked") or r.get_attribute("data-state") or ""
            except Exception:
                continue
            if str(checked).lower() not in ("true", "checked", "on", "active"):
                continue
            name = ((r.get_attribute("aria-label") or "") + " " + (r.inner_text() or "")).strip()
            return any(w.lower() in name.lower() for w in VIDEO_WORDS)
    except Exception:
        pass
    return False


def click_mode_video(page, log=None) -> str:
    """切到视频模式。返回点中的说法；一个都没点中返回空字符串。"""
    say = log or (lambda *a, **k: None)
    if _mode_is_video(page):
        return "已经是视频模式"
    # ① 按 role=radio 的名字找（最靠谱，界面换语言也认）
    for w in VIDEO_WORDS:
        try:
            loc = page.get_by_role("radio", name=w, exact=False).first
            if loc.count():
                loc.click(timeout=1500)
                page.wait_for_timeout(250)
                return w
        except Exception:
            pass
    # ② 退而求其次：页面上的可见文字
    for w in VIDEO_WORDS:
        try:
            page.get_by_text(w, exact=True).first.click(timeout=1200)
            page.wait_for_timeout(250)
            return w
        except Exception:
            pass
    # ③ 最后：按位置。那一排的顺序是 图片 / 视频 / 代理
    try:
        radios = page.get_by_role("radio")
        if radios.count() >= 2:
            radios.nth(1).click(timeout=1500)
            page.wait_for_timeout(250)
            return "第二个模式按钮"
    except Exception:
        pass
    say("  ⚠ 没找到「视频」模式按钮——这一条可能会当成生图提交")
    return ""


def set_params(page, duration=FALLBACK_DUR, log=None):
    """切 Video / 720p / 时长(按段传入) / 画幅比例。

    Video 支持多语言并回读确认（0908）；720p / 画幅找不到按钮仍旧跳过；
    时长这一项要回读确认，切不过去会抛 DurationError（见顶部 0904 的说明）。
    """
    if CLICK_VIDEO:
        how = click_mode_video(page, log=log)
        if how and log:
            log(f"  · 视频模式：{how}")
        if not _mode_is_video(page) and log:
            log("  ⚠ 点完仍然读不到「视频」选中态，继续往下走，注意出来的是不是视频")
    if CLICK_720P:
        try:
            page.get_by_text("720p", exact=True).first.click(timeout=1500)
            page.wait_for_timeout(200)
        except Exception:
            pass
    if duration:
        set_duration(page, duration, log=log)
    if ASPECT:
        try:
            btn = page.locator("button").filter(
                has_text=re.compile(r"\d+:\d+")).last
            if ASPECT not in btn.inner_text(timeout=1500):
                btn.click(timeout=1500)
                page.wait_for_timeout(400)
                page.get_by_text(ASPECT, exact=True).last.click(timeout=2000)
                page.wait_for_timeout(200)
        except Exception:
            pass


def verify_tab(page, seg_text, duration=None):
    """校验：文字逐字一致 + 图片确已附上 + 时长档位对，任一不过即抛异常

    0828：原先的图片校验认 input.files.length > 0 —— set_input_files 一执行
    这个必然为真，跟网站到底收没收这张图毫无关系，等于压根没校验。
    「图片未成功附上」这道闸从来没响过，所以"有字没图"的残页也照样记成
    已铺页。现在只认页面上真的出现了缩略图。

    0904：时长是同一个病 —— 这道闸以前根本不看时长，于是"想要 10s、页面
    停在 15s"的残页照样过闸提交。现在传了 duration 就一起校验；读不回选中态
    时按 DUR_STRICT 决定是放行（默认，留证据）还是拦下。
    """
    got = page.locator("div.tiptap").first.inner_text(timeout=8000)
    if norm_text(got) != norm_text(seg_text):
        raise Exception(f"文案校验不一致（框内{len(norm_text(got))}字符 "
                        f"vs 原文{len(norm_text(seg_text))}字符）")
    has_img = page.evaluate(
        "() => !!document.querySelector(\"img[src^='blob:']\")")
    if not has_img:
        raise Exception("图片未成功附上（页面上没有缩略图）")
    if duration:
        want = str(duration).strip()
        cur, probe = read_duration(page)
        if cur is None:
            _dump_dur_probe(probe, want, cur)
            if DUR_STRICT == "strict":
                raise Exception(f"时长读不回来（想要 {want}）")
        elif cur != want:
            _dump_dur_probe(probe, want, cur)
            raise Exception(f"时长不对：想要 {want}，页面上是 {cur}")


def prepare_tab(ctx, img_path, seg_text, tab_title, duration=FALLBACK_DUR):
    """开新页 → 传图 → 设参数 → 贴文案 → 校验 → 设标题。
    任何一步失败都会关闭这个半成品页并抛异常（由外层重试）"""
    page = ctx.new_page()
    try:
        page.goto(IMAGINE_URL, wait_until="domcontentloaded", timeout=60000)
        pass_tos_gate(page)
        # 等页面真正可交互（编辑器渲染完成），取代原先固定等 2.5 秒
        page.wait_for_selector("input[type=file]", state="attached",
                               timeout=30000)
        page.wait_for_selector("div.tiptap", state="visible", timeout=30000)
        page.set_input_files("input[type=file]", img_path)
        # 等缩略图出现即继续（最多 UPLOAD_WAIT 秒），取代原先固定等 2.5 秒；
        # 个别版本不渲染缩略图也没关系，最终由 verify_tab 兜底校验
        try:
            page.wait_for_selector("img[src^='blob:']", state="attached",
                                   timeout=int(UPLOAD_WAIT * 1000))
        except Exception:
            pass
        page.wait_for_timeout(400)
        set_params(page, duration, log=tprint)
        editor = page.locator("div.tiptap").first
        editor.click(timeout=8000)
        page.keyboard.insert_text(seg_text)
        page.wait_for_timeout(500)
        verify_tab(page, seg_text, duration)
        try:
            page.evaluate("t => { document.title = t }", tab_title)
        except Exception:
            pass
        return page
    except Exception:
        try:
            page.close()
        except Exception:
            pass
        raise


def prepare_with_retry(ctx, img_path, seg_text, tab_title,
                       duration=FALLBACK_DUR):
    """重试外壳：断网等恢复，失败退避重试，最多 MAX_RETRY 次"""
    last_err = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            prepare_tab(ctx, img_path, seg_text, tab_title, duration)
            return True, None
        except Exception as e:
            last_err = str(e)[:120]
            # 只在失败后才探测网络（若因断网失败，等恢复再算下一次重试），
            # 取代原先每段开始前都探测一次
            wait_online()
            if attempt < MAX_RETRY:
                backoff = min(30, 4 * attempt)
                tprint(f"  ↻ {tab_title} 第{attempt}次失败({last_err[:60]})，"
                       f"{backoff}秒后重试")
                time.sleep(backoff)
    return False, last_err


# ---------------- 并发工作线程 ----------------

def worker(wid, task_q, done, state):
    """每个线程自己连一路 CDP（playwright 同步 API 不能跨线程共用），
    从队列取任务开标签页。台账/计数写入均加锁。"""
    from playwright.sync_api import sync_playwright
    time.sleep(0.8 * wid)   # 错峰启动，避免同时轰 new_page
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(
                CDP_URL, no_defaults=True, is_local=True)
        except Exception:
            tprint(f"  ! 线程{wid} 连不上 Chrome 调试端口({CDP_PORT})，退出")
            return
        ctx = browser.contexts[0]
        while True:
            try:
                title, key, img, full, dur = task_q.get_nowait()
            except queue.Empty:
                return
            success, err = prepare_with_retry(ctx, img, full, title, dur)
            with state["lock"]:
                if success:
                    state["ok"] += 1
                    state["done"][key] = seg_hash(full)
                    save_json(DONE_FILE, state["done"])  # 立即落盘，中断也不丢
                else:
                    state["fail"] += 1
                    state["failures"].append((title, err))
            if success:
                tprint(f"✓ {title}  [{len(full)}字符/{dur}] {full[:40]}…")
            else:
                tprint(f"✗ {title} 放弃（重试{MAX_RETRY}次仍失败）：{err}")
            time.sleep(TAB_DELAY)


# ---------------- 主流程 ----------------

def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("缺少 playwright，请先运行: pip install playwright")
        sys.exit(1)

    folder = sys.argv[1] if len(sys.argv) > 1 else find_folder()
    if not folder or not os.path.isdir(folder):
        print("找不到图片文件夹。用法: python grok_batch.py [文件夹路径]")
        sys.exit(1)
    day = os.path.basename(folder.rstrip("\\/"))
    print(f"文件夹:{folder}")

    jobs = collect_jobs(folder)
    total = sum(len(s) for _, _, s in jobs)
    print(f"共 {len(jobs)} 张图片 / {total} 段文案")
    for label, img, segs in jobs:
        durs = "+".join(pick_duration(s) for s in segs)
        print(f"  {label}: {len(segs)} 段 [{durs}]  ({os.path.basename(img)})")
    if not jobs:
        sys.exit(0)

    rng = input("回车=全部执行；或输入范围如 1-10 只处理 v01~v10: ").strip()
    if rng:
        m = re.match(r"(\d+)\s*-\s*(\d+)", rng)
        if m:
            jobs = collect_jobs(folder, int(m.group(1)), int(m.group(2)))

    # 开头描述语（图片旁 vNN_desc.txt，按 v 号一一对应）
    descs = fetch_desc_map(folder)
    no_desc = [label for label, _, _ in jobs if not descs.get(label)]
    if no_desc:
        print(f"  ! 这些 v 号图片旁没有 vNN_desc.txt，将只贴英文："
              f"{', '.join(no_desc)}")
        print("  ! 请用 描述语生成器.html 生成后点「写到图片旁边」")

    # 展开成任务列表：(标签页标题, 台账key, 图片, 英文段, 实际粘贴全文, 旧台账指纹们)
    #
    # 【A 层描述语要先清洗，不能原样贴】
    #   vNN_desc.txt 的描述语是给 Flow 那套 build_prompt 用的原料，带两样 Grok 读不懂
    #   的东西，实测 0807 那批 230 条：
    #     · 230/230 结尾有机读标签【闭眼|常规】—— 那是给程序读的 4 个字，
    #       原样贴进去 Grok 会当成画面内容
    #     · 203/230 含「原地不动」—— 本意是双脚不移动，但模型理解成整个画面静止，
    #       输出一张不动的图 + 只有嘴在动（详见 flow_shots.defreeze 的注释）
    #   所以这里复用 flow_shots 的同一套清洗：切旧结语（内含去标签）+ 解冻。
    #   车内场景【不解冻】：那里本来就要求整个画面静止。
    #
    # 【为什么 Grok 现在也要 B 层运镜卡】
    #   老版本到清洗为止就停了，理由是"运镜卡的英文摄影术语是照 Veo 调的，
    #   照搬 Grok 未必成立"。可代价是 Grok 那边根本没有任何镜头指令，出来的
    #   视频镜头基本不动 —— 起始图什么样，八九不离十就是一张会说话的照片。
    #   Flow 那套 A 层身份锁 + B 层运镜卡已经把「一段之内只有一个连续镜头、
    #   段与段之间才换手法」跑通了，这里直接复用同一个 build_prompt，
    #   两边逐字一致；运镜卡文件 vXX_shot.txt 也和 Flow 共用同一份，
    #   手工改过的镜头两边同时生效。
    #   接力（接力-运行.bat）最终调的就是本脚本，所以接力也一并有了运镜卡。
    #
    # 【三档开关】config*.json 的 grok_shot_mode（环境变量 GROK_SHOT_MODE 优先）
    #   完整（默认）= 和 Flow 一字不差，实测约 800 字符
    #   精简        = 只留 运镜卡+运动强制+走动强制+身份锁+说话指令，约 470 字符
    #   关闭        = 退回老行为：只清洗描述语，不加任何镜头指令
    #   —— Grok 输入框若容不下长文本，verify_tab 会报「逐字不一致」并重试，
    #      这时把 grok_shot_mode 改成 精简 即可，不用动代码。
    mode = (os.environ.get("GROK_SHOT_MODE")
            or load_json(CONFIG_FILE, {}).get("grok_shot_mode")
            or "完整").strip()
    if mode in ("full", "完整版"):
        mode = "完整"
    elif mode in ("lite", "精简版", "short"):
        mode = "精简"
    elif mode in ("off", "none", "关", "关闭"):
        mode = "关闭"
    elif mode not in ("完整", "精简"):
        print(f"  ! grok_shot_mode=「{mode}」不认识（只认 完整/精简/关闭），"
              f"按「完整」处理")
        mode = "完整"

    clean = build = None
    shots_lib = None
    shot_map = {}                    # label -> (cards, eye, scene, 是否手工)
    try:
        from flow_shots import (parse_tag, strip_legacy_tail, defreeze,
                                load_shots, cards_for, build_prompt)

        def clean(desc):
            # 0811：车内也解冻（defreeze 内部按 scene 换车内那张替换表，
            # "原地不动"→"坐在座位上不挪动"）。原因是车内已经开放小幅度运镜，
            # A 层那句"画面保持静止"会直接和运镜卡打架。
            _eye, scene, _body = parse_tag(desc)
            return defreeze(strip_legacy_tail(desc), scene)

        if mode != "关闭":
            shots_lib = load_shots()
            if mode == "精简":
                # 只替换非车内的组装顺序。车内那份要保留运镜卡和车辆静止锁，
                # 再砍就只剩身份锁了，反而丢掉「车停着熄火、画面不淡化」的交代。
                shots_lib = dict(shots_lib)
                shots_lib["组装顺序"] = list(
                    shots_lib.get("组装顺序_精简") or shots_lib["组装顺序"])
            build = build_prompt
    except Exception as e:                    # 缺文件也不能让接力跑不起来
        print(f"  ! 读不到 flow_shots（{str(e)[:50]}），描述语只能原样贴 —— "
              f"机读标签和「原地不动」会一起进提示词，也没有运镜卡")
        build = None

    # ── B 层运镜卡：逐段不同，一段之内只有一个连续镜头 ──
    # 手工写过 vXX_shot.txt 且行数对得上就用手工的，否则自动生成并落盘。
    # 注意分段数：接力过来的文件夹是按 Grok 规则重切的（170 而不是 Flow 的 110），
    # 行数和 Flow 那份对不上时 cards_for 会自动重算，不会串镜头。
    if build:
        n_manual = 0
        scene_stat = {}
        for label, _, segs in jobs:
            cards, eye, scene, manual = cards_for(
                folder, label, len(segs), descs.get(label) or "", shots_lib)
            shot_map[label] = (cards, eye, scene, manual)
            n_manual += bool(manual)
            scene_stat[f"{eye}|{scene}"] = scene_stat.get(f"{eye}|{scene}", 0) + 1
        print(f"运镜卡[{mode}]：{len(shot_map)} 个编号已配"
              f"（{n_manual} 个用了手工 vXX_shot.txt）  "
              + "，".join(f"{k}×{v}" for k, v in sorted(scene_stat.items())))
    else:
        print(f"运镜卡[{mode}]：本次不加镜头指令，只贴清洗后的描述语 + 英文")

    tasks = []
    n_clean = 0
    for label, img, segs in jobs:
        raw = (descs.get(label) or "").strip()
        desc = clean(raw) if (clean and raw) else raw
        if raw and desc != raw:
            n_clean += 1
        cards, eye, scene, _ = shot_map.get(label, ([], "闭眼", "常规", False))
        for i, seg in enumerate(segs, 1):
            title = f"{label}-{i}of{len(segs)}"
            if raw and build:
                card = cards[i - 1] if 0 < i <= len(cards) else ""
                full = build(raw, card, eye, scene, seg, shots_lib)
            else:
                full = f"{desc}\n{seg}" if desc else seg
            # 认旧台账用的历史写法（只比对指纹，一个字都不会被粘贴）：
            #   一代=只有英文段  二代=原始描述语+英文段  三代=清洗后描述语+英文段
            # 少一个都会把之前做完的重做一遍，纯烧额度。
            legacies = [f"{raw}\n{seg}" if raw else seg,
                        f"{desc}\n{seg}" if desc else seg]
            tasks.append((title, f"{day}/{title}", img, seg, full, legacies))
    print(f"本次共 {len(tasks)} 段" + (f"，{n_clean} 个编号的描述语已清洗" if n_clean else ""))
    if build and tasks:
        _lens = [len(t[4]) for t in tasks]
        # 0807 那批 230 编号实测：完整版 663~1290 字符（中位 951），
        # 精简版 531~930（中位 629）。超过 1500 才提示，否则天天报噪音。
        print(f"提示词长度：最短 {min(_lens)} / 最长 {max(_lens)} 字符"
              + ("　⚠ 偏长，若 Grok 输入框截断（verify 报逐字不一致），"
                 "把 grok_shot_mode 改成「精简」" if max(_lens) > 1500 else ""))

    # 主线程先连一次：读取已打开的标签页标题 + 处理台账问答
    done = load_json(DONE_FILE, {})
    open_titles = set()
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(
                CDP_URL, no_defaults=True, is_local=True)
        except Exception:
            print(f"\n连不上 Chrome 调试端口({CDP_PORT})。")
            print("请用 启动-Grok批量.bat 拉起专用 Chrome 并登录 Grok 后再试。")
            sys.exit(1)
        for pg in browser.contexts[0].pages:
            try:
                open_titles.add(pg.title())
            except Exception:
                pass

    def is_done(key, seg, full, legacies=None):
        # 兼容四代台账，缺一个就会把做完的重做一遍：
        #   一代：只记英文段的指纹
        #   二代：记「原始描述语 + 英文段」的指纹
        #   三代：记「清洗后的描述语 + 英文段」的指纹
        #   四代（当前）：记「A层+B层运镜卡全文」的指纹 —— 就是 full
        got = done.get(key)
        if got is None:
            return False
        cand = {seg_hash(seg), seg_hash(full)}
        for lg in (legacies or []):
            if lg:
                cand.add(seg_hash(lg))
        return got in cand

    # 台账里记完成、但标签页已不在了的（比如 Chrome 重启过）
    stale = [t for t, k, _, s, f, lg in tasks
             if is_done(k, s, f, lg) and t not in open_titles]
    redo_stale = False
    if stale:
        ans = input(
            f"发现 {len(stale)} 段之前已完成但标签页已关闭"
            f"（如已生成过视频请直接回车跳过；输入 r 重做）: ").strip()
        redo_stale = ans.lower() == "r"

    # 先筛掉要跳过的，剩下的进队列
    skip = 0
    task_q = queue.Queue()
    for title, key, img, seg, full, legacies in tasks:
        if is_done(key, seg, full, legacies):
            if title in open_titles:
                skip += 1
                print(f"− {title} 已开好，跳过")
                continue
            if not redo_stale:
                skip += 1
                print(f"− {title} 台账已记录（标签页已关），跳过")
                continue
        # 时长按英文段本身测算（开头描述语是画面提示，不占朗读时间）
        task_q.put((title, key, img, full, pick_duration(seg)))

    n = task_q.qsize()
    workers = max(1, min(WORKERS, n)) if n else 0
    state = {"lock": threading.Lock(), "done": done,
             "ok": 0, "fail": 0, "failures": []}
    if n:
        print(f"开始执行：{n} 段，{workers} 个窗口同时进行…")
        threads = [threading.Thread(target=worker,
                                    args=(i + 1, task_q, done, state),
                                    daemon=True)
                   for i in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    print("=" * 44)
    print(f"本次：新开 {state['ok']} / 跳过 {skip} / 失败 {state['fail']}，"
          f"共 {len(tasks)} 段")
    if state["failures"]:
        print("失败清单（重新运行本工具会自动只补这些）：")
        for t, e in state["failures"]:
            print(f"  {t}: {e}")
    else:
        print("全部就绪：开头描述语已随英文贴好，逐个标签页点生成即可。")


if __name__ == "__main__":
    main()
