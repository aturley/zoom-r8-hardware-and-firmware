#include "../r8.h"
#include <stdint.h>
uint32_t test(uint32_t seed, uint32_t *out);
u32 r8_main(u32 a, u32 b) { return test(a, (uint32_t *)b); }
