#!/usr/bin/env python3
"""
Проверка модели reqack явным перебором состояний .

Модель повторяет reqack.smv / reqack.pml:
  состояние = (client, req, ack, timer), входы = (send, lose), начальное = (idle, 0, 0, 0).
Вычисляет достижимые состояния и проверяет свойства S1-S3, L1, L2, P6 (+ вспомогательные C1, C2).

"""
import sys
from collections import deque

INIT = ('idle', False, False, 0)


def make_step(T):
    def step(s, send, lose):
        c, req, ack, t = s
        if c == 'idle':
            c2 = 'waiting' if send else 'idle'
        elif c == 'waiting':
            c2 = 'done_ok' if ack else ('done_to' if t == T else 'waiting')
        else:
            c2 = 'idle'
        r2 = (c == 'idle' and send)
        a2 = req and not lose
        if c == 'waiting' and not ack:
            t2 = min(t + 1, T)
        elif c == 'waiting':
            t2 = t
        else:
            t2 = 0
        return (c2, r2, a2, t2)
    return step


def build(T):
    step = make_step(T)
    succ, seen, q = {}, {INIT}, deque([INIT])
    while q:
        s = q.popleft()
        succ[s] = [((sd, ls), step(s, sd, ls)) for sd in (False, True) for ls in (False, True)]
        for _, s2 in succ[s]:
            if s2 not in seen:
                seen.add(s2)
                q.append(s2)
    return succ


def fmt(s):
    c, r, a, t = s
    return f"client={c} req={str(r).upper()} ack={str(a).upper()} timer={t}"


def bfs_path(succ, src, goal, allowed=None):
    prev, q = {src: None}, deque([src])
    while q:
        s = q.popleft()
        if goal(s):
            path = []
            while prev[s] is not None:
                inp, p = prev[s]
                path.append((inp, s))
                s = p
            return path and path[0][1] or src, list(reversed(path))
        for inp, s2 in succ[s]:
            if (allowed is None or s2 in allowed) and s2 not in prev:
                prev[s2] = (inp, s)
                q.append(s2)
    return None, None


def EG(succ, P): # существует путь, который всё время остаётся в P
    Z = set(P)
    while True:
        Z2 = {s for s in Z if any(s2 in Z for _, s2 in succ[s])}
        if Z2 == Z:
            return Z
        Z = Z2


def EU(succ, P, Q): # существует путь, идущий по P, пока не достигнет Q
    Z = set(Q)
    while True:
        Z2 = Z | {s for s in P if any(s2 in Z for _, s2 in succ[s])}
        if Z2 == Z:
            return Z
        Z = Z2


def EF(succ, Q):
    return EU(succ, set(succ), Q)


def shortest_cycle(succ, E, c): #обход в глубину 
    prev, q = {}, deque()
    for inp, s2 in succ[c]:
        if s2 in E and s2 not in prev:
            prev[s2] = (inp, c)
            q.append(s2)
    while q:
        s = q.popleft()
        if s == c:
            path = []
            while True:
                inp, p = prev[s]
                path.append((inp, s))
                if p == c:
                    break
                s = p
            return list(reversed(path))
        for inp, s2 in succ[s]:
            if s2 in E and s2 not in prev:
                prev[s2] = (inp, s)
                q.append(s2)
    return None


def find_lasso(succ, E, bad): #кратчайший префикс до состояния из bad, 
                              #затем путь внутри E до первого состояния на цикле, цикл
    s_w, pref = bfs_path(succ, INIT, lambda s: s in bad)
    order, seen, q, parent = [], {s_w}, deque([s_w]), {s_w: None}
    while q:
        s = q.popleft()
        order.append(s)
        for inp, s2 in succ[s]:
            if s2 in E and s2 not in seen:
                seen.add(s2)
                parent[s2] = (inp, s)
                q.append(s2)
    best = None
    for c in order:
        cyc = shortest_cycle(succ, E, c)
        if cyc:
            mid, s = [], c
            while parent[s] is not None:
                inp, p = parent[s]
                mid.append((inp, s))
                s = p
            mid = list(reversed(mid))
            total = len(pref) + len(mid) + len(cyc)
            if best is None or total < best[0]:
                best = (total, pref, mid, cyc)
    if best is None:
        return None
    _, pref, mid, cyc = best
    return pref + mid, cyc


def trace_text(path, loop_from=None):
    lines = [f"  -> State 1.1 <-\n       {fmt(INIT)}"]
    for i, (inp, s) in enumerate(path, start=2):
        lines.append(f"  -> Input: send={str(inp[0]).upper()} lose={str(inp[1]).upper()} <-")
        mark = "   <== начало цикла (Loop starts here)" if loop_from == i - 2 else ""
        lines.append(f"  -> State 1.{i} <-{mark}\n       {fmt(s)}")
    return "\n".join(lines)

def analyse(T):
    succ = build(T)                         
    R = set(succ)                           # все достижимые состояния
    waiting = {s for s in R if s[0] == 'waiting'}      # w0, w1a, w1, w2, w3
    fin = lambda s: s[0] in ('done_ok', 'done_to')     
    res = {}                               

    inv = {                                 
        'S1': lambda s: (not s[2]) or s[3] == 1,         # ack -> timer = 1
        'S2': lambda s: (not s[2]) or s[0] == 'waiting', # ack -> client = waiting
        'S3': lambda s: s[0] != 'done_to' or s[3] == T,  # done_to -> timer = T
    }
    for k, pred in inv.items():
        bad, _ = bfs_path(succ, INIT, lambda s: not pred(s))  # ищем состояние, где инвариант нарушен
        res[k] = bad is None                

    E1 = EG(succ, {s for s in R if not fin(s)})   # откуда можно вечно не заканчивать
    res['L1'] = not (waiting & E1)          # среди них нет ожидающих -> L1 истинно

    E2 = EG(succ, {s for s in R if s[0] != 'done_ok'})  # откуда можно вечно без успеха
    bad2 = waiting & E2                     # ждём и успеха может не быть: {w0,w1,w2,w3}
    res['L2'] = not bad2                    # непусто -> L2 ложно
    lasso = find_lasso(succ, E2, bad2) if bad2 else None  # контрпример

    done_ok = {s for s in R if s[0] == 'done_ok'}                
    p6 = {s for s in R if s[0] == 'done_ok' and s[3] == T}       
    res['P6'] = INIT in EF(succ, p6)        
    res['C2'] = INIT in EF(succ, done_ok)   
    res['C1'] = not (waiting & E1)         

    nt = sum(len(v) for v in succ.values()) 
    return dict(T=T, succ=succ, R=R, res=res, lasso=lasso, nt=nt, done_ok=done_ok, p6=p6)

def main():
    T = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    A = analyse(T)
    R, res = A['R'], A['res']
    print(f"=== reqack: T = {T} ===")
    print(f"достижимых состояний: {len(R)};  переходов (с учётом входов send/lose): {A['nt']}")
    print("\nДостижимые состояния:")
    for i, s in enumerate(sorted(R, key=lambda s: (s[0], s[3], s[1], s[2]))):
        print(f"  {i:2d}: {fmt(s)}")
    names = {
        'S1': 'G (chan_ack -> timer = 1)',
        'S2': 'G (chan_ack -> client = waiting)',
        'S3': 'G (client = done_to -> timer = T)',
        'L1': 'G (client = waiting -> F (client = done_ok | client = done_to))',
        'L2': 'G (client = waiting -> F client = done_ok)',
        'P6': 'EF (client = done_ok & timer = T)',
        'C1': 'AG (client = waiting -> AF (client = done_ok | client = done_to))   [вспомогательное]',
        'C2': 'EF client = done_ok   [вспомогательное]',
    }
    print("\nРезультаты:")
    for k in ('S1', 'S2', 'S3', 'L1', 'L2', 'P6', 'C1', 'C2'):
        print(f"  -- {names[k]} is {'true' if res[k] else 'false'}")
    if A['lasso']:
        pre, cyc = A['lasso']
        print("\nКонтрпример к L2 (лассо: префикс + цикл; префикс кратчайший, получен BFS):")
        print(trace_text(pre + cyc[:-1], loop_from=len(pre) - 1))
        print(f"  замыкающий переход цикла: Input send={str(cyc[-1][0][0]).upper()} lose={str(cyc[-1][0][1]).upper()} -> снова в состояние начала цикла")
        print(f"  длина трассы: {len(pre) + len(cyc)} состояний, из них в цикле: {len(cyc)}")
    _, path = bfs_path(A['succ'], INIT, lambda s: s[0] == 'done_ok')
    print("\nСвидетельство для вспомогательной EF client = done_ok (кратчайший путь):")
    print(trace_text(path))
    print("\nОбоснование ложности P6 = EF (client = done_ok & timer = T):")
    print(f"  |{{s : client=done_ok & timer=T}} ∩ Reach| = {len(A['p6'])}")
    print("  состояния Reach с client=done_ok: " + "; ".join(fmt(s) for s in sorted(A['done_ok'])))
    print(f"  INIT ∈ lfp Z.(p ∪ EX Z) = {res['P6']}")
    inv_ok = all(s[0] != 'done_ok' or s[3] == 1 for s in R)
    print(f"  инвариант I: client=done_ok -> timer=1 выполнен на всех достижимых состояниях: {inv_ok}")


if __name__ == '__main__':
    main()
