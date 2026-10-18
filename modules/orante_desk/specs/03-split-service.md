# 规格 03 · 拆段服务 `orante/services/split.py`

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作（这就是当前目录）。先完整读 `specs/00-architecture.md`，再读本规格。只改本规格点名的文件；老流水线 `..\下载图片\` 里的任何文件**只读不改**，需要就拷到 `orante/legacy/`。写完自己跑一遍验收；最后把「改了哪些文件、怎么验的、没做完的、需要后端补的」写到 `specs/reports/<本文件名>.md`。全程用中文写注释和文字。不要问问题，拿不准的按 00 文件的规矩自己定并在报告里说明。


先读 `specs/00-architecture.md` 第 7 节。只写这个文件；老模块拷到 `orante/legacy/`（`flow_split.py`、`wenan_norm.py`、`wenan_words.txt`，以及 `grok_pipe.py` 里 `_pick_duration` 依赖的那几行逻辑——**照抄阈值，不改数值**）。

## 接口

```python
def split_task(job, task: dict, manifest) -> tuple[dict, list[dict]]:
    """返回 (split_rule, segments)。segments 元素是 models.Segment 的字段（s 从 1 起；image 先留空，编排面板再填）。"""
def preview(text: str, engine: str, rule: dict | None = None) -> list[dict]:
    """不落盘，给前端预演用：[{s, text, duration, words, chars}]"""
```

## 规则

- 规则来源：`config.get("split.flow.*")` / `config.get("split.grok.*")`，返回的 `split_rule` 把当时用的值原样记下来（含 `engine`）。
- **Flow**：`legacy.flow_split.split_copy(text, max_chars=rule['max_chars'])`；时长：词数 ≤ `dur_4s_max_words` → 4，≤ `dur_6s_max_words` → 6，否则 8；`allow_10s` 为真且词数超过 8s 档阈值（在 legacy 里找 8s 的上限，找不到就用 20 词）→ 10。
- **Grok**：段长按 `max_chars_15s`（默认 250）切；时长：字符数 ≤ `dur_6s_max_chars` → 6，≤ `max_chars_10s` → 10，否则 15。请去 `grok_pipe._pick_duration` 和 `表格自动下载器.py` 的 `split_max` 处核对现值，把**实际在用的数**回填到 `config.DEFAULTS`（改 `orante/config.py` 里 `split.grok.*` 的默认值是允许的，其它不要动）。
- 文案先过 `wenan_norm.normalize`（去粘连词、修标点空格），不调 AI。
- 短文案（`type == 'short'`）也走同一套切法（可能只有 1~3 段）。
- 段文本不能为空；总字数守恒（拆完 join 回去，去掉空白后要和原文一致），不一致就 `OranteError`。

## 验收

`tools/test_split.py`：拿 `下载图片\0-长文案\数据\` 里任意一个 `src.txt` 跑 Flow 和 Grok 两种规则，打印每段 `s / 时长 / 词数 / 前 40 字`，并断言字数守恒。
