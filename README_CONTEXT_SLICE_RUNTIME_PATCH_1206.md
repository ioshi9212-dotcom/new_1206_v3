# Context Slice Runtime Patch — 1206 v3

Patch-only слой для новой отдельной Railway-репы `new_1206_v3`.

## Что делает

- `startSession` и `createSession` теперь возвращают маленький ack, а не огромный scene_contract.
- Добавляет цепочку:
  - `getPreflight`
  - `requestContextSlice`
  - `requestMoreContext`
  - `getStartSceneText`
- Старые `/api/v2/.../scene-contract` оставлены как безопасные указатели, но больше не возвращают полные YAML-карточки.
- Полные карточки остаются на Railway; GPT получает только смысловые блоки.
- Расписание и ambient NPC пока placeholder_empty: API не должен выдумывать рейды/смены/фоновых NPC.

## После заливки

1. Redeploy Railway.
2. Проверить `/health`: версия `0.3.186-v3-preflight-context-slice`.
3. В Custom GPT заново импортировать schema из `/openapi.json` или `/openapi-actions.json`.
4. Стартовая цепочка GPT должна быть: `startSession` → `getPreflight` → `requestContextSlice` → при необходимости `getStartSceneText`.
