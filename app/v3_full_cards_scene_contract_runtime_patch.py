"""V3 full-card scene contract override for Akira 1206.

This module intentionally replaces the old compact/runtime-summary contract.
It reads characters from the v3 content structure:

    characters/<id>/main.yaml
    characters/<id>/character.yaml
    characters/<id>/knowledge.yaml
    state/character_memory/<id>.json

It never uses runtime/characters and never falls back to Akira just because a
scene has no selected ids. Hidden past is included only when a scene trigger is
present.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from fastapi import Query

from app import compact as base

app = base.app
RUNTIME_VERSION = "0.3.180-v3-full-cards-builder-v1"

CURRENT_STATE_FILE = "state/current_state.json"
SCENE_HISTORY_FILE = "state/scene_history.json"
CALENDAR_RUNTIME_FILE = "state/calendar_runtime.json"

# Content folders that must be copied from the repository into DATA on Railway.
for _name in ["api_contracts", "calendar", "canon_lore", "characters", "gpt", "state"]:
    try:
        if _name not in base.SYNC_FROM_REPO:
            base.SYNC_FROM_REPO.append(_name)
    except Exception:
        pass

ID_ALIASES = {
    "акира": "akira", "akira": "akira", "кира": "akira",
    "алекс": "alex", "alex": "alex",
    "эмма": "emma", "emma": "emma",
    "ирэй": "irey", "ирей": "irey", "irey": "irey",
    "джун": "jun", "jun": "jun", "jun_carter": "jun",
    "кай": "kai", "kai": "kai",
    "мики": "miki", "miki": "miki",
    "рейден": "raiden", "рейдон": "raiden", "raiden": "raiden", "raiden_sterling": "raiden", "стерлинг": "raiden",
    "рэй": "ray", "рей": "ray", "ray": "ray", "ray_carter": "ray",
    "хару": "haru", "haru": "haru", "haru_foster": "haru",
    "широ": "shiro", "shiro": "shiro",
    "юна": "yuna", "yuna": "yuna",
}

CHARACTER_FIELDS = (
    "pov_character_id", "pov", "point_of_view",
    "active_character_ids", "active_characters",
    "present_character_ids", "present_characters",
    "nearby_character_ids", "nearby_characters",
    "speaking_character_ids", "speaking_characters",
    "addressed_character_ids", "addressed_characters",
    "looked_at_character_ids", "looked_at_characters",
    "observing_character_ids", "observing_characters",
    "scene_character_ids", "characters", "participants",
    "pending_character_ids", "scene_goal_character_ids", "relationship_focus_character_ids",
)

PAST_TRIGGER_WORDS = (
    "прошл", "вспом", "памят", "флэшбек", "флешбек", "академ", "1198", "1170",
    "беремен", "ребен", "ребён", "потер", "плен", "самуэль", "samuel", "браслет",
    "джун", "дом джуна", "записка", "детств", "мать", "отец", "пожар",
)

ENERGY_WORDS = (
    "energy", "энерг", "поток", "простран", "вода", "влага", "давлен", "холод",
    "воздух", "огонь", "тепло", "свет", "барьер", "гравита", "подавлен", "электр",
)


def _remove_route(path: str, method: str | None = None) -> None:
    method_upper = method.upper() if method else None
    for route in list(app.router.routes):
        if getattr(route, "path", None) != path:
            continue
        methods = set(getattr(route, "methods", set()) or set())
        if method_upper is None or method_upper in methods:
            app.router.routes.remove(route)


def _safe_session_id(session_id: str) -> str:
    try:
        return base.safe_session_id(session_id)
    except Exception:
        cleaned = "".join(ch for ch in str(session_id or "") if ch.isalnum() or ch in "-_")
        return cleaned or "default"


def _trim(value: Any, limit: int = 2400) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 18)].rstrip() + "\n...[truncated]"


def _compact(value: Any, *, max_chars: int = 1600, max_items: int = 10, depth: int = 3) -> Any:
    if depth <= 0:
        if isinstance(value, str):
            return _trim(value, max_chars)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return _trim(json.dumps(value, ensure_ascii=False, separators=(",", ":")), max_chars)
    if isinstance(value, str):
        return _trim(value, max_chars)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_compact(item, max_chars=max_chars, max_items=max_items, depth=depth - 1) for item in value[:max_items]]
    if isinstance(value, dict):
        preferred = [
            "schema", "id", "character_id", "display_name", "current", "status", "summary",
            "visible_state", "internal_state", "knows", "does_not_know", "wrong_beliefs",
            "observed", "heard", "events_witnessed", "conclusions", "hides_from",
            "last_scene_notes", "memory", "important", "rules",
        ]
        result: dict[str, Any] = {}
        keys = [key for key in preferred if key in value]
        keys += [key for key in value.keys() if key not in keys]
        for key in keys[:max_items]:
            result[str(key)] = _compact(value[key], max_chars=max_chars, max_items=max_items, depth=depth - 1)
        return result
    return _trim(str(value), max_chars)


def _read_text(path: str, session_id: str | None = None) -> str:
    if session_id:
        try:
            text = base.read_text(path, session_id=session_id)
            if isinstance(text, str) and text.strip():
                return text
        except Exception:
            pass
    try:
        text = base.read_text(path)
        return text if isinstance(text, str) else ""
    except Exception:
        return ""


def _read_json(path: str, session_id: str, default: Any) -> Any:
    try:
        value = base.read_json(path, session_id=session_id, default=default)
        return default if value is None else value
    except Exception:
        return default


def _exists(path: str, session_id: str | None = None) -> bool:
    if session_id and _read_text(path, session_id).strip():
        return True
    return bool(_read_text(path).strip())


def _canonical_id(value: Any) -> str:
    raw = str(value or "").strip()
    key = raw.lower().replace("ё", "е")
    return ID_ALIASES.get(key, key)


def _unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        cid = _canonical_id(value)
        if cid and cid not in result and cid not in {"none", "null", "unknown", "off", "false"}:
            result.append(cid)
    return result


def _collect_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _unique([part.strip() for part in re.split(r"[,;|]", value) if part.strip()])
    if isinstance(value, list):
        raw: list[Any] = []
        for item in value:
            if isinstance(item, dict):
                raw.append(item.get("id") or item.get("character_id") or item.get("name") or item.get("slug"))
            else:
                raw.append(item)
        return _unique(raw)
    if isinstance(value, dict):
        raw = []
        for key, item in value.items():
            if isinstance(item, dict):
                raw.append(item.get("id") or item.get("character_id") or key)
            else:
                raw.append(key)
        return _unique(raw)
    return []


def _derive_scene_ids(current: dict[str, Any]) -> tuple[str | None, list[str], list[str], list[str]]:
    raw: list[str] = []
    active_raw: list[str] = []
    reasons: list[str] = []

    pov = _canonical_id(current.get("pov_character_id") or current.get("pov") or current.get("point_of_view") or "") or None
    if pov:
        raw.append(pov)
        active_raw.append(pov)
        reasons.append(f"pov:{pov}")

    for field in CHARACTER_FIELDS:
        ids = _collect_ids(current.get(field))
        if not ids:
            continue
        raw.extend(ids)
        reasons.append(f"{field}:{','.join(ids)}")
        if field.startswith(("active", "present", "speaking", "pov")):
            active_raw.extend(ids)

    # Explicit current scene focus can add characters, but there is no Akira fallback here.
    for key in ("scene_goal", "scene_goal_text", "last_player_input"):
        value = current.get(key)
        if not isinstance(value, str):
            continue
        low = value.lower().replace("ё", "е")
        for alias, cid in ID_ALIASES.items():
            if alias and alias in low and cid not in raw:
                raw.append(cid)
                reasons.append(f"mentioned_in_{key}:{cid}")

    scene_ids = [cid for cid in _unique(raw) if _exists(f"characters/{cid}/main.yaml") or _exists(f"characters/{cid}/character.yaml")]
    active_ids = [cid for cid in _unique(active_raw or scene_ids) if cid in scene_ids]
    if not active_ids and scene_ids:
        active_ids = scene_ids[:]
    return pov, active_ids[:8], scene_ids[:8], reasons


def _past_triggered(current: dict[str, Any], cid: str) -> tuple[bool, list[str]]:
    explicit = _collect_ids(current.get("past_trigger_character_ids") or current.get("past_triggered_character_ids"))
    if cid in explicit:
        return True, ["explicit_past_trigger_character_ids"]
    if bool(current.get("load_past") or current.get("past_triggered")):
        return True, ["current_state_load_past"]
    blob = "\n".join(str(current.get(key) or "") for key in ["scene_goal", "scene_goal_text", "last_player_input", "current_scene_id", "scene_id"])
    low = blob.lower().replace("ё", "е")
    hits = [word for word in PAST_TRIGGER_WORDS if word in low]
    if hits:
        return True, [f"keyword:{hit}" for hit in hits[:5]]
    return False, []


def _character_card(session_id: str, cid: str, current: dict[str, Any]) -> dict[str, Any]:
    main_path = f"characters/{cid}/main.yaml"
    character_path = f"characters/{cid}/character.yaml"
    knowledge_path = f"characters/{cid}/knowledge.yaml"
    past_path = f"characters/{cid}/past.yaml"
    memory_path = f"state/character_memory/{cid}.json"

    main = _read_text(main_path, session_id)
    character = _read_text(character_path, session_id)
    knowledge = _read_text(knowledge_path, session_id)
    memory = _read_json(memory_path, session_id, {})
    past_ok, past_reasons = _past_triggered(current, cid)
    past_text = _read_text(past_path, session_id) if past_ok else ""

    joined = "\n".join([main, character, knowledge, json.dumps(memory, ensure_ascii=False)])
    energy_loaded = any(word in joined.lower().replace("ё", "е") for word in ENERGY_WORDS)

    return {
        "id": cid,
        "source_files": {
            "main": main_path,
            "character": character_path,
            "knowledge": knowledge_path,
            "memory": memory_path,
            "past": past_path if past_ok else None,
        },
        "main_yaml": _trim(main, 2200),
        "character_yaml": _trim(character, 5200),
        "knowledge_yaml": _trim(knowledge, 3000),
        "character_memory": _compact(memory, max_chars=2800, max_items=14, depth=3),
        "past": {
            "loaded": bool(past_ok and past_text.strip()),
            "trigger_reasons": past_reasons,
            "content": _trim(past_text, 2600) if past_ok else "",
            "default_rule": "past.yaml is hidden by default and is included only when triggered.",
        },
        "energy_loaded": bool(energy_loaded),
        "use_rule": "Use these full-card fields as the primary behavior source. Do not replace them with compact summaries.",
    }


def _pair_candidates(scene_ids: list[str], current: dict[str, Any]) -> list[str]:
    ids = list(scene_ids)
    explicit = current.get("relationship_pair_ids") or current.get("relationship_pairs") or current.get("relationship_focus_pairs")
    pairs: list[str] = []
    for pair in _collect_pair_ids(explicit):
        pairs.append(pair)
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            for candidate in (f"{left}__{right}", f"{right}__{left}"):
                if _exists(f"state/relationship_pairs/{candidate}.json"):
                    pairs.append(candidate)
                    break
    return _unique_pair_ids(pairs)[:10]


def _collect_pair_ids(value: Any) -> list[str]:
    raw: list[str] = []
    if isinstance(value, str):
        raw.extend([part.strip() for part in re.split(r"[,;|]", value) if part.strip()])
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                raw.append(item)
            elif isinstance(item, dict):
                pair = item.get("pair_id") or item.get("pair") or item.get("id")
                if isinstance(pair, list) and len(pair) == 2:
                    raw.append(f"{pair[0]}__{pair[1]}")
                elif pair:
                    raw.append(str(pair))
    elif isinstance(value, dict):
        raw.extend(str(key) for key in value.keys())
    return raw


def _unique_pair_ids(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[frozenset[str]] = set()
    for value in values:
        parts = [part for part in str(value or "").split("__") if part]
        if len(parts) != 2:
            continue
        left, right = _canonical_id(parts[0]), _canonical_id(parts[1])
        key = frozenset([left, right])
        pair = f"{left}__{right}"
        if key not in seen:
            result.append(pair)
            seen.add(key)
    return result


def _relationship_slice(session_id: str, scene_ids: list[str], current: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for pair in _pair_candidates(scene_ids, current):
        parts = pair.split("__")
        paths = [f"state/relationship_pairs/{pair}.json"]
        if len(parts) == 2:
            paths.append(f"state/relationship_pairs/{parts[1]}__{parts[0]}.json")
        for path in paths:
            data = _read_json(path, session_id, None)
            if isinstance(data, dict):
                result[pair] = {"source_file": path, "data": _compact(data, max_chars=2200, max_items=12, depth=3)}
                break
    return result


def _calendar_slice(session_id: str, current: dict[str, Any]) -> dict[str, Any]:
    calendar = _read_json(CALENDAR_RUNTIME_FILE, session_id, {})
    if not isinstance(calendar, dict):
        calendar = {}
    return {
        "current_date": calendar.get("current_date") or current.get("current_date") or current.get("date"),
        "current_day_phase": calendar.get("current_day_phase") or current.get("current_day_phase") or current.get("time_of_day"),
        "current_beat_id": calendar.get("current_beat_id") or current.get("current_beat_id"),
        "pending_events": _compact(calendar.get("pending_events", []), max_chars=600, max_items=5, depth=2),
        "rule": "Use only current date/phase/beat unless player explicitly skips time or asks diagnostics.",
    }


def _recent_history(session_id: str) -> list[dict[str, Any]]:
    history = _read_json(SCENE_HISTORY_FILE, session_id, [])
    entries = history if isinstance(history, list) else history.get("entries", []) if isinstance(history, dict) else []
    result: list[dict[str, Any]] = []
    for entry in entries[-3:]:
        if not isinstance(entry, dict):
            continue
        result.append({
            "scene_id": entry.get("id") or entry.get("scene_id"),
            "turn_number": entry.get("turn_number"),
            "player_input": _trim(entry.get("player_input"), 300),
            "visible_scene_text": _trim(entry.get("visible_scene_text") or entry.get("scene_text"), 900),
            "changed_files_snapshot": entry.get("changed_files_snapshot", []),
        })
    return result


def _current_frame(current: dict[str, Any], pov: str | None, active_ids: list[str], scene_ids: list[str], reasons: list[str]) -> dict[str, Any]:
    return {
        "current_scene_id": current.get("current_scene_id") or current.get("scene_id"),
        "current_date": current.get("current_date") or current.get("date"),
        "current_day_phase": current.get("current_day_phase") or current.get("time_of_day"),
        "current_location_id": current.get("current_location_id") or current.get("location_id"),
        "current_location_text": current.get("current_location_text") or current.get("location_text"),
        "pov_character_id": pov,
        "active_character_ids": active_ids,
        "scene_character_ids": scene_ids,
        "selection_reasons": reasons,
        "last_player_input": _trim(current.get("last_player_input"), 420),
        "visible_inventory": _compact(current.get("visible_inventory", []), max_chars=500, max_items=8, depth=2),
        "nearby_items": _compact(current.get("nearby_items", []), max_chars=500, max_items=8, depth=2),
        "scene_goal": _trim(current.get("scene_goal") or current.get("scene_goal_text"), 520),
    }


def build_v3_scene_contract_response(session_id: str, *, max_total_chars: int = 18000, include_debug: bool = False) -> dict[str, Any]:
    sid = _safe_session_id(session_id)
    base.seed()
    base.ensure_session(sid)
    current = _read_json(CURRENT_STATE_FILE, sid, {})
    if not isinstance(current, dict):
        current = {}

    pov, active_ids, scene_ids, selection_reasons = _derive_scene_ids(current)
    character_cards = {cid: _character_card(sid, cid, current) for cid in scene_ids}
    relationship_slice = _relationship_slice(sid, scene_ids, current)

    contract: dict[str, Any] = {
        "version": "scene_contract_1206_v3_full_cards_v1",
        "current_frame": _current_frame(current, pov, active_ids, scene_ids, selection_reasons),
        "calendar_slice": _calendar_slice(sid, current),
        "loaded_characters": character_cards,
        "loaded_relationship_pairs": relationship_slice,
        "recent_scene_history": _recent_history(sid),
        "visible_knowledge_boundaries": {
            "rule": "State/global/card facts are not NPC knowledge by default.",
            "npc_may_state_as_fact_only_from": ["own knowledge.yaml", "state/character_memory/<id>.json", "visible observation", "heard dialogue"],
            "npc_must_not_state_as_fact_from": ["another character card", "hidden/past unless loaded by trigger", "engine state", "author-only lore"],
        },
        "forbidden_context": [
            "No runtime/characters summaries.",
            "No state/character_knowledge path.",
            "No fallback insertion of Akira when she is absent.",
            "No hidden past unless past.loaded=true for that character.",
            "No relationship pair unless loaded_relationship_pairs contains it or scene observation creates it.",
        ],
        "render_rules": [
            "Write from this scene_contract only.",
            "Use loaded_characters.<id>.main_yaml + character_yaml + knowledge_yaml + character_memory as primary behavior source.",
            "Session memory overrides static baseline for events already played.",
            "Do not make important choices for the POV character.",
            "After meaningful scene changes, return proposed_updates; API applies them via applyTurnResult.",
        ],
        "scene_assembly_gate": {
            "status": "ready" if scene_ids else "missing_scene_characters",
            "failure_line": "API-контекст неполный: scene_contract не выбрал персонажей сцены. Сцену продолжить нельзя.",
            "required_character_sources": ["main_yaml", "character_yaml", "knowledge_yaml", "character_memory"],
        },
    }

    response: dict[str, Any] = {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_full_cards_scene_contract",
        "created_at": datetime.utcnow().isoformat(),
        "active_character_ids": active_ids,
        "scene_character_ids": scene_ids,
        "scene_contract": contract,
        "context_audit": {
            "full_cards_builder_enabled": True,
            "runtime_characters_used": False,
            "character_knowledge_legacy_path_used": False,
            "source_files_by_character": {cid: card.get("source_files") for cid, card in character_cards.items()},
            "relationship_pair_files": {pair: data.get("source_file") for pair, data in relationship_slice.items()},
            "contract_chars_estimate": len(json.dumps(contract, ensure_ascii=False)),
        },
    }
    if include_debug:
        response["context_audit"]["selection_reasons"] = selection_reasons
        response["context_audit"]["current_state_keys"] = sorted(current.keys())[:80]

    # Soft cap: keep behavior sources first; compact history and memory only if needed.
    try:
        limit = max(12000, min(int(max_total_chars or 18000), 30000))
    except Exception:
        limit = 18000
    estimate = len(json.dumps(response, ensure_ascii=False))
    if estimate > limit:
        contract["recent_scene_history"] = contract["recent_scene_history"][-1:]
        for card in character_cards.values():
            if isinstance(card, dict):
                card["character_memory"] = _compact(card.get("character_memory"), max_chars=1400, max_items=8, depth=2)
                card["main_yaml"] = _trim(card.get("main_yaml"), 1600)
                card["knowledge_yaml"] = _trim(card.get("knowledge_yaml"), 2200)
                card["character_yaml"] = _trim(card.get("character_yaml"), 4200)
        response["context_audit"]["compacted_after_estimate"] = estimate
        response["context_audit"]["contract_chars_estimate"] = len(json.dumps(contract, ensure_ascii=False))
    return response


_remove_route("/api/v2/sessions/{session_id}/scene-contract", "GET")
_remove_route("/api/v2/sessions/{session_id}/turn-packet", "GET")
_remove_route("/api/v2/sessions/{session_id}/debug/context-audit", "GET")


@app.get("/api/v2/sessions/{session_id}/scene-contract", operation_id="getSceneContract")
def get_scene_contract_v3_full_cards(
    session_id: str,
    max_total_chars: int = Query(default=18000, ge=12000, le=30000),
    include_debug: bool = Query(default=False),
) -> dict[str, Any]:
    return build_v3_scene_contract_response(session_id, max_total_chars=max_total_chars, include_debug=include_debug)


@app.get("/api/v2/sessions/{session_id}/turn-packet", operation_id="getTurnPacket")
def get_turn_packet_v3_full_cards(
    session_id: str,
    max_total_chars: int = Query(default=18000, ge=12000, le=30000),
    include_debug: bool = Query(default=False),
) -> dict[str, Any]:
    response = build_v3_scene_contract_response(session_id, max_total_chars=max_total_chars, include_debug=include_debug)
    response["mode"] = "turn_packet_compat_returns_v3_full_cards_scene_contract"
    return response


@app.get("/api/v2/sessions/{session_id}/debug/context-audit", operation_id="getContextAudit")
def get_context_audit_v3_full_cards(
    session_id: str,
    max_total_chars: int = Query(default=22000, ge=12000, le=30000),
) -> dict[str, Any]:
    response = build_v3_scene_contract_response(session_id, max_total_chars=max_total_chars, include_debug=True)
    audit = response.get("context_audit", {}) if isinstance(response.get("context_audit"), dict) else {}
    return {
        "success": True,
        "session_id": response.get("session_id"),
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_full_cards_context_audit",
        "active_character_ids": response.get("active_character_ids"),
        "scene_character_ids": response.get("scene_character_ids"),
        "runtime_characters_used": False,
        "character_knowledge_legacy_path_used": False,
        "source_files_by_character": audit.get("source_files_by_character", {}),
        "relationship_pair_files": audit.get("relationship_pair_files", {}),
        "contract_chars_estimate": audit.get("contract_chars_estimate"),
        "instructions": [
            "Read-only audit. Do not continue scene from this endpoint.",
            "Characters must come from full v3 cards, not runtime summaries.",
            "If scene_character_ids is empty, fix current_state scene participants before gameplay.",
        ],
    }


try:
    app.version = RUNTIME_VERSION
except Exception:
    pass
