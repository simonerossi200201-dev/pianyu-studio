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
                    # 仅纳入有 clips 目录的任务
                    if r.clips_dir and Path(r.clips_dir).is_dir():
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


def sync_stitch_missing_to_orante(
    missing_map: dict[str, Any],
    cuts_map: dict[str, Any] | None = None,
    folder_of: dict[str, str] | None = None,
    out_dirs: dict[str, str] | None = None,
) -> dict[str, Any]:
    """将 CUTORA 智能拼接发现的缺词/遗漏片段自动定位到 Orante 对应任务的指定分镜 (`seg['mark'] = True`, `seg['missing'] = ...`)，
    写回 각 `<task_dir>/manifest.json` 并在各自 `<clips_dir>/_stitched/缺失文案清单.txt` 落盘，同时通知运行中的 Orante 刷新。
    这样用户回到 Orante -> 收片 -> 全部任务 -> 已标记 即可一键批量重生补全！
    """
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
        if not holes:
            continue

        folder_str = folder_of.get(gnum) or ""
        if not folder_str and gnum in out_dirs:
            folder_str = str(Path(out_dirs[gnum]).parent)
        if not folder_str:
            continue
        cdir = Path(folder_str)
        tdir = cdir.parent if cdir.name.lower() in ("clips", "片段") else cdir
        mf_path = tdir / "manifest.json"

        # 1. 在该任务自己的 clips/_stitched/缺失文案清单.txt 写入缺失报告
        try:
            st_dir = cdir / "_stitched"
            st_dir.mkdir(parents=True, exist_ok=True)
            lines = [
                "缺失文案清单 — 以下内容现有片段都没讲到, 请在 Orante「全部任务 -> 已标记」中一键重生",
                "=" * 60,
                f"\n【组 {gnum}】 现有片段: {', '.join(clip_names)}",
            ]
            for mi, mm in enumerate(holes, 1):
                if isinstance(mm, dict):
                    s0 = int(mm.get("s", 0)) + 1
                    e0 = int(mm.get("e", 0))
                    mtxt = str(mm.get("text") or "").strip()
                    lines.append(f"  缺失{mi} (文案第{s0}~{e0}词):")
                    lines.append(f"    {mtxt}")
                else:
                    lines.append(f"  缺失{mi}:")
                    lines.append(f"    {mm}")
            (st_dir / "缺失文案清单.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            pass

        if not mf_path.is_file():
            continue

        # 2. 精准匹配到 manifest.json 的具体分镜 s，并设置 mark=True 与 missing 文案
        try:
            mdata = json.loads(mf_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        segs = mdata.get("segments") or []
        if not segs:
            continue

        # 计算每个分镜在整篇文案中的词区间 [w0, w1)
        seg_spans: list[tuple[dict[str, Any], int, int, list[str]]] = []
        cursor = 0
        for seg in segs:
            w_list = _tok_words(str(seg.get("text") or ""))
            seg_spans.append((seg, cursor, cursor + len(w_list), w_list))
            cursor += len(w_list)

        hit_s_nums: list[int] = []
        for mm in holes:
            if not isinstance(mm, dict):
                mm = {"text": str(mm), "s": -1, "e": -1}
            m_text = str(mm.get("text") or "").strip()
            if not m_text:
                continue
            m_s = int(mm.get("s", -1))
            m_e = int(mm.get("e", -1))
            m_words = _tok_words(m_text)
            matched_segs: list[dict[str, Any]] = []

            # (A) 词坐标区间重叠匹配
            if m_s >= 0 and m_e > m_s:
                for seg, w0, w1, _ in seg_spans:
                    if max(w0, m_s) < min(w1, m_e):
                        matched_segs.append(seg)

            # (B) 子串或词集合高重合度匹配
            if not matched_segs and m_words:
                best_seg = None
                best_score = 0.0
                m_low = m_text.lower()
                for seg, _, _, s_words in seg_spans:
                    stxt_low = str(seg.get("text") or "").lower()
                    if m_low in stxt_low or (stxt_low and stxt_low in m_low):
                        matched_segs.append(seg)
                        continue
                    if s_words:
                        common = sum(1 for w in m_words if w in s_words)
                        score = common / max(1, len(m_words))
                        if score > best_score:
                            best_score = score
                            best_seg = seg
                if not matched_segs and best_seg is not None and best_score >= 0.35:
                    matched_segs.append(best_seg)

            # (C) cutlist 邻近低置信/截断片段的真实文件名 -s00X 匹配
            if not matched_segs:
                for it in (cuts_map.get(gnum) or []):
                    nm = str(it.get("name") or "")
                    m_sn = re.search(r"-s0*(\d+)", nm, re.IGNORECASE)
                    if not m_sn:
                        continue
                    s_num = int(m_sn.group(1))
                    it_e = int(it.get("s_end", -1))
                    it_s = int(it.get("s_start", -1))
                    if (m_s >= 0 and (abs(it_e - m_s) <= 2 or abs(it_s - m_e) <= 2)) or it.get("level") in ("low", "none", "partial"):
                        for seg in segs:
                            if int(seg.get("s", 0)) == s_num and seg not in matched_segs:
                                matched_segs.append(seg)

            # (D) 兜底：若仍未命中，标记最后一个分镜
            if not matched_segs and segs:
                matched_segs.append(segs[-1])

            for seg in matched_segs:
                seg["mark"] = True
                prev_miss = str(seg.get("missing") or "").strip()
                if not prev_miss:
                    seg["missing"] = m_text
                elif m_text not in prev_miss:
                    seg["missing"] = f"{prev_miss} … {m_text}"
                s_val = int(seg.get("s", 0))
                if s_val not in hit_s_nums:
                    hit_s_nums.append(s_val)

        if hit_s_nums:
            try:
                mf_path.write_text(json.dumps(mdata, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            marked_summary.append(
                {
                    "task_id": str(mdata.get("task_id") or tdir.name.split("-")[0]),
                    "name": str(mdata.get("name") or tdir.name),
                    "task_dir": str(tdir),
                    "marked_segments": sorted(hit_s_nums),
                }
            )

    # 3. 实时通知 Orante 后端 (127.0.0.1:9400) 广播刷新并同步标记
    if marked_summary:
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:9400/api/tasks/sync-stitch-marks",
                data=json.dumps({"marked_tasks": marked_summary}, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3.0).read()
        except Exception:
            pass

    return {
        "ok": True,
        "marked_task_count": len(marked_summary),
        "marked_segment_count": sum(len(x["marked_segments"]) for x in marked_summary),
        "marked_tasks": marked_summary,
    }
