# PROMPT OUTPUT RULES PATCH 1206

Маленький patch-only слой для правил ответа ChatGPT после получения `scene_contract`.

## Содержимое

```txt
gpt/scene_output/chatgpt_output_contract_rules_1206_ru.md
gpt/scene_output/prompt_assembly_rules_1206.json
gpt/scene_output/scene_quality_control_1206_ru.md
gpt/scene_output/scene_footer_rules_1206_ru.md
api_contracts/scene_contract.example.json
```

## Что делает

- закрепляет, что ChatGPT работает только по `scene_contract`;
- запрещает применять state напрямую;
- требует `proposed_updates` вместо прямой записи файлов;
- связывает видимый footer с `scene.footer` и `player_options`;
- усиливает запреты на hidden past, absent characters и чужие знания;
- добавляет список prompt rule files для сборки запроса.

## Что НЕ трогает

```txt
characters/
state/character_memory/
state/relationship_pairs/
calendar/
canon_lore/
app/
Railway
```

## Важно

Это не builder и не код Railway. Это только правила/контракт для GPT-ответа.
