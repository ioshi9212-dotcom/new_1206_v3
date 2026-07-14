from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "app/session_repair.py"
text = TARGET.read_text(encoding="utf-8")
old = '            "next_action": "waitForPlayerInput" if healthy else "repairSessionState",\n'
new = '            "next_action": "repairSessionState" if not healthy else ("getPreflight" if pending else "waitForPlayerInput"),\n'
count = text.count(old)
if count != 1:
    raise RuntimeError(f"public integrity next_action patch expected one marker, found {count}")
TARGET.write_text(text.replace(old, new, 1), encoding="utf-8")
Path(__file__).unlink(missing_ok=True)
