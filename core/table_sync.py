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

from . import LTX_ROOT, resolve_python_exe


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
    b_date = str(batch.get("date") or "")
    b_no = str(batch.get("no") or "")
    if not b_date:
        m_dt = re.search(r"(20\d{2}-\d{2}-\d{2})", str(task_dir))
        if m_dt:
            b_date = m_dt.group(1)
    if not b_no and task_dir.parent:
        p_name = task_dir.parent.name
        if re.match(r"^[A-Za-z0-9_]{1,8}$", p_name) and not re.match(r"^20\d{2}-", p_name):
            b_no = p_name
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
        batch_date=b_date,
        batch_no=b_no,
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


def filter_records_by_range(
    records: list[UnifiedTaskRecord],
    range_text: str = "",
    task_ids: list[str] | None = None,
) -> list[UnifiedTaskRecord]:
    """按任务编号或视频名范围过滤记录，支持：
    - task_ids 列表: ["T0137", "T0138"]
    - range_text: "T0137-T0162" / "137-162" / "v01-v26" / "T0137,T0139,T0142" / "A" (批次号)
    """
    if task_ids:
        want = {str(x).strip().upper() for x in task_ids if str(x).strip()}
        return [
            r for r in records
            if r.task_id.upper() in want or r.name.upper() in want
        ]
    spec = (range_text or "").strip()
    if not spec or spec.lower() in ("all", "*", "全部"):
        return records

    # 按逗号/空格拆分多个 token 或区间
    tokens = [t.strip() for t in re.split(r"[,，;；\s]+", spec) if t.strip()]
    if len(tokens) == 1 and re.match(r"^[A-Za-z]$", tokens[0]):
        b_want = tokens[0].upper()
        matched_b = [r for r in records if (r.batch_no or "").upper() == b_want]
        if matched_b:
            return matched_b

    def _extract_tid_num(s: str) -> int | None:
        m = re.match(r"^[Tt]?0*(\d+)$", s.strip())
        return int(m.group(1)) if m else None

    def _extract_v_num(s: str) -> int | None:
        m = re.match(r"^[Vv]0*(\d+)$", s.strip())
        return int(m.group(1)) if m else None

    selected: list[UnifiedTaskRecord] = []
    seen_ids: set[str] = set()
    for tok in tokens:
        if "-" in tok or "~" in tok or ".." in tok:
            parts = [p.strip() for p in re.split(r"[-~]+|\.\.", tok, maxsplit=1)]
            if len(parts) == 2:
                a_str, b_str = parts[0], parts[1]
                va, vb = _extract_v_num(a_str), _extract_v_num(b_str)
                if va is not None and (vb is not None or _extract_tid_num(b_str) is not None):
                    vb_val = vb if vb is not None else int(_extract_tid_num(b_str) or va)
                    lo, hi = min(va, vb_val), max(va, vb_val)
                    for r in records:
                        rv = _extract_v_num(r.name)
                        if rv is not None and lo <= rv <= hi and r.task_id not in seen_ids:
                            selected.append(r)
                            seen_ids.add(r.task_id)
                    continue
                ta, tb = _extract_tid_num(a_str), _extract_tid_num(b_str)
                if ta is not None and tb is not None:
                    lo, hi = min(ta, tb), max(ta, tb)
                    for r in records:
                        rt = _extract_tid_num(r.task_id)
                        if rt is not None and lo <= rt <= hi and r.task_id not in seen_ids:
                            selected.append(r)
                            seen_ids.add(r.task_id)
                    continue
        # 单个编号匹配
        v_single = _extract_v_num(tok)
        t_single = _extract_tid_num(tok)
        for r in records:
            if r.task_id in seen_ids:
                continue
            if (
                r.task_id.upper() == tok.upper()
                or r.name.upper() == tok.upper()
                or (v_single is not None and _extract_v_num(r.name) == v_single)
                or (t_single is not None and _extract_tid_num(r.task_id) == t_single)
            ):
                selected.append(r)
                seen_ids.add(r.task_id)
    return selected if selected else records


def discover_orante_batch_tasks(
    batch_or_workspace_dir: Path | None = None,
    orante_db_path: Path | None = None,
    batch_date: str = "",
    batch_no: str = "",
    range_text: str = "",
    task_ids: list[str] | None = None,
) -> list[UnifiedTaskRecord]:
    """供 CUTORA 智能拼接直接调用：无需人工复制粘贴文案，一键读取 Orante 当前批次全部任务与文案！"""
    records: list[UnifiedTaskRecord] = []

    # 1. 若指定了具体批次目录或任务目录，扫描其下的 manifest.json
    if batch_or_workspace_dir and batch_or_workspace_dir.is_dir():
        if (batch_or_workspace_dir / "manifest.json").is_file():
            rec = load_task_from_manifest(batch_or_workspace_dir / "manifest.json")
            if rec:
                return filter_records_by_range([rec], range_text=range_text, task_ids=task_ids)
        if batch_or_workspace_dir.name.lower() in ("clips", "片段") and (batch_or_workspace_dir.parent / "manifest.json").is_file():
            rec = load_task_from_manifest(batch_or_workspace_dir.parent / "manifest.json")
            if rec:
                return filter_records_by_range([rec], range_text=range_text, task_ids=task_ids)
        for mf in sorted(batch_or_workspace_dir.rglob("manifest.json")):
            rec = load_task_from_manifest(mf)
            if rec:
                if batch_date and rec.batch_date != batch_date:
                    continue
                if batch_no and rec.batch_no != batch_no:
                    continue
                records.append(rec)
        if records:
            return filter_records_by_range(records, range_text=range_text, task_ids=task_ids)

    # 2. 从 Orante SQLite 数据库 (orante.db) 读取当前活动任务
    db_candidates = [
        p for p in [
            orante_db_path,
            LTX_ROOT / "modules" / "orante_desk" / "data" / "orante.db",
            LTX_ROOT / "data" / "orante.db",
            Path(r"D:\0_Dev\Orante\data\orante.db"),
        ]
        if p and p.is_file()
    ]
    for db_p in db_candidates:
        try:
            conn = sqlite3.connect(f"file:{db_p}?mode=ro", uri=True, timeout=5.0)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, name, copy, engine, batch_id, dir, status, meta, row_order "
                "FROM tasks WHERE status != 'trash' AND IFNULL(archived, 0) = 0 "
                "ORDER BY row_order ASC, id ASC"
            ).fetchall()
            conn.close()
            all_recs: list[UnifiedTaskRecord] = []
            for r in rows:
                tdir = Path(r["dir"]) if r["dir"] else None
                if tdir and (tdir / "manifest.json").is_file():
                    rec = load_task_from_manifest(tdir / "manifest.json")
                    if rec:
                        all_recs.append(rec)
                        continue
                meta = {}
                try:
                    meta = json.loads(r["meta"] or "{}")
                except Exception:
                    pass
                dir_str = str(r["dir"] or "")
                m_dt = re.search(r"(20\d{2}-\d{2}-\d{2})", dir_str)
                b_dt = m_dt.group(1) if m_dt else ""
                b_no = tdir.parent.name if (tdir and tdir.parent) else ""
                all_recs.append(
                    UnifiedTaskRecord(
                        task_id=r["id"],
                        name=r["name"] or r["id"],
                        batch_date=b_dt,
                        batch_no=b_no,
                        task_dir=dir_str,
                        clips_dir=str(Path(dir_str) / "clips") if dir_str else "",
                        copy_text=normalize_copy_safe(r["copy"] or ""),
                        engine=r["engine"] or "flow",
                        status=r["status"] or "draft",
                        target_filename=meta.get("target_filename") or f"{r['name'] or r['id']}.mp4",
                        drive_upload_folder=meta.get("drive_upload_folder") or "",
                        drive_result_link=meta.get("drive_result_link") or "",
                    )
                )
            if all_recs:
                eff_date = batch_date
                if not eff_date and not task_ids and not range_text:
                    # 默认取最新批次日期的活跃任务
                    dates = sorted({r.batch_date for r in all_recs if r.batch_date}, reverse=True)
                    if dates:
                        eff_date = dates[0]
                for r in all_recs:
                    if eff_date and r.batch_date and r.batch_date != eff_date:
                        continue
                    if batch_no and r.batch_no and r.batch_no.upper() != batch_no.upper():
                        continue
                    # 仅纳入有 clips 目录且内含视频文件的任务
                    if r.clips_dir and Path(r.clips_dir).is_dir():
                        has_vid = any(
                            p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi"}
                            for p in Path(r.clips_dir).iterdir()
                        )
                        if has_vid:
                            records.append(r)
                if not records and all_recs:
                    for r in all_recs:
                        if r.clips_dir and Path(r.clips_dir).is_dir():
                            has_vid = any(
                                p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi"}
                                for p in Path(r.clips_dir).iterdir()
                            )
                            if has_vid:
                                records.append(r)
                if records:
                    break
        except Exception:
            continue

    return filter_records_by_range(records, range_text=range_text, task_ids=task_ids)


def write_order_lock_for_cutora(task_dir: Path) -> Path | None:
    """在 <task_dir>/clips/_order.json 写入 Orante 的分镜顺序锁，供 CUTORA 智能拼接锁定长文案顺序。"""
    rec = load_task_from_manifest(task_dir / "manifest.json")
    if not rec or not rec.segment_order:
        return None
    clips_dir = Path(rec.clips_dir)
    clips_dir.mkdir(parents=True, exist_ok=True)
    order_file = clips_dir / "_order.json"
    short_tid = re.sub(r"^([A-Za-z]+)0+(\d+)$", r"\1\2", rec.task_id)
    payload = {
        "task_id": rec.task_id,
        "name": rec.name,
        "copy_text": rec.copy_text,
        "ordered_files": rec.segment_order,
        rec.task_id: rec.segment_order,
        short_tid: rec.segment_order,
        rec.name: rec.segment_order,
        task_dir.name: rec.segment_order,
        "clips": rec.segment_order,
        f"clips({task_dir.name})": rec.segment_order,
    }
    order_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return order_file


HANDOFF_FILE = LTX_ROOT / "data" / "cutora_stitch_handoff.json"


def prepare_cutora_stitch_handoff(
    range_text: str = "",
    task_ids: list[str] | None = None,
    batch_date: str = "",
    batch_no: str = "",
    auto_start: bool = True,
    auto_ai: bool = True,
    source: str = "orante",
) -> dict[str, Any]:
    """生成 Orante -> CUTORA 智能拼接的交接清单 (`cutora_stitch_handoff.json`)，并为所有任务写入 `_order.json`。"""
    import time

    recs = discover_orante_batch_tasks(
        batch_date=batch_date,
        batch_no=batch_no,
        range_text=range_text,
        task_ids=task_ids,
    )
    clip_dirs: list[str] = []
    tasks_meta: list[dict[str, Any]] = []
    for r in recs:
        tdir = Path(r.task_dir) if r.task_dir else None
        if tdir and tdir.is_dir():
            try:
                write_order_lock_for_cutora(tdir)
            except Exception:
                pass
        cdir = Path(r.clips_dir) if r.clips_dir else None
        if cdir and cdir.is_dir():
            clip_dirs.append(str(cdir))
            tasks_meta.append(
                {
                    "task_id": r.task_id,
                    "name": r.name,
                    "batch_date": r.batch_date,
                    "batch_no": r.batch_no,
                    "task_dir": r.task_dir,
                    "clips_dir": str(cdir),
                    "copy_text": r.copy_text,
                }
            )
    ts_ms = int(time.time() * 1000)
    payload = {
        "handoff_id": f"stitch_{ts_ms}",
        "created_at": ts_ms,
        "consumed": False,
        "source": source,
        "range_text": range_text,
        "auto_start": bool(auto_start),
        "auto_ai": bool(auto_ai),
        "task_count": len(tasks_meta),
        "clip_dirs": clip_dirs,
        "tasks": tasks_meta,
    }
    HANDOFF_FILE.parent.mkdir(parents=True, exist_ok=True)
    HANDOFF_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _tok_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def _skel_norm(s: str) -> str:
    """去除所有空白与标点并转小写，用于忽略法语/拉丁语分词内部的断字空格误差（如 'che min' vs 'chemin'）。"""
    import unicodedata
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[\W_]+", "", s, flags=re.UNICODE)


def sync_stitch_missing_to_orante(
    missing_map: dict[str, Any],
    cuts_map: dict[str, Any] | None = None,
    folder_of: dict[str, str] | None = None,
    out_dirs: dict[str, str] | None = None,
) -> dict[str, Any]:
    """将 CUTORA 智能拼接发现的缺词/遗漏片段自动定位到 Orante 对应任务的指定分镜 (`seg['mark'] = True`, `seg['missing'] = ...`)，
    写回各 `<task_dir>/manifest.json` 并在各自 `<clips_dir>/_stitched/缺失文案清单.txt` 落盘，同时通知运行中的 Orante 刷新。
    这样用户回到 Orante -> 收片 -> 全部任务 -> 已标记 即可一键批量重生补全！
    """
    import difflib
    import urllib.request

    cuts_map = cuts_map or {}
    folder_of = folder_of or {}
    out_dirs = out_dirs or {}
    marked_summary: list[dict[str, Any]] = []

    for gnum, info in (missing_map or {}).items():
        if isinstance(info, dict):
            holes = list(info.get("missing") or [])
            clip_names = list(info.get("clips") or [])
        elif isinstance(info, list):
            holes = list(info)
            clip_names = []
        else:
            continue

        folder_str = folder_of.get(gnum) or ""
        if not folder_str and gnum in out_dirs:
            folder_str = str(Path(out_dirs[gnum]).parent)
        if not folder_str:
            continue
        cdir = Path(folder_str)
        tdir = cdir.parent if cdir.name.lower() in ("clips", "片段") else cdir
        mf_path = tdir / "manifest.json"
        st_dir = cdir / "_stitched"

        # 若未传入 cuts_map 或键名被归一化（如 T0191 -> T191），尝试从 _stitched/cutlist.json 兜底读取
        group_cuts: list[dict[str, Any]] = list(cuts_map.get(gnum) or [])
        if not group_cuts:
            for k, v in cuts_map.items():
                if str(k).lstrip("Tt0") == str(gnum).lstrip("Tt0") and isinstance(v, list):
                    group_cuts = list(v)
                    break
        if not group_cuts and (st_dir / "cutlist.json").is_file():
            try:
                cl_data = json.loads((st_dir / "cutlist.json").read_text(encoding="utf-8"))
                if isinstance(cl_data, dict):
                    for k, v in cl_data.items():
                        if isinstance(v, list) and (k == gnum or str(k).lstrip("Tt0") == str(gnum).lstrip("Tt0") or len(cl_data) == 1):
                            group_cuts = list(v)
                            break
            except Exception:
                pass

        # 建立分镜号 s_num -> cutlist 条目映射
        clip_by_s: dict[int, dict[str, Any]] = {}
        for it in group_cuts:
            if not isinstance(it, dict):
                continue
            nm = str(it.get("name") or "")
            m_sn = re.search(r"-s0*(\d+)", nm, re.IGNORECASE)
            if m_sn:
                clip_by_s[int(m_sn.group(1))] = it

        mdata: dict[str, Any] | None = None
        segs: list[dict[str, Any]] = []
        if mf_path.is_file():
            try:
                mdata = json.loads(mf_path.read_text(encoding="utf-8"))
                segs = list(mdata.get("segments") or [])
            except Exception:
                mdata = None
                segs = []

        # 先清除该任务所有分镜上的历史旧标记，防止上次拼接的残留标记污染本次结果
        for seg in segs:
            if isinstance(seg, dict):
                seg["mark"] = False
                seg.pop("missing", None)

        if not holes:
            if mdata is not None and segs:
                try:
                    mf_path.write_text(json.dumps(mdata, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
            continue

        # 预计算每个分镜的无空格字符骨架与在整篇文案中的真实词区间
        copy_text = ""
        if mdata and isinstance(mdata.get("copy"), dict):
            copy_text = str(mdata["copy"].get("text") or "")
        copy_words = _tok_words(copy_text) if copy_text else []

        seg_infos: list[dict[str, Any]] = []
        cursor = 0
        for seg in segs:
            s_num = int(seg.get("s", 0))
            s_txt = str(seg.get("text") or "")
            s_skel = _skel_norm(s_txt)
            w_list = _tok_words(s_txt)
            # 若存在 copy_words，通过字符骨架在 copy_words 中对齐真实起止词号，消除断词空格偏移
            w0, w1 = cursor, cursor + len(w_list)
            if copy_words and s_skel:
                best_w0, best_w1, best_ratio = w0, w1, 0.0
                search_lo = max(0, cursor - 6)
                search_hi = min(len(copy_words), cursor + len(w_list) + 10)
                for i0 in range(search_lo, search_hi):
                    acc = ""
                    for i1 in range(i0 + 1, min(len(copy_words) + 1, i0 + len(w_list) + 6)):
                        acc += _skel_norm(copy_words[i1 - 1])
                        r = difflib.SequenceMatcher(None, s_skel, acc).ratio()
                        if r > best_ratio:
                            best_ratio = r
                            best_w0, best_w1 = i0, i1
                if best_ratio >= 0.72:
                    w0, w1 = best_w0, best_w1
                    cursor = w1
                else:
                    cursor += len(w_list)
            else:
                cursor += len(w_list)

            cl_item = clip_by_s.get(s_num) or {}
            is_failed_clip = bool(
                cl_item and (
                    cl_item.get("keep") is False
                    or cl_item.get("level") in ("junk", "low", "none")
                )
            )
            is_good_clip = bool(
                cl_item and cl_item.get("keep") is True
                and cl_item.get("level") == "ok"
                and float(cl_item.get("ratio") or 0.0) >= 0.70
            )
            seg_infos.append({
                "seg": seg,
                "s": s_num,
                "text": s_txt,
                "skel": s_skel,
                "w0": w0,
                "w1": w1,
                "words": w_list,
                "is_failed_clip": is_failed_clip,
                "is_good_clip": is_good_clip,
            })

        hit_s_nums: list[int] = []
        hole_matched_s: list[list[int]] = []

        for mm in holes:
            if not isinstance(mm, dict):
                mm = {"text": str(mm), "s": -1, "e": -1}
            m_text = str(mm.get("text") or "").strip()
            if not m_text:
                hole_matched_s.append([])
                continue
            m_s = int(mm.get("s", -1))
            m_e = int(mm.get("e", -1))
            m_skel = _skel_norm(m_text)
            matched_infos: list[dict[str, Any]] = []

            # (A) 优先使用无空格字符骨架精准匹配（彻底免疫 'che min' / 'Part age' 等断字空格与词号偏移）
            if m_skel and seg_infos:
                scored: list[tuple[float, float, float, dict[str, Any]]] = []
                for si in seg_infos:
                    s_skel = si["skel"]
                    if not s_skel:
                        continue
                    match = difflib.SequenceMatcher(None, m_skel, s_skel).find_longest_match(
                        0, len(m_skel), 0, len(s_skel)
                    )
                    ov = match.size
                    cov_seg = ov / max(1, len(s_skel))
                    cov_hole = ov / max(1, len(m_skel))
                    score = max(cov_seg, cov_hole)
                    if si["is_failed_clip"] and ov >= 8:
                        score += 0.25
                    scored.append((score, cov_seg, cov_hole, si))

                for score, cov_seg, cov_hole, si in scored:
                    # 若某分镜本身视频已判定为 ok 且仅在边界沾了 1~2 个词（如上一镜结尾吞词），不误标该 ok 分镜
                    if si["is_good_clip"] and cov_seg < 0.45 and any(
                        other["is_failed_clip"] and max(c_s, c_h) >= 0.45
                        for _, c_s, c_h, other in scored
                    ):
                        continue
                    if cov_seg >= 0.42 or cov_hole >= 0.45:
                        matched_infos.append(si)

                if not matched_infos and scored:
                    scored.sort(key=lambda x: x[0], reverse=True)
                    if scored[0][0] >= 0.30:
                        matched_infos.append(scored[0][3])

            # (B) 词坐标区间重叠匹配（使用经过字符骨架校准后的 w0, w1）
            if not matched_infos and m_s >= 0 and m_e > m_s:
                for si in seg_infos:
                    ov_w = max(0, min(si["w1"], m_e) - max(si["w0"], m_s))
                    seg_w_len = max(1, si["w1"] - si["w0"])
                    hole_w_len = max(1, m_e - m_s)
                    if ov_w > 0 and (ov_w / seg_w_len >= 0.35 or ov_w / hole_w_len >= 0.45):
                        if si["is_good_clip"] and (ov_w / seg_w_len) < 0.45:
                            continue
                        matched_infos.append(si)

            # (C) cutlist 中明确失败的片段兜底匹配
            if not matched_infos and seg_infos:
                for si in seg_infos:
                    if si["is_failed_clip"]:
                        matched_infos.append(si)

            # (D) 兜底：若仍未命中，标记最后一个分镜
            if not matched_infos and seg_infos:
                matched_infos.append(seg_infos[-1])

            cur_hole_s: list[int] = []
            for si in matched_infos:
                seg = si["seg"]
                seg["mark"] = True
                prev_miss = str(seg.get("missing") or "").strip()
                if not prev_miss:
                    seg["missing"] = m_text
                elif m_text not in prev_miss:
                    seg["missing"] = f"{prev_miss} … {m_text}"
                s_val = int(seg.get("s", 0))
                if s_val not in cur_hole_s:
                    cur_hole_s.append(s_val)
                if s_val not in hit_s_nums:
                    hit_s_nums.append(s_val)
            hole_matched_s.append(sorted(cur_hole_s))

        # 1. 在该任务自己的 clips/_stitched/缺失文案清单.txt 写入缺失报告（带对应分镜号，与 Orante 标记 100% 一致）
        try:
            st_dir.mkdir(parents=True, exist_ok=True)
            lines = [
                "缺失文案清单 — 以下内容现有片段都没讲到, 请在 Orante「全部任务 -> 已标记」中一键重生",
                "=" * 60,
                f"\n【组 {gnum}】 现有片段: {', '.join(clip_names)}",
            ]
            if hit_s_nums:
                s_tags = ", ".join(f"s{sn:03d}" for sn in sorted(hit_s_nums))
                lines.append(f"  已自动标记 Orante 分镜 ({len(hit_s_nums)}段): {s_tags}")
            for mi, mm in enumerate(holes, 1):
                s_list = hole_matched_s[mi - 1] if mi - 1 < len(hole_matched_s) else []
                s_label = f"对应分镜 {'/'.join(f's{x:03d}' for x in s_list)} · " if s_list else ""
                if isinstance(mm, dict):
                    s0 = int(mm.get("s", 0)) + 1
                    e0 = int(mm.get("e", 0))
                    mtxt = str(mm.get("text") or "").strip()
                    lines.append(f"  缺失{mi} ({s_label}文案第{s0}~{e0}词):")
                    lines.append(f"    {mtxt}")
                else:
                    lines.append(f"  缺失{mi} ({s_label.rstrip(' · ')}):")
                    lines.append(f"    {mm}")
            (st_dir / "缺失文案清单.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            pass

        if mdata is not None and segs:
            try:
                mf_path.write_text(json.dumps(mdata, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            if hit_s_nums:
                marked_summary.append(
                    {
                        "task_id": str(mdata.get("task_id") or tdir.name.split("-")[0]),
                        "name": str(mdata.get("name") or tdir.name),
                        "task_dir": str(tdir),
                        "marked_segments": sorted(hit_s_nums),
                    }
                )

    # 3. 实时通知所有运行中的 Orante 后端 (127.0.0.1:9400..9419) 广播刷新并同步标记
    if marked_summary:
        for port in (find_running_orante_ports() or [9400]):
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/tasks/sync-stitch-marks",
                    data=json.dumps({"marked_tasks": marked_summary}, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Pianyu-Internal": "1"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=2.5).read()
            except Exception:
                pass

    return {
        "ok": True,
        "marked_task_count": len(marked_summary),
        "marked_segment_count": sum(len(x["marked_segments"]) for x in marked_summary),
        "marked_tasks": marked_summary,
    }


# --- 6. Orante ⇄ CUTORA 跨平台单实例检测、API 控制与一键双向唤醒/聚焦 ---


def find_running_orante_ports(base_port: int = 9400, span: int = 20) -> list[int]:
    """扫描本机运行中的 Orante 实例端口 (默认 9400..9419)。"""
    import socket
    import urllib.request

    active_ports: list[int] = []
    for offset in range(max(1, span)):
        port = base_port + offset
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.12):
                pass
        except Exception:
            continue
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/ping",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=0.45) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                if isinstance(data, dict) and data.get("ok") and data.get("app") == "orante":
                    active_ports.append(port)
        except Exception:
            continue
    return active_ports


def _bring_window_foreground_by_keywords(keywords: tuple[str, ...], app_activate_name: str = "") -> bool:
    """跨平台将标题包含指定关键字的桌面窗口恢复并置顶 (Windows Win32 + macOS AppleScript)。"""
    import os
    import subprocess
    import sys

    activated = False
    if os.name == "nt":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            def _enum_cb(hwnd, _lparam):
                nonlocal activated
                if user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        title = buf.value or ""
                        if any(k.lower() in title.lower() for k in keywords):
                            if user32.IsIconic(hwnd):
                                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                            else:
                                user32.ShowWindow(hwnd, 5)  # SW_SHOW
                            user32.SetForegroundWindow(hwnd)
                            activated = True
                            return False
                return True

            user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
        except Exception:
            pass
        if not activated and app_activate_name:
            try:
                ps_cmd = (
                    "$ws = New-Object -ComObject WScript.Shell; "
                    f"[void]$ws.AppActivate('{app_activate_name}')"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_cmd],
                    timeout=2.5,
                    creationflags=0x08000000,
                )
                activated = True
            except Exception:
                pass
    elif sys.platform == "darwin":
        try:
            cond = " or ".join(f'name contains "{k}"' for k in keywords)
            script = f'tell application "System Events" to set frontmost of (first process whose {cond} or name contains "Python") to true'
            subprocess.run(["osascript", "-e", script], timeout=2.5, capture_output=True)
            activated = True
        except Exception:
            pass
    return activated


def wake_orante_window(
    node: str = "review",
    filter_mode: str = "marked",
    tid: str = "",
    message: str = "",
    hint: str = "",
) -> bool:
    """从 CUTORA 一键唤醒或拉起 Orante 窗口，并自动切换到指定节点（默认 ⑤ 收片 -> 全部任务 -> 已标记）。"""
    import os
    import subprocess
    import time
    import urllib.request

    ports = find_running_orante_ports()
    if ports:
        payload = json.dumps(
            {
                "node": node,
                "filter": filter_mode,
                "tid": tid,
                "message": message,
                "hint": hint,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        for port in ports:
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/ui/focus",
                    data=payload,
                    headers={"Content-Type": "application/json", "X-Pianyu-Internal": "1"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=1.8).read()
            except Exception:
                pass
        _bring_window_foreground_by_keywords(("Orante", "制片台", "分镜工作台"), app_activate_name="Orante")
        return True

    # 若 Orante 尚未启动，则自动在后台拉起 Orante 并定位到目标节点
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["ORANTE_START_NODE"] = node
    bin_dirs = [
        str(LTX_ROOT / "runtime" / "bin"),
        str(LTX_ROOT / "runtime" / "ffmpeg" / "bin"),
        str(LTX_ROOT / "runtime" / "ffmpeg"),
    ]
    env["PATH"] = os.pathsep.join(bin_dirs + [env.get("PATH", "")])
    py_exe = resolve_python_exe(gui=True)
    log_dir = LTX_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (
        open(log_dir / "orante_stdout.log", "a", encoding="utf-8") as stdout_log,
        open(log_dir / "orante_stderr.log", "a", encoding="utf-8") as stderr_log,
    ):
        popen_kwargs: dict[str, Any] = {
            "cwd": str(LTX_ROOT / "modules" / "orante_desk"),
            "env": env,
            "stdout": stdout_log,
            "stderr": stderr_log,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = 0x00000008
        else:
            popen_kwargs["start_new_session"] = True
            popen_kwargs["close_fds"] = True
        subprocess.Popen([py_exe, "-m", "orante"], **popen_kwargs)
    time.sleep(0.5)
    return True


def is_cutora_running() -> bool:
    """检测 CUTORA 是否正在运行（Windows 检测单实例命名互斥体，macOS/POSIX 检测 flock 锁文件或本地 API）。"""
    import os
    import tempfile

    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            h = kernel32.OpenMutexW(0x00100000, False, "Local\\CUTORA_SingleInstance_Mutex_v1")
            if h:
                kernel32.CloseHandle(h)
                return True
        except Exception:
            pass
        return False

    # macOS / POSIX: 检测单实例 flock 文件锁（绝不仅凭残留 PID 数字误判，必须真实持有 flock 才算运行中）
    for lock_cand in (
        Path(tempfile.gettempdir()) / "cutora_single_instance.pid",
        Path.home() / ".cutora_single_instance.pid",
        Path.home() / "Library" / "Application Support" / "CUTORA" / "cutora.lock",
    ):
        if not lock_cand.is_file():
            continue
        try:
            import fcntl

            with open(lock_cand, "a+", encoding="utf-8") as fh:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    return True
        except Exception:
            pass

    # 兜底探测本地控制 API (127.0.0.1:47321)，必须校验服务身份为 CUTORA，防止端口被其他程序占用误判
    try:
        import urllib.request

        req = urllib.request.Request("http://127.0.0.1:47321/v1/health", method="GET")
        with urllib.request.urlopen(req, timeout=0.35) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            if "cutora" in raw.lower() or '"ok"' in raw.lower():
                return True
    except Exception:
        pass
    return False


def call_cutora_api(command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """调用 CUTORA 本地控制 API (127.0.0.1:47321/v1/commands/<command>)。"""
    import sys
    import urllib.request

    token = ""
    try:
        cutora_dir = str(LTX_ROOT / "modules" / "cutora_editor")
        if cutora_dir not in sys.path:
            sys.path.insert(0, cutora_dir)
        from cutora.api.token import read_token  # type: ignore

        token = read_token() or ""
    except Exception:
        pass

    url = f"http://127.0.0.1:47321/v1/commands/{command}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url,
        data=json.dumps({"params": params or {}}, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _has_unconsumed_handoff() -> bool:
    try:
        if not HANDOFF_FILE.is_file():
            return False
        data = json.loads(HANDOFF_FILE.read_text(encoding="utf-8"))
        return bool(isinstance(data, dict) and not data.get("consumed") and data.get("clip_dirs"))
    except Exception:
        return False


_LAST_CUTORA_LAUNCH: dict[str, Any] = {}


def get_last_cutora_launch() -> dict[str, Any]:
    return dict(_LAST_CUTORA_LAUNCH)


def launch_cutora_room_detailed(
    room: str = "stitch",
    project_path: str = "",
    prepare_handoff: bool = False,
) -> dict[str, Any]:
    """启动或切换聚焦 CUTORA 到指定功能房间，返回包含 Popen 句柄、session_id 与日志路径的结构化 LaunchResult。"""
    import os
    import subprocess
    import time
    from .launch_state import write_launch_state

    global _LAST_CUTORA_LAUNCH
    room = (room or "stitch").strip().lower()
    if room == "stitch":
        try:
            from .spine_db import get_spine_db

            get_spine_db().export_batch_for_cutora_stitch()
        except Exception:
            pass
        if prepare_handoff or not _has_unconsumed_handoff():
            try:
                prepare_cutora_stitch_handoff(
                    auto_start=True,
                    auto_ai=True,
                    source="launcher",
                )
            except Exception:
                pass

    if is_cutora_running():
        try:
            if project_path and Path(project_path).is_file():
                call_cutora_api("project.open", {"path": str(project_path)})
            call_cutora_api("ui.focus", {"panel": room or "preview"})
        except Exception:
            pass
        _bring_window_foreground_by_keywords(("CUTORA", "片段拼接", "智能拼接"), app_activate_name="CUTORA")
        res = {
            "ok": True,
            "already_running": True,
            "proc": None,
            "session_id": "",
            "stdout_path": str(LTX_ROOT / "data" / "logs" / "cutora_stdout.log"),
            "stderr_path": str(LTX_ROOT / "data" / "logs" / "cutora_stderr.log"),
        }
        _LAST_CUTORA_LAUNCH = res
        return res

    session_id = f"cutora-{int(time.time() * 1000)}"
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["CUTORA_RUNTIME"] = str(LTX_ROOT / "runtime")
    env["CUTORA_START_ROOM"] = room
    env["CUTORA_LAUNCH_SESSION_ID"] = session_id
    if room == "stitch":
        env["CUTORA_SYNC_ORANTE"] = "1"
    if project_path:
        env["CUTORA_OPEN_PROJECT"] = str(project_path)
    bin_dirs = [
        str(LTX_ROOT / "runtime" / "bin"),
        str(LTX_ROOT / "runtime" / "ffmpeg" / "bin"),
        str(LTX_ROOT / "runtime" / "ffmpeg"),
    ]
    env["PATH"] = os.pathsep.join(bin_dirs + [env.get("PATH", "")])

    py_exe = resolve_python_exe(gui=True)
    log_dir = LTX_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "cutora_stdout.log"
    stderr_path = log_dir / "cutora_stderr.log"
    write_launch_state("BOOTSTRAP_START", app_name="cutora", session_id=session_id, detail={"room": room, "python": py_exe})
    with (
        open(stdout_path, "a", encoding="utf-8") as stdout_log,
        open(stderr_path, "a", encoding="utf-8") as stderr_log,
    ):
        stdout_log.write(f"\n=== [{time.strftime('%Y-%m-%d %H:%M:%S')}] LAUNCH {session_id} (room={room}) ===\n")
        stdout_log.flush()
        popen_kwargs: dict[str, Any] = {
            "cwd": str(LTX_ROOT / "modules" / "cutora_editor"),
            "env": env,
            "stdout": stdout_log,
            "stderr": stderr_log,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = 0x00000008
        else:
            popen_kwargs["start_new_session"] = True
            popen_kwargs["close_fds"] = True
        proc = subprocess.Popen([py_exe, "-m", "cutora"], **popen_kwargs)

    res = {
        "ok": True,
        "already_running": False,
        "proc": proc,
        "pid": proc.pid,
        "session_id": session_id,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }
    _LAST_CUTORA_LAUNCH = res
    return res


def launch_cutora_room(
    room: str = "stitch",
    project_path: str = "",
    prepare_handoff: bool = False,
) -> bool:
    """启动或切换聚焦 CUTORA 到指定功能房间 (兼容返回 bool)。"""
    res = launch_cutora_room_detailed(room=room, project_path=project_path, prepare_handoff=prepare_handoff)
    return bool(res.get("ok"))

