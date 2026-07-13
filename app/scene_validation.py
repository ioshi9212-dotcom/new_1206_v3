"""Pure pre-commit scene validator used by the transactional apply writer."""
from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from typing import Any

from app import compact as base

VERSION = "0.10.0-v3-quarantine-repair"
PROTOCOL = "precommit_scene_gate_frozen_snapshot_rewrite_v1"
MAX_SCENE_CHARS = 32000
MAX_REWRITES = 3
SCENE_HISTORY_FILE = "state/scene_history.json"

ALIASES = {
    "акира": "akira", "akira": "akira", "алекс": "alex", "alex": "alex",
    "эмма": "emma", "emma": "emma", "ирэй": "irey", "ирей": "irey", "irey": "irey",
    "джун": "jun", "jun": "jun", "jun_carter": "jun", "кай": "kai", "kai": "kai",
    "мики": "miki", "miki": "miki", "райден": "raiden", "рейден": "raiden",
    "рейдон": "raiden", "raiden": "raiden", "raiden_sterling": "raiden",
    "рэй": "ray", "рей": "ray", "ray": "ray", "ray_carter": "ray",
    "хару": "haru", "haru": "haru", "haru_foster": "haru",
    "широ": "shiro", "shiro": "shiro", "юна": "yuna", "yuna": "yuna",
}
NAMES = {
    "akira": ("Акира", "Akira"), "alex": ("Алекс", "Alex"),
    "emma": ("Эмма", "Emma"), "irey": ("Ирэй", "Ирей", "Irey"),
    "jun": ("Джун", "Jun"), "kai": ("Кай", "Kai"),
    "miki": ("Мики", "Miki"), "raiden": ("Райден", "Рейден", "Raiden"),
    "ray": ("Рэй", "Рей", "Ray"), "haru": ("Хару", "Haru"),
    "shiro": ("Широ", "Shiro"), "yuna": ("Юна", "Yuna"),
}
KNOWN_IDS = set(NAMES)
INTERNAL_KEYS = (
    "proposed_updates", "safety_checks", "npc_autonomy_updates",
    "relationship_updates", "character_memory_updates", "scene_response",
    "context_snapshot_sha256",
)
REQUIRED_SAFETY = {
    "used_only_loaded_characters", "respected_knowledge_boundaries",
    "no_hidden_past_without_trigger", "no_unjustified_character_arrival",
    "no_major_pov_choice_for_player", "no_major_akira_choice_for_player",
    "akira_non_pov_actions_are_low_stakes", "clock_changed_only_through_elapsed_minutes",
    "npc_routes_respect_eta", "missed_events_do_not_script_pov",
}
CHOICES = (
    ("consent", r"\bсогласил(?:ся|ась|ись)\b|\bпринял(?:а)? предложение\b|\bдала согласие\b"),
    ("refusal", r"\bотказал(?:ся|ась|ись)\b|\bдала отказ\b|\bокончательно сказала нет\b"),
    ("promise", r"\bпообещал(?:а)?\b|\bдал(?:а)? обещание\b|\bпоклял(?:ся|ась)\b"),
    ("confession", r"\bпризнал(?:ся|ась)\b|\bраскрыл(?:а)? (?:тайну|секрет|правду)\b"),
    ("trust", r"\bпростил(?:а)?\b|\bрешил(?:а)? довериться\b|\bполностью доверил(?:а)?\b"),
    ("route", r"\bпоехал(?:а)? с\b|\bуш[её]л(?:а)? с\b|\bпоследовал(?:а)? за\b|\bвыбрал(?:а)? (?:сторону|маршрут|план)\b"),
    ("force", r"\bприменил(?:а)? (?:силу|энергию|способность)\b|\bатаковал(?:а)?\b|\bударил(?:а)? его\b"),
    ("access", r"\bсогласил(?:ся|ась) на лечение\b|\bразрешил(?:а)? (?:доступ|войти|осмотр)\b"),
    ("romance", r"\bпоцеловал(?:а)? (?:его|её|ее)\b|\bответил(?:а)? на поцелуй\b"),
)
AUTH = {
    "consent": ("соглас", "принима", "да", "хорошо", "ладно"),
    "refusal": ("отказ", "нет", "не буду", "не пойду"),
    "promise": ("обещ", "клян"), "confession": ("призна", "раскрыва", "говорю секрет"),
    "trust": ("прощ", "довер"), "route": ("иду с", "еду с", "поеду", "следую", "выбираю"),
    "force": ("применя", "атак", "ударя", "бью", "энерги"),
    "access": ("лечение", "разрешаю", "впускаю", "доступ"),
    "romance": ("целу", "отвечаю на поцелуй"),
}


def cid(value: Any) -> str:
    raw = str(value or "").strip()
    return ALIASES.get(raw.lower(), raw.lower())


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else ([] if value is None else [value])


def _containers(body: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    result, queue, seen = [], [body, payload], set()
    while queue:
        value = queue.pop(0)
        if not isinstance(value, dict) or id(value) in seen:
            continue
        seen.add(id(value)); result.append(value)
        queue.extend(value[k] for k in ("data", "scene_response", "metadata", "proposed_updates", "validation") if isinstance(value.get(k), dict))
    return result


def _mapping(body: dict[str, Any], payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    for container in _containers(body, payload):
        for key in keys:
            if isinstance(container.get(key), dict):
                return container[key]
    return {}


def _collect(value: Any, keys: set[str], depth: int = 0) -> list[Any]:
    if depth > 7:
        return []
    found = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in keys:
                found.append(nested)
            if isinstance(nested, (dict, list)):
                found.extend(_collect(nested, keys, depth + 1))
    elif isinstance(value, list):
        for nested in value:
            if isinstance(nested, (dict, list)):
                found.extend(_collect(nested, keys, depth + 1))
    return found


def _snapshot_ids(snapshot: dict[str, Any]) -> set[str]:
    keys = {
        "character_ids", "active_character_ids", "scene_character_ids", "present_character_ids",
        "memory_character_ids", "addressed_character_ids", "speaking_character_ids",
        "remote_contact_character_ids", "contacted_character_ids",
    }
    return {cid(item) for value in _collect(snapshot, keys) for item in _list(value) if cid(item)}


def _pov(snapshot: dict[str, Any], pending: dict[str, Any]) -> str:
    for value in _collect(snapshot, {"pov_character_id"}):
        if cid(value):
            return cid(value)
    patch = pending.get("current_state_patch") if isinstance(pending.get("current_state_patch"), dict) else {}
    return cid(patch.get("pov_character_id") or "akira") or "akira"


def _speakers(text: str, validation: dict[str, Any]) -> set[str]:
    labels = re.findall(r"\*\*([^*\n]{1,48})\*\*\s*[—–-]\s*", text)
    labels += [str(x) for key in ("speaker_character_ids", "speaking_character_ids", "characters_who_spoke") for x in _list(validation.get(key))]
    return {cid(label.strip().strip(":：")) for label in labels if cid(label.strip().strip(":：")) in KNOWN_IDS}


def _last_scene(sid: str) -> str:
    history = base.read_session_json(SCENE_HISTORY_FILE, sid, default=[])
    entries = history.get("entries", []) if isinstance(history, dict) else history if isinstance(history, list) else []
    for entry in reversed(entries):
        if isinstance(entry, dict) and (entry.get("visible_scene_text") or entry.get("scene_text")):
            return str(entry.get("visible_scene_text") or entry.get("scene_text"))
    return ""


def _normalize(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"[#*_>`~\[\](){}]", " ", text).lower()).strip()


def _similarity(current: str, previous: str) -> float:
    left, right = _normalize(current), _normalize(previous)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if min(len(left.split()), len(right.split())) < 18:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _internal_json(text: str) -> bool:
    lower = text.lower()
    if any(re.search(rf'"{re.escape(key)}"\s*:', lower) for key in INTERNAL_KEYS):
        return True
    if text.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(text)
            return any(key in json.dumps(parsed, ensure_ascii=False).lower() for key in INTERNAL_KEYS)
        except Exception:
            pass
    return False


def _choice_hits(text: str, character_id: str) -> list[dict[str, str]]:
    names = NAMES.get(character_id, ())
    if not names:
        return []
    subject = "(?:" + "|".join(re.escape(name) for name in names) + ")"
    hits = []
    for category, choice in CHOICES:
        for match in re.finditer(rf"\b{subject}\b[^.!?\n]{{0,100}}(?:{choice})", text, flags=re.I):
            hits.append({"category": category, "excerpt": match.group(0)[:180]})
    return hits


def _authorized(player_input: str, character_id: str, category: str, pov_id: str) -> bool:
    normalized = player_input.lower().replace("ё", "е")
    marker_found = any(marker.replace("ё", "е") in normalized for marker in AUTH.get(category, ()))
    if character_id == "akira" and pov_id != "akira":
        return marker_found and ("акира" in normalized or "akira" in normalized)
    return marker_found


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **extra}


def validate_scene(
    sid: str,
    body: dict[str, Any],
    payload: dict[str, Any],
    turn_id: str,
    text: str,
    *,
    pending: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    player_input = str(pending.get("player_input") or "")
    loaded, pov_id = _snapshot_ids(snapshot), _pov(snapshot, pending)
    validation = _mapping(body, payload, "scene_validation", "validation")
    safety = _mapping(body, payload, "safety_checks")
    continuity = _mapping(body, payload, "continuity_checks")
    errors, warnings = [], []

    if len(text) > MAX_SCENE_CHARS:
        errors.append(_error("scene_too_large", "Scene exceeds the pre-commit size limit.", actual=len(text), maximum=MAX_SCENE_CHARS))
    if _internal_json(text):
        errors.append(_error("internal_json_visible", "Player-visible scene contains internal JSON/state fields."))

    speaker_ids = _speakers(text, validation)
    if loaded:
        for character_id in sorted(speaker_ids - loaded):
            errors.append(_error("unloaded_character_spoke", f"Character '{character_id}' spoke outside the frozen snapshot.", character_id=character_id, loaded_character_ids=sorted(loaded)))

    similarity = _similarity(text, _last_scene(sid))
    if similarity >= 0.93:
        errors.append(_error("scene_repeats_previous_result", "Draft substantially repeats the previous applied scene.", similarity=round(similarity, 4)))

    for hit in _choice_hits(text, pov_id):
        if not _authorized(player_input, pov_id, hit["category"], pov_id):
            errors.append(_error("major_pov_choice_not_in_player_input", "Draft generated a meaningful current-POV decision.", character_id=pov_id, **hit))
    if pov_id != "akira" and "akira" in loaded:
        for hit in _choice_hits(text, "akira"):
            if not _authorized(player_input, "akira", hit["category"], pov_id):
                errors.append(_error("major_non_pov_akira_choice_not_in_player_input", "Non-POV Akira received a meaningful generated choice; use a neutral reply, deflection, pause or local movement.", character_id="akira", **hit))

    if safety:
        for key in sorted(REQUIRED_SAFETY):
            if key in safety and safety.get(key) is not True:
                errors.append(_error("failed_safety_attestation", f"Safety check '{key}' is not true.", safety_check=key))
        missing = sorted(REQUIRED_SAFETY - set(safety))
        if missing:
            warnings.append(_error("partial_safety_attestation", "Safety attestation is incomplete.", missing=missing))
    else:
        warnings.append(_error("legacy_payload_without_safety_attestation", "Deterministic checks ran without explicit safety_checks."))

    for key, value in continuity.items():
        if isinstance(value, bool) and not value:
            errors.append(_error("failed_continuity_attestation", f"Continuity check '{key}' is false.", continuity_check=key))
    addressed = validation.get("addressed_character_responses")
    if isinstance(addressed, dict):
        for raw, responded in addressed.items():
            if responded is not True:
                errors.append(_error("addressed_character_left_without_response", "Directly addressed character has no visible response/refusal/reaction.", character_id=cid(raw) or str(raw)))

    checks = {
        "turn_id": turn_id, "pov_character_id": pov_id,
        "snapshot_character_ids": sorted(loaded), "speaker_character_ids": sorted(speaker_ids),
        "previous_scene_similarity": round(similarity, 4),
        "player_input_sha256": hashlib.sha256(player_input.encode()).hexdigest(),
    }
    return {"passed": not errors, "protocol": PROTOCOL, "runtime_version": VERSION, "errors": errors, "warnings": warnings, "checks": checks}


def repair_attempt(body: dict[str, Any], payload: dict[str, Any]) -> int:
    value = _mapping(body, payload, "scene_validation", "validation").get("repair_attempt", 0)
    try:
        return max(0, int(value))
    except Exception:
        return 0


def rewrite_response(sid: str, turn_id: str, result: dict[str, Any], attempt: int, runtime: dict[str, Any]) -> dict[str, Any]:
    pending = runtime.get("pending_turn") if isinstance(runtime.get("pending_turn"), dict) else {}
    next_attempt = attempt + 1
    return {
        "success": False,
        "status": "rewrite_required" if next_attempt <= MAX_REWRITES else "full_regeneration_required",
        "session_id": sid, "runtime_version": VERSION, "scene_validation_protocol": PROTOCOL,
        "turn_id": turn_id, "expected_turn_id": pending.get("turn_id"),
        "state_revision": int(runtime.get("state_revision") or 0),
        "pending_turn_preserved": True, "visible_scene_output_allowed": False,
        "do_not_show_validation_to_player": True,
        "next_action": "rewriteSceneFromFrozenSnapshotAndRetryApply",
        "repair_packet": {
            "repair_attempt": next_attempt, "maximum_automatic_rewrite_attempts": MAX_REWRITES,
            "reuse_same_turn_id": True, "reuse_same_context_snapshot": True,
            "preserve_player_input_exactly": True,
            "required_changes": result["errors"], "warnings": result["warnings"],
            "instruction": "Rewrite only the invalid draft/update, keep the same turn_id and frozen context, then call applyTurnResult again. Never show this packet to the player.",
        },
        "scene_validation": result,
    }
