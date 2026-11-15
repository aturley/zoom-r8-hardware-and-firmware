; Position-independent copy-and-jump for loading an app (reload.c). reload.c copies these bytes (tramp_start..tramp_end)
; to DDR, writes back L1D and invalidates L1P, then calls them there, because the copies overwrite the running app:
; its L2 image and, for a version 2 app, its DDR window.
;   A4 = copy list: {source, destination, word count} triples (word aligned), ended by a triple with count 0
;   B4 = entry point
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
        MV      .L2     B4, B6                  ; keep the entry point
        MV      .L1     A4, A3                  ; walk the list with A3
next:
        LDW     .D1T1   *A3++, A4               ; source
        LDW     .D1T2   *A3++, B4               ; destination
        LDW     .D1T1   *A3++, A1               ; word count (conditions: A0-A2, B0-B2 only)
        NOP     4
  [!A1] B       .S1     done
        NOP     5
copy:
        LDW     .D1T1   *A4++, A0
        SUB     .L1     A1, 1, A1
        NOP     3
        STW     .D2T1   A0, *B4++
  [A1]  B       .S1     copy
        NOP     5
        B       .S1     next
        NOP     5
done:
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
