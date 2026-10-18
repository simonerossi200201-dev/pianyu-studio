# 48 · ③ 编排文案列显示全 + 文案规范两处误改

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作。先读 `specs/22-search-and-prompt-edit.md`
> 和它的报告，再读 `ui/js/panels/track.js`（第 1112~1147 行的 `.btable` 表格）、
> `ui/css/app.css`（第 677~690 行）、`orante/legacy/wenan_norm.py`、`orante/services/split.py`，
> 再读本规格。**先做完 47。**
> 改前备份 `*.bak-0911text`。报告写到 `specs/reports/48-track-text-full-and-norm-fix.md`。中文注释。

## 现象

③ 编排底部的段表里，「文案」一列只显示前 90 个字符，之后**硬截断、没有省略号**
（`"…a glacie"`、`"But o"` 这种半个词收尾），用户以为文案被裁掉了。

## 结论：数据没丢，只是显示的问题

查了 T0015 的 `manifest.json`：12 段 `segments[].text` 长度 237/239/238/213/250/111/203/221/215/238/233/72，
拼起来和 `copy.text` 骨架一致。`split.py::_assert_conserved` 在每次拆段后都做「去空白字数必须相等」
的断言，字数对不上就整段报错，所以拆段**不可能**吞字。

截断在 `ui/js/panels/track.js:1138-1139`：

    <span v-if="ts.q" v-html="ts.highlight((sg.text || '').slice(0, 90))"></span>
    <template v-else>{{ (sg.text || '').slice(0, 90) }}</template>

`slice(0, 90)` 是纯显示层的截断，还连省略号都没加。

**顺带发现**：拆段前的 `wenan_norm.normalize` 有两处把原文改坏了（只动空格，骨架不变，所以断言放行）：

1. `knoweth` → `know eth`（T0015 第 2 段）。`_best_split` 把词频表里没有的古英语动词
   `knoweth` 拆成了 `know` + `eth`，两半在词表里都「够常用」。
   `_SUFFIX_NOT_WORD` 收了 `est` 却没收 `eth`。KJV 经文里 `knoweth / cometh / doeth / saith / hath`
   这一类会反复中招，Grok 读出来就成了「know eth」。
2. `only." So` → `only. " So`，`sorrows." Look` → `sorrows. " Look`。
   `_GLUED_PUNCT` 的前瞻把直引号 `"` 当成了**开引号**：`[”"’')]*` 回溯成空，
   `.` 后面紧跟的闭引号 `"` 被前瞻 `[A-Za-z“"‘'(\[]` 吃掉，于是在 `.` 和 `"` 之间补了个空格。

## 要做的

### A. 文案列显示全（`track.js` + `app.css`）

- 去掉两处 `.slice(0, 90)`，`sg.text` 全文交给模板；`ts.highlight` 照旧作用在全文上，
  搜索命中不受影响（spec 22 的 `ts-hit / ts-flash` 定位靠 `tr[data-s]`，不动）。
- `.panel-track .btable .txt` 默认**折叠成 2 行**：
  `display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
   white-space:normal;word-break:break-word`，末尾自然出省略号。
- 整行 `title="全文"`，鼠标悬停就能看全文；单击 `.txt` 单元格切换展开/折叠
  （给 `td` 加 `class="txt open"`，`open` 时取消 line-clamp）。展开状态存在组件内，
  切换任务时重置。`.w` 警告行（补拍/锁定提示）不参与折叠，仍在文案下方单独一行。
- `sn / pp` 两列保持 `white-space:nowrap`，文案列 `width:100%` 吃掉剩余宽度，
  别让长文案把时长列挤没。
- 不改 `manifest.json`、不改任何后端。

### B. `wenan_norm` 两处修正（`orante/legacy/wenan_norm.py`）

1. `_SUFFIX_NOT_WORD` 加 `eth`、`th`、`st`（古英语第三/第二人称词尾）。
   再加一张**古英语白名单** `_ARCHAIC_WORDS`：
   `knoweth cometh doeth goeth saith hath doth shalt wilt thou thee thy thine ye art
    knowest sayest hast dost canst`，`scan_glued` 里命中白名单的 token 直接跳过（视同已知词）。
2. `_GLUED_PUNCT` 的前瞻改成：字母、中文开引号 `“‘`、括号，或者**直引号后面紧跟非空白**
   （`["'](?=\S)`）——这样 `only." So` 里的 `"` 后面是空格，就不再被当成开引号。
   `Ecclesiastes 4:9` 这类数字保护（注释里写的）照旧。

### C. 测试（`tools/test_wenan_norm.py`，新建）

- `normalize('knoweth no man')` 原样不动；`normalize('cometh')`、`'saith'`、`'hath'` 不拆。
- `normalize('my Father only." So no')` 原样不动；`normalize('sorrows." Look')` 原样不动。
- 仍然要拆的：`normalize('SaulJoseph')` → `Saul Joseph`；`normalize('end.Then')` → `end. Then`；
  `normalize('Ecclesiastes 4:9')` 原样不动。
- `skeleton` 不变式在以上每一条都成立。
- 前端：`tools/test_track.py`里加一条断言 `track.js` 不再含 `slice(0, 90)`。

## 验收

1. ③ 编排打开 T0015：每段文案显示 2 行带省略号；悬停看到全文；单击展开成全文，再单击折回；
   时长列没有被挤掉。
2. 搜索框输入 `glacier`：命中高亮仍在，定位/闪烁照旧。
3. 对 T0015 重新「拆段」（或用预演接口 `split.preview` 喂 `copy.text`）：
   第 2 段出现 `knoweth` 而不是 `know eth`；全文里没有 `. "` 这种「句号 空格 闭引号」。
4. `python -m pytest tools/test_wenan_norm.py tools/test_split.py tools/test_track.py tools/test_spec22.py` 全过；
   spec 22 的搜索测试不回退。
