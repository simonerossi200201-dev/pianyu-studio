# 规格 02 · 图片来源服务 `orante/services/images.py`

> **给 Grok 的工作约定**：你在 `D:\0_Dev\Orante` 里工作（这就是当前目录）。先完整读 `specs/00-architecture.md`，再读本规格。只改本规格点名的文件；老流水线 `..\下载图片\` 里的任何文件**只读不改**，需要就拷到 `orante/legacy/`。写完自己跑一遍验收；最后把「改了哪些文件、怎么验的、没做完的、需要后端补的」写到 `specs/reports/<本文件名>.md`。全程用中文写注释和文字。不要问问题，拿不准的按 00 文件的规矩自己定并在报告里说明。


先读 `specs/00-architecture.md`。只写这个文件（可以往 `orante/legacy/` 拷老模块，但不要改它们）。

## 接口

```python
def fetch(job, task: dict, task_dir: Path) -> list[dict]:
    """把 task['image_source'] 里的图弄到 task_dir/images/pNN.<ext>，返回 Manifest.images 列表（见 models.Image 的字段）。"""
```

`task['type']` 是 `long` / `short`；`job.log(str)` 写日志、`job.check()` 响应取消。

## 来源识别（按顺序）

1. **本地文件夹**：`Path(src).is_dir()` → 取里面 jpg/jpeg/png/webp，自然排序（`p1,p2,p10` 不是 `p1,p10,p2`）。
2. **本地单文件**：`Path(src).is_file()`。
3. **Google Drive 文件夹链接**：`drive.google.com/drive/folders/<id>` 或 `?id=`。
4. **Google Drive 单文件链接**：`/file/d/<id>/`、`open?id=`、`uc?id=`。
5. **其他 http(s) 直链**：直接 GET；`Content-Type` 不是 image/* 就报错。

Drive 的访问：优先复用老模块 `legacy/google_drive_downloader.py`（`下载图片\core\google_drive_downloader.py`，用 OAuth token）；没有 token 时退回公开链接直下（`https://drive.google.com/uc?export=download&id=<id>` 及文件夹页面解析）。**分发版用户很可能没有 OAuth**——所以公开链接路径必须能用，且失败时错误信息要说「把文件夹设为『任何知道链接的人可查看』」。

OAuth 的 client_secret / token 放 `data/google/`（`paths.DATA / "google"`），不要放 legacy 目录。

## 规则

- 长文案：**全部**下载。短文案：只挑**一张**——优先没被用过的（见「已使用」记录），都用过就随机。
- 编号：按顺序 `p01, p02, …`，扩展名保留小写。
- 「已使用」记录：`paths.DATA / "used_images.json"`，键是 Drive 文件 id 或 sha1（本地/直链），值是使用次数 + 最近时间。**Drive 上改名**（`【已使用】`→`【已再次使用】`→`【已第3次使用】`…）只在有 OAuth 且设置 `images.rename_on_drive` 为真时做；这个设置键请加进 `config.DEFAULTS`（默认 False）。
- 每张图算 sha1 填进 `sha1`；`origin` = `download` 或 `local`；`src` = 原链接/文件名。
- 图太小（任一边 < 300px）→ `job.log` 警告但仍收下；不是图片文件 → 跳过并警告。
- 一张都拿不到 → `raise OranteError("一张图都没拿到", "检查链接是否公开 / 文件夹里有没有图片")`。

## 验收

写 `tools/test_images.py`：三个用例（本地文件夹 / Drive 文件夹公开链接 / 直链）都能落到 `images/p01…`，并打印返回列表。
