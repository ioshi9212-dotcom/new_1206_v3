from __future__ import annotations

import json
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "repo-audit-report.json"


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def walk_strings(value: Any):
    if isinstance(value, dict):
        for nested in value.values():
            yield from walk_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_strings(nested)
    elif isinstance(value, str):
        yield value


def referenced_paths_from_python() -> dict[str, list[str]]:
    prefixes = ("characters/", "state/", "calendar/", "schedule/", "scenes/", "gpt/", "canon_lore/", "api_contracts/")
    refs: dict[str, list[str]] = defaultdict(list)
    pattern = re.compile(r"['\"]((?:characters|state|calendar|schedule|scenes|gpt|canon_lore|api_contracts)/[^'\"]+)['\"]")
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            value = match.group(1)
            if not value.startswith(prefixes):
                continue
            if any(token in value for token in ("{", "}", "*", "<", ">")):
                continue
            refs[value].append(rel(path))
    return dict(sorted(refs.items()))


def collect_relationship_index_refs(index: Any) -> list[str]:
    result: list[str] = []
    for value in walk_strings(index):
        if value.startswith("state/relationship_pairs/") and value.endswith(".json"):
            result.append(value)
        elif value.endswith(".json") and "__" in value:
            result.append(f"state/relationship_pairs/{value}")
    return sorted(set(result))


def main() -> int:
    report: dict[str, Any] = {
        "hard_errors": [],
        "warnings": [],
        "notes": [],
    }
    files = [path for path in ROOT.rglob("*") if path.is_file() and ".git" not in path.parts]
    report["tree"] = {
        "total_files": len(files),
        "top_level_counts": dict(sorted(Counter(path.relative_to(ROOT).parts[0] for path in files).items())),
        "extensions": dict(sorted(Counter(path.suffix or "<none>" for path in files).items())),
    }

    invalid_json: list[dict[str, str]] = []
    for path in sorted(ROOT.rglob("*.json")):
        if ".git" in path.parts:
            continue
        try:
            json_load(path)
        except Exception as exc:
            invalid_json.append({"path": rel(path), "error": str(exc)})
    report["json_validation"] = {
        "checked": len(list(ROOT.rglob("*.json"))),
        "invalid": invalid_json,
    }
    if invalid_json:
        report["hard_errors"].append({"code": "invalid_repository_json", "files": invalid_json})

    character_root = ROOT / "characters"
    character_dirs = sorted(path for path in character_root.iterdir() if path.is_dir()) if character_root.is_dir() else []
    character_rows = []
    missing_required = []
    for directory in character_dirs:
        required = {name: (directory / name).is_file() and bool((directory / name).read_text(encoding="utf-8").strip()) for name in ("main.yaml", "character.yaml", "knowledge.yaml")}
        optional = sorted(path.name for path in directory.iterdir() if path.is_file() and path.name not in required)
        row = {"character_id": directory.name, "required": required, "optional_files": optional}
        character_rows.append(row)
        missing = [name for name, present in required.items() if not present]
        if missing:
            missing_required.append({"character_id": directory.name, "missing": missing})
    report["characters"] = {
        "directories": len(character_dirs),
        "rows": character_rows,
        "missing_required_sources": missing_required,
    }
    if missing_required:
        report["warnings"].append({"code": "character_directories_incomplete", "items": missing_required})

    literal_refs = referenced_paths_from_python()
    missing_literal_refs = []
    existing_literal_refs = []
    for path, sources in literal_refs.items():
        if (ROOT / path).is_file():
            existing_literal_refs.append(path)
        else:
            missing_literal_refs.append({"path": path, "referenced_by": sources})
    report["literal_repository_references"] = {
        "total": len(literal_refs),
        "existing": existing_literal_refs,
        "missing": missing_literal_refs,
    }
    if missing_literal_refs:
        report["warnings"].append({"code": "literal_paths_missing", "items": missing_literal_refs})

    index_path = ROOT / "state/relationship_pairs/_index.json"
    if index_path.is_file():
        index = json_load(index_path)
        pair_refs = collect_relationship_index_refs(index)
        missing_pairs = [path for path in pair_refs if not (ROOT / path).is_file()]
        report["relationship_index"] = {
            "exists": True,
            "referenced_pair_files": pair_refs,
            "missing_pair_files": missing_pairs,
        }
        if missing_pairs:
            report["hard_errors"].append({"code": "relationship_index_broken", "files": missing_pairs})
    else:
        report["relationship_index"] = {"exists": False}
        report["warnings"].append({"code": "relationship_index_missing"})

    calendar_runtime_path = ROOT / "state/calendar_runtime.json"
    if calendar_runtime_path.is_file():
        calendar_runtime = json_load(calendar_runtime_path)
        current_day_file = calendar_runtime.get("current_day_file") if isinstance(calendar_runtime, dict) else None
        report["calendar"] = {
            "runtime_exists": True,
            "current_day_file": current_day_file,
            "current_day_exists": bool(current_day_file and (ROOT / current_day_file).is_file()),
            "pending_events": len(calendar_runtime.get("pending_events", [])) if isinstance(calendar_runtime, dict) and isinstance(calendar_runtime.get("pending_events"), list) else None,
            "npc_autonomy_characters": sorted(calendar_runtime.get("npc_autonomy", {}).keys()) if isinstance(calendar_runtime, dict) and isinstance(calendar_runtime.get("npc_autonomy"), dict) else [],
        }
        if current_day_file and not (ROOT / current_day_file).is_file():
            report["hard_errors"].append({"code": "calendar_current_day_missing", "path": current_day_file})
    else:
        report["calendar"] = {"runtime_exists": False}
        report["hard_errors"].append({"code": "calendar_runtime_missing"})

    deploy_candidates = ["requirements.txt", "Procfile", "railway.toml", "Dockerfile", "runtime.txt"]
    report["deployment"] = {path: (ROOT / path).is_file() for path in deploy_candidates}
    if not (ROOT / "requirements.txt").is_file():
        report["hard_errors"].append({"code": "requirements_missing"})
    if not any((ROOT / path).is_file() for path in ("Procfile", "railway.toml", "Dockerfile")):
        report["warnings"].append({"code": "no_explicit_deployment_entry_file"})

    try:
        from app import compact as base
        from app.context_request_runtime_patch import ID_ALIASES
        from app.main import app
        from app.production_runtime_patch import openapi_actions
        from app.director_openapi_patch import director_openapi_actions
    except Exception as exc:
        report["hard_errors"].append({"code": "runtime_import_failed", "error": repr(exc)})
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    route_rows = []
    duplicate_route_keys = []
    seen_routes: dict[tuple[str, str], int] = Counter()
    for route in app.router.routes:
        path = getattr(route, "path", None)
        methods = sorted(getattr(route, "methods", set()) or [])
        operation_id = getattr(route, "operation_id", None)
        if path:
            route_rows.append({"path": path, "methods": methods, "operation_id": operation_id})
            for method in methods:
                seen_routes[(path, method)] += 1
    for (path, method), count in sorted(seen_routes.items()):
        if count > 1:
            duplicate_route_keys.append({"path": path, "method": method, "count": count})
    report["routes"] = {
        "count": len(route_rows),
        "duplicates": duplicate_route_keys,
        "rows": route_rows,
    }
    if duplicate_route_keys:
        report["hard_errors"].append({"code": "duplicate_runtime_routes", "items": duplicate_route_keys})

    live_schema = openapi_actions()
    director_schema = director_openapi_actions("https://example.invalid")
    live_operations = {
        spec.get("operationId")
        for path_item in live_schema.get("paths", {}).values()
        for spec in path_item.values()
        if isinstance(spec, dict) and spec.get("operationId")
    }
    director_operations = {
        spec.get("operationId")
        for path_item in director_schema.get("paths", {}).values()
        for spec in path_item.values()
        if isinstance(spec, dict) and spec.get("operationId")
    }
    required_live = {
        "startSession", "createSession", "processTurn", "getPreflight",
        "getTurnContract", "getRequiredContextManifest", "getRequiredContextChunk",
        "getStartSceneText", "applyTurnResult", "getSessionIntegrity",
        "rollbackLastTurn", "repairSessionState",
    }
    required_director = {
        "startDirectorDraft", "addDirectorContext", "rewriteDirectorDraft",
        "validateDirectorDraft", "saveDirectorDraft", "getDirectorDraft",
    }
    report["openapi"] = {
        "live_version": live_schema.get("info", {}).get("version"),
        "live_operations": sorted(live_operations),
        "missing_live_operations": sorted(required_live - live_operations),
        "director_version": director_schema.get("info", {}).get("version"),
        "director_operations": sorted(director_operations),
        "missing_director_operations": sorted(required_director - director_operations),
        "operation_overlap": sorted(live_operations & director_operations),
    }
    if required_live - live_operations:
        report["hard_errors"].append({"code": "live_openapi_incomplete", "operations": sorted(required_live - live_operations)})
    if required_director - director_operations:
        report["hard_errors"].append({"code": "director_openapi_incomplete", "operations": sorted(required_director - director_operations)})

    alias_ids = sorted(set(ID_ALIASES.values()))
    source_matrix = {}
    for cid in alias_ids:
        source_matrix[cid] = {
            name: (ROOT / f"characters/{cid}/{name}").is_file()
            for name in ("main.yaml", "character.yaml", "knowledge.yaml")
        }
    report["runtime_character_source_matrix"] = source_matrix
    incomplete_runtime_ids = {cid: values for cid, values in source_matrix.items() if not all(values.values())}
    if incomplete_runtime_ids:
        report["warnings"].append({"code": "runtime_alias_character_sources_incomplete", "items": incomplete_runtime_ids})

    with tempfile.TemporaryDirectory() as temp_dir:
        data_dir = Path(temp_dir) / "data"
        base.DATA_DIR = data_dir
        base.SESSIONS_DIR = data_dir / "sessions"
        client = TestClient(app)
        sid = "full-repo-audit"
        start = client.post("/api/v1/start", json={"session_id": sid}).json()
        preflight = client.get(f"/api/v3/sessions/{sid}/preflight").json()
        exact = client.get(f"/api/v3/sessions/{sid}/start-scene-text").json()
        first_turn = client.post(
            f"/api/v1/sessions/{sid}/turn",
            json={"player_input": "Остаюсь у двери и слушаю дальше."},
        ).json()
        flow: dict[str, Any] = {
            "start_success": start.get("success"),
            "preflight_next_action": preflight.get("next_action"),
            "start_scene_exact_required": exact.get("exact_text_required"),
            "start_scene_chars": len(str(exact.get("exact_text") or "")),
            "process_turn_success": first_turn.get("success"),
        }
        if first_turn.get("success"):
            turn_id = first_turn["turn_id"]
            contract = client.post(
                f"/api/v3/sessions/{sid}/turn-contract",
                json={"turn_id": turn_id},
            ).json()
            manifest = client.post(
                f"/api/v3/sessions/{sid}/required-context/manifest",
                json={"turn_id": turn_id, "turn_contract": contract},
            ).json()
            packets = []
            for index in range(int(manifest.get("total_chunks") or 0)):
                packets.append(client.post(
                    f"/api/v3/sessions/{sid}/required-context/chunk",
                    json={"turn_id": turn_id, "chunk_index": index, "turn_contract": contract},
                ).json())
            render_packets = [packet for packet in packets if packet.get("chunk_type") == "location_inventory_calendar_render"]
            render_content = render_packets[0].get("content", {}) if render_packets else {}
            flow.update({
                "contract_success": contract.get("success"),
                "contract_character_ids": contract.get("character_ids"),
                "contract_character_source_audit": contract.get("character_source_audit"),
                "total_chunks": manifest.get("total_chunks"),
                "chunk_types": [packet.get("chunk_type") for packet in packets],
                "all_chunks_success": all(packet.get("success") for packet in packets),
                "last_chunk_all_served": packets[-1].get("all_required_chunks_served") if packets else None,
                "render_contract_present": isinstance(render_content.get("final_render_contract"), dict),
                "apply_instruction_present": bool(render_content.get("apply_instruction")),
            })
            scene = (
                "🌘 Восточный сектор · 1206 г., 31 августа\n"
                "🕒 23:40 · поздняя ночь · 📍 дом Джуна Картера, комната Акиры\n"
                "⚙️ Активное состояние сцены: Акира слушает разговор внизу\n"
                "✦ POV: Акира · внешне собрана\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Акира остаётся у двери, как и решила. Снизу женский голос обрывает фразу, а Джун отвечает тише; слов пока не разобрать. "
                "Лестница остаётся свободной, окно — за спиной. Значимого решения за неё сцена не принимает.\n\n"
                "✦ Что можно сделать\n◈ Продолжить слушать.\n◈ Вернуться к столу.\n\n"
                "✦ Состояние\nПозиция: у двери. Риск: высокий."
            )
            applied = client.post(
                f"/api/v1/sessions/{sid}/apply-turn-result",
                json={
                    "turn_id": turn_id,
                    "visible_scene_text": scene,
                    "scene_continuity_patch": {"audit_marker": "first_turn"},
                    "change_reason": "temporary full repository audit",
                },
            ).json()
            history = base.read_session_json("state/scene_history.json", sid, default={})
            entries = history.get("entries", []) if isinstance(history, dict) else history if isinstance(history, list) else []
            exact_text = str(exact.get("exact_text") or "")
            exact_recorded = any(
                isinstance(entry, dict) and str(entry.get("visible_scene_text") or "") == exact_text
                for entry in entries
            )
            flow.update({
                "apply_status": applied.get("status"),
                "apply_visible_allowed": applied.get("visible_scene_output_allowed"),
                "history_entries_after_first_player_turn": len(entries),
                "exact_start_scene_recorded_in_history": exact_recorded,
                "start_scene_completed_after_apply": base.read_session_json("state/current_state.json", sid, default={}).get("start_scene_completed"),
                "integrity_after_apply": client.get(f"/api/v1/sessions/{sid}/integrity").json().get("status"),
            })
        report["live_flow_probe"] = flow

    flow = report["live_flow_probe"]
    for key in ("start_success", "process_turn_success", "contract_success", "all_chunks_success"):
        if flow.get(key) is not True:
            report["hard_errors"].append({"code": "live_flow_failed", "stage": key, "flow": flow})
    if flow.get("apply_status") != "applied":
        report["hard_errors"].append({"code": "live_apply_failed", "flow": flow})
    if not flow.get("render_contract_present"):
        report["hard_errors"].append({"code": "render_contract_not_loaded"})
    if flow.get("exact_start_scene_recorded_in_history") is False:
        report["warnings"].append({
            "code": "exact_start_scene_not_recorded_in_scene_history",
            "impact": "The first visible scene is shown before a pending turn exists, so scene_history starts with the first generated reply rather than the canonical opening scene.",
        })

    scene_contract_path = ROOT / "gpt/scene_output_contract_1206.json"
    if scene_contract_path.is_file():
        scene_contract = json_load(scene_contract_path)
        report["scene_output_contract"] = {
            "version": scene_contract.get("version"),
            "header_required": scene_contract.get("visible_scene_must_start_with_header"),
            "dialogue_format": scene_contract.get("dialogue_format_required"),
            "bottom_blocks_order": scene_contract.get("bottom_blocks_order"),
            "prose_rules_count": len(scene_contract.get("prose_rules", [])) if isinstance(scene_contract.get("prose_rules"), list) else 0,
        }
    else:
        report["hard_errors"].append({"code": "scene_output_contract_missing"})

    validator_text = (ROOT / "app/scene_validation.py").read_text(encoding="utf-8")
    report["deterministic_scene_validation_coverage"] = {
        "checks_internal_json": "internal_json_visible" in validator_text,
        "checks_unloaded_speaker": "unloaded_character_spoke" in validator_text,
        "checks_repetition": "scene_repeats_previous_result" in validator_text,
        "checks_major_pov_choice": "major_pov_choice_not_in_player_input" in validator_text,
        "checks_non_pov_akira_choice": "major_non_pov_akira_choice_not_in_player_input" in validator_text,
        "checks_header_shape": "visible_scene_must_start_with_header" in validator_text or "header_template" in validator_text,
        "checks_required_bottom_blocks": "bottom_blocks_order" in validator_text,
        "checks_unknown_name_leak": "unknown_name" in validator_text or "name_source" in validator_text,
        "checks_third_person_style": "third_person" in validator_text,
    }
    missing_style_checks = [
        key for key, value in report["deterministic_scene_validation_coverage"].items()
        if key in {"checks_header_shape", "checks_required_bottom_blocks", "checks_unknown_name_leak", "checks_third_person_style"} and not value
    ]
    if missing_style_checks:
        report["warnings"].append({
            "code": "scene_contract_rules_are_prompt_only_not_deterministically_validated",
            "missing_checks": missing_style_checks,
        })

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["hard_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
