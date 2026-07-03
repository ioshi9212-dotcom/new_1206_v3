"""Action-safe character context pipeline for Akira 1206 v3.

Single replacement patch. No extra runtime layers.

Principle:
- POV character always gets a compact writer card.
- Active/speaking NPCs get compact role/goal/voice/knowledge/unknowns/reaction cards.
- Response stays small enough for Custom GPT Actions by returning targeted lines, not full YAML sections.
"""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import Body

from app import compact as base

app = base.app
RUNTIME_VERSION = base.APP_VERSION

CURRENT_STATE_FILE = "state/current_state.json"
SCENE_HISTORY_FILE = "state/scene_history.json"
CALENDAR_RUNTIME_FILE = "state/calendar_runtime.json"
STORY_LINES_FILE = "state/story_lines.json"
START_SCENE_PATH = "scenes/start_scene.md"
RENDER_CONTRACT_PATH = "gpt/scene_output_contract_1206.json"

ID_ALIASES = {
    "акира": "akira", "akira": "akira", "кира": "akira",
    "джун": "jun", "jun": "jun", "jun_carter": "jun",
    "эмма": "emma", "emma": "emma", "беловолосая девушка": "emma",
    "ирэй": "irey", "ирей": "irey", "irey": "irey", "беловолосый парень": "irey",
    "рэй": "ray", "рей": "ray", "ray": "ray", "ray_carter": "ray", "мужчина в форме": "ray",
    "рейден": "raiden", "рейдон": "raiden", "raiden": "raiden", "sterling": "raiden", "стерлинг": "raiden",
    "хару": "haru", "haru": "haru", "haru_foster": "haru",
    "мики": "miki", "miki": "miki",
    "алекс": "alex", "alex": "alex",
    "широ": "shiro", "shiro": "shiro",
    "юна": "yuna", "yuna": "yuna",
    "кай": "kai", "kai": "kai",
}

VISIBLE_LABELS = {
    "akira": "Акира",
    "jun": "Джун",
    "emma": "беловолосая девушка",
    "irey": "беловолосый парень",
    "ray": "мужчина в форме",
    "raiden": "парень с пирсингом",
}

DIALOGUE_HINTS = (
    "говор", "объяс", "спрос", "ответ", "кто", "что", "зачем", "почему",
    "молчи", "слуш", "вопрос", "назов", "памят", "помн", "узна", "смотр",
)

# Targeted patterns keep scene logic, not full card dumps.
CHARACTER_PATTERNS = {
    "voice": ("voice", "speech", "голос", "реплик", "tone", "style"),
    "goal": ("goal", "цель", "primary_goal", "current_goal", "защит", "контрол", "скры"),
    "reaction": (
        "не узна", "не помн", "пуст", "чуж", "теряется", "пауза", "сух",
        "реакц", "trigger", "visible", "боль", "сдерж", "дистанц", "вопрос",
    ),
    "behavior": ("behavior", "body_language", "микр", "движ", "дистанц", "рук", "выход", "угроз"),
    "player_control": ("player_control", "игрок", "не писать", "не задавать", "не соглаш"),
    "ability": ("energy", "ability", "способ", "поток", "энерг", "касани", "простран"),
}

KNOWLEDGE_PATTERNS = {
    "knows": ("знает", "knows", "known", "stable_knows", "starting_knowledge", "known_at_start"),
    "unknowns": ("не знает", "does_not_know", "unknown", "stable_does_not_know", "unknown_at_start", "strict_unknowns"),
    "hides": ("скрывает", "withholds", "hides", "sealed", "locked", "не раскры"),
    "rules": ("rule", "правил", "disclosure", "inference", "assumption", "предполага"),
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
    if not isinstance(body, dict):
        return {}
    data = body.get("data")
    if isinstance(data, dict):
        merged = dict(data)
        for key, value in body.items():
            if key not in merged:
                merged[key] = value
        return merged
    return body


def _trim(value: Any, max_chars: int = 500) -> str:
    text = str(value or "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _read_text(path: str, sid: str | None = None) -> str:
    try:
        return base.read_text(path, session_id=sid, default="") or ""
    except Exception:
        return ""


def _read_json(path: str, sid: str, default: Any) -> Any:
    try:
        value = base.read_json(path, session_id=sid, default=default)
        return default if value is None else value
    except Exception:
        return default


def _ensure_current(sid: str) -> dict[str, Any]:
    base.ensure_session(sid)
    current = _read_json(CURRENT_STATE_FILE, sid, {})
    if not isinstance(current, dict) or not current:
        current = base.initialize_start_session(sid, {"last_player_input": "начнем"})
    return current


def _canonical_id(value: Any) -> str:
    raw = str(value or "").strip()
    key = raw.lower().replace("ё", "е")
    return ID_ALIASES.get(key, key)


def _add_id(ids: list[str], value: Any) -> None:
    if isinstance(value, str):
        cid = _canonical_id(value)
        if cid and cid not in {"none", "null", "unknown", "false"} and cid not in ids:
            ids.append(cid)
    elif isinstance(value, list):
        for item in value:
            _add_id(ids, item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, dict):
                _add_id(ids, item.get("id") or item.get("character_id") or item.get("slug") or key)
            else:
                _add_id(ids, key)


def _compact(value: Any, *, max_chars: int = 700, max_items: int = 8, depth: int = 2) -> Any:
    if depth <= 0:
        if isinstance(value, str):
            return _trim(value, max_chars)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return _trim(json.dumps(value, ensure_ascii=False, sort_keys=True), max_chars)
    if isinstance(value, dict):
        keys = [
            "summary", "current_goal", "current_status", "memory", "knows_as_fact", "knows",
            "does_not_know", "suspects", "assumes", "wrongly_believes", "is_hiding",
            "future_hooks", "recent_scene_notes", "last_interaction", "ray_to_akira",
            "akira_to_ray", "irey_to_akira", "emma_to_akira", "jun_to_akira",
        ]
        ordered = [k for k in keys if k in value] + [k for k in value if k not in keys]
        return {k: _compact(value[k], max_chars=max_chars, max_items=max_items, depth=depth - 1) for k in ordered[:max_items]}
    if isinstance(value, list):
        return [_compact(item, max_chars=max_chars, max_items=max_items, depth=depth - 1) for item in value[:max_items]]
    if isinstance(value, str):
        return _trim(value, max_chars)
    return value


def _matching_lines(text: str, patterns: tuple[str, ...], *, max_lines: int = 10, max_chars: int = 1200) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        clean = line.strip()
        if not clean or clean in seen:
            continue
        low = clean.lower().replace("ё", "е")
        if any(p in low for p in patterns):
            seen.add(clean)
            out.append(clean)
        if len(out) >= max_lines:
            break
    joined = _trim("\n".join(out), max_chars)
    return [x for x in joined.splitlines() if x.strip()]


def _first_nonempty(*values: Any, max_chars: int = 500) -> str:
    for value in values:
        text = _trim(value, max_chars)
        if text:
            return text
    return ""


def _memory_lines(memory: dict[str, Any], keys: tuple[str, ...], *, max_items: int = 8, max_chars: int = 1000) -> list[str]:
    result: list[str] = []

    def walk(obj: Any) -> None:
        if len(result) >= max_items:
            return
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_low = str(key).lower().replace("ё", "е")
                if any(k in key_low for k in keys):
                    if isinstance(value, list):
                        for item in value:
                            if len(result) >= max_items:
                                return
                            result.append(_trim(item, 220))
                    elif value:
                        result.append(_trim(value, 220))
                elif key in ("memory", "current_status", "temporary_knowledge_state"):
                    walk(value)

    if isinstance(memory, dict):
        walk(memory)
    joined = _trim("\n".join(x for x in result if x), max_chars)
    return [x for x in joined.splitlines() if x.strip()]


def _scene_is_dialogue_or_pressure(player_input: str, scene_plan: dict[str, Any]) -> bool:
    text = (player_input or "").lower().replace("ё", "е")
    scene_type = str(scene_plan.get("scene_type") or scene_plan.get("type") or "").lower()
    if any(x in scene_type for x in ("dialog", "разговор", "опрос", "конфликт", "наблюдение")):
        return True
    return any(x in text for x in DIALOGUE_HINTS)


def _collect_character_ids(payload: dict[str, Any], current: dict[str, Any], scene_plan: dict[str, Any], player_input: str) -> list[str]:
    ids: list[str] = []
    _add_id(ids, current.get("pov_character_id"))
    for key in ("speaking_character_ids", "addressed_character_ids", "present_character_ids"):
        _add_id(ids, current.get(key))
    for key in ("speaking_characters", "addressed_characters", "present_characters", "character_ids", "characters", "character_requests"):
        _add_id(ids, scene_plan.get(key))
        _add_id(ids, payload.get(key))
    _add_id(ids, current.get("scene_character_ids") or current.get("active_character_ids"))
    # Keep hard cap. More can be requested through requestMoreContext with requested_blocks.characters.
    return ids[:6]


def _role_for(cid: str, current: dict[str, Any], scene_plan: dict[str, Any]) -> str:
    pov = _canonical_id(current.get("pov_character_id"))
    if cid == pov:
        return "pov"
    speaking = [_canonical_id(x) for x in (current.get("speaking_character_ids") or [])]
    speaking += [_canonical_id(x) for x in (scene_plan.get("speaking_characters") or [])]
    addressed = [_canonical_id(x) for x in (current.get("addressed_character_ids") or [])]
    addressed += [_canonical_id(x) for x in (scene_plan.get("addressed_characters") or [])]
    if cid in speaking:
        return "speaking"
    if cid in addressed:
        return "addressed"
    if cid in [_canonical_id(x) for x in (current.get("present_character_ids") or [])]:
        return "present_reaction"
    return "referenced"


def _character_card(sid: str, cid: str, role: str, expanded: bool = False) -> dict[str, Any]:
    char_text = _read_text(f"characters/{cid}/character.yaml", sid)
    know_text = _read_text(f"characters/{cid}/knowledge.yaml", sid)
    main_text = _read_text(f"characters/{cid}/main.yaml", sid)
    memory = _read_json(f"state/character_memory/{cid}.json", sid, {})
    if not isinstance(memory, dict):
        memory = {}

    # Normal cards are intentionally compact. Expanded cards are still bounded.
    line_boost = 14 if expanded or role == "pov" else 8
    char_budget = 1500 if expanded or role == "pov" else 900
    know_budget = 1500 if expanded or role == "pov" else 1000

    card: dict[str, Any] = {
        "id": cid,
        "visible_label_default": VISIBLE_LABELS.get(cid, cid),
        "role_in_context": role,
        "source_files": [
            f"characters/{cid}/character.yaml",
            f"characters/{cid}/knowledge.yaml",
            f"state/character_memory/{cid}.json",
        ],
        "identity_role_goal": (
            _matching_lines(main_text + "\n" + char_text, CHARACTER_PATTERNS["goal"], max_lines=6, max_chars=600)
        ),
        "voice_behavior": _matching_lines(char_text, CHARACTER_PATTERNS["voice"] + CHARACTER_PATTERNS["behavior"], max_lines=line_boost, max_chars=char_budget),
        "must_react_to": _matching_lines(char_text + "\n" + know_text, CHARACTER_PATTERNS["reaction"], max_lines=line_boost, max_chars=char_budget),
        "knows_or_assumes": (
            _matching_lines(know_text, KNOWLEDGE_PATTERNS["knows"] + KNOWLEDGE_PATTERNS["rules"], max_lines=line_boost, max_chars=know_budget)
            + _memory_lines(memory, ("knows", "assumes", "suspects", "предполага", "знает"), max_items=6, max_chars=700)
        )[:line_boost],
        "does_not_know_or_hides": (
            _matching_lines(know_text, KNOWLEDGE_PATTERNS["unknowns"] + KNOWLEDGE_PATTERNS["hides"], max_lines=line_boost, max_chars=know_budget)
            + _memory_lines(memory, ("does_not_know", "не знает", "hiding", "is_hiding", "скры"), max_items=6, max_chars=700)
        )[:line_boost],
        "current_memory_hooks": _compact(memory.get("memory") or memory.get("current_status") or memory, max_chars=900 if expanded else 550, max_items=6, depth=2),
    }

    if role == "pov":
        card["pov_required"] = True
        card["pov_voice_limits"] = _matching_lines(
            char_text + "\n" + know_text,
            CHARACTER_PATTERNS["voice"] + CHARACTER_PATTERNS["player_control"] + ("known_at_start", "unknown_at_start", "pov_rules", "пуст", "микр"),
            max_lines=18,
            max_chars=1800,
        )
        card["player_control_rule"] = "Do not invent important Akira replies/questions/agreements. If player did not give a key reply, show microreaction and stop for choice."
    else:
        card["npc_line_rule"] = "Before each line: use this NPC's knows/unknowns/hides. Unknowns should create questions, checks, pauses, pressure, evasion or wrong assumptions, not omniscience."

    ability = _matching_lines(char_text + "\n" + know_text, CHARACTER_PATTERNS["ability"], max_lines=6 if expanded else 3, max_chars=800)
    if ability:
        card["ability_if_relevant"] = ability

    return card


def _relationship_cards(sid: str, current: dict[str, Any], payload: dict[str, Any], expanded: bool = False) -> dict[str, Any]:
    pairs = payload.get("relationship_pair_ids") or current.get("relationship_pair_ids", [])
    result: dict[str, Any] = {}
    for pair in list(pairs)[:5 if expanded else 4]:
        pid = str(pair or "").strip()
        if "__" not in pid:
            continue
        path = f"state/relationship_pairs/{pid}.json"
        data = _read_json(path, sid, {})
        if not isinstance(data, dict) or not data:
            continue
        result[pid] = {
            "source_file": path,
            "summary": _first_nonempty(
                data.get("surface_dynamic", {}).get("summary") if isinstance(data.get("surface_dynamic"), dict) else "",
                data.get("summary"),
                max_chars=500,
            ),
            "reaction_hooks": _compact(
                {
                    "left_to_right": data.get(str(pid.split("__")[0]) + "_to_" + str(pid.split("__")[1])),
                    "right_to_left": data.get(str(pid.split("__")[1]) + "_to_" + str(pid.split("__")[0])),
                    "last_interaction": data.get("last_interaction"),
                },
                max_chars=900 if expanded else 650,
                max_items=6,
                depth=2,
            ),
        }
    return result


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
        "present_character_ids": current.get("present_character_ids", []),
        "speaking_character_ids": current.get("speaking_character_ids", []),
        "addressed_character_ids": current.get("addressed_character_ids", []),
        "conditional_character_ids": current.get("conditional_character_ids", []),
        "relationship_pair_ids": current.get("relationship_pair_ids", []),
        "visible_inventory": _compact(current.get("visible_inventory", []), max_chars=450, max_items=8, depth=2),
        "nearby_items": _compact(current.get("nearby_items", []), max_chars=450, max_items=8, depth=2),
        "scene_goal": _trim(current.get("scene_goal") or current.get("current_scene_goal"), 450),
        "last_player_input": _trim(current.get("last_player_input"), 300),
        "start_scene_exact_text_required": bool(current.get("start_scene_exact_text_required")),
        "start_scene_completed": bool(current.get("start_scene_completed")),
        "boundary_note": "Inventory/state are not NPC knowledge unless the NPC saw/heard/source confirms it.",
    }


def _calendar_slice(sid: str, current: dict[str, Any]) -> dict[str, Any]:
    runtime = _read_json(CALENDAR_RUNTIME_FILE, sid, {})
    if not isinstance(runtime, dict):
        runtime = {}
    return {
        "current_date": current.get("current_date") or runtime.get("current_date"),
        "current_day_phase": current.get("current_day_phase") or runtime.get("current_day_phase"),
        "current_beat_id": runtime.get("current_beat_id") or current.get("current_beat_id"),
        "pending_events": _compact(runtime.get("pending_events", []), max_chars=450, max_items=5, depth=2),
        "rules": _compact(runtime.get("rules", []), max_chars=450, max_items=4, depth=2),
    }


def _history_slice(sid: str, depth: int = 3) -> list[dict[str, Any]]:
    history = _read_json(SCENE_HISTORY_FILE, sid, [])
    if isinstance(history, dict):
        history = history.get("entries", [])
    if not isinstance(history, list):
        return []
    out = []
    for item in history[-depth:]:
        if isinstance(item, dict):
            out.append({
                "scene_id": item.get("scene_id") or item.get("id"),
                "player_input": _trim(item.get("player_input"), 140),
                "summary": _trim(item.get("summary") or item.get("visible_scene_text") or item.get("scene_text"), 350),
            })
    return out


def _location_slice(current: dict[str, Any], scene_plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "location_id": scene_plan.get("location_id") or current.get("current_location_id") or current.get("location_id"),
        "location_text": current.get("current_location_text") or current.get("location_text"),
        "requested_depth": scene_plan.get("location_depth") or scene_plan.get("needs", {}).get("location"),
        "rule": "Use explicit location/current_state. Do not invent background NPCs or noises without source.",
    }


def _extract_start_scene_text() -> str:
    text = _read_text(START_SCENE_PATH)
    match = re.search(r"```text\s*(.*?)```", text, flags=re.S)
    return match.group(1).strip() if match else text.strip()


def _render_contract_small() -> dict[str, Any]:
    data = _read_json(RENDER_CONTRACT_PATH, "default", {})
    if not isinstance(data, dict) or not data:
        return {
            "dialogue_format": "**Имя** — реплика.",
            "pov_rule": "Respect POV knowledge and player control.",
            "bottom_blocks": ["Что можно сделать", "Что Акира могла бы сказать", "Мысли Акиры", "Состояние"],
        }
    # Do not return full contract; only the key writer constraints.
    return {
        "source_file": RENDER_CONTRACT_PATH,
        "must_be_last_writer_instruction": True,
        "dialogue_format_required": data.get("dialogue_format_required") or "**Имя** — реплика.",
        "unknown_names_rule": "If POV does not know a name, use visible descriptor, not engine id/display_name.",
        "bottom_blocks_rule": "Keep choice/options/status blocks; do not expose hidden lore as POV thoughts.",
    }


_remove_route("/api/v3/sessions/{session_id}/preflight", "GET")
_remove_route("/api/v3/sessions/{session_id}/context-request", "POST")
_remove_route("/api/v3/sessions/{session_id}/context-more", "POST")
_remove_route("/api/v3/sessions/{session_id}/start-scene-text", "GET")


@app.get("/api/v3/sessions/{session_id}/preflight", operation_id="getPreflight")
def get_preflight(session_id: str) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_preflight_character_cards_small",
        "current_state": _current_state_slice(current),
        "calendar": _calendar_slice(sid, current),
        "recent_scene_history": _history_slice(sid, 3),
        "next_action": "requestContextSlice",
        "writer_note": "POV and active NPC cards are loaded in requestContextSlice as compact writer cards.",
    }


@app.post("/api/v3/sessions/{session_id}/context-request", operation_id="requestContextSlice")
def request_context_slice(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    payload = _payload(body)
    scene_plan = payload.get("scene_plan") if isinstance(payload.get("scene_plan"), dict) else {}
    explicit_input = payload.get("player_input") or payload.get("user_input") or scene_plan.get("player_input") or scene_plan.get("user_input")
    player_input = _trim(explicit_input or current.get("last_player_input"), 360)

    requested_blocks = payload.get("requested_blocks") or scene_plan.get("required_blocks") or {}
    expanded = bool(payload.get("expanded_context")) or bool(requested_blocks and payload.get("reason"))
    cids = _collect_character_ids(payload, current, scene_plan, player_input)
    roles = {cid: _role_for(cid, current, scene_plan) for cid in cids}

    # If not dialogue/pressure, still load POV + present/speaking, but cards stay compact.
    characters = {
        cid: _character_card(sid, cid, roles[cid], expanded=(expanded and roles[cid] in {"pov", "speaking", "addressed"}))
        for cid in cids
    }

    start_scene_available = bool(current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"))
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_context_slice_character_cards_small",
        "player_input": player_input,
        "input_consistency": {
            "explicit_input_received": bool(explicit_input),
            "warning": None if explicit_input else "No explicit input; used current_state.last_player_input.",
        },
        "context_slice": {
            "current_state": _current_state_slice(current),
            "calendar": _calendar_slice(sid, current),
            "location": _location_slice(current, scene_plan),
            "recent_scene_history": _history_slice(sid, 4 if expanded else 3),
            "characters": characters,
            "relationships": _relationship_cards(sid, current, payload, expanded=expanded),
            "scene_rules": {
                "pov_required": "POV character card is always present. Use it for Akira voice, emptiness, microreactions and player-control limits.",
                "npc_reaction_required": "For each NPC line/reaction, check role/goal/knows/unknowns/hides/must_react_to.",
                "unknowns_are_active": "If a character does not know something, they may question, check, lie, pause, evade or misread; do not make them passive furniture.",
                "engine_state_not_npc_knowledge": True,
            },
            "blocked_blocks": {
                "full_yaml": "not returned to Actions",
                "deep_past": "requestMoreContext only if scene explicitly triggers it",
                "full_relationships": "relationship summary/hooks only",
            },
            "start_scene": {
                "exact_text_required": start_scene_available,
                "text_endpoint": f"/api/v3/sessions/{sid}/start-scene-text" if start_scene_available else None,
            },
            "final_render_contract": _render_contract_small(),
        },
        "size_guard": "small writer cards; no full YAML sections",
        "next_action": "getStartSceneText" if start_scene_available else "writeScene",
    }


@app.post("/api/v3/sessions/{session_id}/context-more", operation_id="requestMoreContext")
def request_more_context(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = _payload(body)
    payload["expanded_context"] = True
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
        "after_output_instruction": "After outputting exact_text, wait for the player. On next input call processTurn.",
    }


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
        "message": "Full scene_contract is disabled for Actions. Use getPreflight then requestContextSlice.",
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
        "mode": "v3_action_safe_context_audit_character_cards_small",
        "current_state": _current_state_slice(current),
        "pov_character_must_load": True,
        "full_scene_contract_actions_disabled": True,
        "context_request_endpoint": f"/api/v3/sessions/{sid}/context-request",
    }


try:
    app.version = RUNTIME_VERSION
except Exception:
    pass
