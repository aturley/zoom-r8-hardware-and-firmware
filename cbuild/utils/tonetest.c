/* TONETEST.BIN: an experiment in playing and recording from a utility, through the stock audio interrupt.

   The stock audio interrupt (0x1180E738, every 8 frames) works out which half of its double buffers is free
   (B14[821] = 0xC08137F4: 1 when the transmit DMA is in the first half), calls the per-block pointers at
   0x11F02E68 and 0x11F02E6C, then for each of the 8 frames reads the input frame (0x11F02828 + 8 * (8h + f): left,
   right), runs a chain of 20 pointers (0x11F02E78...), writes the output frame (0x11F028A8 + 16 * (8h + f): four
   words, serializers 2 and 4, left and right), and then calls the per-block pointers at 0x11F02E70 and 0x11F02E74.
   Samples are 24 bits, left-justified in 32.

   This utility wraps the last pointer (0x11F02E74): our block() calls the original, then overwrites the block's
   output frames - 1 kHz on words 1 and 3, 440 Hz on words 0 and 2, at -12 dBFS - and copies the block's input
   frames to memory. After 3 seconds (counted in blocks) the pointer is put back, and the input is written to
   A:\TONETEST.WAV (24-bit stereo, 44100 Hz). A popup gives the number of blocks and the input peak.
   Questions: which output words are the line out and which the headphones; does the interrupt run while a menu is
   up; the delay from output to input (with a cable from the outputs to the inputs). */
#include "r8.h"
static char *put_s(char *d, const char *s) { while (*s) *d++ = *s++; *d = 0; return d; }
static char *put_u(char *d, u32 v, int w)
{
    char t[12]; int k = 0;
    do { t[k++] = (char)('0' + v % 10); v /= 10; } while (v);
    while (k < w) t[k++] = '0';
    while (k) *d++ = t[--k];
    *d = 0; return d;
}

#define HOOK      (*(void (*volatile *)(void))0x11F02E74u)
#define HALF      (*(volatile int *)0xC08137F4u)
#define RXBUF     ((volatile s32 *)0x11F02828u)
#define TXBUF     ((volatile s32 *)0x11F028A8u)
#define RATE      44100
#define SECONDS   3
#define FRAMES    (RATE * SECONDS / 8 * 8)

static void (*orig)(void);
static volatile u32 blocks, captured;
static u32 ph1, ph2;
static s32 sinetab[1024];
static u8 rec[FRAMES * 6];                         /* 24-bit stereo */
static s32 peak;
static u8 hdr[44];

static s32 tone(u32 ph) { return sinetab[ph >> 22]; }

static void block(void)                            /* called from the stock audio interrupt */
{
    orig();
    int h = HALF;
    volatile s32 *tx = TXBUF + 4 * 8 * h, *rx = RXBUF + 2 * 8 * h;
    for (int f = 0; f < 8; f++) {
        s32 a = tone(ph1), b = tone(ph2);
        ph1 += 97391550u;                          /* 1000 Hz at 44100 */
        ph2 += 42852282u;                          /* 440 Hz */
        tx[4 * f + 0] = b; tx[4 * f + 2] = b;
        tx[4 * f + 1] = a; tx[4 * f + 3] = a;
        if (captured < FRAMES) {
            u8 *d = rec + captured * 6;
            for (int c = 0; c < 2; c++) {
                s32 v = rx[2 * f + c] >> 8;
                s32 m = v < 0 ? -v : v;
                if (m > peak) peak = m;
                d[3 * c] = (u8)v; d[3 * c + 1] = (u8)(v >> 8); d[3 * c + 2] = (u8)(v >> 16);
            }
            captured++;
        }
    }
    blocks++;
}

static void put32(u8 *p, u32 v) { p[0] = (u8)v; p[1] = (u8)(v >> 8); p[2] = (u8)(v >> 16); p[3] = (u8)(v >> 24); }
static void put16(u8 *p, u32 v) { p[0] = (u8)v; p[1] = (u8)(v >> 8); }

u32 r8_main(u32 path, u32 b)
{
    (void)path; (void)b;
    /* sine table by rotation, -12 dBFS, as 24-bit left-justified */
    double c = 0.99998117528260111, s = 0.0061358846491544753, x = 1.0, y = 0.0;
    for (int i = 0; i < 1024; i++) {
        sinetab[i] = (s32)(y * 0.25 * 8388607.0) << 8;
        double nx = x * c - y * s; y = x * s + y * c; x = nx;
    }
    orig = HOOK;
    u32 o = (u32)orig;
    if (!((o >= 0x11800000u && o < 0x11840000u) || (o >= 0xC0700000u && o < 0xC0800000u))) {
        static char m[24];
        put_s(put_u(put_s(m, "HOOK? "), o >> 16, 0), "");
        r8_popup(m);
        return 0;
    }
    blocks = captured = 0; peak = 0;
    r8_pre_msg();
    r8_show_msg(R8_STR_PLEASE_WAIT);
    HOOK = block;
    u32 t = 0;
    while (captured < FRAMES && ++t < 0x40000000u) { }      /* the interrupt does the work */
    HOOK = orig;
    u32 nb = blocks;
    r8_dialog_close(1);

    int fd; u32 n, data = captured * 6;
    put32(hdr, 0x46464952u); put32(hdr + 4, 36 + data); put32(hdr + 8, 0x45564157u);
    put32(hdr + 12, 0x20746d66u); put32(hdr + 16, 16); put16(hdr + 20, 1); put16(hdr + 22, 2);
    put32(hdr + 24, RATE); put32(hdr + 28, RATE * 6); put16(hdr + 32, 6); put16(hdr + 34, 24);
    put32(hdr + 36, 0x61746164u); put32(hdr + 40, data);
    if (!r8_open(&fd, "A:\\TONETEST.WAV", 0x101, 0x80)) {
        r8_write(fd, hdr, 44, &n); r8_write(fd, rec, data, &n); r8_close(fd);
    }
    static char msg[32];
    char *e = put_s(msg, "BLOCKS "); e = put_u(e, nb, 0);
    e = put_s(e, " PK "); put_u(e, (u32)(peak >> 8), 0);    /* peak in 16-bit units */
    r8_popup(msg);
    return 0;
}
