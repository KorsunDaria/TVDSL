/* HW5. Протокол запрос-ответ с потерей и таймаутом (Spin/Promela) */
#ifndef T
#define T 3
#endif

mtype = { idle, waiting, done_ok, done_to };

mtype client = idle;
bool  req = false;
bool  ack = false;
byte  timer = 0;

active proctype sys() {
  bool send, lose, r2, a2;
  mtype c2;
  byte t2;
  do
  :: atomic {
       if :: send = true :: send = false fi;
       if :: lose = true :: lose = false fi;
       if
       :: client == idle && send                      -> c2 = waiting
       :: client == idle && !send                     -> c2 = idle
       :: client == waiting && ack                    -> c2 = done_ok
       :: client == waiting && !ack && timer == T     -> c2 = done_to
       :: client == waiting && !ack && timer < T      -> c2 = waiting
       :: client == done_ok || client == done_to      -> c2 = idle
       fi;
       r2 = (client == idle && send);
       a2 = (req && !lose);
       if
       :: client == waiting && !ack -> t2 = (timer < T -> timer + 1 : timer)
       :: client == waiting && ack  -> t2 = timer
       :: client != waiting         -> t2 = 0
       fi;
       client = c2; req = r2; ack = a2; timer = t2;
       send = false; lose = false
     }
  od
}

/* Безопасность */
ltl s1 { [] (ack -> timer == 1) }
ltl s2 { [] (ack -> client == waiting) }
ltl s3 { [] (client == done_to -> timer == T) }
/* Живость */
ltl l1 { [] (client == waiting -> <> (client == done_ok || client == done_to)) }
ltl l2 { [] (client == waiting -> <> (client == done_ok)) }
