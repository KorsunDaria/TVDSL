"""
Тесты для funny_parser.py.

Запуск:
    python3 test_parser.py

"""

import json
import os
import signal

from funny_parser import Parser, parse, to_dict

HERE = os.path.dirname(os.path.abspath(__file__))
HEAD = 'f() returns r: int '


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------

def sx(n):
    """Компактная запись AST (из to_dict без позиций)."""
    if isinstance(n, dict):
        parts = [sx(v) for k, v in n.items() if k != 'node']
        return '(' + ' '.join([n['node']] + parts) + ')'
    if isinstance(n, list):
        return '[' + ' '.join(sx(x) for x in n) + ']'
    if n is None:
        return '_'
    if isinstance(n, bool):
        return 'true' if n else 'false'
    return str(n)


def ok_parse(src):
    res = parse(src)
    assert not res.errors, f'неожиданные ошибки: {[str(e) for e in res.errors]}'
    return res.ast


def body_of(src):
    return sx(to_dict(ok_parse(HEAD + src).decls[0].body))


def cond_of(src):
    return sx(to_dict(ok_parse(HEAD + f'if ({src}) r = 1;').decls[0].body.cond))


def pred_of(src):
    return sx(to_dict(ok_parse(HEAD + f'ensures {src} r = 1;').decls[0].ensures))


def expr_of(src):
    return sx(to_dict(ok_parse(HEAD + f'r = {src};').decls[0].body.value))


def line_col(src, offset):
    line = src.count('\n', 0, offset) + 1
    col = offset - (src.rfind('\n', 0, offset) + 1) + 1
    return line, col


# ---------------------------------------------------------------------------
# Позитивные тесты
# ---------------------------------------------------------------------------

A0 = '(Compare > (Var a) (Num 0))'
B0 = '(Compare > (Var b) (Num 0))'
C0 = '(Compare > (Var c) (Num 0))'

EXPR_CASES = [
    ('num', '42', '(Num 42)'),
    ('zero', '0', '(Num 0)'),
    ('var', 'x', '(Var x)'),
    ('prec_mul_over_add', '1 + 2 * 3', '(BinOp + (Num 1) (BinOp * (Num 2) (Num 3)))'),
    ('prec_div_over_sub', '1 - 4 / 2', '(BinOp - (Num 1) (BinOp / (Num 4) (Num 2)))'),
    ('left_assoc_sub', '1 - 2 - 3', '(BinOp - (BinOp - (Num 1) (Num 2)) (Num 3))'),
    ('left_assoc_div', '8 / 4 / 2', '(BinOp / (BinOp / (Num 8) (Num 4)) (Num 2))'),
    ('left_assoc_mixed', '1 * 2 + 3 * 4',
     '(BinOp + (BinOp * (Num 1) (Num 2)) (BinOp * (Num 3) (Num 4)))'),
    ('parens', '(1 + 2) * 3', '(BinOp * (BinOp + (Num 1) (Num 2)) (Num 3))'),
    ('unary_minus', '-x', '(Neg (Var x))'),
    ('unary_binds_tighter', '-a * b', '(BinOp * (Neg (Var a)) (Var b))'),
    ('unary_double', '- - 1', '(Neg (Neg (Num 1)))'),
    ('unary_paren', '-(a + b)', '(Neg (BinOp + (Var a) (Var b)))'),
    ('binary_minus_unary', 'a - -b', '(BinOp - (Var a) (Neg (Var b)))'),
    ('call_no_args', 'g()', '(Call g [])'),
    ('call_args', 'g(1, x + 2)', '(Call g [(Num 1) (BinOp + (Var x) (Num 2))])'),
    ('call_nested', 'g(h(1))', '(Call g [(Call h [(Num 1)])])'),
    ('length', 'length(a)', '(Call length [(Var a)])'),
    ('index', 'a[i]', '(Index a (Var i))'),
    ('index_expr', 'a[i + 1]', '(Index a (BinOp + (Var i) (Num 1)))'),
    ('index_of_index', 'a[b[0]]', '(Index a (Index b (Num 0)))'),
    ('call_in_index', 'a[length(a) - 1]',
     '(Index a (BinOp - (Call length [(Var a)]) (Num 1)))'),
]

STMT_CASES = [
    ('assign', 'x = 1;', '(Assign x (Num 1))'),
    ('assign_call', 'x = g(1);', '(Assign x (Call g [(Num 1)]))'),
    ('array_update_sugar', 'a[i + 1] = x;',
     '(Assign a (ArrayUpdate a (BinOp + (Var i) (Num 1)) (Var x)))'),
    ('tuple_assign', 'q, r = divide(a, 2);',
     '(TupleAssign [q r] (Call divide [(Var a) (Num 2)]))'),
    ('tuple_assign_three', 'a, b, c = g();', '(TupleAssign [a b c] (Call g []))'),
    ('assert', 'assert x > 0;', '(Assert (Compare > (Var x) (Num 0)))'),
    ('assume', 'assume x > 0 and y > 0;',
     '(Assume (And (Compare > (Var x) (Num 0)) (Compare > (Var y) (Num 0))))'),
    ('assert_parens', 'assert (x > 0);', '(Assert (Compare > (Var x) (Num 0)))'),
    ('assert_quantifier', 'assert forall (i: int | a[i] > 0);',
     '(Assert (Quantifier forall (VarDef i int) (Compare > (Index a (Var i)) (Num 0))))'),
    ('assert_formula', 'assume P(x);', '(Assume (FormulaRef P [(Var x)]))'),
    ('assert_in_block', '{ assume x > 0; x = 1; assert x > 0; }',
     '(Block [(Assume (Compare > (Var x) (Num 0))) (Assign x (Num 1)) '
     '(Assert (Compare > (Var x) (Num 0)))])'),
    ('empty_block', '{}', '(Block [])'),
    ('block', '{ x = 1; y = 2; }', '(Block [(Assign x (Num 1)) (Assign y (Num 2))])'),
    ('nested_block', '{ { x = 1; } }', '(Block [(Block [(Assign x (Num 1))])])'),
    ('if_no_else', 'if (x > 0) y = 1;',
     '(If (Compare > (Var x) (Num 0)) (Assign y (Num 1)) _)'),
    ('if_else', 'if (x > 0) y = 1; else y = 2;',
     '(If (Compare > (Var x) (Num 0)) (Assign y (Num 1)) (Assign y (Num 2)))'),
    ('dangling_else', 'if (x > 0) if (y < 0) z = 1; else z = 5;',
     '(If (Compare > (Var x) (Num 0)) '
     '(If (Compare < (Var y) (Num 0)) (Assign z (Num 1)) (Assign z (Num 5))) _)'),
    ('else_if_chain', 'if (a > 0) x = 1; else if (b > 0) x = 2; else x = 3;',
     f'(If {A0} (Assign x (Num 1)) (If {B0} (Assign x (Num 2)) (Assign x (Num 3))))'),
    ('while_no_invariant', 'while (true) x = 1;', '(While (BoolLit true) _ (Assign x (Num 1)))'),
    ('while_invariant_block', 'while (i < 10) invariant i >= 0 { i = i + 1; }',
     '(While (Compare < (Var i) (Num 10)) (Compare >= (Var i) (Num 0)) '
     '(Block [(Assign i (BinOp + (Var i) (Num 1)))]))'),
    ('while_invariant_then_assign', 'while (i < 10) invariant i >= 0 i = i + 1;',
     '(While (Compare < (Var i) (Num 10)) (Compare >= (Var i) (Num 0)) '
     '(Assign i (BinOp + (Var i) (Num 1))))'),
    ('while_invariant_formula', 'while (i < 10) invariant P(i) i = i + 1;',
     '(While (Compare < (Var i) (Num 10)) (FormulaRef P [(Var i)]) '
     '(Assign i (BinOp + (Var i) (Num 1))))'),
    ('assign_array_elem_after_invariant', 'while (i < 2) invariant i >= 0 a[i] = 1;',
     '(While (Compare < (Var i) (Num 2)) (Compare >= (Var i) (Num 0)) '
     '(Assign a (ArrayUpdate a (Var i) (Num 1))))'),
]

COND_CASES = [
    ('true', 'true', '(BoolLit true)'),
    ('false', 'false', '(BoolLit false)'),
    ('cmp_eq', 'a == b', '(Compare == (Var a) (Var b))'),
    ('cmp_neq', 'a != b', '(Compare != (Var a) (Var b))'),
    ('cmp_le', 'a <= b', '(Compare <= (Var a) (Var b))'),
    ('cmp_ge', 'a >= b', '(Compare >= (Var a) (Var b))'),
    ('cmp_lt', 'a < b', '(Compare < (Var a) (Var b))'),
    ('cmp_gt', 'a > b', '(Compare > (Var a) (Var b))'),
    ('cmp_arith', 'a + 1 > b * 2',
     '(Compare > (BinOp + (Var a) (Num 1)) (BinOp * (Var b) (Num 2)))'),
    ('cmp_call_index', 'g(x) + 1 > a[i]',
     '(Compare > (BinOp + (Call g [(Var x)]) (Num 1)) (Index a (Var i)))'),
    ('prec_and_over_or', 'a > 0 or b > 0 and c > 0', f'(Or {A0} (And {B0} {C0}))'),
    ('left_assoc_or', 'a > 0 or b > 0 or c > 0', f'(Or (Or {A0} {B0}) {C0})'),
    ('left_assoc_and', 'a > 0 and b > 0 and c > 0', f'(And (And {A0} {B0}) {C0})'),
    ('not_binds_tighter', 'not a > 0 and b > 0', f'(And (Not {A0}) {B0})'),
    ('not_not', 'not not a > 0', f'(Not (Not {A0}))'),
    ('not_paren', 'not (a > 0 or b > 0)', f'(Not (Or {A0} {B0}))'),
    ('implies', 'a > 0 -> b > 0', f'(Implies {A0} {B0})'),
    ('implies_right_assoc', 'a > 0 -> b > 0 -> c > 0', f'(Implies {A0} (Implies {B0} {C0}))'),
    ('implies_lowest', 'a > 0 or b > 0 -> c > 0', f'(Implies (Or {A0} {B0}) {C0})'),
    ('paren_bool', '(a > 0 or b > 0) and c > 0', f'(And (Or {A0} {B0}) {C0})'),
    ('paren_bool_redundant', '((a > 0))', A0),
    ('paren_arith_lhs', '(x + 1) * 2 > 3',
     '(Compare > (BinOp * (BinOp + (Var x) (Num 1)) (Num 2)) (Num 3))'),
    ('paren_var_lhs', '(x) > 1', '(Compare > (Var x) (Num 1))'),
    ('paren_var_double', '((x)) > 1', '(Compare > (Var x) (Num 1))'),
    ('paren_var_plus', '(x) + 1 > 2', '(Compare > (BinOp + (Var x) (Num 1)) (Num 2))'),
    ('paren_arith_both', '(x + 1) > (y - 1)',
     '(Compare > (BinOp + (Var x) (Num 1)) (BinOp - (Var y) (Num 1)))'),
    ('paren_bool_with_paren_arith', '((x + 1) > 2) and y > 0',
     '(And (Compare > (BinOp + (Var x) (Num 1)) (Num 2)) (Compare > (Var y) (Num 0)))'),
    ('bool_consts_logic', 'true and not false', '(And (BoolLit true) (Not (BoolLit false)))'),
    ('length_cmp', 'length(a) != 0', '(Compare != (Call length [(Var a)]) (Num 0))'),
]

PRED_CASES = [
    ('forall', 'forall (i: int | i >= 0 -> a[i] > 0)',
     '(Quantifier forall (VarDef i int) (Implies (Compare >= (Var i) (Num 0)) '
     '(Compare > (Index a (Var i)) (Num 0))))'),
    ('exists_array_var', 'exists (k: int[] | true)',
     '(Quantifier exists (VarDef k int[]) (BoolLit true))'),
    ('quantifier_nested', 'forall (i: int | exists (j: int | j > i))',
     '(Quantifier forall (VarDef i int) (Quantifier exists (VarDef j int) '
     '(Compare > (Var j) (Var i))))'),
    ('formula_ref', 'sorted(a, 3)', '(FormulaRef sorted [(Var a) (Num 3)])'),
    ('formula_ref_no_args', 'ok()', '(FormulaRef ok [])'),
    ('formula_ref_paren', '(sorted(a))', '(FormulaRef sorted [(Var a)])'),
    ('formula_ref_not', 'not sorted(a)', '(Not (FormulaRef sorted [(Var a)]))'),
    ('formula_ref_and', 'sorted(a) and b > 0', f'(And (FormulaRef sorted [(Var a)]) {B0})'),
    ('call_in_comparison', 'g(x) > 0', '(Compare > (Call g [(Var x)]) (Num 0))'),
    ('call_in_arith_comparison', 'g(x) + 1 == 2',
     '(Compare == (BinOp + (Call g [(Var x)]) (Num 1)) (Num 2))'),
    ('call_then_formula', 'g(x) < 2 and P(x)',
     '(And (Compare < (Call g [(Var x)]) (Num 2)) (FormulaRef P [(Var x)]))'),
    ('predicate_implies', 'a > 0 -> b > 0', f'(Implies {A0} {B0})'),
    ('predicate_plain', 'a > 0 and b > 0', f'(And {A0} {B0})'),
]


def _make(kind, name, src, expected):
    fn = {'expr': expr_of, 'stmt': body_of, 'cond': cond_of, 'pred': pred_of}[kind]

    def _t():
        got = fn(src)
        assert got == expected, f'{src!r}:\n  получено  {got}\n  ожидалось {expected}'
    _t.__name__ = f'test_{kind}_{name}'
    return _t


POSITIVE = []
for _kind, _cases in (('expr', EXPR_CASES), ('stmt', STMT_CASES),
                      ('cond', COND_CASES), ('pred', PRED_CASES)):
    for _name, _src, _exp in _cases:
        _f = _make(_kind, _name, _src, _exp)
        globals()[_f.__name__] = _f
        POSITIVE.append(_f)


def test_function_header():
    src = ('f(a: int, b: int[]) requires a > 0 returns q: int, r: int[] '
           'ensures q == a uses i: int, j: int x = 1;')
    got = sx(to_dict(ok_parse(src)))
    exp = ('(Module [(Function f [(VarDef a int) (VarDef b int[])] '
           '(Compare > (Var a) (Num 0)) [(VarDef q int) (VarDef r int[])] '
           '(Compare == (Var q) (Var a)) [(VarDef i int) (VarDef j int)] '
           '(Assign x (Num 1)))])')
    assert got == exp, got


def test_function_minimal_optional_parts_absent():
    got = sx(to_dict(ok_parse('f() returns r: int r = 1;')))
    assert got == '(Module [(Function f [] _ [(VarDef r int)] _ [] (Assign r (Num 1)))])', got


def test_function_requires_without_ensures():
    got = sx(to_dict(ok_parse('f(x: int) requires x > 0 returns r: int r = x;')))
    exp = ('(Module [(Function f [(VarDef x int)] (Compare > (Var x) (Num 0)) '
           '[(VarDef r int)] _ [] (Assign r (Var x)))])')
    assert got == exp, got


def test_formula_definition():
    got = sx(to_dict(ok_parse('sorted(a: int[]) => forall (i: int | a[i] <= a[i + 1])')))
    exp = ('(Module [(Formula sorted [(VarDef a int[])] (Quantifier forall (VarDef i int) '
           '(Compare <= (Index a (Var i)) (Index a (BinOp + (Var i) (Num 1))))))])')
    assert got == exp, got


def test_formula_then_function():
    got = ok_parse('P(x: int) => x > 0\nf() returns r: int r = 1;')
    assert [type(d).__name__ for d in got.decls] == ['Formula', 'Function']


def test_several_functions_and_comments():
    src = ('// first\nf() returns r: int r = 1; // tail\n'
           'g(x: int) returns r: int {\n  r = x; // set\n}\n')
    assert [d.name for d in ok_parse(src).decls] == ['f', 'g']


def test_crlf_and_tabs():
    src = 'f()\r\n\treturns r: int\r\n{\r\n\tr = 1;\r\n}\r\n'
    assert sx(to_dict(ok_parse(src).decls[0].body)) == '(Block [(Assign r (Num 1))])'


def test_whitespace_agnostic():
    a = to_dict(ok_parse('f() returns r: int if (r > 0) r = 1; else r = 2;'))
    b = to_dict(ok_parse('f()returns r:int if(r>0)r=1;else r=2;'))
    assert a == b


def test_snapshot_sample():
    with open(os.path.join(HERE, 'sample_hw2.funny'), encoding='utf-8') as f:
        src = f.read()
    with open(os.path.join(HERE, 'sample_hw2.ast.json'), encoding='utf-8') as f:
        expected = json.load(f)
    assert to_dict(ok_parse(src)) == expected


def test_json_serializable_with_positions():
    src = 'f() returns r: int\n{\n  r = 1 + 2;\n}'
    d = json.loads(json.dumps(to_dict(ok_parse(src), with_pos=True)))
    func = d['decls'][0]
    assert func['pos'] == '1:1'
    block = func['body']
    assert block['node'] == 'Block' and block['pos'] == '2:1'
    assign = block['stmts'][0]
    assert assign['pos'] == '3:3'
    binop = assign['value']
    assert binop['pos'] == '3:9'          # позиция оператора
    assert binop['left']['pos'] == '3:7' and binop['right']['pos'] == '3:11'


# ---------------------------------------------------------------------------
# Негативные тесты: (имя, исходник, [(иголка, фрагмент сообщения), ...])
# иголка None — конец ввода; (текст, k) — k-й символ первого вхождения текста;
# позиция = первое вхождение иголки в исходнике.
# ---------------------------------------------------------------------------

NEGATIVE = [
    ('empty_input', '', [(None, 'ожидается определение функции или формулы')]),
    ('whitespace_comment_only', '  // c\n', [(None, 'ожидается определение функции или формулы')]),
    ('unclosed_paren_expr', HEAD + 'r = (1 + 2;', [(';', "ожидается ')'")]),
    ('unclosed_paren_if', HEAD + 'if (x > 0 r = 1;', [('r = 1', "ожидается ')'")]),
    ('unclosed_paren_call', HEAD + 'r = g(1, 2;', [(';', "ожидается ')'")]),
    ('unclosed_bracket', HEAD + 'r = a[1;', [(';', "ожидается ']'")]),
    ('unclosed_params', 'f(a: int[) returns r: int r = 1;', [(')', "ожидается ']'")]),
    ('unclosed_brace', 'f() returns r: int {\n  r = 1;\n', [(None, "не закрыта '{', открытая в 1:20")]),
    ('unclosed_braces_nested', 'f() returns r: int {\n { r = 1;\n',
     [(None, "не закрыта '{', открытая в 2:2"), (None, "не закрыта '{', открытая в 1:20")]),
    ('extra_closing_brace', HEAD + 'r = 1;\n}', [('}', "ожидается имя функции или формулы")]),
    ('extra_closing_paren', HEAD + 'r = (1));', [(');', "ожидается ';'")]),
    ('extra_token_in_stmt', HEAD + 'r = 1 2;', [('2;', "ожидается ';'")]),
    ('missing_semicolon', 'f() returns r: int {\n  r = 1\n}', [('}', "ожидается ';'")]),
    ('wrong_op_assign_in_cond', HEAD + 'if (x = 1) r = 1;', [('= 1)', 'ожидается оператор сравнения')]),
    ('wrong_op_double_eq_stmt', HEAD + 'x == 1;', [('== 1', "ожидается '='")]),
    ('wrong_op_binary_star', HEAD + 'r = 1 +* 2;', [('* 2', 'ожидается выражение')]),
    ('missing_operand', HEAD + 'r = 1 +;', [(';', 'ожидается выражение')]),
    ('cond_without_comparison', HEAD + 'if (x) r = 1;', [(('x) r', 1), 'ожидается оператор сравнения')]),
    ('and_without_comparison', HEAD + 'if (x and y > 0) r = 1;', [('and', 'ожидается оператор сравнения')]),
    ('arrow_alone', HEAD + 'if (x > 0 ->) r = 1;', [(('->)', 2), 'ожидается выражение')]),
    ('quantifier_in_condition', HEAD + 'if (forall (i: int | true)) r = 1;', [('forall', 'квантор допустим только в предикате')]),
    ('invalid_char', HEAD + 'r = a_b;', [('_b', 'недопустимый символ')]),
    ('invalid_char_non_ascii', HEAD + 'r = é;', [('é', 'недопустимый символ')]),
    ('missing_returns', 'f(x: int) x = 1;', [('x = 1', "'requires', 'returns' или '=>'")]),
    ('param_without_type', 'f(x) returns r: int r = 1;', [(')', "ожидается ':'")]),
    ('unknown_type', 'f(x: float) returns r: int r = 1;', [('float', "тип 'int' или 'int[]'")]),
    ('old_function_keyword', 'function f() returns r: int r = 1;', [('function', 'ожидается имя функции или формулы')]),
    ('unterminated_header', 'f(', [(None, 'ожидается имя переменной')]),
    ('missing_body', 'f() returns r: int', [(None, 'ожидается оператор')]),
    ('tuple_without_call', 'f() returns r: int { a, b = 1; }', [('1;', 'ожидается вызов функции')]),
    ('assert_without_comparison', 'f() returns r: int { assert x; }', [('; }', 'ожидается оператор сравнения')]),
    ('assert_missing_semicolon', 'f() returns r: int { assert x > 0 }', [('}', "ожидается ';'")]),
    ('assert_empty', 'f() returns r: int { assume ; }', [(';', 'ожидается выражение')]),
    ('empty_statement', 'f() returns r: int { ; }', [(';', 'ожидается оператор')]),
    ('else_without_if', 'f() returns r: int { else r = 1; }', [('else', 'ожидается оператор')]),
    ('call_as_statement', 'f() returns r: int { g(1); }', [('(1)', "ожидается '='")]),
    ('leading_zero_numbers', HEAD + 'r = 007;', [('07;', "ожидается ';'")]),
    ('formula_without_body', 'P(x: int) =>', [(None, 'ожидается')]),
    ('multiple_errors_one_block', 'f() returns r: int {\n  r = ;\n  x = 1 2;\n  y = 3;\n}',
     [(';\n  x', 'ожидается выражение'), ('2;', "ожидается ';'")]),
]


def _make_neg(name, src, expected):
    def _t():
        res = parse(src)
        got = [(d.line, d.col, d.message) for d in res.errors]
        assert len(got) == len(expected), f'{name}: ожидалось {len(expected)} ошибок, получено {got}'
        for (line, col, msg), (needle, frag) in zip(got, expected):
            if needle is None:
                off = len(src)
            elif isinstance(needle, tuple):  # (фрагмент, смещение внутри него)
                off = src.index(needle[0]) + needle[1]
            else:
                off = src.index(needle)
            assert (line, col) == line_col(src, off), (
                f'{name}: позиция {line}:{col}, ожидалась {line_col(src, off)} ({msg})')
            assert frag in msg, f'{name}: {msg!r} не содержит {frag!r}'
    _t.__name__ = f'test_neg_{name}'
    return _t


NEG_TESTS = []
for _name, _src, _exp in NEGATIVE:
    _f = _make_neg(_name, _src, _exp)
    globals()[_f.__name__] = _f
    NEG_TESTS.append(_f)


def test_neg_crlf_position():
    src = 'f() returns r: int\r\n{\r\n  r = ;\r\n}'
    errs = parse(src).errors
    assert [(e.line, e.col) for e in errs] == [(3, 7)], errs


def test_recovery_continues_in_block():
    res = parse('f() returns r: int {\n  r = ;\n  x = 1 2;\n  y = 3;\n}')
    assert len(res.errors) == 2
    stmts = res.ast.decls[0].body.stmts
    assert sx(to_dict(stmts)) == '[(Assign y (Num 3))]'


def test_recovery_continues_at_module_level():
    res = parse('f( returns r: int r = 1;\ng() returns r: int r = 2;')
    assert len(res.errors) == 1
    assert [d.name for d in res.ast.decls] == ['g']


def test_recovery_after_bad_statement_resumes_at_if_and_while():
    src = 'f() returns r: int {\n  x = = 1\n  if (x > 0) r = 1;\n}'
    res = parse(src)
    assert len(res.errors) == 1
    assert [type(s).__name__ for s in res.ast.decls[0].body.stmts] == ['If']


def test_errors_sorted_and_positive_coordinates():
    res = parse('f( returns r: int { r = ; x = 1 2; }\n}\n')
    assert res.errors
    assert all(e.line >= 1 and e.col >= 1 for e in res.errors)
    keys = [(e.line, e.col) for e in res.errors]
    assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# Устойчивость: ошибки не зацикливают и не роняют парсер
# ---------------------------------------------------------------------------

class _Timeout(Exception):
    pass


def _alarm(seconds):
    if hasattr(signal, 'SIGALRM'):
        def handler(signum, frame):
            raise _Timeout('парсер завис')
        signal.signal(signal.SIGALRM, handler)
        signal.alarm(seconds)


def _check_terminates(src):
    res = parse(src)
    for d in res.errors:
        assert d.line >= 1 and d.col >= 1


def test_no_hang_on_truncation_and_deletion():
    with open(os.path.join(HERE, 'sample_hw2.funny'), encoding='utf-8') as f:
        src = f.read()
    toks = Parser(src).toks[:-1]
    _alarm(60)
    try:
        for t in toks:
            _check_terminates(src[:t.pos])                       # обрыв перед токеном
            _check_terminates(src[:t.pos + len(t.lexeme)])       # обрыв после токена
            _check_terminates(src[:t.pos] + src[t.pos + len(t.lexeme):])   # удаление токена
            _check_terminates(src[:t.pos] + t.lexeme + ' ' + src[t.pos:])  # дублирование
    finally:
        if hasattr(signal, 'SIGALRM'):
            signal.alarm(0)


def test_no_hang_on_garbage():
    garbage = [')', '}', '{', '((((', '))))', '{{{{', '}}}}', ';;;;', '= = =', '-> -> ->',
               '| | |', 'if if if', 'else else', 'f(f(f(', 'forall(', 'a[a[a[', ',,,,',
               'f() returns', 'f() returns r: int {{{{ r = (', '\x00', 'é é é']
    _alarm(30)
    try:
        for g in garbage:
            _check_terminates(g)
            _check_terminates(HEAD + g)
            _check_terminates(HEAD + '{ ' + g + ' }')
    finally:
        if hasattr(signal, 'SIGALRM'):
            signal.alarm(0)


# ---------------------------------------------------------------------------

def run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f'[OK  ] {name[5:]}')
            passed += 1
        except Exception as e:  # AssertionError и неожиданные исключения
            print(f'[FAIL] {name[5:]}: {type(e).__name__}: {e}')
    print(f'\n{passed}/{len(tests)} тестов пройдено')
    return passed == len(tests)


if __name__ == '__main__':
    import sys
    sys.exit(0 if run_all() else 1)
