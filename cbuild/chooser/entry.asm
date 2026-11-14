; Entry point: the AIS loader jumps here (JumpClose 0x11800000). Interrupts off, stack in L2, then main().
        .sect   ".text:entry"
        .global r8_boot
        .global main
        .global __r8_stack_top
r8_boot:
        MVK     .S2     0, B0
        MVC     .S2     B0, IER
        MVC     .S2     CSR, B0
        AND     .L2     -2, B0, B0          ; GIE = 0
        MVC     .S2     B0, CSR
        MVK     .S2     0, B0
        MVC     .S2     B0, AMR
        MVKL    .S2     __r8_stack_top, B15
        MVKH    .S2     __r8_stack_top, B15
        MVKL    .S2     main, B0
        MVKH    .S2     main, B0
        B       .S2     B0
        NOP     5
