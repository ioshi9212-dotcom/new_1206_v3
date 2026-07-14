from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

apply_path = ROOT / "app/v3_apply_turn_result_runtime_patch.py"
text = apply_path.read_text(encoding="utf-8")
old = '''    response["error"] = error
    return response


def _scene_text'''
new = '''    response["error"] = error
    if failure_stage != "scene_gate":
        # Preserve the established API distinction: prose/choice failures ask
        # for a scene rewrite, while invalid state/time/memory/relationship
        # payloads remain rejected corrections. Both keep the same pending turn
        # and now persist exact diagnostics for recovery.
        response["status"] = "rejected"
        response["next_action"] = "applyTurnResult"
        response["correction_required"] = True
        response["repair_packet"]["instruction"] = (
            "Correct only the rejected state/update payload, keep the same pending turn_id and frozen context, "
            "then retry applyTurnResult. Never reset or discard the pending turn for this validation failure."
        )
    return response


def _scene_text'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f"state validation status patch expected one marker, found {count}")
apply_path.write_text(text.replace(old, new, 1), encoding="utf-8")

test_path = ROOT / "tests/test_pending_validation_diagnostics.py"
test = test_path.read_text(encoding="utf-8")
old_assert = '    assert rejected["status"] == "rewrite_required"\n    assert rejected["repair_packet"]["failure_stage"] == "current_state_patch"\n'
new_assert = '    assert rejected["status"] == "rejected"\n    assert rejected["correction_required"] is True\n    assert rejected["repair_packet"]["failure_stage"] == "current_state_patch"\n'
count = test.count(old_assert)
if count != 1:
    raise RuntimeError(f"focused state status assertion expected one marker, found {count}")
test_path.write_text(test.replace(old_assert, new_assert, 1), encoding="utf-8")

Path(__file__).unlink(missing_ok=True)
