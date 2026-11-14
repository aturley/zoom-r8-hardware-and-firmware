/* Cycle count of voice_render on the C674x (run in sim674 by tools/test_looper_cycles.py): 8 voices, stereo and
   mono, odd speeds, 64 frames per block. Results land in bench_result[]. */
#include "voice.h"
extern cregister volatile unsigned int TSCL;
#define NV 8
#define BLK 64
#pragma DATA_SECTION(loopdata, ".far")
short loopdata[2 * 4096];
static float L[BLK], R[BLK];
static voice_t v[NV];
volatile unsigned bench_result[4];
int main(void)
{
    for (int i = 0; i < 2 * 4096; i++) loopdata[i] = (short)(i * 37);
    for (int k = 0; k < NV; k++) {
        voice_init(&v[k], loopdata, k & 1 ? 4096 : 2048, k & 1 ? 1 : 2, 44100, 44100);
        voice_speed(&v[k], -3.7f + k * 1.1f);
    }
    TSCL = 0;
    for (int b = 0; b < 4; b++) {
        unsigned t0 = TSCL;
        for (int j = 0; j < BLK; j++) L[j] = R[j] = 0;
        for (int k = 0; k < NV; k++) voice_render(&v[k], L, R, BLK);
        bench_result[b] = TSCL - t0;
    }
    for (;;) ;
}
