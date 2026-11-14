/* REVERSE's file work in C: A:\IN.WAV -> A:\OUT.WAV, header copied, audio frames reversed, in 64 KB blocks.
   Returns 0 on success, otherwise a step number. Uses only the firmware file API (r8.h). */
#include "../r8.h"
#include <stdint.h>
#define BUF ((u8 *)0xC0050000)
#define BLK 0x10000u
static u8 hdr[24];
static u32 le32(const u8 *p) { return p[0] | p[1] << 8 | p[2] << 16 | (u32)p[3] << 24; }
static u16 le16(const u8 *p) { return (u16)(p[0] | p[1] << 8); }
static int copy(int fin, int fout, u32 from, u32 n)
{
    u32 got;
    if (r8_seek(fin, (s32)from, 2)) return 1;
    while (n) {
        u32 k = n < BLK ? n : BLK;
        if (r8_read(fin, BUF, k, &got) || got != k) return 2;
        if (r8_write(fout, BUF, k, &got) || got != k) return 3;
        n -= k;
    }
    return 0;
}
static void rev_frames(u8 *p, u32 n, u32 ba)
{
    u8 *a = p, *b = p + n - ba;
    while (a < b) { for (u32 i = 0; i < ba; i++) { u8 t = a[i]; a[i] = b[i]; b[i] = t; } a += ba; b -= ba; }
}
u32 r8_main(u32 unused_a, u32 unused_b)
{
    int fin, fout; u32 got, p = 12, ba = 0, doff = 0, dlen = 0; int rc = 0;
    if (r8_open(&fin, "A:\\IN.WAV", 0, 0)) return 10;
    if (r8_read(fin, hdr, 12, &got) || got != 12 || le32(hdr) != 0x46464952 || le32(hdr + 8) != 0x45564157) { rc = 11; goto out_in; }
    for (;;) {                                           /* chunk walk */
        if (r8_seek(fin, (s32)p, 2) || r8_read(fin, hdr, 24, &got) || got < 8) { rc = 12; goto out_in; }
        u32 id = le32(hdr), n = le32(hdr + 4);
        if (id == 0x20746d66 && got >= 24) ba = le16(hdr + 20);
        if (id == 0x61746164) { doff = p + 8; dlen = n; break; }
        p += 8 + n + (n & 1);
    }
    if (!ba || dlen % ba) { rc = 13; goto out_in; }
    if (r8_open(&fout, "A:\\OUT.WAV", 0x101, 0x80)) { rc = 14; goto out_in; }
    if ((rc = copy(fin, fout, 0, doff)) != 0) { rc += 20; goto out_both; }
    for (u32 left = dlen; left; ) {                      /* blocks from the end, each reversed */
        u32 k = left < BLK ? left : BLK; k -= k % ba;
        left -= k;
        if (r8_seek(fin, (s32)(doff + left), 2) || r8_read(fin, BUF, k, &got) || got != k) { rc = 30; goto out_both; }
        rev_frames(BUF, k, ba);
        if (r8_write(fout, BUF, k, &got) || got != k) { rc = 31; goto out_both; }
    }
out_both:
    r8_close(fout);
out_in:
    r8_close(fin);
    return (u32)rc;
}
