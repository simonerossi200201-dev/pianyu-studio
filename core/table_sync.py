"""一表到底同步器与骨架守恒分词引擎 (TableSync)。

打通：
1. 【右键表格工具】的超链接感知 TSV/HTML 粘贴解析器（自动提取 Google Sheets 隐藏 Drive 链接）
2. 【Orante 2.0】的任务表与分镜台账 (`orante.db` / `manifest.json`)
3. 【CUTORA 智能拼接】的批次文案与分镜顺序表（免去用户在 CUTORA 中二次复制粘贴文案！）
4. 统一 `wenan_norm` 骨架守恒粘连词修复（确保 Orante 与 CUTORA 不再维护两套重复代码）
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


# --- 1. 骨架守恒文案归一化 (统一 Orante 与 CUTORA 的 wenan_norm) ---

_PUNCT_GLUE_RE = re.compile(r"([,;:.!?])([A-Za-z])")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def text_skeleton(s: str) -> str:
    """提取纯字母数字小写骨架，用于严格校验归一化前后绝不增删篡改任何字符。"""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def normalize_copy_safe(text: str) -> str:
    """修复常见标点后缺空格粘连 (如 'bless you,two' -> 'bless you, two')，严格遵守骨架守恒定律。"""
    if not text:
        return ""
    candidate = _PUNCT_GLUE_RE.sub(r"\1 \2", text)
    candidate = _MULTI_SPACE_RE.sub(" ", candidate).strip()
    if text_skeleton(candidate) == text_skeleton(text):
        return candidate
    return text.strip()


# --- 2. 超链接感知 TSV / HTML 剪贴板解析器 (源自右键表格工具并升级) ---

_DRIVE_ID_PATTERNS = [
    re.compile(r"drive\.google\.com/file/d/([0-9A-Za-z_-]{20,})"),
    re.compile(r"drive\.google\.com/drive/(?:u/\d+/)?folders/([0-9A-Za-z_-]{20,})"),
    re.compile(r"[?&]id=([0-9A-Za-z_-]{20,})"),
]
_FORMULA_LINK_RE = re.compile(r'=HYPERLINK\(\s*"([^"]+)"', re.IGNORECASE)
_HTML_HREF_RE = re.compile(r'(?:data-sheets-hyperlink|href)\s*=\s*"([^"]+)"', re.IGNORECASE)


def extract_drive_url(raw: str) -> str:
    """从单元格文本、=HYPERLINK 公式或 Google 重定向 URL 中提取干净的 Google Drive 链接。"""
    s = (raw or "").strip()
    if not s:
        return ""
    m_form = _FORMULA_LINK_RE.search(s)
    if m_form:
        s = m_form.group(1).strip()
    if "google.com/url?" in s:
        try:
            q = parse_qs(urlparse(s).query).get("q", [""])[0]
            if q:
                s = unquote(q)
        except Exception:
            pass
    for pat in _DRIVE_ID_PATTERNS:
        m = pat.search(s)
        if m:
            return s
    return ""


def parse_clipboard_grid(tsv_text: str, html_text: str = "") -> list[list[str]]:
    """解析来自 Excel / Google Sheets / 飞书的复制内容，支持多行引号单元格与底层超链接提取。"""
    if not tsv_text:
        return []
    reader = csv.reader(io.StringIO(tsv_text), delimiter="\t")
    rows = [[cell.strip() for cell in row] for row in reader if any(c.strip() for c in row)]

    if html_text:
        links = [extract_drive_url(u) for u in _HTML_HREF_RE.findall(html_text)]
        links = [u for u in links if u]
        if links and len(rows) == len(links):
            for idx, r in enumerate(rows):
                if not any(extract_drive_url(c) for c in r):
                    r.append(links[idx])
    return rows


# --- 3. Orante 任务表 ⇄ CUTORA 智能拼接 ⇄ 表格工具 统一任务模型 ---

@dataclass
class UnifiedTaskRecord:
    task_id: str                 # 如 "T0125"
    name: str                    # 如 "Tik008" 或任务名
    batch_date: str              # 如 "2026-10-14"
    batch_no: str                # 如 "A"
    task_dir: str                # 任务文件夹绝对路径
    clips_dir: str               # 视频片段目录 (<task_dir>/clips)
    copy_text: str               # 归一化后的完整口播文案
    engine: str                  # "flow" | "grok" | "studio"
    status: str                  # "draft" | "prepared" | "generating" | "collected" | "finished"
    segment_order: list[str] = field(default_factory=list)
    missing_segments: list[int] = field(default_factory=list)
    target_filename: str = ""    # 表格工具预设的最终导出文件名
    drive_upload_folder: str = ""  # 目标上传 Google Drive 文件夹链接
    drive_result_link: str = ""  # 上传完成后的回写链接

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_task_from_manifest(manifest_path: Path) -> UnifiedTaskRecord | None:
    """直接从 Orante 任务目录的 manifest.json 构建统一任务记录，供 CUTORA 智能拼接秒级直读。"""
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    task_dir = manifest_path.parent
    clips_dir = task_dir / "clips"
    copy_obj = data.get("copy") or {}
    raw_copy = copy_obj.get("text") or copy_obj.get("raw") or ""
    clean_copy = normalize_copy_safe(raw_copy)

    batch = data.get("batch") or {}
    segs = data.get("segments") or []
    seg_files: list[str] = []
    missing_segs: list[int] = []

    for seg in segs:
        s_num = int(seg.get("s", 0))
        st = seg.get("status", "")
        takes = seg.get("takes") or []
        valid_take = next((t for t in reversed(takes) if t.get("verdict") == "ok" and t.get("file")), None)
        if not valid_take and takes:
            valid_take = takes[-1]
        if valid_take and valid_take.get("file"):
            seg_files.append(Path(valid_take["file"]).name)
        elif st in ("failed", "blocked", "planned"):
            missing_segs.append(s_num)

    delivery = data.get("delivery") or {}
    return UnifiedTaskRecord(
        task_id=str(data.get("task_id") or task_dir.name.split("-")[0]),
        name=str(data.get("name") or task_dir.name),
        batch_date=str(batch.get("date") or ""),
        batch_no=str(batch.get("no") or ""),
        task_dir=str(task_dir),
        clips_dir=str(clips_dir),
        copy_text=clean_copy,
        engine=str(data.get("engine") or "flow"),
        status="collected" if not missing_segs and seg_files else "generating",
        segment_order=seg_files,
        missing_segments=missing_segs,
        target_filename=str(delivery.get("target_filename") or f"{data.get('name') or task_dir.name}.mp4"),
        drive_upload_folder=str(delivery.get("drive_upload_folder") or ""),
        drive_result_link=str(delivery.get("drive_result_link") or ""),
    )


def discover_orante_batch_tasks(
    batch_or_workspace_dir: Path | None = None,
    orante_db_path: Path | None = None,
    batch_date: str = "",
    batch_no: str = "",
) -> list[UnifiedTaskRecord]:
    """供 CUTORA 智能拼接直接调用：无需人工复制粘贴文案，一键读取 Orante 当前批次全部任务与文案！"""
    records: list[UnifiedTaskRecord] = []

    # 1. 若指定了具体批次目录或任务目录，扫描其下的 manifest.json
    if batch_or_workspace_dir and batch_or_workspace_dir.is_dir():
        if (batch_or_workspace_dir / "manifest.json").is_file():
            rec = load_task_from_manifest(batch_or_workspace_dir / "manifest.json")
            if rec:
                return [rec]
        for mf in sorted(batch_or_workspace_dir.rglob("manifest.json")):
            rec = load_task_from_manifest(mf)
            if rec:
                if batch_date and rec.batch_date != batch_date:
                    continue
                if batch_no and rec.batch_no != batch_no:
                    continue
                records.append(rec)
        if records:
            return records

    # 2. 从 Orante SQLite 数据库 (orante.db) 读取最新批次任务
    db_candidates = [
        p for p in [
            orante_db_path,
            Path(r"D:\0_LTX\data\orante.db"),
            Path(r"D:\0_Dev\Orante\data\orante.db"),
        ]
        if p and p.is_file()
    ]
    for db_p in db_candidates:
        try:
            conn = sqlite3.connect(f"file:{db_p}?mode=ro", uri=True, timeout=5.0)
            conn.row_factory = sqlite3.Row
            q = "SELECT id, name, copy, engine, batch_date, batch_no, dir, status, meta FROM tasks WHERE status != 'trash'"
            params: list[Any] = []
            if batch_date:
                q += " AND batch_date = ?"
                params.append(batch_date)
            if batch_no:
                q += " AND batch_no = ?"
                params.append(batch_no)
            q += " ORDER BY seq ASC, id ASC"
            rows = conn.execute(q, params).fetchall()
            conn.close()
            for r in rows:
                tdir = Path(r["dir"]) if r["dir"] else None
                if tdir and (tdir / "manifest.json").is_file():
                    rec = load_task_from_manifest(tdir / "manifest.json")
                    if rec:
                        records.append(rec)
                        continue
                meta = {}
                try:
                    meta = json.loads(r["meta"] or "{}")
                except Exception:
                    pass
                records.append(
                    UnifiedTaskRecord(
                        task_id=r["id"],
                        name=r["name"] or r["id"],
                        batch_date=r["batch_date"] or "",
                        batch_no=r["batch_no"] or "",
                        task_dir=r["dir"] or "",
                        clips_dir=str(Path(r["dir"]) / "clips") if r["dir"] else "",
                        copy_text=normalize_copy_safe(r["copy"] or ""),
                        engine=r["engine"] or "flow",
                        status=r["status"] or "draft",
                        target_filename=meta.get("target_filename") or f"{r['name'] or r['id']}.mp4",
                        drive_upload_folder=meta.get("drive_upload_folder") or "",
                        drive_result_link=meta.get("drive_result_link") or "",
                    )
                )
            if records:
                break
        except Exception:
            continue

    return records


def write_order_lock_for_cutora(task_dir: Path) -> Path | None:
    """在 <task_dir>/clips/_order.json 写入 Orante 的分镜顺序锁，供 CUTORA 智能拼接锁定长文案顺序。"""
    rec = load_task_from_manifest(task_dir / "manifest.json")
    if not rec or not rec.segment_order:
        return None
    clips_dir = Path(rec.clips_dir)
    clips_dir.mkdir(parents=True, exist_ok=True)
    order_file = clips_dir / "_order.json"
    payload = {
        "task_id": rec.task_id,
        "name": rec.name,
        "copy_text": rec.copy_text,
        "ordered_files": rec.segment_order,
    }
    order_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return order_file
