#!/usr/bin/env python3
"""Тесты funny_checker.py. Запуск: python3 test_checker.py"""

import os

from funny_checker import check, dump

HERE = os.path.dirname(os.path.abspath(__file__))
H = 'f(a: int, b: int[]) returns r: int\n'
G = 'g(x: int) returns y: int y = x;\n'
D = 'd() returns p: int, q: int { p = 1; q = 2; }\n'


def errs(src):
    return [(d.line, d.col, d.message) for d in check(src)[1]]


def expect(src, fragment):
    off = src.index('§')
    src = src.replace('§', '')
    line, col = src.count('\n', 0, off) + 1, off - (src.rfind('\n', 0, off) + 1) + 1
    got = errs(src)
    assert len(got) == 1 and got[0][:2] == (line, col) and fragment in got[0][2], (src, got)


NEGATIVE = [
    (H + 'r = §x;', 'не объявлена'),                                        # неизвестное имя
    ('f(a: int, §a: int)\nreturns r: int\nr = 1;', 'повторное объявление'),  # повтор переменной
    ('f() returns r: int r = 1;\n§f() returns r: int r = 2;', 'повторное определение'),
    (H + '§r = b;', 'нельзя присвоить'),                                    # int[] -> int
    (H + 'if (§b == 1) r = 1;', 'ожидается int'),                           # операнд сравнения
    (H + 'r = §a[0];', 'не массив'),
    (G + 'f() returns r: int r = §g(1, 2);', 'ожидает 1'),                  # число аргументов
    (G + 'f(b: int[]) returns r: int r = g(§b);', 'аргумент 1'),            # тип аргумента
    (D + 'f() returns r: int r = §d();', 'возвращает 2'),
    (D + 'f() returns r: int uses s: int, t: int, u: int §s, t, u = d();', 'слева 3'),
    ('f() returns r: int r = §h(1);', 'неизвестная функция'),
    ('f() returns r: int r = §g();\ng() returns y: int y = 1;', 'неизвестная функция'),  # до определения
    ('p(x: int) => x > 0\nf() returns r: int r = §p(1);', 'формула'),
    ('f() returns r: int ensures r > §q uses q: int r = 1;', 'не объявлена'),  # ensures не видит locals
    ('f(a: int[]) returns r: int { x = 1; §x = a; r = x; }', 'нельзя присвоить'),  # вывод: противоречие
    ('f() returns r: int { r = §x; x = 1; }', 'не объявлена'),                # чтение до присваивания
]


def test_negative():
    for src, fragment in NEGATIVE:
        expect(src, fragment)


def test_multiple_errors():
    got = errs(H + '{ r = x;\nr = y; }')
    assert [g[:2] for g in got] == [(2, 7), (3, 5)], got


def test_syntax_error_reported():
    assert errs('f() returns r: int r = ;')[0][:2] == (1, 24)


def test_sample_positive():
    with open(os.path.join(HERE, 'sample_hw2.funny'), encoding='utf-8') as f:
        ast, errors = check(f.read())
    assert not errors, [str(e) for e in errors]
    d = dump(ast)
    gcd = d['decls'][0]
    assert gcd['ensures']['etype'] == 'bool'
    assert gcd['body']['stmts'][0]['value']['etype'] == 'int'
    assert d['decls'][2]['body']['stmts'][0]['value']['etype'] == 'int[]'


def test_inference():
    ast, errors = check('f(a: int[]) returns r: int { x = 1; b = a; r = x + b[0]; }')
    assert not errors
    assert [(v.name, v.type) for v in ast.decls[0].locals] == [('x', 'int'), ('b', 'int[]')]


def test_tuple_inference():
    ast, errors = check(D + 'f() returns r: int { s, t = d(); r = s + t; }')
    assert not errors and [v.type for v in ast.decls[1].locals] == ['int', 'int']


if __name__ == '__main__':
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith('test_')]
    for name, fn in tests:
        fn()
        print('ok', name)
    print(f'все {len(tests)} тестов пройдены')
