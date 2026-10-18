# T03b 热修 — 点「新建项目」直接崩溃（0xC0000409）

> 在 `task/T03b-shell` 上继续。**先取证，再修**——不许凭猜改代码（这是第三次原生崩溃了，前两次都是取证之后一击命中）。

## 一、现象
Luca 真机：欢迎页点「新建项目 · 竖屏 1080×1920」→ 进程立刻退出，`[CRASH] Application exited with error code -1073740791`。
`-1073740791` = `0xC0000409` = **STATUS_STACK_BUFFER_OVERRUN / __fastfail**。在 PyQt 里最常见的两种成因：**① 无限递归把栈撑爆**；② Qt 的 C++ 断言/致命错误。**不是**普通 Python 异常（那会打 traceback 而不是这个码）。

## 二、取证（先做完这三步，把结果写进报告）

1. **读 faulthandler 日志**：`%LOCALAPPDATA%\CUTORA\logs\faulthandler.log`（便携模式则在 `<程序目录>\UserData\logs\`）。这是 `hotfix-forensics` 专门为这种时刻装的——全线程 Python 栈。原样贴进报告。同时贴启动那行 `STARTUP git=... branch=...`，确认崩的是哪个提交。
2. **无头复现**：`QT_QPA_PLATFORM=offscreen` 下构造 `MainWindow`，直接调 `mw._welcome_new(1080, 1920)`，看是否同样崩、或抛 `RecursionError`。能无头复现就好办——此后所有调试都无头做，不用麻烦 Luca。
3. **顺便试**另外三个入口是否同样崩：`_welcome_new_custom()`、`_welcome_drop([一个mp4])`、菜单 `文件 → 新建项目`。**范围能不能缩小，决定了根因在哪一层**（是 `_welcome_new` 这条链，还是 `show_editor` / `set_ui_mode` 这层）。

## 三、Claude 读代码后的怀疑方向（供参考，**以取证结果为准**）

`shell_stack` 是 `QStackedWidget`，欢迎页不会被销毁，所以「控件在自己的信号回调里被析构」这条排除了。剩下最可疑的是**递归**：

```
show_editor()            → set_ui_mode(...)
reset_shell_layout()     → set_ui_mode(self._ui_mode)
_welcome_new()           → _set_canvas_size() → refresh_ui()
                         → new_project() → update_ui_state() → show_editor() → set_ui_mode()
```
如果 `set_ui_mode()` 里 `dock.setVisible()/show()/hide()` 触发了 `visibilityChanged`，而它的槽又回头调 `set_ui_mode()` 或 `show_editor()`，就会无限递归 → 栈爆 → 0xC0000409。
**查法**：在 `set_ui_mode` / `show_editor` / `refresh_ui` / `update_ui_state` 入口各打一行计数日志，无头跑一次，看有没有同一函数被重入几百次。

另外注意 `_welcome_new` 的顺序：`_set_canvas_size()` 在 `new_project()` **之前**调用，而它会 `push_undo()` + `refresh_ui()` + `statusBar().showMessage()`——此时工程还不存在、docks 还是隐藏的。这层顺序本身也值得怀疑。

## 四、修法要求
- 给所有会被信号回调重入的方法加**重入保护**（一个 `self._in_mode_switch` 标志或 `QSignalBlocker`），不要靠"应该不会再调一次"。
- `_welcome_new` 的调用顺序改成先建工程、再切模式、最后显示编辑器；`_set_canvas_size` 在没有工程时不该 `push_undo` / `refresh_ui`。
- 修完这四个入口都要能走通：竖屏 / 横屏 / 自定义 / 拖入视频。

## 五、必须补的回归网（这条最重要）

**这次崩溃截图是看不出来的，而离屏截图只证明"画得对"，证明不了"点得动"。** 补一个无头交互冒烟测试 `tests/test_ui_actions_smoke.py`：

- `QT_QPA_PLATFORM=offscreen` 构造 `MainWindow`
- 遍历 `findChildren(QAction)`，逐个 `trigger()`；遍历欢迎页的每个按钮，逐个 `click()`
- 对会弹模态框的动作（文件对话框、`QMessageBox`、`QInputDialog`）用 monkeypatch 打桩返回默认值，**不要跳过它们**
- 断言：无异常、无 `RecursionError`、进程存活
- 白名单里只允许放「真的会退出程序 / 真的会写磁盘」的动作，并在测试里列出理由

以后每个动界面的任务，这条测试和 golden 一样是合并前提。

## 六、验收
- [ ] 四个新建入口在真机都能进编辑器
- [ ] `tests/test_ui_actions_smoke.py` 通过，且**在修复前跑它必须能复现这次崩溃**（先写测试、看它红，再修——报告里贴出红→绿的两次输出）
- [ ] `pytest` 全绿；`golden --all --repeat 3` PASS；`security_scan` 业务代码栏 0
- [ ] 仍停在分支，等 Luca 真机重验 + 25 条冒烟
