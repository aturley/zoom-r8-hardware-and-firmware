#include "simtest.h"
static const double xs[] = { 1.0, 2.0, 7.5, -123456.75, 1e10, 3.0, 0.1, -2.5e-3, 6442450943.0, 1.0/3.0 };
static const double ys[] = { 3.0, 1e3, 7.0, 0.3, -11.0, 1.5, 3.0, 1e3, 3.0, 1.0 };
uint32_t test(uint32_t seed, uint32_t *out)
{
    volatile double a, b;
    for (int i = 0; i < 10; i++) {
        a = xs[i]; b = ys[i];
        double q = a / b, s = a + b, p = a * b, d = a - b;
        uint64_t u; memcpy(&u, &q, 8); out[i * 4] = (uint32_t)u; out[i * 4 + 1] = (uint32_t)(u >> 32);
        memcpy(&u, &p, 8); out[i * 4 + 2] = (uint32_t)(u >> 32) ^ (uint32_t)u;
        memcpy(&u, &s, 8); out[i * 4 + 3] = (uint32_t)(u >> 32) ^ (uint32_t)u ^ (uint32_t)(int32_t)d;
    }
    float fa = 7.0f, fb = 3.0f; volatile float vf = fa; float fq = vf / fb; memcpy(&out[60], &fq, 4);
    return 0;
}
