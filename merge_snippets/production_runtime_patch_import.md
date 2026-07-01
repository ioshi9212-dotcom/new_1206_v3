# MERGE SNIPPET — app/production_runtime_patch.py

Add this import after other runtime patch imports, preferably after `fast_context_runtime_patch` and after story/NPC patch imports if they exist:

```python
import app.bottom_block_compact_runtime_patch as bottom_block_compact  # noqa: F401
```

Do not replace the whole production file if it already contains newer imports from NPC/story/east-sector patches. Only add this import once.
