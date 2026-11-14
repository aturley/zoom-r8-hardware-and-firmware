#include "simtest.h"
/* integer division/modulo, 64-bit, shifts, saturation, bit ops, function pointers, recursion, structs, some double */
struct S { int8_t a; int16_t b; int32_t c; int64_t d; uint8_t e[5]; };
typedef int32_t (*op_t)(int32_t, int32_t);
static int32_t add_(int32_t a, int32_t b) { return a + b; }
static int32_t sdiv(int32_t a, int32_t b) { return b ? a / b : 0; }
static int32_t smod(int32_t a, int32_t b) { return b ? a % b : 0; }
static int32_t udiv(int32_t a, int32_t b) { return b ? (int32_t)((uint32_t)a / (uint32_t)b) : 0; }
static int32_t umod(int32_t a, int32_t b) { return b ? (int32_t)((uint32_t)a % (uint32_t)b) : 0; }
static int32_t mul(int32_t a, int32_t b) { return a * b; }
static int32_t shl(int32_t a, int32_t b) { return (int32_t)((uint32_t)a << (b & 31)); }
static int32_t sar(int32_t a, int32_t b) { return a >> (b & 31); }
static int32_t shr(int32_t a, int32_t b) { return (int32_t)((uint32_t)a >> (b & 31)); }
static int32_t mn(int32_t a, int32_t b) { return a < b ? a : b; }
static int32_t mxu(int32_t a, int32_t b) { return (uint32_t)a > (uint32_t)b ? a : b; }
static int32_t sat(int32_t a, int32_t b) { int64_t s = (int64_t)a + b; return s > 0x7fffffff ? 0x7fffffff : s < -0x7fffffffLL - 1 ? (int32_t)0x80000000 : (int32_t)s; }
static int32_t clz(int32_t a, int32_t b) { (void)b; uint32_t v = (uint32_t)a; int n = 0; if (!v) return 32; while (!(v & 0x80000000u)) { v <<= 1; n++; } return n; }
static int32_t pop(int32_t a, int32_t b) { uint32_t v = (uint32_t)a ^ (uint32_t)b; int n = 0; while (v) { n += v & 1; v >>= 1; } return n; }
static int32_t m16(int32_t a, int32_t b) { return (int16_t)a * (int16_t)b; }
static int32_t mulhi(int32_t a, int32_t b) { return (int32_t)(((int64_t)a * b) >> 32); }
static const op_t ops[] = { add_, sdiv, smod, udiv, umod, mul, shl, sar, shr, mn, mxu, sat, clz, pop, m16, mulhi };
static uint64_t fact(uint32_t n) { return n < 2 ? 1 : n * fact(n - 1); }
static int32_t r32(void) { uint32_t r = rnd(); switch (r & 7) { case 0: return 0; case 1: return (int32_t)0x80000000; case 2: return -1; case 3: return (int32_t)(r >> 28); case 4: return -(int32_t)(r >> 27); default: return (int32_t)r; } }
uint32_t test(uint32_t seed, uint32_t *out)
{
    uint32_t h = FNV0; rng_state = seed;
    uint32_t hk[32]; for (int k = 0; k < 32; k++) hk[k] = FNV0;
    for (int i = 0; i < 400; i++) {
        int32_t a = r32(), b = r32();
        if ((a == (int32_t)0x80000000 && b == -1)) b = 3;
        for (int k = 0; k < 16; k++) { int32_t v = ops[k](a, b); h = fnv(h, &v, 4); hk[k] = fnv(hk[k], &v, 4); }
        uint64_t x = (uint64_t)rnd() << 32 | rnd(), y = (uint64_t)rnd() << 32 | rnd();
        uint64_t q[8] = { x * y, x + y, x - y, x >> (b & 63), x << (a & 63), (uint64_t)((int64_t)x >> (b & 63)), y ? x / (y >> (a & 31)| 1) : 0, x % (y | 1) };
        h = fnv(h, q, sizeof q); for (int k = 0; k < 8; k++) hk[16 + k] = fnv(hk[16 + k], &q[k], 8);
        int64_t sx = (int64_t)x, sy = (int64_t)(y >> 3) | 1; int64_t sq[2] = { sx / sy, sx % sy }; h = fnv(h, sq, sizeof sq); hk[24] = fnv(hk[24], sq, 8); hk[25] = fnv(hk[25], sq + 1, 8);
        int32_t c[6] = { a / 7, a % 7, (int32_t)((uint32_t)a / 10u), a / -3, a / 1000, (int32_t)((uint32_t)a % 441u) }; h = fnv(h, c, sizeof c); hk[26] = fnv(hk[26], c, sizeof c);
        double d = (double)a * 1.5 + (double)b / 3.0; float f = (float)a * 0.25f - (float)b * 0.25f;
        int32_t di[4] = { (int32_t)(d / 1e3), (int32_t)f, d > f, (int32_t)(float)(uint32_t)a }; h = fnv(h, di, sizeof di); for (int k = 0; k < 4; k++) hk[27 + k] = fnv(hk[27 + k], &di[k], 4);
    }
    struct S s1 = { -5, -300, 123456, -9876543210LL, { 1, 2, 3, 4, 5 } }, s2;
    s2 = s1; s2.e[4] += (uint8_t)s2.a; h = fnv(h, &s2.d, 8); h = fnv(h, s2.e, 5);
    uint64_t f20 = fact(20); h = fnv(h, &f20, 8);
    for (int k = 0; k < 31; k++) out[1 + k] = hk[k];
    out[0] = h; return h;
}
