from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
old_preflight_fields = '''        "pending_turn": {
            "turn_id": pending.get("turn_id"),
            "turn_number": pending.get("turn_number"),
            "base_revision": pending.get("base_revision"),
            "player_input": pending.get("player_input"),
        } if pending else None,
        "context_snapshot": {
'''
new_preflight_fields = '''        "pending_turn": {
            "turn_id": pending.get("turn_id"),
            "turn_number": pending.get("turn_number"),
            "base_revision": pending.get("base_revision"),
            "player_input": pending.get("player_input"),
        } if pending else None,
        "pending_validation": pending_validation,
        "context_snapshot": {
'''
context = replace_once(context, old_preflight_fields, new_preflight_fields, "preflight pending diagnostics field")
write("app/context_request_runtime_patch.py", context)


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


readme = read("README.md")
if "## Pending validation diagnostics" not in readme:
    readme += '''\n\n## Pending validation diagnostics\n\nОшибка проверки сцены не означает повреждение сессии. Сервер сам считает попытки, хранит точные error codes в `pending_turn.validation_runtime` и возвращает их через `getPreflight` и `getSessionIntegrity`. Игровой ввод, `turn_id` и frozen context сохраняются. Исправление выполняется повторным `applyTurnResult` на том же ходе; `repairSessionState`, reset и удаление pending используются только для настоящего повреждения состояния, а не для невалидного черновика.\n'''
write("README.md", readme)


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


for relative in (
    "tools/install_pending_validation_diagnostics.py",
    "tools/continue_pending_validation_diagnostics.py",
    ".github/workflows/install-pending-validation-diagnostics.yml",
):
    target = ROOT / relative
    if target.exists():
        target.unlink()
