# Funny: лексер и парсер

HW1 — лексер (regex → НКА → ДКА → минимизация). HW2 — LL(1)-парсер (рекурсивный спуск) на его основе.

## Файлы

- `lexer.py`, `test_lexer.py`, `dfa_table.json` — лексер HW1 (расширен новыми токенами для HW2)
- `funny_parser.py` — парсер, AST, ошибки
- `test_parser.py` — тесты парсера
- `grammar.txt` — грамматика
- `sample_hw2.funny`, `sample_hw2.ast.json` — пример и эталонный AST

## Запуск

```
python3 funny_parser.py sample_hw2.funny          # AST в JSON
python3 funny_parser.py sample_hw2.funny --pos    # с позициями строка:столбец
python3 test_parser.py                            # тесты
```

Есть ошибки — они печатаются в stderr в виде `файл:строка:столбец: ошибка: ...`, код возврата 1.
Пример:

```
bad.funny:3:7: ошибка: ожидается выражение, получено ';'
bad.funny:4:9: ошибка: ожидается ';', получено '2'
```

## Ошибки

Режим паники: в блоке пропуск до `;` или `}`, на верхнем уровне — до следующего определения.
Парсер собирает все ошибки за один проход и не зацикливается.

## AST

Узел — `{"node": "Имя", ...поля}`, отсутствующие `requires`/`ensures`/`invariant`/`else` — `null`.

| Узел         | Поля                                               |
| ---------------- | ------------------------------------------------------ |
| Module           | decls                                                  |
| Function         | name, params, requires, returns, ensures, locals, body |
| Formula          | name, params, body                                     |
| VarDef           | name, type (`int` / `int[]`)                       |
| Block            | stmts                                                  |
| Assign           | target, value                                          |
| TupleAssign      | targets, call                                          |
| If               | cond, then, orelse                                     |
| While            | cond, invariant, body                                  |
| Assert, Assume   | cond                                                   |
| Num, Var         | value / name                                           |
| Neg              | operand                                                |
| BinOp            | op, left, right                                        |
| Call             | name, args (`length` тоже Call)                  |
| Index            | array, index                                           |
| ArrayUpdate      | array, index, value                                    |
| BoolLit          | value                                                  |
| Compare          | op, left, right                                        |
| Not              | operand                                                |
| And, Or, Implies | left, right                                            |
| Quantifier       | kind, var, body                                        |
| FormulaRef       | name, args                                             |

`a[i] = e;` разбирается как `Assign(a, ArrayUpdate(a, i, e))`.

## Отклонения от funny.ru.md

- импликация `->` вместо `→`
- `assert`/`assume` добавлены по заданию: `assert predicate;`
- пустой ввод — ошибка
- проверяется только синтаксис, семантика не проверяется
