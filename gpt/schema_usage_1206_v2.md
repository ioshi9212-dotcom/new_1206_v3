# GPT Actions Schema Usage — 1206v2

Production URL:

`https://1206v2-production.up.railway.app`

Actions schema URL:

`https://1206v2-production.up.railway.app/openapi-actions.json`

Health:

`https://1206v2-production.up.railway.app/health`

Volume debug:

`https://1206v2-production.up.railway.app/debug/volume`

## Main operations

- `healthCheck` / `health`
- `debugVolume`
- `createSession`
- `processTurn`
- `getTurnContract` / `getSessionTurnContract`
- `getFastRenderContext` ← основной быстрый режим для обычной игры
- `getFastRenderContext` ← полный fallback / диагностика
- Полный файловый аудит ← только вручную вне gameplay
- `submitTurnResult`
- `applyTurnResult`
- `readProjectFile`

## Recommended fast test

1. `health`
2. `createSession(session_id="main-1206-v2", reset=false)`
3. `getSessionTurnContract(session_id="main-1206-v2")`
4. `getFastRenderContext(session_id="main-1206-v2", max_total_chars=45000, per_file_chars=8000)`
5. Render scene.
6. `applyTurnResult` after meaningful scene changes.

## Full diagnostic test

1. `health`
2. `createSession(session_id="main-1206-v2", reset=false)`
3. `getSessionTurnContract(session_id="main-1206-v2")`
4. `getFastRenderContext(session_id="main-1206-v2")`
5. Не вызывай файловые chunks в обычной сцене.
6. If `has_more=true`, call next chunks only for full audit mode. Normal gameplay should use `getFastRenderContext`.

## Volume expectations

`debugVolume` must show mount `/data` or Railway-provided volume path and persistent session files.
