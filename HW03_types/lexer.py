#!/usr/bin/env python3
"""
HW1: Лексер: — регулярное выражение -> НКА -> ДКА -> минимизация.

  1. HKA
  2. НКА -> ДКА.
  3. Минимизация ДКА разбиением на классы эквивалентности .
  4. Экспорт таблицы переходов в консоль и в JSON.
  5. tokenize()

Тесты вынесены в test_lexer.py. 

Запуск:  python3 lexer.py
"""

import json
import string
from collections import defaultdict, deque

EPS = None 



class NFA:
    def __init__(self):
        self.n_states = 0
        self.trans = defaultdict(lambda: defaultdict(set))  # state -> sym(или EPS) -> {states}
        self.accept = {}  # state -> (token_name, priority, skip)

    def new_state(self):
        s = self.n_states
        self.n_states += 1
        return s

    def add_edge(self, s, sym, t):
        self.trans[s][sym].add(t)


def frag_chars(nfa, chars):
    "Один переход по любому символу из набора chars"
    s, e = nfa.new_state(), nfa.new_state()
    for c in chars:
        nfa.add_edge(s, c, e)
    return s, e


def frag_str(nfa, literal):
    "Цепочка переходов для точной строки."
    start = cur = nfa.new_state()
    for ch in literal:
        nxt = nfa.new_state()
        nfa.add_edge(cur, ch, nxt)
        cur = nxt
    return start, cur


def frag_concat(nfa, frags):
    "Склейка"
    for (_, e1), (s2, _) in zip(frags, frags[1:]):
        nfa.add_edge(e1, EPS, s2)
    return frags[0][0], frags[-1][1]


def frag_union(nfa, frags):
    s, e = nfa.new_state(), nfa.new_state()
    for fs, fe in frags:
        nfa.add_edge(s, EPS, fs)
        nfa.add_edge(fe, EPS, e)
    return s, e


def frag_star(nfa, frag):
    fs, fe = frag
    s, e = nfa.new_state(), nfa.new_state()
    nfa.add_edge(s, EPS, fs)
    nfa.add_edge(s, EPS, e)
    nfa.add_edge(fe, EPS, fs)
    nfa.add_edge(fe, EPS, e)
    return s, e


def frag_plus(nfa, frag):
    fs, fe = frag
    e = nfa.new_state()
    nfa.add_edge(fe, EPS, fs)
    nfa.add_edge(fe, EPS, e)
    return fs, e




DIGITS = string.digits
NONZERO = '123456789'
LETTERS = string.ascii_letters
WS_CHARS = ' \t\r\n'

PRINTABLE_NO_NL = ''.join(chr(c) for c in range(0x20, 0x7F))

KEYWORDS = ['function', 'returns', 'while', 'if', 'else',
            'assert', 'assume', 'invariant', 'length',
            # HW2: аннотации, типы, логика
            'requires', 'ensures', 'uses', 'int', 'true', 'false',
            'not', 'and', 'or', 'forall', 'exists']

OPERATORS = [
    ('==', 'EQ'), ('!=', 'NEQ'), ('<=', 'LE'), ('>=', 'GE'),
    ('<', 'LT'), ('>', 'GT'), ('=', 'ASSIGN'),
    ('(', 'LPAREN'), (')', 'RPAREN'), ('[', 'LBRACKET'), (']', 'RBRACKET'),
    ('{', 'LBRACE'), ('}', 'RBRACE'), (',', 'COMMA'), (';', 'SEMI'),
    ('+', 'PLUS'), ('-', 'MINUS'), ('*', 'STAR'), ('/', 'SLASH'), (':', 'COLON'),
    # HW2: '=>' (тело формулы), '->' (импликация), '|' (квантор)
    ('=>', 'FAT_ARROW'), ('->', 'ARROW'), ('|', 'PIPE'),
]


def build_master_nfa():
    nfa = NFA()
    start = nfa.new_state()
    rules = []  # только для счётчика приоритета

    def add_rule(name, frag, skip=False):
        priority = len(rules)
        rules.append(name)
        nfa.add_edge(start, EPS, frag[0])
        nfa.accept[frag[1]] = (name, priority, skip)

    # 1) ключевые слова
    for kw in KEYWORDS:
        add_rule('KW_' + kw.upper(), frag_str(nfa, kw))

    # 2) IDENT: letter (letter|digit)*
    ident = frag_concat(nfa, [frag_chars(nfa, LETTERS),
                               frag_star(nfa, frag_chars(nfa, LETTERS + DIGITS))])
    add_rule('IDENT', ident)

    # 3) INT: '0' | [1-9][0-9]*  
    int_frag = frag_union(nfa, [
        frag_str(nfa, '0'),
        frag_concat(nfa, [frag_chars(nfa, NONZERO), frag_star(nfa, frag_chars(nfa, DIGITS))]),
    ])
    add_rule('INT', int_frag)

    # 4) WS: (space|tab|CR|LF)+ — skip
    add_rule('WS', frag_plus(nfa, frag_chars(nfa, WS_CHARS)), skip=True)

    # 5) COMMENT: '//' (любой печатный ASCII кроме CR/LF)* — skip
    comment = frag_concat(nfa, [frag_str(nfa, '//'),
                                 frag_star(nfa, frag_chars(nfa, PRINTABLE_NO_NL))])
    add_rule('COMMENT', comment, skip=True)

    # 6) операторы / разделители
    for lit, name in OPERATORS:
        add_rule(name, frag_str(nfa, lit))

    return nfa, start


def eps_closure(nfa, states):
    stack = list(states)
    result = set(states)
    while stack:
        s = stack.pop()
        for t in nfa.trans[s].get(EPS, ()):
            if t not in result:
                result.add(t)
                stack.append(t)
    return frozenset(result)


def alphabet_of(nfa):
    chars = set()
    for edges in nfa.trans.values():
        for sym in edges:
            if sym is not EPS:
                chars.add(sym)
    return chars


def nfa_to_dfa(nfa, start):
    alphabet = alphabet_of(nfa)
    start_set = eps_closure(nfa, {start})
    dfa_trans, dfa_accept = {}, {}
    dfa_states = {start_set: 0}
    queue = deque([start_set])
    while queue:
        cur = queue.popleft()
        cur_id = dfa_states[cur]
        accs = [nfa.accept[s] for s in cur if s in nfa.accept]
        if accs:
            name, _, skip = min(accs, key=lambda a: a[1])  # приоритет = меньше число
            dfa_accept[cur_id] = (name, skip)
        for ch in alphabet:
            moved = set()
            for s in cur:
                moved |= nfa.trans[s].get(ch, set())
            if not moved:
                continue
            nxt = eps_closure(nfa, moved)
            if nxt not in dfa_states:
                dfa_states[nxt] = len(dfa_states)
                queue.append(nxt)
            dfa_trans.setdefault(cur_id, {})[ch] = dfa_states[nxt]
    return dfa_trans, dfa_accept, dfa_states[start_set], len(dfa_states), alphabet



def minimize_dfa(dfa_trans, dfa_accept, start, n_states, alphabet):
    def label(s):
        return dfa_accept.get(s, (None, None))

    groups = defaultdict(list)
    for s in range(n_states):
        groups[label(s)].append(s)
    state_group = {s: gid for gid, states in enumerate(groups.values()) for s in states}

    changed = True
    while changed:
        buckets = defaultdict(list)
        for s in range(n_states):
            sig = tuple(state_group.get(dfa_trans.get(s, {}).get(ch), -1) for ch in alphabet)
            buckets[(state_group[s], sig)].append(s)
        new_state_group = {s: gid for gid, (_, states) in enumerate(buckets.items()) for s in states}
        changed = new_state_group != state_group
        state_group = new_state_group

    min_trans, min_accept = {}, {}
    for s in range(n_states):
        g = state_group[s]
        if s in dfa_accept:
            min_accept[g] = dfa_accept[s]
        for ch in alphabet:
            t = dfa_trans.get(s, {}).get(ch)
            if t is not None:
                min_trans.setdefault(g, {})[ch] = state_group[t]
    return min_trans, min_accept, state_group[start], len(set(state_group.values()))



class Token:
    __slots__ = ('type', 'lexeme', 'pos')

    def __init__(self, type_, lexeme, pos):
        self.type, self.lexeme, self.pos = type_, lexeme, pos

    def __repr__(self):
        return f'{self.type}({self.lexeme!r}@{self.pos})'


def tokenize(text, trans, accept, start):
    pos, n = 0, len(text)
    tokens = []
    while pos < n:
        state, i = start, pos
        last_accept = None  # (конец_лексемы, имя_токена, skip)
        while i < n and state in trans and text[i] in trans[state]:
            state = trans[state][text[i]]
            i += 1
            if state in accept:
                name, skip = accept[state]
                last_accept = (i, name, skip)
        if last_accept is None:
            tokens.append(Token('ERROR', text[pos], pos))  # ловушка: символ вне алфавита токенов
            pos += 1
        else:
            end, name, skip = last_accept
            if not skip:
                tokens.append(Token(name, text[pos:end], pos))
            pos = end
    return tokens


def export_table(trans, accept, start, n_states, alphabet, json_path='dfa_table.json'):

    accept_out = {
        str(s): {'token': name, 'skip': skip}
        for s, (name, skip) in accept.items()
    }
    trans_out = {
        str(s): {ch: trans[s][ch] for ch in trans[s]}
        for s in trans
    }
    data = {
        'start': start,
        'n_states': n_states,
        'alphabet': sorted(alphabet),
        'accept': accept_out,
        'transitions': trans_out,
    }

    print(f'Start state: {start}, states: {n_states}')
    text = json.dumps(data, ensure_ascii=False, indent=2)
    print(text)

    with open(json_path, 'w', encoding='utf-8') as f:
        f.write(text)
    print(f'-> сохранено в {json_path}')


def build_pipeline():
    nfa, start_nfa = build_master_nfa()
    dfa_trans, dfa_accept, dfa_start, n_dfa, alphabet = nfa_to_dfa(nfa, start_nfa)
    min_trans, min_accept, min_start, n_min = minimize_dfa(
        dfa_trans, dfa_accept, dfa_start, n_dfa, alphabet)
    stats = {'nfa_states': nfa.n_states, 'dfa_states': n_dfa, 'min_dfa_states': n_min}
    return min_trans, min_accept, min_start, alphabet, stats



def main():
    trans, accept, start, alphabet, stats = build_pipeline()

    print(f"НКА: {stats['nfa_states']} состояний")
    print(f"ДКА до минимизации: {stats['dfa_states']} состояний")
    print(f"ДКА после минимизации: {stats['min_dfa_states']} состояний\n")

    export_table(trans, accept, start, stats['min_dfa_states'], alphabet)

    print(f"НКА: {stats['nfa_states']} состояний")
    print(f"ДКА до минимизации: {stats['dfa_states']} состояний")
    print(f"ДКА после минимизации: {stats['min_dfa_states']} состояний\n")


if __name__ == '__main__':
    main()