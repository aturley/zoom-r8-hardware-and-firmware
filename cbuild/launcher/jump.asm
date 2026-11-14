; r8_launch(A4 src, B4 dst, A6 len, B6 entry): uses no stack and never returns.
; Global L2 writeback-invalidate (also writes back L1D), L2 all SRAM, copy the image, write back L1D,
; invalidate L1P, jump to the entry with interrupts still off.
        .text
        .global r8_launch
r8_launch:
        MVC     CSR, B0
        AND     -2, B0, B0
        MVC     B0, CSR                     ; GIE off (again)
        MVKL    0x01845004, A0              ; L2WBINV
        MVKH    0x01845004, A0
        MVK     1, B0
        STW     B0, *A0
wbinv:  LDW     *A0, B1
        NOP     4
 [B1]   B       wbinv
        NOP     5
        MVKL    0x01840000, A1              ; L2CFG: L2MODE = 0 (no L2 cache)
        MVKH    0x01840000, A1
        LDW     *A1, B2
        NOP     4
        MVK     -8, B3
        AND     B3, B2, B2
        STW     B2, *A1
        LDW     *A1, B2                     ; read back: the mode change has taken effect
        NOP     4
        ADDK    3, A6
        SHRU    A6, 2, A2                   ; words to copy
copy:   LDW     *A4++, B5
        NOP     4
        STW     B5, *B4++
        SUB     A2, 1, A2
 [A2]   B       copy
        NOP     5
        MVKL    0x01845040, A0              ; L1DWB
        MVKH    0x01845040, A0
        MVK     1, B0
        STW     B0, *A0
l1dwb:  LDW     *A0, B1
        NOP     4
 [B1]   B       l1dwb
        NOP     5
        MVKL    0x01845028, A0              ; L1PINV
        MVKH    0x01845028, A0
        STW     B0, *A0
l1pinv: LDW     *A0, B1
        NOP     4
 [B1]   B       l1pinv
        NOP     5
        B       B6
        NOP     5
