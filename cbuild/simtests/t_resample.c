#include "simtest.h"
/* varispeed: read the input at `speed` (-4..4, negative = backwards) with linear interpolation */
#define NIN 2000
static int16_t in16[NIN * 2], out16[NIN * 8 + 16];
static uint8_t in24[NIN * 3], out24[NIN * 3 * 4 + 16];
static inline int32_t clip16(int32_t v) { return v > 32767 ? 32767 : v < -32768 ? -32768 : v; }
static inline int32_t rd24(const uint8_t *p) { return (int32_t)((uint32_t)p[0] << 8 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 24) >> 8; }
static inline void wr24(uint8_t *p, int32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); }

/* float version, stereo 16-bit */
static uint32_t vs_float(float speed, int ch)
{
    float pos = speed < 0 ? (float)(NIN - 1) : 0.0f, step = speed < 0 ? -speed : speed;
    uint32_t n = 0;
    while (pos >= 0.0f && pos <= (float)(NIN - 1) && n < NIN * 4) {
        int32_t i = (int32_t)pos; float f = pos - (float)i;
        for (int c = 0; c < ch; c++) {
            float a = in16[i * ch + c], b = i + 1 < NIN ? in16[(i + 1) * ch + c] : a;
            float v = a + (b - a) * f;
            out16[n * ch + c] = (int16_t)clip16((int32_t)(v + (v >= 0 ? 0.5f : -0.5f)));
        }
        n++;
        pos = speed < 0 ? pos - step : pos + step;
    }
    return n;
}
/* fixed version: Q32.32 position in 64 bits, 24-bit mono */
static uint32_t vs_fixed(int32_t speed_q16)
{
    int64_t pos = speed_q16 < 0 ? (int64_t)(NIN - 1) << 32 : 0;
    int64_t step = (int64_t)speed_q16 << 16;
    uint32_t n = 0;
    while (pos >= 0 && pos <= ((int64_t)(NIN - 1) << 32) && n < NIN * 4) {
        int32_t i = (int32_t)(pos >> 32); uint32_t f = (uint32_t)pos >> 16;
        int32_t a = rd24(in24 + 3 * i), b = i + 1 < NIN ? rd24(in24 + 3 * (i + 1)) : a;
        int64_t v = (int64_t)a * (65536 - f) + (int64_t)b * f;
        int32_t s = (int32_t)((v + 32768) >> 16);
        if (s > 8388607) s = 8388607; if (s < -8388608) s = -8388608;
        wr24(out24 + 3 * n, s);
        n++; pos += step;
    }
    return n;
}
uint32_t test(uint32_t seed, uint32_t *out)
{
    uint32_t h = FNV0; rng_state = seed;
    for (int i = 0; i < NIN * 2; i++) in16[i] = (int16_t)(rnd() >> 16);
    for (int i = 0; i < NIN * 3; i++) in24[i] = (uint8_t)rnd();
    static const float speeds[] = { 1.0f, 0.5f, 2.0f, 4.0f, 0.25f, 1.37f, -1.0f, -0.73f, -4.0f, 3.1f };
    for (int k = 0; k < 10; k++) {
        uint32_t n = vs_float(speeds[k], 1 + (k & 1));
        h = fnv(h, out16, n * (1 + (k & 1)) * 2); out[k] = n;
    }
    static const int32_t fs[] = { 65536, 32768, 131072, 262144, 16384, 89785, -65536, -47841, -262144, 203162 };
    for (int k = 0; k < 10; k++) {
        uint32_t n = vs_fixed(fs[k]);
        h = fnv(h, out24, n * 3); out[16 + k] = n;
    }
    out[0] = h; return h;
}
