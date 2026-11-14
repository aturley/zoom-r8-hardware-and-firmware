/* A Blippoo Box: Rob Hordijk's chaotic instrument (Leonardo Music Journal 19, 2009), as DSP. Portable C (no R8
   code), so it also builds on a computer to render test files (bp_host.c).

   Chaotic core: two oscillators with triangle and square outputs (the square is high while the triangle rises),
   about 20 octaves of exponential range (12 kHz down to almost stopped). Each is frequency-modulated by the other's
   triangle (SWEEP), by a rungler (STEP: a 4-bit shift register clocked by the other oscillator's square, with this
   one's square as data, 3 bits to an 8-level DAC; no feedback, unlike the Benjolin), and by a sample-and-hold
   (TIME WARP) that samples a triangle each time the two triangles cross; the crossing also fires a short pulse.
   Twin peak resonator: the pulses (and the external input) excite two parallel 3-pole lowpass filters held just
   below self-oscillation; the second is subtracted from the first, giving a bandpass with a resonant peak at each
   edge. Each filter's second-pole output modulates its own cutoff (COLOUR: Hordijk's "z-plane" self-modulation,
   harmonics and subharmonics without clipping); the runglers and the S&H (in opposite directions: SPREAD) move
   the peaks. Runs at 4x the output rate, decimated by a 64-tap FIR. */
#ifndef BP_H
#define BP_H

enum {
    BP_OSC_A, BP_OSC_B, BP_SWEEP_A, BP_SWEEP_B, BP_RUNG_A, BP_RUNG_B, BP_WARP, BP_COLOUR,     /* bank 1: core */
    BP_PEAK_1, BP_PEAK_2, BP_RUNG_PK, BP_SPREAD, BP_EXT, BP_PING, BP_SPARE_7, BP_SPARE_8,    /* bank 2: resonator */
    BP_NPARAM
};
#define BP_OS 4                                    /* oversampling */

typedef struct {
    float target[BP_NPARAM], p[BP_NPARAM];         /* knob positions 0..1: target, smoothed */
    float fs;                                      /* oversampled rate */
    float ta, tb; int da, db;                      /* triangles (-1..1) and directions (+1 up = square high) */
    unsigned ra, rb; float rva, rvb;               /* rungler registers and DAC outputs (-1..1) */
    int cmp; float sh; int pulse;                  /* comparator state, sampled value, pulse samples left */
    float dc_x, dc_y;                              /* DC blocker on the excitation */
    float f1[4], f2[4];                            /* the two 3-pole filters (states, pole 2 output) */
    float dec[64]; int dpos;                       /* decimator history */
    unsigned crossings;                            /* comparator pulses (for tests) */
} bp_t;

void bp_init(bp_t *b, float out_rate);
void bp_set(bp_t *b, int param, float v);          /* 0..1 */
/* n output frames (mono): in = external input (n samples, mono, -1..1; may be 0), out = n samples */
void bp_render(bp_t *b, const float *in, float *out, int n);
#endif
