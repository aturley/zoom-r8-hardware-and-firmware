; Interrupt service table for the looper: ISTP points here (1 KB aligned, 16 fetch packets of 32 bytes).
; INT15 (timer 1, event 40) -> lp_isr; anything else unexpected spins in place, where a debugger can see it.
        .sect   ".vecs"
        .global lp_vectors
        .global lp_isr
        .align  1024
lp_vectors:
        .loop   15
        B       .S2     $
        NOP     5
        NOP
        NOP
        NOP
        NOP
        NOP
        NOP
        .endloop
        B       .S2     lp_isr
        NOP     5
        NOP
        NOP
        NOP
        NOP
        NOP
        NOP
