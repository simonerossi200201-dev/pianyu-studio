# 片语 Studio 命令参考

由 `tools/gen_api_docs.py` 从命令注册表生成。不要手改。

本地 API：`POST /v1/commands/{name}`，Header `Authorization: Bearer <token>`。
CLI：`python -m cutora cmd <name> --json '{...}'`。

## ai

### `ai.matte.object` — AI 抠像对象增删

op=add 给图层新建一个要抠的对象（最多 8 个，各有颜色）；op=remove 删掉 object_id 及它在所有帧上的提示点。结果缓存里该对象的 mask 由界面在后台清理。锁定轨拒绝。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `element_index` | integer | 可选。覆层里 elements 的下标（可选） |
| `node_id` | string | 可选。效果节点图里的节点 id（可选；给了就改那个节点） |
| `op` | string | 必填。add / remove |
| `object_id` | integer | 可选。op=remove 时要删的对象 id |
| `name` | string | 可选。op=add 时的名字（可选） |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd ai.matte.object --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "op": "add", "object_id": 0, "name": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "op": "add", "object_id": 0, "name": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/ai.matte.object
```

### `ai.matte.prompt` — AI 抠像提示点

在源帧 frame 上 op=add 一个提示（prompt={obj,x,y,label} 正点 label=1 / 负点 0，或 {obj,box:[x0,y0,x1,y1]}，坐标为源帧归一化 0–1）；op=clear 清掉这一帧（object_id 可选）的提示。只改数据，分割由界面后台任务做。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `element_index` | integer | 可选。覆层里 elements 的下标（可选） |
| `node_id` | string | 可选。效果节点图里的节点 id（可选；给了就改那个节点） |
| `op` | string | 可选。add / clear |
| `frame` | integer | 必填。源帧号 |
| `prompt` | object | 可选。op=add 时的提示 |
| `object_id` | integer | 可选。op=clear 时只清这个对象（可选） |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd ai.matte.prompt --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "op": "add", "frame": 0, "prompt": "", "object_id": 0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "op": "add", "frame": 0, "prompt": "", "object_id": 0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/ai.matte.prompt
```

### `ai.matte.session` — AI 抠像会话

op=create 给图层建结果缓存会话并建第一个对象（已有则原样返回，可带 model）；op=clear 清空全部提示点和缓存结果（保留对象和设置）；op=detach 把整个 AI 抠像从图层上拿掉并删缓存。锁定轨拒绝。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `element_index` | integer | 可选。覆层里 elements 的下标（可选） |
| `node_id` | string | 可选。效果节点图里的节点 id（可选；给了就改那个节点） |
| `op` | string | 可选。create / clear / detach |
| `model` | string | 可选。op=create 时指定模型 id（可选） |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd ai.matte.session --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "op": "create", "model": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "op": "create", "model": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/ai.matte.session
```

### `ai.matte.set` — 设置 AI 抠像

合并 patch 到视频片段的 AI 抠像设置：enabled、model、feather（画布像素）、shrink（+收缩/−扩张）、refine（导向滤波精修边缘）、merge（相邻「保留」对象合成一整块去接缝，默认开）、view(comp/matte/overlay)、objects（按 id 改 name/color/enabled/mode：add 保留 / subtract 挖掉）。不改提示点和会话。锁定轨拒绝。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `element_index` | integer | 可选。覆层里 elements 的下标（可选） |
| `node_id` | string | 可选。效果节点图里的节点 id（可选；给了就改那个节点） |
| `patch` | object | 必填。要改的字段 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd ai.matte.set --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "patch": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "patch": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/ai.matte.set
```

## app

### `app.free_vram` — 读取空闲显存

用 nvidia-smi 读取当前显卡显存总量、已用与空闲（GB），不加载 torch，不改任何状态。失败常见原因：没有 NVIDIA 驱动或 nvidia-smi 不在 PATH。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

CLI：

```
python -m cutora cmd app.free_vram --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/app.free_vram
```

### `app.gpu_info` — 读取 GPU 信息

调用唯一的 GPU 判断入口 detect_gpu_config，返回设备、compute_type 与判断原因。不改设置。失败常见原因：未安装 PyTorch 或驱动不可用。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

CLI：

```
python -m cutora cmd app.gpu_info --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/app.gpu_info
```

### `app.settings.get` — 读取应用设置

读取 app_settings.json 的当前内容。返回前会剥离密钥字段（*_key/*_token/*_secret），密钥永远不会出现在结果里。失败常见原因：设置文件损坏。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `key` | string | 可选。只返回这一项；省略则返回全部非密钥设置 |

CLI：

```
python -m cutora cmd app.settings.get --json '{"key": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"key": ""}' http://127.0.0.1:47321/v1/commands/app.settings.get
```

### `app.settings.set` — 写入应用设置

把一个非密钥设置项写入 app_settings.json。密钥字段会被拒绝，必须走 keyring。失败常见原因：key 为空、试图写入 *_key/*_token/*_secret。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `key` | string | 必填。设置键名 |
| `value` | object | 可选。任意 JSON 值 |

CLI：

```
python -m cutora cmd app.settings.set --json '{"key": "", "value": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"key": "", "value": ""}' http://127.0.0.1:47321/v1/commands/app.settings.set
```

### `app.theme.list` — 列出主题

列出内置调色板（roma-gold / midnight-blue / amethyst / rose / pine / graphite）的 id 与中英文名。不切换主题。失败常见原因：调色板 JSON 缺失。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

CLI：

```
python -m cutora cmd app.theme.list --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/app.theme.list
```

### `app.theme.set` — 切换主题

把当前进程的调色板切到 theme_id，重建 QSS 并写回 app_settings。未知 id 会回退到 roma-gold。必须在图形界面进程里执行。

- 可撤销：否
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `theme_id` | string | 必填。内置主题 id |

CLI：

```
python -m cutora cmd app.theme.set --json '{"theme_id": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"theme_id": ""}' http://127.0.0.1:47321/v1/commands/app.theme.set
```

### `app.version` — 读取版本

返回当前片语 Studio 的应用名称与版本号，不改动任何工程、文件或设置。给机器人探测「软件是否就是这一套」用。失败常见原因：安装不完整。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

CLI：

```
python -m cutora cmd app.version --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/app.version
```

## asr

### `asr.detect_language` — 检测语言

用已安装的识别模型听音频或视频的前 30 秒，返回语言代码、置信度、是否低于 0.6、是否实验性语言，以及给界面用的低置信提示文案。不写工程、不改设置。失败常见原因：media 路径无效、识别模型未安装。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：是

| 参数 | 类型 | 说明 |
|---|---|---|
| `media` | string | 可选。音频或视频文件路径 |
| `media_id` | integer | 可选。当前工程媒体下标；有窗口且未给 media 时用 |

CLI：

```
python -m cutora cmd asr.detect_language --json '{"media": "", "media_id": 0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"media": "", "media_id": 0}' http://127.0.0.1:47321/v1/commands/asr.detect_language
```

### `asr.models.delete` — 删除模型

删除指定 id 的本地模型文件。不可恢复。API/MCP 必须带 confirm=true。对齐 en/it 只删对应权重文件，不拆公共目录里的另一份。失败常见原因：id 未知、文件正在使用。

- 可撤销：否
- 危险：是（API 需 confirm=true）
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | string | 必填。模型 id |

CLI：

```
python -m cutora cmd asr.models.delete --json '{"id": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"id": ""}' http://127.0.0.1:47321/v1/commands/asr.models.delete
```

### `asr.models.download` — 下载模型

从 huggingface.co 下载指定 id 的模型文件到本地模型目录，走 net.py 白名单，SHA256 校验，断点续传，并发 1。忽略非官方 HF_ENDPOINT。gated / 未启用的默认拒绝。不静默下载。失败常见原因：id 未知、未启用、网络被拒绝。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：是

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | string | 必填。模型 id，如 asr-small / asr-large-v3-turbo |

CLI：

```
python -m cutora cmd asr.models.download --json '{"id": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"id": ""}' http://127.0.0.1:47321/v1/commands/asr.models.download
```

### `asr.models.list` — 列出模型

列出识别、对齐、VAD 模型的 id、仓库、许可证、大小、本地状态（missing / ready / checksum_failed）以及是否默认启用。只读，不联网。失败常见原因：models.json 缺失。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

CLI：

```
python -m cutora cmd asr.models.list --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/asr.models.list
```

## audio

### `audio.auto_fades` — 说话边缘淡化

给原声 clip 补上 30 毫秒头尾淡化，只写 fade_in_dur 和 fade_out_dur。默认不自动跑。已经有淡化的不覆盖。失败常见原因：没有原声 clip。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `dur` | number | 可选。淡化秒数，默认 0.03 |
| `scope` | string | 可选。current 或 all |

CLI：

```
python -m cutora cmd audio.auto_fades --json '{"dur": 0.0, "scope": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"dur": 0.0, "scope": ""}' http://127.0.0.1:47321/v1/commands/audio.auto_fades
```

### `audio.denoise` — 音频降噪预设

把降噪预设和强度写到原声 clip 的 audio_proc。关闭写成空字典，不改媒体文件路径。scope=clip 时只改 track_id 与 clip_index 指定的那一条。失败常见原因：没有时间线。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `preset` | string | 可选。off / denoise / podcast / youtube / hall |
| `strength` | string | 可选。weak / mid / strong |
| `scope` | string | 可选。current / all / clip |
| `track_id` | string | 可选。scope=clip 时的轨道 id |
| `clip_index` | integer | 可选。scope=clip 时的片段下标 |

CLI：

```
python -m cutora cmd audio.denoise --json '{"preset": "", "strength": "", "scope": "", "track_id": "", "clip_index": 0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"preset": "", "strength": "", "scope": "", "track_id": "", "clip_index": 0}' http://127.0.0.1:47321/v1/commands/audio.denoise
```

### `audio.detect_silence` — 检测静音

用口播词间停顿（没有词时再用语音活动或 ffmpeg）写出 analysis.silences。不点这个命令就不写这个键，老工程不变。失败常见原因：当前没有时间线。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `scope` | string | 可选。current 或 all |

CLI：

```
python -m cutora cmd audio.detect_silence --json '{"scope": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"scope": ""}' http://127.0.0.1:47321/v1/commands/audio.detect_silence
```

### `audio.duck_set` — 人声闪避

把人声压配乐写到任务的 audio_duck。缺这个键或 enabled 为假时，导出滤镜与今天相同。深度用 -6 / -12 / -18，恢复用 80 / 250 / 600 毫秒。失败常见原因：没有当前任务。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `enabled` | boolean | 必填。true 才插入角色侧链 |
| `depth_db` | number | 可选。-6 / -12 / -18 |
| `release_ms` | number | 可选。80 / 250 / 600 |
| `scope` | string | 可选。current 或 all |

CLI：

```
python -m cutora cmd audio.duck_set --json '{"enabled": true, "depth_db": 0.0, "release_ms": 0.0, "scope": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"enabled": true, "depth_db": 0.0, "release_ms": 0.0, "scope": ""}' http://127.0.0.1:47321/v1/commands/audio.duck_set
```

### `audio.isolate` — 只留说话或伴奏

把当前原声送进人声分离队列。mode=vocals 只留说话，novocals 只留伴奏。右键只处理点中的那一条；批量勾选则每条任务的原声各排一个进程。导出进行中直接拒绝。没有本地权重时失败并说明原因，不改媒体路径。

- 可撤销：否
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `mode` | string | 可选。vocals 或 novocals |
| `scope` | string | 可选。current 或 all |
| `track_id` | string | 可选。右键点中的音轨 |
| `clip_index` | integer | 可选。右键点中的 clip 下标 |

CLI：

```
python -m cutora cmd audio.isolate --json '{"mode": "", "scope": "", "track_id": "", "clip_index": 0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"mode": "", "scope": "", "track_id": "", "clip_index": 0}' http://127.0.0.1:47321/v1/commands/audio.isolate
```

### `audio.loudness.set` — 设置导出响度

把导出总线的响度标准化写成 关 / -14 LUFS / -16 LUFS / 自定义，真峰值上限 -1 dBTP。默认关，golden 不变。失败常见原因：mode 无法识别。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `mode` | string | 必填。off / lufs14 / lufs16 / custom |
| `target_lufs` | number | 可选。自定义目标 LUFS；lufs14/lufs16 可省略 |

CLI：

```
python -m cutora cmd audio.loudness.set --json '{"mode": "", "target_lufs": 0.0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"mode": "", "target_lufs": 0.0}' http://127.0.0.1:47321/v1/commands/audio.loudness.set
```

### `audio.remove_silences` — 删除长停顿

把 analysis.silences 里长于 min_dur 的区间做波纹删除，链接的视频和原声一起缩短。默认 0.8 秒，避免吃掉换气。没有检测结果时不删。失败常见原因：没有原声轨。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `min_dur` | number | 可选。最短删除秒数，默认 0.8 |
| `scope` | string | 可选。current 或 all |
| `start` | number | 可选。只删这一段的起点 |
| `end` | number | 可选。只删这一段的终点 |

CLI：

```
python -m cutora cmd audio.remove_silences --json '{"min_dur": 0.0, "scope": "", "start": 0.0, "end": 0.0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"min_dur": 0.0, "scope": "", "start": 0.0, "end": 0.0}' http://127.0.0.1:47321/v1/commands/audio.remove_silences
```

### `audio.sfx.apply_all` — 给全部铺音效

按当前音效设置给工程里每一条视频铺 SFX clip，不弹窗。失败常见原因：工程为空、音效模块未加载。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

CLI：

```
python -m cutora cmd audio.sfx.apply_all --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/audio.sfx.apply_all
```

### `audio.sfx.apply_current` — 给当前条铺音效

按当前音效设置给当前选中视频铺 SFX clip，不弹窗。失败常见原因：未选中视频、音效模块未加载。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

CLI：

```
python -m cutora cmd audio.sfx.apply_current --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/audio.sfx.apply_current
```

### `audio.spatial.set` — 设置空间立体声

预设和宽度写到全局音效设置，并写进当前任务的 audio_fx，不是 clip 字段。湿声写到主音频 clip 的 spatial_amount。失败常见原因：未导入视频、音效模块未加载。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `preset` | string | 可选。空间预设名，如 stage / cathedral |
| `wet` | number | 可选。湿声 0~1 |
| `width` | number | 可选。宽度 0~1 |
| `all` | boolean | 可选。true 时应用到全部视频 |

CLI：

```
python -m cutora cmd audio.spatial.set --json '{"preset": "", "wet": 0.0, "width": 0.0, "all": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"preset": "", "wet": 0.0, "width": 0.0, "all": true}' http://127.0.0.1:47321/v1/commands/audio.spatial.set
```

### `audio.volume_master` — 音量总控

按 scope=selected|all 把原声、配乐、音效的线性音量写成对应 clip 的 gain_db，原声同时写 src_volume。命令体不弹对话框。失败常见原因：scope=selected 但没有选中项。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `src` | number | 可选。原生音量线性值 |
| `bgm` | number | 可选。配乐音量线性值 |
| `sfx` | number | 可选。音效音量线性值 |
| `scope` | string | 可选。selected 或 all |

CLI：

```
python -m cutora cmd audio.volume_master --json '{"src": 0.0, "bgm": 0.0, "sfx": 0.0, "scope": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"src": 0.0, "bgm": 0.0, "sfx": 0.0, "scope": ""}' http://127.0.0.1:47321/v1/commands/audio.volume_master
```

## batch

### `batch.add_bgm` — 无头批量配乐

从音频文件夹给工程里每条视频配一首够长的背景音乐（对齐界面「全部配乐」）。按视频时长挑一首更长的音频，尽量不重复。只改工程，不渲染。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `project` | string | 必填。工程文件 .asproj.json |
| `dir` | string | 必填。音频文件夹（mp3/wav/m4a/flac/ogg/aac） |
| `volume` | number | 可选。线性音量，默认 0.10，与界面批量配乐一致。短于视频的音频会循环 |
| `out` | string | 可选。写出路径，默认覆盖原工程 |

CLI：

```
python -m cutora cmd batch.add_bgm --json '{"project": "", "dir": "", "volume": 0.0, "out": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"project": "", "dir": "", "volume": 0.0, "out": ""}' http://127.0.0.1:47321/v1/commands/batch.add_bgm
```

### `batch.add_text_overlay` — 批量文字覆层

给工程里每条视频加上一条文字覆层（署名 / Generated with AI #XY）。只改工程，不渲染。要出片再跑 batch_export。需要加插图时先这一步再打开界面。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `project` | string | 必填。工程文件 .asproj.json |
| `preset` | string | 可选。文本预设 id（或显示名）。读 UserData/presets/text_presets.json。默认 credit.default（署名） |
| `content` | string | 可选。覆盖预设文案。默认用预设里的 Generated with AI #XY |
| `replace` | boolean | 可选。true 时先删掉同名预设的旧覆层，再写一条新的 |
| `out` | string | 可选。写出路径，默认覆盖原工程 |

CLI：

```
python -m cutora cmd batch.add_text_overlay --json '{"project": "", "preset": "", "content": "", "replace": true, "out": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"project": "", "preset": "", "content": "", "replace": true, "out": ""}' http://127.0.0.1:47321/v1/commands/batch.add_text_overlay
```

### `batch.auto_subtitle` — 无头自动字幕

按每条视频的文案强制对齐，生成字幕时间轴（对应界面「一键全自动」）。静默覆盖已调好的时间轴是不可逆的，所以 force_relayout 默认 false。只处理「有文案但还没有字幕分段」的条目。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：是

| 参数 | 类型 | 说明 |
|---|---|---|
| `project` | string | 必填。工程文件 .asproj.json |
| `out` | string | 可选。写出路径。默认覆盖原工程 |
| `force_relayout` | boolean | 可选。当所有视频已经有字幕时，默认不重打轴。true 才会覆盖已有时间轴 |

CLI：

```
python -m cutora cmd batch.auto_subtitle --json '{"project": "", "out": "", "force_relayout": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"project": "", "out": "", "force_relayout": true}' http://127.0.0.1:47321/v1/commands/batch.auto_subtitle
```

### `batch.batch_export` — 无头批量导出

按工程里的字幕把每条视频渲染成带字幕的成品，写到「字幕成品」目录。耗时长时请加 --async，立刻返回 job id，再用 --status 查进度。导出期间右下角会弹出独立的监视器小窗，这是正常的。成品在每条原视频旁边的「字幕成品」文件夹。

- 可撤销：否
- 危险：是（API 需 confirm=true）
- 无头：是
- 异步作业：是

| 参数 | 类型 | 说明 |
|---|---|---|
| `project` | string | 必填。工程文件 |
| `workers` | integer|null | 可选。并行进程数。null 表示按当时内存/显存/CPU 自动算 |
| `gpu` | boolean|null | 可选。是否用 NVENC。null 表示有 NVIDIA 显卡就尝试，不够再降级 |
| `mode` | string | 可选。渲染质量。quality 或 fast，与界面一致 |

CLI：

```
python -m cutora cmd batch.batch_export --json '{"project": "", "workers": 0, "gpu": true, "mode": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"project": "", "workers": 0, "gpu": true, "mode": ""}' http://127.0.0.1:47321/v1/commands/batch.batch_export
```

### `batch.batch_subtitles` — 口播字幕配方

一批口播素材：拼接 → 按 80 切开工程 → 打轴 → 导出字幕成品。stitch 若以退出码 3 停下，先按「缺失文案清单.txt」补片段，再重跑 stitch，不要继续导出。多份工程要连续导出请改走 split_and_queue_export。

- 可撤销：否
- 危险：是（API 需 confirm=true）
- 无头：是
- 异步作业：是

| 参数 | 类型 | 说明 |
|---|---|---|
| `dir` | string | 必填。素材目录。里面是按组命名的短视频，例如 01.mp4、01 (2).mp4、01a.mp4 |
| `captions` | string | 必填。文案。三种写法：1) 直接把全文放进这个字段；2) @文件路径 读取文本文件；3) 字符串 "-" 表示从 stdin 读。文本格式二选一：每段以「01\|文案」或「01:文案」开头；或按空行分段，段数必须和素材组数一致。 |
| `language` | string | 可选。识别/对齐语言。常用 en（英语）或 it（意大利语） |
| `out_dir` | string | 可选。成品目录。默认是素材目录下的 _stitched |
| `allow_blocking` | boolean | 可选。素材体检发现断号/无音轨/组内分辨率不一致时，默认停下。true 才继续 |
| `quarantine` | boolean | 可选。把根本不是视频的坏文件挪到 <dir>/_invalid/。只移动不删除 |
| `write_project` | boolean | 可选。成功后写出 stitched.asproj.json，供后续 auto_subtitle / 导出使用 |
| `allow_missing` | boolean | 可选。有缺失文案时默认 exit 3 不写时间线。true 仍写出时间线工程 |
| `analyze` | boolean | 可选。false = 只跑素材体检，不调 whisper、不写时间线。给「仅检查」用 |
| `encode` | boolean | 可选。false = 分析到 cutlist 为止。true 写出时间线工程（不再拼中间 mp4） |
| `ignore_scripture_refs` | boolean | 可选。括号里的经文出处（如 (John 8:34-35)）不列入缺失清单。配音不会念这些。默认 true |
| `group_by_folder` | boolean | 可选。true 时一个文件夹算一组，忽略文件名与前缀规则。文件随便命名也能拼 |

CLI：

```
python -m cutora cmd batch.batch_subtitles --json '{"dir": "", "captions": "", "language": "", "out_dir": "", "allow_blocking": true, "quarantine": true, "write_project": true, "allow_missing": true, "analyze": true, "encode": true, "ignore_scripture_refs": true, "group_by_folder": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"dir": "", "captions": "", "language": "", "out_dir": "", "allow_blocking": true, "quarantine": true, "write_project": true, "allow_missing": true, "analyze": true, "encode": true, "ignore_scripture_refs": true, "group_by_folder": true}' http://127.0.0.1:47321/v1/commands/batch.batch_subtitles
```

### `batch.bgm_assign` — 批量配乐

从音频文件夹给当前工程（或给定 ids）配背景音乐，对齐界面「全部配乐」。命令体不弹文件夹框。失败常见原因：文件夹里没有音频。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `dir` | string | 必填。音频文件夹 |
| `ids` | array | 可选。只给这些媒体配乐；省略则全部 |

CLI：

```
python -m cutora cmd batch.bgm_assign --json '{"dir": "", "ids": []}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"dir": "", "ids": []}' http://127.0.0.1:47321/v1/commands/batch.bgm_assign
```

### `batch.copy_subtitle_style` — 拷贝字幕样式

把 src_id 的字幕样式套到 target_ids，对齐资产库右键「拷贝字幕样式」。命令体不弹确认框。失败常见原因：src_id 越界、targets 为空。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `src_id` | integer | 必填。源媒体下标 |
| `target_ids` | array | 必填。目标媒体下标 |

CLI：

```
python -m cutora cmd batch.copy_subtitle_style --json '{"src_id": 0, "target_ids": []}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"src_id": 0, "target_ids": []}' http://127.0.0.1:47321/v1/commands/batch.copy_subtitle_style
```

### `batch.import_media` — 无头导入媒体

把一组成品视频（通常是 _stitched 里的 mp4）写成片语工程文件。stitch 成功时已经自动写过工程。只有手里只有 mp4+文案、没有工程文件时才需要这个。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `videos` | string|array[string] | 必填。视频目录，或视频路径数组。目录下按文件名排序 |
| `captions` | string | 可选。可选文案，格式同 stitch。按文件名中的组号对应 |
| `out` | string | 可选。工程输出路径。默认 <目录>/imported.asproj.json |

CLI：

```
python -m cutora cmd batch.import_media --json '{"videos": "", "captions": "", "out": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"videos": "", "captions": "", "out": ""}' http://127.0.0.1:47321/v1/commands/batch.import_media
```

### `batch.insert_image` — 批量加载

把同一张图批量铺到工程每条视频的 Images 轨道，默认 1 分钟后出现、铺到片尾、铺满 9:16 画面。只改工程 image_overlays，不渲染。默认铺满 9:16 画面、从第 60 秒到片尾、Images 1。带 platform_copies 时预览仍只插一张图，导出才按平台分份。render_mode 默认 once（一次渲染叠图）；each 为每平台单独渲染。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `project` | string | 必填。工程文件 .asproj.json |
| `image` | string | 必填。图片路径，如 C:\\Users\\Timco\\Downloads\\Bible_study.png |
| `start_sec` | number|string | 可选。起始空余。秒数或 m:ss。此前不出现。默认 60（1 分钟） |
| `end_pad_sec` | number|string | 可选。结束空余：距片尾多少秒消失。默认 0=铺到视频结尾 |
| `end_sec` | number|string | 可选。绝对结束时间（秒或 m:ss）。若提供则覆盖 end_pad_sec |
| `image_track` | integer | 可选。Images 轨道编号，默认 1 |
| `fit` | string | 可选。fill 铺满画面；fit 完整放下；stretch 拉伸 |
| `scale` | number | 可选。额外缩放，1=适配画面 |
| `x` | number | 可选。水平偏移像素 |
| `y` | number | 可选。垂直偏移像素 |
| `replace` | boolean | 可选。true 时先去掉本脚本上次插的同一张图 |
| `platform_copies` | object | 可选。多平台副本。例 {"youtube":"a.png","instagram":"b.png","tiktok":"c.png"}。默认同一次渲染：字幕烧一遍再叠图。可加 "render_mode":"each" 做多次单独渲染（字幕盖住图片）。文件名前加 Youtube_ / Instagram_ / TikTok_ |
| `render_mode` | string | 可选。once=一次性渲染（默认，更快）；each=多次单独渲染（字幕盖住图片）。写入 platform_copies |
| `out` | string | 可选。写出工程路径。默认覆盖原工程 |
| `indices` | string|array[string] | 可选。只处理这些序号（1 起）。例 "1-3,5"。默认全部 |
| `videos` | array | 可选。只处理这些文件名，如 ["Tik001.mp4"] |

CLI：

```
python -m cutora cmd batch.insert_image --json '{"project": "", "image": "", "start_sec": 0.0, "end_pad_sec": 0.0, "end_sec": 0.0, "image_track": 0, "fit": "", "scale": 0.0, "x": 0.0, "y": 0.0, "replace": true, "platform_copies": "", "render_mode": "", "out": "", "indices": "", "videos": []}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"project": "", "image": "", "start_sec": 0.0, "end_pad_sec": 0.0, "end_sec": 0.0, "image_track": 0, "fit": "", "scale": 0.0, "x": 0.0, "y": 0.0, "replace": true, "platform_copies": "", "render_mode": "", "out": "", "indices": "", "videos": []}' http://127.0.0.1:47321/v1/commands/batch.insert_image
```

### `batch.longtik_finish` — 长文案成片配方

长文案成片：拼接 → 打轴 → 署名 → 配乐 →（可选）导出。要加插图就停在工程，不要导出。署名默认预设 credit.default（署名）= Generated with AI。最后一步 batch_export 可省略：只保存工程，再用图形界面加插图后导出。

- 可撤销：否
- 危险：是（API 需 confirm=true）
- 无头：是
- 异步作业：是

| 参数 | 类型 | 说明 |
|---|---|---|
| `dir` | string | 必填。素材目录。里面是按组命名的短视频，例如 01.mp4、01 (2).mp4、01a.mp4 |
| `captions` | string | 必填。文案。三种写法：1) 直接把全文放进这个字段；2) @文件路径 读取文本文件；3) 字符串 "-" 表示从 stdin 读。文本格式二选一：每段以「01\|文案」或「01:文案」开头；或按空行分段，段数必须和素材组数一致。 |
| `language` | string | 可选。识别/对齐语言。常用 en（英语）或 it（意大利语） |
| `out_dir` | string | 可选。成品目录。默认是素材目录下的 _stitched |
| `allow_blocking` | boolean | 可选。素材体检发现断号/无音轨/组内分辨率不一致时，默认停下。true 才继续 |
| `quarantine` | boolean | 可选。把根本不是视频的坏文件挪到 <dir>/_invalid/。只移动不删除 |
| `write_project` | boolean | 可选。成功后写出 stitched.asproj.json，供后续 auto_subtitle / 导出使用 |
| `allow_missing` | boolean | 可选。有缺失文案时默认 exit 3 不写时间线。true 仍写出时间线工程 |
| `analyze` | boolean | 可选。false = 只跑素材体检，不调 whisper、不写时间线。给「仅检查」用 |
| `encode` | boolean | 可选。false = 分析到 cutlist 为止。true 写出时间线工程（不再拼中间 mp4） |
| `ignore_scripture_refs` | boolean | 可选。括号里的经文出处（如 (John 8:34-35)）不列入缺失清单。配音不会念这些。默认 true |
| `group_by_folder` | boolean | 可选。true 时一个文件夹算一组，忽略文件名与前缀规则。文件随便命名也能拼 |

CLI：

```
python -m cutora cmd batch.longtik_finish --json '{"dir": "", "captions": "", "language": "", "out_dir": "", "allow_blocking": true, "quarantine": true, "write_project": true, "allow_missing": true, "analyze": true, "encode": true, "ignore_scripture_refs": true, "group_by_folder": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"dir": "", "captions": "", "language": "", "out_dir": "", "allow_blocking": true, "quarantine": true, "write_project": true, "allow_missing": true, "analyze": true, "encode": true, "ignore_scripture_refs": true, "group_by_folder": true}' http://127.0.0.1:47321/v1/commands/batch.longtik_finish
```

### `batch.merge_projects` — 合并工程

把多个工程文件的视频条目合并成一个工程，每条视频自己的字幕、署名、配乐都保留。不会重新打轴、不会重跑 whisper。任务里已有的 segments / text / 覆层原样并进新工程。用界面打开即可继续加插图或导出。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `projects` | string|array[string] | 必填。工程文件、目录、或数组。目录下收取全部 .asproj.json / .asproj。也可写 params.dir 当目录用。 |
| `out` | string | 可选。写出路径。默认放在第一份工程旁边，名叫 merged.asproj.json |
| `sort` | string | 可选。name 按文件名自然序（Tik001 在 Tik002 前）；mtime 按修改时间；given 按传入顺序 |
| `dedupe` | boolean | 可选。同一视频路径只留第一次出现的那条 |

CLI：

```
python -m cutora cmd batch.merge_projects --json '{"projects": "", "out": "", "sort": "", "dedupe": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"projects": "", "out": "", "sort": "", "dedupe": true}' http://127.0.0.1:47321/v1/commands/batch.merge_projects
```

### `batch.queue.add` — 加入工程队列

把一组工程文件写入 USER_DATA 的工程队列 JSON，供 batch.queue_run 使用。不开始导出。失败常见原因：projects 为空或路径不存在。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `projects` | array | 必填。工程文件路径 |
| `auto_subtitle` | boolean | 可选。导出前是否先打轴 |

CLI：

```
python -m cutora cmd batch.queue.add --json '{"projects": [], "auto_subtitle": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"projects": [], "auto_subtitle": true}' http://127.0.0.1:47321/v1/commands/batch.queue.add
```

### `batch.queue_run` — 跑工程队列

按工程队列依次导出；每个工程都在全新进程里跑完再进入下一个（等价于软件重启）。每个工程默认：auto_subtitle（有缺字幕才跑）→ 写回工程 → 再 spawn batch_export。batch_export 子进程退出后再开下一个，用来释放显存/内存，对应图形界面「工程队列」的重启策略。拆分后的工程没有字幕，不要把 auto_subtitle 设成 false。耗时长请加 --async，再用 --status 看当前工程进度和队列剩余。图形界面：文件 → 自动拆分工程 / 工程队列。

- 可撤销：否
- 危险：是（API 需 confirm=true）
- 无头：是
- 异步作业：是

| 参数 | 类型 | 说明 |
|---|---|---|
| `projects` | string|array[string] | 可选。工程文件、目录、或数组。目录下按给定顺序收取 .asproj.json |
| `project` | string | 可选。单个工程。和 chunk 一起用时先拆分再排队 |
| `chunk` | integer | 可选。与 project 联用：先按条数切开，再按切片排队。例 100 条 chunk=40 → 3 个工程 |
| `outdir` | string | 可选。拆分输出目录，默认与源工程同目录 |
| `name_pattern` | string | 可选。拆分文件名，默认 <原名>_<start>-<end>.asproj.json |
| `sort` | string | 可选。projects 排序：given（保持传入顺序）/ name / mtime |
| `stop_on_error` | boolean | 可选。某一个工程失败时是否停下。false 则继续后面的 |
| `workers` | integer|null | 可选。传给每个工程的 batch_export.workers |
| `gpu` | boolean|null | 可选。传给每个工程的 batch_export.gpu |
| `mode` | string | 可选。传给每个工程的 batch_export.mode |
| `auto_subtitle` | boolean | 可选。每个工程导出前先跑 auto_subtitle（一键全自动）并写回工程。拆分后还没有字幕时必须开。已有字幕的条目默认不重打轴 |
| `auto_credit` | boolean | 可选。导出前按文本预设幂等加署名（source_preset_id）。重复跑不叠加 |
| `credit_preset_id` | string | 可选。自动署名用的 text_preset id |

CLI：

```
python -m cutora cmd batch.queue_run --json '{"projects": "", "project": "", "chunk": 0, "outdir": "", "name_pattern": "", "sort": "", "stop_on_error": true, "workers": 0, "gpu": true, "mode": "", "auto_subtitle": true, "auto_credit": true, "credit_preset_id": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"projects": "", "project": "", "chunk": 0, "outdir": "", "name_pattern": "", "sort": "", "stop_on_error": true, "workers": 0, "gpu": true, "mode": "", "auto_subtitle": true, "auto_credit": true, "credit_preset_id": ""}' http://127.0.0.1:47321/v1/commands/batch.queue_run
```

### `batch.save_project` — 无头保存工程

读取工程并重新规范化后写回（另存为）。不弹窗、不改用户的最近项目列表。。失败常见原因：路径不存在、工程损坏、或被单实例锁占用。

- 可撤销：否
- 危险：是（API 需 confirm=true）
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `project` | string | 必填。源工程 |
| `out` | string | 可选。目标路径，默认覆盖源文件 |

CLI：

```
python -m cutora cmd batch.save_project --json '{"project": "", "out": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"project": "", "out": ""}' http://127.0.0.1:47321/v1/commands/batch.save_project
```

### `batch.scatter_broll` — 散落 B-roll

把短视频按固定时长切开，静音后循环铺到工程每条长视频的 Video2（或指定轨道）。先按 clip_sec 切开（末段不足也保留），再从 start_sec 起每隔 interval_sec 在每条长视频的 Video 轨道上循环铺切片。默认静音、不进音轨、不碰主视频。只改工程，不渲染。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `project` | string | 必填。工程文件 .asproj.json，例如 八段视频.asproj.json |
| `clips_dir` | string | 必填。待散落的源视频目录。只读该目录下一层，切片写到其中 _scatter/ |
| `clips` | array | 可选。目录和/或单个视频文件混着给，例 ["D:/观众席", "D:/a/b.mp4"]。目录只收下一层，重复路径去重 |
| `clip_sec` | number | 可选。每段裁剪时长（秒）。源视频最后一段不足此时长也保留 |
| `interval_sec` | number | 可选。工程时间线上相邻两段落点的间隔（秒） |
| `start_sec` | number | 可选。第一条落点。默认等于 interval_sec（开头不放，从第 20 秒起） |
| `mute` | boolean | 可选。true 时切片去掉音频，也不写入音轨 |
| `video_track` | integer | 可选。目标 Video 轨道编号。1 是主视频，禁止写入。默认 2 |
| `replace` | boolean | 可选。true 时先清空目标轨道再铺，并拿掉该目录素材残留音轨 |
| `out_dir` | string | 可选。切片输出目录。默认 <clips_dir>/_scatter |
| `out` | string | 可选。写出工程路径。默认覆盖原工程 |
| `indices` | string|array[string] | 可选。只处理这些序号（1 起）。例 "1-3,5" 或 [1,2,5]。默认全部 |
| `videos` | array | 可选。只处理这些文件名，如 ["Tik001.mp4","Tik003.mp4"] |

CLI：

```
python -m cutora cmd batch.scatter_broll --json '{"project": "", "clips_dir": "", "clips": [], "clip_sec": 0.0, "interval_sec": 0.0, "start_sec": 0.0, "mute": true, "video_track": 0, "replace": true, "out_dir": "", "out": "", "indices": "", "videos": []}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"project": "", "clips_dir": "", "clips": [], "clip_sec": 0.0, "interval_sec": 0.0, "start_sec": 0.0, "mute": true, "video_track": 0, "replace": true, "out_dir": "", "out": "", "indices": "", "videos": []}' http://127.0.0.1:47321/v1/commands/batch.scatter_broll
```

### `batch.share_tracks` — 共享轨道

把 src_id 上指定类型的可共享轨道复制到 target_ids，对齐「从选中视频共享轨道」。命令体不弹对话框。失败常见原因：源上没有可共享轨道。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `src_id` | integer | 必填。源媒体下标 |
| `target_ids` | array | 必填。目标媒体下标 |
| `track_types` | array | 可选。只共享这些类型；省略则按源上已开启共享的轨道 |
| `track_ids` | array | 可选。只共享这些轨道 id（比 track_types 精确；给了就不看 track_types） |
| `keep_duration_track_ids` | array | 可选。这些轨道保持源视频里的时长，不拉伸到目标视频时长 |
| `auto_fit_duration` | boolean | 可选。其余轨道是否拉伸匹配目标视频时长（默认 true） |

CLI：

```
python -m cutora cmd batch.share_tracks --json '{"src_id": 0, "target_ids": [], "track_types": [], "track_ids": [], "keep_duration_track_ids": [], "auto_fit_duration": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"src_id": 0, "target_ids": [], "track_types": [], "track_ids": [], "keep_duration_track_ids": [], "auto_fit_duration": true}' http://127.0.0.1:47321/v1/commands/batch.share_tracks
```

### `batch.split_and_queue_export` — 拆分并排队导出

大工程按条数切开，再按工程队列逐份导出；每份结束都起一个新进程（释放显存）。一条命令也可以：--do queue_run --params {"project":"...","chunk":40}。100 条 chunk=40 → 3 个工程；每份先 auto_subtitle 再导出。

- 可撤销：否
- 危险：是（API 需 confirm=true）
- 无头：是
- 异步作业：是

| 参数 | 类型 | 说明 |
|---|---|---|
| `project` | string | 必填。源工程 |
| `chunk` | integer | 可选。每个工程最多几条视频。225 条 chunk=80 → 80/80/65 |
| `outdir` | string | 可选。输出目录，默认与源工程同目录 |
| `name_pattern` | string | 可选。默认 <原名>_<start>-<end>.asproj.json |

CLI：

```
python -m cutora cmd batch.split_and_queue_export --json '{"project": "", "chunk": 0, "outdir": "", "name_pattern": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"project": "", "chunk": 0, "outdir": "", "name_pattern": ""}' http://127.0.0.1:47321/v1/commands/batch.split_and_queue_export
```

### `batch.split_project` — 拆分工程

按列表顺序把一个工程切成多个较小的工程文件。不修改原工程。产出的每个文件都可以单独用界面打开或交给 auto_subtitle / batch_export。图形界面：文件 → 自动拆分工程。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `project` | string | 必填。源工程 |
| `chunk` | integer | 可选。每个工程最多几条视频。225 条 chunk=80 → 80/80/65 |
| `outdir` | string | 可选。输出目录，默认与源工程同目录 |
| `name_pattern` | string | 可选。默认 <原名>_<start>-<end>.asproj.json |

CLI：

```
python -m cutora cmd batch.split_project --json '{"project": "", "chunk": 0, "outdir": "", "name_pattern": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"project": "", "chunk": 0, "outdir": "", "name_pattern": ""}' http://127.0.0.1:47321/v1/commands/batch.split_project
```

### `batch.stitch` — 智能拼接

把一个目录里的片段按文案智能拼接成整条视频，并写出可打开的工程文件。有缺失文案时默认不写时间线，会导出「缺失文案清单.txt」并以 3 退出。补齐缺失片段后用同一命令重跑即可；未改动的组会复用上次转录缓存。成功时写出 stitched.asproj.json，不生成 _stitched 中间 mp4。

- 可撤销：否
- 危险：是（API 需 confirm=true）
- 无头：是
- 异步作业：是

| 参数 | 类型 | 说明 |
|---|---|---|
| `dir` | string | 必填。素材目录。里面是按组命名的短视频，例如 01.mp4、01 (2).mp4、01a.mp4 |
| `captions` | string | 必填。文案。三种写法：1) 直接把全文放进这个字段；2) @文件路径 读取文本文件；3) 字符串 "-" 表示从 stdin 读。文本格式二选一：每段以「01\|文案」或「01:文案」开头；或按空行分段，段数必须和素材组数一致。 |
| `language` | string | 可选。识别/对齐语言。常用 en（英语）或 it（意大利语） |
| `out_dir` | string | 可选。成品目录。默认是素材目录下的 _stitched |
| `allow_blocking` | boolean | 可选。素材体检发现断号/无音轨/组内分辨率不一致时，默认停下。true 才继续 |
| `quarantine` | boolean | 可选。把根本不是视频的坏文件挪到 <dir>/_invalid/。只移动不删除 |
| `write_project` | boolean | 可选。成功后写出 stitched.asproj.json，供后续 auto_subtitle / 导出使用 |
| `allow_missing` | boolean | 可选。有缺失文案时默认 exit 3 不写时间线。true 仍写出时间线工程 |
| `analyze` | boolean | 可选。false = 只跑素材体检，不调 whisper、不写时间线。给「仅检查」用 |
| `encode` | boolean | 可选。false = 分析到 cutlist 为止。true 写出时间线工程（不再拼中间 mp4） |
| `ignore_scripture_refs` | boolean | 可选。括号里的经文出处（如 (John 8:34-35)）不列入缺失清单。配音不会念这些。默认 true |
| `group_by_folder` | boolean | 可选。true 时一个文件夹算一组，忽略文件名与前缀规则。文件随便命名也能拼 |

CLI：

```
python -m cutora cmd batch.stitch --json '{"dir": "", "captions": "", "language": "", "out_dir": "", "allow_blocking": true, "quarantine": true, "write_project": true, "allow_missing": true, "analyze": true, "encode": true, "ignore_scripture_refs": true, "group_by_folder": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"dir": "", "captions": "", "language": "", "out_dir": "", "allow_blocking": true, "quarantine": true, "write_project": true, "allow_missing": true, "analyze": true, "encode": true, "ignore_scripture_refs": true, "group_by_folder": true}' http://127.0.0.1:47321/v1/commands/batch.stitch
```

## export

### `export.batch` — 批量导出

按工程队列或当前列表批量导出到 out_dir。对应界面「批量导出」。失败常见原因：没有视频、输出目录不可写。

- 可撤销：否
- 危险：是（API 需 confirm=true）
- 无头：否（需要图形界面）
- 异步作业：是

| 参数 | 类型 | 说明 |
|---|---|---|
| `project_paths` | array | 可选。工程文件；省略则导出当前工程全部条目 |
| `out_dir` | string | 可选。输出目录 |
| `allow_offline` | boolean | 可选。true 才允许带着离线素材导出；默认 false |

CLI：

```
python -m cutora cmd export.batch --json '{"project_paths": [], "out_dir": "", "allow_offline": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"project_paths": [], "out_dir": "", "allow_offline": true}' http://127.0.0.1:47321/v1/commands/export.batch
```

### `export.profile.delete` — 删除导出预设

删除一个用户导出预设。内置预设不能删。正在使用的会被打回社交竖版。失败常见原因：id 是内置、或不存在。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | string | 必填。预设 id |

CLI：

```
python -m cutora cmd export.profile.delete --json '{"id": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"id": ""}' http://127.0.0.1:47321/v1/commands/export.profile.delete
```

### `export.profile.list` — 列出导出预设

列出内置与用户导出预设（id、名称、容器、视频编码、画质档），不改任何文件。失败常见原因：用户预设 JSON 损坏时该项被跳过，内置仍在。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

CLI：

```
python -m cutora cmd export.profile.list --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/export.profile.list
```

### `export.profile.save` — 保存导出预设

把一个用户导出预设写入 USER_DATA/presets/export_profiles.json。内置 id 拒绝覆盖，请换新 id。失败常见原因：id 非法、试图覆盖内置。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | string | 必填。预设 id |
| `name` | string | 可选。显示名 |
| `container` | string | 可选。mp4 / mov / m4a |
| `vcodec` | string | 可选。h264 / hevc / prores / none |
| `quality` | string | 可选。compressed / high / archive / source |
| `audio_codec` | string | 可选。aac / pcm_s16le |

CLI：

```
python -m cutora cmd export.profile.save --json '{"id": "", "name": "", "container": "", "vcodec": "", "quality": "", "audio_codec": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"id": "", "name": "", "container": "", "vcodec": "", "quality": "", "audio_codec": ""}' http://127.0.0.1:47321/v1/commands/export.profile.save
```

### `export.run` — 导出当前条

把当前选中视频渲染成 out 指定的文件。profile 选内置或用户导出预设，真正改编码器。耗时，标记为异步作业；T13a 仍在主线程启动既有 worker。失败常见原因：未选中视频、out 不可写、未知 profile。

- 可撤销：否
- 危险：是（API 需 confirm=true）
- 无头：否（需要图形界面）
- 异步作业：是

| 参数 | 类型 | 说明 |
|---|---|---|
| `out` | string | 可选。输出路径 |
| `out_dir` | string | 可选。若未给 out，则写到此目录 |
| `honor_io` | boolean | 可选。true 时只导出 I/O 区间；默认 false，避免误裁 |
| `profile` | string | 可选。导出预设 id；省略则用当前设置 |
| `allow_offline` | boolean | 可选。true 才允许带着离线素材导出；默认 false，命令层硬拦 |

CLI：

```
python -m cutora cmd export.run --json '{"out": "", "out_dir": "", "honor_io": true, "profile": "", "allow_offline": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"out": "", "out_dir": "", "honor_io": true, "profile": "", "allow_offline": true}' http://127.0.0.1:47321/v1/commands/export.run
```

### `export.settings` — 导出设置

读取当前导出相关设置（并行开关、进程数、视频模式、导出预设），不开始导出。给机器人在 export.run 之前确认参数。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

CLI：

```
python -m cutora cmd export.settings --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/export.settings
```

### `export.status` — 导出状态

返回当前是否正在导出、批导出进度、最近错误。不改变导出任务。给机器人轮询 export.run / export.batch。

- 可撤销：否
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

CLI：

```
python -m cutora cmd export.status --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/export.status
```

## fx

### `fx.graph` — 效果节点图

op=create 把视频片段 / 图片层上的蓝绿幕、AI 抠像、形状遮罩搬进一张等价的节点图（结果逐位一致，老键清空）；op=delete 删掉节点图回到直通；op=replace 用 graph 整张替换（粘贴、自动排布用）。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。image_overlays（文字层没有节点图） |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `op` | string | 必填。create / delete / replace |
| `graph` | object | 可选。op=replace 时的整张图 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd fx.graph --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "op": "create", "graph": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "op": "create", "graph": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/fx.graph
```

### `fx.keyer_set` — 设置蓝绿幕抠像

合并 patch 到视频片段 / 图片层的抠像设置：enabled、color(#RRGGBB)、gain、balance、despill、preblur、black、white、gamma、erode、blur、despot、view(comp/matte/source/spill)。全部回到默认且未启用时清掉。锁定轨拒绝。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `element_index` | integer | 可选。覆层里 elements 的下标（可选） |
| `node_id` | string | 可选。效果节点图里的节点 id（可选；给了就改那个节点） |
| `patch` | object | 必填。要改的字段 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd fx.keyer_set --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "patch": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "patch": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/fx.keyer_set
```

### `fx.link` — 效果节点连线

op=connect 把 from 节点的输出接到 to 节点的 port 输入（类型不配——遮罩不能接到画面端口——或会成环时拒绝；画面接到遮罩端口时取它的透明度；目标端口原来的线被替换）；op=disconnect 拔掉 to 节点 port 上的线。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。image_overlays（文字层没有节点图） |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `op` | string | 必填。connect / disconnect |
| `from` | string | 可选。上游节点 id（src / 节点 id） |
| `to` | string | 可选。下游节点 id（out / 节点 id） |
| `port` | string | 可选。下游端口名（in / mask / matte / a / b） |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd fx.link --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "op": "connect", "from": "", "to": "", "port": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "op": "connect", "from": "", "to": "", "port": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/fx.link
```

### `fx.mask_add` — 添加遮罩

给视频片段 / 图片层 / 文本层加一个形状遮罩。mask: type(rect/ellipse/polygon/bezier)、rect [nx,ny,nw,nh] 或 points [[nx,ny],…]（图层自己的 0–1 坐标；bezier 每点 [nx,ny,进手柄dx,dy,出手柄dx,dy]）、rotation、radius、feather、expand、opacity、invert、mode(add/subtract/intersect)、enabled、name。锁定轨拒绝。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `element_index` | integer | 可选。覆层里 elements 的下标（可选） |
| `node_id` | string | 可选。效果节点图里的节点 id（可选；给了就改那个节点） |
| `mask` | object | 必填。遮罩定义 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd fx.mask_add --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "mask": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "mask": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/fx.mask_add
```

### `fx.mask_delete` — 删除遮罩

按 mask_id 删掉对象上的一个遮罩，其余顺序不变；没有遮罩了就摘掉 masks 键。锁定轨拒绝。失败常见原因：找不到遮罩。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `element_index` | integer | 可选。覆层里 elements 的下标（可选） |
| `node_id` | string | 可选。效果节点图里的节点 id（可选；给了就改那个节点） |
| `mask_id` | string | 必填。遮罩 id |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd fx.mask_delete --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "mask_id": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "mask_id": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/fx.mask_delete
```

### `fx.mask_reorder` — 调整遮罩顺序

order = 对象上全部遮罩 id 的新顺序，叠加运算（加 / 减 / 交）按这个顺序从上到下算。锁定轨拒绝。失败常见原因：order 漏了或多了 id。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `element_index` | integer | 可选。覆层里 elements 的下标（可选） |
| `node_id` | string | 可选。效果节点图里的节点 id（可选；给了就改那个节点） |
| `order` | array | 必填。遮罩 id 顺序 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd fx.mask_reorder --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "order": [], "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "order": [], "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/fx.mask_reorder
```

### `fx.mask_update` — 修改遮罩

按 mask_id 找到那个遮罩，把 patch 里的字段合并进去（字段同 fx.mask_add 的 mask），合并后重新规范化。锁定轨拒绝。失败常见原因：找不到遮罩、多边形点数不足。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `element_index` | integer | 可选。覆层里 elements 的下标（可选） |
| `node_id` | string | 可选。效果节点图里的节点 id（可选；给了就改那个节点） |
| `mask_id` | string | 必填。遮罩 id |
| `patch` | object | 必填。要改的字段 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd fx.mask_update --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "mask_id": "", "patch": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "mask_id": "", "patch": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/fx.mask_update
```

### `fx.node` — 效果节点增删改

op=add 加节点（type、pos、可选 params、after=插到某节点后面并接好线）；op=remove 删节点（reconnect=把上下游接上）；op=set 改 label / color / enabled（旁路）/ pos / params；op=move 批量改位置（positions，含 src / out，一次撤销）；op=swap 前两个同类输入互换。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。image_overlays（文字层没有节点图） |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `op` | string | 必填。add / remove / set / move / swap |
| `type` | string | 可选。节点类型：keyer / ai_matte / mask / matte_ops / matte_apply / blur / color |
| `id` | string | 可选。节点 id |
| `pos` | array | 可选。[x, y] |
| `params` | object | 可选。初始参数（add） |
| `after` | string | 可选。add 时插到这个节点后面 |
| `reconnect` | boolean | 可选。remove 时是否把上下游接上（默认是） |
| `patch` | object | 可选。set 的改动 |
| `positions` | object | 可选。move：{id: [x, y]} |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd fx.node --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "op": "add", "type": "", "id": "", "pos": [], "params": "", "after": "", "reconnect": true, "patch": "", "positions": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "op": "add", "type": "", "id": "", "pos": [], "params": "", "after": "", "reconnect": true, "patch": "", "positions": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/fx.node
```

## glossary

### `glossary.get` — 读取术语表

读取全局或工程级术语表（人名、地名、经文、专业词，一行一个）。默认 scope=global，读 USER_DATA/presets/glossary.txt，可无头。scope=project 读当前工程；无窗口时返回 needs_project。不改识别、不联网。失败常见原因：工程未打开。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `scope` | string | 可选。global 或 project，默认 global |

CLI：

```
python -m cutora cmd glossary.get --json '{"scope": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"scope": ""}' http://127.0.0.1:47321/v1/commands/glossary.get
```

### `glossary.set` — 写入术语表

写入全局或工程级术语表。text 或 terms 均可，一行一个，空行忽略。默认 scope=global，写 USER_DATA/presets/glossary.txt，可无头。scope=project 写进当前项目；无窗口时返回 needs_project。下次自动字幕会拼进 initial_prompt 与 hotwords。失败常见原因：工程未打开。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `scope` | string | 可选。global 或 project，默认 global |
| `text` | string | 可选。整段文本，按行切开 |
| `terms` | array | 可选。术语数组，与 text 二选一 |

CLI：

```
python -m cutora cmd glossary.set --json '{"scope": "", "text": "", "terms": []}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"scope": "", "text": "", "terms": []}' http://127.0.0.1:47321/v1/commands/glossary.set
```

## grade

### `grade.set` — 设置调色

把一级调色写到 clip / track / project。patch 与 identity_grade 同形。锁定轨拒绝。失败常见原因：找不到对象、patch 不是对象。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 必填。clip / track / project |
| `track_id` | string | 可选。轨道 id，target=clip 或 track |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `patch` | object | 必填。调色字段，缺的保持原值 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd grade.set --json '{"target": "clip", "track_id": "", "clip_index": 0, "patch": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "patch": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/grade.set
```

## group

### `group.create` — 预合成（打组）

把选中的片段打成一个预合成组：片段留在原轨道上，锁住时点一条选整组、拖一条整组走、改外观整组一起变；可选 loop_fill=以整组时长循环铺满到视频结尾。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `clips` | array | 必填。片段列表：[{track_id, clip_index}] 或 [{uid}] |
| `name` | string | 可选。组名（可选） |
| `color` | string | 可选。组颜色 #RRGGBB（可选，默认轮着给） |
| `locked` | boolean | 可选。锁住组内编辑（默认 true） |
| `loop_fill` | boolean | 可选。以整组时长循环铺满到视频结尾（默认 false） |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd group.create --json '{"clips": [], "name": "", "color": "", "locked": true, "loop_fill": true, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"clips": [], "name": "", "color": "", "locked": true, "loop_fill": true, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/group.create
```

### `group.detach` — 从预合成组分离

把若干片段从各自的预合成组里分离出来；一个组一条真身都不剩时它的重复份一起删掉。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `clips` | array | 必填。片段列表：[{track_id, clip_index}] 或 [{uid}] |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd group.detach --json '{"clips": [], "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"clips": [], "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/group.detach
```

### `group.join` — 加入预合成组

把若干片段加入一个已有的预合成组（在别的组里的先搬出来）；组开着循环铺满就顺手重算重复份。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `group` | string | 必填。组 id |
| `clips` | array | 必填。片段列表：[{track_id, clip_index}] 或 [{uid}] |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd group.join --json '{"group": "", "clips": [], "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"group": "", "clips": [], "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/group.join
```

### `group.set` — 设置预合成组

改一个预合成组的名字 / 颜色 / 锁定 / 循环铺满（patch 里给哪个改哪个）；loop_fill 开关后立即按整组时长重算重复份。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `group` | string | 必填。组 id |
| `patch` | object | 必填。{name, color, locked, loop_fill} 任意子集 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd group.set --json '{"group": "", "patch": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"group": "", "patch": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/group.set
```

### `group.ungroup` — 解除预合成

解除一个预合成组：组里的片段原样留在各自的轨道上（不再联动），循环铺满时自动复制出来的重复份一起删掉。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `group` | string | 必填。组 id |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd group.ungroup --json '{"group": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"group": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/group.ungroup
```

## keyframe

### `keyframe.copy` — 复制关键帧

把 path 在 t 的点放进关键帧剪贴板，随后 keyframe.paste 用。不改工程。失败常见原因：该处没有点。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id |
| `clip_index` | integer | 可选。片段下标 |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标 |
| `path` | string | 必填。参数路径 |
| `t` | number | 必填。相对对象起点的秒 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd keyframe.copy --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "path": "", "t": 0.0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "path": "", "t": 0.0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/keyframe.copy
```

### `keyframe.delete` — 删除关键帧

删掉 path 在局部时间 t 的那个点。该路径空了就摘掉路径。锁定轨拒绝。无窗口时给 project。失败常见原因：找不到对象或该处没有点。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id |
| `clip_index` | integer | 可选。片段下标 |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标 |
| `path` | string | 必填。参数路径 |
| `t` | number | 必填。相对对象起点的秒 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd keyframe.delete --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "path": "", "t": 0.0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "path": "", "t": 0.0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/keyframe.delete
```

### `keyframe.enable` — 启用或关闭参数关键帧

enabled=true 时用当前值在 t 打第一个点；false 时删掉该路径全部点。对应属性面板秒表开关。锁定轨拒绝。失败常见原因：找不到对象。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id |
| `clip_index` | integer | 可选。片段下标 |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标 |
| `path` | string | 必填。参数路径 |
| `enabled` | boolean | 必填。true 打开 / false 关闭 |
| `t` | number | 可选。打开时打点的局部秒 |
| `v` | number | 可选。打开时的数字值 |
| `color` | string | 可选。打开时的颜色 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd keyframe.enable --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "path": "", "enabled": true, "t": 0.0, "v": 0.0, "color": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "path": "", "enabled": true, "t": 0.0, "v": 0.0, "color": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/keyframe.enable
```

### `keyframe.move` — 移动关键帧

把 path 上 t_from 的点改到 t_to（相对对象起点）。锁定轨拒绝。无窗口时给 project。失败常见原因：找不到原时间的点。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id |
| `clip_index` | integer | 可选。片段下标 |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标 |
| `path` | string | 必填。参数路径 |
| `t_from` | number | 必填。原来的局部秒 |
| `t_to` | number | 必填。新的局部秒 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd keyframe.move --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "path": "", "t_from": 0.0, "t_to": 0.0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "path": "", "t_from": 0.0, "t_to": 0.0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/keyframe.move
```

### `keyframe.paste` — 粘贴关键帧

把剪贴板里的点贴到 path 的局部时间 t。没有剪贴板时失败。锁定轨拒绝。失败常见原因：还没复制过、找不到对象。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id |
| `clip_index` | integer | 可选。片段下标 |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标 |
| `path` | string | 可选。参数路径，省略则用剪贴板的 path |
| `t` | number | 必填。贴到这个局部秒 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd keyframe.paste --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "path": "", "t": 0.0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "path": "", "t": 0.0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/keyframe.paste
```

### `keyframe.replace` — 改写关键帧

把一个对象上若干参数的点表整段换掉（关键帧面板 / 曲线编辑器拖完松手时调用一次，一个手势一次撤销）。paths 形如 {"x": [{"t":0,"v":0,"ease":"bezier","bez":[0.33,0,0.67,1]}, …], "y": null}；null 或空数组 = 关掉该参数的关键帧。bez 的 x 在 0–1，y 在 -2–3（超出 0–1 即过冲）。锁定轨拒绝。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `element_index` | integer | 可选。覆层里 elements 的下标（可选） |
| `node_id` | string | 可选。效果节点图里的节点 id（可选；给了就改那个节点） |
| `paths` | object | 必填。{参数路径: 点数组或 null} |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd keyframe.replace --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "paths": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "paths": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/keyframe.replace
```

### `keyframe.set` — 打关键帧

在对象局部时间 t 给 path 写入一个关键帧（同 t 覆盖）。ease 为 linear/ease_in/ease_out/ease_in_out/hold/bezier；bezier 时带 bez 四个控制点。锁定轨拒绝。无窗口时给 project。失败常见原因：找不到对象、path 为空。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `path` | string | 必填。参数路径，如 x 或 fill.colors[0] |
| `t` | number | 必填。相对对象起点的秒 |
| `v` | number | 可选。数字值；颜色请用 color |
| `color` | string | 可选。颜色 #RRGGBB，path 为颜色时用 |
| `ease` | string | 可选。缓动 |
| `bez` | array | 可选。cubic-bezier 四元组 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd keyframe.set --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "path": "", "t": 0.0, "v": 0.0, "color": "", "ease": "linear", "bez": [], "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "path": "", "t": 0.0, "v": 0.0, "color": "", "ease": "linear", "bez": [], "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/keyframe.set
```

### `keyframe.set_interp` — 设置关键帧缓动

改 path 上 t 那个点的 ease（及可选 bez）。第一版没有曲线手柄，只改这六种缓动。锁定轨拒绝。失败常见原因：该处没有关键帧。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id |
| `clip_index` | integer | 可选。片段下标 |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标 |
| `path` | string | 必填。参数路径 |
| `t` | number | 必填。相对对象起点的秒 |
| `ease` | string | 必填。缓动 |
| `bez` | array | 可选。cubic-bezier 四元组 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd keyframe.set_interp --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "path": "", "t": 0.0, "ease": "linear", "bez": [], "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "path": "", "t": 0.0, "ease": "linear", "bez": [], "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/keyframe.set_interp
```

## marker

### `marker.add` — 添加标记

在当前视频 t 秒处加一个标记，返回 uuid。name 和 color 可省略。t 是绝对时间，与当前媒体绑定，不录入配方。失败常见原因：未选中视频。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `t` | number | 必填。绝对秒 |
| `name` | string | 可选。显示名，可空 |
| `color` | string | 可选。十六进制颜色，默认 #F5B700 |

CLI：

```
python -m cutora cmd marker.add --json '{"t": 0.0, "name": "", "color": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"t": 0.0, "name": "", "color": ""}' http://127.0.0.1:47321/v1/commands/marker.add
```

### `marker.move` — 移动标记

把指定 uuid 标记的时间改到 t 秒。t 是绝对时间，与当前媒体绑定，不录入配方。失败常见原因：id 不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | string | 必填。标记 uuid |
| `t` | number | 必填。绝对秒 |

CLI：

```
python -m cutora cmd marker.move --json '{"id": "", "t": 0.0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"id": "", "t": 0.0}' http://127.0.0.1:47321/v1/commands/marker.move
```

### `marker.remove` — 删除标记

按 uuid 列表删除当前视频上的标记。ids 里有找不到的 id 则失败。与当前媒体绑定，不录入配方。失败常见原因：id 不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `ids` | array | 必填。标记 uuid 列表 |

CLI：

```
python -m cutora cmd marker.remove --json '{"ids": []}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"ids": []}' http://127.0.0.1:47321/v1/commands/marker.remove
```

### `marker.rename` — 重命名标记

把指定 uuid 标记的 name 改成新字符串，空字符串表示只显示三角。与当前媒体绑定，不录入配方。失败常见原因：id 不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | string | 必填。标记 uuid |
| `name` | string | 必填。新名称，可空 |

CLI：

```
python -m cutora cmd marker.rename --json '{"id": "", "name": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"id": "", "name": ""}' http://127.0.0.1:47321/v1/commands/marker.rename
```

### `marker.set_color` — 设置标记颜色

把指定 uuid 标记的 color 改成 #RRGGBB。非法颜色回落到默认金。与当前媒体绑定，不录入配方。失败常见原因：id 不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | string | 必填。标记 uuid |
| `color` | string | 必填。#RRGGBB |

CLI：

```
python -m cutora cmd marker.set_color --json '{"id": "", "color": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"id": "", "color": ""}' http://127.0.0.1:47321/v1/commands/marker.set_color
```

## media

### `media.import` — 导入媒体

把一组视频路径导入当前工程（等价拖放），返回新条目的 media_ids（列表下标）。命令体不弹文件框。失败常见原因：paths 为空、文件不是可识别视频。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `paths` | array | 必填。视频或目录路径 |

CLI：

```
python -m cutora cmd media.import --json '{"paths": []}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"paths": []}' http://127.0.0.1:47321/v1/commands/media.import
```

### `media.info` — 媒体详情

返回指定 id 那一条视频的路径、文案长度、字幕段数、画布尺寸。失败常见原因：id 越界或工程为空。

- 可撤销：否
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | integer | 必填。媒体下标 |

CLI：

```
python -m cutora cmd media.info --json '{"id": 0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"id": 0}' http://127.0.0.1:47321/v1/commands/media.info
```

### `media.list` — 列出媒体

列出当前工程里每一条视频的 id、文件名、路径与时长，不改动状态。空工程返回空数组。给机器人确认导入是否成功用。

- 可撤销：否
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

CLI：

```
python -m cutora cmd media.list --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/media.list
```

### `media.probe` — 检查素材

用本机 ffprobe 读一条视频的时长。不是视频或时长接近 0 时失败。不改工程、不渲染。给配方在导入前过滤坏文件。失败常见原因：路径不存在、假 mp4。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `path` | string | 必填。视频路径 |

CLI：

```
python -m cutora cmd media.probe --json '{"path": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"path": ""}' http://127.0.0.1:47321/v1/commands/media.probe
```

### `media.relink` — 重链一条素材

把工程里 source_id 对应的文件改到 new_path，并重新探测离线/时长。含绝对路径，不进配方。失败常见原因：新路径不是文件、id 对不上。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `source_id` | string | 必填。时间线 sources 的 id，或 video |
| `new_path` | string | 必填。新文件的绝对路径 |

CLI：

```
python -m cutora cmd media.relink --json '{"source_id": "", "new_path": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"source_id": "", "new_path": ""}' http://127.0.0.1:47321/v1/commands/media.relink
```

### `media.relink_folder` — 整批换素材根目录

把工程里所有以 old_root 开头的素材路径改成 new_root 下的对应文件。用户把 D:\素材\2026 挪到 E:\素材\2026 时用这一条。含绝对路径，不进配方。失败常见原因：新根目录不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `old_root` | string | 必填。原来的素材根目录 |
| `new_root` | string | 必填。新的素材根目录 |

CLI：

```
python -m cutora cmd media.relink_folder --json '{"old_root": "", "new_root": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"old_root": "", "new_root": ""}' http://127.0.0.1:47321/v1/commands/media.relink_folder
```

### `media.remove` — 移除媒体

按 media_ids（列表下标）从当前工程删除视频条目，不可恢复，除非随后撤销。命令体不弹确认框。失败常见原因：id 越界、尚未导入视频。

- 可撤销：是
- 危险：是（API 需 confirm=true）
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `ids` | array | 必填。要删除的媒体下标 |

CLI：

```
python -m cutora cmd media.remove --json '{"ids": []}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"ids": []}' http://127.0.0.1:47321/v1/commands/media.remove
```

### `media.select` — 选中媒体

把资产库选中项设为 ids 数组（多选），并让第一个 id 成为当前编辑条目。失败常见原因：id 越界。不改时间轴内容。

- 可撤销：否
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `ids` | array | 必填。要选中的媒体下标 |

CLI：

```
python -m cutora cmd media.select --json '{"ids": []}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"ids": []}' http://127.0.0.1:47321/v1/commands/media.select
```

## overlay

### `overlay.insert_sticker` — 插入贴纸

按 Unicode codepoint（sticker_id）在画面中心插入一张贴纸，默认边长为画面短边的 12%。走现有图片元素路径。无窗口时给 project。失败常见原因：未知 sticker_id、工程无匹配任务。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `sticker_id` | string | 必填。Unicode codepoint，如 1f525 |
| `anim_in` | string | 可选。淡入/缩放入/无 |
| `anim_out` | string | 可选。淡出/缩放/无 |
| `start` | number | 可选。开始秒，默认 0 |
| `end` | number | 可选。结束秒，默认主视频时长 |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd overlay.insert_sticker --json '{"sticker_id": "", "anim_in": "fade", "anim_out": "fade", "start": 0.0, "end": 0.0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"sticker_id": "", "anim_in": "fade", "anim_out": "fade", "start": 0.0, "end": 0.0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/overlay.insert_sticker
```

### `overlay.set_fade` — 设置叠层四边渐隐

把 fade_top/bottom/left/right（0–0.5，包围盒比例）写到图片覆层、图片/形状元素或视频片段。缺字段视为 0。无窗口时给 project。失败常见原因：下标不存在、工程无匹配任务。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 必填。overlay / element / clip |
| `overlay_index` | integer | 可选。image_overlays 下标 |
| `element_index` | integer | 可选。elements 下标，仅 target=element |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `fade_top` | number | 可选。上渐隐 0–0.5 |
| `fade_bottom` | number | 可选。下渐隐 0–0.5 |
| `fade_left` | number | 可选。左渐隐 0–0.5 |
| `fade_right` | number | 可选。右渐隐 0–0.5 |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd overlay.set_fade --json '{"target": "overlay", "overlay_index": 0, "element_index": 0, "track_id": "", "clip_index": 0, "fade_top": 0.0, "fade_bottom": 0.0, "fade_left": 0.0, "fade_right": 0.0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "overlay", "overlay_index": 0, "element_index": 0, "track_id": "", "clip_index": 0, "fade_top": 0.0, "fade_bottom": 0.0, "fade_left": 0.0, "fade_right": 0.0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/overlay.set_fade
```

### `overlay.split` — 切开覆层

在 t 秒处把 image/text 覆层拆成两段，右半从切开点起。undoable，portable=False（按下标寻址）。锁定轨拒绝。失败常见原因：kind 非法、下标越界、切开点不在覆层内、轨道已锁定。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `kind` | string | 必填。image 或 text |
| `idx` | integer | 必填。覆层下标，从 0 起 |
| `t` | number | 必填。切开点（秒） |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd overlay.split --json '{"kind": "image", "idx": 0, "t": 0.0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"kind": "image", "idx": 0, "t": 0.0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/overlay.split
```

## project

### `project.autosave.restore` — 恢复自动保存

把残留的自动保存加载进当前窗口，不自动覆盖工程文件。用户保存才写回。失败常见原因：没有残留文件。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `path` | string | 可选。sidecar 路径；省略则用当前工程旁的残留 |

CLI：

```
python -m cutora cmd project.autosave.restore --json '{"path": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"path": ""}' http://127.0.0.1:47321/v1/commands/project.autosave.restore
```

### `project.autosave.status` — 自动保存状态

返回自动保存间隔、是否有残留的 .autosave.json（上次未正常退出）。不改文件。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

CLI：

```
python -m cutora cmd project.autosave.status --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/project.autosave.status
```

### `project.close` — 关闭工程

清空任务列表并忘掉当前工程路径，界面回到欢迎页（若有）。不保存；未保存的改动会被丢弃。不弹确认框。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

CLI：

```
python -m cutora cmd project.close --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/project.close
```

### `project.info` — 工程摘要

返回当前工程路径、视频条数、画布尺寸、当前选中下标，不改动任何状态。没有工程时仍返回空摘要而不是报错，方便机器人探活。

- 可撤销：否
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

CLI：

```
python -m cutora cmd project.info --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/project.info
```

### `project.new` — 新建工程

清空当前任务列表、关闭已打开工程路径，按当前画布模板开始一份空白工程。不弹窗。已有未保存改动会被丢弃，调用方应先自行保存。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

CLI：

```
python -m cutora cmd project.new --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/project.new
```

### `project.open` — 打开工程

从给定的 .asproj.json 路径加载工程到主窗口。命令体不弹文件框。失败常见原因：路径不存在、版本不支持、JSON 损坏。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `path` | string | 必填。工程文件路径 |

CLI：

```
python -m cutora cmd project.open --json '{"path": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"path": ""}' http://127.0.0.1:47321/v1/commands/project.open
```

### `project.pack` — 打包工程

把当前工程用到的全部素材拷到 out_dir/media/，路径改成相对工程文件，写成 project.cutora。mode=copy 默认；move 会搬走原文件。含绝对路径，不进配方。失败常见原因：out_dir 为空。收不到的素材列在 missing。

- 可撤销：否
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `out_dir` | string | 必填。打包输出目录 |
| `mode` | string | 可选。copy 或 move，默认 copy |

CLI：

```
python -m cutora cmd project.pack --json '{"out_dir": "", "mode": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"out_dir": "", "mode": ""}' http://127.0.0.1:47321/v1/commands/project.pack
```

### `project.save` — 保存工程

把当前工程写回 project_path。若尚未指定路径则失败，应改用 project.save_as。命令体不弹成功/失败对话框。失败常见原因：未打开工程、磁盘只读。

- 可撤销：否
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

CLI：

```
python -m cutora cmd project.save --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/project.save
```

### `project.save_as` — 另存工程

把当前工程写到指定路径并记住为当前 project_path。命令体不弹文件框。失败常见原因：path 为空、目录不可写。

- 可撤销：否
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `path` | string | 必填。目标 .asproj.json 路径 |

CLI：

```
python -m cutora cmd project.save_as --json '{"path": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"path": ""}' http://127.0.0.1:47321/v1/commands/project.save_as
```

## recipe

### `recipe.apply_folder` — 套用配方到文件夹

对文件夹里每个视频按配方逐步执行。subtitle.auto 串行占 GPU；失败时按该步 on_error=stop|skip 处理。写出 _cutora_batch_report.json。失败常见原因：配方不合法、目录没有视频。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：是

| 参数 | 类型 | 说明 |
|---|---|---|
| `recipe` | string | 必填。配方文件路径 |
| `folder` | string | 必填。素材文件夹 |
| `out_dir` | string | 必填。输出目录 |
| `jobs` | integer | 可选。并行数，默认 min(2, cpu//4) |
| `recursive` | boolean | 可选。是否递归子目录 |

CLI：

```
python -m cutora cmd recipe.apply_folder --json '{"recipe": "", "folder": "", "out_dir": "", "jobs": 0, "recursive": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"recipe": "", "folder": "", "out_dir": "", "jobs": 0, "recursive": true}' http://127.0.0.1:47321/v1/commands/recipe.apply_folder
```

### `recipe.record_start` — 开始录制配方

开始记录来源为 ui、可撤销、且 portable=True 的命令。含下标或媒体绑定的操作不录，状态栏会提示。媒体路径会换成 {media}，输出目录换成 {out_dir}。失败常见原因：总线未初始化。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

CLI：

```
python -m cutora cmd recipe.record_start --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/recipe.record_start
```

### `recipe.record_stop` — 停止录制配方

结束录制并返回已记下的步骤列表，供保存为 .cutorarecipe。不写盘。失败常见原因：尚未开始录制（此时返回空列表）。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

CLI：

```
python -m cutora cmd recipe.record_stop --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/recipe.record_stop
```

## split

### `split.set_divider` — 设置分界线

改一条全局分界线的位置、旋转或羽化，并联动重算相邻格子 rect。分界线是工程级的，所有参与轨共用。不录入配方。失败常见原因：分界线不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | string | 必填。分界线 id，如 d1 |
| `pos` | number | 可选。归一化位置 0–1 |
| `rotation_deg` | number | 可选。旋转角度 |
| `feather_px` | number | 可选。羽化像素 |

CLI：

```
python -m cutora cmd split.set_divider --json '{"id": "", "pos": 0.0, "rotation_deg": 0.0, "feather_px": 0.0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"id": "", "pos": 0.0, "rotation_deg": 0.0, "feather_px": 0.0}' http://127.0.0.1:47321/v1/commands/split.set_divider
```

### `split.set_layout` — 设置分屏布局

写入工程级 task.split：选预设生成格子和分界线，或开关分屏、改描边。任何轨道开启分屏即工程开启。不录入配方。失败常见原因：未打开工程、未知布局。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `layout` | string | 可选。top_bottom / left_right / left_right_fit / grid_2x2 / grid_3x3 / left1_right2 / top1_bottom2 / custom |
| `enabled` | boolean | 可选。是否开启分屏；省略且给了 layout 则开启 |
| `border` | object | 可选。可选描边补丁 style/width_px/color/curve/shadow_px/glass_sigma |
| `backdrop` | object | 可选。分屏留白处的背景补丁 mode(each 每格各自 / full 单个视频撑满整幅（任何布局）/ first 老左右完整整幅)/cell(full 用哪一格的视频)/blur(0–100)/style(lens/legacy)/highlights/dim |

CLI：

```
python -m cutora cmd split.set_layout --json '{"layout": "", "enabled": true, "border": "", "backdrop": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"layout": "", "enabled": true, "border": "", "backdrop": ""}' http://127.0.0.1:47321/v1/commands/split.set_layout
```

## stitch

### `stitch.to_timeline` — 拼接成时间线

读 task_dir 下的 cutlist.json，把保留片段铺成一条视频轨的 ClipPlacement 列表（source_path / in_t / out_t / start_t / link_audio），fit_mode=fill 铺满画布。不生成 _stitched 中间 mp4。portable，按目录寻址。失败常见原因：cutlist.json 不存在。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `task_dir` | string | 必填。任务目录，内含 cutlist.json 或 _stitched/cutlist.json |

CLI：

```
python -m cutora cmd stitch.to_timeline --json '{"task_dir": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"task_dir": ""}' http://127.0.0.1:47321/v1/commands/stitch.to_timeline
```

## style

### `style.apply_keyword_preset` — 套用关键词预设

按名称读取关键词着色规则并套到当前选中视频，高亮词表与颜色一并替换。失败常见原因：name 不存在、未选中视频。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `name` | string | 必填。预设名 |

CLI：

```
python -m cutora cmd style.apply_keyword_preset --json '{"name": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"name": ""}' http://127.0.0.1:47321/v1/commands/style.apply_keyword_preset
```

### `style.apply_subtitle_preset` — 套用字幕样式预设

按名称读取字幕样式预设并套到当前视频的 style。失败常见原因：name 不存在、未选中视频。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `name` | string | 必填。预设名 |

CLI：

```
python -m cutora cmd style.apply_subtitle_preset --json '{"name": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"name": ""}' http://127.0.0.1:47321/v1/commands/style.apply_subtitle_preset
```

### `style.apply_text_preset` — 套用文本样式预设

按名称读取文本覆层预设。只返回样式数据并交给当前文本覆层逻辑；失败常见原因：name 不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `name` | string | 必填。预设名 |

CLI：

```
python -m cutora cmd style.apply_text_preset --json '{"name": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"name": ""}' http://127.0.0.1:47321/v1/commands/style.apply_text_preset
```

### `style.list_presets` — 列出样式预设

列出字幕、文本、关键词三类预设的名称。不套用、不改文件。给机器人先查有什么预设再 apply。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

CLI：

```
python -m cutora cmd style.list_presets --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/style.list_presets
```

### `style.save_current_as` — 保存当前为预设

把当前字幕样式存成名为 name 的用户风格包，风格页和预设下拉都是这一份。同名用户包会原地更新。失败常见原因：name 为空、未选中视频。

- 可撤销：否
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `name` | string | 必填。新预设名 |

CLI：

```
python -m cutora cmd style.save_current_as --json '{"name": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"name": ""}' http://127.0.0.1:47321/v1/commands/style.save_current_as
```

## stylepack

### `stylepack.apply` — 应用风格包

把 path 指向的风格包套到当前或指定视频。apply_animation=false 时保留用户现有动画。字体不在打包集里则退回默认。无窗口时给 project。失败常见原因：路径无效。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `path` | string | 必填。风格包目录 |
| `apply_animation` | boolean | 可选。是否覆盖动画，默认 true |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd stylepack.apply --json '{"path": "", "apply_animation": true, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"path": "", "apply_animation": true, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/stylepack.apply
```

### `stylepack.convert_preset` — 预设转风格包

字幕预设就是风格包。按显示名找到那一个目录并返回路径，不另写第二份。失败常见原因：没有这个名字。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `name` | string | 必填。字幕预设名 |

CLI：

```
python -m cutora cmd stylepack.convert_preset --json '{"name": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"name": ""}' http://127.0.0.1:47321/v1/commands/stylepack.convert_preset
```

### `stylepack.delete` — 删除用户风格包

删除用户目录里的一个风格包目录。拒绝内置包。失败常见原因：路径不是风格包、试图删内置。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `path` | string | 必填。风格包目录 |

CLI：

```
python -m cutora cmd stylepack.delete --json '{"path": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"path": ""}' http://127.0.0.1:47321/v1/commands/stylepack.delete
```

### `stylepack.export_file` — 导出风格包

把一个风格包目录打成 .cutorastyle zip。失败常见原因：目录不是风格包、路径不可写。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `pack_dir` | string | 必填。风格包目录 |
| `path` | string | 必填。输出 zip 路径 |

CLI：

```
python -m cutora cmd stylepack.export_file --json '{"pack_dir": "", "path": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"pack_dir": "", "path": ""}' http://127.0.0.1:47321/v1/commands/stylepack.export_file
```

### `stylepack.import_file` — 导入风格包

把 .cutorastyle zip 解到用户风格包目录。拒绝绝对路径、父目录穿越、非白名单扩展名。失败常见原因：压缩包损坏、含恶意路径。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `path` | string | 必填。.cutorastyle 或 .zip 路径 |

CLI：

```
python -m cutora cmd stylepack.import_file --json '{"path": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"path": ""}' http://127.0.0.1:47321/v1/commands/stylepack.import_file
```

### `stylepack.list` — 列出风格包

列出内置与用户目录里的风格包：id、名称、标签、是否缺字体、预览路径。不改工程。失败常见原因：目录不可读。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

CLI：

```
python -m cutora cmd stylepack.list --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/stylepack.list
```

### `stylepack.rename` — 重命名用户风格包

改用户风格包 meta.json 里的显示名，不改目录名。拒绝内置包。失败常见原因：name 为空、不是用户包。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `path` | string | 必填。风格包目录 |
| `name` | string | 必填。新显示名 |

CLI：

```
python -m cutora cmd stylepack.rename --json '{"path": "", "name": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"path": "", "name": ""}' http://127.0.0.1:47321/v1/commands/stylepack.rename
```

### `stylepack.save_current` — 保存当前为风格包

把当前视频的字幕样式写成用户目录下的一个风格包。失败常见原因：未选中视频、name 为空。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `name` | string | 必填。风格包显示名 |
| `tags` | string | 可选。逗号分隔标签，默认用户 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd stylepack.save_current --json '{"name": "", "tags": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"name": "", "tags": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/stylepack.save_current
```

### `stylepack.update` — 覆盖保存用户风格包

把当前字幕样式写回 path 指向的用户风格包，保留原 id 与目录，更新 meta.updated_at。拒绝内置包。失败常见原因：path 是内置目录、不是风格包、未选中视频。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `path` | string | 必填。用户风格包目录 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd stylepack.update --json '{"path": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"path": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/stylepack.update
```

## subtitle

### `subtitle.apply_glossary` — 按词典批量替换

用两列 CSV（csv）或 pairs 列表在字幕里做查找替换，长词优先，改过的句子锁定。不另建存储。无窗口时给 project。失败常见原因：CSV 读不到。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `csv` | string | 可选。两列 CSV 路径，左查找右替换 |
| `pairs` | array | 可选。[{from,to}, ...] 或二元数组 |
| `scope` | string | 可选。all 全部 / selected 选中 |
| `seg_ids` | array | 可选。只处理这些分段；优先于 scope=selected |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.apply_glossary --json '{"csv": "", "pairs": [], "scope": "all", "seg_ids": [], "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"csv": "", "pairs": [], "scope": "all", "seg_ids": [], "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.apply_glossary
```

### `subtitle.apply_preset` — 套用字幕预设

把名为 name 的字幕样式预设套到当前（或 media_id）视频，效果与样式面板下拉框相同。失败常见原因：预设不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `name` | string | 必填。预设名 |
| `media_id` | integer | 可选。媒体下标，省略则用当前选中 |

CLI：

```
python -m cutora cmd subtitle.apply_preset --json '{"name": "", "media_id": 0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"name": "", "media_id": 0}' http://127.0.0.1:47321/v1/commands/subtitle.apply_preset
```

### `subtitle.auto` — 自动字幕

对当前工程跑「一键全自动」对齐：有文案但还没有分段的条目会打轴；force=true 时覆盖已有时间轴。需要图形界面，不能无头调用。配方里请用 skip_if + subtitle.status 跳过已有字幕的文件。失败常见原因：没有文案、模型未安装、未打开工程。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：是

| 参数 | 类型 | 说明 |
|---|---|---|
| `media_id` | integer | 可选。只处理这一条；省略则按界面规则处理全部 |
| `language` | string|array[string] | 可选。省略 = 用当前设置的语言；"auto" = 听前 30 秒自动检测。也可传语言代码数组，对同一素材依次识别。 |
| `model` | string | 可选。识别档位 small / medium / large-v3 / large-v3-turbo；省略则用当前已装模型 |
| `glossary` | string|array[string] | 可选。本次额外术语，字符串或字符串数组，一行一个，拼进 initial_prompt 与 hotwords（前 200 token） |
| `force` | boolean | 可选。true 时强制重打轴 |

CLI：

```
python -m cutora cmd subtitle.auto --json '{"media_id": 0, "language": "", "model": "", "glossary": "", "force": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"media_id": 0, "language": "", "model": "", "glossary": "", "force": true}' http://127.0.0.1:47321/v1/commands/subtitle.auto
```

### `subtitle.check_cps` — 检查阅读速度

列出全部 CPS（字符数÷时长）超过阈值的句子。默认阈值 17。不改时间轴。无窗口时给 project。失败常见原因：工程无匹配任务。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `threshold` | number | 可选。CPS 上限，默认 17 |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.check_cps --json '{"threshold": 0.0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"threshold": 0.0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.check_cps
```

### `subtitle.check_overlong` — 检查超长字幕

列出字符数超过 max_chars_per_line×max_lines 的句子（会超出画面）。只读。无窗口时给 project。失败常见原因：工程无匹配任务。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.check_overlong --json '{"media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.check_overlong
```

### `subtitle.delete` — 删除字幕

删除指定分段，只删字幕不删视频。不可恢复，除非随后撤销。命令体不弹确认框。无窗口时给 project。失败常见原因：seg_id 越界。

- 可撤销：是
- 危险：是（API 需 confirm=true）
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `seg_id` | string|integer | 必填。分段 uuid；整数下标仅迁移旧工程 |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.delete --json '{"seg_id": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"seg_id": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.delete
```

### `subtitle.delete_segments` — 删除多条字幕

按 seg_ids 删除多条字幕，只删字幕不删视频。下标从大到小删以免错位。对应时间线 Delete。命令体不弹确认框。失败常见原因：seg_id 越界。

- 可撤销：是
- 危险：是（API 需 confirm=true）
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `seg_ids` | array | 必填。分段 uuid 列表；整数下标仅迁移旧工程 |
| `media_id` | integer | 可选。媒体下标，省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.delete_segments --json '{"seg_ids": [], "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"seg_ids": [], "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.delete_segments
```

### `subtitle.duplicate_segments` — 复制字幕段

把指定字幕段复制一份，整组平移到 time 秒（最早一条对齐播放头），words 同步平移。可传 seg_ids 或 items 快照。与当前媒体绑定，不录入配方。失败常见原因：seg_id 越界、没有可复制的段。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `seg_ids` | array | 可选。分段 uuid 列表；整数下标仅迁移旧工程 |
| `items` | array | 可选。字幕段快照 |
| `time` | number | 可选。复制后最早一条的开始秒 |
| `media_id` | integer | 可选。媒体下标，省略则用当前选中 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd subtitle.duplicate_segments --json '{"seg_ids": [], "items": [], "time": 0.0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"seg_ids": [], "items": [], "time": 0.0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.duplicate_segments
```

### `subtitle.emphasis.auto` — 设置自动强调

写 style.fx.emphasis：auto 规则、词表、每条上限。add_word / remove_word 给 Transcript 右键强调。无窗口时给 project。失败常见原因：工程无匹配任务。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `auto` | array | 可选。规则 numbers/caps/glossary/longest/list |
| `words` | array | 可选。显式强调词，整词匹配 |
| `add_word` | string | 可选。加入词表 |
| `remove_word` | string | 可选。从词表去掉 |
| `max_per_cue` | integer | 可选。自动强调每条上限 0–8，0=不限 |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.emphasis.auto --json '{"auto": [], "words": [], "add_word": "", "remove_word": "", "max_per_cue": 0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"auto": [], "words": [], "add_word": "", "remove_word": "", "max_per_cue": 0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.emphasis.auto
```

### `subtitle.export_file` — 导出字幕文件

把当前或指定视频的字幕写成 SRT / WebVTT / ASS / TXT / JSON。SRT 默认 UTF-8 无 BOM，bom=true 时写 BOM。ASS 在有 words 时用 \k 卡拉OK。无窗口时必须给 project。失败常见原因：没有字幕、路径不可写。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `path` | string | 必填。输出文件路径，扩展名决定格式 |
| `format` | string | 可选。srt/vtt/ass/ssa/txt/json；省略则按扩展名 |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |
| `bom` | boolean | 可选。仅 SRT：true 时写出 UTF-8 BOM |
| `karaoke` | boolean | 可选。仅 ASS：false 时不写逐字 \k 标签 |

CLI：

```
python -m cutora cmd subtitle.export_file --json '{"path": "", "format": "", "media_id": 0, "media": "", "project": "", "bom": true, "karaoke": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"path": "", "format": "", "media_id": 0, "media": "", "project": "", "bom": true, "karaoke": true}' http://127.0.0.1:47321/v1/commands/subtitle.export_file
```

### `subtitle.export_srt` — 导出 SRT

把一条视频的字幕分段写成 .srt 到 out_dir，不启动渲染子进程。优先用 project 参数或同目录 project.asproj.json 里的分段。失败常见原因：没有字幕、out_dir 不可写。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `media` | string | 必填。视频路径 |
| `out_dir` | string | 必填。SRT 输出目录 |
| `project` | string | 可选。可选工程文件，用来取字幕 |

CLI：

```
python -m cutora cmd subtitle.export_srt --json '{"media": "", "out_dir": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"media": "", "out_dir": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.export_srt
```

### `subtitle.extend_durations` — 延长显示时间

把超 CPS 的句子延长到阈值以内，但不与下一句重叠（保留 min_gap）。跳过 locked。无窗口时给 project。失败常见原因：工程无字幕。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `threshold` | number | 可选。CPS 上限，默认 17 |
| `min_gap` | number | 可选。与下一句的最小间隔秒，默认 0.08 |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.extend_durations --json '{"threshold": 0.0, "min_gap": 0.0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"threshold": 0.0, "min_gap": 0.0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.extend_durations
```

### `subtitle.find` — 查找字幕文本

在当前或指定视频的全部分句里查找 query，可开正则与区分大小写。只读，不改工程。无窗口时给 project。失败常见原因：工程无匹配任务。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `query` | string | 必填。查找串或正则 |
| `regex` | boolean | 可选。true 时按正则 |
| `case` | boolean | 可选。true 时区分大小写 |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.find --json '{"query": "", "regex": true, "case": true, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"query": "", "regex": true, "case": true, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.find
```

### `subtitle.import_file` — 导入字幕文件

从 SRT / WebVTT / ASS / TXT / JSON 读取字幕写入当前或指定视频。mode=replace 替换当前轨，append 追加。TXT 只填文案、不写 segments（待对齐）。无窗口时必须给 project（或视频同目录工程）。失败常见原因：文件不存在、工程无匹配任务。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `path` | string | 必填。字幕文件路径 |
| `mode` | string | 可选。replace 替换当前 / append 追加 |
| `format` | string | 可选。srt/vtt/ass/ssa/txt/json；省略则按扩展名 |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.import_file --json '{"path": "", "mode": "replace", "format": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"path": "", "mode": "replace", "format": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.import_file
```

### `subtitle.list` — 列出字幕

列出指定媒体（默认当前选中）的字幕分段：下标、起止时间、文本、角色。不改动时间轴。失败常见原因：media_id 越界。

- 可撤销：否
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `media_id` | integer | 可选。媒体下标，省略则用当前选中 |

CLI：

```
python -m cutora cmd subtitle.list --json '{"media_id": 0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"media_id": 0}' http://127.0.0.1:47321/v1/commands/subtitle.list
```

### `subtitle.lock` — 锁定或解锁字幕

把指定句子的 locked 设为 true 或 false。锁定后自动断行和时间轴优化会跳过。无窗口时给 project。失败常见原因：工程无匹配任务。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `seg_id` | string|integer | 可选。单个分段；也可只用 seg_ids |
| `seg_ids` | array | 可选。分段 uuid 列表；整数下标仅迁移旧工程 |
| `locked` | boolean | 可选。true 锁定 / false 解锁，默认 true |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.lock --json '{"seg_id": "", "seg_ids": [], "locked": true, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"seg_id": "", "seg_ids": [], "locked": true, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.lock
```

### `subtitle.merge` — 合并字幕

把 seg_id 与相邻段合并成一条，时间取并集、文本拼接，结果锁定。direction=next 与下一句并，prev 与上一句并。无窗口时给 project。失败常见原因：没有相邻段。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `seg_id` | string|integer | 必填。分段 uuid；整数下标仅迁移旧工程 |
| `direction` | string | 可选。next 与下一句 / prev 与上一句 |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.merge --json '{"seg_id": "", "direction": "next", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"seg_id": "", "direction": "next", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.merge
```

### `subtitle.move_segments` — 移动字幕段

把一组字幕段平移到新的 start，时长不变，words 同步平移。对应时间线多选拖动松手一次。失败常见原因：seg_id 越界。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `segments` | array | 必填。[{seg_id, start}, ...] |
| `media_id` | integer | 可选。媒体下标，省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.move_segments --json '{"segments": [], "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"segments": [], "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.move_segments
```

### `subtitle.relayout` — 按当前排版重新断句

按当前字号、行数和边距重新断行并切分字幕，写入 segment.lines，保留原文与词时间、不重跑 ASR。装不下就在词边界拆成多条，行数硬上限。跳过 locked。无窗口时给 project。失败常见原因：工程无字幕。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `scope` | string | 可选。all 全部 / selected 选中 |
| `seg_ids` | array | 可选。只处理这些分段；优先于 scope=selected |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |
| `max_chars` | integer | 可选。每行最多字符，默认 42 |
| `max_lines` | integer | 可选。最多行数，默认 2 |

CLI：

```
python -m cutora cmd subtitle.relayout --json '{"scope": "all", "seg_ids": [], "media_id": 0, "media": "", "project": "", "max_chars": 0, "max_lines": 0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"scope": "all", "seg_ids": [], "media_id": 0, "media": "", "project": "", "max_chars": 0, "max_lines": 0}' http://127.0.0.1:47321/v1/commands/subtitle.relayout
```

### `subtitle.replace` — 替换字幕文本

在全部分句或选中句里把 query 换成 replacement，可开正则与区分大小写。改过的句子走 LCS 保时间并锁定。无窗口时给 project。失败常见原因：工程无字幕。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `query` | string | 必填。查找串或正则 |
| `replacement` | string | 可选。替换为 |
| `regex` | boolean | 可选。true 时按正则 |
| `case` | boolean | 可选。true 时区分大小写 |
| `scope` | string | 可选。all 全部 / selected 选中 |
| `seg_ids` | array | 可选。只处理这些分段；优先于 scope=selected |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.replace --json '{"query": "", "replacement": "", "regex": true, "case": true, "scope": "all", "seg_ids": [], "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"query": "", "replacement": "", "regex": true, "case": true, "scope": "all", "seg_ids": [], "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.replace
```

### `subtitle.retime_segments` — 变速字幕段

按 pivot + scale 缩放一组字幕段的起止时间，words 用同一线性映射。t' = pivot + (t - pivot) * scale。对应时间线两端拉伸松手。失败常见原因：seg_id 越界、scale 非正。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `seg_ids` | array | 必填。分段 uuid 列表；整数下标仅迁移旧工程 |
| `pivot` | number | 必填。不动点（秒） |
| `scale` | number | 必填。时长倍率，必须为正 |
| `media_id` | integer | 可选。媒体下标，省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.retime_segments --json '{"seg_ids": [], "pivot": 0.0, "scale": 0.0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"seg_ids": [], "pivot": 0.0, "scale": 0.0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.retime_segments
```

### `subtitle.set_text` — 改字幕文本

把指定分段的文本改成 text。有 words 时用最长公共子序列保住时间，对不上的词按字符长度在句内插值，并锁定该句。无窗口时给 project。失败常见原因：seg_id 未找到。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `seg_id` | string|integer | 必填。分段 uuid；整数下标仅迁移旧工程 |
| `text` | string | 必填。新文本 |
| `media_id` | integer | 可选。媒体下标，省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.set_text --json '{"seg_id": "", "text": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"seg_id": "", "text": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.set_text
```

### `subtitle.split` — 拆分字幕

在光标字符 at_char 或 time 秒处把一句拆成两段，时间按字符比例切，两侧锁定。都省略则取中点。无窗口时给 project。失败常见原因：seg_id 越界、切完有一侧为空。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `seg_id` | string|integer | 必填。分段 uuid；整数下标仅迁移旧工程 |
| `time` | number | 可选。拆分点（秒）；省略则取中点 |
| `at_char` | integer | 可选。按字符下标拆；优先于 time |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.split --json '{"seg_id": "", "time": 0.0, "at_char": 0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"seg_id": "", "time": 0.0, "at_char": 0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.split
```

### `subtitle.status` — 字幕状态

只读：查一条视频或当前工程有没有字幕分段。返回 has_segments、segment_count、language。不启动识别、不改工程。配方 skip_if 用它决定要不要跑 subtitle.auto。失败常见原因：media 路径无效。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `media_id` | integer | 可选。媒体下标；省略则用当前选中或 media 路径 |
| `media` | string | 可选。视频路径，无窗口时用来读同目录工程 |
| `project` | string | 可选。可选工程文件 |

CLI：

```
python -m cutora cmd subtitle.status --json '{"media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.status
```

### `subtitle.strip_end_punct` — 移除句末标点

去掉全部或选中句末尾的标点和空白，改过的句子锁定并重映射时间。无窗口时给 project。失败常见原因：工程无匹配任务。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `scope` | string | 可选。all 全部 / selected 选中 |
| `seg_ids` | array | 可选。只处理这些分段；优先于 scope=selected |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.strip_end_punct --json '{"scope": "all", "seg_ids": [], "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"scope": "all", "seg_ids": [], "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.strip_end_punct
```

### `subtitle.transform_case` — 变换字幕大小写

对全部或选中句做全部大写 / 全部小写 / 首字母大写，改过的句子锁定并重映射时间。无窗口时给 project。失败常见原因：mode 不是 upper/lower/title。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `mode` | string | 必填。upper 全大写 / lower 全小写 / title 首字母大写 |
| `scope` | string | 可选。all 全部 / selected 选中 |
| `seg_ids` | array | 可选。只处理这些分段；优先于 scope=selected |
| `media_id` | integer | 可选。媒体下标；有窗口时省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.transform_case --json '{"mode": "upper", "scope": "all", "seg_ids": [], "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"mode": "upper", "scope": "all", "seg_ids": [], "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.transform_case
```

### `subtitle.trim_segment` — 裁剪字幕段

把指定字幕段的 start/end 写成给定秒，并把 words 从旧窗口线性映射到新窗口。对应时间线拖边缘松手一次。失败常见原因：seg_id 越界。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `seg_id` | string|integer | 必填。分段 uuid；整数下标仅迁移旧工程 |
| `start` | number | 必填。新的开始（秒） |
| `end` | number | 必填。新的结束（秒） |
| `media_id` | integer | 可选。媒体下标，省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.trim_segment --json '{"seg_id": "", "start": 0.0, "end": 0.0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"seg_id": "", "start": 0.0, "end": 0.0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.trim_segment
```

### `subtitle.trim_word` — 微调词边界

拖一条字幕里第 word_index 个词的左或右边界到 t 秒，相邻词的共享边界跟着让位，其余词的时间一个都不改。钳制重叠和负时长。对应时间线展开后拖词块松手。失败常见原因：seg_id / word_index 越界。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `seg_id` | string|integer | 必填。分段 uuid；整数下标仅迁移旧工程 |
| `word_index` | integer | 必填。词下标，从 0 起 |
| `edge` | string | 必填。left 左边界 / right 右边界 |
| `t` | number | 必填。新的边界时间（秒） |
| `media_id` | integer | 可选。媒体下标，省略则用当前选中 |
| `media` | string | 可选。视频路径，无窗口时用来匹配工程任务 |
| `project` | string | 可选。工程文件；无窗口且未给则试视频同目录 |

CLI：

```
python -m cutora cmd subtitle.trim_word --json '{"seg_id": "", "word_index": 0, "edge": "left", "t": 0.0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"seg_id": "", "word_index": 0, "edge": "left", "t": 0.0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/subtitle.trim_word
```

### `subtitle.word_appear.set` — 逐词出现

打开或关闭「逐词出现」（内部对应 metronome 入场动画），作用在当前样式面板/当前视频。on=false 时恢复为 none。不改渲染算法本身。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `on` | boolean | 必填。true 打开逐词出现 |

CLI：

```
python -m cutora cmd subtitle.word_appear.set --json '{"on": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"on": true}' http://127.0.0.1:47321/v1/commands/subtitle.word_appear.set
```

## text_preset

### `text_preset.apply` — 应用文本预设

按模板新建一条文本覆层（不是改现有层）。idempotent=true 时按source_preset_id 幂等，同一预设只保留一层，供自动署名。失败常见原因：id 不存在、工程无视频。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | string | 必填。预设 id |
| `idempotent` | boolean | 可选。自动署名路径，默认 false |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd text_preset.apply --json '{"id": "", "idempotent": true, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"id": "", "idempotent": true, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/text_preset.apply
```

### `text_preset.delete` — 删除用户文本预设

删除用户目录里的一条文本层模板。拒绝内置预设。失败常见原因：id 不存在、试图删内置。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | string | 必填。预设 id |

CLI：

```
python -m cutora cmd text_preset.delete --json '{"id": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"id": ""}' http://127.0.0.1:47321/v1/commands/text_preset.delete
```

### `text_preset.list` — 列出文本预设

列出内置与用户文本层模板：id、名称、锚点、是否内置。不改工程。失败常见原因：预设目录不可读。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

CLI：

```
python -m cutora cmd text_preset.list --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/text_preset.list
```

### `text_preset.save` — 保存文本预设

把一条文本层模板写入 UserData/presets/text_presets.json。定位用九宫格锚点 + 相对画布短边的边距和字号。失败常见原因：name 为空、试图覆盖内置。

- 可撤销：否
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `name` | string | 可选。显示名 |
| `id` | string | 可选。预设 id，省略则新建 |
| `text` | string | 可选。文案 |
| `layout` | object | 可选。anchor / margin_x / margin_y / size_pct |
| `style` | object | 可选。字体颜色描边阴影背景 |
| `animation` | string | 可选。入场动画，默认 none |
| `overlay` | object | 可选。从现有文本覆层推断 |
| `media_id` | integer | 可选。媒体下标，配合 overlay 省略时用当前选中覆层 |

CLI：

```
python -m cutora cmd text_preset.save --json '{"name": "", "id": "", "text": "", "layout": "", "style": "", "animation": "", "overlay": "", "media_id": 0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"name": "", "id": "", "text": "", "layout": "", "style": "", "animation": "", "overlay": "", "media_id": 0}' http://127.0.0.1:47321/v1/commands/text_preset.save
```

## timeline

### `timeline.add_track` — 添加轨道

给当前视频加一条空轨道，type 为 video/text/image/audio。对应轨头右键「添加轨道」。失败常见原因：type 非法、未选中视频。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `type` | string | 必填。video / text / image / audio / adjust（调整轨） |

CLI：

```
python -m cutora cmd timeline.add_track --json '{"type": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"type": ""}' http://127.0.0.1:47321/v1/commands/timeline.add_track
```

### `timeline.add_transition` — 在接缝添加转场

在指定轨道的 start_t 处写入一条独立 TransitionItem（视频或音频）。已有重叠转场时替换预设与时长。失败常见原因：找不到轨道。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 必填。轨道 id |
| `start_t` | number | 必填。转场起点（秒） |
| `duration` | number | 必填。转场时长（秒） |
| `preset` | string | 必填。转场预设名 |
| `domain` | string | 可选。visual 或 audio |

CLI：

```
python -m cutora cmd timeline.add_transition --json '{"track_id": "", "start_t": 0.0, "duration": 0.0, "preset": "", "domain": "visual"}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": "", "start_t": 0.0, "duration": 0.0, "preset": "", "domain": "visual"}' http://127.0.0.1:47321/v1/commands/timeline.add_transition
```

### `timeline.clear_io_points` — 清除入出点

清除当前视频的入点、出点或两者。which=in/out/both，默认 both。与当前媒体绑定，不录入配方。失败常见原因：未选中视频。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `which` | string | 可选。in / out / both |

CLI：

```
python -m cutora cmd timeline.clear_io_points --json '{"which": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"which": ""}' http://127.0.0.1:47321/v1/commands/timeline.clear_io_points
```

### `timeline.close_gaps` — 闭合间隙

消除指定轨道上的间隙（全部，或 at_t 所在那一处），后方对象前移。字幕和标记按同一时间映射。不录入配方。失败常见原因：轨道不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 可选。轨道 id，省略则用焦点轨 |
| `at_t` | number | 可选。只闭合包含该时刻的间隙；省略则闭合全部 |

CLI：

```
python -m cutora cmd timeline.close_gaps --json '{"track_id": "", "at_t": 0.0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": "", "at_t": 0.0}' http://127.0.0.1:47321/v1/commands/timeline.close_gaps
```

### `timeline.delete_clip` — 删除片段

按 track_id + clip_index 删除一个片段。命令体不弹确认框。失败常见原因：轨道或下标不存在。

- 可撤销：是
- 危险：是（API 需 confirm=true）
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 必填。轨道 id |
| `clip_index` | integer | 必填。片段下标，从 0 起 |

CLI：

```
python -m cutora cmd timeline.delete_clip --json '{"track_id": "", "clip_index": 0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": "", "clip_index": 0}' http://127.0.0.1:47321/v1/commands/timeline.delete_clip
```

### `timeline.delete_transition` — 删除转场

从指定轨道删掉一条独立转场。优先按 transition_id 查找，没有 id 时按 start_t（容差万分之一秒）并可用 domain 区分同一时刻的视频和音频转场。锁定轨道会拒绝。撤销后这条转场连同预设、时长、方向和羽化回到原处。

- 可撤销：是
- 危险：是（API 需 confirm=true）
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 必填。轨道 id |
| `transition_id` | string | 可选。转场 id，省略则按 start_t 匹配 |
| `start_t` | number | 可选。转场起点（秒），没有 id 时用来匹配 |
| `domain` | string | 可选。visual 或 audio，按起点匹配时区分同位置的两条 |

CLI：

```
python -m cutora cmd timeline.delete_transition --json '{"track_id": "", "transition_id": "", "start_t": 0.0, "domain": "visual"}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": "", "transition_id": "", "start_t": 0.0, "domain": "visual"}' http://127.0.0.1:47321/v1/commands/timeline.delete_transition
```

### `timeline.duplicate_clips` — 复制片段

把指定片段复制到 time 秒（最早一条对齐播放头）。可传 clips 下标或 items 快照。与当前媒体绑定，不录入配方。失败常见原因：轨道或下标不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `clips` | array | 可选。[{track_id, clip_index}, ...] |
| `items` | array | 可选。片段快照 |
| `time` | number | 可选。复制后最早一条的开始秒 |

CLI：

```
python -m cutora cmd timeline.duplicate_clips --json '{"clips": [], "items": [], "time": 0.0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"clips": [], "items": [], "time": 0.0}' http://127.0.0.1:47321/v1/commands/timeline.duplicate_clips
```

### `timeline.info` — 时间线摘要

返回当前视频时间线的宽高帧率、轨道列表（id/type）、播放头位置（毫秒）。不改动时间线。失败常见原因：未选中视频。

- 可撤销：否
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

CLI：

```
python -m cutora cmd timeline.info --json '{}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}' http://127.0.0.1:47321/v1/commands/timeline.info
```

### `timeline.join_clips` — 合并接缝两侧片段

把同一轨道上相邻且同源、源出入点连续（±1 帧）、速度相同、接缝无转场的两个片段合回一个。失败常见原因：不同素材、出入点不连续、速度不同、接缝上已有转场。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 必填。轨道 id |
| `left_index` | integer | 必填。左侧片段下标 |
| `right_index` | integer | 必填。右侧片段下标 |

CLI：

```
python -m cutora cmd timeline.join_clips --json '{"track_id": "", "left_index": 0, "right_index": 0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": "", "left_index": 0, "right_index": 0}' http://127.0.0.1:47321/v1/commands/timeline.join_clips
```

### `timeline.link_clips` — 重新绑定音视频

给选中的片段写同一枚新的 link_group_id。通常是 1 视频 + 1 音频；配乐也可以手动绑到视频。失败常见原因：少于两条、轨道不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `clips` | array | 必填。[{track_id, clip_index}, ...] |

CLI：

```
python -m cutora cmd timeline.link_clips --json '{"clips": []}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"clips": []}' http://127.0.0.1:47321/v1/commands/timeline.link_clips
```

### `timeline.move_clip` — 移动片段

把指定片段的时间线起点改到 start 秒。不改源入出点。失败常见原因：轨道或下标不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 必填。轨道 id |
| `clip_index` | integer | 必填。片段下标 |
| `start` | number | 必填。新的时间线起点（秒） |

CLI：

```
python -m cutora cmd timeline.move_clip --json '{"track_id": "", "clip_index": 0, "start": 0.0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": "", "clip_index": 0, "start": 0.0}' http://127.0.0.1:47321/v1/commands/timeline.move_clip
```

### `timeline.move_clip_to_track` — 片段换轨

把指定片段从当前轨道挪到 dest_track_id，并设置时间线起点。对应跨轨拖放松手。失败常见原因：轨道不存在、类型不同。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 必填。源轨道 id |
| `clip_index` | integer | 必填。片段下标，从 0 起 |
| `dest_track_id` | string | 必填。目标轨道 id |
| `start` | number | 必填。新的时间线起点（秒） |

CLI：

```
python -m cutora cmd timeline.move_clip_to_track --json '{"track_id": "", "clip_index": 0, "dest_track_id": "", "start": 0.0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": "", "clip_index": 0, "dest_track_id": "", "start": 0.0}' http://127.0.0.1:47321/v1/commands/timeline.move_clip_to_track
```

### `timeline.move_clips` — 移动片段

把一组片段的时间线起点改到给定秒。参数是 JSON 数组，每项含 track_id、clip_index、start。对应拖动松手一次，不换轨。失败常见原因：轨道或下标不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `clips` | array | 必填。[{track_id, clip_index, start}, ...] |

CLI：

```
python -m cutora cmd timeline.move_clips --json '{"clips": []}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"clips": []}' http://127.0.0.1:47321/v1/commands/timeline.move_clips
```

### `timeline.razor` — 切刀

在 time 秒处切开所选视频/音频/字幕/覆层。一次命令一条 undo。video/audio 走 clip.split_at，字幕走 subtitle.split，覆层走 overlay.split。锁定轨拒绝，工程 JSON 不变。失败常见原因：轨道已锁定、切开点不在对象内。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `time` | number | 必填。切开点（秒） |
| `clips` | array | 可选。[{track_id, clip_index}] |
| `overlays` | array | 可选。[{kind, idx}] |
| `subtitles` | array | 可选。[{idx}] |

CLI：

```
python -m cutora cmd timeline.razor --json '{"time": 0.0, "clips": [], "overlays": [], "subtitles": []}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"time": 0.0, "clips": [], "overlays": [], "subtitles": []}' http://127.0.0.1:47321/v1/commands/timeline.razor
```

### `timeline.remove_track` — 删除轨道

按 track_id 删除一条空轨道。轨道上还有片段时失败（与界面「只能删除空轨道」一致）。命令体不弹提示框。

- 可撤销：是
- 危险：是（API 需 confirm=true）
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 必填。轨道 id |

CLI：

```
python -m cutora cmd timeline.remove_track --json '{"track_id": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": ""}' http://127.0.0.1:47321/v1/commands/timeline.remove_track
```

### `timeline.ripple_delete` — Ripple 删除

删除选中 clip/字幕，并把后方同轨对象、字幕、标记前移被删时长。跨越删除区的字幕截断并同步 words。一次命令一次撤销，不录入配方。失败常见原因：targets 为空或下标越界。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `targets` | array | 必填。[{kind:clip\|seg, track_id, clip_index\|seg_id}, ...] |

CLI：

```
python -m cutora cmd timeline.ripple_delete --json '{"targets": []}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"targets": []}' http://127.0.0.1:47321/v1/commands/timeline.ripple_delete
```

### `timeline.ripple_insert` — Ripple 插入空隙

在 t 秒、指定轨道插入 duration 秒空隙，后方同轨对象和字幕、标记后移。跨越插入点的对象整体后移、不拉伸。不录入配方。失败常见原因：缺少 track_id。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `t` | number | 必填。插入点（秒） |
| `duration` | number | 必填。空隙时长（秒） |
| `track_id` | string | 可选。轨道 id，省略则用焦点轨 |

CLI：

```
python -m cutora cmd timeline.ripple_insert --json '{"t": 0.0, "duration": 0.0, "track_id": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"t": 0.0, "duration": 0.0, "track_id": ""}' http://127.0.0.1:47321/v1/commands/timeline.ripple_insert
```

### `timeline.ripple_intro` — 开场视频 ripple

按给定入出点重套当前任务的开场视频（remove→re-apply），后面的主视频跟着平移。对应拖开场片段松手。失败常见原因：没有开场视频。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `in_t` | number | 必填。开场源入点（秒） |
| `out_t` | number | 必填。开场源出点（秒） |
| `start_t` | number | 必填。时间线起点（秒） |
| `intro_duration` | number | 可选。开场时长，省略则 out_t-in_t |

CLI：

```
python -m cutora cmd timeline.ripple_intro --json '{"in_t": 0.0, "out_t": 0.0, "start_t": 0.0, "intro_duration": 0.0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"in_t": 0.0, "out_t": 0.0, "start_t": 0.0, "intro_duration": 0.0}' http://127.0.0.1:47321/v1/commands/timeline.ripple_intro
```

### `timeline.set_clip_fades` — 设置片段淡化

把指定音频片段的淡入、淡出时长写成给定秒数。时间线条上手柄松手和检查器淡化滑条走同一条命令，写的是已有的 fade_in_dur / fade_out_dur，可撤销。失败常见原因：轨道或下标不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 必填。轨道 id |
| `clip_index` | integer | 必填。片段下标，从 0 起 |
| `fade_in_dur` | number | 可选。淡入秒数 |
| `fade_out_dur` | number | 可选。淡出秒数 |

CLI：

```
python -m cutora cmd timeline.set_clip_fades --json '{"track_id": "", "clip_index": 0, "fade_in_dur": 0.0, "fade_out_dur": 0.0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": "", "clip_index": 0, "fade_in_dur": 0.0, "fade_out_dur": 0.0}' http://127.0.0.1:47321/v1/commands/timeline.set_clip_fades
```

### `timeline.set_in_point` — 设置入点

把当前视频的入点设为 t 秒。绝对时间，与当前媒体绑定，不录入配方。失败常见原因：未选中视频。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `t` | number | 必填。入点（秒） |

CLI：

```
python -m cutora cmd timeline.set_in_point --json '{"t": 0.0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"t": 0.0}' http://127.0.0.1:47321/v1/commands/timeline.set_in_point
```

### `timeline.set_out_point` — 设置出点

把当前视频的出点设为 t 秒。绝对时间，与当前媒体绑定，不录入配方。失败常见原因：未选中视频。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `t` | number | 必填。出点（秒） |

CLI：

```
python -m cutora cmd timeline.set_out_point --json '{"t": 0.0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"t": 0.0}' http://127.0.0.1:47321/v1/commands/timeline.set_out_point
```

### `timeline.set_playhead` — 设置播放头

把播放头移到 time 秒并刷新预览，等价时间线点击。失败常见原因：未打开视频。

- 可撤销：否
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `time` | number | 必填。秒 |

CLI：

```
python -m cutora cmd timeline.set_playhead --json '{"time": 0.0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"time": 0.0}' http://127.0.0.1:47321/v1/commands/timeline.set_playhead
```

### `timeline.set_track_height` — 设置轨道高度

改一条或全部轨道的高度（像素）。拖轨头底边松手一次写一条撤销；Shift+拖或右键「所有轨道高度」一次改全部。失败常见原因：找不到轨道。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 必填。轨道 id |
| `height` | number | 必填。轨道高度（像素） |
| `all` | boolean | 可选。true 时所有轨道设为同一高度 |

CLI：

```
python -m cutora cmd timeline.set_track_height --json '{"track_id": "", "height": 0.0, "all": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": "", "height": 0.0, "all": true}' http://127.0.0.1:47321/v1/commands/timeline.set_track_height
```

### `timeline.set_track_locked` — 锁定轨道

设置指定轨道 locked。锁定后仍渲染但不能编辑。写入工程。与当前媒体绑定，不录入配方。失败常见原因：轨道不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 必填。轨道 id |
| `on` | boolean | 必填。true 锁定 |

CLI：

```
python -m cutora cmd timeline.set_track_locked --json '{"track_id": "", "on": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": "", "on": true}' http://127.0.0.1:47321/v1/commands/timeline.set_track_locked
```

### `timeline.set_track_mute` — 轨道静音

设置指定轨道 mute。影响预览和导出，写入工程。on=true 静音。与当前媒体绑定，不录入配方。失败常见原因：轨道不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 必填。轨道 id |
| `on` | boolean | 必填。true 静音 |

CLI：

```
python -m cutora cmd timeline.set_track_mute --json '{"track_id": "", "on": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": "", "on": true}' http://127.0.0.1:47321/v1/commands/timeline.set_track_mute
```

### `timeline.set_transition` — 调整转场时长

改一条已有 TransitionItem 的起点和时长。拖左右手柄松手时调用一次，对边不动。时长最短一帧，允许拖过源余量（不够的部分冻结补帧）。失败常见原因：找不到轨道或转场。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 必填。轨道 id |
| `transition_id` | string | 可选。转场 id，省略则按 start_t 匹配 |
| `start_t` | number | 可选。新的转场起点（秒） |
| `duration` | number | 可选。新的转场时长（秒） |
| `preset` | string | 可选。可选，同时改预设名 |

CLI：

```
python -m cutora cmd timeline.set_transition --json '{"track_id": "", "transition_id": "", "start_t": 0.0, "duration": 0.0, "preset": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": "", "transition_id": "", "start_t": 0.0, "duration": 0.0, "preset": ""}' http://127.0.0.1:47321/v1/commands/timeline.set_transition
```

### `timeline.split_at` — 在时间点切开

在 time 秒处切开当前视频各轨道上覆盖该时刻的片段。失败常见原因：time 越界、没有片段。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `time` | number | 必填。切开点（秒） |

CLI：

```
python -m cutora cmd timeline.split_at --json '{"time": 0.0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"time": 0.0}' http://127.0.0.1:47321/v1/commands/timeline.split_at
```

### `timeline.track.solo` — 轨道独奏

设置指定轨道预览独奏。只影响预览混音，不写入工程、不影响导出。on=true 独奏该轨。失败常见原因：轨道不存在。

- 可撤销：否
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 必填。轨道 id |
| `on` | boolean | 必填。true 独奏 |

CLI：

```
python -m cutora cmd timeline.track.solo --json '{"track_id": "", "on": true}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": "", "on": true}' http://127.0.0.1:47321/v1/commands/timeline.track.solo
```

### `timeline.trim_clip` — 裁剪片段

把指定片段的源入点/出点和时间线起点、速率写成给定数值。对应拖边缘松手一次。失败常见原因：轨道或下标不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 必填。轨道 id |
| `clip_index` | integer | 必填。片段下标，从 0 起 |
| `in_t` | number | 必填。源入点（秒） |
| `out_t` | number | 必填。源出点（秒） |
| `start_t` | number | 必填。时间线起点（秒） |
| `speed` | number | 可选。播放速率，省略则不改 |

CLI：

```
python -m cutora cmd timeline.trim_clip --json '{"track_id": "", "clip_index": 0, "in_t": 0.0, "out_t": 0.0, "start_t": 0.0, "speed": 0.0}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": "", "clip_index": 0, "in_t": 0.0, "out_t": 0.0, "start_t": 0.0, "speed": 0.0}' http://127.0.0.1:47321/v1/commands/timeline.trim_clip
```

### `timeline.trim_clips` — 裁剪多条片段

把一组片段的源入点/出点和时间线起点写成给定数值。对应链接对同侧边缘松手一次，一次命令一次撤销。失败常见原因：轨道或下标不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `clips` | array | 必填。[{track_id, clip_index, in_t, out_t, start_t, speed?}, ...] |

CLI：

```
python -m cutora cmd timeline.trim_clips --json '{"clips": []}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"clips": []}' http://127.0.0.1:47321/v1/commands/timeline.trim_clips
```

### `timeline.unlink_clips` — 解绑音视频

把指定片段的 link_group_id 清掉，之后拖视频不再带走原声。可撤销。失败常见原因：轨道或下标不存在。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `clips` | array | 必填。[{track_id, clip_index}, ...] |

CLI：

```
python -m cutora cmd timeline.unlink_clips --json '{"clips": []}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"clips": []}' http://127.0.0.1:47321/v1/commands/timeline.unlink_clips
```

## track

### `track.apply` — 应用跟踪（生成关键帧）

把界面按运动数据算好的关键帧（paths，形同 keyframe.replace）写到目标上：跟随 = target_loc 指定的文字 / 图片 / 另一条视频，稳定 = 被跟踪的片段自己；并把这条绑定记录到片段的 tracking.bindings 以便更新。一次撤销。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `element_index` | integer | 可选。覆层里 elements 的下标（可选） |
| `node_id` | string | 可选。效果节点图里的节点 id（可选；给了就改那个节点） |
| `target_loc` | object | 可选。目标定位（target/track_id/clip_index 或 list/overlay_index），稳定模式不需要 |
| `paths` | object | 必填。{参数: [关键帧点…]} |
| `binding` | object | 必填。绑定记录（mode / source / points / target / apply / smooth / offset / fill / ref_frame） |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd track.apply --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "target_loc": "", "paths": "", "binding": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "target_loc": "", "paths": "", "binding": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/track.apply
```

### `track.point` — AI 跟踪点

op=add 新建跟踪点（最多 8 个）；op=remove 删点及它所有帧上的点选；op=place 在源帧 frame 上放下 / 挪动这个点（x, y 为源帧归一化 0–1）；op=unplace 去掉这一帧上这个点的点选。只改数据，跟踪由界面后台任务做。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `element_index` | integer | 可选。覆层里 elements 的下标（可选） |
| `node_id` | string | 可选。效果节点图里的节点 id（可选；给了就改那个节点） |
| `op` | string | 必填。add / remove / place / unplace |
| `point_id` | integer | 可选。跟踪点 id（add 以外必填） |
| `frame` | integer | 可选。源帧号（place / unplace） |
| `x` | number | 可选。源帧归一化 x（place） |
| `y` | number | 可选。源帧归一化 y（place） |
| `name` | string | 可选。op=add 时的名字（可选） |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd track.point --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "op": "add", "point_id": 0, "frame": 0, "x": 0.0, "y": 0.0, "name": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "op": "add", "point_id": 0, "frame": 0, "x": 0.0, "y": 0.0, "name": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/track.point
```

### `track.session` — AI 跟踪会话

op=create 给视频片段建跟踪结果缓存会话并建第一个跟踪点（已有则原样返回，可带 model）；op=clear 清空全部点选和跟踪结果（保留点和绑定）；op=detach 把整个 AI 跟踪从片段上拿掉并删缓存。锁定轨拒绝。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `element_index` | integer | 可选。覆层里 elements 的下标（可选） |
| `node_id` | string | 可选。效果节点图里的节点 id（可选；给了就改那个节点） |
| `op` | string | 可选。create / clear / detach |
| `model` | string | 可选。op=create 时指定模型 id（可选） |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd track.session --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "op": "create", "model": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "op": "create", "model": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/track.session
```

### `track.set` — 设置 AI 跟踪

合并 patch 到视频片段的跟踪设置：model（跟踪模型 id）、points（按 id 改 name / color / enabled）。不改点选、结果和绑定记录。锁定轨拒绝。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `element_index` | integer | 可选。覆层里 elements 的下标（可选） |
| `node_id` | string | 可选。效果节点图里的节点 id（可选；给了就改那个节点） |
| `patch` | object | 必填。要改的字段 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd track.set --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "patch": "", "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "patch": "", "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/track.set
```

### `track.set_cell` — 设置轨道分屏格子

把一条视频轨填进分屏格子（A/B/…），或 cell=null 不参与、全屏叠最上。已占用的格子会报错。不录入配方。失败常见原因：轨道不存在、格子被占。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_id` | string | 必填。轨道 id |
| `cell` | string|null | 可选。格子 id；空或 null 表示不参与 |

CLI：

```
python -m cutora cmd track.set_cell --json '{"track_id": "", "cell": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_id": "", "cell": ""}' http://127.0.0.1:47321/v1/commands/track.set_cell
```

### `track.set_props` — 设置轨道属性

把 patch 写到 track_ids 列出的每一条轨道（变换 / 不透明 / 混合 / 名字）。图钉多轨时一次命令写 N 条，undo 一次全部回滚。不录入配方。失败常见原因：轨道不存在、patch 字段未知。

- 可撤销：是
- 危险：否
- 无头：否（需要图形界面）
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `track_ids` | array | 必填。要写入的轨道 id 列表 |
| `patch` | object | 必填。要合并的轨道属性，如 x/y/scale/opacity/blend_mode/name |

CLI：

```
python -m cutora cmd track.set_props --json '{"track_ids": [], "patch": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"track_ids": [], "patch": ""}' http://127.0.0.1:47321/v1/commands/track.set_props
```

### `track.unbind` — 删除跟踪绑定

删掉视频片段 tracking.bindings 里第 index 条绑定记录（已经写到目标上的关键帧保留，需要的话在关键帧面板里删）。锁定轨拒绝。

- 可撤销：是
- 危险：否
- 无头：是
- 异步作业：否

| 参数 | 类型 | 说明 |
|---|---|---|
| `target` | string | 可选。clip / overlay / style |
| `track_id` | string | 可选。轨道 id，仅 target=clip |
| `clip_index` | integer | 可选。片段下标，仅 target=clip |
| `list` | string | 可选。text_overlays 或 image_overlays |
| `overlay_index` | integer | 可选。覆层下标，仅 target=overlay |
| `element_index` | integer | 可选。覆层里 elements 的下标（可选） |
| `node_id` | string | 可选。效果节点图里的节点 id（可选；给了就改那个节点） |
| `index` | integer | 必填。绑定下标 |
| `media_id` | integer | 可选。媒体下标 |
| `media` | string | 可选。视频路径 |
| `project` | string | 可选。工程文件 |

CLI：

```
python -m cutora cmd track.unbind --json '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "index": 0, "media_id": 0, "media": "", "project": ""}'
```

curl：

```
curl -sS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"target": "clip", "track_id": "", "clip_index": 0, "list": "", "overlay_index": 0, "element_index": 0, "node_id": "", "index": 0, "media_id": 0, "media": "", "project": ""}' http://127.0.0.1:47321/v1/commands/track.unbind
```
