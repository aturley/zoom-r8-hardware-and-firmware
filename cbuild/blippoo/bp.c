/* see bp.h */
#include "bp.h"
#include "decim.h"

/* ---- fast functions (no maths library on the R8; division there is a library call, so a reciprocal estimate
   (RCPSP, 8 bits) refined twice by Newton's method, about 1e-7 relative) */
#ifdef __TI_COMPILER_VERSION__
#include <c6x.h>
static inline float rcp(float x) { float r = _rcpsp(x); r = r * (2.0f - x * r); return r * (2.0f - x * r); }
#else
static inline float rcp(float x) { return 1.0f / x; }
#endif
static inline float exp2f_(float x)                       /* 2^x, about 1e-4 relative error, x clamped to [-30, 14] */
{
    if (x < -30.0f) x = -30.0f; else if (x > 14.0f) x = 14.0f;
    int i = (int)x; if ((float)i > x) i--;         /* floor */
    float f = x - (float)i;
    float p = 1.0f + f * (0.69583356f + f * (0.22606716f + f * 0.07809790f));
    union { float f; unsigned u; } v; v.f = p;
    v.u += (unsigned)i << 23;                      /* times 2^i (stays in range for the clamp above) */
    return v.f;
}
static inline float sat(float x)                          /* soft saturation, tanh-like, |x| <= 1 */
{
    if (x > 3.0f) return 1.0f; if (x < -3.0f) return -1.0f;
    float x2 = x * x;
    return x * (27.0f + x2) * rcp(27.0f + 9.0f * x2);
}

#define PULSE_SAMPLES 8                            /* about 45 us at 176.4 kHz */
#define IN_GAIN 0.25f                              /* into the resonator (the pulses would saturate it) */
#define RES_K 7.85f                                /* filter feedback: just below self-oscillation (8) */
#define TWO_PI 6.2831853f

void bp_init(bp_t *b, float out_rate)
{
    char *z = (char *)b;
    for (unsigned i = 0; i < sizeof *b; i++) z[i] = 0;
    b->fs = out_rate * BP_OS;
    b->ta = -0.3f; b->tb = 0.2f; b->da = 1; b->db = -1;
    b->ra = 0x5; b->rb = 0xA;
    static const float defaults[BP_NPARAM] = {
        0.62f, 0.55f, 0.2f, 0.1f, 0.15f, 0.25f, 0.1f, 0.2f,
        0.55f, 0.68f, 0.2f, 0.2f, 0.0f, 1.0f, 0.0f, 0.0f };
    for (int i = 0; i < BP_NPARAM; i++) b->target[i] = b->p[i] = defaults[i];
}
void bp_set(bp_t *b, int param, float v)
{
    if (param < 0 || param >= BP_NPARAM) return;
    b->target[param] = v < 0.0f ? 0.0f : v > 1.0f ? 1.0f : v;
}

static float dac(unsigned r) { return (float)((r >> 1) & 7) * (2.0f / 7.0f) - 1.0f; }

/* One 3-pole resonant lowpass step, zero-delay feedback: three trapezoidal one-poles (G = g / (1 + g), g =
   tan(pi f / fs)), the feedback solved for this sample from the states (y3 = (G^3 x + S) / (1 + k G^3)), then the
   input stage saturated and the poles run. Self-oscillation is at k = 8 at any cutoff; RES_K sits just below it. */
static inline float lp3(float *s, float x, float G)
{
    float b = 1.0f - G, G2 = G * G, G3 = G2 * G;
    float S = G2 * b * s[0] + G * b * s[1] + b * s[2];
    float y3 = (G3 * x + S) * rcp(1.0f + RES_K * G3);
    float u = sat(x - RES_K * y3);
    float v, y;
    v = G * (u - s[0]); y = v + s[0]; s[0] = y + v; float y1 = y;
    v = G * (y1 - s[1]); y = v + s[1]; s[1] = y + v; float y2 = y;
    v = G * (y2 - s[2]); y = v + s[2]; s[2] = y + v;
    s[3] = y2;                                     /* pole 2's output, for COLOUR */
    return y;
}
static inline float coef(float hz, float pi_fs)           /* G for a cutoff (prewarped); pi_fs = pi / fs */
{
    float x = hz * pi_fs;
    if (x > 0.7f) x = 0.7f;
    float x2 = x * x, g = x * (1.0f + x2 * (0.33333333f + x2 * (0.13333333f + x2 * 0.05396825f)));   /* tan */
    return g * rcp(1.0f + g);
}

void bp_render(bp_t *b, const float *in, float *out, int n)
{
    float *p = b->p;
    const float fs = b->fs, k4 = 4.0f / fs, pi_fs = 3.14159265f / fs;
    for (int j = 0; j < n; j++) {
        for (int i = 0; i < BP_NPARAM; i++) p[i] += (b->target[i] - p[i]) * 0.002f;   /* ~10 ms smoothing */
        float sw_a = p[BP_SWEEP_A] * p[BP_SWEEP_A] * 6.0f, sw_b = p[BP_SWEEP_B] * p[BP_SWEEP_B] * 6.0f;
        float rg_a = p[BP_RUNG_A] * p[BP_RUNG_A] * 6.0f, rg_b = p[BP_RUNG_B] * p[BP_RUNG_B] * 6.0f;
        float warp = p[BP_WARP] * p[BP_WARP] * 5.0f, col = p[BP_COLOUR] * p[BP_COLOUR] * 4.0f;
        float rpk = p[BP_RUNG_PK] * p[BP_RUNG_PK] * 4.0f, spr = p[BP_SPREAD] * p[BP_SPREAD] * 4.0f;
        float base_a = 20.0f * (p[BP_OSC_A] - 1.0f), base_b = 20.0f * (p[BP_OSC_B] - 1.0f);
        float pk1 = p[BP_PEAK_1] * 9.5f, pk2 = p[BP_PEAK_2] * 9.5f;
        float ext = in ? in[j] * p[BP_EXT] * p[BP_EXT] * 4.0f : 0.0f, ping = p[BP_PING];
        for (int o = 0; o < BP_OS; o++) {
            /* oscillators: exponential FM from the other triangle, a rungler and the S&H */
            float fa = 12000.0f * exp2f_(base_a + sw_a * b->tb + rg_a * b->rva + warp * b->sh);
            float fb = 12000.0f * exp2f_(base_b + sw_b * b->ta + rg_b * b->rvb + warp * b->sh);
            if (fa > 40000.0f) fa = 40000.0f;
            if (fb > 40000.0f) fb = 40000.0f;
            int pa = b->da, pb = b->db;
            b->ta += (float)b->da * fa * k4;
            if (b->ta > 1.0f) { b->ta = 2.0f - b->ta; b->da = -1; } else if (b->ta < -1.0f) { b->ta = -2.0f - b->ta; b->da = 1; }
            if (b->ta > 1.0f) b->ta = 1.0f; else if (b->ta < -1.0f) b->ta = -1.0f;
            b->tb += (float)b->db * fb * k4;
            if (b->tb > 1.0f) { b->tb = 2.0f - b->tb; b->db = -1; } else if (b->tb < -1.0f) { b->tb = -2.0f - b->tb; b->db = 1; }
            if (b->tb > 1.0f) b->tb = 1.0f; else if (b->tb < -1.0f) b->tb = -1.0f;
            /* runglers: A's register clocked by B's square rising, data = A's square; B's the other way round */
            if (b->db > 0 && pb < 0) { b->ra = ((b->ra << 1) | (b->da > 0)) & 0xF; b->rva = dac(b->ra); }
            if (b->da > 0 && pa < 0) { b->rb = ((b->rb << 1) | (b->db > 0)) & 0xF; b->rvb = dac(b->rb); }
            /* comparator: each crossing of the triangles samples one (S&H) and fires a pulse */
            int c = b->ta > b->tb;
            if (c != b->cmp) { b->cmp = c; b->sh = b->ta; b->pulse = PULSE_SAMPLES; b->crossings++; }
            float x = 0.0f;
            if (b->pulse) { x = ping; b->pulse--; }
            float hp = x - b->dc_x + 0.99982f * b->dc_y;     /* DC blocker, ~5 Hz */
            b->dc_x = x; b->dc_y = hp;
            float e = (hp + ext) * IN_GAIN;
            /* twin peak resonator: two 3-pole lowpasses, pole 2 modulating its own cutoff, subtracted */
            float m = rpk * 0.5f * (b->rva + b->rvb);
            float g1 = coef(20.0f * exp2f_(pk1 + m + spr * b->sh + col * b->f1[3]), pi_fs);
            float g2 = coef(20.0f * exp2f_(pk2 + m - spr * b->sh + col * b->f2[3]), pi_fs);
            float y = lp3(b->f1, e, g1) - lp3(b->f2, e, g2);
            /* decimator history */
            b->dec[b->dpos] = y; b->dpos = (b->dpos + 1) & 63;
        }
        float acc = 0.0f;                          /* 64-tap FIR over the last 64 oversampled values */
        int q = b->dpos;
        for (int t = 0; t < DECIM_N; t++) { acc += decim_h[t] * b->dec[q]; q = (q + 1) & 63; }
        out[j] = sat(acc * 8.0f);
    }
}
