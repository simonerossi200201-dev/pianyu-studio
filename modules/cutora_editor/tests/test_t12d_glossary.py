# -*- coding: utf-8 -*-
"""T12d 提交 3：术语表、subtitle.auto schema、纠正后重跑。"""
from __future__ import annotations

import os


def test_glossary_parse_and_200_token_cap():
    from cutora.asr.glossary import clip_terms, parse_terms, terms_to_prompt
    terms = parse_terms("Abraham\n\nIsaac\nAbraham\n  Sarah  ")
    assert terms == ["Abraham", "Isaac", "Sarah"]
    many = ["w%d" % i for i in range(250)]
    clipped = clip_terms(many, max_tokens=200)
    assert len(clipped) == 200
    prompt = terms_to_prompt(["King David", "Jerusalem", "Psalm 23"])
    assert "King David" in prompt
    assert "Jerusalem" in prompt


def test_global_glossary_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    from cutora.asr.glossary import (
        global_glossary_path, load_global_terms, save_global_terms,
        prompt_and_hotwords,
    )
    path = save_global_terms(["Melchizedek", "Bethel"])
    assert os.path.isfile(path)
    assert path == global_glossary_path()
    assert load_global_terms() == ["Melchizedek", "Bethel"]
    prompt, hot = prompt_and_hotwords(["Abraham"])
    assert "Abraham" in prompt and "Melchizedek" in prompt
    assert prompt == hot


def test_subtitle_auto_schema_language_copy_and_glossary():
    from cutora.core.command_schema import check_schema
    from cutora.core.commands import get_registry
    spec = get_registry().get("subtitle.auto")
    check_schema(spec.params_schema)
    props = spec.params_schema["properties"]
    lang = props["language"]
    desc = lang.get("description") or ""
    assert "省略" in desc
    assert "当前设置" in desc
    assert "auto" in desc
    assert "30" in desc
    assert "oneOf" in lang or lang.get("type") in ("string", "array")
    assert "model" in props
    assert "glossary" in props
    gl = get_registry().get("glossary.get")
    assert gl.headless is True
    assert len(gl.doc_zh) >= 40
    check_schema(gl.params_schema)
    gs = get_registry().get("glossary.set")
    assert len(gs.doc_zh) >= 40
    check_schema(gs.params_schema)


def test_glossary_commands_global_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    from cutora.core.commands import get_bus
    bus = get_bus()
    out = bus.execute(
        "glossary.set",
        {"scope": "global", "text": "Isaac\nRebekah"},
        source="cli",
    )
    assert out["count"] == 2
    got = bus.execute("glossary.get", {"scope": "global"}, source="cli")
    assert got["terms"] == ["Isaac", "Rebekah"]


def test_normalize_subtitle_languages():
    from cutora.asr.detect_plan import normalize_subtitle_languages
    assert normalize_subtitle_languages(None, "en") == []
    assert normalize_subtitle_languages("", "it") == []
    assert normalize_subtitle_languages("auto", "en") == ["auto"]
    assert normalize_subtitle_languages("fr", "en") == ["fr"]
    assert normalize_subtitle_languages(["en", "it"], "en") == ["en", "it"]


def test_resolve_asr_model_id():
    from cutora.asr.models import resolve_asr_model_id
    assert resolve_asr_model_id(None) is None
    assert resolve_asr_model_id("") is None
    assert resolve_asr_model_id("small") == "asr-small"
    assert resolve_asr_model_id("large-v3-turbo") == "asr-large-v3-turbo"
    assert resolve_asr_model_id("asr-medium") == "asr-medium"


def test_glossary_project_scope_needs_project():
    from cutora.core.commands import CommandBus, CommandError, NeedsGui, get_registry
    bus = CommandBus(get_registry())
    try:
        bus.execute("glossary.get", {"scope": "project"}, source="cli")
        assert False, "expected needs_project"
    except NeedsGui:
        assert False, "headless command must not raise NeedsGui"
    except CommandError as exc:
        assert exc.code == "needs_project"
    try:
        bus.execute("glossary.set", {"scope": "project", "text": "x"}, source="cli")
        assert False, "expected needs_project"
    except NeedsGui:
        assert False, "headless command must not raise NeedsGui"
    except CommandError as exc:
        assert exc.code == "needs_project"
