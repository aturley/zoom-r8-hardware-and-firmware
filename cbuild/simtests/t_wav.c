#include "simtest.h"
/* RIFF chunk walk on synthetic headers, unaligned little-endian fields, switch on chunk ids */
static uint8_t hdr[1024];
struct fmt { uint16_t tag, ch; uint32_t rate, bps; uint16_t align, bits; };
static uint32_t le32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static uint16_t le16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static void put32(uint8_t *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
static int kind(uint32_t id)
{
    switch (id) {
    case 0x20746d66: return 1;   /* "fmt " */
    case 0x61746164: return 2;   /* "data" */
    case 0x5453494c: return 3;   /* "LIST" */
    case 0x74786562: return 4;   /* "bext" */
    case 0x4b4e554a: return 5;   /* "JUNK" */
    case 0x6b616570: return 6;   /* "peak" */
    case 0x20657563: return 7;   /* "cue " */
    default: return 0;
    }
}
static int walk(uint32_t len, struct fmt *f, uint32_t *doff, uint32_t *dlen, uint32_t *counts)
{
    if (len < 12 || memcmp(hdr, "RIFF", 4) || memcmp(hdr + 8, "WAVE", 4)) return -1;
    uint32_t p = 12; int got = 0;
    while (p + 8 <= len) {
        uint32_t id = le32(hdr + p), n = le32(hdr + p + 4); int k = kind(id); counts[k]++;
        if (k == 1 && n >= 16) { f->tag = le16(hdr + p + 8); f->ch = le16(hdr + p + 10); f->rate = le32(hdr + p + 12);
            f->bps = le32(hdr + p + 16); f->align = le16(hdr + p + 20); f->bits = le16(hdr + p + 22); got |= 1; }
        if (k == 2) { *doff = p + 8; *dlen = n; got |= 2; break; }
        p += 8 + n + (n & 1);
    }
    return got;
}
uint32_t test(uint32_t seed, uint32_t *out)
{
    uint32_t h = FNV0; rng_state = seed;
    static const char *ids[] = { "fmt ", "LIST", "bext", "JUNK", "peak", "cue ", "zzzz" };
    for (int t = 0; t < 40; t++) {
        memset(hdr, 0, sizeof hdr);
        memcpy(hdr, "RIFF", 4); memcpy(hdr + 8, "WAVE", 4);
        uint32_t p = 12;
        int nch = 1 + rnd() % 5;
        for (int c = 0; c < nch && p < 800; c++) {
            const char *id = c == 0 ? "fmt " : ids[rnd() % 7];
            uint32_t n = id[0] == 'f' ? 16 : rnd() % 61;
            memcpy(hdr + p, id, 4); put32(hdr + p + 4, n);
            if (id[0] == 'f') { uint16_t ch = 1 + rnd() % 2, bits = (rnd() & 1) ? 24 : 16;
                hdr[p+8] = 1; hdr[p+10] = ch; put32(hdr + p + 12, 44100); put32(hdr + p + 16, 44100 * ch * bits / 8);
                hdr[p+20] = ch * bits / 8; hdr[p+22] = bits; }
            p += 8 + n + (n & 1);
        }
        memcpy(hdr + p, "data", 4); put32(hdr + p + 4, rnd() % 100000);
        struct fmt f; memset(&f, 0, sizeof f); uint32_t doff = 0, dlen = 0, counts[8] = {0};
        int r = walk(p + 8 + (t % 3 == 0 ? 0 : 4), &f, &doff, &dlen, counts);
        h = fnv(h, &r, 4); h = fnv(h, &f, sizeof f); h = fnv(h, &doff, 4); h = fnv(h, &dlen, 4); h = fnv(h, counts, sizeof counts);
        out[t] = (uint32_t)r ^ doff << 8;
    }
    out[63] = h; return h;
}
