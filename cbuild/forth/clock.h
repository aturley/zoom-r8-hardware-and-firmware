/* Audio master clock (traced from stock 1.17, see the work log "Audio clocking"). */
#ifndef CLOCK_H
#define CLOCK_H
#include "hw.h"
extern u32 clk_rate;                 /* current output rate in Hz; stock boot leaves 44100 */
int  clk_board(void);                /* board ID straps: GP0[7] | GP1[5] << 1 | GP1[4] << 2 */
int  clk_set_rate(u32 hz);           /* 44100 or 48000; 0 = ok, <0 = error (-1 bad rate, -2 I2C NACK, -3 I2C timeout) */
int  clk_raw(u32 family, u32 v);     /* board 7 only: stop, then reg F5 with family byte and 18-bit value v (exploration) */
#endif
