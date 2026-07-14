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


def ready_turn(client: TestClient, sid: str, player_input: str, **fields: object) -> str:
    created = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": player_input, **fields},
    ).json()
    assert created["success"] is True
    turn_id = created["turn_id"]
    contract = client.post(
        f"/api/v3/sessions/{sid}/turn-contract",
        json={"turn_id": turn_id},
    ).json()
    assert contract["success"] is True
    manifest = client.post(
        f"/api/v3/sessions/{sid}/required-context/manifest",
        json={"turn_id": turn_id, "turn_contract": contract},
    ).json()
    for index in range(manifest["total_chunks"]):
        chunk = client.post(
            f"/api/v3/sessions/{sid}/required-context/chunk",
            json={"turn_id": turn_id, "chunk_index": index, "turn_contract": contract},
        ).json()
        assert chunk["success"] is True
    return turn_id


def apply_turn(client: TestClient, sid: str, turn_id: str, *, marker: str) -> dict:
    return client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": f"Сцена {marker}: изменилась одна наблюдаемая деталь, но герой не принял важного решения.",
            "current_state_patch": {"recovery_probe": marker},
            "scene_continuity_patch": {"recovery_probe": marker},
            "change_reason": f"recovery test {marker}",
        },
    ).json()


def test_apply_creates_revision_snapshot_change_journal_and_integrity_report(client: TestClient) -> None:
    sid = "recovery-apply-snapshot"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id = ready_turn(client, sid, "Осматриваю комнату перед следующим шагом.")

    applied = apply_turn(client, sid, turn_id, marker="first")

    assert applied["status"] == "applied"
    assert applied["rollback_available"] is True
    assert applied["rollback_snapshot_file"].startswith("state/revision_snapshots/revision_000001_")
    snapshot = base.read_session_json(applied["rollback_snapshot_file"], sid, default={})
    assert snapshot["schema"] == "turn_revision_snapshot_v2"
    assert snapshot["status"] == "active"
    assert snapshot["base_revision"] == 0
    assert snapshot["state_revision"] == 1
    assert "state/current_state.json" in snapshot["before"]
    assert snapshot["after_sha256"]["state/current_state.json"]

    journal = base.read_session_json("state/change_journal.json", sid, default={})
    assert journal["entries"][-1]["kind"] == "apply"
    assert journal["entries"][-1]["turn_id"] == turn_id
    assert journal["entries"][-1]["snapshot_file"] == applied["rollback_snapshot_file"]

    integrity = client.get(f"/api/v1/sessions/{sid}/integrity").json()
    assert integrity["status"] == "healthy"
    assert integrity["rollback_available"] is True
    assert integrity["errors"] == []


def test_rollback_restores_all_changed_files_and_uses_new_revision(client: TestClient) -> None:
    sid = "recovery-rollback"
    client.post("/api/v1/start", json={"session_id": sid})
    before_current = base.read_session_json("state/current_state.json", sid, default={})
    scene_continuity_target = base._safe_session_target(sid, "state/scene_continuity_state.json")
    assert scene_continuity_target.exists() is False

    turn_id = ready_turn(client, sid, "Проверяю край стола и остаюсь на месте.")
    applied = apply_turn(client, sid, turn_id, marker="rollback-me")
    assert applied["status"] == "applied"
    assert scene_continuity_target.is_file()
    assert base.read_session_json("state/current_state.json", sid, default={})["recovery_probe"] == "rollback-me"

    rolled_back = client.post(
        f"/api/v1/sessions/{sid}/rollback-last-turn",
        json={"expected_state_revision": 1, "reason": "test undo"},
    ).json()

    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["rolled_back_turn_id"] == turn_id
    assert rolled_back["rolled_back_state_revision"] == 1
    assert rolled_back["state_revision"] == 2
    assert scene_continuity_target.exists() is False

    restored_current = base.read_session_json("state/current_state.json", sid, default={})
    assert restored_current.get("recovery_probe") == before_current.get("recovery_probe")
    assert restored_current["state_revision"] == 2
    runtime = base.read_turn_runtime(sid)
    assert runtime["pending_turn"] is None
    assert runtime["state_revision"] == 2
    assert runtime["last_state_transition"]["kind"] == "rollback"
    assert runtime["last_rollback"]["rolled_back_turn_id"] == turn_id

    history = base.read_session_json("state/scene_history.json", sid, default=[])
    entries = history.get("entries", []) if isinstance(history, dict) else history
    assert not any(isinstance(item, dict) and item.get("turn_id") == turn_id for item in entries)

    journal = base.read_session_json("state/change_journal.json", sid, default={})
    assert [item["kind"] for item in journal["entries"][-2:]] == ["apply", "rollback"]
    snapshot = base.read_session_json(applied["rollback_snapshot_file"], sid, default={})
    assert snapshot["status"] == "rolled_back"

    integrity = client.get(f"/api/v1/sessions/{sid}/integrity").json()
    assert integrity["status"] == "healthy"
    assert integrity["state_revision"] == 2
    assert integrity["rollback_available"] is False

    duplicate = client.post(
        f"/api/v1/sessions/{sid}/rollback-last-turn",
        json={"expected_state_revision": 2},
    ).json()
    assert duplicate["status"] == "rollback_rejected_already_rolled_back"


def test_pending_turn_blocks_rollback(client: TestClient) -> None:
    sid = "recovery-pending-block"
    client.post("/api/v1/start", json={"session_id": sid})
    first = ready_turn(client, sid, "Смотрю на окно.")
    assert apply_turn(client, sid, first, marker="applied")["status"] == "applied"

    pending = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": "Теперь подхожу ближе к двери."},
    ).json()
    assert pending["success"] is True

    result = client.post(
        f"/api/v1/sessions/{sid}/rollback-last-turn",
        json={"expected_state_revision": 1},
    ).json()
    assert result["status"] == "rollback_rejected_pending_turn"
    assert result["pending_turn_id"] == pending["turn_id"]


def test_prepared_transaction_recovers_writes_deletes_and_records_audit(client: TestClient) -> None:
    sid = "recovery-interrupted-transaction"
    client.post("/api/v1/start", json={"session_id": sid})
    base.write_json("state/recovery_keep.json", {"partial": True}, session_id=sid)
    base.write_json("state/recovery_delete.json", {"remove": True}, session_id=sid)
    base.write_json(
        "state/transactions/interrupted.json",
        {
            "schema": "json_transaction_v2",
            "transaction_id": "interrupted",
            "status": "prepared",
            "prepared_at": "2026-07-14T00:00:00+00:00",
            "writes": [{"path": "state/recovery_keep.json", "data": {"final": True}}],
            "deletes": ["state/recovery_delete.json"],
        },
        session_id=sid,
    )

    base.ensure_session(sid)

    assert base.read_session_json("state/recovery_keep.json", sid, default={}) == {"final": True}
    assert base._safe_session_target(sid, "state/recovery_delete.json").exists() is False
    assert base._safe_session_target(sid, "state/transactions/interrupted.json").exists() is False
    audit = base.read_session_json("state/recovery_audit.json", sid, default={})
    assert audit["entries"][-1]["transaction_id"] == "interrupted"
    assert audit["entries"][-1]["recovered_writes"] == ["state/recovery_keep.json"]
    assert audit["entries"][-1]["recovered_deletes"] == ["state/recovery_delete.json"]


def test_integrity_report_detects_out_of_transaction_tampering(client: TestClient) -> None:
    sid = "recovery-integrity-tamper"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id = ready_turn(client, sid, "Сохраняю текущую позицию.")
    applied = apply_turn(client, sid, turn_id, marker="integrity")
    assert applied["status"] == "applied"

    current = base.read_session_json("state/current_state.json", sid, default={})
    current["recovery_probe"] = "tampered outside transaction"
    base.write_json("state/current_state.json", current, session_id=sid)

    integrity = client.get(f"/api/v1/sessions/{sid}/integrity").json()
    assert integrity["status"] == "integrity_errors"
    assert any(item["code"] == "canonical_hash_mismatch" for item in integrity["errors"])
    rollback = client.post(
        f"/api/v1/sessions/{sid}/rollback-last-turn",
        json={"expected_state_revision": 1},
    ).json()
    assert rollback["status"] == "rollback_rejected_integrity_mismatch"


def test_openapi_exposes_integrity_and_rollback_actions(client: TestClient) -> None:
    schema = client.get("/openapi-actions.json").json()
    assert schema["info"]["version"] == "0.11.0-v3-start-scene-commit"
    assert schema["paths"]["/api/v1/sessions/{session_id}/integrity"]["get"]["operationId"] == "getSessionIntegrity"
    rollback = schema["paths"]["/api/v1/sessions/{session_id}/rollback-last-turn"]["post"]
    assert rollback["operationId"] == "rollbackLastTurn"
    request = rollback["requestBody"]["content"]["application/json"]["schema"]
    assert "expected_state_revision" in request["properties"]
