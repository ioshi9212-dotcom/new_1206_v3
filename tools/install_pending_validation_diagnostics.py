from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD_VERSION = "0.11.0-v3-start-scene-commit"
VERSION = "0.11.1-v3-pending-validation-diagnostics"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


# Runtime version is one coordinated release.
for path in (
    "app/compact.py",
    "app/scene_validation.py",
    "app/session_recovery.py",
    "app/session_repair.py",
    "app/start_scene_commit.py",
    "tests/test_runtime_regressions.py",
    "tests/test_scene_validation_runtime.py",
    "tests/test_session_recovery.py",
    "tests/test_session_repair.py",
    "tests/test_start_scene_commit.py",
):
    text = read(path)
    if OLD_VERSION in text:
        text = text.replace(OLD_VERSION, VERSION)
        write(path, text)


# Server-owned pending validation state and diagnostics.
scene = read("app/scene_validation.py")
scene = replace_once(
    scene,
    "import re\nfrom difflib import SequenceMatcher\n",
    "import re\nfrom datetime import UTC, datetime\nfrom difflib import SequenceMatcher\n",
    "scene validation datetime import",
)
scene = replace_once(
    scene,
    'SCENE_HISTORY_FILE = "state/scene_history.json"\n',
    'SCENE_HISTORY_FILE = "state/scene_history.json"\nPENDING_VALIDATION_SCHEMA = "pending_validation_runtime_v1"\n',
    "pending validation schema",
)
old_error = '''def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **extra}


'''
new_error = '''def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **extra}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _validation_items(value: Any, *, maximum: int = 20) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)][:maximum]


def pending_validation_diagnostics(pending: Any) -> dict[str, Any] | None:
    if not isinstance(pending, dict):
        return None
    state = pending.get("validation_runtime")
    if not isinstance(state, dict) or state.get("schema") != PENDING_VALIDATION_SCHEMA:
        return None
    attempts = max(0, int(state.get("attempts") or 0))
    last_failure = state.get("last_failure") if isinstance(state.get("last_failure"), dict) else None
    if attempts <= 0 and not last_failure:
        return None
    history = state.get("failure_history") if isinstance(state.get("failure_history"), list) else []
    return {
        "schema": PENDING_VALIDATION_SCHEMA,
        "attempts": attempts,
        "maximum_automatic_rewrite_attempts": MAX_REWRITES,
        "last_failure": dict(last_failure) if last_failure else None,
        "failure_history": [dict(item) for item in history if isinstance(item, dict)][-8:],
        "pending_turn_preserved": True,
        "session_corruption_detected": False,
        "recovery_rule": "Rewrite or fully regenerate the scene on the same pending turn_id and frozen context. Never discard/reset the pending turn merely because validation failed.",
    }


def record_pending_failure(
    sid: str,
    runtime: dict[str, Any],
    result: dict[str, Any],
    *,
    failure_stage: str,
    draft_text: str = "",
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    pending = runtime.get("pending_turn") if isinstance(runtime.get("pending_turn"), dict) else None
    if not pending or not pending.get("turn_id"):
        return 0, runtime, {}
    previous = pending.get("validation_runtime")
    state = dict(previous) if isinstance(previous, dict) and previous.get("schema") == PENDING_VALIDATION_SCHEMA else {
        "schema": PENDING_VALIDATION_SCHEMA,
        "attempts": 0,
        "failure_history": [],
        "last_failure": None,
    }
    attempt = max(0, int(state.get("attempts") or 0)) + 1
    recorded_at = _now()
    errors = _validation_items(result.get("errors"))
    warnings = _validation_items(result.get("warnings"))
    failure = {
        "attempt": attempt,
        "failure_stage": str(failure_stage or "scene_gate"),
        "recorded_at": recorded_at,
        "error_codes": [str(item.get("code") or "unknown") for item in errors],
        "required_changes": errors,
        "warnings": warnings,
        "checks": dict(result.get("checks")) if isinstance(result.get("checks"), dict) else {},
        "draft_sha256": hashlib.sha256(draft_text.encode("utf-8")).hexdigest() if draft_text else None,
    }
    history = state.get("failure_history") if isinstance(state.get("failure_history"), list) else []
    history = [dict(item) for item in history if isinstance(item, dict)]
    history.append(failure)
    state.update({
        "schema": PENDING_VALIDATION_SCHEMA,
        "attempts": attempt,
        "last_failure": failure,
        "failure_history": history[-8:],
        "last_failed_at": recorded_at,
        "pending_turn_preserved": True,
    })
    updated_pending = dict(pending)
    updated_pending["validation_runtime"] = state
    updated_runtime = dict(runtime)
    updated_runtime["pending_turn"] = updated_pending
    updated_runtime["updated_at"] = recorded_at
    base.write_json(base.TURN_RUNTIME_FILE, updated_runtime, session_id=sid)
    return attempt, updated_runtime, state


'''
scene = replace_once(scene, old_error, new_error, "pending diagnostics helpers")
old_rewrite = '''def rewrite_response(sid: str, turn_id: str, result: dict[str, Any], attempt: int, runtime: dict[str, Any]) -> dict[str, Any]:
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
'''
new_rewrite = '''def rewrite_response(
    sid: str,
    turn_id: str,
    result: dict[str, Any],
    attempt: int,
    runtime: dict[str, Any],
    *,
    failure_stage: str = "scene_gate",
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pending = runtime.get("pending_turn") if isinstance(runtime.get("pending_turn"), dict) else {}
    automatic_retry_allowed = attempt <= MAX_REWRITES
    return {
        "success": False,
        "status": "rewrite_required" if automatic_retry_allowed else "full_regeneration_required",
        "session_id": sid,
        "runtime_version": VERSION,
        "scene_validation_protocol": PROTOCOL,
        "turn_id": turn_id,
        "expected_turn_id": pending.get("turn_id"),
        "state_revision": int(runtime.get("state_revision") or 0),
        "pending_turn_preserved": True,
        "visible_scene_output_allowed": False,
        "do_not_show_validation_to_player": True,
        "do_not_reset_or_discard_pending_turn": True,
        "session_corruption_detected": False,
        "next_action": "rewriteSceneFromFrozenSnapshotAndRetryApply" if automatic_retry_allowed else "getPreflight",
        "validation_errors": result.get("errors", []),
        "repair_packet": {
            "repair_attempt": attempt,
            "attempt_counter_owner": "server",
            "maximum_automatic_rewrite_attempts": MAX_REWRITES,
            "failure_stage": failure_stage,
            "reuse_same_turn_id": True,
            "reuse_same_context_snapshot": True,
            "preserve_player_input_exactly": True,
            "required_changes": result.get("errors", []),
            "warnings": result.get("warnings", []),
            "instruction": (
                "Correct the invalid scene/update on the same pending turn_id and frozen context, then call applyTurnResult again. "
                "After the automatic retry limit, fully regenerate the draft from that same frozen context. Never reset, discard, or ask the player to repeat the move merely because validation failed."
            ),
        },
        "pending_validation": diagnostics or pending_validation_diagnostics(pending),
        "diagnostics_available_from": ["getPreflight", "getSessionIntegrity"],
        "scene_validation": result,
    }
'''
scene = replace_once(scene, old_rewrite, new_rewrite, "server-owned rewrite response")
write("app/scene_validation.py", scene)


# Apply writer records every correctable failure, including state-patch failures.
apply = read("app/v3_apply_turn_result_runtime_patch.py")
old_rejected_tail = '''    if validation_errors:
        result["validation_errors"] = validation_errors
    return result


'''
new_rejected_tail = '''    if validation_errors:
        result["validation_errors"] = validation_errors
    return result


def _recorded_validation_failure(
    sid: str,
    turn_id: str,
    runtime: dict[str, Any],
    text: str,
    *,
    failure_stage: str,
    error: str,
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]] | None = None,
    checks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "passed": False,
        "protocol": scene_validation.PROTOCOL,
        "runtime_version": RUNTIME_VERSION,
        "errors": [dict(item) for item in errors if isinstance(item, dict)],
        "warnings": [dict(item) for item in (warnings or []) if isinstance(item, dict)],
        "checks": {"failure_stage": failure_stage, **(checks or {})},
    }
    attempt, updated_runtime, diagnostics = scene_validation.record_pending_failure(
        sid,
        runtime,
        result,
        failure_stage=failure_stage,
        draft_text=text,
    )
    response = scene_validation.rewrite_response(
        sid,
        turn_id,
        result,
        attempt,
        updated_runtime,
        failure_stage=failure_stage,
        diagnostics=diagnostics,
    )
    response["error"] = error
    return response


'''
apply = replace_once(apply, old_rejected_tail, new_rejected_tail, "recorded validation helper")
old_scene_gate = '''        if not scene_gate["passed"]:
            return scene_validation.rewrite_response(
                sid,
                turn_id,
                scene_gate,
                scene_validation.repair_attempt(body, payload),
                runtime,
            )
'''
new_scene_gate = '''        if not scene_gate["passed"]:
            return _recorded_validation_failure(
                sid,
                turn_id,
                runtime,
                text,
                failure_stage="scene_gate",
                error="Scene prose or declared validation checks failed. Rewrite the same pending turn from the frozen snapshot.",
                errors=scene_gate.get("errors", []),
                warnings=scene_gate.get("warnings", []),
                checks=scene_gate.get("checks", {}),
            )
'''
apply = replace_once(apply, old_scene_gate, new_scene_gate, "record scene gate failure")
old_clock = '''        if direct_clock_key:
            return _rejected(
                sid,
                "Clock fields are engine-owned. Use time_advance; direct date/time patches are blocked.",
                turn_id=turn_id,
                expected_turn_id=expected_turn_id,
                next_action="applyTurnResult",
                validation_errors=[{
                    "code": "direct_clock_patch_blocked",
                    "field": direct_clock_key,
                    "message": "Use elapsed_minutes plus mode/reason/evidence instead of setting a clock field.",
                }],
            )
'''
new_clock = '''        if direct_clock_key:
            return _recorded_validation_failure(
                sid,
                turn_id,
                runtime,
                text,
                failure_stage="current_state_patch",
                error="Clock fields are engine-owned. Use time_advance; direct date/time patches are blocked.",
                errors=[{
                    "code": "direct_clock_patch_blocked",
                    "field": direct_clock_key,
                    "message": "Use elapsed_minutes plus mode/reason/evidence instead of setting a clock field.",
                }],
            )
'''
apply = replace_once(apply, old_clock, new_clock, "record direct clock failure")
old_time = '''        if time_autonomy_errors:
            return _rejected(
                sid,
                "World time or NPC autonomy update was rejected. Correct the elapsed time, evidence, route, ETA or presence transition, then retry the same turn_id.",
                turn_id=turn_id,
                expected_turn_id=expected_turn_id,
                next_action="applyTurnResult",
                validation_errors=time_autonomy_errors,
            )
'''
new_time = '''        if time_autonomy_errors:
            return _recorded_validation_failure(
                sid,
                turn_id,
                runtime,
                text,
                failure_stage="world_time_and_npc_autonomy",
                error="World time or NPC autonomy update was rejected. Correct the elapsed time, evidence, route, ETA or presence transition, then retry the same turn_id.",
                errors=time_autonomy_errors,
            )
'''
apply = replace_once(apply, old_time, new_time, "record time autonomy failure")
old_dynamic = '''        if validation_errors:
            return _rejected(
                sid,
                "Dynamic character state was rejected. Correct the evidence/source or loaded-character/pair scope, then retry the same turn_id.",
                turn_id=turn_id,
                expected_turn_id=expected_turn_id,
                next_action="applyTurnResult",
                validation_errors=validation_errors,
            )
'''
new_dynamic = '''        if validation_errors:
            return _recorded_validation_failure(
                sid,
                turn_id,
                runtime,
                text,
                failure_stage="character_memory_and_relationships",
                error="Dynamic character state was rejected. Correct the evidence/source or loaded-character/pair scope, then retry the same turn_id.",
                errors=validation_errors,
            )
'''
apply = replace_once(apply, old_dynamic, new_dynamic, "record dynamic state failure")
write("app/v3_apply_turn_result_runtime_patch.py", apply)


# New pending turns declare server-owned validation state from creation.
production = read("app/production_runtime_patch.py")
production = replace_once(
    production,
    '            "created_at": created_at,\n        }\n',
    '            "created_at": created_at,\n            "validation_runtime": {\n                "schema": "pending_validation_runtime_v1",\n                "attempts": 0,\n                "failure_history": [],\n                "last_failure": None,\n            },\n        }\n',
    "initialize pending validation runtime",
)
production = replace_once(
    production,
    '        "scene_validation_protocol": "precommit_scene_gate_frozen_snapshot_rewrite_v1",\n',
    '        "scene_validation_protocol": "precommit_scene_gate_frozen_snapshot_rewrite_v1",\n        "pending_validation_diagnostics": "server_owned_attempt_counter_persisted_error_codes_same_turn_recovery",\n',
    "health pending diagnostics",
)
write("app/production_runtime_patch.py", production)


# Preflight exposes exact last failure and explicitly forbids resetting a healthy pending turn.
context = read("app/context_request_runtime_patch.py")
context = replace_once(
    context,
    "from app import compact as base\nfrom app import start_scene_commit\n",
    "from app import compact as base\nfrom app import scene_validation\nfrom app import start_scene_commit\n",
    "context scene validation import",
)
context = replace_once(
    context,
    '    snapshot_ready = bool(pending and _snapshot_matches_pending(context_snapshot, pending))\n    start_scene_required = bool(current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"))\n    if pending:\n        next_action = "getTurnContract"\n        writer_note = "Resume the protected pending turn_id. Do not replace it with another player input."\n',
    '    snapshot_ready = bool(pending and _snapshot_matches_pending(context_snapshot, pending))\n    pending_validation = scene_validation.pending_validation_diagnostics(pending) if pending else None\n    start_scene_required = bool(current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"))\n    if pending:\n        if pending_validation and snapshot_ready and context_snapshot.get("all_required_chunks_served"):\n            next_action = "rewriteSceneFromFrozenSnapshotAndRetryApply"\n            writer_note = (\n                "The session is healthy and the player input is preserved. Inspect pending_validation.last_failure.required_changes, "\n                "rewrite or fully regenerate the scene on this same turn_id and frozen snapshot, then retry applyTurnResult. "\n                "Never call repairSessionState and never reset/discard the pending turn for a validation failure."\n            )\n        else:\n            next_action = "getTurnContract"\n            writer_note = "Resume the protected pending turn_id. Do not replace it with another player input."\n',
    "preflight pending diagnostics logic",
)
context = replace_once(
    context,
    '        "context_snapshot": {\n',
    '        "pending_validation": pending_validation,\n        "context_snapshot": {\n',
    "preflight pending diagnostics field",
)
write("app/context_request_runtime_patch.py", context)


# Integrity distinguishes a rejected draft from actual state corruption.
recovery = read("app/session_recovery.py")
recovery = replace_once(
    recovery,
    "from app import compact as base\n",
    "from app import compact as base\nfrom app import scene_validation\n",
    "recovery scene validation import",
)
recovery = replace_once(
    recovery,
    '        pending = runtime.get("pending_turn") if isinstance(runtime.get("pending_turn"), dict) else None\n        revision = int(runtime.get("state_revision") or 0)\n',
    '        pending = runtime.get("pending_turn") if isinstance(runtime.get("pending_turn"), dict) else None\n        pending_validation = scene_validation.pending_validation_diagnostics(pending) if pending else None\n        revision = int(runtime.get("state_revision") or 0)\n',
    "integrity pending diagnostics",
)
recovery = replace_once(
    recovery,
    '        if pending:\n            if not isinstance(context, dict) or context.get("turn_id") != pending.get("turn_id"):\n',
    '        if pending:\n            if pending_validation:\n                warnings.append({\n                    "code": "pending_scene_validation_failure",\n                    "turn_id": pending.get("turn_id"),\n                    "attempts": pending_validation.get("attempts"),\n                    "last_error_codes": (pending_validation.get("last_failure") or {}).get("error_codes", []),\n                    "message": "Canonical state is healthy; the preserved pending scene must be rewritten on the same turn_id, not repaired or discarded.",\n                })\n            if not isinstance(context, dict) or context.get("turn_id") != pending.get("turn_id"):\n',
    "integrity warning for rejected draft",
)
recovery = replace_once(
    recovery,
    '            "pending_turn_id": pending.get("turn_id") if pending else None,\n',
    '            "pending_turn_id": pending.get("turn_id") if pending else None,\n            "pending_validation": pending_validation,\n            "session_corruption_detected": bool(errors),\n',
    "integrity pending validation response",
)
recovery = replace_once(
    recovery,
    '            "next_action": "waitForPlayerInput" if not errors else "repairSessionState",\n',
    '            "next_action": "repairSessionState" if errors else ("getPreflight" if pending else "waitForPlayerInput"),\n',
    "integrity next action",
)
write("app/session_recovery.py", recovery)


# Documentation keeps failure recovery unambiguous.
readme = read("README.md")
anchor = "Стартовая сцена — отдельный нулевой переход:"
if anchor in readme and "Pending validation diagnostics" not in readme:
    readme += '''\n\n## Pending validation diagnostics\n\nОшибка проверки сцены не означает повреждение сессии. Сервер сам считает попытки, хранит точные error codes в `pending_turn.validation_runtime` и возвращает их через `getPreflight` и `getSessionIntegrity`. Игровой ввод, `turn_id` и frozen context сохраняются. Исправление выполняется повторным `applyTurnResult` на том же ходе; `repairSessionState`, reset и удаление pending используются только для настоящего повреждения состояния, а не для невалидного черновика.\n'''
write("README.md", readme)


# Focused regression tests.
test = r'''from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import compact as base
from app.main import app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(base, "DATA_DIR", data_dir)
    monkeypatch.setattr(base, "SESSIONS_DIR", data_dir / "sessions")
    return TestClient(app)


def commit_opening(client: TestClient, sid: str) -> None:
    started = client.post("/api/v1/start", json={"session_id": sid}).json()
    assert started["success"] is True
    opening = client.get(f"/api/v3/sessions/{sid}/start-scene-text").json()
    committed = client.post(
        f"/api/v1/sessions/{sid}/commit-start-scene",
        json={
            "expected_state_revision": opening["state_revision"],
            "exact_text_sha256": opening["exact_text_sha256"],
        },
    ).json()
    assert committed["status"] == "start_scene_committed"


def ready_turn(client: TestClient, sid: str, player_input: str) -> str:
    turn = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": player_input},
    ).json()
    turn_id = turn["turn_id"]
    contract = client.post(
        f"/api/v3/sessions/{sid}/turn-contract",
        json={"turn_id": turn_id},
    ).json()
    manifest = client.post(
        f"/api/v3/sessions/{sid}/required-context/manifest",
        json={"turn_id": turn_id},
    ).json()
    for index in range(manifest["total_chunks"]):
        chunk = client.post(
            f"/api/v3/sessions/{sid}/required-context/chunk",
            json={"turn_id": turn_id, "chunk_index": index},
        ).json()
        assert chunk["success"] is True
    assert contract["success"] is True
    return turn_id


def test_server_owns_attempt_counter_and_diagnostics_survive_preflight(client: TestClient) -> None:
    sid = "pending-diagnostics-proof"
    commit_opening(client, sid)
    turn_id = ready_turn(client, sid, "Остаюсь у двери и слушаю дальше.")

    invalid = {
        "turn_id": turn_id,
        "visible_scene_text": "Акира согласилась уйти с незнакомцем.",
        "scene_validation": {"repair_attempt": 99},
    }
    first = client.post(f"/api/v1/sessions/{sid}/apply-turn-result", json=invalid).json()
    assert first["status"] == "rewrite_required"
    assert first["repair_packet"]["repair_attempt"] == 1
    assert first["repair_packet"]["attempt_counter_owner"] == "server"
    assert first["pending_turn_preserved"] is True
    assert first["do_not_reset_or_discard_pending_turn"] is True
    assert first["validation_errors"][0]["code"] == "major_pov_choice_not_in_player_input"

    preflight = client.get(f"/api/v3/sessions/{sid}/preflight").json()
    diagnostics = preflight["pending_validation"]
    assert preflight["next_action"] == "rewriteSceneFromFrozenSnapshotAndRetryApply"
    assert diagnostics["attempts"] == 1
    assert diagnostics["last_failure"]["error_codes"] == ["major_pov_choice_not_in_player_input"]
    assert diagnostics["session_corruption_detected"] is False

    integrity = client.get(f"/api/v1/sessions/{sid}/integrity").json()
    assert integrity["status"] == "healthy"
    assert integrity["session_corruption_detected"] is False
    assert integrity["pending_turn_id"] == turn_id
    assert integrity["pending_validation"]["attempts"] == 1
    assert integrity["next_action"] == "getPreflight"
    assert any(item["code"] == "pending_scene_validation_failure" for item in integrity["warnings"])


def test_retry_limit_is_server_counted_but_pending_turn_is_never_discarded(client: TestClient) -> None:
    sid = "pending-retry-limit-proof"
    commit_opening(client, sid)
    turn_id = ready_turn(client, sid, "Остаюсь на месте.")
    body = {"turn_id": turn_id, "visible_scene_text": "Акира согласилась уйти с незнакомцем."}

    statuses = []
    attempts = []
    for _ in range(4):
        result = client.post(f"/api/v1/sessions/{sid}/apply-turn-result", json=body).json()
        statuses.append(result["status"])
        attempts.append(result["repair_packet"]["repair_attempt"])
        assert result["pending_turn_preserved"] is True
    assert statuses == ["rewrite_required", "rewrite_required", "rewrite_required", "full_regeneration_required"]
    assert attempts == [1, 2, 3, 4]
    assert base.get_pending_turn(sid)["turn_id"] == turn_id


def test_state_patch_failure_is_persisted_and_valid_retry_commits_same_turn(client: TestClient) -> None:
    sid = "pending-state-patch-proof"
    commit_opening(client, sid)
    turn_id = ready_turn(client, sid, "Подхожу ближе к двери и прислушиваюсь.")

    rejected = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Акира остановилась у двери и прислушалась к голосам внизу.",
            "current_state_patch": {"current_time": "00:10"},
        },
    ).json()
    assert rejected["status"] == "rewrite_required"
    assert rejected["repair_packet"]["failure_stage"] == "current_state_patch"
    assert rejected["validation_errors"][0]["code"] == "direct_clock_patch_blocked"

    preflight = client.get(f"/api/v3/sessions/{sid}/preflight").json()
    assert preflight["pending_validation"]["last_failure"]["failure_stage"] == "current_state_patch"

    applied = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Акира остановилась у двери и прислушалась к голосам внизу.",
        },
    ).json()
    assert applied["status"] == "applied"
    assert applied["state_revision"] == 2
    assert base.get_pending_turn(sid) is None

    clean = client.get(f"/api/v3/sessions/{sid}/preflight").json()
    assert clean["pending_validation"] is None
    assert clean["next_action"] == "waitForPlayerInput"
'''
write("tests/test_pending_validation_diagnostics.py", test)


# Remove one-shot installation machinery from the verified branch commit.
for relative in (
    "tools/install_pending_validation_diagnostics.py",
    ".github/workflows/install-pending-validation-diagnostics.yml",
):
    target = ROOT / relative
    if target.exists():
        target.unlink()
