#!/usr/bin/env python3
"""Тесты HW4: python3 -m unittest test_ir -v"""

import json
import os
import tempfile
import unittest
from itertools import chain, combinations

from funny_ir import PASSES, Trap, compile_text, main, program, run

HERE = os.path.dirname(os.path.abspath(__file__))
# примеры лежат в ./examples, а если файлы свалены в одну папку с тестом — ищем рядом
EX = os.path.join(HERE, 'examples') if os.path.isdir(os.path.join(HERE, 'examples')) else HERE
ALL_FLAGS = [set(c) for c in chain.from_iterable(combinations(sorted(PASSES), k) for k in range(5))]

# (файл, функция, наборы аргументов)
CASES = [
    ('arith', 'arith', [(x, y) for x in range(-3, 4) for y in (-2, 0, 5)]),
    ('branch', 'clamp', [(x, 0, 5) for x in range(-3, 9)]),
    ('branch', 'sgn', [(x,) for x in range(-3, 4)]),
    ('branch', 'constcond', [(x,) for x in range(-3, 4)]),
    ('loops', 'sumto', [(n,) for n in range(-1, 8)]),
    ('loops', 'fact', [(n,) for n in range(0, 8)]),
    ('loops', 'gcd', [(12, 18), (17, 5), (0, 7), (9, 0), (100, 75)]),
    ('loops', 'sumarr', [([],), ([1, 2, 3],), ([5, -4, 7, 0],)]),
    ('loops', 'fill', [([], 3), ([1, 2, 3], 9)]),
    ('calls', 'twice', [(x,) for x in range(-2, 3)]),
    ('calls', 'usecall', [(x,) for x in range(-2, 3)]),
    ('calls', 'divide', [(7, 2), (-7, 2), (7, -2), (5, 0)]),
    ('calls', 'caller', [(7, 2), (-7, 2), (6, 3), (5, 0)]),
    ('calls', 'checked', [(x,) for x in range(-2, 3)]),
]


def read(name):
    with open(os.path.join(EX, name + '.funny'), encoding='utf-8') as f:
        return f.read()


def outcome(prog, fn, args):
    try:
        return run(prog, fn, args)
    except Trap:
        return 'trap'


class TestEquivalence(unittest.TestCase):
    def test_results_same_before_after(self):
        """Результаты IR до/после совпадают для всех 16 комбинаций флагов."""
        for passes in ALL_FLAGS:
            for src, fn, inputs in CASES:
                res, errors = compile_text(read(src), passes)
                self.assertFalse(errors)
                p0, p1 = program(res, 'ir0'), program(res, 'ir1')
                for args in inputs:
                    self.assertEqual(outcome(p0, fn, args), outcome(p1, fn, args),
                                     f'{fn}{args} flags={sorted(passes)}')

    def test_ir_matches_expected_values(self):
        res, _ = compile_text(read('loops'), PASSES)
        p = program(res, 'ir1')
        self.assertEqual(run(p, 'gcd', (12, 18)), (6,))
        self.assertEqual(run(p, 'fact', (5,)), (120,))
        self.assertEqual(run(p, 'sumarr', ([1, 2, 3],)), (6,))
        self.assertEqual(run(p, 'fill', ([1, 2], 9)), ([9, 9],))
        res, _ = compile_text(read('calls'), PASSES)
        self.assertEqual(run(program(res, 'ir1'), 'divide', (-7, 2)), (-3, -1))  # idiv: усечение
        self.assertEqual(outcome(program(res, 'ir1'), 'checked', (-1,)), 'trap')


class TestOptimizations(unittest.TestCase):
    def get(self, src, fn, passes):
        res, _ = compile_text(read(src), passes)
        return next(r for r in res if r['name'] == fn)

    def ops(self, r, key='ir1'):
        return [i.op for i in r[key].code]

    def test_none_changes_nothing(self):
        r = self.get('arith', 'arith', set())
        self.assertEqual([str(i) for i in r['ir0'].code], [str(i) for i in r['ir1'].code])

    def test_const_and_alg(self):
        r = self.get('arith', 'arith', {'const', 'alg', 'dce'})
        self.assertLess(r['m1']['instrs'], r['m0']['instrs'])
        self.assertNotIn('div', self.ops(r))                    # a / 1 -> 14
        self.assertTrue(all(i.args != [3, 4] for i in r['ir1'].code))  # 3 * 4 свёрнуто

    def test_dce_removes_dead_var(self):
        r = self.get('arith', 'arith', {'const', 'dce'})
        self.assertNotIn('dead', [d for i in r['ir1'].code for d in i.defs()])
        r = self.get('arith', 'arith', {'const'})
        self.assertIn('dead', [d for i in r['ir1'].code for d in i.defs()])

    def test_cse(self):
        a = self.get('arith', 'arith', {'const', 'alg', 'dce'})
        b = self.get('arith', 'arith', {'const', 'alg', 'dce', 'cse'})
        self.assertLess(b['m1']['instrs'], a['m1']['instrs'])

    def test_branch_folding(self):
        r = self.get('branch', 'constcond', {'const', 'dce'})
        self.assertLess(r['m1']['blocks'], r['m0']['blocks'])
        r = self.get('branch', 'constcond', set())
        self.assertEqual(r['m1']['blocks'], r['m0']['blocks'])

    def test_coloring_valid(self):
        for src in ('arith', 'branch', 'loops', 'calls'):
            res, _ = compile_text(read(src), PASSES)
            for r in res:
                for n, nb in r['g1'].items():
                    for m in nb:
                        self.assertNotEqual(r['col1'][n], r['col1'][m], (r['name'], n, m))
                self.assertLessEqual(r['m1']['colors'], max(r['m1']['temps'], 0))


class TestPipeline(unittest.TestCase):
    def test_semantic_error_reported(self):
        res, errors = compile_text('f(x: int) returns r: int { r = y; }', set())
        self.assertIsNone(res)
        self.assertTrue(errors)

    def test_sample_hw2_compiles(self):
        res, errors = compile_text(read('sample_hw2'), PASSES)
        self.assertFalse(errors)
        self.assertEqual([r['name'] for r in res], ['gcd', 'fill', 'divide', 'caller'])

    def test_cli_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            code = main([os.path.join(EX, 'loops.funny'), '--opt', 'all', '--dot', '--out', d])
            self.assertEqual(code, 0)
            self.assertTrue(os.path.exists(os.path.join(d, 'loops.gcd.cfg.dot')))
            self.assertTrue(os.path.exists(os.path.join(d, 'loops.gcd.conflicts.dot')))
            with open(os.path.join(d, 'loops.ir.json'), encoding='utf-8') as f:
                data = json.load(f)
            self.assertIn('gcd', data['functions'])
            self.assertIn('coloring', data['functions']['gcd']['after'])


if __name__ == '__main__':
    unittest.main()