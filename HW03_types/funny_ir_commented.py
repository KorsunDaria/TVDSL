#!/usr/bin/env python3
"""
HW4: IR (трёхадресный код) для Funny, CFG, локальные оптимизации, раскраска временных.

Запуск:  python3 funny_ir.py program.funny [--opt const,alg,dce,cse|all|none] [--dot] [--out DIR]
Выход:   program.ir.txt (IR до/после + метрики), program.ir.json (то же в JSON),
         с --dot: program.<func>.cfg.dot и program.<func>.conflicts.dot.
Код возврата: 0 — ок, 1 — синтаксические/семантические ошибки (HW2/HW3).
Формат IR — README.md.
"""

# =====================================================================================
# ОБЩАЯ КАРТА ФАЙЛА (читай сначала)
#
#   Funny-код --(HW1-HW3: lexer, parser, checker)--> AST с типами
#       --gen_function-->      линейный IR (список инструкций с метками)       [ШАГ 1: генерация]
#       --build_cfg-->         список базовых блоков (граф потока управления)  [ШАГ 2: CFG]
#       --optimize-->          те же блоки, но упрощённые                      [ШАГ 3: оптимизации]
#       --interference/color-> граф конфликтов временных и их раскраска        [ШАГ 4: раскраска]
#       --flatten-->           снова линейный IR (для вывода и для кодогенерации)
#
#   run() — отдельный интерпретатор IR, нужен ТОЛЬКО для тестов: исполнить IR до и после
#   оптимизаций и убедиться, что результат одинаковый.
#   compile_text() склеивает все шаги, main() — командная строка.
# =====================================================================================

import argparse                     # разбор аргументов командной строки (--opt, --dot, --out)
import copy                         # deepcopy: оптимизации меняют инструкции на месте, нужна копия «до»
import json                         # запись .ir.json
import os                           # пути к файлам и каталогам
import sys                          # stderr и код возврата
from collections import defaultdict  # словарь с автосозданием значений (для графа конфликтов)
from dataclasses import dataclass, field, replace, asdict  # компактные классы; replace — копия с изменением; asdict — в JSON
from itertools import count         # бесконечный счётчик 0,1,2,... для подбора цвета

# Классы узлов AST из HW2. Мы по ним определяем, какой это оператор/выражение (isinstance).
from funny_parser import (Function, Block, Assign, TupleAssign, If, While, Assert, Num, Var,
                          Neg, BinOp, Index, ArrayUpdate, BoolLit, Compare, Not, And, Or,
                          Implies, Quantifier, FormulaRef)
# check() из HW3: парсит, проверяет типы и возвращает AST, где у каждого выражения есть .ty
from funny_checker import check

# Из символа арифметики в Funny -> имя инструкции IR.
ARITH = {'+': 'add', '-': 'sub', '*': 'mul', '/': 'div'}
# Терминаторы: инструкции, которыми обязан заканчиваться базовый блок (после них управление уходит из блока).
TERM = {'br', 'jmp', 'ret', 'trap'}
# Инструкции, которые безопасно удалить, если результат никому не нужен: они не могут «упасть».
# (div, aload, aupd, call сюда НЕ входят: они могут бросить исключение или иметь побочный эффект,
#  и удаление изменило бы поведение программы.)
DCE_PURE = {'mov', 'add', 'sub', 'mul', 'neg', 'alen'}
# Инструкции, значение которых зависит только от аргументов -> одинаковые вычисления можно переиспользовать.
CSE_OPS = {'add', 'sub', 'mul', 'div', 'neg', 'aload', 'alen'}
# Допустимые имена оптимизаций для флага --opt.
PASSES = {'const', 'alg', 'cse', 'dce'}


# ---------------------------------------------------------------- семантика операций
# Этот раздел задаёт, как ИМЕННО считаются числа. Им пользуются и оптимизатор (свёртка констант),
# и интерпретатор. Если бы они считали по-разному, «до» и «после» дали бы разные результаты.

def wrap(x):                      # int = 32 бита, как в JVM
    # ЗАЧЕМ: в JVM int переполняется по кругу (2147483647 + 1 = -2147483648). Мы повторяем это,
    # чтобы свёртка констант в оптимизаторе совпадала с тем, что посчитает JVM.
    # АРГУМЕНТ: x — любое целое Python (Python-числа не переполняются).
    # КАК: сдвигаем в диапазон [0, 2^32), берём остаток, сдвигаем обратно в [-2^31, 2^31).
    return (x + 2**31) % 2**32 - 2**31


def tdiv(a, b):                   # деление с усечением к нулю (idiv)
    # ЗАЧЕМ: Python делает `//` с округлением ВНИЗ (-7 // 2 = -4), а JVM idiv — к нулю (-7 / 2 = -3).
    # Нужна именно JVM-семантика, иначе результаты кодогенерации разойдутся с интерпретатором.
    # КАК: делим модули, потом возвращаем знак: минус, если знаки a и b различаются.
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


# Таблица «имя инструкции -> как её посчитать». Одна таблица на всех: и для свёртки констант, и для интерпретатора.
ALU = {'add': lambda a, b: a + b, 'sub': lambda a, b: a - b, 'mul': lambda a, b: a * b,
       'div': tdiv, 'neg': lambda a: -a}
# Таблица «знак сравнения -> функция». Нужна для br: и при свёртке br с константами, и в интерпретаторе.
CMPF = {'==': lambda a, b: a == b, '!=': lambda a, b: a != b, '<': lambda a, b: a < b,
        '<=': lambda a, b: a <= b, '>': lambda a, b: a > b, '>=': lambda a, b: a >= b}


# ---------------------------------------------------------------- IR

@dataclass
class Instr:
    # ОДНА инструкция IR. Все инструкции имеют одинаковую форму из 4 полей — так оптимизациям
    # проще работать со всеми сразу, без десятка разных классов.
    op: str                                   # что делаем: 'add', 'mov', 'br', 'label', ...
    dst: object = None                        # куда пишем результат: имя (str), список имён (call) или None
    args: list = field(default_factory=list)  # что читаем: имена (str) или константы (int)
    aux: object = None                        # «прочее»: метка (label/jmp), имя функции (call), (cmp, Lt, Lf) (br)

    def defs(self):
        # ЗАЧЕМ: единообразно получить «какие переменные эта инструкция ЗАПИСЫВАЕТ».
        # Нужно живости, DCE, распространению констант — всем, кто следит за присваиваниями.
        # Три случая dst: None -> []; список (call с несколькими результатами) -> он сам; строка -> [она].
        return [] if self.dst is None else self.dst if isinstance(self.dst, list) else [self.dst]

    def uses(self):
        # ЗАЧЕМ: «какие переменные инструкция ЧИТАЕТ». Константы (int) — не переменные, отбрасываем их.
        return [a for a in self.args if isinstance(a, str)]

    def __str__(self):
        # ЗАЧЕМ: красивый текстовый вывод IR (для .ir.txt и для сравнения «изменилось ли» в optimize).
        o, a = self.op, ' '.join(map(str, self.args))   # o — имя операции, a — аргументы через пробел
        if o == 'label':
            return f'{self.aux}:'                        # метка печатается без отступа: «L1:»
        if o == 'jmp':
            return f'  jmp {self.aux}'
        if o == 'br':                                    # br <сравнение> a b <куда если истина> <куда если ложь>
            return f'  br {self.aux[0]} {a} {self.aux[1]} {self.aux[2]}'
        if o == 'ret':
            return f'  ret {", ".join(self.args)}'.rstrip()   # rstrip убирает пробел, если возвращать нечего
        if o == 'trap':
            return '  trap'
        if o == 'call':                                  # q, r = call divide a b
            return f'  {", ".join(self.dst)} = call {self.aux} {a}'.rstrip()
        return f'  {self.dst} = {o} {a}'                # остальное: d = add x y, d = mov 5, ...


@dataclass
class IRFunc:
    # Результат трансляции ОДНОЙ функции Funny. Это то, что потом получит кодогенератор JVM.
    name: str      # имя функции
    params: list   # [(имя, тип)] параметры
    returns: list  # [(имя, тип)] переменные, значения которых возвращаются
    locals: list   # [(имя, тип)] локальные (в т.ч. объявленные неявно присваиванием)
    types: dict    # имя -> 'int' | 'int[]' для ВСЕХ имён, включая временные %tN
    code: list     # линейный IR: список Instr, в том числе с op='label'


def is_tmp(n):
    # ЗАЧЕМ: отличить временную (создана компилятором) от переменной пользователя.
    # Временные мы называем %t1, %t2...; символ % не может быть в идентификаторе Funny (лексер HW1
    # допускает только буквы/цифры), поэтому конфликта имён с пользовательскими не бывает.
    return n.startswith('%')


# ---------------------------------------------------------------- генерация IR
# ШАГ 1. Обходим AST и выписываем инструкции. Всё делает класс Gen.

def runtime(e):
    """Предикат можно проверить в рантайме (нет кванторов и ссылок на формулы)."""
    # ЗАЧЕМ: assert в Funny — это спецификация, и в нём бывают forall/exists и вызовы формул
    # (например `assert sorted(b)`). Их нельзя превратить в обычные сравнения, поэтому такие
    # assert мы пропускаем. Эта функция отвечает «можно ли всё-таки выполнить проверку».
    # АРГУМЕНТ: e — узел AST предиката.
    if isinstance(e, (Quantifier, FormulaRef)):
        return False                                 # квантор или формула — выполнить нельзя
    if isinstance(e, (And, Or, Implies)):
        return runtime(e.left) and runtime(e.right)  # составное условие: нужно, чтобы обе части были выполнимы
    return runtime(e.operand) if isinstance(e, Not) else True  # not — смотрим внутрь; сравнение/true/false — можно


class Gen:
    # Генератор кода для одной функции. Хранит накопленный код (self.code) и счётчики для
    # уникальных имён временных (nt) и меток (nl).
    def __init__(self, d):
        # АРГУМЕНТ: d — узел Function из AST.
        self.code, self.nt, self.nl = [], 0, 0
        # Заранее кладём в types типы параметров, возвращаемых и локальных переменных.
        # Типы временных добавим позже в tmp(). Тип нужен кодогенератору (int или массив — разные инструкции JVM).
        self.types = {v.name: v.type for v in d.params + d.returns + d.locals}

    def emit(self, op, dst=None, args=(), aux=None):
        # ЗАЧЕМ: единственное место, где инструкция добавляется в код. args превращаем в список,
        # чтобы оптимизации потом могли его менять.
        self.code.append(Instr(op, dst, list(args), aux))

    def tmp(self, ty):
        # ЗАЧЕМ: завести новую временную переменную для промежуточного результата, например
        # для `x*y` внутри `x*y + 7`. АРГУМЕНТ: ty — её тип ('int' или 'int[]'), берётся из e.ty (HW3).
        self.nt += 1
        self.types[f'%t{self.nt}'] = ty   # запоминаем тип новой временной
        return f'%t{self.nt}'

    def label(self):
        # ЗАЧЕМ: новая уникальная метка (L1, L2, ...). Метка ТОЛЬКО создаётся (имя), в код не ставится.
        self.nl += 1
        return f'L{self.nl}'

    def place(self, l):
        # ЗАЧЕМ: поставить ранее созданную метку l в текущую точку кода. Разделение label()/place()
        # нужно потому, что в ветвлении мы ссылаемся на метку (jump вперёд) ДО того, как её место известно.
        self.emit('label', aux=l)

    def expr(self, e, dst=None):
        """Возвращает операнд (int-константа или имя); при dst результат кладётся в dst."""
        # ЗАЧЕМ: превратить арифметическое выражение в последовательность трёхадресных инструкций.
        # АРГУМЕНТЫ: e — узел выражения; dst — куда положить результат (если None, заведём временную).
        # ПОЧЕМУ dst: для `r = a + b` эффективнее сразу писать `r = add a b`, а не `%t = add a b; r = mov %t`.
        # Так мы с самого начала создаём меньше временных и лишних mov.
        # ВОЗВРАЩАЕТ: операнд, в котором лежит результат — чтобы вызывающий мог подставить его в свою инструкцию.
        if isinstance(e, (Num, Var)):
            # Лист дерева: число или переменная. Инструкция не нужна, результат — сам операнд.
            v = e.value if isinstance(e, Num) else e.name
            if dst:                          # но если просили положить в dst — делаем mov
                self.emit('mov', dst, [v])
                return dst
            return v
        d = dst or self.tmp(e.ty)            # куда писать результат: в dst, а если не задан — в новую временную
        if isinstance(e, Neg):
            self.emit('neg', d, [self.expr(e.operand)])      # сначала вычисляем операнд (рекурсия), потом neg
        elif isinstance(e, BinOp):
            # Порядок важен: сначала вычислить ОБА операнда (они могут породить свои инструкции),
            # и только потом выписать саму операцию. Это обход «в глубину», снизу вверх.
            a, b = self.expr(e.left), self.expr(e.right)
            self.emit(ARITH[e.op], d, [a, b])                # '+' -> 'add', и т.д.
        elif isinstance(e, Index):
            self.emit('aload', d, [e.array, self.expr(e.index)])   # d = arr[i]
        elif e.name == 'length':
            self.emit('alen', d, [self.expr(e.args[0])])           # length(a) — встроенная, отдельная инструкция
        else:
            # Обычный вызов функции. dst у call — СПИСОК (функция может вернуть несколько значений).
            self.emit('call', [d], [self.expr(x) for x in e.args], e.name)
        return d

    def cond(self, e, lt, lf):
        """Условие -> ветвления с коротким замыканием."""
        # ЗАЧЕМ: условия if/while/assert превращаются НЕ в булево значение, а сразу в переходы:
        # «если истинно — иди на lt, иначе — на lf». Так выполняется короткое замыкание
        # (в `a and b` если a ложно, b даже не вычисляется), и нам не нужен булев тип в IR.
        # АРГУМЕНТЫ: e — узел условия; lt — метка «истина»; lf — метка «ложь».
        if isinstance(e, Compare):
            # База рекурсии: обычное сравнение -> одна инструкция br.
            a, b = self.expr(e.left), self.expr(e.right)
            self.emit('br', None, [a, b], (e.op, lt, lf))
        elif isinstance(e, Not):
            # not X: меняем местами «истину» и «ложь» — никакой инструкции не нужно.
            self.cond(e.operand, lf, lt)
        elif isinstance(e, BoolLit):
            # Константа true/false: сразу прыгаем куда надо.
            self.emit('jmp', aux=lt if e.value else lf)
        else:
            # And / Or / Implies — составные условия. Левую часть проверяем первой, а правую —
            # только если результат ещё не решён. Для этого нужна промежуточная метка mid —
            # «начало проверки правой части».
            mid = self.label()
            if isinstance(e, And):
                self.cond(e.left, mid, lf)        # a and b: если a истинно -> проверять b; ложно -> сразу «ложь»
            elif isinstance(e, Or):
                self.cond(e.left, lt, mid)        # a or b:  если a истинно -> сразу «истина»; ложно -> проверять b
            else:                                   # Implies: not L or R
                self.cond(e.left, mid, lt)        # a -> b:  если a ложно -> «истина» (вакуумно); истинно -> проверять b
            self.place(mid)                       # здесь начинается проверка правой части
            self.cond(e.right, lt, lf)

    def stmt(self, s):
        # ЗАЧЕМ: превратить оператор Funny в инструкции. Обходим дерево операторов рекурсивно.
        # АРГУМЕНТ: s — узел оператора (Block, Assign, TupleAssign, If, While, Assert, Assume).
        if isinstance(s, Block):
            for x in s.stmts:              # блок: просто по очереди выполнить все операторы
                self.stmt(x)
        elif isinstance(s, Assign):
            v = s.value
            if isinstance(v, ArrayUpdate):
                # `a[i] = e` парсер превратил в `a = update(a, i, e)`. Массивы у нас — значения,
                # поэтому aupd создаёт новый массив-копию с изменённым элементом и кладёт в a.
                i = self.expr(v.index)
                x = self.expr(v.value)
                self.emit('aupd', s.target, [v.array, i, x])
            else:
                self.expr(v, s.target)     # обычное присваивание: сразу вычисляем выражение в целевую переменную
        elif isinstance(s, TupleAssign):
            # `q, r = divide(a, b)`: вызов с несколькими результатами, dst — список целей.
            self.emit('call', list(s.targets), [self.expr(x) for x in s.call.args], s.call.name)
        elif isinstance(s, If):
            # Схема:      cond -> (lt | lf)
            #   lt:   then ...  [jmp le]
            #   lf:   else ...             (если есть else)
            #   le:   продолжение
            lt, le = self.label(), self.label()
            lf = self.label() if s.orelse else le     # без else «ложь» ведёт сразу на конец
            self.cond(s.cond, lt, lf)
            self.place(lt)
            self.stmt(s.then)
            if s.orelse:
                self.emit('jmp', aux=le)     # после then нужно ПЕРЕПРЫГНУТЬ else
                self.place(lf)
                self.stmt(s.orelse)
            self.place(le)
        elif isinstance(s, While):
            # Схема:  lc: cond -> (lb | le)
            #         lb: тело; jmp lc          (назад на проверку условия — это и есть цикл)
            #         le: продолжение
            lc, lb, le = self.label(), self.label(), self.label()
            self.place(lc)
            self.cond(s.cond, lb, le)
            self.place(lb)
            self.stmt(s.body)
            self.emit('jmp', aux=lc)
            self.place(le)
        elif isinstance(s, Assert) and runtime(s.cond):
            # assert P: если P истинно -> идём дальше (ok), иначе попадаем на trap (аварийный выход).
            ok, bad = self.label(), self.label()
            self.cond(s.cond, ok, bad)
            self.place(bad)
            self.emit('trap')
            self.place(ok)
        # Assume, нерантаймовые assert: только для верификации, в IR не попадают


def gen_function(d):
    # ЗАЧЕМ: собрать полный IR одной функции: метка входа, тело, возврат.
    # АРГУМЕНТ: d — узел Function. ВОЗВРАЩАЕТ: IRFunc с линейным кодом.
    g = Gen(d)
    g.place('entry')                                   # первая метка всегда entry — удобно для CFG (вход известен)
    g.stmt(d.body)                                     # транслируем тело
    g.emit('ret', None, [r.name for r in d.returns])   # в конце всегда ret со списком возвращаемых переменных
    mk = lambda vs: [(v.name, v.type) for v in vs]     # узлы VarDef -> пары (имя, тип)
    return IRFunc(d.name, mk(d.params), mk(d.returns), mk(d.locals), g.types, g.code)


# ---------------------------------------------------------------- CFG
# ШАГ 2. Линейный код неудобен для анализа: непонятно, откуда и куда идёт управление.
# CFG делит код на базовые блоки: куски без переходов внутри (входим только в начало,
# выходим только из конца). Рёбра графа — переходы между блоками.

@dataclass
class BB:
    # Базовый блок: метка + инструкции. Последняя инструкция ВСЕГДА терминатор (br/jmp/ret/trap).
    label: str
    instrs: list

    @property
    def succs(self):
        # ЗАЧЕМ: куда можно попасть из этого блока (его «потомки» в графе). Выводим из последней
        # инструкции — отдельно рёбра не храним, поэтому они не могут «рассинхронизироваться» с кодом.
        t = self.instrs[-1]
        if t.op == 'jmp':
            return [t.aux]                                    # одна цель
        # br -> две цели (aux = (cmp, Lt, Lf), берём Lt, Lf); dict.fromkeys убирает дубли, сохраняя порядок
        return list(dict.fromkeys(t.aux[1:])) if t.op == 'br' else []   # ret/trap — выходов нет


def build_cfg(code):
    """Линейный IR -> список базовых блоков (каждый завершён терминатором)."""
    # ЗАЧЕМ: разрезать линейный код на блоки. АРГУМЕНТ: code — список Instr от gen_function.
    # ПРАВИЛО: новый блок начинается с метки; блок заканчивается терминатором.
    blocks, n = [], 0                                                    # n — счётчик «мёртвых» блоков
    term = lambda b: bool(b.instrs) and b.instrs[-1].op in TERM          # «блок уже закончен терминатором?»
    for i in code:
        if i.op == 'label':
            if blocks and not term(blocks[-1]):
                # Предыдущий блок «проваливается» в метку без jmp (так бывает после gen).
                # Делаем переход явным — тогда у каждого блока есть терминатор, и рёбра однозначны.
                blocks[-1].instrs.append(Instr('jmp', aux=i.aux))
            blocks.append(BB(i.aux, []))                                 # метка открывает новый блок
        else:
            if term(blocks[-1]):
                # Инструкция после терминатора без метки: сюда никто не может прыгнуть (мёртвый код).
                # Заводим для неё отдельный блок, а reachable() потом его выбросит.
                n += 1
                blocks.append(BB(f'dead{n}', []))
            blocks[-1].instrs.append(i)                                  # обычная инструкция — в текущий блок
    return blocks


def reachable(blocks):
    # ЗАЧЕМ: оставить только блоки, до которых можно дойти от входа. После свёртки br -> jmp
    # некоторые ветки становятся недостижимыми (например, else при константном условии) — их удаляем.
    # АРГУМЕНТ: blocks — список BB (первый — вход). ВОЗВРАЩАЕТ: тот же список без недостижимых, порядок сохранён.
    by, seen, stack = {b.label: b for b in blocks}, set(), [blocks[0].label]   # by: метка->блок; обход в глубину от входа
    while stack:
        l = stack.pop()
        if l not in seen:
            seen.add(l)
            stack += by[l].succs       # добавляем всех потомков в очередь обхода
    return [b for b in blocks if b.label in seen]


def flatten(blocks):
    """Блоки -> линейный IR (jmp на следующий блок опускается, лишние метки не печатаются)."""
    # ЗАЧЕМ: вернуться от графа к линейному тексту — для вывода и для кодогенерации/интерпретатора.
    # ПОЧЕМУ можно опускать `jmp` на следующий блок: линейный код и так «проваливается» в соседний блок.
    body = []
    for k, b in enumerate(blocks):
        ins = list(b.instrs)           # копия, чтобы не портить блок
        if ins[-1].op == 'jmp' and k + 1 < len(blocks) and ins[-1].aux == blocks[k + 1].label:
            ins.pop()                  # прыжок ровно на следующий блок — лишний
        body.append(ins)
    # Множество меток, на которые реально кто-то ссылается (после удаления лишних jmp).
    ref = {l for ins in body for i in ins if i.op in ('jmp', 'br')
           for l in ([i.aux] if i.op == 'jmp' else i.aux[1:])}
    out = []
    for k, (b, ins) in enumerate(zip(blocks, body)):
        if k == 0 or b.label in ref:   # метку печатаем, только если она нужна (или это вход)
            out.append(Instr('label', aux=b.label))
        out += ins
    return out


# ---------------------------------------------------------------- живость, конфликты, раскраска
# ШАГ 4 (и подготовка к DCE). Переменная ЖИВА в точке, если её текущее значение ещё будет
# прочитано где-то дальше. Две временные «конфликтуют», если живы одновременно — тогда им
# нельзя дать один и тот же регистр/слот. Если не конфликтуют — можно один на двоих.

def liveness(blocks):
    # ЗАЧЕМ: для каждого блока вычислить live_in (что должно быть живо на входе) и live_out
    # (что живо на выходе). Нужно и для DCE (что можно удалять), и для графа конфликтов.
    # АРГУМЕНТ: blocks — список BB. ВОЗВРАЩАЕТ: (live_in, live_out) — словари метка -> множество имён.
    use, dfn = {}, {}   # use[B]: что блок читает ДО своего первого присваивания; dfn[B]: что блок записывает
    for b in blocks:
        u, d = set(), set()
        for i in b.instrs:
            u |= {x for x in i.uses() if x not in d}   # читаем x, но он ещё не записан в этом блоке -> значение пришло снаружи
            d |= set(i.defs())                         # порядок важен: сначала uses, потом defs (x = x + 1 читает старый x)
        use[b.label], dfn[b.label] = u, d
    lin = {b.label: set() for b in blocks}             # начальное приближение — пусто
    lout = {b.label: set() for b in blocks}
    changed = True
    while changed:                                     # итерируем до неподвижной точки (циклы в графе требуют повторов)
        changed = False
        for b in reversed(blocks):                     # обратный порядок сходится быстрее: живость течёт назад по коду
            # live_out блока = объединение live_in всех его потомков
            o = set().union(*[lin[s] for s in b.succs]) if b.succs else set()
            # live_in = то, что блок читает сам + то, что живо на выходе и им не перезаписывается
            n = use[b.label] | (o - dfn[b.label])
            if o != lout[b.label] or n != lin[b.label]:
                lout[b.label], lin[b.label], changed = o, n, True
    return lin, lout


def interference(blocks):
    """Граф конфликтов временных (%tN) по живости: {tmp: {соседи}}."""
    # ЗАЧЕМ: построить граф «кто с кем не может делить один слот». Вершины — временные %tN.
    # Ребро: при записи в d, какая-то другая временная x ещё жива (её значение понадобится позже).
    _, lout = liveness(blocks)                         # live_in не нужен, берём только live_out
    g = defaultdict(set)                               # g[t] — множество временных, конфликтующих с t
    for b in blocks:
        live = set(lout[b.label])                      # идём по блоку С КОНЦА, начиная с того, что живо на выходе
        for i in reversed(b.instrs):
            ds = i.defs()
            for d in ds:
                if is_tmp(d):
                    g[d]                               # обращение создаёт вершину, даже если рёбер не будет (изолированная временная)
                    for x in live | set(ds):           # живое после инструкции + остальные результаты ЭТОЙ ЖЕ инструкции
                        if x != d and is_tmp(x):       # только временные и не сам с собой
                            g[d].add(x)                # ребро в обе стороны (граф неориентированный)
                            g[x].add(d)
            live -= set(ds)                            # шаг назад по инструкции: то, что она пишет, до неё было «не живо»...
            live |= set(i.uses())                      # ...а то, что она читает, до неё должно быть живо
    return g


def color(g):
    """Жадная раскраска (порядок по убыванию степени)."""
    # ЗАЧЕМ: назначить каждой временной номер цвета (= слот/регистр) так, чтобы соседи по графу
    # получили разные цвета. Меньше цветов -> меньше слотов нужно кодогенератору.
    # ПОЧЕМУ «по убыванию степени»: самые «зажатые» вершины красим первыми, пока цветов ещё много —
    # это эвристика, которая обычно даёт меньше цветов, чем произвольный порядок. Оптимум не гарантирует (задача NP-трудная).
    # АРГУМЕНТ: g — граф от interference(). ВОЗВРАЩАЕТ: словарь временная -> номер цвета.
    col = {}
    for n in sorted(g, key=lambda n: (-len(g[n]), n)):             # сортировка: больше соседей — раньше; имя — для детерминизма
        used = {col[m] for m in g[n] if m in col}                  # цвета уже покрашенных соседей
        col[n] = next(c for c in count() if c not in used)         # наименьший цвет 0,1,2,..., которого нет у соседей
    return col


def analyze(blocks):
    # ЗАЧЕМ: посчитать метрики для задания: число инструкций, блоков, временных, цветов.
    # Вызывается дважды: для блоков «до» и «после» оптимизаций — так видно эффект.
    g = interference(blocks)
    col = color(g)
    # Временные, реально встречающиеся в коде (после DCE часть исчезает).
    temps = {n for b in blocks for i in b.instrs for n in i.defs() + i.uses() if is_tmp(n)}
    # instrs считаем по flatten (без меток и без опущенных jmp) — как выглядит итоговый код.
    m = {'instrs': sum(i.op != 'label' for i in flatten(blocks)), 'blocks': len(blocks),
         'temps': len(temps), 'colors': len(set(col.values()))}
    return m, g, col


# ---------------------------------------------------------------- локальные оптимизации
# ШАГ 3. «Локальные» = внутри ОДНОГО базового блока. Внутри блока нет ветвлений, поэтому можно
# просто идти сверху вниз и запоминать факты; через границы блоков факты не переносим
# (там пришлось бы учитывать несколько путей — это уже глобальный анализ).

def p_const(b):
    """Распространение констант и копий + свёртка; br с константами -> jmp."""
    # ЗАЧЕМ: если мы знаем, что x = 5, то подставляем 5 вместо x; если в инструкции остались
    # только константы — считаем её прямо при компиляции (свёртка). АРГУМЕНТ: b — один блок (меняется на месте).
    # env — «что известно сейчас»: имя -> константа ИЛИ другое имя (копия: после `y = mov x` можно читать x вместо y).
    env = {}
    for i in b.instrs:
        if i.op != 'ret':
            # Подставляем известные значения в аргументы. Для ret НЕ подставляем: он обязан
            # возвращать именно имена переменных, а не числа.
            i.args = [env.get(a, a) if isinstance(a, str) else a for a in i.args]
        if i.op in ALU and all(isinstance(a, int) for a in i.args) \
                and not (i.op == 'div' and i.args[1] == 0):
            # Свёртка: все аргументы — числа. Заменяем инструкцию на mov с посчитанным (с учётом 32 бит) значением.
            # Деление на 0 не трогаем: оно должно упасть в рантайме, а не при компиляции.
            i.op, i.args = 'mov', [wrap(ALU[i.op](*i.args))]
        elif i.op == 'br' and all(isinstance(a, int) for a in i.args):
            # Условие известно заранее -> условный переход становится безусловным. Это убирает ветку целиком.
            c, t, f = i.aux
            i.op, i.aux, i.args = 'jmp', (t if CMPF[c](*i.args) else f), []
        # Обновляем знания: инструкция записала в defs, значит прежние факты о них устарели.
        for d in i.defs():
            env.pop(d, None)                                       # факт «d = что-то» больше неверен
            for k in [k for k, v in env.items() if v == d]:        # и факты вида «k — копия d» тоже (d изменился)
                del env[k]
        if i.op == 'mov' and i.dst != i.args[0]:
            env[i.dst] = i.args[0]                                 # новый факт: dst теперь равен константе/копии источника


def p_alg(b):
    """Алгебраические упрощения: x+0, x*1, x*0, x-x, x/1, x*2 -> x+x."""
    # ЗАЧЕМ: упростить операции, результат которых известен по алгебре, даже если сами значения
    # неизвестны (x + 0 всегда x). Это ловит то, что свёртка констант не видит. АРГУМЕНТ: b — блок.
    for i in b.instrs:
        if i.op not in ALU or i.op == 'neg':
            continue                         # нас интересуют только бинарные арифметические инструкции
        (x, y), k, r = i.args, i.op, None    # x, y — операнды; k — операция; r — чем заменить (None = не нашли)
        if k in ('add', 'sub') and y == 0:
            r = x                            # x + 0 = x,  x - 0 = x
        elif k == 'add' and x == 0:
            r = y                            # 0 + y = y
        elif k in ('mul', 'div') and y == 1:
            r = x                            # x * 1 = x,  x / 1 = x
        elif k == 'mul' and x == 1:
            r = y                            # 1 * y = y
        elif k == 'mul' and 0 in (x, y):
            r = 0                            # x * 0 = 0  (деление не трогаем: 0/x при x=0 упало бы)
        elif k == 'sub' and x == y:
            r = 0                            # x - x = 0
        if r is not None:
            i.op, i.args = 'mov', [r]        # заменяем на простое копирование (r может быть 0 — поэтому проверка «is not None», а не «if r»)
        elif k == 'mul' and 2 in (x, y):
            o = y if x == 2 else x           # x * 2 -> x + x: сложение дешевле умножения (strength reduction)
            i.op, i.args = 'add', [o, o]


def p_cse(b):
    """Устранение общих подвыражений в пределах блока (add/mul коммутативны)."""
    # ЗАЧЕМ: если `x + y` уже посчитано и x, y с тех пор не менялись, второй раз считать не нужно —
    # берём готовый результат. АРГУМЕНТ: b — блок.
    # av («available») — доступные выражения: ключ (операция, аргументы) -> в какой переменной лежит результат.
    av = {}
    for i in b.instrs:
        key = None
        if i.op in CSE_OPS:
            # Для add/mul сортируем аргументы: x+y и y+x — одно и то же выражение (коммутативность).
            a = sorted(i.args, key=str) if i.op in ('add', 'mul') else i.args   # key=str, т.к. в списке могут быть и числа, и строки
            key = (i.op, tuple(a))
            if key in av:
                # Такое выражение уже считали -> вместо вычисления просто копируем готовый результат.
                i.op, i.args, key = 'mov', [av[key]], None
        for d in i.defs():
            # Инструкция записала в d: удаляем из av всё, что УСТАРЕЛО — выражения, где d был аргументом,
            # и выражения, результат которых лежал в d (он теперь затёрт).
            av = {k: v for k, v in av.items() if v != d and d not in k[1]}
        if key and i.dst not in key[1]:
            # Запоминаем новое выражение. Условие `dst not in key[1]`: для `x = x + 1` результат
            # затирает аргумент, и выражение «x+1» сразу же перестаёт быть доступным.
            av[key] = i.dst


def p_dce(blocks):
    """Удаление мёртвых присваиваний (обратный проход по блоку от live-out)."""
    # ЗАЧЕМ: выбросить инструкции, результат которых никто не читает (`dead = x*y+7`).
    # АРГУМЕНТ: blocks — ВСЕ блоки, а не один: чтобы знать, что нужно после блока, нужна живость по всему графу.
    # (Поэтому DCE «локальная» лишь в том смысле, что чистит внутри блока, но опирается на глобальную живость.)
    _, lout = liveness(blocks)
    for b in blocks:
        live, keep = set(lout[b.label]), []                      # live — что нужно «после» текущей инструкции; keep — что оставляем
        for i in reversed(b.instrs):                             # идём с конца: так знаем, что нужно после
            ds = i.defs()
            # «Чистая» = можно удалить без последствий. div чистый только при ненулевой константе-делителе
            # (иначе он мог бы упасть, и удалять его нельзя).
            pure = i.op in DCE_PURE or (i.op == 'div' and isinstance(i.args[1], int) and i.args[1] != 0)
            if (pure and not set(ds) & live) or (i.op == 'mov' and i.dst == i.args[0]):
                continue                                         # результат никому не нужен (или это `x = x`) -> выбрасываем
            keep.append(i)
            live = (live - set(ds)) | set(i.uses())              # обновляем live: результат перестал быть нужен «выше», аргументы — стали нужны
        b.instrs = keep[::-1]                                    # keep собран в обратном порядке — разворачиваем


def optimize(blocks, passes):
    # ЗАЧЕМ: запустить выбранные оптимизации и повторять, пока что-то меняется. Повтор нужен,
    # потому что оптимизации помогают друг другу: свёртка создаёт mov, copy-propagation подставляет,
    # DCE удаляет лишнее, и ещё раз.
    # АРГУМЕНТЫ: blocks — список BB (изменяется!, поэтому снаружи передаём deepcopy);
    #            passes — множество включённых оптимизаций из {'const','alg','cse','dce'} (флаг --opt).
    for _ in range(30):                                          # верхняя граница на случай, если что-то не сходится
        # Снимок текста всех блоков ДО прохода — чтобы понять, изменилось ли что-нибудь.
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
        blocks = reachable(blocks)                               # после br->jmp часть блоков стала недостижимой
        if snap == [(b.label, [str(i) for i in b.instrs]) for b in blocks]:
            break                                                # ничего не изменилось -> неподвижная точка, дальше нечего делать
    return blocks


# ---------------------------------------------------------------- интерпретатор IR (для проверки)
# Нужен ТОЛЬКО для тестов. Идея проверки корректности: исполнить IR до оптимизаций и после
# на одних и тех же входах — результаты обязаны совпасть.

class Trap(Exception):
    # Исключительная ситуация в исполняемой программе: деление на 0, индекс вне массива, провал assert.
    pass


def run(prog, name, args, fuel=500000):
    """Исполняет линейный IR функции name из prog ({имя: IRFunc}); возвращает кортеж результатов."""
    # АРГУМЕНТЫ: prog — все функции программы (нужны для call); name — какую запускаем;
    # args — значения параметров; fuel — лимит шагов, защита от бесконечного цикла в тесте.
    st = [fuel]   # счётчик шагов в списке, чтобы его могла менять вложенная функция call (общий на все вызовы)

    def call(name, args):
        f = prog[name]
        # Начальные значения ВСЕХ имён функции (в т.ч. временных): 0 для int, [] для массива.
        env = {n: ([] if t == 'int[]' else 0) for n, t in f.types.items()}
        env.update(zip((p for p, _ in f.params), args))              # поверх кладём реальные параметры
        pos = {i.aux: k for k, i in enumerate(f.code) if i.op == 'label'}   # метка -> номер строки (для переходов)
        val = lambda a: env[a] if isinstance(a, str) else a          # значение операнда: имя -> из env, число -> само
        pc = 0                                                       # program counter — номер текущей инструкции
        while True:
            i = f.code[pc]
            pc += 1
            o = i.op
            if o == 'label':
                continue                                             # метка — не инструкция, шаг не считаем
            st[0] -= 1
            if st[0] < 0:
                raise Trap('fuel')                                   # слишком долго -> считаем зависанием
            if o == 'mov':
                env[i.dst] = val(i.args[0])
            elif o in ALU:
                x = [val(a) for a in i.args]
                if o == 'div' and x[1] == 0:
                    raise Trap('div0')
                env[i.dst] = wrap(ALU[o](*x))                        # те же ALU и wrap, что в оптимизаторе -> одинаковая арифметика
            elif o == 'alen':
                env[i.dst] = len(val(i.args[0]))
            elif o in ('aload', 'aupd'):
                a, k = val(i.args[0]), val(i.args[1])
                if not 0 <= k < len(a):
                    raise Trap('index')                              # выход за границы массива
                if o == 'aload':
                    env[i.dst] = a[k]
                else:
                    env[i.dst] = a[:k] + [val(i.args[2])] + a[k + 1:]   # копия массива с заменой k-го элемента (старый не портим)
            elif o == 'call':
                for d, v in zip(i.dst, call(i.aux, [val(a) for a in i.args])):   # рекурсивно запускаем функцию, раскладываем результаты по dst
                    env[d] = v
            elif o == 'br':
                c, t, fl = i.aux
                pc = pos[t if CMPF[c](val(i.args[0]), val(i.args[1])) else fl]   # выбираем ветку и «прыгаем» на её метку
            elif o == 'jmp':
                pc = pos[i.aux]
            elif o == 'ret':
                return tuple(val(a) for a in i.args)                 # вернуть значения returns-переменных
            else:
                raise Trap('assert')                                 # остался только trap

    return call(name, list(args))


# ---------------------------------------------------------------- конвейер и вывод

def compile_text(text, passes):
    """Funny-код -> (список результатов по функциям, ошибки HW2/HW3)."""
    # ЗАЧЕМ: главный вход — склеивает ВСЕ шаги от исходного текста до результата. Его же использует
    # кодогенерация и тесты. АРГУМЕНТЫ: text — исходник Funny; passes — набор оптимизаций.
    # ВОЗВРАЩАЕТ: (результаты, []) или (None, ошибки).
    ast, errors = check(text)                  # HW2+HW3: разбор и проверка типов
    if errors:
        return None, errors                    # с ошибками IR строить бессмысленно
    res = []
    for d in ast.decls:
        if not isinstance(d, Function):
            continue                          # формулы — только для спецификаций
        f = gen_function(d)                    # ШАГ 1: AST -> линейный IR
        b0 = build_cfg(f.code)                 # ШАГ 2: блоки «до»
        b1 = optimize(copy.deepcopy(b0), passes)   # ШАГ 3: оптимизируем КОПИЮ, чтобы «до» осталось нетронутым
        m0, g0, c0 = analyze(b0)               # ШАГ 4: метрики и раскраска «до»...
        m1, g1, c1 = analyze(b1)               # ...и «после»
        res.append({'name': d.name, 'ir0': replace(f, code=flatten(b0)),   # IRFunc «до» (replace — та же функция, но код другой)
                    'ir1': replace(f, code=flatten(b1)), 'bb0': b0, 'bb1': b1,
                    'm0': m0, 'm1': m1, 'g1': g1, 'col1': c1})
    return res, []


def program(res, key):
    # ЗАЧЕМ: собрать из результатов словарь {имя функции: IRFunc}, как требует run().
    # АРГУМЕНТЫ: res — результат compile_text; key — 'ir0' (до) или 'ir1' (после).
    return {r['name']: r[key] for r in res}


def text_report(res, passes):
    # ЗАЧЕМ: человекочитаемый отчёт для .ir.txt и stdout: IR до, IR после, раскраска, метрики.
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
    # ЗАЧЕМ: то же самое в JSON — машинный формат, который читает кодогенератор (передача IR в проект).
    # asdict превращает Instr в {op, dst, args, aux}.
    fn = {}
    for r in res:
        f = r['ir1']
        fn[r['name']] = {
            'params': f.params, 'returns': f.returns, 'locals': f.locals, 'types': f.types,
            'before': {'code': [asdict(i) for i in r['ir0'].code], 'metrics': r['m0']},
            'after': {'code': [asdict(i) for i in f.code], 'metrics': r['m1'],
                      'coloring': r['col1'],
                      'cfg': {b.label: b.succs for b in r['bb1']}}}   # рёбра CFG: метка блока -> метки потомков
    return json.dumps({'passes': sorted(passes), 'functions': fn}, ensure_ascii=False, indent=2)


def cfg_dot(name, blocks):
    # ЗАЧЕМ: записать CFG в формате Graphviz DOT, чтобы его можно было нарисовать (`dot -Tpng`).
    # Узел = блок с его инструкциями в подписи (\l — перенос строки с выравниванием влево), ребро = переход.
    L = [f'digraph "{name}" {{', '  node [shape=box, fontname=monospace];']
    for b in blocks:
        body = ''.join(str(i).strip() + '\\l' for i in b.instrs)
        L.append(f'  "{b.label}" [label="{b.label}:\\l{body}"];')
        L += [f'  "{b.label}" -> "{s}";' for s in b.succs]
    return '\n'.join(L + ['}']) + '\n'


def conflicts_dot(name, g, col):
    # ЗАЧЕМ: записать граф конфликтов в DOT. Вершина — временная, подписана номером цвета и
    # залита соответствующим цветом; ребро — конфликт. `a < b` печатает каждое ребро один раз.
    L = [f'graph "{name}" {{', '  node [style=filled, colorscheme=set312];']
    for n in sorted(g):
        L.append(f'  "{n}" [label="{n}\\nc{col[n]}", fillcolor={col[n] % 12 + 1}];')
    L += [f'  "{a}" -- "{b}";' for a in sorted(g) for b in sorted(g[a]) if a < b]
    return '\n'.join(L + ['}']) + '\n'


def main(argv=None):
    # ЗАЧЕМ: командная строка. argv=None -> берётся sys.argv; в тестах передаём список вручную.
    ap = argparse.ArgumentParser(description='Funny -> IR, CFG, оптимизации, раскраска')
    ap.add_argument('file')
    ap.add_argument('--opt', default='const,alg,dce',
                    help='список через запятую из const,alg,cse,dce; all; none (по умолчанию const,alg,dce)')
    ap.add_argument('--dot', action='store_true', help='писать CFG и граф конфликтов в DOT')
    ap.add_argument('--out', help='каталог вывода (по умолчанию рядом с исходником)')
    a = ap.parse_args(argv)
    # Строку --opt превращаем в множество: all — все, none — пусто, иначе делим по запятым.
    passes = PASSES if a.opt == 'all' else set() if a.opt == 'none' else set(a.opt.split(','))
    if passes - PASSES:
        ap.error(f'неизвестные оптимизации: {sorted(passes - PASSES)}')   # опечатка в имени оптимизации -> понятная ошибка
    with open(a.file, encoding='utf-8') as f:
        res, errors = compile_text(f.read(), passes)
    if errors:
        print('\n'.join(f'{a.file}:{d}' for d in errors), file=sys.stderr)   # ошибки в формате файл:строка:столбец
        return 1
    out_dir = a.out or os.path.dirname(os.path.abspath(a.file))
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, os.path.splitext(os.path.basename(a.file))[0])   # путь без расширения: общий префикс всех выходных файлов
    txt = text_report(res, passes)
    for ext, data in (('.ir.txt', txt), ('.ir.json', json_report(res, passes) + '\n')):
        with open(base + ext, 'w', encoding='utf-8') as f:
            f.write(data)
    if a.dot:
        for r in res:                         # по два файла на каждую функцию
            with open(f'{base}.{r["name"]}.cfg.dot', 'w', encoding='utf-8') as f:
                f.write(cfg_dot(r['name'], r['bb1']))
            with open(f'{base}.{r["name"]}.conflicts.dot', 'w', encoding='utf-8') as f:
                f.write(conflicts_dot(r['name'], r['g1'], r['col1']))
    print(txt)
    return 0


if __name__ == '__main__':
    sys.exit(main())
