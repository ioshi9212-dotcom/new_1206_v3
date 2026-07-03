"""Director Mode runtime for Akira 1206 v3.

This module adds a script-drafting layer without removing the existing live-game
runtime. It is intentionally conservative: by default it reads only explicitly
requested character cards and director/style rules. Dynamic state, knowledge,
relationships, calendar and inventory files are skipped unless the user asks to
add them through addDirectorContext.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Body

from app import compact as base
from app.compact import app

DIRECTOR_RUNTIME_VERSION = f"{base.APP_VERSION}+director-stage1"
MAX_FILE_CHARS = 6500
MAX_PACKET_CHARS = 42000

# User-facing aliases. Keep this intentionally boring and explicit.
CHARACTER_ALIASES: dict[str, str] = {
    "акира": "akira",
    "кира": "akira",
    "akira": "akira",
    "юкира": "akira",
    "джун": "jun",
    "jun": "jun",
    "джун картер": "jun",
    "ирэй": "irey",
    "ирей": "irey",
    "irey": "irey",
    "эмма": "emma",
    "emma": "emma",
    "райден": "raiden",
    "рейден": "raiden",
    "рейдон": "raiden",
    "рэйдон": "raiden",
    "raiden": "raiden",
    "rayden": "raiden",
    "рей": "ray",
    "рэй": "ray",
    "ray": "ray",
    "хару": "haru",
    "haru": "haru",
    "мики": "miki",
    "miki": "miki",
    "алекс": "alex",
    "alex": "alex",
    "широ": "shiro",
    "shiro": "shiro",
    "самуэль": "samuel",
    "самуель": "samuel",
    "samuel": "samuel",
    "хана": "hana",
    "hana": "hana",
}

# Default character files. We do not include knowledge/relationships/past here.
CHARACTER_DEFAULT_CANDIDATES: tuple[str, ...] = (
    "characters/{id}/main.yaml",
    "characters/{id}/character.yaml",
    "characters/{id}/profile.yaml",
)

# Optional files are added only when the user asks for them.
OPTIONAL_FILE_HINTS: dict[str, tuple[str, ...]] = {
    "knowledge": (
        "знани", "knowledge", "что знает", "что знает персонаж", "знает",
    ),
    "relationships": (
        "отношен", "relationship", "relations", "связь", "динамика",
    ),
    "past": (
        "прошл", "past", "backstory", "биограф", "истори", "воспомин",
    ),
    "energy": (
        "энерг", "сила", "способност", "power", "ability",
    ),
    "calendar": (
        "календар", "дата", "день", "расписан", "calendar", "schedule",
    ),
    "state": (
        "state", "состояни", "текущий стейт", "current_state",
    ),
    "inventory": (
        "inventory", "инвент", "карман", "записк", "документ",
    ),
    "lore": (
        "лор", "lore", "мир", "кайрос", "материк", "самуэль", "лаборатор",
    ),
}

OPTIONAL_CANDIDATES: dict[str, tuple[str, ...]] = {
    "knowledge": (
        "characters/{id}/knowledge.yaml",
        "characters/{id}/knowledge.json",
        "state/character_knowledge/{id}.json",
    ),
    "relationships": (
        "characters/{id}/relationships.yaml",
        "characters/{id}/relationship.yaml",
        "state/relationships.json",
        "state/relationship_pairs.json",
    ),
    "past": (
        "characters/{id}/past.yaml",
        "characters/{id}/backstory.yaml",
        "characters/{id}/memory.yaml",
    ),
    "energy": (
        "characters/{id}/energy.yaml",
        "characters/{id}/power.yaml",
        "canon_lore/energy.yaml",
    ),
    "calendar": (
        "calendar/current_day.yaml",
        "calendar/days/1206-08-31.yaml",
        "schedule/current.yaml",
    ),
    "state": (
        "state/current_state.json",
    ),
    "inventory": (
        "state/inventory_state.json",
        "state/current_state.json",
    ),
    "lore": (
        "canon_lore/main.yaml",
        "canon_lore/world.yaml",
        "canon_lore/samuel.yaml",
    ),
}

STYLE_CANDIDATES: tuple[str, ...] = (
    "gpt/director_mode_rules.md",
    "gpt/scene_format.md",
    "gpt/style.md",
    "gpt/writer_rules.md",
)

SCENE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "jun_house_start": (
        "scenes/start_scene_logic.md",
        "scenes/start_scene.md",
        "scenes/start_scene.yaml",
        "scenes/1206_start_house.md",
        "scenes/house_jun_start.md",
    ),
    "academy": (
        "scenes/academy_start.md",
        "scenes/academy_prequel.md",
        "scenes/1198_academy.md",
    ),
}

DEFAULT_FORBIDDEN_TERMS: tuple[str, ...] = (
    "Самуэль",
    "Samuel",
    "Рэй",
    "Ray",
    "Восточный сектор",
    "записка",
    "документы",
    "люди Самуэля",
    "люди самуэля",
    "после нас придут",
    "тебе опасно",
    "я предупреждаю",
)

SKIPPED_BY_DEFAULT: tuple[str, ...] = (
    "state/current_state.json",
    "state/relationships.json",
    "state/relationship_pairs.json",
    "state/knowledge_state.json",
    "state/inventory_state.json",
    "state/power_state.json",
    "state/future_locks_progress.json",
    "state/scene_history.json",
    "calendar/*",
    "schedule/*",
    "characters/*/knowledge.yaml",
    "characters/*/past.yaml",
    "characters/*/relationships.yaml",
)


def _payload(body: dict[str, Any] | None) -> dict[str, Any]:
    return body if isinstance(body, dict) else {}


def _now() -> str:
    return datetime.utcnow().isoformat()


def _safe_id(raw: Any, *, prefix: str = "draft") -> str:
    value = str(raw or "").strip()
    if not value:
        value = f"{prefix}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}"
    value = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_.-]+", "_", value).strip("_.-")
    return value[:90] or f"{prefix}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}"


def _normalize(text: str) -> str:
    return " ".join(str(text or "").lower().replace("ё", "е").split())


def _contains_alias(text: str, alias: str) -> bool:
    normalized = _normalize(text)
    alias_norm = _normalize(alias)
    if not alias_norm:
        return False
    # Cyrillic/latin word-ish boundary without requiring \b to understand Cyrillic.
    return re.search(rf"(?<![\wа-яА-Я]){re.escape(alias_norm)}(?![\wа-яА-Я])", normalized, flags=re.IGNORECASE) is not None


def _infer_characters(text: str, explicit: Any = None) -> list[str]:
    found: list[str] = []

    if isinstance(explicit, list):
        for item in explicit:
            key = _normalize(str(item))
            cid = CHARACTER_ALIASES.get(key, key)
            if cid and cid not in found:
                found.append(cid)

    for alias, cid in CHARACTER_ALIASES.items():
        if _contains_alias(text, alias) and cid not in found:
            found.append(cid)

    return found


def _infer_scene_keys(text: str) -> list[str]:
    n = _normalize(text)
    keys: list[str] = []
    if any(token in n for token in ("дом джуна", "джун", "лестниц", "старт", "ночь", "комната акиры")):
        keys.append("jun_house_start")
    if any(token in n for token in ("академ", "1198", "prequel", "приквел")):
        keys.append("academy")
    return keys


def _infer_forbidden(text: str, explicit: Any = None) -> list[str]:
    forbidden: list[str] = []
    n = _normalize(text)

    # If the user wrote a free-form "без ..." sentence, keep the most important
    # known sensitive terms as explicit locks.
    for term in DEFAULT_FORBIDDEN_TERMS:
        if _normalize(term) in n and term not in forbidden:
            forbidden.append(term)

    if any(token in n for token in ("без новых", "новые люди", "не вводи", "не добавляй персонажей")):
        forbidden.append("новые персонажи без разрешения")

    if "эмма" in n and any(token in n for token in ("союзниц", "предупрежд", "спаса", "мягк")):
        forbidden.append("Эмма не союзница, не спасательница и не предупреждает Акиру как заботу")

    if isinstance(explicit, list):
        for item in explicit:
            value = str(item).strip()
            if value and value not in forbidden:
                forbidden.append(value)

    return forbidden


def _read_existing_file(path: str, *, session_id: str | None = None, limit: int = MAX_FILE_CHARS) -> dict[str, Any] | None:
    text = base.read_text(path, session_id=session_id, default="")
    if not text.strip():
        return None
    truncated = len(text) > limit
    return {
        "path": path,
        "chars": min(len(text), limit),
        "truncated": truncated,
        "content": text[:limit],
    }


def _append_unique(items: list[str], values: list[str] | tuple[str, ...]) -> None:
    for value in values:
        if value not in items:
            items.append(value)


def _candidate_character_files(character_ids: list[str], *, include_optional: list[str] | None = None) -> list[str]:
    paths: list[str] = []
    for cid in character_ids:
        for pattern in CHARACTER_DEFAULT_CANDIDATES:
            path = pattern.format(id=cid)
            if path not in paths:
                paths.append(path)
    for block in include_optional or []:
        for cid in character_ids:
            for pattern in OPTIONAL_CANDIDATES.get(block, ()):
                path = pattern.format(id=cid)
                if path not in paths:
                    paths.append(path)
    return paths


def _detect_optional_blocks(text: str) -> list[str]:
    n = _normalize(text)
    blocks: list[str] = []
    for block, hints in OPTIONAL_FILE_HINTS.items():
        if any(_normalize(hint) in n for hint in hints):
            blocks.append(block)
    return blocks


def _extract_explicit_paths(text: str, explicit_files: Any = None) -> list[str]:
    paths: list[str] = []
    if isinstance(explicit_files, list):
        for item in explicit_files:
            value = str(item).strip().lstrip("/")
            if value and ".." not in Path(value).parts and value not in paths:
                paths.append(value)

    # Let advanced users paste exact paths in normal text, but keep it safe.
    for match in re.findall(r"(?:characters|state|calendar|canon_lore|gpt|scenes|schedule|npcs)/[\wа-яА-ЯёЁ./_-]+", text):
        value = match.strip().lstrip("/")
        if value and ".." not in Path(value).parts and value not in paths:
            paths.append(value)
    return paths


def _load_files(paths: list[str], *, session_id: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    loaded: list[dict[str, Any]] = []
    missing: list[str] = []
    seen: set[str] = set()
    total = 0
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        entry = _read_existing_file(path, session_id=session_id)
        if entry is None:
            missing.append(path)
            continue
        if total + len(entry["content"]) > MAX_PACKET_CHARS:
            remaining = max(0, MAX_PACKET_CHARS - total)
            if remaining <= 500:
                missing.append(f"{path} (skipped: packet char limit)")
                continue
            entry = dict(entry)
            entry["content"] = entry["content"][:remaining]
            entry["truncated"] = True
            entry["chars"] = len(entry["content"])
        total += len(entry["content"])
        loaded.append(entry)
    return loaded, missing


def _build_writer_packet(draft: dict[str, Any]) -> str:
    brief = draft.get("brief", {}) if isinstance(draft.get("brief"), dict) else {}
    manifest = draft.get("context_manifest", {}) if isinstance(draft.get("context_manifest"), dict) else {}
    loaded = manifest.get("loaded_files", []) if isinstance(manifest.get("loaded_files"), list) else []
    versions = draft.get("versions", []) if isinstance(draft.get("versions"), list) else []
    last_scene = versions[-1].get("scene_text") if versions and isinstance(versions[-1], dict) else ""

    parts: list[str] = []
    parts.append("# DIRECTOR MODE WRITER PACKET")
    parts.append("Ты не играешь live-новеллу. Ты пишешь черновик сцены по режиссёрскому запросу пользователя.")
    parts.append("Не вызывай processTurn/applyTurnResult. Не обновляй state мира. Не вводи новых персонажей без разрешения.")
    parts.append("")
    parts.append("## Режиссёрский запрос пользователя")
    parts.append(str(brief.get("raw_director_request") or "").strip())
    parts.append("")
    parts.append("## Персонажи, которых пользователь/система допустили в сцену")
    parts.append(", ".join(brief.get("character_ids") or []) or "не определены")
    parts.append("")
    parts.append("## Запреты сцены")
    forbidden = brief.get("forbidden") or []
    if forbidden:
        parts.extend(f"- {item}" for item in forbidden)
    else:
        parts.append("- Не добавлять события/персонажей вне режиссёрского запроса.")
    parts.append("")
    parts.append("## Что не подтянуто по умолчанию")
    parts.extend(f"- {item}" for item in SKIPPED_BY_DEFAULT)
    parts.append("")
    parts.append("## Загруженные файлы")
    if not loaded:
        parts.append("Файлы не загружены или не найдены. Пиши только по режиссёрскому запросу.")
    else:
        for entry in loaded:
            parts.append(f"\n### {entry.get('path')}")
            if entry.get("truncated"):
                parts.append("[Файл обрезан по лимиту Director Mode]")
            parts.append(str(entry.get("content") or ""))
    if last_scene:
        parts.append("\n## Текущая версия сцены для правки")
        parts.append(str(last_scene))
    revision_notes = draft.get("revision_notes") or []
    if revision_notes:
        parts.append("\n## Последние правки пользователя")
        for note in revision_notes[-5:]:
            if isinstance(note, dict):
                parts.append(f"- {note.get('text')}")
            else:
                parts.append(f"- {note}")
    parts.append("\n## Задача")
    parts.append("Напиши или перепиши художественный черновик сцены. Держи характеры, но не раскрывай скрытые факты, если пользователь не разрешил. Если сомневаешься — выбирай более сдержанную, сухую, режиссёрски управляемую версию.")
    return "\n".join(parts)


def _draft_path(draft_id: str) -> str:
    return f"drafts/director/{_safe_id(draft_id)}/draft.json"


def _load_draft(draft_id: str) -> dict[str, Any]:
    base.seed()
    data = base.read_json(_draft_path(draft_id), default=None)
    if isinstance(data, dict):
        return data
    return {
        "draft_id": _safe_id(draft_id),
        "created_at": _now(),
        "updated_at": _now(),
        "brief": {},
        "context_manifest": {"loaded_files": [], "missing_files": [], "manually_added": [], "skipped_by_default": list(SKIPPED_BY_DEFAULT)},
        "versions": [],
        "revision_notes": [],
    }


def _save_draft(draft: dict[str, Any]) -> None:
    draft["updated_at"] = _now()
    base.write_json(_draft_path(str(draft.get("draft_id") or "draft")), draft)


def _build_context_for_request(raw_request: str, *, explicit_characters: Any = None, explicit_files: Any = None, optional_blocks: list[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    characters = _infer_characters(raw_request, explicit_characters)
    forbidden = _infer_forbidden(raw_request)

    paths: list[str] = []
    _append_unique(paths, STYLE_CANDIDATES)
    _append_unique(paths, _candidate_character_files(characters, include_optional=optional_blocks or []))

    for scene_key in _infer_scene_keys(raw_request):
        _append_unique(paths, SCENE_CANDIDATES.get(scene_key, ()))

    _append_unique(paths, _extract_explicit_paths(raw_request, explicit_files))

    loaded, missing = _load_files(paths)
    brief = {
        "raw_director_request": raw_request,
        "character_ids": characters,
        "forbidden": forbidden or ["не вводить новых персонажей без разрешения", "не обновлять игровой state"],
        "mode": "director_draft_not_live_game",
    }
    manifest = {
        "loaded_files": loaded,
        "missing_files": missing,
        "manually_added": [],
        "optional_blocks_loaded": optional_blocks or [],
        "skipped_by_default": list(SKIPPED_BY_DEFAULT),
    }
    return brief, manifest


@app.post("/api/director/drafts/start", operation_id="startDirectorDraft", include_in_schema=False)
def start_director_draft(body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = _payload(body)
    raw_request = str(
        payload.get("director_request")
        or payload.get("request")
        or payload.get("user_text")
        or payload.get("text")
        or payload.get("message")
        or ""
    ).strip()
    if not raw_request:
        return {
            "success": False,
            "mode": "director_request_required",
            "error": "director_request is empty. User should describe the scene in normal text.",
            "next_action": "waitForDirectorRequest",
        }

    draft_id = _safe_id(payload.get("draft_id") or payload.get("title"), prefix="director")
    brief, manifest = _build_context_for_request(
        raw_request,
        explicit_characters=payload.get("characters") or payload.get("character_ids"),
        explicit_files=payload.get("files_to_read") or payload.get("file_paths"),
    )
    draft = {
        "draft_id": draft_id,
        "created_at": _now(),
        "updated_at": _now(),
        "runtime_version": DIRECTOR_RUNTIME_VERSION,
        "brief": brief,
        "context_manifest": manifest,
        "versions": [],
        "revision_notes": [],
    }
    _save_draft(draft)
    return {
        "success": True,
        "mode": "director_draft_started",
        "draft_id": draft_id,
        "runtime_version": DIRECTOR_RUNTIME_VERSION,
        "writer_packet": _build_writer_packet(draft),
        "loaded_file_paths": [entry.get("path") for entry in manifest.get("loaded_files", [])],
        "missing_or_suggested_files": manifest.get("missing_files", []),
        "skipped_by_default": list(SKIPPED_BY_DEFAULT),
        "next_action": "writeSceneDraftInChat",
    }


@app.post("/api/director/drafts/{draft_id}/context/add", operation_id="addDirectorContext", include_in_schema=False)
def add_director_context(draft_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = _payload(body)
    addition = str(payload.get("addition") or payload.get("request") or payload.get("text") or payload.get("message") or "").strip()
    draft = _load_draft(draft_id)
    if not addition:
        return {
            "success": False,
            "draft_id": draft.get("draft_id"),
            "mode": "addition_required",
            "error": "addition is empty. User should describe what extra context to add in normal text.",
        }

    current_chars = list((draft.get("brief") or {}).get("character_ids") or [])
    added_chars = _infer_characters(addition)
    for cid in added_chars:
        if cid not in current_chars:
            current_chars.append(cid)

    blocks = _detect_optional_blocks(addition)
    explicit_paths = _extract_explicit_paths(addition, payload.get("files_to_read") or payload.get("file_paths"))
    paths = _candidate_character_files(current_chars, include_optional=blocks)
    _append_unique(paths, explicit_paths)

    loaded, missing = _load_files(paths)
    manifest = draft.setdefault("context_manifest", {})
    old_loaded = manifest.get("loaded_files") if isinstance(manifest.get("loaded_files"), list) else []
    old_paths = {entry.get("path") for entry in old_loaded if isinstance(entry, dict)}
    merged_loaded = list(old_loaded)
    for entry in loaded:
        if entry.get("path") not in old_paths:
            merged_loaded.append(entry)
            old_paths.add(entry.get("path"))

    manifest["loaded_files"] = merged_loaded
    manifest["missing_files"] = list(dict.fromkeys((manifest.get("missing_files") or []) + missing))
    manifest["manually_added"] = list(dict.fromkeys((manifest.get("manually_added") or []) + [addition]))
    manifest["optional_blocks_loaded"] = list(dict.fromkeys((manifest.get("optional_blocks_loaded") or []) + blocks))

    draft.setdefault("brief", {})["character_ids"] = current_chars
    draft.setdefault("revision_notes", []).append({"at": _now(), "type": "context_add", "text": addition})
    _save_draft(draft)
    return {
        "success": True,
        "mode": "director_context_added",
        "draft_id": draft.get("draft_id"),
        "added_optional_blocks": blocks,
        "loaded_file_paths": [entry.get("path") for entry in merged_loaded],
        "missing_or_suggested_files": manifest.get("missing_files", []),
        "writer_packet": _build_writer_packet(draft),
        "next_action": "writeOrRewriteSceneDraftInChat",
    }


@app.post("/api/director/drafts/{draft_id}/rewrite", operation_id="rewriteDirectorDraft", include_in_schema=False)
def rewrite_director_draft(draft_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = _payload(body)
    draft = _load_draft(draft_id)
    revision = str(payload.get("revision_request") or payload.get("request") or payload.get("text") or payload.get("message") or "").strip()
    scene_text = str(payload.get("scene_text") or payload.get("draft_text") or "").strip()
    if scene_text:
        _append_version(draft, scene_text, source="rewrite_input")
    if revision:
        draft.setdefault("revision_notes", []).append({"at": _now(), "type": "rewrite", "text": revision})
    _save_draft(draft)
    return {
        "success": True,
        "mode": "director_rewrite_packet_ready",
        "draft_id": draft.get("draft_id"),
        "revision_request": revision,
        "writer_packet": _build_writer_packet(draft),
        "next_action": "rewriteSceneDraftInChat",
    }


def _append_version(draft: dict[str, Any], scene_text: str, *, source: str = "user") -> dict[str, Any]:
    versions = draft.setdefault("versions", [])
    idx = len(versions) + 1
    version = {
        "version_id": f"v{idx:03d}",
        "created_at": _now(),
        "source": source,
        "scene_text": scene_text,
    }
    versions.append(version)
    return version


@app.post("/api/director/drafts/{draft_id}/save", operation_id="saveDirectorDraft", include_in_schema=False)
def save_director_draft(draft_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = _payload(body)
    draft = _load_draft(draft_id)
    scene_text = str(payload.get("scene_text") or payload.get("draft_text") or payload.get("text") or "").strip()
    note = str(payload.get("note") or "").strip()
    if not scene_text:
        return {
            "success": False,
            "draft_id": draft.get("draft_id"),
            "mode": "scene_text_required",
            "error": "scene_text is empty; nothing to save.",
        }
    version = _append_version(draft, scene_text, source="save")
    if note:
        draft.setdefault("revision_notes", []).append({"at": _now(), "type": "save_note", "text": note})
    _save_draft(draft)
    # Also keep a human-readable version file in DATA_DIR for easier Railway inspection.
    base.write_text(f"drafts/director/{draft.get('draft_id')}/versions/{version['version_id']}.md", scene_text)
    return {
        "success": True,
        "mode": "director_draft_saved",
        "draft_id": draft.get("draft_id"),
        "version_id": version["version_id"],
        "saved_path": f"drafts/director/{draft.get('draft_id')}/versions/{version['version_id']}.md",
        "next_action": "continueEditingOrExportLater",
    }


@app.post("/api/director/drafts/{draft_id}/validate", operation_id="validateDirectorDraft", include_in_schema=False)
def validate_director_draft(draft_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = _payload(body)
    draft = _load_draft(draft_id)
    versions = draft.get("versions") if isinstance(draft.get("versions"), list) else []
    scene_text = str(payload.get("scene_text") or payload.get("draft_text") or "").strip()
    if not scene_text and versions and isinstance(versions[-1], dict):
        scene_text = str(versions[-1].get("scene_text") or "")
    if not scene_text:
        return {
            "success": False,
            "draft_id": draft.get("draft_id"),
            "mode": "scene_text_required",
            "error": "scene_text is empty and no saved version exists.",
        }

    brief = draft.get("brief", {}) if isinstance(draft.get("brief"), dict) else {}
    forbidden = list(brief.get("forbidden") or []) + list(DEFAULT_FORBIDDEN_TERMS)
    violations: list[dict[str, Any]] = []
    lower_scene = _normalize(scene_text)

    for term in forbidden:
        term_text = str(term).strip()
        if not term_text:
            continue
        # Long descriptive locks are not literal text checks.
        if len(term_text) > 35 and " " in term_text:
            continue
        if _normalize(term_text) in lower_scene:
            violations.append({
                "type": "forbidden_term",
                "term": term_text,
                "problem": f"В сцене встречается запрещённый/чувствительный термин: {term_text}",
                "repair_hint": "Убрать прямое упоминание или оставить только если пользователь явно разрешил в этой сцене.",
            })

    if re.search(r"после нас\s+придут|тебе\s+опасно|я\s+предупреждаю", lower_scene):
        violations.append({
            "type": "helper_warning_tone",
            "problem": "Фраза звучит как предупреждение/забота, а не давление.",
            "repair_hint": "Переделать в давление на задачу/на другого NPC, без заботливого предупреждения Акиры.",
        })

    allowed_ids = brief.get("character_ids") or []
    if allowed_ids:
        allowed_names = set(allowed_ids)
        for alias, cid in CHARACTER_ALIASES.items():
            if cid in allowed_names:
                continue
            # Проверяем только явные имена с заглавной буквы/русские формы; это мягкая эвристика.
            display = alias.strip()
            if not display or len(display) < 3:
                continue
            if _contains_alias(scene_text, display):
                violations.append({
                    "type": "possibly_unapproved_character",
                    "character_id": cid,
                    "matched_alias": display,
                    "problem": "В сцене, возможно, появился персонаж вне списка разрешённых.",
                    "repair_hint": "Убрать персонажа или явно добавить его через addDirectorContext/новый режиссёрский запрос.",
                })

    return {
        "success": True,
        "mode": "director_validation_result",
        "draft_id": draft.get("draft_id"),
        "valid": not violations,
        "violations": violations,
        "checked_rules": [
            "forbidden terms",
            "helper-warning tone patterns",
            "possible unapproved character aliases",
        ],
        "next_action": "repairSceneDraftInChat" if violations else "draftLooksSafeForThisDirectorBrief",
    }


@app.get("/api/director/drafts/{draft_id}", operation_id="getDirectorDraft", include_in_schema=False)
def get_director_draft(draft_id: str) -> dict[str, Any]:
    draft = _load_draft(draft_id)
    manifest = draft.get("context_manifest", {}) if isinstance(draft.get("context_manifest"), dict) else {}
    return {
        "success": True,
        "mode": "director_draft_snapshot",
        "draft": draft,
        "loaded_file_paths": [entry.get("path") for entry in manifest.get("loaded_files", []) if isinstance(entry, dict)],
        "writer_packet": _build_writer_packet(draft),
    }
