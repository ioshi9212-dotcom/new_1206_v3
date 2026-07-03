# Director Mode Stage 1 — ZIP patch for `new_1206_v3`

Это ZIP-пакет для первого этапа переделки: **сценарная комната вместо live-новеллы в Custom GPT Actions**.

## Что делает патч

Патч не удаляет старую игровую систему из кода. Он добавляет новый слой Director Mode и меняет `/openapi-actions.json`, чтобы Custom GPT видел только сценарные endpoints.

Старые live-game endpoints остаются в приложении, но **не показываются в Actions-схеме**:

- `processTurn`
- `applyTurnResult`
- `getSceneContract`
- `getTurnContract`
- `getRequiredContextManifest`
- `getRequiredContextChunk`
- `requestContextSlice`
- `requestMoreContext`

## Какие файлы заменить/добавить

Скопировать содержимое ZIP в корень репозитория `new_1206_v3` с заменой файлов.

### Заменяется

- `app/main.py`

### Добавляются

- `app/director_runtime_patch.py`
- `app/director_openapi_patch.py`
- `gpt/director_mode_rules.md`
- `drafts/.gitkeep`

## Новые Actions endpoints

В новой схеме будут только:

- `health`
- `startDirectorDraft`
- `addDirectorContext`
- `rewriteDirectorDraft`
- `validateDirectorDraft`
- `saveDirectorDraft`
- `getDirectorDraft`

## Как пользоваться в Custom GPT

Пользователь пишет обычным текстом, не JSON:

```text
Сцена: дом Джуна, ночь. Акира выходит на лестницу.
В сцене Джун, Ирэй, Эмма.
Эмма давит на Ирэя. Ирэй видит, что Акира его не узнаёт.
Без Самуэля, Рэя, записки, документов и новых людей.
```

GPT должен вызвать `startDirectorDraft`, получить `writer_packet`, а потом написать сцену в чат.

Если нужно добавить контекст:

```text
Добавь прошлое Ирэя, но не раскрывай его в сцене.
```

GPT вызывает `addDirectorContext`.

Если нужно переписать:

```text
Эмма всё ещё звучит как союзница. Сделай её давящей, а не предупреждающей.
```

GPT вызывает `rewriteDirectorDraft`.

## Что не тянется по умолчанию

По умолчанию Director Mode не тянет:

- `state/current_state.json`
- `state/relationships.json`
- `state/knowledge_state.json`
- `state/inventory_state.json`
- `state/future_locks_progress.json`
- `state/scene_history.json`
- `calendar/*`
- `schedule/*`
- `characters/*/knowledge.yaml`
- `characters/*/past.yaml`
- `characters/*/relationships.yaml`

Эти файлы можно добавить только отдельной просьбой пользователя через `addDirectorContext`.

## Проверка после деплоя

Открыть:

```text
https://<railway-domain>/openapi-actions.json
```

В схеме не должно быть `processTurn` и `applyTurnResult`.

Должны быть:

```text
startDirectorDraft
addDirectorContext
rewriteDirectorDraft
validateDirectorDraft
saveDirectorDraft
getDirectorDraft
```

## Важно

Это Stage 1. Он меняет схему и добавляет сценарное хранилище черновиков в `DATA_DIR/drafts/director/...`.

Он пока не делает полноценный экспорт Ren'Py и не сохраняет сцены обратно в GitHub. Это следующие этапы.
