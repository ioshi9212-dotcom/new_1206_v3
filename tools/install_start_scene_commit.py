from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.11.0-v3-start-scene-commit"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


compact = read("app/compact.py")
compact = replace_once(
    compact,
    'APP_VERSION = "0.10.0-v3-quarantine-repair"',
    f'APP_VERSION = "{VERSION}"',
    "compact version",
)
compact = replace_once(
    compact,
    '        "last_repair": None,\n        "updated_at": datetime.utcnow().isoformat(),\n',
    '        "last_repair": None,\n        "last_start_scene_commit": None,\n        "updated_at": datetime.utcnow().isoformat(),\n',
    "default start scene commit",
)
compact = replace_once(
    compact,
    '    runtime.setdefault("last_repair", None)\n    return runtime\n',
    '    runtime.setdefault("last_repair", None)\n    runtime.setdefault("last_start_scene_commit", None)\n    return runtime\n',
    "runtime start scene commit default",
)
write("app/compact.py", compact)

for path in (
    "app/scene_validation.py",
    "app/session_recovery.py",
    "app/session_repair.py",
):
    text = read(path)
    text = text.replace(
        '0.10.0-v3-quarantine-repair',
        VERSION,
    )
    write(path, text)

recovery = read("app/session_recovery.py")
recovery = replace_once(
    recovery,
    '    after_writes: dict[str, Any],\n    reason: str,\n) -> tuple[str, dict[str, Any]]:\n',
    '    after_writes: dict[str, Any],\n    reason: str,\n    kind: str = "apply",\n) -> tuple[str, dict[str, Any]]:\n',
    "snapshot kind signature",
)
recovery = replace_once(
    recovery,
    '        "kind": "apply",\n        "status": "active",\n',
    '        "kind": _safe_text(kind, 80) or "apply",\n        "status": "active",\n',
    "snapshot kind value",
)
recovery = replace_once(
    recovery,
    '            "next_action": "waitForPlayerInput",\n        }\n\n\ndef integrity_report',
    '            "next_action": (\n'
    '                "getPreflight"\n'
    '                if isinstance(writes.get(CURRENT_STATE_FILE), dict)\n'
    '                and writes[CURRENT_STATE_FILE].get("start_scene_exact_text_required")\n'
    '                and not writes[CURRENT_STATE_FILE].get("start_scene_completed")\n'
    '                else "waitForPlayerInput"\n'
    '            ),\n'
    '        }\n\n\ndef integrity_report',
    "rollback next action",
)
write("app/session_recovery.py", recovery)

production = read("app/production_runtime_patch.py")
production = replace_once(
    production,
    'from app import session_repair\n',
    'from app import session_repair\nfrom app import start_scene_commit\n',
    "production start commit import",
)
production = replace_once(
    production,
    '        "trusted_snapshot_protocol": "full_before_and_after_state_images_with_self_hash",\n',
    '        "trusted_snapshot_protocol": "full_before_and_after_state_images_with_self_hash",\n'
    '        "start_scene_protocol": "canonical_hash_revision_guard_atomic_commit_before_visible_output",\n',
    "health start protocol",
)
route = '''@app.post("/api/v1/sessions/{session_id}/commit-start-scene", operation_id="commitStartScene")
def commit_start_scene(
    session_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return start_scene_commit.commit_start_scene(session_id, body)


'''
production = replace_once(
    production,
    '@app.post("/api/v1/sessions/{session_id}/turn", operation_id="processTurn")\n',
    route + '@app.post("/api/v1/sessions/{session_id}/turn", operation_id="processTurn")\n',
    "commit start route",
)
production = replace_once(
    production,
    '            "required_sequence": ["getPreflight", "getStartSceneText", "show exact_text", "waitForPlayerInput"],\n',
    '            "required_sequence": [\n'
    '                "getPreflight",\n'
    '                "getStartSceneText",\n'
    '                "commitStartScene with state_revision and exact_text_sha256",\n'
    '                "show commitStartScene.visible_scene_text exactly once",\n'
    '                "waitForPlayerInput",\n'
    '            ],\n',
    "start required sequence",
)
production = replace_once(
    production,
    '    process_turn_body_schema = _object_schema({\n',
    '    commit_start_scene_body_schema = _object_schema({\n'
    '        "expected_state_revision": {"type": "integer", "description": "Exact state_revision returned by getStartSceneText."},\n'
    '        "exact_text_sha256": {"type": "string", "description": "Canonical opening hash returned by getStartSceneText."},\n'
    '    }, required=["expected_state_revision", "exact_text_sha256"])\n'
    '    process_turn_body_schema = _object_schema({\n',
    "commit start schema",
)
production = replace_once(
    production,
    '            "description": "Transactional API: applyTurnResult commits the scene with full trusted before/after state images. getSessionIntegrity detects revision, hash and JSON damage. repairSessionState requires explicit confirmation and the exact revision, preserves the damaged state in quarantine, then restores a trusted snapshot under a new monotonic revision. Legacy fallback never loses a turn without allow_turn_loss=true.",\n',
    '            "description": "Transactional API: getStartSceneText returns the canonical opening and hash; commitStartScene atomically records it before visible output. Normal turns then use one protected turn_id, frozen ordered context chunks, precommit validation and atomic apply. Integrity, rollback and quarantine repair remain revision-guarded.",\n',
    "openapi description",
)
production = replace_once(
    production,
    '            "/api/v1/sessions/{session_id}/turn": {\n',
    '            "/api/v1/sessions/{session_id}/commit-start-scene": {\n'
    '                "post": {"operationId": "commitStartScene", "summary": "Atomically commit the exact canonical opening before showing it to the player.", "parameters": [_session_path_param()], "requestBody": {"required": True, "content": {"application/json": {"schema": commit_start_scene_body_schema}}}, "responses": {"200": _response("Committed opening scene")}}\n'
    '            },\n'
    '            "/api/v1/sessions/{session_id}/turn": {\n',
    "openapi commit path",
)
write("app/production_runtime_patch.py", production)

context = read("app/context_request_runtime_patch.py")
context = replace_once(
    context,
    'from app import compact as base\n',
    'from app import compact as base\nfrom app import start_scene_commit\n',
    "context start commit import",
)
context = replace_once(
    context,
    '        writer_note = "Show exact start scene text, then stop and wait for non-empty player input."\n',
    '        writer_note = "Fetch the exact opening, commit it atomically, then show the committed visible_scene_text once and stop."\n',
    "preflight opening note",
)
context = replace_once(
    context,
    '        "visible_scene_output_allowed": start_scene_required and not pending,\n',
    '        "visible_scene_output_allowed": False,\n',
    "preflight visible gate",
)
old_start = '''    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_start_scene_text",
        "exact_text_required": required,
        "exact_text": _extract_start_scene_text() if required else "",
        "after_output_instruction": "Output exact_text once, then stop. Do not call processTurn with an empty/stale input and do not continue the scene. Wait for the player's next non-empty message.",
    }
'''
new_start = '''    exact_text = start_scene_commit.exact_start_scene_text() if required else ""
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
'''
context = replace_once(context, old_start, new_start, "get start scene response")
write("app/context_request_runtime_patch.py", context)

apply = read("app/v3_apply_turn_result_runtime_patch.py")
apply = replace_once(
    apply,
    'from app import session_recovery\n',
    'from app import session_recovery\nfrom app import start_scene_commit\n',
    "apply start commit import",
)
marker = '''        if _plan_scene_history(
            sid,
            turn_id,
'''
insert = '''        compatibility_opening_backfilled = start_scene_commit.plan_compatibility_backfill(
            sid,
            writes,
            committed_current,
            current,
            state_revision=new_revision,
            committed_at=datetime.utcnow().isoformat(),
        )
        if compatibility_opening_backfilled:
            changed.append(SCENE_HISTORY_FILE)

        if _plan_scene_history(
            sid,
            turn_id,
'''
apply = replace_once(apply, marker, insert, "compatibility opening backfill")
write("app/v3_apply_turn_result_runtime_patch.py", apply)

logic = read("scenes/start_scene_logic.md")
logic = replace_once(
    logic,
    'Если `current_state.start_scene_exact_text_required = true`, ChatGPT должен вывести текст из `scene_contract.start_scene.exact_text`, а не писать новую версию сцены.\nПосле вывода сцены API должен получить `applyTurnResult`, чтобы отметить сыгранный первый вывод и сохранить видимый текст в историю.\n',
    'Если `current_state.start_scene_exact_text_required = true`, ChatGPT получает точный текст через `getStartSceneText`, но ещё не показывает его. Затем вызывает `commitStartScene` с возвращёнными `state_revision` и `exact_text_sha256`. Только ответ `status: start_scene_committed` разрешает показать возвращённый `visible_scene_text` один раз. Opening сохраняется в историю отдельной ревизией и не проходит через обычный `pending_turn`.\n',
    "start scene logic commit rule",
)
write("scenes/start_scene_logic.md", logic)

readme = read("README.md")
readme = replace_once(
    readme,
    'Стартовая сцена — отдельный нулевой шаг: `getStartSceneText` отдаёт точный текст,\nпосле чего GPT обязан остановиться и дождаться первого непустого ответа игрока.\n',
    'Стартовая сцена — отдельный нулевой переход: `getStartSceneText` отдаёт точный текст и hash без права показа; `commitStartScene` атомарно записывает opening в историю, создаёт ревизию и rollback-снимок. Только после успешного commit GPT показывает возвращённый `visible_scene_text` один раз и ждёт первый непустой ответ игрока.\n',
    "README start scene protocol",
)
write("README.md", readme)

for test_path in (
    "tests/test_runtime_regressions.py",
    "tests/test_scene_validation_runtime.py",
    "tests/test_session_recovery.py",
    "tests/test_session_repair.py",
):
    text = read(test_path)
    text = text.replace('0.10.0-v3-quarantine-repair', VERSION)
    write(test_path, text)

for relative in (
    "tools/install_start_scene_commit.py",
    ".github/workflows/install-start-scene-commit.yml",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()
