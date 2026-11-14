; Interrupt service table for the Blippoo app: ISTP points here (1 KB aligned, 16 fetch packets of 32 bytes).
; INT15 (timer 1, event 40) -> bp_isr (audio + panel); anything else unexpected spins in place, where a debugger can see it.
        .sect   ".vecs"
        .global bp_vectors
        .global bp_isr
        .align  1024
bp_vectors:
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
        B       .S2     bp_isr
        NOP     5
        NOP
        NOP
        NOP
        NOP
        NOP
        NOP
