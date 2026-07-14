"""Hybrid manifest/chunk context pipeline for Akira 1206 v3.

Single replacement file. No extra runtime layers.

Principle:
- Railway plans the turn once and freezes one server-side snapshot per turn_id.
- Custom GPT receives several small chunks, not one huge JSON blob.
- Manifest and chunks are served only from that immutable snapshot, in order.
- Character depth is preserved: identity + voice + goals + evidence-separated knowledge + response duty are always present.
- Dynamic memory is loaded only for scene-active characters; relationship pairs are selected by scene relevance.
- Energy, deep appearance, lore and past are loaded only when the current turn actually needs them.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import Body

from app import compact as base
from app import scene_validation
from app import start_scene_commit

app = base.app
RUNTIME_VERSION = base.APP_VERSION

CURRENT_STATE_FILE = "state/current_state.json"
SCENE_HISTORY_FILE = "state/scene_history.json"
CALENDAR_RUNTIME_FILE = "state/calendar_runtime.json"
SCHEDULE_AVAILABILITY_FILE = "state/context_loading/schedule_availability_rules_1206.json"
STORY_LINES_FILE = "state/story_lines.json"
START_SCENE_PATH = "scenes/start_scene.md"
RENDER_CONTRACT_PATH = "gpt/scene_output_contract_1206.json"
CONTEXT_SNAPSHOT_FILE = base.CONTEXT_SNAPSHOT_FILE
RELATIONSHIP_INDEX_FILE = "state/relationship_pairs/_index.json"

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

IDENTITY_OVERRIDES = {
    "akira": "25 лет; взрослая девушка; рост 165; платиново-белые/белые волосы; тёмно-карие глаза с янтарным отблеском. Нейтрально не называть девочкой.",
    "jun": "взрослый мужчина; хозяин дома; для Акиры — Джун/отец/опекун по сыгранному контексту; для Эммы/Ирэя на старте — неизвестный мужчина.",
    "irey": "взрослый мужчина/парень; белые волосы; пространственно-сенсорная связь через касание; внешне сдержаннее, чем его внутренняя цель.",
    "emma": "взрослая девушка/женщина; резкая, грубая, эмоциональная; не декор и не удобная кнопка экспозиции.",
    "ray": "взрослый мужчина в форме; командующий Восточного сектора; условное/отложенное появление.",
    "raiden": "взрослый высокий мужчина/рейдер; условное/отложенное появление, не активен в стартовой комнате.",
}

DEFAULT_VISIBLE_LABELS = {
    "akira": "Акира",
    "jun": "Джун",
    "emma": "беловолосая девушка",
    "irey": "беловолосый парень",
    "ray": "мужчина в форме",
    "raiden": "высокий рейдер",
}

UNKNOWN_NAME_RULES = [
    "Internal character_id/display_name is engine knowledge, not automatic scene permission.",
    "If POV or speaker has not heard/read a name in-scene or from state knowledge, use a stable visible descriptor.",
    "Akira does not know Emma/Irey by name at the start: use descriptors until a visible name source appears.",
    "Emma and Irey do not know engine:jun by name at the start: in their speech use 'мужчина', 'хозяин дома', 'тот, кто её прятал', not 'Джун/Картер'.",
    "Do not call 25-year-old Akira 'девочка' in narration or neutral labels. Use 'Акира', 'девушка', 'цель', or an intentionally hostile descriptor only if character voice requires it.",
]

DIALOGUE_HINTS = (
    "говор", "объяс", "спрос", "ответ", "кто", "что", "зачем", "почему",
    "молчи", "слуш", "вопрос", "назов", "памят", "помн", "узна", "смотр",
)
ENERGY_HINTS = (
    "энерг", "сила", "поток", "эхо", "кайрос", "простран", "холод", "огонь",
    "вода", "воздух", "подавлен", "перегруз", "браслет", "барьер", "касани",
)
PAST_HINTS = (
    "прошл", "вспом", "флэшбек", "флешбек", "академ", "1198", "1170",
    "самуэль", "samuel", "беремен", "ребен", "ребён", "лаборатор",
    "эксперимент", "плен", "старое кольцо", "след от кольца", "потеря ребенка", "потеря ребёнка",
)
APPEARANCE_HINTS = ("осмотреть", "выгляд", "лицо", "рост", "волос", "глаза", "шрам", "одеж", "форма", "опис")
LORE_HINTS = ("эхо", "кайрос", "восточный сектор", "рейдер", "сектор", "самуэль", "система", "заказчик")
INVENTORY_HINTS = ("карман", "ножниц", "документ", "записк", "блокнот", "оруж", "стол", "взять", "убрать", "пояс")

CHARACTER_PATTERNS = {
    "voice": ("voice", "speech", "голос", "реплик", "tone", "style", "говорит", "речь"),
    "goal": ("goal", "цель", "primary_goal", "current_goal", "защит", "контрол", "скры", "достав", "заказчик", "система"),
    "reaction": ("не узна", "не помн", "пуст", "чуж", "теряется", "пауза", "сух", "реакц", "trigger", "visible", "боль", "сдерж", "дистанц", "вопрос"),
    "behavior": ("behavior", "body_language", "habit", "микр", "движ", "дистанц", "рук", "выход", "угроз", "взгляд"),
    "player_control": ("player_control", "игрок", "не писать", "не задавать", "не соглаш", "pov_rules"),
    "ability": ("energy", "ability", "способ", "поток", "энерг", "касани", "простран", "вода", "холод", "огонь"),
}

KNOWLEDGE_PATTERNS = {
    "knows": ("знает", "knows", "known", "stable_knows", "starting_knowledge", "known_at_start"),
    "unknowns": ("не знает", "does_not_know", "unknown", "stable_does_not_know", "unknown_at_start", "strict_unknowns"),
    "hides": ("скрывает", "withholds", "hides", "sealed", "locked", "не раскры"),
    "rules": ("rule", "правил", "disclosure", "inference", "assumption", "предполага", "источник"),
}

MEMORY_FIELD_ALIASES = {
    "observed": {"seen", "saw", "observed", "events_witnessed", "видел", "видела", "видело", "видели"},
    "heard": {"heard", "слышал", "слышала", "слышало", "слышали"},
    "facts": {"knows_as_fact", "known_facts", "знает_как_факт", "знает как факт"},
    "beliefs": {"assumes", "suspects", "believes", "may_assume", "conclusions", "выводы", "предполагает", "подозревает"},
    "mistakes": {"wrongly_believes", "wrong_beliefs", "misbelieves", "mistaken_beliefs", "mistakenly_believes", "ошибочно_считает"},
    "unknowns": {"does_not_know", "does_not_know_active", "unknowns", "forbidden_as_fact", "не_знает", "не знает"},
    "hides": {"is_hiding", "hides", "hides_from", "hides_currently", "скрывает", "скрывает_от"},
    "agreements": {"agreements", "orders_or_duties", "договоренности", "договорённости"},
    "goals": {"current_goal", "active_goals", "goals", "цель", "цели"},
    "limitations": {"current_limitations", "limitations", "ограничения"},
    "hooks": {"future_hooks", "open_threads", "зацепки_на_будущее"},
    "recent": {"recent_scene_notes", "last_scene_notes", "last_scene_summary", "последние_сцены"},
}

ACTIVE_MEMORY_ROLES = {"pov", "speaking", "addressed", "observing", "present_reaction"}


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



def _requested_chunk_index(payload: dict[str, Any], default: int = 0) -> int:
    """Accept chunk index from new endpoint or legacy/nested requestMoreContext payloads.

    Custom GPT sometimes puts chunk_index inside requested_blocks when it follows
    the old requestMoreContext operation. Treat top-level chunk_index, force_chunk,
    next_chunk_index, and nested requested_blocks.* as equivalent.
    """
    candidates: list[Any] = [
        payload.get("chunk_index"),
        payload.get("force_chunk"),
        payload.get("next_chunk_index"),
    ]
    for container_key in ("requested_blocks", "chunk", "context_chunk", "required_chunk"):
        nested = payload.get(container_key)
        if isinstance(nested, dict):
            candidates.extend([
                nested.get("chunk_index"),
                nested.get("force_chunk"),
                nested.get("next_chunk_index"),
                nested.get("index"),
            ])
    scene_plan = payload.get("scene_plan")
    if isinstance(scene_plan, dict):
        nested = scene_plan.get("requested_blocks") or scene_plan.get("required_blocks")
        if isinstance(nested, dict):
            candidates.extend([
                nested.get("chunk_index"),
                nested.get("force_chunk"),
                nested.get("next_chunk_index"),
                nested.get("index"),
            ])
    for value in candidates:
        if value is None:
            continue
        try:
            text = str(value).strip()
            if text == "":
                continue
            return max(0, int(text))
        except Exception:
            continue
    return max(0, int(default or 0))

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
    current = base.effective_current_state(sid)
    if not isinstance(current, dict) or not current:
        current = base.initialize_start_session(sid, {"last_player_input": "начнем"})
    return current


def _payload_turn_id(payload: dict[str, Any]) -> str:
    candidates: list[Any] = [payload.get("turn_id")]
    for key in ("turn_contract", "scene_response", "metadata"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            candidates.append(nested.get("turn_id"))
            metadata = nested.get("metadata")
            if isinstance(metadata, dict):
                candidates.append(metadata.get("turn_id"))
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _turn_contract_error(sid: str, current: dict[str, Any], error: str, next_action: str) -> dict[str, Any]:
    pending = base.get_pending_turn(sid)
    runtime = base.read_turn_runtime(sid)
    return {
        "success": False,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_turn_contract_rejected",
        "error": error,
        "turn_id": pending.get("turn_id") if pending else None,
        "state_revision": int(runtime.get("state_revision") or 0),
        "current_frame": _current_state_slice(current),
        "required_chunks": [],
        "total_chunks": 0,
        "next_action": next_action,
        "visible_scene_output_allowed": False,
    }


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
            "future_hooks", "recent_scene_notes", "last_interaction", "surface_dynamic",
            "ray_to_akira", "akira_to_ray", "irey_to_akira", "emma_to_akira", "jun_to_akira",
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


def _confirmed_knowledge_lines(text: str, *, max_lines: int = 10, max_chars: int = 1100) -> list[str]:
    excluded = (
        "не знает", "не_знает", "does_not_know", "unknown", "strict_unknown",
        "предполага", "подозрев", "assum", "suspect", "may believe", "может считать",
        "ошибочно", "wrongly", "misbeliev", "скрывает", "hides", "withhold",
    )
    candidates = _matching_lines(text, KNOWLEDGE_PATTERNS["knows"], max_lines=max_lines * 3, max_chars=max_chars * 2)
    result = [line for line in candidates if not any(term in line.lower().replace("ё", "е") for term in excluded)]
    joined = _trim("\n".join(result[:max_lines]), max_chars)
    return [line for line in joined.splitlines() if line.strip()]


def _yaml_scalar_text(value: str) -> str:
    text = value.strip()
    if text.startswith("-"):
        text = text[1:].strip()
    if ":" in text and not text.startswith(("http:", "https:")):
        key, remainder = text.split(":", 1)
        if _memory_key(key) in {"id", "enabled", "disclosure", "reveal_rule", "can_lie", "can_partial", "status"}:
            return ""
        if _memory_key(key) in {"fact", "text", "summary"}:
            text = remainder.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1]
    return _trim(text, 300)


def _static_knowledge_buckets(text: str) -> dict[str, list[str]]:
    buckets = {"facts": [], "unknowns": [], "beliefs": [], "mistakes": [], "hides": []}
    active_category = ""
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        if indent == 0 and ":" in stripped:
            root = _memory_key(stripped.split(":", 1)[0])
            if root in {"stable_knows", "starting_knowledge", "known_at_start", "starting_facts"}:
                active_category = "facts"
            elif "does_not_know" in root or "strict_unknown" in root or root in {"unknowns", "known_name_rules"}:
                active_category = "unknowns"
            elif "misbelief" in root or "wrong_belief" in root:
                active_category = "mistakes"
            elif "inference" in root or "assumption" in root or "suspect" in root:
                active_category = "beliefs"
            elif "hide" in root or "withhold" in root or "avoid" in root:
                active_category = "hides"
            else:
                active_category = ""
            continue
        if _memory_key(stripped.split(":", 1)[0]) == "fact" and ":" in stripped:
            value = _yaml_scalar_text(stripped)
            if value and value not in buckets["facts"]:
                buckets["facts"].append(value)
            continue
        if active_category and stripped.startswith("-"):
            value = _yaml_scalar_text(stripped)
            if value and value not in buckets[active_category]:
                buckets[active_category].append(value)
    return buckets


def _first_nonempty(*values: Any, max_chars: int = 500) -> str:
    for value in values:
        text = _trim(value, max_chars)
        if text:
            return text
    return ""


def _bounded_lines(values: list[Any], *, max_items: int, max_chars: int) -> list[str]:
    result: list[str] = []
    used = 0
    for value in values:
        text = _memory_value_text(value)
        if not text or text in result:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = _trim(text, remaining)
        if text:
            result.append(text)
            used += len(text)
        if len(result) >= max_items:
            break
    return result


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


def _memory_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("ё", "е").replace(" ", "_")


def _memory_value_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("text", "fact", "summary", "note", "event", "событие", "факт"):
            if value.get(key):
                return _trim(value.get(key), 260)
        return _trim(json.dumps(value, ensure_ascii=False, sort_keys=True), 260)
    return _trim(value, 260)


def _memory_event_ledger(memory: dict[str, Any], *, max_items: int = 10) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for key in ("memory_events", "knowledge_history"):
        values = memory.get(key)
        if not isinstance(values, list):
            continue
        for raw in values:
            if not isinstance(raw, dict):
                continue
            text = _memory_value_text(raw)
            if not text:
                continue
            event = {
                "event_id": _trim(raw.get("event_id"), 100) or None,
                "turn_id": _trim(raw.get("turn_id"), 100) or None,
                "kind": _trim(raw.get("kind") or raw.get("type"), 60) or "scene_memory",
                "text": text,
                "source_type": _trim(raw.get("source_type"), 80) or "legacy_unspecified",
                "evidence": _trim(raw.get("evidence"), 160) or None,
                "confidence": _trim(raw.get("confidence"), 40) or None,
                "status": _trim(raw.get("status"), 40) or None,
            }
            events.append({key: value for key, value in event.items() if value is not None})
    return events[-max_items:]


def _memory_buckets(memory: dict[str, Any], *, max_items: int = 9) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {key: [] for key in MEMORY_FIELD_ALIASES}
    alias_map = {
        _memory_key(alias): category
        for category, aliases in MEMORY_FIELD_ALIASES.items()
        for alias in aliases
    }

    def add(category: str, value: Any) -> None:
        values = value if isinstance(value, list) else [value]
        for item in values:
            text = _memory_value_text(item)
            if text and text not in buckets[category] and len(buckets[category]) < max_items:
                buckets[category].append(text)

    def walk(obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        for key, value in obj.items():
            normalized = _memory_key(key)
            category = alias_map.get(normalized)
            if category:
                add(category, value)
            elif normalized in {"memory", "current_status", "temporary_knowledge_state"}:
                walk(value)

    walk(memory)
    event_categories = {
        "observation": "observed", "observed": "observed", "seen": "observed",
        "heard": "heard", "reported": "heard",
        "fact": "facts", "learned_fact": "facts",
        "belief": "beliefs", "suspicion": "beliefs", "inference": "beliefs",
        "mistaken_belief": "mistakes", "wrong_belief": "mistakes",
        "unknown_boundary": "unknowns", "hidden_intent": "hides",
        "agreement": "agreements", "order": "agreements",
        "goal": "goals", "limitation": "limitations", "future_hook": "hooks",
        "scene_memory": "recent",
    }
    for event in _memory_event_ledger(memory, max_items=max_items * 2):
        category = event_categories.get(_memory_key(event.get("kind")))
        if not category:
            continue
        if event.get("status") == "resolved" and category in {"goals", "limitations", "hooks", "hides"}:
            text = _memory_value_text(event.get("text"))
            buckets[category] = [value for value in buckets[category] if value != text]
            continue
        add(category, event.get("text"))
    return buckets


def _response_obligation(role: str) -> dict[str, Any]:
    if role == "pov":
        return {"required": False, "mode": "player_controlled"}
    if role == "addressed":
        return {"required": True, "mode": "answer_or_visible_refusal"}
    if role == "speaking":
        return {"required": True, "mode": "goal_driven_turn"}
    if role == "observing":
        return {"required": False, "mode": "brief_observer_reaction"}
    if role == "present_reaction":
        return {"required": False, "mode": "brief_background_reaction"}
    if role == "offscreen_decision":
        return {
            "required": True,
            "mode": "offscreen_goal_decision",
            "visible_scene_presence": False,
            "rule": "Choose an evidence-backed activity/arrival/delay from this NPC's own goal and limits; do not give them current-scene knowledge.",
        }
    return {"required": False, "mode": "no_unsourced_action"}


def _scene_text(payload: dict[str, Any], current: dict[str, Any], scene_plan: dict[str, Any]) -> str:
    explicit = payload.get("player_input") or payload.get("user_input") or scene_plan.get("player_input") or scene_plan.get("user_input")
    return _trim(explicit or current.get("last_player_input"), 700)


def _scene_is_dialogue_or_pressure(player_input: str, scene_plan: dict[str, Any]) -> bool:
    text = (player_input or "").lower().replace("ё", "е")
    scene_type = str(scene_plan.get("scene_type") or scene_plan.get("type") or "").lower()
    if any(x in scene_type for x in ("dialog", "разговор", "опрос", "конфликт", "наблюдение")):
        return True
    return any(x in text for x in DIALOGUE_HINTS)


def _needs(payload: dict[str, Any], current: dict[str, Any], scene_plan: dict[str, Any], player_input: str) -> dict[str, bool]:
    low = " ".join([player_input, json.dumps(scene_plan, ensure_ascii=False), str(current.get("scene_goal") or "")]).lower().replace("ё", "е")
    requested = payload.get("needs") if isinstance(payload.get("needs"), dict) else {}
    required_blocks = scene_plan.get("required_blocks") if isinstance(scene_plan.get("required_blocks"), dict) else {}
    return {
        "dialogue_or_pressure": _scene_is_dialogue_or_pressure(player_input, scene_plan),
        "energy": bool(requested.get("energy") or required_blocks.get("energy") or any(x in low for x in ENERGY_HINTS)),
        "past": bool(
            current.get("load_past")
            or any(x in low for x in PAST_HINTS)
            or (requested.get("past") and scene_plan.get("past_trigger_reason"))
        ),
        "deep_appearance": bool(requested.get("appearance") or any(x in low for x in APPEARANCE_HINTS)),
        "lore": bool(requested.get("lore") or any(x in low for x in LORE_HINTS)),
        "inventory": bool(requested.get("inventory") or required_blocks.get("inventory") or any(x in low for x in INVENTORY_HINTS)),
        "calendar": True,
        "location": True,
        "render_contract": True,
    }


def _past_trigger_terms(current: dict[str, Any], scene_plan: dict[str, Any], player_input: str) -> list[str]:
    low = " ".join([
        player_input,
        json.dumps(scene_plan, ensure_ascii=False),
        str(current.get("scene_goal") or current.get("current_scene_goal") or ""),
    ]).lower().replace("ё", "е")
    terms = [term for term in PAST_HINTS if term.replace("ё", "е") in low]
    explicit = scene_plan.get("past_trigger_terms")
    if isinstance(explicit, list):
        for value in explicit:
            term = _trim(value, 80).lower().replace("ё", "е")
            if term and term not in terms:
                terms.append(term)
    if current.get("load_past") and not terms:
        terms.append("current_state_explicit_past_trigger")
    return terms[:8]


def _collect_character_ids(payload: dict[str, Any], current: dict[str, Any], scene_plan: dict[str, Any], player_input: str) -> list[str]:
    ids: list[str] = []
    _add_id(ids, current.get("pov_character_id"))
    for key in ("speaking_character_ids", "addressed_character_ids", "present_character_ids", "observing_character_ids"):
        _add_id(ids, current.get(key))
    for key in (
        "speaking_characters", "addressed_characters", "present_characters", "observing_characters",
        "remote_contact_character_ids", "contacted_character_ids",
        "character_ids", "characters", "character_requests",
    ):
        _add_id(ids, scene_plan.get(key))
        _add_id(ids, payload.get(key))
    _add_id(ids, current.get("scene_character_ids") or current.get("active_character_ids"))
    return ids


def _infer_direct_addressed_ids(player_input: str, cids: list[str]) -> list[str]:
    text = str(player_input or "").lower().replace("ё", "е")
    result: list[str] = []
    for cid in cids:
        aliases = {
            alias.replace("ё", "е")
            for alias, target in ID_ALIASES.items()
            if target == cid and " " not in alias and "_" not in alias and len(alias) >= 3
        }
        if any(re.search(rf"(?:^|[^\w]){re.escape(alias)}\s*[,!:]", text) for alias in aliases):
            result.append(cid)
    return result


def _role_for(cid: str, current: dict[str, Any], scene_plan: dict[str, Any]) -> str:
    pov = _canonical_id(current.get("pov_character_id"))
    if cid == pov:
        return "pov"
    speaking = [_canonical_id(x) for x in (current.get("speaking_character_ids") or [])]
    speaking += [_canonical_id(x) for x in (scene_plan.get("speaking_characters") or [])]
    addressed = [_canonical_id(x) for x in (current.get("addressed_character_ids") or [])]
    addressed += [_canonical_id(x) for x in (scene_plan.get("addressed_characters") or [])]
    if cid in addressed:
        return "addressed"
    if cid in speaking:
        return "speaking"
    observing = [_canonical_id(x) for x in (current.get("observing_character_ids") or [])]
    observing += [_canonical_id(x) for x in (scene_plan.get("observing_characters") or [])]
    if cid in observing:
        return "observing"
    present = [_canonical_id(x) for x in (current.get("present_character_ids") or [])]
    present += [_canonical_id(x) for x in (scene_plan.get("present_characters") or [])]
    if cid in present:
        return "present_reaction"
    return "referenced"


def _visible_labels_for(cid: str, current: dict[str, Any]) -> dict[str, str]:
    visible_start = current.get("visible_relationships_start") if isinstance(current.get("visible_relationships_start"), dict) else {}
    for_pov = DEFAULT_VISIBLE_LABELS.get(cid, cid)
    if cid in visible_start and isinstance(visible_start[cid], dict):
        for_pov = str(visible_start[cid].get("visible_label") or for_pov)
    return {
        "for_pov_akira": for_pov,
        "for_emma": "мужчина / хозяин дома" if cid == "jun" else DEFAULT_VISIBLE_LABELS.get(cid, cid),
        "for_irey": "мужчина / хозяин дома" if cid == "jun" else DEFAULT_VISIBLE_LABELS.get(cid, cid),
        "rule": "Use visible descriptor unless the speaking character has an in-scene/source-backed name permission.",
    }


def _character_sources(sid: str, cid: str, *, include_memory: bool = True) -> tuple[str, str, str, dict[str, Any]]:
    char_text = _read_text(f"characters/{cid}/character.yaml", sid)
    know_text = _read_text(f"characters/{cid}/knowledge.yaml", sid)
    main_text = _read_text(f"characters/{cid}/main.yaml", sid)
    memory = _read_json(f"state/character_memory/{cid}.json", sid, {}) if include_memory else {}
    if not isinstance(memory, dict):
        memory = {}
    return char_text, know_text, main_text, memory


def _character_source_audit(sid: str, cid: str) -> dict[str, Any]:
    required = [
        f"characters/{cid}/main.yaml",
        f"characters/{cid}/character.yaml",
        f"characters/{cid}/knowledge.yaml",
    ]
    missing = [path for path in required if not _read_text(path, sid).strip()]
    return {
        "character_id": cid,
        "valid": not missing,
        "required_sources": required,
        "dynamic_memory_source": f"state/character_memory/{cid}.json",
        "missing_sources": missing,
    }


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _goal_override(cid: str) -> list[str]:
    if cid == "emma":
        return [
            "External goal: get Akira to the hidden customer/system; pressure first, do not become exposition tool.",
            "Emma believes Irey is moving in the same task, but does not know his true personal/protective goal.",
            "Emma may bluff or misread; confidence is not proof of knowledge.",
        ]
    if cid == "irey":
        return [
            "External cover: appears connected to the same search/task as Emma.",
            "Hidden personal goal: protect Akira and prevent the system/Samuel line from taking her if possible.",
            "If Akira looks at him as a stranger, he must register it: pause, test, question, misread or hide the reaction.",
        ]
    if cid == "jun":
        return ["Current goal: delay, protect Akira, keep the downstairs pressure away from her as long as possible."]
    if cid == "akira":
        return ["POV: player controls important words/choices; show guarded body, silence, microreaction, not invented confessions."]
    return []


def _knowledge_guard_override(cid: str) -> dict[str, list[str]]:
    if cid == "emma":
        return {
            "knows": ["Akira is needed by the hidden customer/system; Akira's trace was found near the house."],
            "beliefs": ["Emma reads Irey's reactions as too personal; this is her interpretation, not proof of his motive."],
            "does_not_know": ["Does not know engine:jun by name; does not know he hid/raised/protected Akira; does not know Akira's amnesia; does not know Ray/Raiden connection."],
            "speech_guard": ["Must not say 'Джун' or 'Картер' until an in-scene source gives the name."],
        }
    if cid == "irey":
        return {
            "knows": ["Knows more about Akira than Emma; has a personal/protective motive; can notice body/recognition mismatch."],
            "beliefs": ["Until the scene disproves it, Irey may assume Akira remembers him; an assumption is not a fact."],
            "does_not_know": ["Does not know engine:jun by name at start unless a played source gives it; does not know exact last two years; does not know what Akira currently remembers."],
            "speech_guard": ["Must refer to engine:jun as 'мужчина', 'хозяин дома', 'тот, кто её прятал' until name source."],
            "ability_boundary": ["Intentional touch may give Irey a body/sensory response defined by his card; it never reveals another person's thoughts or memories."],
        }
    if cid == "akira":
        return {
            "knows": ["Remembers the last two years with Jun and current visible objects; does not automatically know hidden lore or strangers' names."],
            "beliefs": [],
            "does_not_know": ["Does not know Emma/Irey names at start; does not know East Sector structure, Kairos/Echo terms, Raiden/Ray/Samuel history unless source appears."],
            "speech_guard": ["If player did not write Akira's speech outside parentheses, do not invent important Akira speech."],
        }
    return {"knows": [], "beliefs": [], "does_not_know": [], "speech_guard": [], "ability_boundary": []}


def _player_control_boundary(cid: str, role: str) -> dict[str, Any]:
    if role == "pov":
        return {"mode": "current_pov_player_controlled"}
    if cid == "akira":
        return {
            "mode": "non_pov_low_stakes_scene_continuity",
            "allowed_without_player_input": ["brief factual/neutral answer"],
            "must_wait_for_player": ["meaningful yes/no"],
            "state_rule": "Never use npc_autonomy_updates.",
        }
    return {"mode": "npc"}


def _character_core_card(sid: str, cid: str, role: str, needs: dict[str, bool], current: dict[str, Any]) -> dict[str, Any]:
    memory_loaded = role in ACTIVE_MEMORY_ROLES
    char_text, know_text, main_text, memory = _character_sources(sid, cid, include_memory=memory_loaded)
    memory_buckets = _memory_buckets(memory) if memory_loaded else {key: [] for key in MEMORY_FIELD_ALIASES}
    line_boost = 11 if role == "pov" else 8
    if needs.get("dialogue_or_pressure"):
        line_boost += 2
    identity_lines = _matching_lines(main_text + "\n" + char_text, ("возраст", "рост", "волос", "глаз", "внеш", "appearance", "height", "age"), max_lines=6 if needs.get("deep_appearance") else 3, max_chars=500)
    return {
        "id": cid,
        "role_in_scene": role,
        "identity_brief": IDENTITY_OVERRIDES.get(cid) or _trim("; ".join(identity_lines), 500) or f"{cid}: use loaded card only; do not invent appearance.",
        "visible_labels": _visible_labels_for(cid, current),
        "current_goal_priority": _bounded_lines((
            memory_buckets["goals"]
            + _goal_override(cid)
            + _matching_lines(main_text + "\n" + char_text, CHARACTER_PATTERNS["goal"], max_lines=6, max_chars=700)
        ), max_items=8, max_chars=1000),
        "decision_constraints": {
            "orders_and_agreements": _bounded_lines(memory_buckets["agreements"], max_items=4, max_chars=420),
            "current_limitations": _bounded_lines(memory_buckets["limitations"], max_items=4, max_chars=420),
            "unresolved_hooks": _bounded_lines(memory_buckets["hooks"], max_items=4, max_chars=500),
            "internal_only_pressure": _bounded_lines(memory_buckets["hides"], max_items=3, max_chars=420),
        },
        "voice_behavior_habits": _matching_lines(char_text, CHARACTER_PATTERNS["voice"] + CHARACTER_PATTERNS["behavior"], max_lines=line_boost, max_chars=1200),
        "must_react_to_now": _matching_lines(char_text + "\n" + know_text, CHARACTER_PATTERNS["reaction"], max_lines=line_boost, max_chars=1200),
        "response_obligation": (
            {"required": role == "addressed", "mode": "akira_low_stakes_reply_or_hold"}
            if cid == "akira" and role != "pov"
            else _response_obligation(role)
        ),
        "player_control_boundary": _player_control_boundary(cid, role),
        "player_control_or_npc_rule": (
            "POV"
            if role == "pov"
            else (
                "Akira"
                if cid == "akira"
                else "NPC: each line must come from goal + visible source + knowledge/unknown boundary."
            )
        ),
        "energy_loaded": bool(needs.get("energy")),
        "energy_note": "Energy is omitted in this chunk because the scene did not request/trigger energy." if not needs.get("energy") else "Energy details are in energy_lore chunk.",
        "dynamic_memory_loaded": memory_loaded,
        "source_files_used": [
            f"characters/{cid}/main.yaml",
            f"characters/{cid}/character.yaml",
            f"characters/{cid}/knowledge.yaml",
        ] + ([f"state/character_memory/{cid}.json"] if memory_loaded else []),
    }


def _character_knowledge_card(sid: str, cid: str, role: str, needs: dict[str, bool]) -> dict[str, Any]:
    memory_loaded = role in ACTIVE_MEMORY_ROLES
    char_text, know_text, main_text, memory = _character_sources(sid, cid, include_memory=memory_loaded)
    memory_buckets = _memory_buckets(memory) if memory_loaded else {key: [] for key in MEMORY_FIELD_ALIASES}
    static_buckets = _static_knowledge_buckets(know_text)
    guard = _knowledge_guard_override(cid)
    line_boost = 10 if role in {"pov", "speaking", "addressed"} else 7
    confirmed = (
        guard.get("knows", [])
        + static_buckets["facts"]
        + _confirmed_knowledge_lines(know_text, max_lines=line_boost, max_chars=1100)
        + memory_buckets["facts"]
    )
    return {
        "id": cid,
        "role_in_scene": role,
        "known_as_fact": _bounded_lines(confirmed, max_items=12, max_chars=1500),
        "direct_observations": _bounded_lines(memory_buckets["observed"], max_items=7, max_chars=700),
        "heard_or_reported": _bounded_lines(memory_buckets["heard"], max_items=7, max_chars=700),
        "beliefs_and_suspicions": _bounded_lines((
            guard.get("beliefs", [])
            + static_buckets["beliefs"]
            + _matching_lines(know_text, ("предполага", "подозр", "assum", "suspect", "may_believe", "may believe"), max_lines=6, max_chars=650)
            + memory_buckets["beliefs"]
        ), max_items=8, max_chars=900),
        "known_mistakes": _bounded_lines((
            _matching_lines(know_text, ("ошибочно", "wrongly", "misbeliev", "mistaken"), max_lines=5, max_chars=500)
            + static_buckets["mistakes"]
            + memory_buckets["mistakes"]
        ), max_items=6, max_chars=600),
        "unknown_or_forbidden": _bounded_lines((
            guard.get("does_not_know", [])
            + static_buckets["unknowns"]
            + _matching_lines(know_text, KNOWLEDGE_PATTERNS["unknowns"], max_lines=line_boost, max_chars=1100)
            + memory_buckets["unknowns"]
        ), max_items=12, max_chars=1500),
        "actively_hidden_internal_only": _bounded_lines((
            _matching_lines(know_text, KNOWLEDGE_PATTERNS["hides"], max_lines=line_boost, max_chars=800)
            + static_buckets["hides"]
            + memory_buckets["hides"]
        ), max_items=8, max_chars=850),
        "recent_evidence_ledger": _memory_event_ledger(memory, max_items=3) if memory_loaded else [],
        "speech_and_name_guard": guard.get("speech_guard", []) or ["Use global_name_rules plus this character's source-backed name permissions."],
        "ability_knowledge_boundary": guard.get("ability_boundary", []),
        "unknowns_are_active_rule": "Unknowns should create questions, checks, pauses, pressure, evasion, bluffing or wrong assumptions — not omniscience and not silence.",
        "dynamic_memory_loaded": memory_loaded,
        "source_files_used": [f"characters/{cid}/knowledge.yaml"] + ([f"state/character_memory/{cid}.json"] if memory_loaded else []),
    }


def _energy_card(sid: str, cids: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for cid in cids:
        char_text, know_text, _main_text, _memory = _character_sources(sid, cid, include_memory=False)
        ability = _matching_lines(char_text + "\n" + know_text, CHARACTER_PATTERNS["ability"], max_lines=7, max_chars=900)
        if ability:
            result[cid] = ability
    return {"energy_loaded_for": list(result.keys()), "characters": result, "rule": "Use only if energy is visible, used, sensed, discussed or mechanically relevant this turn."}


def _pair_parts(value: Any) -> tuple[str, str] | None:
    parts = [part for part in str(value or "").strip().split("__") if part]
    if len(parts) != 2:
        return None
    left, right = _canonical_id(parts[0]), _canonical_id(parts[1])
    if not left or not right or left == right:
        return None
    return left, right


def _relationship_registry(sid: str) -> dict[str, str]:
    index = _read_json(RELATIONSHIP_INDEX_FILE, sid, {})
    files = index.get("files") if isinstance(index, dict) and isinstance(index.get("files"), dict) else {}
    return {
        str(pair_id): str(path)
        for pair_id, path in files.items()
        if _pair_parts(pair_id) and str(path or "").startswith("state/relationship_pairs/")
    }


def _resolve_pair_id(value: Any, registry: dict[str, str]) -> str:
    parts = _pair_parts(value)
    if not parts:
        return ""
    direct = f"{parts[0]}__{parts[1]}"
    reverse = f"{parts[1]}__{parts[0]}"
    if direct in registry:
        return direct
    if reverse in registry:
        return reverse
    return ""


def _list_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _select_relationship_pairs(
    sid: str,
    current: dict[str, Any],
    payload: dict[str, Any],
    scene_plan: dict[str, Any],
    cids: list[str],
    roles: dict[str, str],
) -> tuple[list[str], dict[str, Any]]:
    registry = _relationship_registry(sid)
    active = {cid for cid in cids if roles.get(cid) in ACTIVE_MEMORY_ROLES}
    explicit_raw: list[Any] = []
    for container in (payload, scene_plan, current):
        if not isinstance(container, dict):
            continue
        for key in ("relationship_focus_pair_ids", "relevant_relationship_pair_ids"):
            explicit_raw.extend(_list_values(container.get(key)))
    pov = _canonical_id(current.get("pov_character_id"))
    for container in (payload, scene_plan, current):
        if not isinstance(container, dict) or not pov:
            continue
        for target in _list_values(container.get("thinking_about_character_ids")):
            target_id = _canonical_id(target)
            if target_id and target_id != pov:
                explicit_raw.append(f"{pov}__{target_id}")

    explicit = []
    for raw in explicit_raw:
        resolved = _resolve_pair_id(raw, registry)
        if resolved and resolved not in explicit:
            explicit.append(resolved)

    current_candidates = []
    for container in (current, payload, scene_plan):
        if not isinstance(container, dict):
            continue
        for raw in _list_values(container.get("relationship_pair_ids")):
            resolved = _resolve_pair_id(raw, registry)
            if resolved and resolved not in current_candidates:
                current_candidates.append(resolved)

    ordered = explicit + current_candidates + [pair_id for pair_id in registry if pair_id not in explicit and pair_id not in current_candidates]
    selected: list[str] = []
    reasons: dict[str, str] = {}
    excluded: dict[str, str] = {}
    for pair_id in ordered:
        if pair_id in selected or pair_id in excluded:
            continue
        parts = _pair_parts(pair_id)
        if not parts:
            continue
        left, right = parts
        both_active = left in active and right in active
        one_active_explicit = pair_id in explicit and (left in active or right in active)
        if both_active:
            selected.append(pair_id)
            reasons[pair_id] = "both_participants_scene_active"
        elif one_active_explicit:
            selected.append(pair_id)
            reasons[pair_id] = "explicit_pair_focus_or_active_character_thinks_about_other"
        else:
            excluded[pair_id] = "not_both_active_and_not_explicit_pair_focus"
        if len(selected) >= 8:
            break
    omitted_relevant = [
        pair_id for pair_id in ordered
        if pair_id not in selected and pair_id not in excluded
    ]
    return selected, {
        "loaded_pair_ids": selected,
        "relevance_reason": reasons,
        "excluded_pair_ids": excluded,
        "omitted_relevant_pair_ids": omitted_relevant,
        "rule": "Load a pair only when both participants are scene-active, or when an active character explicitly thinks/speaks about that pair. POV alone never loads every pair.",
    }


def _relationship_cards(sid: str, pair_ids: list[str], selection: dict[str, Any]) -> dict[str, Any]:
    registry = _relationship_registry(sid)
    reasons = selection.get("relevance_reason") if isinstance(selection.get("relevance_reason"), dict) else {}
    result: dict[str, Any] = {}
    for pid in pair_ids[:8]:
        path = registry.get(pid)
        parts = _pair_parts(pid)
        if not path or not parts:
            continue
        left, right = parts
        data = _read_json(path, sid, {})
        if not isinstance(data, dict) or not data:
            continue
        directional = {
            key: value
            for key, value in data.items()
            if isinstance(key, str) and "_to_" in key and isinstance(value, dict)
        }
        events = data.get("relationship_events") if isinstance(data.get("relationship_events"), list) else []
        result[pid] = {
            "pair_id": pid,
            "participants": data.get("participants") or [left, right],
            "source_file": path,
            "relevance_reason": reasons.get(pid),
            "surface_dynamic": _compact(data.get("surface_dynamic", {}), max_chars=650, max_items=8, depth=2),
            "metrics": _compact(data.get("metrics", {}), max_chars=300, max_items=8, depth=1),
            "directional_state_internal_only": _compact(directional, max_chars=900, max_items=4, depth=2),
            "knowledge_boundaries": _compact(data.get("knowledge_boundaries", {}), max_chars=650, max_items=6, depth=2),
            "shared_memory": _compact(data.get("shared_memory") or data.get("memory") or [], max_chars=600, max_items=6, depth=2),
            "recent_relationship_events": _compact(events[-6:], max_chars=700, max_items=6, depth=2),
            "protected_moments": _compact(data.get("protected_moments", [])[-4:] if isinstance(data.get("protected_moments"), list) else [], max_chars=650, max_items=4, depth=2),
            "last_interaction": _compact(data.get("last_interaction", {}), max_chars=500, max_items=6, depth=2),
            "writer_rule": "Let this pair shape distance, interruption, trust and misreading. Internal state is not narrator/POV knowledge; do not force romance, forgiveness or obedience.",
        }
    return result


def _current_state_slice(current: dict[str, Any]) -> dict[str, Any]:
    return {
        "current_scene_id": current.get("current_scene_id") or current.get("scene_id"),
        "current_datetime": current.get("current_datetime"),
        "current_date": current.get("current_date") or current.get("date"),
        "current_time": current.get("current_time"),
        "current_day_phase": current.get("current_day_phase") or current.get("time_of_day"),
        "elapsed_world_minutes": int(current.get("elapsed_world_minutes") or 0),
        "current_location_id": current.get("current_location_id") or current.get("location_id"),
        "current_location_text": current.get("current_location_text") or current.get("location_text"),
        "pov_character_id": current.get("pov_character_id"),
        "active_character_ids": current.get("active_character_ids", []),
        "scene_character_ids": current.get("scene_character_ids", []),
        "present_character_ids": current.get("present_character_ids", []),
        "speaking_character_ids": current.get("speaking_character_ids", []),
        "addressed_character_ids": current.get("addressed_character_ids", []),
        "observing_character_ids": current.get("observing_character_ids", []),
        "remote_contact_character_ids": current.get("remote_contact_character_ids", []),
        "conditional_character_ids": current.get("conditional_character_ids", []),
        "relationship_pair_ids": current.get("relationship_pair_ids", []),
        "relationship_focus_pair_ids": current.get("relationship_focus_pair_ids", []),
        "thinking_about_character_ids": current.get("thinking_about_character_ids", []),
        "visible_inventory": _compact(current.get("visible_inventory", []), max_chars=450, max_items=8, depth=2),
        "nearby_items": _compact(current.get("nearby_items", []), max_chars=450, max_items=8, depth=2),
        "scene_goal": _trim(current.get("scene_goal") or current.get("current_scene_goal"), 500),
        "last_player_input": _trim(current.get("last_player_input"), 450),
        "start_scene_exact_text_required": bool(current.get("start_scene_exact_text_required")),
        "start_scene_completed": bool(current.get("start_scene_completed")),
        "boundary_note": "Inventory/state are not NPC knowledge unless the NPC saw/heard/source confirms it.",
    }


def _location_zone(value: Any) -> str:
    location = str(value or "").strip().lower()
    for prefix in ("jun_house", "east_sector", "east_coast", "off_base"):
        if location.startswith(prefix):
            return prefix
    return location.split("_", 1)[0] if location else "unknown"


def _calendar_slice(sid: str, current: dict[str, Any]) -> dict[str, Any]:
    runtime = _read_json(CALENDAR_RUNTIME_FILE, sid, {})
    if not isinstance(runtime, dict):
        runtime = {}
    schedule_rules = _read_json(SCHEDULE_AVAILABILITY_FILE, sid, {})
    if not isinstance(schedule_rules, dict):
        schedule_rules = {}
    events = runtime.get("pending_events") if isinstance(runtime.get("pending_events"), list) else []
    active_events = [
        item for item in events
        if isinstance(item, dict) and str(item.get("status") or "") not in {"resolved", "cancelled"}
    ][:8]
    npc_autonomy = runtime.get("npc_autonomy") if isinstance(runtime.get("npc_autonomy"), dict) else {}
    compact_autonomy: dict[str, Any] = {}
    for cid, state in list(npc_autonomy.items())[:16]:
        if not isinstance(state, dict):
            continue
        compact_autonomy[str(cid)] = {
            "location_id": state.get("location_id"),
            "location_zone": _location_zone(state.get("location_id")),
            "activity": state.get("activity"),
            "activity_category": state.get("activity_category"),
            "availability": state.get("availability"),
            "since": state.get("since"),
            "destination_location_id": state.get("destination_location_id"),
            "earliest_arrival_at": state.get("earliest_arrival_at"),
            "busy_until": state.get("busy_until"),
            "delay_reason": state.get("delay_reason"),
        }
    decision_due = [
        str(item.get("character_id"))
        for item in active_events
        if item.get("character_id") and str(item.get("status") or "") in {"triggered", "decision_due"}
    ]
    try:
        clock_now = datetime.fromisoformat(str(runtime.get("current_datetime") or ""))
    except ValueError:
        clock_now = None
    if clock_now:
        for cid, state in compact_autonomy.items():
            due_reason = ""
            for field, reason in (
                ("earliest_arrival_at", "travel_eta_reached_choose_arrive_or_delay"),
                ("busy_until", "busy_window_ended_choose_next_activity"),
            ):
                raw_due = state.get(field)
                if not raw_due:
                    continue
                try:
                    due = datetime.fromisoformat(str(raw_due))
                except ValueError:
                    continue
                if due <= clock_now:
                    due_reason = reason
                    break
            if due_reason:
                state["decision_due_reason"] = due_reason
                decision_due.append(str(cid))
    current_day_file = str(runtime.get("current_day_file") or "")
    current_day_text = _read_text(current_day_file, sid) if current_day_file.startswith("calendar/days/") else ""
    return {
        "current_datetime": runtime.get("current_datetime") or current.get("current_datetime"),
        "current_date": runtime.get("current_date") or current.get("current_date"),
        "current_time": runtime.get("current_time") or current.get("current_time"),
        "current_day_phase": runtime.get("current_day_phase") or current.get("current_day_phase"),
        "elapsed_world_minutes": int(runtime.get("elapsed_world_minutes") or current.get("elapsed_world_minutes") or 0),
        "time_revision": int(runtime.get("time_revision") or 0),
        "current_day_file": current_day_file or None,
        "current_day_writer_only_excerpt": _trim(current_day_text, 1500),
        "current_day_scope_rule": "This is the only calendar day file loaded in normal play. It pressures the world/writer and is never NPC knowledge.",
        "current_beat_id": runtime.get("current_beat_id") or current.get("current_beat_id"),
        "active_events": _compact(active_events, max_chars=2800, max_items=8, depth=3),
        "decision_due_character_ids": list(dict.fromkeys(decision_due)),
        "npc_autonomy": _compact(compact_autonomy, max_chars=5200, max_items=16, depth=3),
        "recent_world_consequences": _compact(runtime.get("world_consequences", [])[-6:] if isinstance(runtime.get("world_consequences"), list) else [], max_chars=1000, max_items=6, depth=2),
        "staff_observations": _compact(runtime.get("staff_observations", [])[-5:] if isinstance(runtime.get("staff_observations"), list) else [], max_chars=900, max_items=5, depth=2),
        "schedule_policy": {
            "raider_accountability": _compact(schedule_rules.get("raider_accountability", {}), max_chars=1700, max_items=10, depth=3),
            "time_advance": _compact(schedule_rules.get("time_advance", {}), max_chars=1200, max_items=8, depth=3),
            "travel_and_presence": _compact(schedule_rules.get("travel_and_presence", {}), max_chars=1200, max_items=8, depth=3),
        },
        "rules": _compact(runtime.get("rules", []), max_chars=900, max_items=8, depth=2),
        "writer_rules": [
            "Clock and NPC activity are immutable for this turn snapshot. Propose evidence-backed time_advance/event_updates/npc_autonomy_updates; never edit clock fields directly.",
            "An NPC who is elsewhere, busy, sleeping or in transit may be unavailable, delayed or refuse. Do not teleport them into the scene.",
            "Offscreen state is writer/engine context, not character knowledge. An absent NPC does not learn the scene.",
            "A missed event may change the world or another NPC; it never supplies an unplayed action, consent, thought or motive for Akira.",
        ],
    }


def _presence_gate(
    sid: str,
    cid: str,
    role: str,
    current: dict[str, Any],
    scene_plan: dict[str, Any],
    calendar_context: dict[str, Any],
) -> tuple[bool, str]:
    if role in {"pov", "referenced", "offscreen_decision"}:
        return True, "role_does_not_claim_local_presence"
    committed = _read_json(CURRENT_STATE_FILE, sid, {})
    committed_present = {
        _canonical_id(value)
        for value in (committed.get("present_character_ids") or [])
    } if isinstance(committed, dict) else set()
    remote_ids: list[str] = []
    for source in (current, scene_plan):
        values = source.get("remote_contact_character_ids") or source.get("contacted_character_ids") or []
        if isinstance(values, list):
            remote_ids.extend(_canonical_id(value) for value in values)
    if cid in remote_ids:
        return True, "explicit_remote_contact"
    autonomy = calendar_context.get("npc_autonomy") if isinstance(calendar_context.get("npc_autonomy"), dict) else {}
    state = autonomy.get(cid) if isinstance(autonomy.get(cid), dict) else {}
    if not state:
        return (cid in committed_present, "no_autonomy_state_and_not_already_present")
    availability = str(state.get("availability") or "unknown")
    if availability in {"in_transit", "unavailable", "off_base", "sleeping", "unavailable_until_1206-09-21"}:
        return False, f"availability={availability}"
    scene_zone = _location_zone(current.get("current_location_id") or current.get("location_id"))
    npc_zone = _location_zone(state.get("location_id"))
    if cid not in committed_present and scene_zone != "unknown" and npc_zone not in {"unknown", scene_zone}:
        return False, f"npc_zone={npc_zone};scene_zone={scene_zone}"
    if cid not in committed_present and availability not in {"present", "nearby", "available"}:
        return False, f"availability={availability};arrival_not_committed"
    return True, "present_or_arrival_already_committed"


def _history_slice(sid: str, depth: int = 4) -> list[dict[str, Any]]:
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
                "player_input": _trim(item.get("player_input"), 160),
                "summary": _trim(item.get("summary") or item.get("visible_scene_text") or item.get("scene_text"), 450),
            })
    return out


def _location_slice(current: dict[str, Any], scene_plan: dict[str, Any], needs: dict[str, bool]) -> dict[str, Any]:
    return {
        "location_id": scene_plan.get("location_id") or current.get("current_location_id") or current.get("location_id"),
        "location_text": current.get("current_location_text") or current.get("location_text"),
        "requested_depth": "short_rules" if not needs.get("deep_appearance") else "visual_plus_rules",
        "rule": "Use explicit location/current_state. Do not invent background NPCs or noises without source.",
    }


def _inventory_slice(current: dict[str, Any], needs: dict[str, bool]) -> dict[str, Any]:
    if not needs.get("inventory"):
        return {"loaded": False, "note": "Inventory not triggered beyond current_state visible_inventory/nearby_items."}
    return {
        "loaded": True,
        "visible_inventory": _compact(current.get("visible_inventory", []), max_chars=700, max_items=12, depth=2),
        "nearby_items": _compact(current.get("nearby_items", []), max_chars=700, max_items=12, depth=2),
        "inventory_state": _compact(current.get("inventory_state", {}), max_chars=900, max_items=8, depth=2),
        "rule": "A taken item is visible to Akira, but not automatically known to NPCs unless they saw/heard it.",
    }


def _lore_slice(sid: str, needs: dict[str, bool]) -> dict[str, Any]:
    if not needs.get("lore"):
        return {"loaded": False, "note": "Lore omitted: no current trigger."}
    # Keep this intentionally tiny; character knowledge still controls disclosure.
    files = [
        "canon_lore/core/01_world_public_history_ru.yaml",
        "canon_lore/core/02_kairos_public_ru.yaml",
        "canon_lore/core/03_energy_public_rules_ru.yaml",
    ]
    result = []
    for path in files:
        text = _read_text(path, sid)
        if text:
            result.append({"path": path, "excerpt": _trim(text, 800)})
    return {"loaded": True, "files": result[:3], "rule": "Lore is engine context, not automatic NPC knowledge."}


def _past_memory_slice(sid: str, cids: list[str], contract: dict[str, Any]) -> dict[str, Any]:
    raw_terms = contract.get("past_trigger_terms") if isinstance(contract.get("past_trigger_terms"), list) else []
    terms = tuple(
        str(term).lower().replace("ё", "е")
        for term in raw_terms
        if str(term) != "current_state_explicit_past_trigger"
    )
    result: dict[str, Any] = {}
    if terms:
        for cid in cids:
            path = f"characters/{cid}/past.yaml"
            text = _read_text(path, sid)
            lines = _matching_lines(text, terms, max_lines=10, max_chars=1400) if text else []
            if lines:
                result[cid] = {
                    "source_file": path,
                    "trigger_terms": list(terms),
                    "selected_lines": lines,
                }
    return {
        "past_loaded_for": list(result.keys()),
        "past_slices": result,
        "trigger_terms": raw_terms,
        "diagnostic": None if result else "Past trigger existed, but no narrow matching source lines were found; full past.yaml was not exposed.",
        "rule": "Only trigger-matched lines are available. Do not infer neighboring hidden facts or turn this into an exposition dump.",
    }


def _extract_start_scene_text() -> str:
    text = _read_text(START_SCENE_PATH)
    match = re.search(r"```text\s*(.*?)```", text, flags=re.S)
    return match.group(1).strip() if match else text.strip()


def _render_contract_small() -> dict[str, Any]:
    data = _read_json(RENDER_CONTRACT_PATH, "default", {})
    if not isinstance(data, dict) or not data:
        return {
            "dialogue_format": "**Имя/видимый дескриптор** — реплика.",
            "pov_rule": "Respect POV knowledge and player control.",
            "bottom_blocks": ["Что можно сделать", "Что Акира могла бы сказать", "Мысли Акиры", "Состояние"],
            "unknown_names_rule": "Engine-known id is not visible name permission.",
            "player_character_rule": "current POV keeps normal player-choice protection.",
        }
    return {
        "source_file": RENDER_CONTRACT_PATH,
        "must_be_last_writer_instruction": True,
        "dialogue_format_required": data.get("dialogue_format_required") or "**Имя/видимый дескриптор** — реплика.",
        "time_header_rule": "Render the frozen current_time/current_day_phase; do not invent a later header before applyTurnResult validates time_advance.",
        "unknown_names_rule": "If POV/speaker does not know a name, use visible descriptor, not engine id/display_name.",
        "autonomy_rule": "Presence/arrival/delay must match npc_autonomy and ETA. Propose time_advance/event_updates/npc_autonomy_updates when they changed.",
        "missed_event_rule": "World/NPC consequences are allowed; unplayed Akira actions, thoughts, consent and motives are forbidden.",
        "player_character_rule": "current POV keeps normal player-choice protection.",
        "bottom_blocks_rule": "Keep choice/options/status blocks; do not expose hidden lore as POV thoughts.",
    }


def _build_turn_contract(sid: str, payload: dict[str, Any]) -> dict[str, Any]:
    current = _ensure_current(sid)
    pending = base.get_pending_turn(sid)
    if not pending:
        if current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"):
            return _turn_contract_error(
                sid,
                current,
                "No gameplay turn is pending. The canonical start scene must be shown first.",
                "getStartSceneText",
            )
        return _turn_contract_error(
            sid,
            current,
            "No gameplay turn is pending. Call processTurn with the latest non-empty player input.",
            "waitForPlayerInput",
        )
    supplied_turn_id = _payload_turn_id(payload)
    if not supplied_turn_id:
        return _turn_contract_error(
            sid,
            current,
            "turn_id is required. Use the exact value returned by processTurn.",
            "getTurnContract",
        )
    if supplied_turn_id != str(pending.get("turn_id") or ""):
        return _turn_contract_error(
            sid,
            current,
            "turn_id does not match the protected pending turn; stale context was rejected.",
            "getTurnContract",
        )
    scene_plan = dict(payload.get("scene_plan")) if isinstance(payload.get("scene_plan"), dict) else {}
    for key in (
        "speaking_characters", "addressed_characters", "present_characters", "observing_characters",
        "character_ids", "characters", "character_requests", "relationship_pair_ids",
        "relationship_focus_pair_ids", "thinking_about_character_ids",
        "remote_contact_character_ids", "contacted_character_ids",
    ):
        if key not in scene_plan and key in payload:
            scene_plan[key] = payload[key]
    player_input = str(pending.get("player_input") or "")
    needs = _needs(payload, current, scene_plan, player_input)
    past_trigger_terms = _past_trigger_terms(current, scene_plan, player_input)
    pov_id = _canonical_id(current.get("pov_character_id"))
    if not pov_id:
        error = _turn_contract_error(
            sid,
            current,
            "current_state has no pov_character_id. The builder will not insert Akira as a fallback.",
            "getPreflight",
        )
        error["builder_diagnostics"] = [{
            "severity": "error",
            "fallback_blocked": "default_protagonist_insertion",
            "reason": "POV is missing from active state.",
            "needed_input": "Set an explicit pov_character_id through processTurn/current state.",
        }]
        return error

    calendar_context = _calendar_slice(sid, current)
    requested_cids = _collect_character_ids(payload, current, scene_plan, player_input)
    for cid in calendar_context.get("decision_due_character_ids", []):
        _add_id(requested_cids, cid)
    source_audit = {cid: _character_source_audit(sid, cid) for cid in requested_cids}
    if pov_id not in source_audit:
        source_audit[pov_id] = _character_source_audit(sid, pov_id)
        requested_cids.insert(0, pov_id)
    if not source_audit[pov_id]["valid"]:
        error = _turn_contract_error(
            sid,
            current,
            f"POV character '{pov_id}' is missing required full-card sources.",
            "getPreflight",
        )
        error["builder_diagnostics"] = [{
            "severity": "error",
            "fallback_blocked": "summary_as_behavior_source",
            "reason": f"POV '{pov_id}' cannot be built from main/character/knowledge files.",
            "missing_sources": source_audit[pov_id]["missing_sources"],
            "needed_input": "Restore the full character card or select a valid explicit POV.",
        }]
        return error

    invalid_cids = [cid for cid in requested_cids if not source_audit[cid]["valid"]]
    valid_cids = [cid for cid in requested_cids if source_audit[cid]["valid"]]
    diagnostics: list[dict[str, Any]] = []
    if invalid_cids:
        diagnostics.append({
            "severity": "warning",
            "fallback_blocked": "summary_as_behavior_source",
            "reason": "Characters without complete full-card sources were excluded.",
            "character_ids": invalid_cids,
            "missing_sources": {cid: source_audit[cid]["missing_sources"] for cid in invalid_cids},
        })
    candidate_roles = {cid: _role_for(cid, current, scene_plan) for cid in valid_cids}
    for cid in calendar_context.get("decision_due_character_ids", []):
        canonical = _canonical_id(cid)
        if canonical in candidate_roles and candidate_roles[canonical] == "referenced":
            candidate_roles[canonical] = "offscreen_decision"
    inferred_all = _infer_direct_addressed_ids(player_input, valid_cids)
    for cid in inferred_all:
        if cid != pov_id:
            candidate_roles[cid] = "addressed"

    available_cids: list[str] = []
    blocked_presence: list[dict[str, str]] = []
    for cid in valid_cids:
        allowed, reason = _presence_gate(
            sid, cid, str(candidate_roles.get(cid) or "referenced"), current, scene_plan, calendar_context
        )
        if allowed:
            available_cids.append(cid)
        else:
            candidate_roles[cid] = "referenced"
            available_cids.append(cid)
            blocked_presence.append({"character_id": cid, "reason": reason})
    cids = available_cids[:7]
    omitted_cids = available_cids[7:]
    if blocked_presence:
        diagnostics.append({
            "severity": "warning",
            "fallback_blocked": "npc_teleport_or_unavailable_presence",
            "reason": "Characters whose committed location/availability cannot support this scene were downgraded to referenced-only context.",
            "characters": blocked_presence,
            "needed_input": "Play or apply a valid travel/arrival/contact transition first; an unavailable NPC may instead delay or refuse.",
        })
    if omitted_cids:
        diagnostics.append({
            "severity": "warning",
            "fallback_blocked": "unbounded_character_dump",
            "reason": "Context is bounded to seven relevant full-card characters for one turn.",
            "omitted_character_ids": omitted_cids,
            "needed_input": "Delay them or make their relevance explicit in a later turn.",
        })
    roles = {cid: candidate_roles[cid] for cid in cids}
    inferred_addressed = _infer_direct_addressed_ids(player_input, cids)
    blocked_presence_ids = {item["character_id"] for item in blocked_presence}
    for cid in inferred_addressed:
        if cid != pov_id and cid not in blocked_presence_ids:
            roles[cid] = "addressed"
    memory_character_ids = [cid for cid in cids if roles.get(cid) in ACTIVE_MEMORY_ROLES]
    relationship_pair_ids, relationship_selection = _select_relationship_pairs(
        sid, current, payload, scene_plan, cids, roles
    )
    if relationship_selection.get("omitted_relevant_pair_ids"):
        diagnostics.append({
            "severity": "warning",
            "fallback_blocked": "unbounded_relationship_dump",
            "reason": "Relationship context is bounded to eight relevant pairs for one turn.",
            "omitted_pair_ids": relationship_selection["omitted_relevant_pair_ids"],
        })
    character_groups = [cids[index:index + 4] for index in range(0, len(cids), 4)]
    chunks: list[dict[str, Any]] = []
    for group in character_groups:
        chunks.append({
            "chunk_index": len(chunks), "chunk_type": "characters_core", "contains": group,
            "why": "bounded identity + voice + behavior + goals; energy omitted unless triggered",
        })
    for group in character_groups:
        chunks.append({
            "chunk_index": len(chunks), "chunk_type": "knowledge_boundaries", "contains": group,
            "why": "bounded facts/observations/reports/beliefs/unknowns; calendar is not NPC knowledge",
        })
    chunks.extend([
        {"chunk_index": len(chunks), "chunk_type": "state_relationships_memory", "contains": ["current_state", "recent_history", *relationship_pair_ids], "why": "continuity plus only scene-relevant relationship pressure"},
        {"chunk_index": len(chunks) + 1, "chunk_type": "location_inventory_calendar_render", "contains": ["location", "inventory_if_needed", "calendar", "render_contract"], "why": "scene mechanics and final writer rules"},
    ])
    if needs.get("energy"):
        chunks.append({"chunk_index": len(chunks), "chunk_type": "energy_lore", "contains": cids, "why": "energy/power/echo was triggered by this turn"})
    if needs.get("lore"):
        chunks.append({"chunk_index": len(chunks), "chunk_type": "world_lore_minimal", "contains": ["canon_lore_minimal"], "why": "world/lore terms were triggered by this turn"})
    if needs.get("past"):
        chunks.append({"chunk_index": len(chunks), "chunk_type": "past_memory_minimal", "contains": cids, "why": "past/memory trigger exists; still bounded"})
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_single_snapshot_turn_contract",
        "context_snapshot_id": f"context_{pending.get('turn_id')}",
        "turn_id": pending.get("turn_id"),
        "turn_number": pending.get("turn_number"),
        "base_revision": pending.get("base_revision"),
        "player_input": player_input,
        "player_input_source": "protected_pending_turn",
        "current_frame": _current_state_slice(current),
        "time_context": {
            "current_datetime": calendar_context.get("current_datetime"),
            "current_date": calendar_context.get("current_date"),
            "current_time": calendar_context.get("current_time"),
            "current_day_phase": calendar_context.get("current_day_phase"),
            "current_day_file": calendar_context.get("current_day_file"),
            "time_revision": calendar_context.get("time_revision"),
            "decision_due_character_ids": calendar_context.get("decision_due_character_ids", []),
            "rule": "Full frozen calendar/autonomy state is in location_inventory_calendar_render; this summary pins the turn clock.",
        },
        "scene_plan_used": scene_plan,
        "needs_decided_by_railway": needs,
        "past_trigger_terms": past_trigger_terms,
        "character_ids": cids,
        "character_roles": roles,
        "inferred_addressed_character_ids": inferred_addressed,
        "memory_character_ids": memory_character_ids,
        "relationship_pair_ids": relationship_pair_ids,
        "relationship_pair_selection": relationship_selection,
        "pov_character_id": pov_id,
        "pov_loaded": pov_id in cids,
        "character_source_audit": {cid: source_audit[cid] for cid in cids},
        "writer_card_contract": {
            "required_for_each_loaded_character": [
                "identity_brief", "current_goal_priority", "voice_behavior_habits", "must_react_to_now",
                "response_obligation", "known_as_fact", "direct_observations", "heard_or_reported",
                "beliefs_and_suspicions", "unknown_or_forbidden", "speech_and_name_guard",
            ],
            "pov_rule": "POV full card is mandatory. Never insert Akira merely because she is the protagonist.",
            "player_character_rule": "When Akira is present as non-POV: low stakes only.",
            "npc_rule": "Active NPC behavior must come from goal + knowledge + unknowns + reaction triggers, never generic scene convenience.",
            "autonomy_rule": "Use frozen time_context: NPCs continue their own activity offscreen, may be unavailable/delayed, and cannot cross locations before ETA.",
            "time_rule": "Propose explicit time_advance with elapsed_minutes/mode/evidence. Never rewrite current date/time directly and never move time backward.",
            "ignored_event_rule": "A missed hook changes world pressure or autonomous NPC actions only; never invent an unplayed Akira action, thought, consent or motive.",
            "addressed_rule": "A directly addressed full NPC must answer, gesture, refuse, interrupt or use meaningful silence; it does not have to comply.",
            "background_rule": "Present non-focus characters may add one short visible reaction/intervention when relevant, without taking over the scene.",
            "personality_rule": "A scene may change mood, trust or tactics, never rewrite static personality/voice for convenience.",
            "knowledge_rule": "Never merge observations, reports, beliefs or mistakes into confirmed facts. Calendar/prompt/runtime/hidden lore are not NPC knowledge.",
        },
        "builder_diagnostics": diagnostics,
        "required_chunks": chunks,
        "total_chunks": len(chunks),
        "must_load_rule": "Use this same turn_id for manifest, every chunk and applyTurnResult. Draft only after all chunks; do not show scene text before successful apply.",
        "next_action": "getRequiredContextManifest",
        "visible_scene_output_allowed": False,
    }


def _manifest_from_contract(contract: dict[str, Any]) -> dict[str, Any]:
    if not contract.get("success"):
        return {
            "success": False,
            "session_id": contract.get("session_id"),
            "runtime_version": RUNTIME_VERSION,
            "mode": "v3_required_context_manifest_rejected",
            "error": contract.get("error") or "Turn contract is invalid.",
            "turn_id": contract.get("turn_id"),
            "total_chunks": 0,
            "chunks": [],
            "next_action": contract.get("next_action") or "getTurnContract",
            "next_chunk_index": None,
            "visible_scene_output_allowed": False,
        }
    chunks = contract.get("required_chunks") if isinstance(contract.get("required_chunks"), list) else []
    return {
        "success": True,
        "session_id": contract.get("session_id"),
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_single_snapshot_required_context_manifest",
        "turn_id": contract.get("turn_id"),
        "base_revision": contract.get("base_revision"),
        "total_chunks": len(chunks),
        "chunks": chunks,
        "load_order_rule": "Load chunks in numeric order with this turn_id. After the last chunk, draft internally and call applyTurnResult before any visible scene output.",
        "next_action": "getRequiredContextChunk" if chunks else "getTurnContract",
        "next_chunk_index": 0 if chunks else None,
        "visible_scene_output_allowed": False,
    }


def _chunk_content(
    sid: str,
    contract: dict[str, Any],
    chunk_index: int,
    *,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = current if isinstance(current, dict) else _ensure_current(sid)
    needs = contract.get("needs_decided_by_railway") if isinstance(contract.get("needs_decided_by_railway"), dict) else {}
    cids = contract.get("character_ids") if isinstance(contract.get("character_ids"), list) else []
    cids = [_canonical_id(x) for x in cids][:7]
    roles = contract.get("character_roles") if isinstance(contract.get("character_roles"), dict) else {cid: _role_for(cid, current, {}) for cid in cids}
    chunks = contract.get("required_chunks") if isinstance(contract.get("required_chunks"), list) else []
    chunk_type = "unknown"
    chunk_meta: dict[str, Any] = {}
    if 0 <= chunk_index < len(chunks) and isinstance(chunks[chunk_index], dict):
        chunk_meta = chunks[chunk_index]
        chunk_type = str(chunk_meta.get("chunk_type") or "unknown")
    chunk_cids = cids
    if chunk_type in {"characters_core", "knowledge_boundaries"} and isinstance(chunk_meta.get("contains"), list):
        requested_group = [_canonical_id(value) for value in chunk_meta["contains"]]
        chunk_cids = [cid for cid in cids if cid in requested_group]

    if chunk_type == "characters_core":
        return {
            "characters": {cid: _character_core_card(sid, cid, str(roles.get(cid) or "referenced"), needs, current) for cid in chunk_cids},
            "global_character_rules": [
                "Character behavior comes from loaded cards first, not generic scene convenience.",
                "NPCs may refuse, delay, lie within their knowledge, interrupt, stay or leave according to their own goals and limits; player wishes do not control them.",
                "Directly addressed full characters must visibly respond, but never have to agree.",
                "Background participants get only short relevant reactions unless the scene gives them a real reason to take focus.",
                "An offscreen_decision character acts only in engine/world state: choose arrival, delay, availability or next activity without making them witness the current scene.",
                "Mood, trust and tactics may change; static personality, voice and values cannot be rewritten for scene convenience.",
                "Internal goals may drive NPC action but are not POV knowledge and cannot be explained before visible disclosure.",
                "Appearance is brief unless deep_appearance=true; never invent hair/age/height against identity_brief.",
                "Akira is 25 and controlled/empty/guarded; do not soften her or write major speech for her.",
            ],
            "response_mode_rules": {
                "player_controlled": "Do not invent decisive speech, consent, refusal, plans or emotional conclusions for POV.",
                "answer_or_visible_refusal": "Directly addressed full NPC must reply, gesture, refuse, interrupt, use meaningful silence, or leave if their goal permits; compliance is never automatic.",
                "goal_driven_turn": "Continue from the NPC's goal, knowledge and limits, never as an exposition/helper button.",
                "brief_observer_reaction": "React briefly only to visible/audible relevant stimulus and do not steal focus.",
                "brief_background_reaction": "A short gesture/look/intervention may keep the room alive without taking the lead.",
                "offscreen_goal_decision": "Propose an autonomy update from the character's own goal/location/ETA; no local speech, observation or scene knowledge.",
                "no_unsourced_action": "Referenced/absent character does not speak, observe or learn this scene without an in-world source.",
            },
        }
    if chunk_type == "knowledge_boundaries":
        return {
            "characters": {cid: _character_knowledge_card(sid, cid, str(roles.get(cid) or "referenced"), needs) for cid in chunk_cids},
            "global_name_rules": UNKNOWN_NAME_RULES,
            "visible_source_rule": "Characters know only what they saw, heard, were told, or can plausibly infer from visible signs. Engine ids and loaded file names are not in-world knowledge.",
            "calendar_exclusion_rule": "Calendar, scene rules, prompt instructions, runtime state and hidden lore guide the writer only; they never become a character's memory or dialogue fact.",
            "evidence_bucket_rule": "Fact, observation, report, belief and mistake are different buckets. Never promote belief to fact. A source-backed played event may resolve an older dynamic unknown; static hidden lore still needs an in-world source.",
        }
    if chunk_type == "state_relationships_memory":
        pair_ids = contract.get("relationship_pair_ids") if isinstance(contract.get("relationship_pair_ids"), list) else []
        selection = contract.get("relationship_pair_selection") if isinstance(contract.get("relationship_pair_selection"), dict) else {}
        return {
            "current_state": _current_state_slice(current),
            "recent_scene_history": _history_slice(sid, 5),
            "loaded_character_memory_ids": contract.get("memory_character_ids", []),
            "loaded_relationship_pair_ids": pair_ids,
            "relationship_selection": selection,
            "relationships": _relationship_cards(sid, pair_ids, selection),
            "state_write_contract": {
                "character_memory": "Write only evidence-backed events for loaded_character_memory_ids. Facts require a source; beliefs stay beliefs.",
                "relationships": "Write only loaded_relationship_pair_ids. Use bounded deltas tied to this scene; do not overwrite personality or hidden canon.",
                "absent_character_rule": "An absent/referenced character neither witnesses nor learns the scene without an explicit later source.",
            },
            "story_lines_note": _compact(_read_json(STORY_LINES_FILE, sid, {}), max_chars=1200, max_items=6, depth=2),
        }
    if chunk_type == "location_inventory_calendar_render":
        scene_plan = contract.get("scene_plan_used") if isinstance(contract.get("scene_plan_used"), dict) else {}
        start_scene_available = bool(current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"))
        frozen_time_context = _calendar_slice(sid, current)
        return {
            "location": _location_slice(current, scene_plan, needs),
            "inventory": _inventory_slice(current, needs),
            "calendar_and_npc_autonomy": frozen_time_context,
            "start_scene": {"exact_text_required": start_scene_available, "text_endpoint": f"/api/v3/sessions/{sid}/start-scene-text" if start_scene_available else None},
            "final_render_contract": _render_contract_small(),
            "draft_after_this_if_no_more_chunks": True,
            "apply_before_visible_output": True,
            "apply_instruction": "Draft internally, then call applyTurnResult with contract.turn_id plus evidence-backed time_advance/event_updates/npc_autonomy_updates when anything elapsed or changed offscreen. Show only visible_scene_text returned by status=applied/idempotent replay.",
        }
    if chunk_type == "energy_lore":
        return _energy_card(sid, cids)
    if chunk_type == "world_lore_minimal":
        return _lore_slice(sid, needs)
    if chunk_type == "past_memory_minimal":
        return _past_memory_slice(sid, cids, contract)
    return {"warning": "Unknown chunk type", "chunk_type": chunk_type}


def _context_snapshot_failure(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "context_snapshot_v1",
        "success": False,
        "status": "rejected",
        "turn_id": contract.get("turn_id"),
        "contract": contract,
        "error": contract.get("error") or "Context snapshot could not be built.",
    }


def _snapshot_matches_pending(snapshot: Any, pending: dict[str, Any]) -> bool:
    return bool(
        isinstance(snapshot, dict)
        and snapshot.get("schema") == "context_snapshot_v1"
        and snapshot.get("status") == "ready"
        and snapshot.get("runtime_version") == RUNTIME_VERSION
        and snapshot.get("turn_id") == pending.get("turn_id")
        and int(snapshot.get("base_revision") or 0) == int(pending.get("base_revision") or 0)
        and snapshot.get("player_input_sha256") == pending.get("player_input_sha256")
        and isinstance(snapshot.get("contract"), dict)
        and isinstance(snapshot.get("manifest"), dict)
        and isinstance(snapshot.get("chunk_packets"), list)
    )


def _canonical_snapshot_payload(payload: dict[str, Any], turn_id: str) -> dict[str, Any]:
    canonical = dict(payload)
    supplied_contract = canonical.pop("turn_contract", None)
    if not isinstance(canonical.get("scene_plan"), dict) and isinstance(supplied_contract, dict):
        old_plan = supplied_contract.get("scene_plan_used")
        if isinstance(old_plan, dict):
            canonical["scene_plan"] = old_plan
    canonical["turn_id"] = turn_id
    return canonical


def _get_or_build_context_snapshot(sid: str, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Build exactly one immutable context payload for the protected turn."""
    with base.session_guard(sid):
        current = _ensure_current(sid)
        pending = base.get_pending_turn(sid)
        supplied_turn_id = _payload_turn_id(payload)
        if not pending or not supplied_turn_id or supplied_turn_id != str(pending.get("turn_id") or ""):
            return _context_snapshot_failure(_build_turn_contract(sid, payload)), False

        existing = base.read_session_json(CONTEXT_SNAPSHOT_FILE, sid, default={})
        if _snapshot_matches_pending(existing, pending):
            return existing, True

        canonical_payload = _canonical_snapshot_payload(payload, str(pending.get("turn_id") or ""))
        contract = _build_turn_contract(sid, canonical_payload)
        if not contract.get("success"):
            return _context_snapshot_failure(contract), False

        manifest = _manifest_from_contract(contract)
        required = contract.get("required_chunks") if isinstance(contract.get("required_chunks"), list) else []
        chunk_packets: list[dict[str, Any]] = []
        for chunk_index, chunk_meta in enumerate(required):
            has_more = chunk_index + 1 < len(required)
            packet = {
                "success": True,
                "session_id": sid,
                "runtime_version": RUNTIME_VERSION,
                "mode": "v3_single_snapshot_required_context_chunk",
                "turn_id": contract.get("turn_id"),
                "base_revision": contract.get("base_revision"),
                "chunk_index": chunk_index,
                "chunk_type": chunk_meta.get("chunk_type") if isinstance(chunk_meta, dict) else "unknown",
                "total_chunks": len(required),
                "has_more": has_more,
                "next_chunk_index": chunk_index + 1 if has_more else None,
                "content": _chunk_content(sid, contract, chunk_index, current=current),
                "next_action": "getRequiredContextChunk" if has_more else "draftThenApplyTurnResult",
                "draft_scene_allowed": not has_more,
                "visible_scene_output_allowed": False,
                "after_last_chunk": "Draft internally; call applyTurnResult with this turn_id; show only the scene text returned after successful apply." if not has_more else None,
            }
            chunk_packets.append(packet)

        immutable_hash = _json_sha256({
            "turn_id": contract.get("turn_id"),
            "base_revision": contract.get("base_revision"),
            "player_input_sha256": pending.get("player_input_sha256"),
            "contract": contract,
            "manifest": manifest,
            "chunk_packets": chunk_packets,
        })
        contract["context_snapshot_sha256"] = immutable_hash
        manifest["context_snapshot_sha256"] = immutable_hash
        for packet in chunk_packets:
            packet["context_snapshot_sha256"] = immutable_hash

        snapshot = {
            "schema": "context_snapshot_v1",
            "success": True,
            "status": "ready",
            "runtime_version": RUNTIME_VERSION,
            "context_snapshot_id": contract.get("context_snapshot_id"),
            "context_snapshot_sha256": immutable_hash,
            "turn_id": pending.get("turn_id"),
            "turn_number": pending.get("turn_number"),
            "base_revision": pending.get("base_revision"),
            "player_input_sha256": pending.get("player_input_sha256"),
            "built_at": _now(),
            "contract": contract,
            "manifest": manifest,
            "chunk_packets": chunk_packets,
            "served_chunk_indices": [],
            "next_required_chunk_index": 0 if chunk_packets else None,
            "all_required_chunks_served": not chunk_packets,
        }
        base.write_json(CONTEXT_SNAPSHOT_FILE, snapshot, session_id=sid)
        return snapshot, False


def _contract_from_snapshot(snapshot: dict[str, Any], reused: bool) -> dict[str, Any]:
    contract = snapshot.get("contract")
    if not isinstance(contract, dict):
        return snapshot
    result = dict(contract)
    result["context_snapshot_reused"] = reused
    result["context_progress"] = {
        "served_chunk_indices": snapshot.get("served_chunk_indices", []),
        "next_required_chunk_index": snapshot.get("next_required_chunk_index"),
        "all_required_chunks_served": bool(snapshot.get("all_required_chunks_served")),
    }
    return result


def _manifest_from_snapshot(snapshot: dict[str, Any], reused: bool) -> dict[str, Any]:
    manifest = snapshot.get("manifest")
    if not isinstance(manifest, dict):
        contract = snapshot.get("contract") if isinstance(snapshot.get("contract"), dict) else snapshot
        return _manifest_from_contract(contract)
    result = dict(manifest)
    result["context_snapshot_reused"] = reused
    result["served_chunk_indices"] = snapshot.get("served_chunk_indices", [])
    result["next_chunk_index"] = snapshot.get("next_required_chunk_index")
    result["all_required_chunks_served"] = bool(snapshot.get("all_required_chunks_served"))
    result["next_action"] = "draftThenApplyTurnResult" if result["all_required_chunks_served"] else "getRequiredContextChunk"
    return result


def _serve_context_chunk(sid: str, payload: dict[str, Any], chunk_index: int) -> dict[str, Any]:
    snapshot, _reused = _get_or_build_context_snapshot(sid, payload)
    if not snapshot.get("success"):
        contract = snapshot.get("contract") if isinstance(snapshot.get("contract"), dict) else {}
        return {
            "success": False,
            "session_id": sid,
            "runtime_version": RUNTIME_VERSION,
            "mode": "v3_required_context_chunk_rejected",
            "turn_id": contract.get("turn_id"),
            "error": snapshot.get("error") or contract.get("error") or "Context snapshot is invalid.",
            "next_action": contract.get("next_action") or "getTurnContract",
            "visible_scene_output_allowed": False,
        }

    with base.session_guard(sid):
        latest = base.read_session_json(CONTEXT_SNAPSHOT_FILE, sid, default={})
        pending = base.get_pending_turn(sid)
        if not pending or not _snapshot_matches_pending(latest, pending):
            return {
                "success": False,
                "session_id": sid,
                "runtime_version": RUNTIME_VERSION,
                "mode": "v3_required_context_chunk_rejected",
                "turn_id": snapshot.get("turn_id"),
                "error": "Context snapshot no longer matches the protected pending turn.",
                "next_action": "getPreflight",
                "visible_scene_output_allowed": False,
            }
        packets = latest.get("chunk_packets") if isinstance(latest.get("chunk_packets"), list) else []
        total = len(packets)
        if chunk_index < 0 or chunk_index >= total:
            return {
                "success": False,
                "session_id": sid,
                "runtime_version": RUNTIME_VERSION,
                "mode": "v3_required_context_chunk_rejected",
                "turn_id": latest.get("turn_id"),
                "error": f"chunk_index {chunk_index} is outside required range 0..{max(total - 1, 0)}.",
                "total_chunks": total,
                "next_action": "getRequiredContextChunk" if total else "getTurnContract",
                "next_chunk_index": latest.get("next_required_chunk_index"),
                "visible_scene_output_allowed": False,
            }
        served = sorted({int(value) for value in latest.get("served_chunk_indices", []) if isinstance(value, int) or str(value).isdigit()})
        replayed = chunk_index in served
        next_required = next((index for index in range(total) if index not in served), None)
        if not replayed and chunk_index != next_required:
            return {
                "success": False,
                "session_id": sid,
                "runtime_version": RUNTIME_VERSION,
                "mode": "v3_required_context_chunk_out_of_order",
                "turn_id": latest.get("turn_id"),
                "error": f"Chunk {chunk_index} was requested out of order; load chunk {next_required} next.",
                "total_chunks": total,
                "served_chunk_indices": served,
                "next_chunk_index": next_required,
                "next_action": "getRequiredContextChunk",
                "visible_scene_output_allowed": False,
            }
        if not replayed:
            served.append(chunk_index)
            served.sort()
            next_required = next((index for index in range(total) if index not in served), None)
            latest["served_chunk_indices"] = served
            latest["next_required_chunk_index"] = next_required
            latest["all_required_chunks_served"] = next_required is None
            latest["updated_at"] = _now()
            base.write_json(CONTEXT_SNAPSHOT_FILE, latest, session_id=sid)

        packet = dict(packets[chunk_index])
        packet["chunk_replayed"] = replayed
        packet["served_chunk_indices"] = served
        packet["next_required_chunk_index"] = next_required
        packet["all_required_chunks_served"] = next_required is None
        return packet


# Replace old action-safe endpoints from earlier patches.
for _path, _method in [
    ("/api/v3/sessions/{session_id}/preflight", "GET"),
    ("/api/v3/sessions/{session_id}/context-request", "POST"),
    ("/api/v3/sessions/{session_id}/context-more", "POST"),
    ("/api/v3/sessions/{session_id}/start-scene-text", "GET"),
    ("/api/v3/sessions/{session_id}/turn-contract", "POST"),
    ("/api/v3/sessions/{session_id}/required-context/manifest", "POST"),
    ("/api/v3/sessions/{session_id}/required-context/chunk", "POST"),
]:
    _remove_route(_path, _method)


@app.get("/api/v3/sessions/{session_id}/preflight", operation_id="getPreflight")
def get_preflight(session_id: str) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    runtime = base.read_turn_runtime(sid)
    pending = runtime.get("pending_turn") if isinstance(runtime.get("pending_turn"), dict) else None
    context_snapshot = base.read_session_json(CONTEXT_SNAPSHOT_FILE, sid, default={})
    snapshot_ready = bool(pending and _snapshot_matches_pending(context_snapshot, pending))
    pending_validation = scene_validation.pending_validation_diagnostics(pending) if pending else None
    start_scene_required = bool(current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"))
    if pending:
        if pending_validation and snapshot_ready and context_snapshot.get("all_required_chunks_served"):
            next_action = "rewriteSceneFromFrozenSnapshotAndRetryApply"
            writer_note = (
                "The session is healthy and the player input is preserved. Inspect pending_validation.last_failure.required_changes, "
                "rewrite or fully regenerate the scene on this same turn_id and frozen snapshot, then retry applyTurnResult. "
                "Never call repairSessionState and never reset/discard the pending turn for a validation failure."
            )
        else:
            next_action = "getTurnContract"
            writer_note = "Resume the protected pending turn_id. Do not replace it with another player input."
    elif start_scene_required:
        next_action = "getStartSceneText"
        writer_note = "Fetch the exact opening, commit it atomically, then show the committed visible_scene_text once and stop."
    else:
        next_action = "waitForPlayerInput"
        writer_note = "No turn is pending. Wait for non-empty player input, then call processTurn."
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_preflight_single_snapshot_small",
        "state_revision": int(runtime.get("state_revision") or 0),
        "pending_turn": {
            "turn_id": pending.get("turn_id"),
            "turn_number": pending.get("turn_number"),
            "base_revision": pending.get("base_revision"),
            "player_input": pending.get("player_input"),
        } if pending else None,
        "pending_validation": pending_validation,
        "context_snapshot": {
            "context_snapshot_id": context_snapshot.get("context_snapshot_id"),
            "context_snapshot_sha256": context_snapshot.get("context_snapshot_sha256"),
            "served_chunk_indices": context_snapshot.get("served_chunk_indices", []),
            "next_required_chunk_index": context_snapshot.get("next_required_chunk_index"),
            "all_required_chunks_served": bool(context_snapshot.get("all_required_chunks_served")),
        } if snapshot_ready else None,
        "current_state": _current_state_slice(current),
        "calendar": _calendar_slice(sid, current),
        "recent_scene_history": _history_slice(sid, 3),
        "next_action": next_action,
        "writer_note": writer_note,
        "visible_scene_output_allowed": False,
    }


@app.post("/api/v3/sessions/{session_id}/turn-contract", operation_id="getTurnContract")
def get_turn_contract(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    sid = _sid(session_id)
    snapshot, reused = _get_or_build_context_snapshot(sid, _payload(body))
    return _contract_from_snapshot(snapshot, reused)


@app.post("/api/v3/sessions/{session_id}/required-context/manifest", operation_id="getRequiredContextManifest")
def get_required_context_manifest(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    sid = _sid(session_id)
    payload = _payload(body)
    snapshot, reused = _get_or_build_context_snapshot(sid, payload)
    return _manifest_from_snapshot(snapshot, reused)


@app.post("/api/v3/sessions/{session_id}/required-context/chunk", operation_id="getRequiredContextChunk")
def get_required_context_chunk(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    sid = _sid(session_id)
    payload = _payload(body)
    chunk_index = _requested_chunk_index(payload, default=0)
    return _serve_context_chunk(sid, payload, chunk_index)


@app.post("/api/v3/sessions/{session_id}/context-request", operation_id="requestContextSlice")
def request_context_slice(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    sid = _sid(session_id)
    payload = _payload(body)
    snapshot, reused = _get_or_build_context_snapshot(sid, payload)
    contract = _contract_from_snapshot(snapshot, reused)
    manifest = _manifest_from_snapshot(snapshot, reused)
    return {
        "success": bool(contract.get("success")),
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_legacy_context_request_points_to_manifest_chunks",
        "do_not_write_scene_yet": True,
        "turn_contract": contract,
        "required_context_manifest": manifest,
        "turn_id": contract.get("turn_id"),
        "next_action": manifest.get("next_action") if contract.get("success") else contract.get("next_action"),
        "next_chunk_index": manifest.get("next_chunk_index") if contract.get("success") else None,
        "visible_scene_output_allowed": False,
    }


@app.post("/api/v3/sessions/{session_id}/context-more", operation_id="requestMoreContext")
def request_more_context(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = _payload(body)
    payload.setdefault("reason", "legacy_requestMoreContext_chunk_bridge")
    # Compatibility: Actions may put chunk_index inside requested_blocks.
    # Normalize it to top-level so this endpoint cannot loop forever on chunk 0.
    payload["chunk_index"] = _requested_chunk_index(payload, default=0)
    return get_required_context_chunk(session_id, payload)


@app.get("/api/v3/sessions/{session_id}/start-scene-text", operation_id="getStartSceneText")
def get_start_scene_text(session_id: str) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    required = bool(current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"))
    exact_text = start_scene_commit.exact_start_scene_text() if required else ""
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_start_scene_text",
        "state_revision": int(base.read_turn_runtime(sid).get("state_revision") or 0),
        "exact_text_required": required,
        "exact_text": exact_text,
        "exact_text_sha256": start_scene_commit.exact_start_scene_sha256() if required else None,
        "visible_scene_output_allowed": False,
        "next_action": "commitStartScene" if required else "getPreflight",
        "after_output_instruction": "Do not display exact_text yet. Call commitStartScene with state_revision and exact_text_sha256; only after status=start_scene_committed show returned visible_scene_text exactly once, then stop and wait for the player's next non-empty message.",
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
        "message": "Full scene_contract is disabled for Actions. Build one snapshot through getTurnContract, then load its manifest and ordered chunks.",
        "next_action": "getTurnContract",
    }


@app.get("/api/v2/sessions/{session_id}/turn-packet", operation_id="getTurnPacket")
def get_turn_packet_action_safe(session_id: str) -> dict[str, Any]:
    return get_scene_contract_action_safe(session_id)


@app.get("/api/v2/sessions/{session_id}/debug/context-audit", operation_id="getContextAudit")
def get_context_audit_action_safe(session_id: str) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    snapshot = base.read_session_json(CONTEXT_SNAPSHOT_FILE, sid, default={})
    snapshot_contract = snapshot.get("contract") if isinstance(snapshot, dict) and isinstance(snapshot.get("contract"), dict) else {}
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_single_snapshot_context_audit",
        "current_state": _current_state_slice(current),
        "pov_character_must_load": True,
        "full_scene_contract_actions_disabled": True,
        "context_snapshot": {
            "status": snapshot.get("status"),
            "context_snapshot_id": snapshot.get("context_snapshot_id"),
            "context_snapshot_sha256": snapshot.get("context_snapshot_sha256"),
            "turn_id": snapshot.get("turn_id"),
            "served_chunk_indices": snapshot.get("served_chunk_indices", []),
            "next_required_chunk_index": snapshot.get("next_required_chunk_index"),
            "all_required_chunks_served": bool(snapshot.get("all_required_chunks_served")),
            "memory_character_ids": snapshot_contract.get("memory_character_ids", []),
            "relationship_pair_ids": snapshot_contract.get("relationship_pair_ids", []),
        } if isinstance(snapshot, dict) and snapshot else None,
        "turn_contract_endpoint": f"/api/v3/sessions/{sid}/turn-contract",
        "manifest_endpoint": f"/api/v3/sessions/{sid}/required-context/manifest",
        "chunk_endpoint": f"/api/v3/sessions/{sid}/required-context/chunk",
    }


try:
    app.version = RUNTIME_VERSION
except Exception:
    pass
