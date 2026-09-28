"""
Тесты для lexer.py.

Запуск:
    python3 test_lexer.py           

Каждый сценарий по заданию HW1:
пустая строка; только WS (пробелы/табы/CRLF); 
0/00/01; идентификатор с '_';
все ключевые слова; 
операторы/разделители; 
комментарий до конца строки;
символ вне ASCII -> ловушка.
"""

from lexer import build_pipeline, tokenize

TRANS, ACCEPT, START, ALPHABET, STATS = build_pipeline()

CASES = [
    ('empty', '', []),
    ('whitespace_only', '   \t\r\n  ', []),
    ('int_zero', '0', ['INT']),
    ('int_leading_zero_00', '00', ['INT', 'INT']),      # '00' не склеивается в один INT
    ('int_leading_zero_01', '01', ['INT', 'INT']),
    ('ident_with_underscore', 'foo_bar', ['IDENT', 'ERROR', 'IDENT']),  # '_' вне алфавита IDENT
    ('kw_function', 'function', ['KW_FUNCTION']),
    ('kw_returns', 'returns', ['KW_RETURNS']),
    ('kw_while', 'while', ['KW_WHILE']),
    ('kw_if', 'if', ['KW_IF']),
    ('kw_else', 'else', ['KW_ELSE']),
    ('kw_assert', 'assert', ['KW_ASSERT']),
    ('kw_assume', 'assume', ['KW_ASSUME']),
    ('kw_invariant', 'invariant', ['KW_INVARIANT']),
    ('kw_length', 'length', ['KW_LENGTH']),
    ('delimiters', '()[]{},;',
     ['LPAREN', 'RPAREN', 'LBRACKET', 'RBRACKET', 'LBRACE', 'RBRACE', 'COMMA', 'SEMI']),
    ('operators', '+ - * / == != <= >= < > =',
     ['PLUS', 'MINUS', 'STAR', 'SLASH', 'EQ', 'NEQ', 'LE', 'GE', 'LT', 'GT', 'ASSIGN']),
    ('comment_to_eol', '// comment to eol', []),
    ('mixed_line', 'a = 1; // trailing comment\nb = 2;',
     ['IDENT', 'ASSIGN', 'INT', 'SEMI', 'IDENT', 'ASSIGN', 'INT', 'SEMI']),
    ('non_ascii_trap', 'café', ['IDENT', 'ERROR']),    # 'é' вне ASCII -> ловушка
]


def _check(text, expected):
    got = [t.type for t in tokenize(text, TRANS, ACCEPT, START)]
    assert got == expected, f'{text!r}: получено {got}, ожидалось {expected}'


def _make_test(name, text, expected):
    def _t():
        _check(text, expected)
    _t.__name__ = f'test_{name}'
    return _t


for _name, _text, _expected in CASES:
    globals()[f'test_{_name}'] = _make_test(_name, _text, _expected)


def run_all():
    passed = 0
    for name, text, expected in CASES:
        try:
            _check(text, expected)
            print(f'[OK  ] {name}')
            passed += 1
        except AssertionError as e:
            print(f'[FAIL] {name}: {e}')
    print(f'\n{passed}/{len(CASES)} тестов пройдено')
    return passed == len(CASES)


if __name__ == '__main__':
    import sys
    sys.exit(0 if run_all() else 1)