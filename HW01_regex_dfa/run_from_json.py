#!/usr/bin/env python3
"""
Запуск:
    python run_from_json.py path/to/dfa_table.json path/to/source.funny
"""

import json
import sys


class Token:
    __slots__ = ('type', 'lexeme', 'pos')

    def __init__(self, type_, lexeme, pos):
        self.type, self.lexeme, self.pos = type_, lexeme, pos

    def __repr__(self):
        return f'{self.type}({self.lexeme!r}@{self.pos})'


def load_dfa(json_path):
    with open(json_path, encoding='utf-8') as f:
        data = json.load(f)

    start = data['start']
    trans = {int(s): edges for s, edges in data['transitions'].items()}
    accept = {int(s): (v['token'], v['skip']) for s, v in data['accept'].items()}
    return trans, accept, start


def tokenize(text, trans, accept, start):
    pos, n = 0, len(text)
    tokens = []
    while pos < n:
        state, i = start, pos
        last_accept = None 
        while i < n and state in trans and text[i] in trans[state]:
            state = trans[state][text[i]]
            i += 1
            if state in accept:
                name, skip = accept[state]
                last_accept = (i, name, skip)
        if last_accept is None:
            tokens.append(Token('ERROR', text[pos], pos))
            pos += 1
        else:
            end, name, skip = last_accept
            if not skip:
                tokens.append(Token(name, text[pos:end], pos))
            pos = end
    return tokens


def main():
    if len(sys.argv) != 3:
        print('Использование: python run_from_json.py dfa_table.json source.funny')
        sys.exit(1)

    dfa_path, source_path = sys.argv[1], sys.argv[2]
    trans, accept, start = load_dfa(dfa_path)

    with open(source_path, encoding='utf-8') as f:
        source = f.read()

    for tok in tokenize(source, trans, accept, start):
        print(tok)


if __name__ == '__main__':
    main()