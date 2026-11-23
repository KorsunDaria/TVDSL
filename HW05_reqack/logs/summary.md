# Сводка запусков

Платформа: darwin; версии: This is NuSMV 2.7.1 (compiled on (unknown)); Spin Version 6.5.2 -- 6 December 2019

## Python

| Инструмент | Свойства | Результат | Время, с | Пик RSS, МБ |
|---|---|---|---|---|
| Python (явный перебор) | все | см. лог | 0.02 | 10 |

## NuSMV

| Режим | Вердикты | Время, с | Пик RSS, МБ |
|---|---|---|---|
| NuSMV, BDD | EF (client = done_ok & timer = 3) → false; AG (client = waiting -> AF (client = done_ok | client = done_to)) → true; EF client = done_ok → true; G (chan_ack -> timer = 1) → true; G (chan_ack -> client = waiting) → true; G (client = done_to -> timer = 3) → true; G (client = waiting ->  F (client = done_ok | client = done_to)) → true; G (client = waiting ->  F client = done_ok) → false | 0.02 | 15 |

## Spin (каждое свойство — отдельный запуск)

| Режим | Свойство | Вердикт | states, stored | Время, с | Пик RSS, МБ |
|---|---|---|---|---|---|
| Spin, por | s1 | true | 9 | 0.27 | 130 |
| Spin, por | s2 | true | 9 | 0.02 | 130 |
| Spin, por | s3 | true | 9 | 0.01 | 130 |
| Spin, por | l1 | true | 15 | 0.01 | 130 |
| Spin, por | l2 | false | 8 | 0.01 | 130 |
| Spin, nored | s1 | true | 9 | 0.29 | 130 |
| Spin, nored | s2 | true | 9 | 0.01 | 130 |
| Spin, nored | s3 | true | 9 | 0.02 | 130 |
| Spin, nored | l1 | true | 15 | 0.01 | 130 |
| Spin, nored | l2 | false | 8 | 0.01 | 130 |
| Spin, bitstate | s1 | true | 9 | 0.24 | 18 |
| Spin, bitstate | s2 | true | 9 | 0.01 | 18 |
| Spin, bitstate | s3 | true | 9 | 0.01 | 18 |
| Spin, bitstate | l1 | true | 15 | 0.01 | 18 |
| Spin, bitstate | l2 | false | 9 | 0.01 | 18 |
