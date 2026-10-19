#!/usr/bin/env python3
"""
HW3: семантические проверки и типы для Funny (поверх парсера HW2).

Запуск:  python3 funny_checker.py program.funny
AST (JSON) пишется в program.typed.json и в stdout; ошибки `файл:строка:столбец: ошибка: ...` —
в program.errors.txt (если есть) и в stderr.
Код возврата: 0 — ошибок нет, 1 — есть ошибки (синтаксические или семантические).
"""

import json
import os
import sys
from dataclasses import fields

from funny_parser import (Node, Module, VarDef, Function, Formula, Block, Assign, TupleAssign,
                          If, While, Assert, Assume, Num, Var, Neg, BinOp, Call, Index,
                          ArrayUpdate, BoolLit, Compare, Not, And, Or, Implies, Quantifier,
                          FormulaRef, Diagnostic, parse)

INT, ARR, BOOL, ERR = 'int', 'int[]', 'bool', '?' 
KIND = {'func': 'функция', 'formula': 'формула'}


def unify(a, b):
    if a == ERR:
        return b
    return a if b == ERR or a == b else None


class Checker:
    def __init__(self):
        self.errors = []
        self.funcs = {'length': ('func', [ARR], [INT])}  
        self.env = {}      
        self.locals = []    

    def err(self, node, msg):
        self.errors.append(Diagnostic(node.pos[0], node.pos[1], msg))

    def declare(self, defs, scope):
        for v in defs:
            if v.name in scope:
                self.err(v, f"повторное объявление переменной '{v.name}'")
            else:
                scope[v.name] = v.type

    def module(self, m):
        for d in m.decls:
            is_fn = isinstance(d, Function)
            sig = ('func' if is_fn else 'formula', [p.type for p in d.params],
                   [r.type for r in d.returns] if is_fn else [BOOL])
            if d.name in self.funcs:
                self.err(d, f"повторное определение '{d.name}'")
            else:
                self.funcs[d.name] = sig       
            scope = {}
            self.declare(d.params, scope)
            self.locals = d.locals if is_fn else []
            if not is_fn:
                self.env = scope
                self.want(d.body, BOOL, 'тело формулы')
                continue
            if d.requires:
                self.env = dict(scope)
                self.want(d.requires, BOOL, 'requires')
            self.declare(d.returns, scope)
            if d.ensures:
                self.env = dict(scope)
                self.want(d.ensures, BOOL, 'ensures')
            self.declare(d.locals, scope)
            self.env = scope
            self.stmt(d.body)


    def stmt(self, s):
        if isinstance(s, Block):
            for x in s.stmts:
                self.stmt(x)
        elif isinstance(s, Assign):
            self.bind(s.target, self.expr(s.value), s)
        elif isinstance(s, TupleAssign):
            rets = self.call(s.call, 'func')
            s.call.ty = '(' + ', '.join(rets) + ')'
            if ERR in rets:
                rets = [ERR] * len(s.targets)
            elif len(rets) != len(s.targets):
                self.err(s, f"слева {len(s.targets)} переменных, '{s.call.name}' возвращает {len(rets)}")
                return
            for name, t in zip(s.targets, rets):
                self.bind(name, t, s)
        elif isinstance(s, If):
            self.want(s.cond, BOOL, 'условие if')
            self.stmt(s.then)
            if s.orelse:
                self.stmt(s.orelse)
        elif isinstance(s, While):
            self.want(s.cond, BOOL, 'условие while')
            if s.invariant:
                self.want(s.invariant, BOOL, 'invariant')
            self.stmt(s.body)
        elif isinstance(s, (Assert, Assume)):
            self.want(s.cond, BOOL, type(s).__name__.lower())

    def bind(self, name, t, node):
        cur = self.env.get(name)
        if cur is None:
            self.env[name] = t
            if t != ERR:
                v = VarDef(name, t)
                v.pos = node.pos
                self.locals.append(v)
        elif unify(cur, t) is None:
            self.err(node, f"нельзя присвоить {t} переменной '{name}' типа {cur}")

    def want(self, node, expected, ctx):
        got = self.expr(node)
        if unify(got, expected) is None:
            self.err(node, f"{ctx}: ожидается {expected}, получено {got}")

    def expr(self, e):
        e.ty = self._expr(e)
        return e.ty

    def array(self, e):
        t = self.env.get(e.array)
        if t is None:
            self.err(e, f"переменная '{e.array}' не объявлена")
        elif t not in (ARR, ERR):
            self.err(e, f"'{e.array}' не массив (тип {t})")

    def _expr(self, e):
        if isinstance(e, Num):
            return INT
        if isinstance(e, BoolLit):
            return BOOL
        if isinstance(e, Var):
            t = self.env.get(e.name)
            if t is None:
                self.err(e, f"переменная '{e.name}' не объявлена (или используется до первого присваивания)")
                return ERR
            return t
        if isinstance(e, Neg):
            self.want(e.operand, INT, "унарный '-'")
            return INT
        if isinstance(e, (BinOp, Compare)):
            self.want(e.left, INT, f"операнд '{e.op}'")
            self.want(e.right, INT, f"операнд '{e.op}'")
            return INT if isinstance(e, BinOp) else BOOL
        if isinstance(e, Not):
            self.want(e.operand, BOOL, 'not')
            return BOOL
        if isinstance(e, (And, Or, Implies)):
            op = {And: 'and', Or: 'or', Implies: '->'}[type(e)]
            self.want(e.left, BOOL, f"операнд '{op}'")
            self.want(e.right, BOOL, f"операнд '{op}'")
            return BOOL
        if isinstance(e, Quantifier):
            outer = self.env
            self.env = {**outer, e.var.name: e.var.type}   # своя область видимости, затенение допустимо
            self.want(e.body, BOOL, e.kind)
            self.env = outer
            return BOOL
        if isinstance(e, (Index, ArrayUpdate)):
            self.array(e)
            self.want(e.index, INT, 'индекс')
            if isinstance(e, Index):
                return INT
            self.want(e.value, INT, 'значение элемента')
            return ARR
        rets = self.call(e, 'formula' if isinstance(e, FormulaRef) else 'func')
        if len(rets) > 1:
            self.err(e, f"'{e.name}' возвращает {len(rets)} значений, в выражении нужно одно")
            return ERR
        return rets[0]

    def call(self, c, kind):
        ts = [self.expr(a) for a in c.args]
        sig = self.funcs.get(c.name)
        if sig is None:
            self.err(c, f"неизвестная функция '{c.name}' (не определена выше)")
            return [ERR]
        k, params, rets = sig
        if k != kind:
            self.err(c, f"'{c.name}' — {KIND[k]}, а не {KIND[kind]}")
            return [ERR]
        if len(ts) != len(params):
            self.err(c, f"'{c.name}' ожидает {len(params)} арг., передано {len(ts)}")
            return rets
        for i, (t, p) in enumerate(zip(ts, params)):
            if unify(t, p) is None:
                self.err(c.args[i], f"аргумент {i + 1} '{c.name}': ожидается {p}, получено {t}")
        return rets


def check(text):
    res = parse(text)
    errors = res.errors
    if not errors:
        c = Checker()
        c.module(res.ast)
        errors = c.errors
    return res.ast, sorted(errors, key=lambda d: (d.line, d.col))


def dump(n):
    if isinstance(n, Node):
        d = {'node': type(n).__name__}
        for f in fields(n):
            d[f.name] = dump(getattr(n, f.name))
        if hasattr(n, 'ty'):
            d['etype'] = n.ty
        d['pos'] = f'{n.pos[0]}:{n.pos[1]}'
        return d
    if isinstance(n, list):
        return [dump(x) for x in n]
    return n


def main():
    if len(sys.argv) != 2:
        print('Использование: python3 funny_checker.py program.funny')
        sys.exit(2)
    path = sys.argv[1]
    with open(path, encoding='utf-8') as f:
        ast, errors = check(f.read())
    lines = [f'{path}:{d}' for d in errors]
    out = json.dumps(dump(ast), ensure_ascii=False, indent=2)
    base = os.path.splitext(path)[0]
    with open(base + '.typed.json', 'w', encoding='utf-8') as f:   
        f.write(out + '\n')
    if lines:
        with open(base + '.errors.txt', 'w', encoding='utf-8') as f:  
            f.write('\n'.join(lines) + '\n')
    print('\n'.join(lines), file=sys.stderr)                         
    print(out)
    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main()