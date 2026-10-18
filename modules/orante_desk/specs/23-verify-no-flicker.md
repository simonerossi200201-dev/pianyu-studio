# 23 · 收片打开时的黑窗口闪屏 + 首帧校验性能

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/00-architecture.md`，
> 再读你自己刚写的 `specs/reports/21-collect-integrity.md`，再读本规格。这是 21 号规格的
> **回归修复**，功能不变，只修「怎么跑」。改前每个文件备份成 `*.bak-0909flick`。
> 写完把报告写到 `specs/reports/23-verify-no-flicker.md`。全程中文注释。不要问问题。

## 现象

用户点进 ⑤ 收片，屏幕上**闪出十几个黑色控制台窗口**，一个接一个快速出现又消失，
持续两三秒，像电脑卡死；闪完视频墙才出来。

## 根因（已定位，三条叠加）

### 1. `subprocess.run` 没有 `CREATE_NO_WINDOW` —— 这是闪窗的直接原因

Orante 是 **pythonw 起的（无控制台）**（`orante/main.py:47` 的注释写明了）。
在这种进程里每 `subprocess.run` 一个控制台程序，Windows 就会**新建一个控制台窗口**，
黑框一闪。仓库里别的地方本来就做对了：

    app/jobs.py:58        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    orante/server.py:105  flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0

**漏掉的有两处**：

| 文件 | 行 | 调的什么 |
|---|---|---|
| `orante/services/verify.py` | 95 `_ffmpeg_gray16` | 抽首帧算 aHash（21 号新加的） |
| `orante/services/checks.py` | 202 `loudness` | 响度检测（老代码，一直有这毛病） |

### 2. 一次要跑二十来次 ffmpeg

`review.get_review()`（`review.py:96~103`）**同步**调 `verify.light_check()`。
T0013 有 11 段：11 个视频抽首帧 + 每段的配图各抽一次 ≈ **20 次 ffmpeg**，
正好对上「十几次黑窗」。

视频的 `frame_ahash` 会写进 manifest 的 take，**但配图的哈希只进了内存
`_CACHE`（`verify.py:118`），进程一重启全丢**，所以每次开 Orante 第一次进收片都要重跑一遍。

### 3. 它是同步的，面板被它堵着

`light_check` 跑完 `get_review` 才返回，所以「闪完才出画面」不是错觉，是真的在等。

## 要做的

### A. 统一一个「不弹窗」的子进程入口（根治闪屏）

新建 `orante/services/procutil.py`：

    import os, subprocess

    def _hidden_kw() -> dict:
        """Windows 下让子进程完全不弹控制台窗口。非 Windows 返回空。"""
        if os.name != "nt":
            return {}
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE          # 0
        return {
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            "startupinfo": si,
        }

    def run_quiet(cmd, timeout=None, **kw):
        """跑一个命令行工具，绝不弹窗。capture_output 默认开，stdin 掐死
        （ffmpeg 偶尔会等键盘输入，pythonw 下没有 stdin 会直接挂住）。"""
        kw.setdefault("capture_output", True)
        kw.setdefault("stdin", subprocess.DEVNULL)
        kw.update(_hidden_kw())
        return subprocess.run(cmd, timeout=timeout, **kw)

**`creationflags` 和 `startupinfo` 两个都要给**，不要只给一个 —— 两条路径在不同的
Windows 版本 / 不同的父进程（pythonw / python / 从 .bat 起）下表现不一样，一起给才稳。

然后把**全仓所有跑 ffmpeg / ffprobe 的地方**换成 `run_quiet`：
`orante/services/verify.py:95`、`orante/services/checks.py:202`。
换完 `grep -rn "subprocess.run\|subprocess.Popen" orante/ app/ --include=*.py`
自己过一遍，凡是跑外部 exe 又没带 `creationflags` 的，一律补上（`app/checks.py`
里那份老的也顺手改掉，别留两套行为）。

### B. 配图哈希改用 Pillow，直接砍掉一半子进程

venv 里已经有 **pillow 12.3.0**（`.venv\Lib\site-packages\PIL`）。
静态图片根本没必要开 ffmpeg：

    from PIL import Image
    im = Image.open(path).convert("L").resize((16, 16), Image.BICUBIC)
    raw = im.tobytes()        # 256 字节，喂给现有的 _ahash_raw

`ahash_image()` 改成 **优先 Pillow，Pillow 失败才退回 ffmpeg**。
好处有两个：少一半进程；而且 `ffmpeg` 找不到时 4b 不再整条静默跳过
（现在 `verify.py:441` 那句 `ahash_image(img, ffmpeg) if (img and ffmpeg) else None`
在没有 ffmpeg 时会让首帧比对彻底失效，等于闸 4b 白做）。

**视频首帧仍然要用 ffmpeg**，但顺手把 `-ss 0` 去掉（在 `-i` 前面写 `-ss 0` 没有意义），
并加 `-an -sn -dn`（不解音频/字幕/数据轨）再快一点。

### C. 哈希结果落盘缓存，重启后不再重算

新建 `data/ahash-cache.json`，结构：

    {"<绝对路径>": {"size": 1234, "mtime": 1757400000, "hash": "……64位十六进制"}}

`size` 或 `mtime` 对不上就重算。配图哈希走这里；视频哈希继续写 manifest 的 take，
**但要连 `bytes` 和 `mtime` 一起记**，现在 `verify.py:410` 的 `need_hash` 只比 `bytes`，
换成同样大小的另一个文件就会漏判。写文件用「先写 `.tmp` 再 `os.replace`」，
别让并发的 4 个 Orante 窗口写坏它。

### D. sha256 先比大小，撞了再算全文

`verify.py` 现在对每段都算完整 sha256 —— 11 个文件约 150 MB 读盘。
改成两段式：**先按文件大小分组，只有大小相同的才去算 sha256**。
拿错片必然是同一份字节、大小必然相同，所以判定能力一点不减，IO 却几乎归零。

### E. 校验挪到后台，面板秒开

`review.get_review()` **不再同步跑 `light_check`**。改成：

1. `get_review` 只读**已有的凭证**（manifest take 里的 `verdict` / `frame_dist` / `sha256`
   和 `data/ahash-cache.json`），立刻返回，面板马上出画面。
2. 同时丢一个后台线程跑缺的那部分（守护线程，或复用现有的 job/线程池 —— 按 00 文件的规矩定）。
   **同一个任务同一时间只允许一个校验线程**，用一把锁挡住重复点击来回切面板。
3. 算完 `events.emit("review.verified", {"tid": tid, "results": [...]})`，
   前端在 SSE 里收到就地更新旗子，不要整页重刷、不要打断正在播放的视频。
4. 前端在校验没跑完时，顶栏显示一行淡淡的 `正在校验 3/11…`（i18n `review.verifying`），
   跑完淡出。**第一次进入确实要算二十来次，让用户看得见在干活，比静默卡住好。**

### F. 别在无关的时候跑

只有**这个任务的 clips 目录里有文件变动**（新落盘 / 大小或 mtime 变了）时才需要重算。
纯粹来回切面板、只是看看，全部命中缓存 → **零个子进程**。
做完自己验：开 Orante，进收片，退出去，再进来 —— 第二次必须一个 ffmpeg 都不起。

## 验收（每条都要真跑，写进报告）

1. pythonw 起 Orante，进 ⑤ 收片（T0013，11 段）：**一个黑窗口都不许闪**。
2. 同上，面板内容**立刻**出来，不等校验；顶栏出现 `正在校验 n/11` 并在结束后消失。
3. 退出收片再进来：日志里 ffmpeg 调用次数 = 0（在 `_ffmpeg_gray16` 里加一句 debug 日志计数，验完可以留着）。
4. 重启 Orante 再进收片：因为有 `data\ahash-cache.json`，配图哈希不重算；
   只有视频 take 里缺 `frame_ahash` 的才算。
5. 21 号规格的判定结果**不能变**：T0013 仍然是 s004/s006/s008/s010/s011 报 `wrong_clip`，
   s001/s002/s003/s005/s007/s009 通过。**这是回归底线**，先跑 21 的验收第 1 条再交。
6. 点「运行体检」（会跑响度那条）：同样一个黑窗口都不许闪。
7. `grep -rn "subprocess.run\|subprocess.Popen" orante/ app/ --include=*.py` 的结果贴进报告，
   逐条说明每个调用点是走了 `run_quiet` 还是本来就带了 flags。
