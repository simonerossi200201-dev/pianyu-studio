# -*- coding: utf-8 -*-
"""设置：SQLite settings 表 + 默认值。取不到就给默认。

所有「用户可调」的东西都在这里登记默认值，前端设置页按 DEFAULTS 的键渲染。
"""
from __future__ import annotations

from . import db, paths

DEFAULTS: dict = {
    # 工作区
    "workspace.root": str(paths.DEFAULT_WORKSPACE),
    "workspace.by_date": True,            # 根\日期\版本
    "workspace.ask_each_batch": True,     # 每批开跑前弹一次「存哪」
    # 自动归档：做完超过 N 天的 finished 任务导出到批次目录并从库里移出
    "archive.auto_on": False,             # 默认关，用户自己开
    "archive.auto_days": 7,               # 完成超过 N 天
    # 拆段（默认值 = 现脚本正在用的）
    "split.flow.max_chars": 145,
    "split.flow.dur_4s_max_words": 8,
    "split.flow.dur_6s_max_words": 13,
    # "split.flow.allow_10s" 已删（规格 84）
    "split.no_copy_duration.flow": 8,
    "split.no_copy_duration.grok": 10,
    # 规格 86：无文案任务的静音提示（prompts 可编辑块 silent）
    "prompts.silent": (
        "画面中的人物全程不说话、没有对白，嘴唇保持闭合；可以有自然的表情、眨眼和细微的动作。\n"
        "No dialogue. The person does not speak; lips stay closed. Natural expressions and subtle movements only."
    ),
    # Grok：段长 250 = 表格 config.json 正在用的 split_max（15s 模式）
    # 时长分界照抄 grok_batch.pick_duration 默认折算（17 字/秒 × 1.1 容差）
    "split.grok.max_chars_10s": 187,   # 10*17*1.1；表格 10s 档 split_max 是 170
    "split.grok.max_chars_15s": 250,
    "split.grok.dur_6s_max_chars": 112,  # 6*17*1.1
    # Gemini
    "gemini.api_key": "",
    "gemini.model": "gemini-3.5-flash-lite",
    "gemini.mode": "direct",              # direct | page（referrer 锁定 Key 走隐藏页）
    "gemini.concurrency": 4,
    # 多模态接口池：描述语线程数跟着 key 走，再卡这个总上限
    "ai.max_workers": 16,
    # 多镜头扩图
    "flow.image_model_key": "",   # 空 = GEM_PIX_2（Nano Banana Pro）；填 NARWHAL 改用 Nano Banana 2
    "flow.allow_paid": False,     # true 才允许提交白名单外的（会扣额度的）模型
    "flow.credit_guard": True,    # 余额下降就停提交；探针证明 nzlxg 不是额度后改 false（只记不拦）
    "flow.human_warmup": True,    # 规格 96：标签页第一次提交先真点真打字真点生成（暖场），后面的段照旧走接口直连
    "expand.min_images": 6,
    "expand.per_round": 4,
    "expand.flip_replacements": True,
    "expand.dup_dist": 40,            # aHash 汉明距离 < 此值判雷同。T0014 生成时 8–26 是几乎同一张，60+ 已是不同裁切；90 会误伤
    "expand.no_frontal": True,        # 关掉正脸：不生成人物正对镜头且占画面大的机位
    # 配图轨默认
    "track.hold": 3,
    "track.jitter": 1,
    "track.no_adjacent_repeat": True,   # 间隔排列：同一张图不连着两段
    # 图片来源
    "images.rename_on_drive": False,  # 有 OAuth 时才在 Drive 上改【已使用】前缀
    # 本地文件夹落到任务目录：copy 复制 / copy_mark 复制并加【已使用】 / move 搬走
    "images.local_mode": "copy",
    "images.local_move_confirmed": False,  # 第一次选「移动」时确认过就记住
    # 界面
    "ui.lang": "zh",
    # 规格 92：成片语种（提示词按它改写；和界面语言 ui.lang 是两回事）
    "content.lang": "",
    "ui.open_mode": "window",             # window(pywebview) | browser
    "ui.port": 9400,
    "ui.current_task": "",                # 顶栏胶囊 / 各面板共用的当前任务
    "track.strip_view": "pool",           # 编排中间条：pool 图池 / shots 镜头序列
    # 检查台密度：紧凑 / 舒适 / 大图；zoom 40~200，跟着预设走
    "ui.bench.density": "comfy",
    "ui.bench.zoom": 100,
    # 规格 89：悬浮图标。窗口全部最小化 → 屏幕上留一个始终置顶的小图标
    "ui.float_icon": True,
    "ui.float_icon.size": 96,             # 40~256 像素，滚轮也能改
    "ui.float_icon.edge_hide": True,      # 拖到屏幕边就缩成一条，鼠标碰到再出来
    "ui.float_icon.pos": "",              # "x,y"，图标自己记，设置页不显示
    # 引擎
    "engine.flow.mode": "api",            # api | dom
    "engine.flow.model": "Veo 3.1 - Lite [Lower Priority]",
    "engine.flow.max_concurrent": 5,
    "engine.grok.max_concurrent": 4,
    # 规格 91：同一个账号让几个任务一起出片。默认关（一个账号一次一个任务）。
    # 打开后在飞的段数仍由账号并发上限卡死，只是位子由这几个任务轮流占。
    "engine.pack.multi_task": False,
    "engine.pack.max_tasks": 3,           # 打开后最多几个任务共用一个账号（2~5）
    # 冷却期间每隔几分钟真的试提交一次；成功就当场解除。0 = 不探路，等满为止
    "engine.flow.throttle_probe_min": 4,
    # 规格 94：两次提交之间至少隔几秒（带抖动）。连打最容易被 Google 判成「异常活动」。0 = 不等
    "engine.flow.submit_gap_sec": 2,
    # 规格 98：本地 AI 算力网关（LTX-2.5 22B 视频 / FLUX.2 4B 生图）。
    "engine.studio.enabled": True,            # 总开关：默认开，支持随时手动切本地 AI 或自动兜底
    "engine.studio.fallback": True,           # 优先开关（默认开）：Flow 拒图时优先唤醒本地视频模型 LTX（不重新生图，自动按本地/Grok规则重分段）
    "engine.studio.url": "http://127.0.0.1:7870",
    "engine.studio.launcher": "",             # 连不上时自动拉起的脚本。空 = 找 D:\0_LTX\启动本地算力网关.bat；填 off 不自动启动
    "engine.studio.max_inflight": 2,
    "engine.studio.quality": "balanced",      # fast 极速 | balanced 均衡 (standard) | high 高清 (hq) | best 精品 (hq)
    "engine.studio.lock_last": "auto",        # auto 按【镜头】行判断 | always | never
    "engine.studio.match_flow_spec": True,    # 下载后统一成 Flow 规格（画幅跟图走、24fps、AAC 48k）
    # 生成方式：新任务沿用上一次。first_frame 逐段首帧（默认）/ anchor_bridge 锚点+接片
    "generate.mode": "first_frame",
    # 规格 95：ffmpeg 位置。空 = 自动找（Orante/bin → pip 装的 imageio-ffmpeg → PATH → 常见位置）
    "tools.ffmpeg": "",
    # 规格 95：尾帧接龙的描述语。never 一直沿用上一段（默认）/ always 每张尾帧都重写 / auto 变化大才重写
    "chain.desc_policy": "never",
    # 模板套装：当前生效的那份；切换前是否先把改动存回去
    "suite.current": "",
    "suite.autosave_on_switch": True,
    # 多开：配置世代。任何用户向的 config.set / 套装切换都 +1
    "_rev": 0,
    "_rev_hint": "",
    "_rev_port": 0,               # 最后一次涨世代的窗口端口；本窗口自己改的不弹条
}

# 程序内部状态，设置页不给人调。prompts.* 有自己的编辑区，同样跳过。
HIDDEN_KEYS = frozenset({
    "ui.current_task",
    "setup.done",
    "suite.current",
    "_rev",
    "_rev_hint",
    "_rev_port",
    "archive.last_run",
    "descqueue.owner_port",
    "descqueue.owner_pid",
    "descqueue.heartbeat",
    "images.local_move_confirmed",
    "flow.last_throttle",
    "flow.network_throttle",
    "ui.float_icon.pos",
    "content.lang",
    "content.lang_applied",
    "prompts.lang_backup",
    "studio.pending_tasks",       # 规格 98：等着转交本地模型的任务（ticker 每分钟再试）
})

# 这些键变了不涨 _rev：内部状态、心跳，别让别的窗口一直弹「配置改了」。
_REV_SKIP = HIDDEN_KEYS


def is_hidden_key(key: str) -> bool:
    """设置页渲染跳过：内部状态 + 提示词块。"""
    k = str(key or "")
    return k in HIDDEN_KEYS or k.startswith("prompts.")


def get(key: str, default=None):
    r = db.one("SELECT value FROM settings WHERE key=?", (key,))
    if r is None:
        return DEFAULTS.get(key, default)
    return db.unj(r["value"], DEFAULTS.get(key, default))


def _writer_port() -> int:
    """本窗口端口。locks.OWNER_PORT 由 instance.configure 写入。"""
    try:
        from . import locks
        return int(locks.OWNER_PORT or 0)
    except Exception:
        return 0


def bump_rev(hint: str = "") -> int:
    """settings._rev +1。hint 给别的窗口顶栏提示用（比如 切到了「温暖布道」）。"""
    try:
        n = int(get("_rev") or 0)
    except (TypeError, ValueError):
        n = 0
    n += 1
    db.run("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", ("_rev", db.j(n)))
    db.run("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
           ("_rev_hint", db.j(hint or "")))
    db.run("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
           ("_rev_port", db.j(_writer_port())))
    return n


def set(key: str, value, *, touch_rev: bool | None = None) -> None:  # noqa: A001
    db.run("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, db.j(value)))
    skip = key in _REV_SKIP or str(key).startswith("descqueue.")
    if touch_rev is None:
        touch_rev = not skip
    if touch_rev:
        bump_rev()


def all() -> dict:  # noqa: A001
    out = dict(DEFAULTS)
    for r in db.rows("SELECT key,value FROM settings"):
        out[r["key"]] = db.unj(r["value"])
    return out


def reset(key: str) -> None:
    db.run("DELETE FROM settings WHERE key=?", (key,))
