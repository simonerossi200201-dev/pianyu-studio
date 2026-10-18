# 右侧面板的布局由 Claude 亲自在做（2026-09-21 起）

Luca 的原话：「UI 的布局这方面 Grok 实在是不擅长，我希望你（Claude）能亲自调整软件右侧所有分页的布局。」

所以从这一批开始：

- **你可以**给右侧面板**加控件、接信号、写渲染逻辑**——走现有的挂载函数（`fx_fields.mount_*`、`INSPECTOR_LAYOUT` 表、`add_inspect_row`）。
- **你不许**改这些东西的尺寸与布局规则：`InspectorRow` / `CollapsibleSection` / `SliderSpin` 的高度宽度、`tokens.py` 里的行高控件高、`theme_dark.qss.j2` 里的 padding / min-height / font-size、右侧 dock 的最小最大宽度。
  这些 Claude 正在整体重做，你改了会被覆盖，而且两边会打架。
- 新加的控件长得不好看**没关系**，接对信号、改得动像素就行。外观 Claude 会统一收。
