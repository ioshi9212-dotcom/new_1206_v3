# Scene Contract Requirements 1206

Этот файл описывает, каким должен быть `scene_contract`, который Railway/API отдаёт GPT перед написанием сцены.

## Главная идея

GPT не должен сам собирать контекст из репозитория. Он должен получить компактный `scene_contract` на один ход и писать только по нему.

`scene_contract` обязан отвечать на вопросы:

1. Где и когда сцена?
2. Кто текущий POV?
3. Кто реально в сцене, говорит, слышит, наблюдает?
4. Какие знания доступны каждому участнику?
5. Какие отношения нужны именно сейчас?
6. Какие NPC могут появиться по локации?
7. Какие правила формата сцены и footer действуют?
8. Что после сцены нужно записать в state?

## Обязательные блоки

```yaml
runtime:
  session_id:
  builder_version:
  runtime_version:

current_frame:
  date:
  time_of_day:
  location:
  location_privacy:
  pov_character_id:
  scene_phase:
  current_scene_goal:

participants:
  pov:
  active:
  present:
  speaking:
  observing:
  addressed:
  nearby:
  audible:

calendar_slice:
  current_only:
  current_pressure:
  allowed_hooks:
  forbidden_future_spoilers:

characters:
  <character_id>:
    role_digest:
    voice_digest:
    current_goal:
    loaded_because:

character_memory:
  <character_id>:
    saw:
    heard:
    knows_as_fact:
    assumes:
    mistaken_beliefs:
    does_not_know:
    protected_moments_relevant_now:

relationship_pairs:
  <pair_id>:
    loaded_because:
    surface_dynamic:
    side_a_to_b:
    side_b_to_a:
    active_open_threads:
    protected_moments:

npc_context:
  mode: none | light | medium | full | function_based
  rules_digest:
  relevant_session_npcs:

location_context:
  privacy:
  density:
  audibility:
  allowed_interruptions:

recent_scene_memory:
  relevant_events:
  unresolved_hooks:

output_contract:
  scene_body_pov:
  dialogue_format:
  footer_limits:
  quality_gates:

state_write_contract:
  every_turn_write:
  must_save_if_changed:
  protected_moment_detection:
```

## Требования к компактности

- Не вставлять большие файлы целиком, если нужен один digest.
- Для персонажа обычно достаточно 3–8 коротких пунктов.
- Для пары отношений обычно достаточно 3–8 коротких пунктов.
- Для календаря — только текущий день/окно.
- Для NPC — по режиму локации, не всегда полный файл.

## POV-aware loading

Акира не добавляется автоматически. Если POV Хару и Акиры нет в сцене, scene_contract не должен включать карточки Акиры, её memory и пары `akira__*`.

## Output rules are not world knowledge

Правила формата, footer, качества, календаря и сборщика — служебные инструкции для GPT. Персонажи не знают их внутри сцены.
