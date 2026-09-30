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

Плюс граничные случаи на стыке букв и цифр.
"""

from lexer import build_pipeline, tokenize

TRANS, ACCEPT, START, ALPHABET, STATS = build_pipeline()

CASES = [
    ('empty', '', []),
    ('whitespace_only', '   \t\r\n  ', []),
    ('int_zero', '0', ['INT']),
    ('int_leading_zero_00', '00', ['INT', 'INT']),
    ('int_leading_zero_01', '01', ['INT', 'INT']),
    ('ident_with_underscore', 'foo_bar', ['IDENT', 'ERROR', 'IDENT']),
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
    ('non_ascii_trap', 'café', ['IDENT', 'ERROR']),

    ('int_multi_digit', '123', ['INT']),
    ('int_ends_with_zero', '10', ['INT']),
    ('int_leading_zeros_007', '007', ['INT', 'INT', 'INT']),
    ('two_ints_by_space', '1 2', ['INT', 'INT']),
    ('ident_ends_with_digit', 'x1', ['IDENT']),
    ('ident_letters_digits_mixed', 'x1y2', ['IDENT']),
    ('int_then_ident', '1x', ['INT', 'IDENT']),
    ('int_then_ident_long', '12ab', ['INT', 'IDENT']),
    ('zero_then_ident', '0x1', ['INT', 'IDENT']),
    ('expr_x_plus_1x', 'x+1x', ['IDENT', 'PLUS', 'INT', 'IDENT']),
    ('expr_idents_with_digits', 'a1+b2', ['IDENT', 'PLUS', 'IDENT']),
    ('expr_arithmetic', '1+2*3', ['INT', 'PLUS', 'INT', 'STAR', 'INT']),
    ('minus_then_int', '-1', ['MINUS', 'INT']),
    ('compare_int_ident', '3<=x', ['INT', 'LE', 'IDENT']),
    ('assign_ident_with_digit', 'x1=2;', ['IDENT', 'ASSIGN', 'INT', 'SEMI']),
    ('dot_not_in_alphabet', '1.5', ['INT', 'ERROR', 'INT']),

    ('kw_prefix_digit_is_ident', 'if1', ['IDENT']),
    ('kw_prefix_letter_is_ident', 'iff', ['IDENT']),
    ('kw_while_digit_is_ident', 'while0', ['IDENT']),
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