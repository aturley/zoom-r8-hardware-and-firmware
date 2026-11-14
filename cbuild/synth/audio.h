#ifndef AUDIO_H
#define AUDIO_H
#include "hw.h"
#define FS 44100u                   /* the project rate the stock clock was running at when captured */
extern int audio_clock_ok;          /* every McASP clock-domain step latched (master clock present) */
extern u32 audio_slots;             /* slots written since start (2 per stereo frame) */
void board_state_stock(void);       /* GPIO outputs as the running stock firmware had them */
void audio_init(void);
int  audio_ready(void);             /* McASP wants the next slot */
void audio_put(s32 sample24);       /* one slot (left, right, left, ...) to both transmit serializers */
void audio_pump(void);              /* feed the McASP from the ring (main.c); call at least every ~2000 cycles */
#endif
