from __future__ import annotations

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
    assert rejected["status"] == "rejected"
    assert rejected["correction_required"] is True
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
