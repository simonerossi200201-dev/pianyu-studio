# 配方格式

文件扩展名 `.cutorarecipe`（JSON）。schema 见 `resources/data/recipe.schema.json`。

- `schema_version`：必须为 `1`。
- `on_error`：`stop` 或 `skip`。
- `skip_if`：可选。`{command, field, equals, params}`，先跑 command，字段等于 equals 则跳过该步。
- 录制只记 `source=ui` 且 `undoable=True`；`app.*` 与 `media.select` 不录。
- `{media}` / `{out_dir}` 在套用到文件夹时替换。
- CLI：`python -m cutora recipe run <file> --input <folder> --out <dir> [--jobs N]`。

## 内置配方

### 横竖双版本

导入一条口播、打轴，导出成品到 out_dir。T08a 删除了空转的 profile；横竖双预设对象见 T08b。

步骤：
- `media.probe`
- `media.import`
- `subtitle.auto`
- `export.run`

### 只出 SRT 不渲染

无字幕则先自动识别（已有分段则 skip_if 跳过），再写成 SRT。不启动渲染子进程。

步骤：
- `subtitle.auto`
- `subtitle.export_srt`

### 竖屏字幕快出

导入一条口播，打轴，套字幕预设，导出竖屏成品。

步骤：
- `media.probe`
- `media.import`
- `subtitle.auto`
- `style.apply_subtitle_preset`
- `export.run`
