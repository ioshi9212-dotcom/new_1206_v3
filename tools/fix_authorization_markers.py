from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "app/scene_validation.py"
text = TARGET.read_text(encoding="utf-8")
old = '''def _authorized(player_input: str, character_id: str, category: str, pov_id: str) -> bool:
    normalized = player_input.lower().replace("ё", "е")
    marker_found = any(marker.replace("ё", "е") in normalized for marker in AUTH.get(category, ()))
    if character_id == "akira" and pov_id != "akira":
        return marker_found and ("акира" in normalized or "akira" in normalized)
    return marker_found
'''
new = '''def _authorization_marker_found(normalized: str, marker: str) -> bool:
    marker = marker.lower().replace("ё", "е").strip()
    if not marker:
        return False
    if " " in marker:
        phrase = r"\\s+".join(re.escape(part) for part in marker.split())
        return bool(re.search(rf"(?<!\\w){phrase}(?!\\w)", normalized, flags=re.I))
    # Short answers such as `да` and `нет` must be standalone words. Prefix
    # markers such as `соглас`, `обещ` or `энерги` may match inflected words.
    if marker in {"да", "нет"}:
        return bool(re.search(rf"(?<!\\w){re.escape(marker)}(?!\\w)", normalized, flags=re.I))
    return bool(re.search(rf"(?<!\\w){re.escape(marker)}[\\w-]*", normalized, flags=re.I))


def _authorized(player_input: str, character_id: str, category: str, pov_id: str) -> bool:
    normalized = player_input.lower().replace("ё", "е")
    marker_found = any(_authorization_marker_found(normalized, marker) for marker in AUTH.get(category, ()))
    if character_id == "akira" and pov_id != "akira":
        return marker_found and bool(re.search(r"(?<!\\w)(?:акира|akira)(?!\\w)", normalized, flags=re.I))
    return marker_found
'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f"authorization marker patch expected one marker, found {count}")
TARGET.write_text(text.replace(old, new, 1), encoding="utf-8")
Path(__file__).unlink(missing_ok=True)
