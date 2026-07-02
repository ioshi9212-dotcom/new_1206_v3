# Context Builder Rules Patch — 1206 v3

Patch-only слой для сборщика `scene_contract`.

## Что добавляет

```txt
state/context_loading/scene_context_builder_rules_1206.json
state/context_loading/character_selection_rules_1206.json
state/context_loading/past_trigger_rules_1206.json
state/context_loading/forbidden_fallback_rules_1206.json
api_contracts/scene_contract.example.json
```

## Что НЕ трогает

```txt
characters/
state/character_memory/
state/relationship_pairs/
calendar/
canon_lore/
gpt/
app/
```

## Главная логика

Сборщик сначала определяет текущую сцену: POV, место, время, цель, последний ввод игрока. Потом выбирает только тех персонажей, которые реально нужны сцене.

Для выбранного персонажа источник поведения:

```txt
characters/<id>/main.yaml
characters/<id>/character.yaml
characters/<id>/knowledge.yaml
state/character_memory/<id>.json
```

`characters/<id>/past.yaml` добавляется только по триггеру прошлого.

## Важно

Акира не добавляется в сцену автоматически. Если она не POV, не присутствует, не упомянута с реальной сценической важностью и не нужна цели сцены — её карточки и память не грузятся.

Короткие отдельные summary-файлы персонажей не создавать. Они снова сделают персонажей плоскими.

## Следующий шаг после этого patch

После добавления этих правил можно переходить к коду builder-а: он должен фактически собирать `scene_contract` по `api_contracts/scene_contract.schema.json` и этим правилам.
