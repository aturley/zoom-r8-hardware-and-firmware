#ifndef SYNTH_H
#define SYNTH_H
#include "hw.h"
#define NV 8
void synth_init(u32 fs);
void synth_set(int voice, int level127);    /* faders 1-8 */
void synth_master(int level127);
void synth_mute(int on);
void synth_frame(s32 *l, s32 *r);           /* one stereo frame, 24-bit */
#endif
