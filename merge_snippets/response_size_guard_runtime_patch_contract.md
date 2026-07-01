# OPTIONAL MERGE SNIPPET — app/response_size_guard_runtime_patch.py

The runtime patch already wraps `_small_output_contract()` automatically.
If you prefer a direct edit instead of importing `app/bottom_block_compact_runtime_patch.py`, add these strings to `_small_output_contract()["rules"]`:

```python
"Bottom block is a short interface panel, not a state dump, protocol report, recap, or hidden-lore summary.",
"State block must keep numeric stats when available, but only as 2-3 compact lines: memory/emotions/flow; strength/endurance/agility/fatigue; combat/energy/risk.",
"State block must not include 'new facts', protocol history, offscreen reports, clothing/inventory lists, long medical explanations, or scene recap.",
"Relationships block format: Name: single signed number · 2-3 words describing relationship type/status.",
"Relationships block must never use paired numbers like +12/-1 or +12 / -1.",
"Relationships block must not explain what happened; it must describe current relation quality only.",
```
