# State Memory Architecture 1206 RU

Этот ZIP — не промт и не лор. Это основа для следующего этапа: state, отношения и maintenance.

## Файлы

- `state/maintenance_rules_1206.json` — правила периодического recovery-аудита и мягкой чистки/summarization state.
- `state/character_knowledge/_template.json` — шаблон личного dynamic state персонажа.
- `state/relationship_pairs/_pair_template.json` — шаблон отношений пары персонажей.

## Главная идея

- Обычная запись state происходит каждый ход.
- Каждые 10 ходов recovery-аудит проверяет, не пропущено ли важное.
- Каждые 15 ходов со сдвигом cleanup мягко сжимает state.
- Recovery и cleanup не запускаются в один ход.
- Чистка удаляет шум, но не память.
- Если персонажа что-то зацепило по характеру, страху, долгу, вине, привязанности или подозрению — это нужно сохранить хотя бы коротким protected_moment.

## Что делать дальше

Следующий логичный шаг — создать первые реальные файлы `state/relationship_pairs/` для пар:

- `akira__raiden.json`
- `akira__ray.json`
- `akira__miki.json`
- `akira__yuna.json`
- `raiden__haru.json`

Промт лучше править позже, после state и сборщика.
