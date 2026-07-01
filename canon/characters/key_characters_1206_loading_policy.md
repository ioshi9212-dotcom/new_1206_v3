# Key Characters 1206 Loading Policy — unified standard

## Активный стандарт карточек

Для ключевых персонажей 1206 v2 использовать только новый набор файлов:

```text
characters/<id>/main.yaml
characters/<id>/character.yaml
characters/<id>/knowledge.yaml
characters/<id>/past.yaml
```

Старые файлы `*_main_profile.yaml`, `*_hidden_past.yaml`, `*_knowledge_connections.yaml` считаются legacy-материалом. Их не удалять в рамках этой правки, но не указывать как рабочий стандарт загрузки.

## Always-load для присутствующих персонажей

Если персонаж физически присутствует в сцене, говорит, наблюдает, является адресатом действия или стоит в active/nearby/current_state, грузить:

- `characters/<id>/main.yaml`
- `characters/<id>/character.yaml`
- `characters/<id>/knowledge.yaml`

Для первой ночной сцены 31 августа 1206 важны:

- `characters/akira/main.yaml`
- `characters/akira/character.yaml`
- `characters/akira/knowledge.yaml`
- `characters/jun/main.yaml`
- `characters/jun/character.yaml`
- `characters/jun/knowledge.yaml`
- `characters/irey/main.yaml`
- `characters/irey/character.yaml`
- `characters/irey/knowledge.yaml`
- `characters/emma/main.yaml`
- `characters/emma/character.yaml`
- `characters/emma/knowledge.yaml`

Райден, Рэй, Юна, Мики и другие персонажи грузятся по current_state, scene roster, календарному условию или прямому появлению в сцене.

## Conditional-load past.yaml

`past.yaml` грузить только если сцена касается:

- прошлого;
- памяти;
- Самуэля;
- лаборатории;
- Райдена/Акиры;
- ребёнка/потери;
- кольца;
- шрама;
- Эхо;
- кайросов;
- пространства между;
- срыва;
- самоблокировки;
- скрытого происхождения;
- старых связей, которые нельзя раскрывать автоматически.

## Временные знания и планы

Временные знания персонажа, текущая тактика, предположения и план конкретного дня не должны жить в постоянной карточке персонажа.

Использовать:

- `calendar/days/<date>.yaml` — давление дня, временные намерения, day beat;
- `state/character_knowledge/<id>.json` — что персонаж узнал в сыгранных сценах;
- `state/current_state.json` — кто активен, где находится, что видно сейчас;
- `state/relationships.json` — изменившаяся динамика отношений;
- `state/scene_continuity_state.json` — физическая непрерывность, травмы, предметы, положения.

Пример: если Ирэй на 31 августа считает Восточный сектор риском, это временная установка дня. Если сцена показывает, что он остаётся в Восточном секторе или меняет мнение, обновлять state, а не переписывать его постоянную карточку.

## Без scattered lock-файлов

Старые lock-и и scattered rules не переносить отдельными файлами. Их смысл должен быть собран в обычные поля YAML:

- `knows`
- `does_not_know`
- `forbidden`
- `allowed`
- `pov_rules`
- `rules`
- `load_only_if`
- `temporary_knowledge_and_intent`

## Приоритет текущего канона

Если старые репозитории, legacy-файлы или старые карточки противоречат текущим правкам пользователя, использовать текущие правки:

- Акира сама заблокировала эмоции, поток и память.
- Перегруз и потеря контроля не одно и то же.
- 1198 — Академия, 1206 — спустя восемь лет.
- Акира чистокровный кайрос, но выросла среди людей и не помнит этого на старте.
- Райден после стирания не помнит Акиру/Картер и всё, что с ней связано.
- Карточка персонажа описывает персонажа, а не календарный маршрут и не временный план дня.
