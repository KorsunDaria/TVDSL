#!/usr/bin/env python3
"""
HW4: IR (трёхадресный код) для Funny, CFG, локальные оптимизации, раскраска временных.

Запуск:  python3 funny_ir.py program.funny [--opt const,alg,dce,cse|all|none] [--dot] [--out DIR]
Выход:   program.ir.txt (IR до/после + метрики), program.ir.json (то же в JSON),
         с --dot: program.<func>.cfg.dot и program.<func>.conflicts.dot.
Код возврата: 0 — ок, 1 — синтаксические/семантические ошибки (HW2/HW3).
Формат IR — README.md.
"""

import argparse
import copy
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field, replace, asdict
from itertools import count

from funny_parser import (Function, Block, Assign, TupleAssign, If, While, Assert, Num, Var,
                          Neg, BinOp, Index, ArrayUpdate, BoolLit, Compare, Not, And, Or,
                          Implies, Quantifier, FormulaRef)
from funny_checker import check

ARITH = {'+': 'add', '-': 'sub', '*': 'mul', '/': 'div'}
TERM = {'br', 'jmp', 'ret', 'trap'}
DCE_PURE = {'mov', 'add', 'sub', 'mul', 'neg', 'alen'}   # не бросают исключений -> можно удалять
CSE_OPS = {'add', 'sub', 'mul', 'div', 'neg', 'aload', 'alen'}
PASSES = {'const', 'alg', 'cse', 'dce'}


# ---------------------------------------------------------------- семантика операций

def wrap(x):                      # int = 32 бита, как в JVM
    return (x + 2**31) % 2**32 - 2**31


def tdiv(a, b):                   # деление с усечением к нулю (idiv)
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


ALU = {'add': lambda a, b: a + b, 'sub': lambda a, b: a - b, 'mul': lambda a, b: a * b,
       'div': tdiv, 'neg': lambda a: -a}
CMPF = {'==': lambda a, b: a == b, '!=': lambda a, b: a != b, '<': lambda a, b: a < b,
        '<=': lambda a, b: a <= b, '>': lambda a, b: a > b, '>=': lambda a, b: a >= b}


# ---------------------------------------------------------------- IR

@dataclass
class Instr:
    op: str
    dst: object = None                       # str | list[str] (call) | None
    args: list = field(default_factory=list)  # имена (str) или целые константы (int)
    aux: object = None                       # label / callee / (cmp, Ltrue, Lfalse)

    def defs(self):
        return [] if self.dst is None else self.dst if isinstance(self.dst, list) else [self.dst]

    def uses(self):
        return [a for a in self.args if isinstance(a, str)]

    def __str__(self):
        o, a = self.op, ' '.join(map(str, self.args))
        if o == 'label':
            return f'{self.aux}:'
        if o == 'jmp':
            return f'  jmp {self.aux}'
        if o == 'br':
            return f'  br {self.aux[0]} {a} {self.aux[1]} {self.aux[2]}'
        if o == 'ret':
            return f'  ret {", ".join(self.args)}'.rstrip()
        if o == 'trap':
            return '  trap'
        if o == 'call':
            return f'  {", ".join(self.dst)} = call {self.aux} {a}'.rstrip()
        return f'  {self.dst} = {o} {a}'


@dataclass
class IRFunc:
    name: str
    params: list      # [(имя, тип)]
    returns: list
    locals: list
    types: dict       # имя -> 'int' | 'int[]' (включая временные %tN)
    code: list        # линейный IR: Instr, в т.ч. op='label'


def is_tmp(n):
    return n.startswith('%')


# ---------------------------------------------------------------- генерация IR

def runtime(e):
    """Предикат можно проверить в рантайме (нет кванторов и ссылок на формулы)."""
    if isinstance(e, (Quantifier, FormulaRef)):
        return False
    if isinstance(e, (And, Or, Implies)):
        return runtime(e.left) and runtime(e.right)
    return runtime(e.operand) if isinstance(e, Not) else True


class Gen:
    def __init__(self, d):
        self.code, self.nt, self.nl = [], 0, 0
        self.types = {v.name: v.type for v in d.params + d.returns + d.locals}

    def emit(self, op, dst=None, args=(), aux=None):
        self.code.append(Instr(op, dst, list(args), aux))

    def tmp(self, ty):
        self.nt += 1
        self.types[f'%t{self.nt}'] = ty
        return f'%t{self.nt}'

    def label(self):
        self.nl += 1
        return f'L{self.nl}'

    def place(self, l):
        self.emit('label', aux=l)

    def expr(self, e, dst=None):
        """Возвращает операнд (int-константа или имя); при dst результат кладётся в dst."""
        if isinstance(e, (Num, Var)):
            v = e.value if isinstance(e, Num) else e.name
            if dst:
                self.emit('mov', dst, [v])
                return dst
            return v
        d = dst or self.tmp(e.ty)
        if isinstance(e, Neg):
            self.emit('neg', d, [self.expr(e.operand)])
        elif isinstance(e, BinOp):
            a, b = self.expr(e.left), self.expr(e.right)
            self.emit(ARITH[e.op], d, [a, b])
        elif isinstance(e, Index):
            self.emit('aload', d, [e.array, self.expr(e.index)])
        elif e.name == 'length':
            self.emit('alen', d, [self.expr(e.args[0])])
        else:
            self.emit('call', [d], [self.expr(x) for x in e.args], e.name)
        return d

    def cond(self, e, lt, lf):
        """Условие -> ветвления с коротким замыканием."""
        if isinstance(e, Compare):
            a, b = self.expr(e.left), self.expr(e.right)
            self.emit('br', None, [a, b], (e.op, lt, lf))
        elif isinstance(e, Not):
            self.cond(e.operand, lf, lt)
        elif isinstance(e, BoolLit):
            self.emit('jmp', aux=lt if e.value else lf)
        else:
            mid = self.label()
            if isinstance(e, And):
                self.cond(e.left, mid, lf)
            elif isinstance(e, Or):
                self.cond(e.left, lt, mid)
            else:                                   # Implies: not L or R
                self.cond(e.left, mid, lt)
            self.place(mid)
            self.cond(e.right, lt, lf)

    def stmt(self, s):
        if isinstance(s, Block):
            for x in s.stmts:
                self.stmt(x)
        elif isinstance(s, Assign):
            v = s.value
            if isinstance(v, ArrayUpdate):
                i = self.expr(v.index)
                x = self.expr(v.value)
                self.emit('aupd', s.target, [v.array, i, x])
            else:
                self.expr(v, s.target)
        elif isinstance(s, TupleAssign):
            self.emit('call', list(s.targets), [self.expr(x) for x in s.call.args], s.call.name)
        elif isinstance(s, If):
            lt, le = self.label(), self.label()
            lf = self.label() if s.orelse else le
            self.cond(s.cond, lt, lf)
            self.place(lt)
            self.stmt(s.then)
            if s.orelse:
                self.emit('jmp', aux=le)
                self.place(lf)
                self.stmt(s.orelse)
            self.place(le)
        elif isinstance(s, While):
            lc, lb, le = self.label(), self.label(), self.label()
            self.place(lc)
            self.cond(s.cond, lb, le)
            self.place(lb)
            self.stmt(s.body)
            self.emit('jmp', aux=lc)
            self.place(le)
        elif isinstance(s, Assert) and runtime(s.cond):
            ok, bad = self.label(), self.label()
            self.cond(s.cond, ok, bad)
            self.place(bad)
            self.emit('trap')
            self.place(ok)
        # Assume, нерантаймовые assert: только для верификации, в IR не попадают


def gen_function(d):
    g = Gen(d)
    g.place('entry')
    g.stmt(d.body)
    g.emit('ret', None, [r.name for r in d.returns])
    mk = lambda vs: [(v.name, v.type) for v in vs]
    return IRFunc(d.name, mk(d.params), mk(d.returns), mk(d.locals), g.types, g.code)


# ---------------------------------------------------------------- CFG

@dataclass
class BB:
    label: str
    instrs: list

    @property
    def succs(self):
        t = self.instrs[-1]
        if t.op == 'jmp':
            return [t.aux]
        return list(dict.fromkeys(t.aux[1:])) if t.op == 'br' else []


def build_cfg(code):
    """Линейный IR -> список базовых блоков (каждый завершён терминатором)."""
    blocks, n = [], 0
    term = lambda b: bool(b.instrs) and b.instrs[-1].op in TERM
    for i in code:
        if i.op == 'label':
            if blocks and not term(blocks[-1]):
                blocks[-1].instrs.append(Instr('jmp', aux=i.aux))
            blocks.append(BB(i.aux, []))
        else:
            if term(blocks[-1]):
                n += 1
                blocks.append(BB(f'dead{n}', []))
            blocks[-1].instrs.append(i)
    return blocks


def reachable(blocks):
    by, seen, stack = {b.label: b for b in blocks}, set(), [blocks[0].label]
    while stack:
        l = stack.pop()
        if l not in seen:
            seen.add(l)
            stack += by[l].succs
    return [b for b in blocks if b.label in seen]


def flatten(blocks):
    """Блоки -> линейный IR (jmp на следующий блок опускается, лишние метки не печатаются)."""
    body = []
    for k, b in enumerate(blocks):
        ins = list(b.instrs)
        if ins[-1].op == 'jmp' and k + 1 < len(blocks) and ins[-1].aux == blocks[k + 1].label:
            ins.pop()
        body.append(ins)
    ref = {l for ins in body for i in ins if i.op in ('jmp', 'br')
           for l in ([i.aux] if i.op == 'jmp' else i.aux[1:])}
    out = []
    for k, (b, ins) in enumerate(zip(blocks, body)):
        if k == 0 or b.label in ref:
            out.append(Instr('label', aux=b.label))
        out += ins
    return out


# ---------------------------------------------------------------- живость, конфликты, раскраска

def liveness(blocks):
    use, dfn = {}, {}
    for b in blocks:
        u, d = set(), set()
        for i in b.instrs:
            u |= {x for x in i.uses() if x not in d}
            d |= set(i.defs())
        use[b.label], dfn[b.label] = u, d
    lin = {b.label: set() for b in blocks}
    lout = {b.label: set() for b in blocks}
    changed = True
    while changed:
        changed = False
        for b in reversed(blocks):
            o = set().union(*[lin[s] for s in b.succs]) if b.succs else set()
            n = use[b.label] | (o - dfn[b.label])
            if o != lout[b.label] or n != lin[b.label]:
                lout[b.label], lin[b.label], changed = o, n, True
    return lin, lout


def interference(blocks):
    """Граф конфликтов временных (%tN) по живости: {tmp: {соседи}}."""
    _, lout = liveness(blocks)
    g = defaultdict(set)
    for b in blocks:
        live = set(lout[b.label])
        for i in reversed(b.instrs):
            ds = i.defs()
            for d in ds:
                if is_tmp(d):
                    g[d]
                    for x in live | set(ds):
                        if x != d and is_tmp(x):
                            g[d].add(x)
                            g[x].add(d)
            live -= set(ds)
            live |= set(i.uses())
    return g


def color(g):
    """Жадная раскраска (порядок по убыванию степени)."""
    col = {}
    for n in sorted(g, key=lambda n: (-len(g[n]), n)):
        used = {col[m] for m in g[n] if m in col}
        col[n] = next(c for c in count() if c not in used)
    return col


def analyze(blocks):
    g = interference(blocks)
    col = color(g)
    temps = {n for b in blocks for i in b.instrs for n in i.defs() + i.uses() if is_tmp(n)}
    m = {'instrs': sum(i.op != 'label' for i in flatten(blocks)), 'blocks': len(blocks),
         'temps': len(temps), 'colors': len(set(col.values()))}
    return m, g, col


# ---------------------------------------------------------------- локальные оптимизации

def p_const(b):
    """Распространение констант и копий + свёртка; br с константами -> jmp."""
    env = {}
    for i in b.instrs:
        if i.op != 'ret':
            i.args = [env.get(a, a) if isinstance(a, str) else a for a in i.args]
        if i.op in ALU and all(isinstance(a, int) for a in i.args) \
                and not (i.op == 'div' and i.args[1] == 0):
            i.op, i.args = 'mov', [wrap(ALU[i.op](*i.args))]
        elif i.op == 'br' and all(isinstance(a, int) for a in i.args):
            c, t, f = i.aux
            i.op, i.aux, i.args = 'jmp', (t if CMPF[c](*i.args) else f), []
        for d in i.defs():
            env.pop(d, None)
            for k in [k for k, v in env.items() if v == d]:
                del env[k]
        if i.op == 'mov' and i.dst != i.args[0]:
            env[i.dst] = i.args[0]


def p_alg(b):
    """Алгебраические упрощения: x+0, x*1, x*0, x-x, x/1, x*2 -> x+x."""
    for i in b.instrs:
        if i.op not in ALU or i.op == 'neg':
            continue
        (x, y), k, r = i.args, i.op, None
        if k in ('add', 'sub') and y == 0:
            r = x
        elif k == 'add' and x == 0:
            r = y
        elif k in ('mul', 'div') and y == 1:
            r = x
        elif k == 'mul' and x == 1:
            r = y
        elif k == 'mul' and 0 in (x, y):
            r = 0
        elif k == 'sub' and x == y:
            r = 0
        if r is not None:
            i.op, i.args = 'mov', [r]
        elif k == 'mul' and 2 in (x, y):
            o = y if x == 2 else x
            i.op, i.args = 'add', [o, o]


def p_cse(b):
    """Устранение общих подвыражений в пределах блока (add/mul коммутативны)."""
    av = {}
    for i in b.instrs:
        key = None
        if i.op in CSE_OPS:
            a = sorted(i.args, key=str) if i.op in ('add', 'mul') else i.args
            key = (i.op, tuple(a))
            if key in av:
                i.op, i.args, key = 'mov', [av[key]], None
        for d in i.defs():
            av = {k: v for k, v in av.items() if v != d and d not in k[1]}
        if key and i.dst not in key[1]:
            av[key] = i.dst


def p_dce(blocks):
    """Удаление мёртвых присваиваний (обратный проход по блоку от live-out)."""
    _, lout = liveness(blocks)
    for b in blocks:
        live, keep = set(lout[b.label]), []
        for i in reversed(b.instrs):
            ds = i.defs()
            pure = i.op in DCE_PURE or (i.op == 'div' and isinstance(i.args[1], int) and i.args[1] != 0)
            if (pure and not set(ds) & live) or (i.op == 'mov' and i.dst == i.args[0]):
                continue
            keep.append(i)
            live = (live - set(ds)) | set(i.uses())
        b.instrs = keep[::-1]


def optimize(blocks, passes):
    for _ in range(30):
        snap = [(b.label, [str(i) for i in b.instrs]) for b in blocks]
        for b in blocks:
            if 'const' in passes:
                p_const(b)
            if 'alg' in passes:
                p_alg(b)
            if 'cse' in passes:
                p_cse(b)
        if 'dce' in passes:
            p_dce(blocks)
        blocks = reachable(blocks)
        if snap == [(b.label, [str(i) for i in b.instrs]) for b in blocks]:
            break
    return blocks


# ---------------------------------------------------------------- интерпретатор IR (для проверки)

class Trap(Exception):
    pass


def run(prog, name, args, fuel=500000):
    """Исполняет линейный IR функции name из prog ({имя: IRFunc}); возвращает кортеж результатов."""
    st = [fuel]

    def call(name, args):
        f = prog[name]
        env = {n: ([] if t == 'int[]' else 0) for n, t in f.types.items()}
        env.update(zip((p for p, _ in f.params), args))
        pos = {i.aux: k for k, i in enumerate(f.code) if i.op == 'label'}
        val = lambda a: env[a] if isinstance(a, str) else a
        pc = 0
        while True:
            i = f.code[pc]
            pc += 1
            o = i.op
            if o == 'label':
                continue
            st[0] -= 1
            if st[0] < 0:
                raise Trap('fuel')
            if o == 'mov':
                env[i.dst] = val(i.args[0])
            elif o in ALU:
                x = [val(a) for a in i.args]
                if o == 'div' and x[1] == 0:
                    raise Trap('div0')
                env[i.dst] = wrap(ALU[o](*x))
            elif o == 'alen':
                env[i.dst] = len(val(i.args[0]))
            elif o in ('aload', 'aupd'):
                a, k = val(i.args[0]), val(i.args[1])
                if not 0 <= k < len(a):
                    raise Trap('index')
                if o == 'aload':
                    env[i.dst] = a[k]
                else:
                    env[i.dst] = a[:k] + [val(i.args[2])] + a[k + 1:]
            elif o == 'call':
                for d, v in zip(i.dst, call(i.aux, [val(a) for a in i.args])):
                    env[d] = v
            elif o == 'br':
                c, t, fl = i.aux
                pc = pos[t if CMPF[c](val(i.args[0]), val(i.args[1])) else fl]
            elif o == 'jmp':
                pc = pos[i.aux]
            elif o == 'ret':
                return tuple(val(a) for a in i.args)
            else:
                raise Trap('assert')

    return call(name, list(args))


# ---------------------------------------------------------------- конвейер и вывод

def compile_text(text, passes):
    """Funny-код -> (список результатов по функциям, ошибки HW2/HW3)."""
    ast, errors = check(text)
    if errors:
        return None, errors
    res = []
    for d in ast.decls:
        if not isinstance(d, Function):
            continue                          # формулы — только для спецификаций
        f = gen_function(d)
        b0 = build_cfg(f.code)
        b1 = optimize(copy.deepcopy(b0), passes)
        m0, g0, c0 = analyze(b0)
        m1, g1, c1 = analyze(b1)
        res.append({'name': d.name, 'ir0': replace(f, code=flatten(b0)),
                    'ir1': replace(f, code=flatten(b1)), 'bb0': b0, 'bb1': b1,
                    'm0': m0, 'm1': m1, 'g1': g1, 'col1': c1})
    return res, []


def program(res, key):
    return {r['name']: r[key] for r in res}


def text_report(res, passes):
    out = [f'# оптимизации: {",".join(sorted(passes)) or "нет"}']
    for r in res:
        out += [f'\n== {r["name"]} ==', '-- IR до --'] + [str(i) for i in r['ir0'].code]
        out += ['-- IR после --'] + [str(i) for i in r['ir1'].code]
        out += ['-- раскраска временных (после) --',
                '  ' + (', '.join(f'{t}:c{c}' for t, c in sorted(r['col1'].items())) or '(нет временных)')]
        out.append('-- метрики (до -> после) --')
        out += [f'  {k:7} {r["m0"][k]:>4} -> {r["m1"][k]:>4}' for k in r['m0']]
    return '\n'.join(out) + '\n'


def json_report(res, passes):
    fn = {}
    for r in res:
        f = r['ir1']
        fn[r['name']] = {
            'params': f.params, 'returns': f.returns, 'locals': f.locals, 'types': f.types,
            'before': {'code': [asdict(i) for i in r['ir0'].code], 'metrics': r['m0']},
            'after': {'code': [asdict(i) for i in f.code], 'metrics': r['m1'],
                      'coloring': r['col1'],
                      'cfg': {b.label: b.succs for b in r['bb1']}}}
    return json.dumps({'passes': sorted(passes), 'functions': fn}, ensure_ascii=False, indent=2)


def cfg_dot(name, blocks):
    L = [f'digraph "{name}" {{', '  node [shape=box, fontname=monospace];']
    for b in blocks:
        body = ''.join(str(i).strip() + '\\l' for i in b.instrs)
        L.append(f'  "{b.label}" [label="{b.label}:\\l{body}"];')
        L += [f'  "{b.label}" -> "{s}";' for s in b.succs]
    return '\n'.join(L + ['}']) + '\n'


def conflicts_dot(name, g, col):
    L = [f'graph "{name}" {{', '  node [style=filled, colorscheme=set312];']
    for n in sorted(g):
        L.append(f'  "{n}" [label="{n}\\nc{col[n]}", fillcolor={col[n] % 12 + 1}];')
    L += [f'  "{a}" -- "{b}";' for a in sorted(g) for b in sorted(g[a]) if a < b]
    return '\n'.join(L + ['}']) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description='Funny -> IR, CFG, оптимизации, раскраска')
    ap.add_argument('file')
    ap.add_argument('--opt', default='const,alg,dce',
                    help='список через запятую из const,alg,cse,dce; all; none (по умолчанию const,alg,dce)')
    ap.add_argument('--dot', action='store_true', help='писать CFG и граф конфликтов в DOT')
    ap.add_argument('--out', help='каталог вывода (по умолчанию рядом с исходником)')
    a = ap.parse_args(argv)
    passes = PASSES if a.opt == 'all' else set() if a.opt == 'none' else set(a.opt.split(','))
    if passes - PASSES:
        ap.error(f'неизвестные оптимизации: {sorted(passes - PASSES)}')
    with open(a.file, encoding='utf-8') as f:
        res, errors = compile_text(f.read(), passes)
    if errors:
        print('\n'.join(f'{a.file}:{d}' for d in errors), file=sys.stderr)
        return 1
    out_dir = a.out or os.path.dirname(os.path.abspath(a.file))
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, os.path.splitext(os.path.basename(a.file))[0])
    txt = text_report(res, passes)
    for ext, data in (('.ir.txt', txt), ('.ir.json', json_report(res, passes) + '\n')):
        with open(base + ext, 'w', encoding='utf-8') as f:
            f.write(data)
    if a.dot:
        for r in res:
            with open(f'{base}.{r["name"]}.cfg.dot', 'w', encoding='utf-8') as f:
                f.write(cfg_dot(r['name'], r['bb1']))
            with open(f'{base}.{r["name"]}.conflicts.dot', 'w', encoding='utf-8') as f:
                f.write(conflicts_dot(r['name'], r['g1'], r['col1']))
    print(txt)
    return 0


if __name__ == '__main__':
    sys.exit(main())
