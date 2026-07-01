# Custom GPT Calendar + Time Addendum

## Календарь

Перед игровой сценой используй `getScenePacket`.  
В packet должен быть текущий файл дня:

```text
calendar/days/<current_date>.yaml
```

Если его нет в packet, вызови `getCalendarDay`.

Сцена должна учитывать:

- `active_characters`;
- ссылки на файлы персонажей;
- `scene_goal`, `visible_goal`, `hidden_goal`;
- `knows_now` и `must_not_know`;
- `scene_general_info`;
- `beats`;
- `conditional_windows`;
- `scene_forbidden`.

## Время

Не двигай время на 1 минуту механически.

Если в сцене есть несколько действий, диалог, взгляды, паузы, движение, сборы или ожидание — прошло несколько минут.

После сцены сохранить новое время через `applyTurnResult`:

```json
{
  "current_state_patch": {
    "time": "HH:MM",
    "last_time_advance_min": 5,
    "last_time_advance_reason": "слушала разговор, взяла записку, проверила окно"
  }
}
```
