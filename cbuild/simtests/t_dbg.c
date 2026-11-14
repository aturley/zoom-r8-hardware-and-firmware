#include "simtest.h"
static uint8_t b[16];
uint32_t test(uint32_t seed, uint32_t *out)
{
    rng_state = seed;
    for (int i = 0; i < 8; i++) out[i] = rnd();
    for (int i = 0; i < 16; i++) b[i] = (uint8_t)(i * 37 + 1);
    out[8] = fnv(FNV0, b, 1); out[9] = fnv(FNV0, b, 16);
    out[10] = seed * 1664525u; out[11] = seed ^ (seed >> 15); out[12] = 0x12345678u * 16777619u;
    return out[9];
}
