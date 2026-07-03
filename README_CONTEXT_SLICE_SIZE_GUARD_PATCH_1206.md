# context_slice_size_guard_patch_1206

Version: `0.3.191-v3-context-slice-size-guard`

Purpose: fix `ResponseTooLargeError` from `requestContextSlice` after the knowledge-boundary patch.

What changed:

- Keeps the speaking-NPC knowledge boundary.
- Stops returning oversized character/relationship/render blocks to Custom GPT Actions.
- `requestContextSlice` now returns an intentionally slim action-safe context slice.
- POV character card is not returned by default; POV state is taken from `current_state`.
- Speaking NPCs still get compact `knowledge_boundary`:
  - `can_say_as_fact`
  - `believes_or_assumes`
  - `does_not_know`
  - `forbidden_as_fact`
- Critical guards for Emma/Irey/Jun are prioritized inside the slim boundary.
- `final_render_contract` remains the last writer-facing block.
- 10/15 turn maintenance flags remain present but compact.

Expected `/health` version:

```txt
0.3.191-v3-context-slice-size-guard
```

After deployment, re-import OpenAPI in Custom GPT Actions.
