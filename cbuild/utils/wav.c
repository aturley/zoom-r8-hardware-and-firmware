/* see wav.h */
#include "wav.h"
static u32 le32(const u8 *p) { return p[0] | p[1] << 8 | p[2] << 16 | (u32)p[3] << 24; }
static u32 le16(const u8 *p) { return p[0] | p[1] << 8; }
static void put32(u8 *p, u32 v) { p[0] = (u8)v; p[1] = (u8)(v >> 8); p[2] = (u8)(v >> 16); p[3] = (u8)(v >> 24); }
static void put16(u8 *p, u32 v) { p[0] = (u8)v; p[1] = (u8)(v >> 8); }
static u8 hb[16];
static int rd(int fd, u32 off, void *buf, u32 n)
{
    u32 got;
    return r8_seek(fd, (s32)off, 2) == 0 && r8_read(fd, buf, n, &got) == 0 && got == n;
}
int wav_parse(const char *path, wav_info *w)
{
    int fd;
    if (r8_open(&fd, path, 0, 0)) return 0;
    u32 off = 12;
    w->rate = w->bits = w->ch = w->fmt = w->doff = w->dlen = 0;
    int ok = rd(fd, 0, hb, 12) && le32(hb) == 0x46464952u && le32(hb + 8) == 0x45564157u;      /* RIFF....WAVE */
    for (int k = 0; ok && k < 64 && !(w->rate && w->doff); k++) {
        if (!rd(fd, off, hb, 8)) break;
        u32 id = le32(hb), sz = le32(hb + 4);
        if (id == 0x20746d66u && sz >= 16 && rd(fd, off + 8, hb, 16)) {                           /* "fmt " */
            w->fmt = le16(hb); w->ch = le16(hb + 2); w->rate = le32(hb + 4); w->bits = le16(hb + 14);
            if (w->fmt == 0xFFFE && sz >= 40 && rd(fd, off + 8 + 24, hb, 2)) w->fmt = le16(hb);    /* EXTENSIBLE */
        } else if (id == 0x61746164u) { w->doff = off + 8; w->dlen = sz; }                      /* "data" */
        off += 8 + sz + (sz & 1);
    }
    r8_close(fd);
    if (!w->rate || !w->doff || w->ch < 1 || w->ch > WAV_CH_MAX) return 0;
    if (!(w->fmt == 1 && (w->bits == 8 || w->bits == 16 || w->bits == 24 || w->bits == 32)) && !(w->fmt == 3 && w->bits == 32))
        return 0;
    w->ba = w->ch * w->bits / 8;
    w->frames = w->dlen / w->ba;
    return w->frames > 0;
}
float wav_sample(const wav_info *w, const u8 *p, u32 c)
{
    const u8 *s = p + c * (w->bits / 8);
    switch (w->bits) {
    case 8:  return (float)((int)s[0] - 128) * (1.0f / 128);
    case 16: return (float)(short)(s[0] | s[1] << 8) * (1.0f / 32768);
    case 24: return (float)((int)((u32)s[0] << 8 | (u32)s[1] << 16 | (u32)s[2] << 24) >> 8) * (1.0f / 8388608);
    default:
        if (w->fmt == 3) { union { u32 u; float f; } v; v.u = le32(s); return v.f; }
        return (float)(int)le32(s) * (1.0f / 2147483648.0f);
    }
}
void wav_header(u8 *h, u32 rate, u32 bits, u32 ch, u32 fmt, u32 frames)
{
    u32 ba = ch * bits / 8, data = frames * ba;
    put32(h, 0x46464952u); put32(h + 4, 36 + data + (data & 1)); put32(h + 8, 0x45564157u);
    put32(h + 12, 0x20746d66u); put32(h + 16, 16);
    put16(h + 20, fmt); put16(h + 22, ch); put32(h + 24, rate); put32(h + 28, rate * ba); put16(h + 32, ba);
    put16(h + 34, bits); put32(h + 36, 0x61746164u); put32(h + 40, data);
}
