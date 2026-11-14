#include "simtest.h"
/* frame reversal as REVERSE does it, for 1..8 byte frames, plus block-wise from both ends */
static uint8_t buf[4100], ref[4100];
static void rev_frames(uint8_t *p, uint32_t n, uint32_t ba)
{
    uint8_t *a = p, *b = p + n - ba;
    while (a < b) { for (uint32_t i = 0; i < ba; i++) { uint8_t t = a[i]; a[i] = b[i]; b[i] = t; } a += ba; b -= ba; }
}
static void rev16(int16_t *p, int n) { for (int i = 0, j = n - 1; i < j; i++, j--) { int16_t t = p[i]; p[i] = p[j]; p[j] = t; } }
static void rev32(uint32_t *p, int n) { for (int i = 0, j = n - 1; i < j; i++, j--) { uint32_t t = p[i]; p[i] = p[j]; p[j] = t; } }
uint32_t test(uint32_t seed, uint32_t *out)
{
    uint32_t h = FNV0; rng_state = seed;
    for (int i = 0; i < 4100; i++) ref[i] = (uint8_t)rnd();
    for (uint32_t ba = 1; ba <= 8; ba++) {
        uint32_t n = (4096 / ba) * ba;
        memcpy(buf, ref, sizeof buf);
        rev_frames(buf, n, ba);
        h = fnv(h, buf, sizeof buf); out[ba] = h;
        rev_frames(buf, n, ba);
        out[8 + ba] = memcmp(buf, ref, sizeof buf) == 0;
    }
    memcpy(buf, ref, sizeof buf); rev16((int16_t *)buf, 2050); h = fnv(h, buf, sizeof buf);
    memcpy(buf, ref, sizeof buf); rev32((uint32_t *)buf, 1025); h = fnv(h, buf, sizeof buf);
    memmove(buf + 3, buf, 1000); memmove(buf, buf + 7, 1000); h = fnv(h, buf, sizeof buf);
    memset(buf + 1, 0x5a, 999); h = fnv(h, buf, sizeof buf);
    out[0] = h; return h;
}
