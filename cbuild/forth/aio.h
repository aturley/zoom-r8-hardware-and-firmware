#ifndef AIO_H
#define AIO_H
#include "hw.h"
extern int aio_ok;                  /* every McASP clock-domain step latched (master clock present) */
extern volatile u32 aio_errs;       /* overrun/underrun/sync/clock errors seen during the last aio_pass */
extern s32 aio_peak[2];             /* largest |input| per channel (0 = left, 1 = right) in the last aio_pass (24-bit) */
extern u32 aio_frames;              /* frames received in the last aio_pass */
int  aio_init(void);                /* stock GPIO state (LCD pins kept), McASP0 receive + transmit */
void aio_pass(u32 ms, s32 gain);    /* input -> output, polled, ms milliseconds, gain Q8 */
#endif
