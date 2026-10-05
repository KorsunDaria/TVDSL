#!/usr/bin/env python3
"""
HW2: Парсер Funny — LL(1), рекурсивный спуск, режим паники, AST.

Лексер — lexer.py из HW1 (токены несут смещение pos; здесь оно переводится
в строку:столбец).

Запуск:
    python3 funny_parser.py program.funny [--pos]

stdout — AST в JSON (при отсутствии ошибок), stderr — диагностика.
Код возврата: 0 — ошибок нет, 1 — есть синтаксические ошибки.
Грамматика — grammar.txt, формат AST — README.md.
"""

import bisect
import json
import sys
from dataclasses import dataclass, fields
from typing import Optional

from lexer import Token, build_pipeline, tokenize

class Node:
    pos = None 


@dataclass
class Module(Node):
    decls: list


@dataclass
class VarDef(Node):
    name: str
    type: str 


@dataclass
class Function(Node):
    name: str
    params: list
    requires: Optional[Node]
    returns: list
    ensures: Optional[Node]
    locals: list
    body: Node


@dataclass
class Formula(Node):
    name: str
    params: list
    body: Node


# операторы
@dataclass
class Block(Node):
    stmts: list


@dataclass
class Assign(Node):
    target: str
    value: Node


@dataclass
class TupleAssign(Node):
    targets: list
    call: Node


@dataclass
class If(Node):
    cond: Node
    then: Node
    orelse: Optional[Node]


@dataclass
class While(Node):
    cond: Node
    invariant: Optional[Node]
    body: Node


@dataclass
class Assert(Node):
    cond: Node


@dataclass
class Assume(Node):
    cond: Node


# арифметические выражения
@dataclass
class Num(Node):
    value: int


@dataclass
class Var(Node):
    name: str


@dataclass
class Neg(Node):
    operand: Node


@dataclass
class BinOp(Node):
    op: str
    left: Node
    right: Node


@dataclass
class Call(Node):
    name: str
    args: list


@dataclass
class Index(Node):
    array: str
    index: Node


@dataclass
class ArrayUpdate(Node):
    """Сахар: `a[i] = e;` -> Assign(a, ArrayUpdate(a, i, e)) — копия a с a[i] = e."""
    array: str
    index: Node
    value: Node


@dataclass
class BoolLit(Node):
    value: bool


@dataclass
class Compare(Node):
    op: str
    left: Node
    right: Node


@dataclass
class Not(Node):
    operand: Node


@dataclass
class And(Node):
    left: Node
    right: Node


@dataclass
class Or(Node):
    left: Node
    right: Node


@dataclass
class Implies(Node):
    left: Node
    right: Node


@dataclass
class Quantifier(Node):
    kind: str  # 'forall' | 'exists'
    var: VarDef
    body: Node


@dataclass
class FormulaRef(Node):
    name: str
    args: list


EXPR_NODES = (Num, Var, Neg, BinOp, Call, Index)


def is_expr(node):
    return isinstance(node, EXPR_NODES)


def to_dict(node, with_pos=False):
    """AST -> словарь/списки/числа/строки (сериализуется в JSON)."""
    if isinstance(node, Node):
        d = {'node': type(node).__name__}
        for f in fields(node):
            d[f.name] = to_dict(getattr(node, f.name), with_pos)
        if with_pos and node.pos is not None:
            d['pos'] = f'{node.pos[0]}:{node.pos[1]}'
        return d
    if isinstance(node, list):
        return [to_dict(x, with_pos) for x in node]
    return node



@dataclass
class Diagnostic:
    line: int
    col: int
    message: str

    def __str__(self):
        return f'{self.line}:{self.col}: ошибка: {self.message}'


class SyntaxErr(Exception):
    def __init__(self, diag):
        super().__init__(str(diag))
        self.diag = diag


@dataclass
class ParseResult:
    ast: Module
    errors: list

    @property
    def ok(self):
        return not self.errors



CMP = {'EQ': '==', 'NEQ': '!=', 'LE': '<=', 'GE': '>=', 'LT': '<', 'GT': '>'}
ARITH = {'PLUS': '+', 'MINUS': '-', 'STAR': '*', 'SLASH': '/'}
PREC = {'+': 1, '-': 1, '*': 2, '/': 2}

_lexer = None


def _get_lexer():
    global _lexer
    if _lexer is None:
        trans, accept, start, _, _ = build_pipeline()
        _lexer = (trans, accept, start)
    return _lexer


class Parser:
    def __init__(self, text):
        self.text = text
        trans, accept, start = _get_lexer()
        self.toks = tokenize(text, trans, accept, start)
        self.toks.append(Token('EOF', '', len(text)))
        self.i = 0
        self.errors = []
        self.line_starts = [0] + [k + 1 for k, c in enumerate(text) if c == '\n']

   
    @property
    def tok(self):
        return self.toks[self.i]

    def peek(self):
        return self.toks[min(self.i + 1, len(self.toks) - 1)]

    def at(self, *types):
        return self.tok.type in types

    def advance(self):
        t = self.tok
        if t.type != 'EOF':
            self.i += 1
        return t

    def line_col(self, pos):
        line = bisect.bisect_right(self.line_starts, pos)
        return line, pos - self.line_starts[line - 1] + 1

    def mk(self, cls, tok, *args):
        node = cls(*args)
        node.pos = self.line_col(tok.pos)
        return node

    @staticmethod
    def describe(tok):
        if tok.type == 'EOF':
            return 'конец ввода'
        if tok.type == 'ERROR':
            return f'недопустимый символ {tok.lexeme!r}'
        return f"'{tok.lexeme}'"

    def error_at(self, tok, message):
        line, col = self.line_col(tok.pos)
        return SyntaxErr(Diagnostic(line, col, message))

    def expected(self, what):
        return self.error_at(self.tok, f'ожидается {what}, получено {self.describe(self.tok)}')

    def expect(self, type_, what):
        if self.tok.type != type_:
            raise self.expected(what)
        return self.advance()

    
    def parse_module(self):
        first = self.tok
        decls = []
        if self.at('EOF'):
            self.errors.append(self.expected('определение функции или формулы').diag)
        while not self.at('EOF'):
            start = self.i
            try:
                decls.append(self.parse_decl())
            except SyntaxErr as e:
                self.errors.append(e.diag)
                self.sync_decl(start)
        module = Module(decls)
        module.pos = self.line_col(first.pos)
        return module

    def sync_decl(self, start):
        """Режим паники на уровне модуля: пропуск до начала следующего определения
        (IDENT '(' сразу после ';' или '}')."""
        if self.i == start:
            self.advance()
        while not self.at('EOF'):
            prev = self.toks[self.i - 1].type if self.i > 0 else None
            if (self.at('IDENT') and self.peek().type == 'LPAREN'
                    and prev in ('SEMI', 'RBRACE')):
                return
            self.advance()

    def parse_decl(self):
        name = self.expect('IDENT', 'имя функции или формулы')
        self.expect('LPAREN', "'('")
        params = []
        if not self.at('RPAREN'):
            params = self.parse_var_defs()
        self.expect('RPAREN', "')'")
        if self.at('FAT_ARROW'):
            self.advance()
            body = self.parse_predicate()
            return self.mk(Formula, name, name.lexeme, params, body)
        if not self.at('KW_REQUIRES', 'KW_RETURNS'):
            raise self.expected("'requires', 'returns' или '=>'")
        requires = None
        if self.at('KW_REQUIRES'):
            self.advance()
            requires = self.parse_predicate()
        self.expect('KW_RETURNS', "'returns'")
        returns = self.parse_var_defs()
        ensures = None
        if self.at('KW_ENSURES'):
            self.advance()
            ensures = self.parse_predicate()
        local_vars = []
        if self.at('KW_USES'):
            self.advance()
            local_vars = self.parse_var_defs()
        body = self.parse_statement()
        return self.mk(Function, name, name.lexeme, params, requires, returns,
                       ensures, local_vars, body)

    def parse_var_defs(self):
        defs = [self.parse_var_def()]
        while self.at('COMMA'):
            self.advance()
            defs.append(self.parse_var_def())
        return defs

    def parse_var_def(self):
        name = self.expect('IDENT', 'имя переменной')
        self.expect('COLON', "':'")
        if not self.at('KW_INT'):
            raise self.expected("тип 'int' или 'int[]'")
        self.advance()
        typ = 'int'
        if self.at('LBRACKET'):
            self.advance()
            self.expect('RBRACKET', "']'")
            typ = 'int[]'
        return self.mk(VarDef, name, name.lexeme, typ)

    
    def parse_statement(self):
        t = self.tok
        if t.type == 'KW_IF':
            return self.parse_if()
        if t.type == 'KW_WHILE':
            return self.parse_while()
        if t.type == 'LBRACE':
            return self.parse_block()
        if t.type in ('KW_ASSERT', 'KW_ASSUME'):
            kw = self.advance()
            cond = self.parse_predicate()
            self.expect('SEMI', "';'")
            return self.mk(Assert if kw.type == 'KW_ASSERT' else Assume, kw, cond)
        if t.type == 'IDENT':
            return self.parse_assignment()
        raise self.expected('оператор (присваивание, if, while, assert, assume или блок)')

    def parse_if(self):
        kw = self.advance()
        self.expect('LPAREN', "'('")
        cond = self.parse_condition()
        self.expect('RPAREN', "')'")
        then = self.parse_statement()
        orelse = None
        if self.at('KW_ELSE'):  # else относится к ближайшему if
            self.advance()
            orelse = self.parse_statement()
        return self.mk(If, kw, cond, then, orelse)

    def parse_while(self):
        kw = self.advance()
        self.expect('LPAREN', "'('")
        cond = self.parse_condition()
        self.expect('RPAREN', "')'")
        invariant = None
        if self.at('KW_INVARIANT'):
            self.advance()
            invariant = self.parse_predicate()
        body = self.parse_statement()
        return self.mk(While, kw, cond, invariant, body)

    def parse_block(self):
        open_tok = self.advance()
        stmts = []
        while not self.at('RBRACE', 'EOF'):
            start = self.i
            try:
                stmts.append(self.parse_statement())
            except SyntaxErr as e:
                self.errors.append(e.diag)
                self.sync_stmt(start)
        if self.at('EOF'):
            line, col = self.line_col(open_tok.pos)
            raise self.error_at(self.tok, f"не закрыта '{{', открытая в {line}:{col}")
        self.advance()
        return self.mk(Block, open_tok, stmts)

    def sync_stmt(self, start):
        """Режим паники внутри блока: пропуск до ';' (включительно) либо до
        '}', '{', 'if', 'while', 'assert', 'assume' (не поглощаются)."""
        while True:
            t = self.tok.type
            if t == 'EOF':
                return
            if t == 'SEMI':
                self.advance()
                return
            if t in ('RBRACE', 'LBRACE', 'KW_IF', 'KW_WHILE', 'KW_ASSERT', 'KW_ASSUME'):
                break
            self.advance()
        if self.i == start: 
            self.advance()

    def parse_assignment(self):
        name = self.tok
        if self.peek().type not in ('ASSIGN', 'LBRACKET', 'COMMA'):
            raise self.error_at(
                self.peek(), f"ожидается '=', '[' или ',', получено {self.describe(self.peek())}")
        self.advance()
        if self.at('LBRACKET'):
            self.advance()
            index = self.parse_expr()
            self.expect('RBRACKET', "']'")
            self.expect('ASSIGN', "'='")
            value = self.parse_expr()
            self.expect('SEMI', "';'")
            upd = self.mk(ArrayUpdate, name, name.lexeme, index, value)
            return self.mk(Assign, name, name.lexeme, upd)
        if self.at('COMMA'):
            targets = [name.lexeme]
            while self.at('COMMA'):
                self.advance()
                targets.append(self.expect('IDENT', 'имя переменной').lexeme)
            self.expect('ASSIGN', "'='")
            if not (self.at('IDENT') and self.peek().type == 'LPAREN'):
                raise self.expected('вызов функции')
            call = self.parse_call()
            self.expect('SEMI', "';'")
            return self.mk(TupleAssign, name, targets, call)
        self.expect('ASSIGN', "'='")
        value = self.parse_expr()
        self.expect('SEMI', "';'")
        return self.mk(Assign, name, name.lexeme, value)

    def parse_condition(self):
        node = self.parse_implies(False)
        self.require_bool(node)
        return node

    def parse_predicate(self):
        node = self.parse_implies(True)
        self.require_bool(node)
        return node

    def require_bool(self, node):
        if is_expr(node):
            raise self.expected('оператор сравнения (==, !=, <, >, <=, >=)')

    def parse_implies(self, pred):
        left = self.parse_or(pred)
        if self.at('ARROW'):
            self.require_bool(left)
            op = self.advance()
            right = self.parse_implies(pred)
            self.require_bool(right)
            return self.mk(Implies, op, left, right)
        return left

    def parse_or(self, pred):
        left = self.parse_and(pred)
        while self.at('KW_OR'):
            self.require_bool(left)
            op = self.advance()
            right = self.parse_and(pred)
            self.require_bool(right)
            left = self.mk(Or, op, left, right)
        return left

    def parse_and(self, pred):
        left = self.parse_not(pred)
        while self.at('KW_AND'):
            self.require_bool(left)
            op = self.advance()
            right = self.parse_not(pred)
            self.require_bool(right)
            left = self.mk(And, op, left, right)
        return left

    def parse_not(self, pred):
        if self.at('KW_NOT'):
            op = self.advance()
            operand = self.parse_not(pred)
            self.require_bool(operand)
            return self.mk(Not, op, operand)
        return self.parse_atom(pred)

    def parse_atom(self, pred):
        t = self.tok
        if t.type in ('KW_TRUE', 'KW_FALSE'):
            self.advance()
            return self.mk(BoolLit, t, t.type == 'KW_TRUE')
        if t.type in ('KW_FORALL', 'KW_EXISTS'):
            if not pred:
                raise self.error_at(t, 'квантор допустим только в предикате, не в условии')
            return self.parse_quantifier()
        if t.type == 'LPAREN':
            self.advance()
            inner = self.parse_implies(pred)
            self.expect('RPAREN', "')'")
            if not is_expr(inner):
                return inner
            lhs = self.parse_expr(lhs=inner)
        elif pred and t.type == 'IDENT' and self.peek().type == 'LPAREN':
            call = self.parse_call()
            if self.at(*ARITH, *CMP):
                lhs = self.parse_expr(lhs=call)
            else:
                return self.mk(FormulaRef, t, call.name, call.args)
        else:
            lhs = self.parse_expr()
        if self.at(*CMP):
            op = self.advance()
            right = self.parse_expr()
            return self.mk(Compare, op, CMP[op.type], lhs, right)
        return lhs  

    def parse_quantifier(self):
        kw = self.advance()
        self.expect('LPAREN', "'('")
        var = self.parse_var_def()
        self.expect('PIPE', "'|'")
        body = self.parse_predicate()
        self.expect('RPAREN', "')'")
        return self.mk(Quantifier, kw, kw.lexeme, var, body)


    def parse_expr(self, min_prec=1, lhs=None):
        if lhs is None:
            lhs = self.parse_unary()
        while self.at(*ARITH) and PREC[ARITH[self.tok.type]] >= min_prec:
            op_tok = self.advance()
            op = ARITH[op_tok.type]
            rhs = self.parse_expr(PREC[op] + 1)  
            lhs = self.mk(BinOp, op_tok, op, lhs, rhs)
        return lhs

    def parse_unary(self):
        if self.at('MINUS'):
            op = self.advance()
            return self.mk(Neg, op, self.parse_unary())
        return self.parse_primary()

    def parse_primary(self):
        t = self.tok
        if t.type == 'INT':
            self.advance()
            return self.mk(Num, t, int(t.lexeme))
        if t.type == 'KW_LENGTH':
            return self.parse_call()
        if t.type == 'IDENT':
            nxt = self.peek().type
            if nxt == 'LPAREN':
                return self.parse_call()
            if nxt == 'LBRACKET':
                self.advance()
                self.advance()
                index = self.parse_expr()
                self.expect('RBRACKET', "']'")
                return self.mk(Index, t, t.lexeme, index)
            self.advance()
            return self.mk(Var, t, t.lexeme)
        if t.type == 'LPAREN':
            self.advance()
            e = self.parse_expr()
            self.expect('RPAREN', "')'")
            return e
        raise self.expected('выражение')

    def parse_call(self):
        name = self.advance()  
        self.expect('LPAREN', "'('")
        args = []
        if not self.at('RPAREN'):
            args.append(self.parse_expr())
            while self.at('COMMA'):
                self.advance()
                args.append(self.parse_expr())
        self.expect('RPAREN', "')'")
        return self.mk(Call, name, name.lexeme, args)


def parse(text):
    p = Parser(text)
    ast = p.parse_module()
    return ParseResult(ast, p.errors)


def main():
    args = [a for a in sys.argv[1:] if a != '--pos']
    if len(args) != 1:
        print('Использование: python3 funny_parser.py program.funny [--pos]')
        sys.exit(2)
    with open(args[0], encoding='utf-8') as f:
        text = f.read()
    result = parse(text)
    if result.errors:
        for d in result.errors:
            print(f'{args[0]}:{d}', file=sys.stderr)
        sys.exit(1)
    print(json.dumps(to_dict(result.ast, '--pos' in sys.argv), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
