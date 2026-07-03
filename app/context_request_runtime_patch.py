"""Action-safe preflight/context-request pipeline for Akira 1206 v3.

The full cards stay on Railway. Custom GPT receives small semantic slices only.
This module also replaces the old huge /scene-contract responses with a safe
pointer so Actions stop failing with ResponseTooLargeError.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from fastapi import Body

from app import compact as base

app = base.app
RUNTIME_VERSION = base.APP_VERSION

CURRENT_STATE_FILE = "state/current_state.json"
SCENE_HISTORY_FILE = "state/scene_history.json"
CALENDAR_RUNTIME_FILE = "state/calendar_runtime.json"
START_SCENE_PATH = "scenes/start_scene.md"

ID_ALIASES = {
    "акира": "akira", "akira": "akira", "кира": "akira",
    "алекс": "alex", "alex": "alex",
    "эмма": "emma", "emma": "emma",
    "ирэй": "irey", "ирей": "irey", "irey": "irey",
    "джун": "jun", "jun": "jun", "jun_carter": "jun",
    "кай": "kai", "kai": "kai",
    "мики": "miki", "miki": "miki",
    "рейден": "raiden", "рейдон": "raiden", "raiden": "raiden", "sterling": "raiden", "стерлинг": "raiden",
    "рэй": "ray", "рей": "ray", "ray": "ray", "ray_carter": "ray",
    "хару": "haru", "haru": "haru", "haru_foster": "haru",
    "широ": "shiro", "shiro": "shiro",
    "юна": "yuna", "yuna": "yuna",
}


def _remove_route(path: str, method: str | None = None) -> None:
    method_upper = method.upper() if method else None
    for route in list(app.router.routes):
        if getattr(route, "path", None) != path:
            continue
        methods = set(getattr(route, "methods", set()) or set())
        if method_upper is None or method_upper in methods:
            app.router.routes.remove(route)


def _sid(session_id: str | None) -> str:
    return base.safe_session_id(session_id or "default")


def _payload(body: dict[str, Any] | None) -> dict[str, Any]:
    return body if isinstance(body, dict) else {}


def _trim(value: Any, max_chars: int = 600) -> str:
    text = str(value or "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _compact(value: Any, *, max_chars: int = 700, max_items: int = 6, depth: int = 2) -> Any:
    if depth <= 0:
        return _trim(value, max_chars)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        preferred = [
            "id", "name", "role", "status", "current_date", "current_day_phase", "current_location_id",
            "pov_character_id", "active_character_ids", "scene_character_ids", "relationship_pair_ids",
            "summary", "current_beat_id", "pending_events", "rules", "last_player_input", "scene_goal",
        ]
        keys = [k for k in preferred if k in value] + [k for k in value.keys() if k not in preferred]
        for key in keys[:max_items]:
            result[str(key)] = _compact(value[key], max_chars=max_chars, max_items=max_items, depth=depth - 1)
        return result
    if isinstance(value, list):
        return [_compact(item, max_chars=max_chars, max_items=max_items, depth=depth - 1) for item in value[:max_items]]
    if isinstance(value, str):
        return _trim(value, max_chars)
    return value


def _read_json(path: str, sid: str, default: Any) -> Any:
    value = base.read_json(path, session_id=sid, default=default)
    return default if value is None else value


def _read_text(path: str, sid: str | None = None) -> str:
    return base.read_text(path, session_id=sid, default="") or ""


def _ensure_current(sid: str) -> dict[str, Any]:
    base.ensure_session(sid)
    current = _read_json(CURRENT_STATE_FILE, sid, {})
    if not isinstance(current, dict) or not current:
        current = base.initialize_start_session(sid, {"last_player_input": "начнем"})
    return current


def _current_state_slice(current: dict[str, Any]) -> dict[str, Any]:
    return {
        "current_scene_id": current.get("current_scene_id") or current.get("scene_id"),
        "current_date": current.get("current_date") or current.get("date"),
        "current_day_phase": current.get("current_day_phase") or current.get("time_of_day"),
        "current_location_id": current.get("current_location_id") or current.get("location_id"),
        "current_location_text": current.get("current_location_text") or current.get("location_text"),
        "pov_character_id": current.get("pov_character_id"),
        "active_character_ids": current.get("active_character_ids", []),
        "scene_character_ids": current.get("scene_character_ids", []),
        "conditional_character_ids": current.get("conditional_character_ids", []),
        "relationship_pair_ids": current.get("relationship_pair_ids", []),
        "visible_inventory": _compact(current.get("visible_inventory", []), max_chars=400, max_items=8, depth=2),
        "nearby_items": _compact(current.get("nearby_items", []), max_chars=400, max_items=8, depth=2),
        "scene_goal": _trim(current.get("scene_goal") or current.get("current_scene_goal"), 450),
        "last_player_input": _trim(current.get("last_player_input"), 320),
        "start_scene_exact_text_required": bool(current.get("start_scene_exact_text_required")),
        "start_scene_completed": bool(current.get("start_scene_completed")),
    }


def _calendar_slice(sid: str, current: dict[str, Any]) -> dict[str, Any]:
    runtime = _read_json(CALENDAR_RUNTIME_FILE, sid, {})
    if not isinstance(runtime, dict):
        runtime = {}
    return {
        "current_date": current.get("current_date") or runtime.get("current_date"),
        "current_day_phase": current.get("current_day_phase") or runtime.get("current_day_phase"),
        "current_beat_id": runtime.get("current_beat_id") or current.get("current_beat_id"),
        "pending_events": _compact(runtime.get("pending_events", []), max_chars=500, max_items=6, depth=2),
        "rules": _compact(runtime.get("rules", []), max_chars=500, max_items=4, depth=2),
        "detail_level": "current_beat_only",
    }


def _history_slice(sid: str) -> list[dict[str, Any]]:
    history = _read_json(SCENE_HISTORY_FILE, sid, [])
    if isinstance(history, dict):
        history = history.get("entries", [])
    if not isinstance(history, list):
        return []
    result = []
    for item in history[-3:]:
        if isinstance(item, dict):
            result.append({
                "scene_id": item.get("scene_id") or item.get("id"),
                "summary": _trim(item.get("summary") or item.get("visible_scene_text") or item.get("scene_text"), 450),
                "changed_files_snapshot": _compact(item.get("changed_files_snapshot", []), max_chars=300, max_items=4, depth=2),
            })
    return result


def _schedule_status() -> dict[str, Any]:
    files = [
        "schedule/base_weekly_schedule.yaml",
        "schedule/base_daily_routine.yaml",
        "schedule/squad_weekly_raids.yaml",
        "schedule/lessons_and_training_schedule.yaml",
        "schedule/staff_shifts.yaml",
        "schedule/location_activity_windows.yaml",
    ]
    empty = True
    present = []
    for path in files:
        text = _read_text(path)
        if text.strip():
            present.append(path)
        if "placeholder_empty" not in text and re.search(r"\n\s*[-\w]+:\s*(?!\[\]|placeholder_empty)", text):
            empty = False
    return {
        "status": "placeholder_empty" if empty else "configured_partial",
        "source_files": present,
        "rule": "If schedule files are placeholder_empty, do not invent raids, lessons, staff shifts or exact NPC locations.",
    }


def _location_slice(current: dict[str, Any], scene_plan: dict[str, Any]) -> dict[str, Any]:
    location_id = scene_plan.get("location_id") or scene_plan.get("target_location_id") or current.get("current_location_id") or current.get("location_id")
    text = current.get("current_location_text") or current.get("location_text") or str(location_id or "")
    lid = str(location_id or "").lower()
    if "room" in lid or "комнат" in text.lower():
        privacy = "private"
        default_activity = "quiet/private; background NPCs are not expected without a scene source"
    elif "cafeteria" in lid or "столов" in text.lower():
        privacy = "public"
        default_activity = "food service, background conversations, staff and passing raiders if schedule allows"
    elif "corridor" in lid or "корид" in text.lower():
        privacy = "semi_public_transition"
        default_activity = "movement, overheard voices, possible passersby depending on time"
    elif "training" in lid or "зал" in text.lower() or "корт" in text.lower():
        privacy = "functional_public"
        default_activity = "training only if schedule/location state allows"
    else:
        privacy = "unknown_or_custom"
        default_activity = "use current_state and explicit scene source only"
    return {
        "location_id": location_id,
        "location_text": text,
        "privacy_level": privacy,
        "detail_level": scene_plan.get("location_detail") or scene_plan.get("needs", {}).get("location", "basic"),
        "default_activity": default_activity,
        "rules": [
            "Load visual details when POV enters, observes, or the location changes.",
            "Load location rules only if conflict/noise/security/access matters.",
            "Load location lore only if explicitly asked or scene-critical.",
        ],
    }


def _canonical_id(value: Any) -> str:
    raw = str(value or "").strip()
    key = raw.lower().replace("ё", "е")
    return ID_ALIASES.get(key, key)


def _collect_requested_character_ids(payload: dict[str, Any], current: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    def add(v: Any) -> None:
        if isinstance(v, str):
            cid = _canonical_id(v)
            if cid and cid not in ids:
                ids.append(cid)
        elif isinstance(v, list):
            for x in v:
                add(x)
        elif isinstance(v, dict):
            for k, item in v.items():
                if isinstance(item, dict):
                    add(item.get("id") or item.get("character_id") or k)
                else:
                    add(k)
    for key in ["character_requests", "characters", "requested_characters", "character_ids"]:
        add(payload.get(key))
    scene_plan = payload.get("scene_plan") if isinstance(payload.get("scene_plan"), dict) else {}
    for key in ["character_requests", "characters", "requested_characters", "character_ids"]:
        add(scene_plan.get(key))
    # For exact first scene, only return identities for explicitly selected scene chars, not full behavior.
    if not ids and current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"):
        for cid in current.get("scene_character_ids", [])[:4]:
            add(cid)
    return ids[:8]


def _grep_lines(text: str, patterns: list[str], *, max_lines: int = 10, max_chars: int = 900) -> str:
    lines = []
    for line in text.splitlines():
        low = line.lower()
        if any(p in low for p in patterns):
            clean = line.strip()
            if clean and clean not in lines:
                lines.append(clean)
        if len(lines) >= max_lines:
            break
    return _trim("\n".join(lines), max_chars)


def _character_slice(sid: str, cid: str, request: Any, current: dict[str, Any]) -> dict[str, Any]:
    if not cid:
        return {}
    main_path = f"characters/{cid}/main.yaml"
    char_path = f"characters/{cid}/character.yaml"
    know_path = f"characters/{cid}/knowledge.yaml"
    mem_path = f"state/character_memory/{cid}.json"
    main = _read_text(main_path, sid)
    char = _read_text(char_path, sid)
    memory = _read_json(mem_path, sid, {})
    req = request if isinstance(request, dict) else {}
    presence = req.get("presence_level") or req.get("level") or "background_visible"
    needs = req.get("needs") if isinstance(req.get("needs"), dict) else {}
    # Start scene exact text means the writer should use exact text, not character depth.
    if current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"):
        presence = "start_scene_reference_only"
    result: dict[str, Any] = {
        "id": cid,
        "presence_level": presence,
        "source_files": {"main": main_path, "character": char_path, "knowledge": know_path, "memory": mem_path},
        "identity_basic": _grep_lines(main, ["name", "имя", "age", "возраст", "role", "роль", "height", "рост"], max_lines=10, max_chars=900),
        "appearance_basic": _grep_lines(main, ["appearance", "внеш", "hair", "волос", "eyes", "глаз", "body", "телослож", "кожа", "одеж"], max_lines=8, max_chars=800),
    }
    if presence in {"speaking", "emotionally_relevant", "combat_or_energy"} or needs.get("voice") or needs.get("behavior"):
        result["voice_and_behavior"] = _grep_lines(char, ["voice", "speech", "style", "характер", "поведен", "голос", "речь", "habit"], max_lines=14, max_chars=1400)
    if presence in {"speaking", "emotionally_relevant"} or needs.get("knowledge"):
        knowledge = _read_text(know_path, sid)
        result["knowledge_relevant_guard"] = _grep_lines(knowledge, ["known", "unknown", "зна", "не зна", "must", "forbid", "нельзя"], max_lines=12, max_chars=1300)
    if presence in {"emotionally_relevant"} or needs.get("memory"):
        result["memory_relevant"] = _compact(memory, max_chars=1200, max_items=8, depth=2)
    if presence in {"combat_or_energy"} or needs.get("energy") or needs.get("combat"):
        result["energy_or_combat_hint"] = _grep_lines(char, ["energy", "энерг", "combat", "бой", "overload", "перегруз", "weak", "слаб"], max_lines=14, max_chars=1400)
    result["blocked_by_default"] = {
        "past": "not included unless explicit past trigger and API approval",
        "full_yaml": "not returned to Actions; use block slices only",
    }
    return result


def _relationship_slices(sid: str, payload: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    scene_plan = payload.get("scene_plan") if isinstance(payload.get("scene_plan"), dict) else {}
    rel_need = scene_plan.get("relationships") or scene_plan.get("needs", {}).get("relationships") or payload.get("relationships")
    if rel_need in [False, "false", "none", "no", None]:
        return {}
    pair_ids = payload.get("relationship_pair_ids") or scene_plan.get("relationship_pair_ids") or []
    if not pair_ids and rel_need in ["surface_only", "only_if_contact"]:
        pair_ids = current.get("relationship_pair_ids", [])[:3]
    result: dict[str, Any] = {}
    for pair in pair_ids[:4]:
        pid = str(pair or "").strip()
        if "__" not in pid:
            continue
        path = f"state/relationship_pairs/{pid}.json"
        data = _read_json(path, sid, {})
        if isinstance(data, dict) and data:
            result[pid] = {"source_file": path, "surface_slice": _compact(data, max_chars=1000, max_items=8, depth=2)}
    return result


def _extract_start_scene_text() -> str:
    text = _read_text(START_SCENE_PATH)
    m = re.search(r"```text\s*(.*?)```", text, flags=re.S)
    if m:
        return m.group(1).strip()
    return text.strip()


def _context_rules() -> dict[str, Any]:
    return {
        "state": "always include active current_state slice",
        "location": "basic always; visual/rules/lore only if requested by scene_plan",
        "schedule": "check schedule files; if placeholder_empty, do not invent availability",
        "characters": "return block slices by presence level, never full YAML dumps",
        "relationships": "surface only unless direct interaction/emotional conflict",
        "knowledge": "only for speaking/decision/secret/reaction scenes",
        "energy": "only combat, training, overload, lecture, or explicit energy trigger",
        "past": "only hard past trigger; never because of ordinary start words",
    }


@app.get("/api/v3/sessions/{session_id}/preflight", operation_id="getPreflight")
def get_preflight(session_id: str) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_preflight_light",
        "current_state": _current_state_slice(current),
        "calendar": _calendar_slice(sid, current),
        "recent_scene_history": _history_slice(sid),
        "schedule_status": _schedule_status(),
        "next_action": "requestContextSlice",
        "planner_checklist": [
            "determine scene_type before requesting context blocks",
            "request location depth, schedule check, visible/background characters, and only necessary character blocks",
            "do not request energy/knowledge/past/lore unless the player input or scene type needs it",
        ],
    }


@app.post("/api/v3/sessions/{session_id}/context-request", operation_id="requestContextSlice")
def request_context_slice(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    payload = _payload(body)
    scene_plan = payload.get("scene_plan") if isinstance(payload.get("scene_plan"), dict) else {}
    player_input = _trim(payload.get("player_input") or scene_plan.get("player_input") or current.get("last_player_input"), 400)
    char_requests = payload.get("character_requests") or scene_plan.get("character_requests") or scene_plan.get("characters") or payload.get("characters") or {}
    if not isinstance(char_requests, dict):
        char_requests = {}
    cids = _collect_requested_character_ids(payload, current)
    character_slices = {}
    for cid in cids:
        character_slices[cid] = _character_slice(sid, cid, char_requests.get(cid, {}), current)
    start_scene_available = bool(current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"))
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_context_slice_light",
        "player_input": player_input,
        "scene_plan_echo": _compact(scene_plan, max_chars=900, max_items=8, depth=3),
        "context_slice": {
            "current_state": _current_state_slice(current),
            "calendar": _calendar_slice(sid, current),
            "location": _location_slice(current, scene_plan),
            "schedule": _schedule_status(),
            "characters": character_slices,
            "relationships": _relationship_slices(sid, payload, current),
            "ambient_npcs": {
                "status": "placeholder_empty",
                "rule": "Ambient NPCs exist only after npcs/ambient is populated or current_state explicitly creates them.",
            },
            "start_scene": {
                "exact_text_required": start_scene_available,
                "text_endpoint": f"/api/v3/sessions/{sid}/start-scene-text" if start_scene_available else None,
                "rule": "If exact_text_required=true, call getStartSceneText and output that text as the first scene before normal play.",
            },
            "blocked_blocks": {
                "past": "blocked unless hard past trigger is present and requested",
                "energy": "blocked unless combat/training/overload/energy lesson/application is present",
                "deep_lore": "blocked unless explicit question, explanation, or story-critical trigger",
                "full_character_yaml": "blocked for Actions; use semantic slices only",
            },
            "rules": _context_rules(),
        },
        "next_action": "writeScene" if not start_scene_available else "getStartSceneText",
    }


@app.post("/api/v3/sessions/{session_id}/context-more", operation_id="requestMoreContext")
def request_more_context(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = _payload(body)
    # Same endpoint shape for now: the caller asks for one extra block/character.
    return request_context_slice(session_id, payload)


@app.get("/api/v3/sessions/{session_id}/start-scene-text", operation_id="getStartSceneText")
def get_start_scene_text(session_id: str) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    required = bool(current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"))
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_start_scene_text",
        "exact_text_required": required,
        "exact_text": _extract_start_scene_text() if required else "",
        "after_output_instruction": "After outputting exact_text, call applyTurnResult with start_scene_completed=true if the user continues.",
    }


# Replace old huge v2 contract routes with action-safe pointers. Keep paths for
# backwards compatibility, but do not return full character YAML.
_remove_route("/api/v2/sessions/{session_id}/scene-contract", "GET")
_remove_route("/api/v2/sessions/{session_id}/turn-packet", "GET")
_remove_route("/api/v2/sessions/{session_id}/debug/context-audit", "GET")


@app.get("/api/v2/sessions/{session_id}/scene-contract", operation_id="getSceneContract")
def get_scene_contract_action_safe(session_id: str) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "deprecated_scene_contract_action_safe_pointer",
        "current_state": _current_state_slice(current),
        "message": "Full scene_contract is disabled for Actions to avoid ResponseTooLargeError. Use getPreflight then requestContextSlice.",
        "next_action": "getPreflight",
    }


@app.get("/api/v2/sessions/{session_id}/turn-packet", operation_id="getTurnPacket")
def get_turn_packet_action_safe(session_id: str) -> dict[str, Any]:
    return get_scene_contract_action_safe(session_id)


@app.get("/api/v2/sessions/{session_id}/debug/context-audit", operation_id="getContextAudit")
def get_context_audit_action_safe(session_id: str) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_action_safe_context_audit",
        "current_state": _current_state_slice(current),
        "short_character_summaries_used": False,
        "legacy_character_memory_path_used": False,
        "full_scene_contract_actions_disabled": True,
        "preflight_endpoint": f"/api/v3/sessions/{sid}/preflight",
        "context_request_endpoint": f"/api/v3/sessions/{sid}/context-request",
        "schedule_status": _schedule_status(),
    }


try:
    app.version = RUNTIME_VERSION
except Exception:
    pass
