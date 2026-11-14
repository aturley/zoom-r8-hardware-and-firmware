/* Shared by the differential tests: the same source is built with cl6x (run in sim674) and clang (run natively).
   Each test defines  uint32_t test(uint32_t seed, uint32_t *out)  that fills out[0..63] and returns a hash. */
#include <stdint.h>
#include <string.h>
static uint32_t rng_state;
static inline uint32_t rnd(void) { rng_state = rng_state * 1664525u + 1013904223u; return rng_state ^ (rng_state >> 15); }
static inline uint32_t fnv(uint32_t h, const void *p, uint32_t n)
{ const uint8_t *b = (const uint8_t *)p; while (n--) { h ^= *b++; h *= 16777619u; } return h; }
#define FNV0 2166136261u
uint32_t test(uint32_t seed, uint32_t *out);
