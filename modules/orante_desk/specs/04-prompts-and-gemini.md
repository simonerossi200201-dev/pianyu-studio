# 规格 04 · 提示词服务 + Gemini 客户端 `orante/services/prompts.py`、`orante/services/gemini.py`

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作（这就是当前目录）。先完整读 `specs/00-architecture.md`，再读本规格。只改本规格点名的文件；老流水线 `..\下载图片\` 里的任何文件**只读不改**，需要就拷到 `orante/legacy/`。写完自己跑一遍验收；最后把「改了哪些文件、怎么验的、没做完的、需要后端补的」写到 `specs/reports/<本文件名>.md`。全程用中文写注释和文字。不要问问题，拿不准的按 00 文件的规矩自己定并在报告里说明。

## A. `services/prompts.py` —— 四块提示词，明文、可改、可恢复默认

### A1. 默认值从哪来（一个字不改地搬）

| 块 | 键（settings） | 默认值来源 |
|---|---|---|
| 描述语提示词 | `prompts.desc` | `..\下载图片\描述语生成器.html` 里 `const DEFAULT_PROMPT = \`...\`` 的完整内容（v7·0905） |
| 运镜规则 | `prompts.camera` | `..\下载图片\core\flow_shots.json` 里这几块：`安静运镜_多样`、`安静运镜_多样_睁眼`、`人物坐姿_安静`、`人物原地_安静`、`镜头禁令_点名版`、`运动强制_安静`、`人群保留_安静`、`空镜卡池`、`画外音指令`；以 JSON 对象形式整体存，键名保留中文 |
| 固定结语 | `prompts.tail` | `flow_shots.json["固定结语"]` 整个对象（去掉以 `_` 开头的说明键；说明另存成 `prompts.tail_notes` 只读展示） |
| 问题句子库 | `prompts.problem` | 见 A3 |

写一个 `defaults()` 函数：**优先**从 `orante/legacy/prompt_defaults.json` 读（你要生成这个文件——把上面四块抽出来写成一个 JSON，这样分发版不依赖老目录）；生成它的脚本放 `tools/extract_prompt_defaults.py`，能从 `paths.find_legacy_root()` 重新抽一次。

### A2. 接口

```python
def get_block(name: str) -> dict | str      # 用户改过的，没改过就默认
def set_block(name: str, value) -> None
def reset_block(name: str) -> None
def all_blocks() -> dict                    # {desc, camera, tail, problem, tail_notes, versions:{...}}
def build_final_prompt(m: Manifest, seg: dict) -> str
    """成品提示词。调用 legacy.flow_shots.build_prompt(desc, card, eye, scene, seg_text, seg_idx, n_segs, voice_lock)
       但把 flow_shots 读的卡库替换成用户当前的 prompts.camera/tail（flow_shots.load_shots 支持传入 shots 字典——去看它的签名；
       如果只能读文件，就把合并后的字典写到 data/tmp/flow_shots.effective.json 再让它读）。
       最后把 {停顿强调} 用 A3 的逻辑填掉。"""
def find_problem_sentences(text: str) -> list[str]     # A3
```

### A3. 问题句子库（跨语言、可无限加、可让 Gemini 补）

结构：`prompts.problem = {"patterns": [{"kind": "regex"|"phrase", "value": "...", "lang": "en"|"zh"|"it"|"*", "note": "...", "source": "default"|"user"|"gemini"}], "template": "包括{原话}这句话也要读"}`

默认 patterns = `flow_shots.py` 里 `_PAUSE_RES` 那 4 条正则原样（kind=regex，note 用源码注释里那句话）。
`find_problem_sentences(text)`：按 `flow_shots._sentences` 的断句方式切句；`regex` 用 `re.search(I)`，`phrase` 用去空格、大小写不敏感的**包含**匹配；返回命中的**原句**（一字不改），去重保序。
`gemini_scan_problems(text) -> list[str]`：调 B 的 `ask_text`，提示词固定写死在代码里（中文）：让模型只输出 JSON 数组，列出「文案里要求观众/人物停顿、等待、静默、输入某词、评论某词、点赞、分享等**会被视频模型当成拍摄指令而不念出来**的原句」，语言不限。结果每条作为 `kind=phrase, source=gemini` 加进库（不重复）。

## B. `services/gemini.py` —— Gemini REST 客户端

```python
class Gemini:
    def __init__(self, api_key: str | None = None, model: str | None = None)   # 默认取 config gemini.api_key / gemini.model
    def ask_text(self, prompt: str, system: str = "", temperature=0.4, json_mode=False) -> str
    def ask_image(self, prompt: str, image_path: Path, system: str = "", json_mode=False) -> str    # 多模态：图 + 文
    def ask_images(self, prompt: str, image_paths: list[Path], ...) -> str
    def list_models(self) -> list[str]
```

- 端点：`https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=...`，用 `requests`（已在 requirements）。图片 inline_data base64，超过 4MB 先用 Pillow 缩到长边 1280。
- 429 / 503：指数退避重试 5 次（1,2,4,8,16 秒），日志一行。
- `json_mode=True` 时设 `generationConfig.response_mime_type = "application/json"`，并在返回前 `json.loads` 校验，失败抛 `OranteError("Gemini 没有返回合法 JSON", 前 200 字)`。
- Key 为空 → `OranteError("还没填 Gemini API Key", "设置 → Gemini → api_key。到 aistudio.google.com 免费申请。")`。
- `config.get("gemini.mode") == "page"`（referrer 锁定的 Key）：**这一版先不实现**，抛 `OranteError("page 模式尚未实现", "把 Key 换成不限制 referrer 的普通 Key")`，报告里注明。
- 并发由调用方管，这里不做线程。

## C. 验收

`tools/test_prompts.py`：
1. `all_blocks()` 四块都有默认值，`desc` 以「【提示词版本 v7」开头。
2. `find_problem_sentences("Stay for 7 seconds and type Amen. God bless you.")` → `["Stay for 7 seconds and type Amen."]`。
3. 随便拿一个老任务的 manifest（`..\下载图片\0-长文案\数据\<泳道>\<日期>\Tik0NN\manifest.json` 是**老格式**，不是 v2——所以这一条改成：手工构造一个 v2 Manifest，一张图 + 一段，调 `build_final_prompt`，打印结果，肉眼确认含描述语正文 + 运镜卡 + 结语 + 台词四部分）。
4. 有 Key 时 `Gemini().ask_text("回复 OK")` 返回含 OK；没 Key 时抛 OranteError 且信息里有「设置」。
