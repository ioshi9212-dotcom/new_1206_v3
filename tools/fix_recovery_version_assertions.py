from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "tests/test_scene_validation_runtime.py"
text = path.read_text(encoding="utf-8")
old = 'assert schema["info"]["version"] == "0.8.0-v3-scene-validation-rewrite-gate"'
new = 'assert schema["info"]["version"] == "0.9.0-v3-session-recovery-rollback"'
if text.count(old) != 1:
    raise RuntimeError(f"Expected one old version assertion, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
Path(__file__).unlink()
