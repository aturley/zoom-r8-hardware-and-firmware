/* REAMP.BIN: re-amping through an external effect. Pick a WAV (browse.c) at the R8's current sample rate; ENTER on
   the confirmation row starts: the track plays on the line out (to the effect), the inputs (the effect's return)
   are recorded as 24-bit stereo, and the headphones carry the inputs only. When the track ends the line out goes
   quiet and recording goes on, for tails, until STOP (or EXIT). The recording is saved as "<name> REAMP.wav" next
   to the source (8.3 fallback: ...R.WAV), shifted by the measured round trip (47 frames) so that it lines up with
   the source. Levels are set on the R8 (rear trim, input knobs).

   Audio runs in the stock audio interrupt (see tonetest.c): the per-block pointer at 0x11F02E74 is wrapped, and
   block() writes the line out (output words 1, 3) from the play ring, the headphones (words 0, 2) from the inputs,
   and pushes the inputs onto the record ring. The utility's own loop keeps the play ring full from the card,
   writes the record ring to the card, and polls the UI for STOP with a 1-tick wait (so the stock tasks run). The
   rings are single-producer single-consumer between this loop and the interrupt on one core, so no cache
   maintenance is needed. Overruns and underruns are counted and reported. */
#include "browse.h"
#include "wav.h"
#include "outfile.h"

#define HOOK      (*(void (*volatile *)(void))0x11F02E74u)
#define HALF      (*(volatile int *)0xC08137F4u)     /* B14[821] */
#define RXBUF     ((volatile s32 *)0x11F02828u)
#define TXBUF     ((volatile s32 *)0x11F028A8u)
#define LATENCY   47                                 /* frames, line out -> inputs (TONETEST 2026-11-12) */
#define PRING     32768                              /* frames (power of 2) */
#define RRING     32768
#define CHUNK     4096                               /* frames per card read / write */
#define SUFFIX    " REAMP"

static wav_info src;
static char path[BROWSE_PATH], dst[BROWSE_PATH + 128], title[48], lname[100];

/* rings: s32 left-justified samples, L R */
static s32 pring[PRING][2], rring[RRING][2];
static volatile u32 p_head, p_tail;                 /* loop writes head, interrupt reads tail */
static volatile u32 r_head, r_tail;                 /* interrupt writes head, loop reads tail */
static volatile int recording, src_done;
static volatile u32 underruns, overruns, blocks;
static void (*orig)(void);

static void block(void)                             /* in the stock audio interrupt, every 8 frames */
{
    orig();
    int h = HALF;
    volatile s32 *tx = TXBUF + 32 * h, *rx = RXBUF + 16 * h;
    for (int f = 0; f < 8; f++) {
        s32 l = rx[2 * f], r = rx[2 * f + 1];
        tx[4 * f + 0] = l; tx[4 * f + 2] = r;       /* headphones: the input only */
        if (p_tail != p_head) {                     /* line out: the track */
            u32 t = p_tail & (PRING - 1);
            tx[4 * f + 1] = pring[t][0]; tx[4 * f + 3] = pring[t][1];
            p_tail++;
        } else {
            tx[4 * f + 1] = 0; tx[4 * f + 3] = 0;
            if (!src_done) underruns++;
        }
        if (recording) {
            if (r_head - r_tail < RRING) {
                u32 k = r_head & (RRING - 1);
                rring[k][0] = l; rring[k][1] = r;
                r_head++;
            } else overruns++;
        }
    }
    blocks++;
}

static u8 io[CHUNK * 8 * WAV_CH_MAX / 2];           /* card buffer: CHUNK frames of the source, or of the output */
static int fin, fout;
static u32 src_left;                                /* source frames not yet read */
static s32 to_s32(float v)
{
    float x = v * 8388608.0f;
    x += x >= 0 ? 0.5f : -0.5f;
    if (x > 8388607.0f) x = 8388607.0f; else if (x < -8388608.0f) x = -8388608.0f;
    return (s32)x << 8;
}
/* fill the play ring from the source while there is room for a chunk; 0 on a read error */
static int feed(void)
{
    while (src_left && PRING - (p_head - p_tail) >= CHUNK) {
        u32 n = src_left < CHUNK ? src_left : CHUNK, got;
        if (r8_read(fin, io, n * src.ba, &got) || got != n * src.ba) return 0;
        for (u32 i = 0; i < n; i++) {
            const u8 *fr = io + i * src.ba;
            u32 k = (p_head + i) & (PRING - 1);
            pring[k][0] = to_s32(wav_sample(&src, fr, 0));
            pring[k][1] = to_s32(wav_sample(&src, fr, src.ch > 1 ? 1 : 0));
        }
        p_head += n; src_left -= n;
        if (!src_left) src_done = 1;
    }
    return 1;
}
/* write recorded frames to the output (24-bit packed), skipping the first LATENCY; all = also a partial chunk */
static u32 skip, written;
static int drain(int all)
{
    for (;;) {
        u32 avail = r_head - r_tail;
        if (!avail || (!all && avail < CHUNK)) return 1;
        u32 n = avail < CHUNK ? avail : CHUNK, m = 0, w;
        for (u32 i = 0; i < n; i++) {
            u32 k = (r_tail + i) & (RRING - 1);
            if (skip) { skip--; continue; }
            u8 *d = io + 6 * m++;
            for (int c = 0; c < 2; c++) {
                s32 v = rring[k][c] >> 8;
                d[3 * c] = (u8)v; d[3 * c + 1] = (u8)(v >> 8); d[3 * c + 2] = (u8)(v >> 16);
            }
        }
        r_tail += n;
        if (m && (r8_write(fout, io, m * 6, &w) || w != m * 6)) return 0;
        written += m;
    }
}

static u8 hdr[44];
static char msg[40];
static void run(int row)
{
    (void)row;
    u32 rate = r8_sample_rate();
    if (src.rate != rate) {
        char *e = put_s(msg, "RATE "); e = put_u(e, src.rate, 0); e = put_s(e, " NOT "); put_u(e, rate, 0);
        r8_popup(msg); return;
    }
    if (r8_card_check(11)) return;
    void (*o)(void) = HOOK;
    u32 oa = (u32)o;
    if (!((oa >= 0x11800000u && oa < 0x11840000u) || (oa >= 0xC0700000u && oa < 0xC0800000u))) { r8_popup("NO AUDIO HOOK"); return; }
    if (r8_open(&fin, path, 0, 0)) { r8_popup(R8_STR_ACCESS_ERROR); return; }
    const char *name;
    int made = out_create(&fout, dst, &name, path, lname, SUFFIX, 'R');
    if (!made) { r8_close(fin); r8_popup("NO FREE NAME"); return; }
    wav_header(hdr, rate, 24, 2, 1, 0);
    u32 w;
    int ok = r8_write(fout, hdr, 44, &w) == 0 && w == 44 && r8_seek(fin, (s32)src.doff, 2) == 0;
    p_head = p_tail = r_head = r_tail = 0; underruns = overruns = blocks = 0;
    src_left = src.frames; src_done = 0; skip = LATENCY; written = 0;
    ok = ok && feed();                              /* a full play ring before starting */
    int stopped = 0;
    if (ok) {
        r8_pre_msg();
        r8_show_msg("REAMP: STOP ENDS");
        orig = o;
        recording = 1;
        HOOK = block;                               /* playing and recording from the next block */
        while (ok && !stopped) {
            ok = feed() && drain(0);
            int ev = r8_ui_event(1);
            if (ev == R8_EV_STOP || ev == R8_EV_EXIT) stopped = 1;
        }
        recording = 0;
        HOOK = o;                                   /* stock audio again */
        r8_dialog_close(ok);
    }
    ok = ok && drain(1);
    r8_close(fin);
    if (ok) {                                       /* the real length in the header */
        wav_header(hdr, rate, 24, 2, 1, written);
        ok = r8_seek(fout, 0, 2) == 0 && r8_write(fout, hdr, 44, &w) == 0 && w == 44;
    }
    if (r8_close(fout)) ok = 0;
    if (!ok) { r8_delete(dst); r8_popup(R8_STR_ACCESS_ERROR); return; }
    char *e = put_s(msg, made == 2 ? name : "SAVED");   /* the long name would not fit; an 8.3 one is shown */
    if (underruns || overruns) { e = put_s(e, " XRUN "); put_u(e, underruns + overruns, 0); }
    r8_popup(msg);
}

static const char *val_len(const u32 *s)
{
    (void)s;
    static char b[16];
    u32 cs = src.frames / src.rate * 100 + src.frames % src.rate * 100 / src.rate;
    char *d = put_u(b, cs / 6000, 0); d = put_s(d, ":"); d = put_u(d, cs / 100 % 60, 2); d = put_s(d, ".");
    put_u(d, cs % 100, 2);
    return b;
}
static const menu_item rows[] = {
    { "START", 0, 0, 0, 0, 0, 0 },
    { "LENGTH", val_len, 0, 0, 0, 0, 0 },
};
static void picked(const char *p, const u8 *e)
{
    put_s(path, p);
    const char *ln = entry_name(e);
    int k = 0; for (; ln[k] && k < (int)sizeof lname - 1; k++) lname[k] = ln[k];
    lname[k] = 0;
    for (k = 0; ln[k] && k < (int)sizeof title - 1; k++) title[k] = ln[k];
    title[k] = 0;
    if (!wav_parse(path, &src)) { r8_popup(R8_STR_INVALID_FILE); return; }
    show_items(title, rows, 2, run, 0);
}

u32 r8_main(u32 a, u32 b)
{
    (void)a; (void)b;
    browse("A:", "REAMP", picked);
    return 0;
}
