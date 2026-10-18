# 18 · 多开并行不打架 + Grok 自己生图

## 背景（0907 事故）

四个 Orante 窗口同时跑扩图，T0011 报
`Page.evaluate: Execution context was destroyed, most likely because of a navigation`。

日志核对（data/logs/J00098..J00101.log）：四个 Job **全部**连到了
`TIK-D1，端口 9240`——包括两个 Grok 任务。原因两条：

1. `expand.resolve_account_id()` 只有两档：任务的 `expand_account_id`（全库都是
   NULL），否则「第一个启用的 Flow 账号」。于是四个任务落到同一个账号。
2. 每个 Orante 窗口是独立进程、独立 `Scheduler`，进程内那句「一个账号一次只跑
   一个 Job」跨进程完全不作数。四个进程同时驱动同一个 Chrome、同一个标签页，
   `_bypass_csp()` 又会 `page.reload()`（`_orante_csp_off` 标记是每进程的），
   于是 A 在 `page.evaluate`、B 在刷新 → 上下文被销毁。
   时间戳对得上：J00100 14:11:01 刷新 → J00099 14:11:01 报错；
   J00101 14:11:25 刷新 → J00100 14:11:25 报错。
3. `_recaptcha()` 里的 `page.evaluate` 没有任何重试，而 `_rpc()` 调 `inner()`
   是在 `try` 之外——所以这个异常直接把整个 Job 打成失败。

## 已经改好的（0907，Claude 直接改的）

- `orante/locks.py`（新）+ `account_locks` 表：跨进程账号锁，心跳 20s、
  超时 90s 可接管。`scheduler._run` 对所有 `account_bound` 的 Job 先抢锁，
  抢不到就排队（日志写「账号被 T00xx 占着，排队等它跑完…」），排队时可被停止。
- `expand.resolve_account_id`：① 任务指定的 `expand_account_id`
  ② 任务自己的账号（Flow 任务用自己那个）③ Grok 任务借一个最闲的 Flow 号，
  并写回 `expand_account_id` 固定下来。
- `flow_api`：每个引擎实例自己开专属标签页，先 `Page.setBypassCSP` 再导航，
  不再 reload 任何已有标签；`close()` 关掉自己开的标签；
  `chrome.launch(tab_if_alive=False)` 不再每次连都塞一个新标签。
- `_recaptcha` 加重试；`_rpc` 把 `inner()` 挪进 try；上传缓存原子写 + 合并；
  `db.next_id` 用 `BEGIN IMMEDIATE`，多进程不会发同一个号。

## 已经做好：Grok 任务用自己的 Grok 账号生图（0907 实地抓包 + 实现）

抓包工具（都在 tools\，双击就跑，输出在 data\logs\grok-capture.log、
截图和控件清单在 data\capture\）：

    1-Grok-Probe.bat     只看界面：截图 + 控件清单，不点任何东西
    2-Grok-Dry.bat       切图片模式 + 拖参考图 + 贴提示词，停在提交前
    3-Grok-Auto.bat      同上再按回车，全程录接口
    4-Grok-TestImage.bat 直接用 GrokEngine.gen_images 生 2 张，验收用

### Grok Imagine 的协议（0907 抓包定型）

  ① 传图  POST https://grok.com/http/upload-file-v2/direct
          multipart/form-data，字段名 file
          → {"uploadId":…,"fileMetadata":{"fileMetadataId":<资产Id>,"fileUri":…}}
          这一步不要反爬头，自己发得通。

  ② 生图  POST https://grok.com/rest/app-chat/conversations/new   （NDJSON 流）
          body:
            {"modelName":"imagine-image-edit",
             "message":<提示词>,
             "enableImageStreaming":true,"enableSideBySide":true,"sendFinalMetadata":true,
             "responseMetadata":{"modelConfigOverride":{"modelMap":{"imageEditModel":"imagine"}}},
             "mediaGenInput":{"imageToImage":{"prompt":<提示词>,"inputAssets":[<资产Id>]}},
             "kind":"CONVERSATION_KIND_IMAGINE"}
          流里那行
            {"result":{"response":{"streamingImageGenerationResponse":
               {"imageId":…,"imageUrl":"users/<uid>/generated/<id>/image.jpg",
                "progress":100,"moderated":false,…}}}}
          就是成品；progress 50 那条是 -part-0 的预览，别拿。
          moderated=true = 被审掉。

  ③ 取图  GET https://assets.grok.com/<imageUrl>   要带 cookie，所以在页面里 fetch。

  额度   POST /rest/media/imagine/quota_info → {image, imagePro, imageEdit, video, video720p}

### 为什么生图走界面而不是直接打接口

conversations/new 还要一个 x-statsig-id 头，是他们的 JS 每个请求现算的（抓包里
32 个请求 31 个不同值）。自己发就 403：
`{"error":{"code":7,"message":"This page is out of date. Reload to continue."}}`。
所以 `GrokEngine._generate_via_ui()` 照人手那条路走：
打开 /imagine → 点「图片」模式的 radio → 用老 `grok_pipe.attach_by_drop`
把图拖进 composer（走 drop 事件才不会跳成图片帖）→ `grok_pipe.type_prompt`
贴提示词 → 回车 → 用 CDP 把那次 conversations/new 的流原样读回来解析。
读不到流就退回扫页面上新冒出来的 `img[src*="/generated/"]`（提交前先记一遍
已有的图，只认新增的，否则会捡到首页铺的旧作品）。
上传和直接打接口的两个方法（`upload_image` / `GEN_JS`）留在代码里，
哪天摸清 statsig 就能切回接口，快很多。

实测：一张约 40 秒（含重开页面），2 张 90 秒，来源都是 stream。

### 路由

`expand.resolve_account_id` 现在按引擎能力走：
`base.can_gen_images(engine)` 为真就用任务自己的账号——Flow 任务用 Flow 号，
Grok 任务用 Grok 号；只有引擎不能生图或账号停用时才去借一个最闲的 Flow。
实测四个任务 → A001 / A004 / A005 / A006，四个 Chrome 各跑各的。

### 0907 第一次上机跑出来的两个坑（已修）

1. **Cookie 同意条**。新 profile（A006 Lucy）第一次开 grok.com 会弹 OneTrust 的
   同意条，第一张图等满 4 分钟没出来。现在 `connect()` 和每次 `_generate_via_ui()`
   都先 `_dismiss_consent()`，按最保守的「全部拒绝」点掉。
2. **把 Cookie 条的文案当成了审核理由**。`_blocked_reason()` 扫页面正文，横幅里的
   "Cookie Policy / Privacy Policy / Terms of Service" 命中了 `polic`，于是「没出图」
   被判成「这张参考图违规」，T0013 的 p04 被错标 blocked。现在扫正文走
   `_PAGE_TEXT_JS`，先把 onetrust / cookie 那几块从副本里删掉再取 innerText。
   并且 `gen_images` 区分「被审」和「没反应」：只有真的 moderated 或流里带拒绝信息
   才抛 BlockedImage（才会去标参考图），超时只算这一张没出来。
3. 顺带：提交后 35 秒一个请求都没有就重敲一次回车（先再关一次 Cookie 条），
   75 秒还没动静就放弃这一张，不再干等满 timeout（上限也从 240 秒降到 180）。

### 还可以做

- 纯文生图（没有参考图）那条路没接：`mediaGenInput` 应该是别的形状，要再抓一次。
- 摸清 x-statsig-id 就能把生图从界面切回接口，速度能到 Flow 那个量级。
