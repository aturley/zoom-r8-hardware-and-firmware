; value_of(A4 struct): the record +32 value function the stock row drawer calls (see browse.c). It passes its own
; return address (B3) as the second argument to value_of_c, which needs to know which call site it came from;
; B3 is left alone, so value_of_c returns straight to the row drawer.
        .text
        .global value_of
        .ref    value_of_c
value_of:
        MVKL    value_of_c, B5
        MVKH    value_of_c, B5
        B       B5
        MV      B3, B4
        NOP     4
