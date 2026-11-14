/* Eight sine voices, a Cmaj9 chord. Fader n sets voice n's level (squared curve, smoothed per sample);
   MASTER the overall level. Fixed point: 32-bit phase, 1024-entry table with linear interpolation. */
#include "synth.h"
#include "sine.h"
static const u32 HZ_X100[NV] = { 13081, 19600, 26163, 32963, 39200, 49388, 58733, 65926 };  /* C3 G3 C4 E4 G4 B4 D5 E5 */
static const s32 PAN_L[NV] = { 28000, 18000, 26000, 14000, 24000, 12000, 22000, 16000 };    /* Q15 */
static u32 phase[NV], inc[NV];
static s32 gain[NV], target[NV], mgain, mtarget, mute;

void synth_init(u32 fs)
{
    for (int v = 0; v < NV; v++) {
        /* inc = f / fs * 2^32 = f_x100 * 2^32 / (100 fs), in integer arithmetic */
        unsigned long long n = ((unsigned long long)HZ_X100[v] << 32) / (100ull * fs);
        inc[v] = (u32)n; phase[v] = (u32)v * 0x20000000u; gain[v] = target[v] = 0;
    }
    mgain = 0; mtarget = 0; mute = 0;
}
static s32 curve(int l) { return (s32)((l * l * 32767) / (127 * 127)); }   /* 0..127 -> Q15, squared */
void synth_set(int v, int l) { target[v] = curve(l); }
void synth_master(int l) { mtarget = curve(l); }
void synth_mute(int on) { mute = on; }
void synth_frame(s32 *pl, s32 *pr)
{
    s32 l = 0, r = 0;
    for (int v = 0; v < NV; v++) {
        u32 ph = phase[v]; phase[v] = ph + inc[v];
        int i = (int)(ph >> 22); s32 f = (s32)((ph >> 6) & 0xffff);
        s32 a = SINE[i], b = SINE[i + 1];
        s32 s = a + (((b - a) * f) >> 16);                   /* Q15 */
        gain[v] += (target[v] - gain[v]) >> 9;               /* ~12 ms smoothing at 44.1 kHz */
        s32 sg = (s * gain[v]) >> 15;                        /* Q15 */
        l += (sg * PAN_L[v]) >> 15;
        r += (sg * (32767 - PAN_L[v])) >> 15;
    }
    s32 mt = mute ? 0 : mtarget;
    mgain += (mt - mgain) >> 9;
    /* 8 voices at full level sum to <= 8 * 0.85 in Q15; scale to 24 bit with headroom: <<8 then /8 */
    *pl = (s32)(((long long)l * mgain) >> 15) << 5;
    *pr = (s32)(((long long)r * mgain) >> 15) << 5;
}
