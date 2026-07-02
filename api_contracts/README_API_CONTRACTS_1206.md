# API contracts 1206

Patch-only слой для новой структуры `new_1206_v3`.

Он не трогает персонажей, память, отношения, календарь и канон.

## Файлы

```txt
api_contracts/scene_contract.schema.json
api_contracts/chatgpt_scene_response.schema.json
api_contracts/apply_turn_result.schema.json
api_contracts/context_builder_rules_1206.md
api_contracts/README_API_CONTRACTS_1206.md
```

## Зачем нужен слой

Старые репы 1206 v2 и Академия-приквел работали нестабильно не только из-за промта, а из-за сборки контекста:

```txt
данные были разбросаны
персонажи иногда брались из коротких выжимок
Акира могла грузиться fallback-ом
hidden past мог попадать без триггера
relationship/state могли подтягиваться слишком широко
```

Этот слой фиксирует контракт между API и ChatGPT.

## Главный принцип

Персонаж читается из полных карточек:

```txt
characters/<id>/main.yaml
characters/<id>/character.yaml
characters/<id>/knowledge.yaml
state/character_memory/<id>.json
```

`past.yaml` — только по триггеру.

Короткие отдельные summary-файлы персонажей не создавать.

## Как использовать

1. API собирает `scene_contract` по `scene_contract.schema.json`.
2. ChatGPT возвращает сцену и proposed updates по `chatgpt_scene_response.schema.json`.
3. API проверяет proposed updates и применяет только валидные через `apply_turn_result.schema.json`.
4. Если есть нарушение границ знания/hidden past/необоснованный приход персонажа — патч отклоняется или отправляется в repair.

## Не добавлять в этот слой

```txt
characters/
state/character_memory/*.json
state/relationship_pairs/*.json
runtime/characters/
полный app/Railway код
```

Это только API/schema слой.
