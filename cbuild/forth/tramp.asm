; Position-independent copy-and-jump for RELOAD. reload.c copies these bytes (tramp_start..tramp_end) to DDR,
; writes back L1D and invalidates L1P, then calls them there, because the copy overwrites the running app in L2.
;   A4 = source (word aligned), B4 = destination, A6 = word count (> 0), B6 = entry point.
; Only PC-relative branches and absolute register addresses: it runs at any address. Never returns.
        .sect   ".text"
        .global tramp_start
        .global tramp_end
tramp_start:
        MVK     .S2     0, B0
        MVC     .S2     B0, IER                 ; no interrupts from here on
        MVC     .S2     CSR, B0
        AND     .L2     -2, B0, B0
        MVC     .S2     B0, CSR
        MV      .L1     A6, A1                  ; counter (conditions: A0-A2, B0-B2 only)
copy:
        LDW     .D1T1   *A4++, A0
        SUB     .L1     A1, 1, A1
        NOP     3
        STW     .D2T1   A0, *B4++
  [A1]  B       .S1     copy
        NOP     5
        ; L1D write-back + invalidate (L1DWBINV 0x01845044), then L1P invalidate (L1PINV 0x01845028)
        MVKL    .S1     0x01845044, A2
        MVKH    .S1     0x01845044, A2
        MVK     .S1     1, A1
        STW     .D1T1   A1, *A2
wb:
        LDW     .D1T1   *A2, A1
        NOP     4
  [A1]  B       .S1     wb
        NOP     5
        MVKL    .S1     0x01845028, A2
        MVKH    .S1     0x01845028, A2
        MVK     .S1     1, A1
        STW     .D1T1   A1, *A2
pinv:
        LDW     .D1T1   *A2, A1
        NOP     4
  [A1]  B       .S1     pinv
        NOP     5
        B       .S2     B6                      ; the new app's entry
        NOP     5
tramp_end:
