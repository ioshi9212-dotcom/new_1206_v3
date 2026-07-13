from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.8.0-v3-scene-validation-rewrite-gate"
PROTOCOL = "precommit_scene_gate_frozen_snapshot_rewrite_v1"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


context_path = ROOT / "app/context_request_runtime_patch.py"
raw = context_path.read_bytes()
start = raw.find(b'"""Hybrid manifest/chunk context pipeline')
if start < 0:
    raise RuntimeError("context runtime canonical header not found")
if start:
    context_path.write_bytes(raw[start:])
context_path.read_text(encoding="utf-8")

compact = read("app/compact.py")
compact = replace_once(
    compact,
    'APP_VERSION = "0.7.1-v3-akira-nonpov-micro-agency"',
    f'APP_VERSION = "{VERSION}"',
    "compact version",
)
write("app/compact.py", compact)

main = read("app/main.py")
main = main.replace(
    '# The live-game scene gate must be registered after the transactional writer and\n'
    '# context builder, but before Director Mode adds its separate endpoints.\n'
    'import_module("app.scene_validation_runtime_patch")\n\n',
    "",
)
write("app/main.py", main)

writer = read("app/v3_apply_turn_result_runtime_patch.py")
writer = replace_once(
    writer,
    "from app import compact as base\n",
    "from app import compact as base\nfrom app import scene_validation\n",
    "writer import",
)
gate = '''        scene_gate = scene_validation.validate_scene(\n            sid, body, payload, turn_id, text, pending=pending, snapshot=context_snapshot\n        )\n        if not scene_gate["passed"]:\n            return scene_validation.rewrite_response(\n                sid,\n                turn_id,\n                scene_gate,\n                scene_validation.repair_attempt(body, payload),\n                runtime,\n            )\n\n'''
writer = replace_once(
    writer,
    "        new_revision = current_revision + 1\n",
    gate + "        new_revision = current_revision + 1\n",
    "writer precommit gate",
)
result_gate = '''        result["scene_validation"] = {\n            "passed": True,\n            "protocol": scene_validation.PROTOCOL,\n            "warnings": scene_gate["warnings"],\n            "checks": scene_gate["checks"],\n        }\n        result["display_instruction"] = (\n            "State and scene validation passed. Show visible_scene_text only; "\n            "hide validation and internal JSON."\n        )\n'''
writer = replace_once(
    writer,
    "        audit_result = {\n",
    result_gate + "        audit_result = {\n",
    "writer result validation",
)
write("app/v3_apply_turn_result_runtime_patch.py", writer)

production = read("app/production_runtime_patch.py")
production = replace_once(
    production,
    '        "player_character_protocol": "current_pov_protected_nonpov_akira_low_stakes_micro_agency",\n',
    '        "player_character_protocol": "current_pov_protected_nonpov_akira_low_stakes_micro_agency",\n'
    f'        "scene_validation_protocol": "{PROTOCOL}",\n'
    '        "automatic_scene_rewrite_attempts": 3,\n',
    "health validation protocol",
)
production = replace_once(
    production,
    '        "dry_run": {"type": "boolean"},\n',
    '        "dry_run": {"type": "boolean"},\n'
    '        "safety_checks": {"type": "object", "properties": {}, "additionalProperties": {"type": "boolean"}},\n'
    '        "continuity_checks": {"type": "object", "properties": {}, "additionalProperties": {"type": "boolean"}},\n'
    '        "scene_validation": _object_schema({\n'
    '            "repair_attempt": {"type": "integer", "minimum": 0},\n'
    '            "speaker_character_ids": _array_string(),\n'
    '            "addressed_character_responses": object_any,\n'
    '        }),\n',
    "apply request validation fields",
)
production = replace_once(
    production,
    '            "description": "Transactional API: processTurn creates turn_id; Railway freezes one snapshot with exact world time, NPC activity/location/availability/ETA, evidence-bounded character memory, relevant relationship pairs and player-control boundaries. The current POV keeps normal player-choice protection; a present non-POV Akira receives only low-stakes scene continuity. applyTurnResult atomically validates time, routes, events and state before scene text is shown.",\n',
    '            "description": "Transactional API: processTurn creates turn_id; Railway freezes one snapshot with exact world time, NPC activity/location/availability/ETA, evidence-bounded character memory, relevant relationship pairs and player-control boundaries. The current POV keeps normal player-choice protection; a present non-POV Akira receives only low-stakes scene continuity. applyTurnResult validates the draft, requests a same-turn rewrite when needed, then atomically validates time, routes, events and state before scene text is shown.",\n',
    "openapi description",
)
production = replace_once(
    production,
    '                "post": {"operationId": "applyTurnResult", "summary": "Atomically apply this exact pending turn_id. Only its successful response authorizes showing visible_scene_text.", "parameters": [_session_path_param()], "requestBody": {"required": True, "content": {"application/json": {"schema": apply_body_schema}}}, "responses": {"200": _response("Atomic apply result")}}\n',
    '                "post": {"operationId": "applyTurnResult", "summary": "Validate and, if needed, rewrite this exact pending turn_id before atomic apply. Only a successful applied response authorizes showing visible_scene_text.", "parameters": [_session_path_param()], "requestBody": {"required": True, "content": {"application/json": {"schema": apply_body_schema}}}, "responses": {"200": _response("Validated atomic apply result")}}\n',
    "openapi apply summary",
)
write("app/production_runtime_patch.py", production)

for relative in (
    "app/scene_validation_runtime_patch.py",
    "tools/consolidate_scene_gate.py",
    ".github/workflows/consolidate-scene-gate.yml",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()
