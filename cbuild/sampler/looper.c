/* Four-loop variable-speed player (looper.h).
   Audio path: McASP0 transmit as stock (serializers 2 and 4, I2S, 24-bit in 32-bit slots), but fed through the
   AFIFO write FIFO (64 words = 16 frames for two serializers) from its data port. Timer 1 (24 MHz input) raises
   event 40 at 8 kHz; the INTC routes it to CPU INT15; the ISR renders 4-frame blocks while the FIFO has room for
   them (about 5.5 frames are used per tick, so the FIFO stays 12-16 frames full: ~270 us of slack).
   The ISR also reads the panel UART, so faders keep working while Forth, USB or the SD card keep the main loop busy.
   Input: McASP0 receive as stock (serializer 1, same slots and clocks), read through the AFIFO read FIFO (64 words)
   from the same data port. The ISR first moves whole frames from the FIFO into a small ring; render() takes one input
   frame per output frame (receive and transmit share one clock, so the counts match), mixes it in at the monitor
   level and, while a track records, stores it as 16-bit stereo in that track's slot. */
#include "looper.h"
#include "core.h"
#include "stockstate.h"
#include "panel.h"
#include "voice.h"
#include "fat.h"
#include "clock.h"
#include "msc.h"
#include "fadergain.h"
#include <c6x.h>

extern cregister volatile unsigned int ISTP;
#define M(o) REG(MCASP0 + (o))
#define GBLCTL 0x44
#define RGBLCTL 0x60
#define XGBLCTL 0xA0
#define XMASK 0xA4
#define XFMT 0xA8
#define AFSXCTL 0xAC
#define ACLKXCTL 0xB0
#define AHCLKXCTL 0xB4
#define XTDM 0xB8
#define XINTCTL 0xBC
#define XSTAT 0xC0
#define XCLKCHK 0xC8
#define XEVTCTL 0xCC
#define SRCTL(n) (0x180 + 4 * (n))
#define PFUNC 0x10
#define PDIR 0x14
#define AMUTE 0x48
#define DLBCTL 0x4C
#define DITCTL 0x50
#define WFIFOCTL 0x1010
#define WFIFOSTS 0x1014
#define RFIFOCTL 0x1018
#define RFIFOSTS 0x101C
#define RMASK 0x64
#define RFMT 0x68
#define AFSRCTL 0x6C
#define ACLKRCTL 0x70
#define AHCLKRCTL 0x74
#define RTDM 0x78
#define RINTCTL 0x7C
#define RSTAT 0x80
#define RCLKCHK 0x88
#define REVTCTL 0x8C
/* receive side as stock (aio.c); RFMT.RBUSEL = 0 here: words come through the data port */
#define STOCK_RMASK     0xFFFFFF00u
#define STOCK_RFMT      0x000180F0u
#define STOCK_AFSRCTL   0x00000113u
#define STOCK_ACLKRCTL  0x0000002Fu
#define STOCK_AHCLKRCTL 0x00000000u
#define STOCK_RTDM      0x00000003u
#define STOCK_RCLKCHK   0x02FF0008u
#define STOCK_SRCTL1    0x0000000Eu
#define R_HCLKRST (1u << 1)
#define R_CLKRST  (1u << 0)
#define R_SRCLR   (1u << 2)
#define R_SMRST   (1u << 3)
#define R_FRST    (1u << 4)
#define DATAPORT 0x01D02000u
#define X_HCLKRST (1u << 9)
#define X_CLKRST  (1u << 8)
#define X_SRCLR   (1u << 10)
#define X_SMRST   (1u << 11)
#define X_FRST    (1u << 12)
#define TIMER1 0x01C21000u
#define INTC   0x01800000u
#define ISR_HZ 8000u
#define BLK 4                               /* frames per render block */
#define FIFO_WORDS 64
#define LOOP_MEM 0xC0400000u                /* loops live in DDR from here (below: MSC and test buffers) */
#define LOOP_END 0xC4000000u

int lp_running;
const char *lp_err[LP_VOICES];              /* why the last load into a track failed (shown on the LCD), or 0 */
volatile u32 lp_ints, lp_underruns, lp_isr_max, lp_isr_cycles, lp_frames;
volatile u32 lp_in_frames, lp_in_overruns, lp_in_short, lp_in_drops;
volatile u32 lp_tx_fixes, lp_rx_fixes;     /* channel realignments (see tx_align / rx_align) */
static volatile int tx_align_due, rx_align_due;
volatile s32 lp_in_peak[2];
volatile int lp_rec_track = -1;           /* track being recorded, or -1 */
volatile int lp_rec_mix;                  /* 1: it records the mix of the other tracks (resample), 0: the inputs */
volatile u32 lp_rec_n, lp_rec_max;        /* frames recorded / room in the slot */
#define REC_OFF 512u                      /* a recording's samples start here in the slot; its WAV header (44 bytes)
                                             goes just before them, so header + samples are one buffer for SAVE */
static int is_rec[LP_VOICES];             /* the track plays an unsaved recording (or one saved as lp_names) */
char lp_names[LP_VOICES][40];
u32 lp_len[LP_VOICES], lp_rate[LP_VOICES], lp_ch[LP_VOICES];
static voice_t vc[LP_VOICES];
/* per track: EMPTY (nothing loaded or a failed load), PLAY, SELECT (its key under the screen held: silent, the
   wheel picks a file), LOAD (key released: the main loop loads vsel[] into the track's slot, then PLAY) */
enum { V_EMPTY, V_PLAY, V_SELECT, V_LOAD, V_REC };
static volatile int vstate[LP_VOICES], vsel[LP_VOICES], vcur[LP_VOICES];
static u32 sel_prev;
#define MAXFILES 64
static fat_ent files[MAXFILES];
static int nfiles;
#define SLOT_BYTES ((LOOP_END - LOOP_MEM) / LP_VOICES)   /* 15 MB of file per track */
static u8 *slot(int i) { return (u8 *)(LOOP_MEM + (u32)i * SLOT_BYTES); }
static void (*lp_pollfn)(void);
static float lvl_t[LP_VOICES], lvl[LP_VOICES], mst_t;
static volatile float mst;
static volatile int restart_req;
static volatile int rec_armed;              /* REC pressed: the next key under the screen records its track */
static float mon_t, mon;                    /* input monitor level (target, current) */
#define IN_RING 64                          /* frames */
static s32 in_l[IN_RING], in_r[IN_RING];
static volatile u32 in_w, in_rd;            /* ring write / read counts */

static float glide(float g, float t)        /* one block of gain smoothing (~9 ms), landing exactly on t */
{
    float d = t - g;
    return d > -1e-5f && d < 1e-5f ? t : g + d * 0.01f;
}
static float curve(int v) { float x = (float)v / 127.0f; return x * x; }   /* MONITOR's scale: 127 = unity */
static float fader_db(int v) { return fader_gain[v < 0 ? 0 : v > 127 ? 127 : v]; }   /* faders: 100 = 0 dB, 127 = +12 dB */
static void apply(int f, int v)
{
    if (f == 8) { mst_t = fader_db(v); return; }
    int i = f >> 1;
    if (f & 1) lvl_t[i] = fader_db(v); else voice_speed(&vc[i], fader_speed(v));
}

/* ---- McASP transmit through the FIFO */
static int xgbl(u32 bits)                   /* set transmit GBLCTL bits, wait (<= 5 ms) for them to latch */
{
    M(XGBLCTL) = (M(GBLCTL) & 0x1F00u) | bits;
    u32 t0 = TSCL;
    while ((M(GBLCTL) & bits) != bits) if (TSCL - t0 > CPU_HZ / 200) return 0;
    return 1;
}
static int tx_start(void)                   /* from transmit reset (clocks may run): FIFO on, prime, go */
{
    M(WFIFOCTL) = 0x1002;                   /* WNUMEVT 16, WNUMDMA 2 (serializers 2 and 4), off: flushes */
    M(WFIFOCTL) = 0x11002;                  /* WENA */
    int ok = xgbl(X_HCLKRST) && xgbl(X_CLKRST);
    M(XSTAT) = 0xFFFF;
    ok = ok && xgbl(X_SRCLR);
    for (int i = 0; i < 32; i++) REG(DATAPORT) = 0;  /* 8 frames of silence */
    ok = ok && xgbl(X_SMRST) && xgbl(X_FRST);
    tx_align_due = 1;
    return ok;
}
static void tx_restart(void)                /* after an underrun: the transmitter must be restarted (aio.c) */
{
    M(XGBLCTL) = X_HCLKRST | X_CLKRST;
    u32 t0 = TSCL;
    while (M(GBLCTL) & (X_SRCLR | X_SMRST | X_FRST)) if (TSCL - t0 > CPU_HZ / 200) break;
    tx_start();
}
static int rgbl(u32 bits)                   /* set receive GBLCTL bits, wait (<= 5 ms) for them to latch */
{
    M(RGBLCTL) = (M(GBLCTL) & 0x1Fu) | bits;
    u32 t0 = TSCL;
    while ((M(GBLCTL) & bits) != bits) if (TSCL - t0 > CPU_HZ / 200) return 0;
    return 1;
}
/* Receiver from reset, with the transmitter's clocks running (ACLKXCTL.ASYNC = 0: it uses them). Which slot the
   first FIFO word comes from is NOT fixed (seen on the R8: some sessions had left and right swapped, on the
   transmit side too), so rx_align() checks it against RSTAT.RTDMSLT before the first frame is used. */
static int rx_start(void)
{
    M(RGBLCTL) = 0;
    M(RFIFOCTL) = 0x0101;                   /* RNUMEVT 1, RNUMDMA 1 (serializer 1), off: flushes */
    M(RFIFOCTL) = 0x10101;                  /* RENA */
    in_w = in_rd = 0;
    int ok = rgbl(R_HCLKRST) && rgbl(R_CLKRST);
    M(RSTAT) = 0xFFFF;
    rx_align_due = 1;
    return ok && rgbl(R_SRCLR) && rgbl(R_SMRST) && rgbl(R_FRST);
}
static int mcasp_init(void)
{
    psc_enable(PSC1, 7);
    M(GBLCTL) = 0; M(RGBLCTL) = 0; M(XGBLCTL) = 0;
    M(WFIFOCTL) = 0;
    M(XMASK) = STOCK_XMASK; M(XFMT) = STOCK_XFMT & ~(1u << 3);    /* XBUSEL = 0: data through the data port */
    M(AFSXCTL) = STOCK_AFSXCTL; M(ACLKXCTL) = STOCK_ACLKXCTL; M(AHCLKXCTL) = STOCK_AHCLKXCTL;
    M(XTDM) = STOCK_XTDM; M(XINTCTL) = 0; M(XCLKCHK) = STOCK_XCLKCHK;
    M(XEVTCTL) = 0;                         /* XDATDMA = 0: the McASP's request is what pulls words out of the FIFO */
    M(RFIFOCTL) = 0;
    M(RMASK) = STOCK_RMASK; M(RFMT) = STOCK_RFMT & ~(1u << 3);    /* RBUSEL = 0: data port */
    M(AFSRCTL) = STOCK_AFSRCTL; M(ACLKRCTL) = STOCK_ACLKRCTL; M(AHCLKRCTL) = STOCK_AHCLKRCTL;
    M(RTDM) = STOCK_RTDM; M(RINTCTL) = 0; M(RCLKCHK) = STOCK_RCLKCHK;
    M(REVTCTL) = 0;                         /* RDATDMA = 0, as XDATDMA: the request fills the read FIFO */
    for (int n = 0; n < 16; n++) M(SRCTL(n)) = 0;
    M(SRCTL(1)) = STOCK_SRCTL1; M(SRCTL(2)) = STOCK_SRCTL2; M(SRCTL(4)) = STOCK_SRCTL4;
    M(PFUNC) = STOCK_PFUNC; M(PDIR) = STOCK_PDIR;
    M(DITCTL) = 0; M(DLBCTL) = 0; M(AMUTE) = 0;
    return tx_start() && rx_start();
}
static void mcasp_off(void) { M(RGBLCTL) = 0; M(RFIFOCTL) = 0x0101; M(XGBLCTL) = 0; M(WFIFOCTL) = 0x1002; }
/* Channel alignment, from the McASP's own slot counter rather than from counting words since start-up.
   XSTAT/RSTAT bit 3 (XTDMSLT/RTDMSLT) = 1 while the slot on the wire is even (left; I2S slot 0). Both are read
   around the FIFO level and the reading is retried if a slot boundary passed in between.
   Transmit: the slot on the wire is s, XBUF already holds s + 1, so the next group (2 words: serializers 2 and 4)
   written to the FIFO goes out in slot s + 2 + W/2 (W = words queued). If that is a right slot, one silent slot is
   written first. Receive: the FIFO holds the R completed slots before the one being received (c), so its oldest
   word is from slot c - R; if that is a right slot, one word is dropped. */
static void tx_align(void)
{
    for (int t = 0; t < 4; t++) {
        u32 x1 = M(XSTAT), w = M(WFIFOSTS) & 0xFF, x2 = M(XSTAT);
        if ((x1 ^ x2) & 8) continue;
        u32 s_odd = !(x1 & 8), next = (s_odd + w / 2) & 1;
        if (next) { REG(DATAPORT) = 0; REG(DATAPORT) = 0; lp_tx_fixes++; }
        tx_align_due = 0;
        return;
    }
}
static void rx_align(void)
{
    for (int t = 0; t < 4; t++) {
        u32 r1 = M(RSTAT), n = M(RFIFOSTS) & 0xFF, r2 = M(RSTAT);
        if ((r1 ^ r2) & 8) continue;
        if (!n) return;                                   /* nothing yet: try again next tick */
        u32 c_odd = !(r1 & 8), oldest = (c_odd + n) & 1;  /* parity of slot c - n */
        if (oldest) { (void)REG(DATAPORT); lp_rx_fixes++; }
        rx_align_due = 0;
        return;
    }
}
/* Move whole input frames from the read FIFO into the ring. If the ring backs up (render fell behind: it should
   not), keep only the newest few frames, so monitoring latency stays small. */
static void input_poll(void)
{
    if (M(RSTAT) & 1) { lp_in_overruns++; rx_start(); return; }  /* overrun: restart (then realigned) */
    if (rx_align_due) { rx_align(); if (rx_align_due) return; }
    u32 n = (M(RFIFOSTS) & 0xFF) >> 1;
    while (n--) {
        s32 a = (s32)REG(DATAPORT) >> 8, b = (s32)REG(DATAPORT) >> 8;   /* 24-bit, left-justified */
        u32 w = in_w;
        in_l[w % IN_RING] = a; in_r[w % IN_RING] = b; in_w = w + 1;
        if (a < 0) a = -a; if (b < 0) b = -b;
        if (a > lp_in_peak[0]) lp_in_peak[0] = a;
        if (b > lp_in_peak[1]) lp_in_peak[1] = b;
        lp_in_frames++;
    }
    if (in_w - in_rd > 24) { lp_in_drops += in_w - in_rd - 8; in_rd = in_w - 8; }
}

/* ---- the interrupt */
static void render(void)
{
    float l[BLK], r[BLK];
    for (int j = 0; j < BLK; j++) l[j] = r[j] = 0.0f;
    for (int i = 0; i < LP_VOICES; i++) {
        int st = vstate[i];
        if (st == V_SELECT && lvl[i] == 0.0f) continue;          /* key held: faded out, silent */
        if (st != V_PLAY && st != V_SELECT) continue;           /* empty, or its slot is being loaded */
        lvl[i] = glide(lvl[i], st == V_PLAY ? lvl_t[i] : 0.0f); /* a press fades the track out (~10 ms) */
        vc[i].gain = lvl[i];
        voice_render(&vc[i], l, r, BLK);   /* also advances silent voices, so they stay in time */
    }
    mst = glide(mst, mst_t);
    mon = glide(mon, mon_t);
    int rt = lp_rec_track;
    short *rec = rt >= 0 ? (short *)(slot(rt) + REC_OFF) : 0;
    for (int j = 0; j < BLK; j++) {
        s32 il = 0, ir = 0;
        if (in_rd != in_w) { il = in_l[in_rd % IN_RING]; ir = in_r[in_rd % IN_RING]; in_rd++; } else lp_in_short++;
        if (rec && lp_rec_n < lp_rec_max) {
            u32 k = lp_rec_n++;
            if (lp_rec_mix) {                        /* the other tracks at their levels, before MASTER and the input */
                float a = l[j] * 32767.0f, b = r[j] * 32767.0f;
                if (a > 32767.0f) a = 32767.0f; else if (a < -32767.0f) a = -32767.0f;
                if (b > 32767.0f) b = 32767.0f; else if (b < -32767.0f) b = -32767.0f;
                rec[2 * k] = (short)a; rec[2 * k + 1] = (short)b;
            } else { rec[2 * k] = (short)(il >> 8); rec[2 * k + 1] = (short)(ir >> 8); }
        }
        l[j] += (float)il * (mon * (1.0f / 8388608.0f)); r[j] += (float)ir * (mon * (1.0f / 8388608.0f));
        float a = l[j] * mst, b = r[j] * mst;
        if (a > 1.0f) a = 1.0f; else if (a < -1.0f) a = -1.0f;
        if (b > 1.0f) b = 1.0f; else if (b < -1.0f) b = -1.0f;
        u32 wa = (u32)(s32)(a * 8388607.0f) << 8, wb = (u32)(s32)(b * 8388607.0f) << 8;
        REG(DATAPORT) = wa; REG(DATAPORT) = wa;      /* left slot: serializer 2, serializer 4 */
        REG(DATAPORT) = wb; REG(DATAPORT) = wb;      /* right slot */
    }
    lp_frames += BLK;
}
interrupt void lp_isr(void)
{
    u32 t0 = TSCL;
    lp_ints++;
    REG(TIMER1 + 0x44) = 3;                          /* PRDINTSTAT12 (sticky status) */
    panel_poll();
    u32 ch = fader_changed; fader_changed = 0;
    for (int f = 0; f < NFADERS; f++) if (ch & (1u << f)) apply(f, fader[f]);
    u32 down = sel_down, chg = down ^ sel_prev; sel_prev = down;
    int wd = wheel_delta; wheel_delta = 0;
    /* REC: arm (again: disarm); while recording, REC stops it. Armed, a key under the screen records its track;
       while recording, that track's key stops it (other keys are ignored until then). */
    if (key_pressed & 2) {
        key_pressed &= ~2u;
        if (lp_rec_track >= 0) lp_rec_stop(); else rec_armed = !rec_armed;
    }
    /* A track status key (either of the two above a track's faders) records the mix of the other tracks into that
       track; pressed again (either key) it stops. While another recording runs, the keys are ignored. */
    u32 tp = trk_pressed; trk_pressed = 0;
    for (int k = 0; k < 8 && tp; k++) if (tp & (1u << k)) {
        int t = k >> 1;
        if (lp_rec_track == t) lp_rec_stop();
        else if (lp_rec_track < 0) { rec_armed = 0; lp_rec_start_mix(t); }
        break;
    }
    for (int i = 0; i < LP_VOICES; i++) {
        u32 bit = 1u << i;
        if ((chg & bit) && (down & bit) && (rec_armed || lp_rec_track >= 0)) {
            if (lp_rec_track == i) lp_rec_stop();
            else if (lp_rec_track < 0) { rec_armed = 0; lp_rec_start(i); }
            continue;                                /* consumed: no file selection for this press */
        }
        if (chg & bit) {
            if (down & bit) { vstate[i] = V_SELECT; vsel[i] = vcur[i]; }          /* silent while held */
            else if (vstate[i] == V_SELECT) vstate[i] = nfiles ? V_LOAD : V_EMPTY; /* released: load the pick */
        }
        if ((down & bit) && wd && nfiles) { int k = (vsel[i] + wd) % nfiles; vsel[i] = k < 0 ? k + nfiles : k; }
    }
    if ((key_pressed & 1) || restart_req) { key_pressed &= ~1u; restart_req = 0; for (int i = 0; i < LP_VOICES; i++) vc[i].pos = 0; }
    input_poll();
    if (M(XSTAT) & 1) { lp_underruns++; tx_restart(); }
    if (tx_align_due) tx_align();
    int room = FIFO_WORDS - (int)(M(WFIFOSTS) & 0xFF);
    while (room >= 4 * BLK) { render(); room -= 4 * BLK; }
    u32 dt = TSCL - t0;
    lp_isr_cycles += dt; if (dt > lp_isr_max) lp_isr_max = dt;
}

/* ---- loading */
static int up(int c) { return c >= 'a' && c <= 'z' ? c - 32 : c; }
static int name_lt(const char *a, const char *b)
{
    for (; *a && up(*a) == up(*b); a++, b++) ;
    return up(*a) < up(*b);
}
static int is_wav(const fat_ent *e)
{
    if (e->attr & 0x18 || e->name[0] == '.' || e->size < 48) return 0;     /* dirs, labels, "._x" Mac files */
    int n = 0; while (e->name[n]) n++;
    return n > 4 && e->name[n - 4] == '.' && up(e->name[n - 3]) == 'W' && up(e->name[n - 2]) == 'A' && up(e->name[n - 1]) == 'V';
}
static u32 le16(const u8 *p) { return p[0] | p[1] << 8; }
static u32 le32(const u8 *p) { return p[0] | p[1] << 8 | p[2] << 16 | (u32)p[3] << 24; }
/* parse the WAV loaded at buf (n bytes); 24-bit is converted to 16-bit in place. Returns 0 or an error text */
static const char *wav_parse(u8 *buf, u32 n, int i)
{
    if (n < 44 || le32(buf) != 0x46464952u || le32(buf + 8) != 0x45564157u) return "not a WAV";
    u32 p = 12, ch = 0, rate = 0, bits = 0, fmt = 0;
    while (p + 8 <= n) {
        u32 id = le32(buf + p), len = le32(buf + p + 4);
        if (id == 0x20746D66u && len >= 16) {
            fmt = le16(buf + p + 8); ch = le16(buf + p + 10); rate = le32(buf + p + 12); bits = le16(buf + p + 22);
            if (fmt == 0xFFFE && len >= 26) fmt = le16(buf + p + 32);   /* WAVE_FORMAT_EXTENSIBLE: the sub-format */
        }
        if (id == 0x61746164u) {
            if (fmt != 1 && fmt != 3) return "format not PCM/float";
            if (ch < 1 || ch > 2) return "not mono/stereo";
            if (rate < 8000 || rate > 96000) return "odd rate";
            u32 off = p + 8; if (len > n - off) len = n - off;
            u32 frames;
            const short *d;
            if (bits == 16) { frames = len / (2 * ch); d = (const short *)(buf + off); }
            else if (bits == 24) {
                frames = len / (3 * ch);
                short *o = (short *)buf; const u8 *s = buf + off;
                for (u32 k = 0; k < frames * ch; k++) o[k] = (short)(s[3 * k + 1] | s[3 * k + 2] << 8);
                d = o;
            } else if (bits == 32) {                 /* 32-bit float (format 3) or integer (1): to 16-bit in place */
                frames = len / (4 * ch);
                short *o = (short *)buf; const u8 *s = buf + off;
                for (u32 k = 0; k < frames * ch; k++) {
                    u32 w = le32(s + 4 * k);
                    if (fmt == 3) {
                        union { u32 u; float f; } v; v.u = w;
                        float x = v.f * 32767.0f;
                        if (!(x == x)) x = 0.0f;        /* NaN */
                        if (x > 32767.0f) x = 32767.0f; else if (x < -32767.0f) x = -32767.0f;
                        o[k] = (short)x;
                    } else o[k] = (short)((s32)w >> 16);
                }
                d = o;
            } else return "bits not 16/24/32";
            if (fmt == 3 && bits != 32) return "float not 32-bit";
            if (frames < 4) return "too short";
            voice_init(&vc[i], d, frames, (int)ch, rate, clk_rate);
            vc[i].smooth = 0.00625f;         /* per 4-frame block: ~15 ms time constant */
            voice_speed(&vc[i], 1.0f);
            lp_len[i] = frames; lp_rate[i] = rate; lp_ch[i] = ch;
            return 0;
        }
        p += 8 + len + (len & 1);
    }
    return "no data";
}
static void noop(const char *m) { (void)m; }
static void msg_u(void (*msg)(const char *), u32 v)
{
    char t[12], *q = t + 11; *q = 0;
    do { *--q = (char)('0' + v % 10); v /= 10; } while (v);
    msg(q);
}
static int scan_files(void (*msg)(const char *))         /* the sorted WAV list of ZOOM_R8/LOOP */
{
    fat_ent dir, e; u32 pos = 0;
    nfiles = 0;
    int r = fat_mount();
    if (r) { msg(r == FAT_SD ? " no SD card" : " card is not FAT32"); return -1; }
    if (fat_find("ZOOM_R8/LOOP", &dir) || !(dir.attr & 0x10)) { msg(" no ZOOM_R8/LOOP folder"); return -1; }
    while (fat_next(&dir, &pos, &e)) {
        if (!is_wav(&e)) continue;
        int k = nfiles < MAXFILES ? nfiles++ : MAXFILES - 1;
        if (k == MAXFILES - 1 && !name_lt(e.name, files[k].name)) continue;
        while (k && name_lt(e.name, files[k - 1].name)) { fat_copy(&files[k], &files[k - 1]); k--; }
        fat_copy(&files[k], &e);
    }
    if (!nfiles) msg(" no WAV files in ZOOM_R8/LOOP");
    return nfiles;
}
/* Load file k into track i's slot. The track must not be playing. Returns 0 or an error text. */
static const char *load_slot(int i, int k)
{
    if (k < 0 || k >= nfiles) return "no file";
    if (files[k].size > SLOT_BYTES) return "too big";
    if (fat_read(&files[k], slot(i), files[k].size, lp_pollfn)) return "read error";
    const char *err = wav_parse(slot(i), files[k].size, i);
    if (err) return err;
    for (int c = 0; c < 40; c++) lp_names[i][c] = files[k].name[c];
    is_rec[i] = 0;
    return 0;
}
static void reapply(int i)                   /* the track's speed and level faders, after a new voice_init */
{
    if (fader_known & (1u << (2 * i))) voice_speed(&vc[i], fader_speed(fader[2 * i]));
    if (fader_known & (1u << (2 * i + 1))) lvl_t[i] = fader_db(fader[2 * i + 1]);
    lvl[i] = 0.0f;                           /* fade in from silence */
}
static int load_all(void (*msg)(const char *))
{
    for (int i = 0; i < LP_VOICES; i++) { vstate[i] = V_EMPTY; vcur[i] = vsel[i] = i; lp_names[i][0] = 0; lp_len[i] = 0; is_rec[i] = 0; lp_err[i] = 0; }
    if (scan_files(msg) < 0) return -1;
    int got = 0;
    for (int k = 0; k < nfiles && got < LP_VOICES; k++) {
        msg("\r\n "); msg(files[k].name); msg(": ");
        const char *err = load_slot(got, k);
        if (err) { msg(err); continue; }
        msg_u(msg, lp_len[got] * 10 / lp_rate[got] / 10); msg("."); msg_u(msg, lp_len[got] * 10 / lp_rate[got] % 10);
        msg(" s, "); msg_u(msg, lp_rate[got]); msg(lp_ch[got] == 2 ? " Hz stereo" : " Hz mono");
        msg(" -> loop "); msg_u(msg, (u32)got + 1);
        vcur[got] = vsel[got] = k; vstate[got] = V_PLAY; got++;
    }
    return got;
}
/* Main-loop side of the select keys: load what a released key picked (called from forth_poll while running). */
/* REC LED (panel command 88: 8 LED bytes, REC = byte 4 bit 2): blinks while armed, on while recording. Sent from
   the main loop, not the ISR: a message takes ~0.7 ms on the UART, longer than the output FIFO's slack. */
static int rec_led = -1;
static void rec_led_set(int on)              /* on: REC LED; plus the status keys of a track recording the mix */
{
    int st = on | (lp_rec_track >= 0 && lp_rec_mix ? (lp_rec_track + 1) << 1 : 0);
    if (st == rec_led) return;
    rec_led = st;
    u8 m[12] = { 0x88, 0x01, 0x08, 0, 0, 0, 0, 0, 0, 0, 0, 0xF7 };   /* b0..b7: REC = b4 bit 2 */
    if (lp_rec_track >= 0 && lp_rec_mix) {   /* both status keys above the track (k = 2t, 2t+1) red: byte 2(k%4), bit k/4 */
        for (int k = 2 * lp_rec_track; k <= 2 * lp_rec_track + 1; k++) m[3 + 2 * (k % 4)] |= (u8)(1u << (k / 4));
    } else if (on) m[7] = 4;
    panel_send(m, 12);
}
void lp_service(void)
{
    static int busy;
    if (!lp_running || busy) return;
    busy = 1;
    rec_led_set(lp_rec_track >= 0 ? !lp_rec_mix : rec_armed ? (int)(TSCL / (CPU_HZ / 4)) & 1 : 0);
    for (int i = 0; i < LP_VOICES; i++) {
        if (vstate[i] != V_LOAD) continue;
        int k = vsel[i];
        lp_len[i] = 0;                       /* the screen shows "loading" */
        const char *err = load_slot(i, k);
        unsigned cs = _disable_interrupts();
        if (vstate[i] == V_LOAD && vsel[i] == k) {          /* not re-pressed meanwhile */
            lp_err[i] = err;
            if (err) { vstate[i] = V_EMPTY; lp_names[i][0] = 0; }
            else { vcur[i] = k; reapply(i); vc[i].pos = 0; vstate[i] = V_PLAY; }
        }
        _restore_interrupts(cs);
    }
    busy = 0;
}
int lp_track_state(int i) { return vstate[i]; }               /* 0 empty, 1 play, 2 selecting, 3 loading */
int lp_track_pick(int i) { return vsel[i]; }
int lp_track_file(int i) { return vcur[i]; }
int lp_nfiles(void) { return nfiles; }
const char *lp_file_name(int k) { return k >= 0 && k < nfiles ? files[k].name : ""; }
void lp_load_request(int i, int k)                            /* as if key i picked file k and was released */
{
    if (i < 0 || i >= LP_VOICES || k < 0 || k >= nfiles) return;
    unsigned cs = _disable_interrupts();
    vsel[i] = k; vstate[i] = V_LOAD;
    _restore_interrupts(cs);
    lp_service();
}

/* ---- start / stop */
extern void lp_vectors(void);
/* The panel link belongs to the Forth core (core.c), shared with its words */
void lp_panel_up(void) { core_panel_up(); }
int lp_panel_is_up(void) { return core_panel_is_up(); }
void lp_faders_sync(void) { core_faders_sync(); }
int lp_start(void (*msg)(const char *s), void (*poll)(void))
{
    if (lp_running) lp_stop();
    lp_pollfn = poll;
    int got = load_all(msg);
    if (got < 0) return -1;
    for (int i = 0; i < LP_VOICES; i++) { lvl_t[i] = lvl[i] = 0.0f; }
    mst_t = mst = fader_db(100);                       /* 0 dB until MASTER moves */
    lp_rec_track = -1; rec_armed = 0; lp_in_frames = lp_in_overruns = lp_in_short = lp_in_drops = 0;
    lp_tx_fixes = lp_rx_fixes = 0;
    for (int f = 0; f < NFADERS; f++) if (fader_known & (1u << f)) apply(f, fader[f]);   /* positions known so far */
    if (!core_panel_is_up()) core_panel_up();         /* its start-up includes the snapshot request */
    else panel_request();                              /* faders: current positions, answered into the ISR */
    if (!mcasp_init()) { mcasp_off(); msg(" no audio clock"); return -1; }
    /* timer 1: 12-bit half, continuous, 24 MHz / 3000 = 8 kHz -> event 40 -> INT15 */
    REG(TIMER1 + 0x20) = 0;                            /* TCR: stop */
    REG(TIMER1 + 0x24) |= 1;                           /* TGCR: TIM12 out of reset (stock: 5) */
    REG(TIMER1 + 0x10) = 0;                            /* TIM12 */
    REG(TIMER1 + 0x18) = 24000000u / ISR_HZ - 1;       /* PRD12 */
    REG(TIMER1 + 0x44) = 3;                            /* PRDINTEN12, clear status */
    REG(INTC + 0x10C) = (REG(INTC + 0x10C) & 0x00FFFFFFu) | (40u << 24);   /* INTMUX3: INT15 = event 40 */
    lp_ints = lp_underruns = lp_isr_max = lp_isr_cycles = lp_frames = 0;
    ISTP = (unsigned)lp_vectors;
    ICR = 1u << 15;
    IER |= (1u << 15) | 2;                             /* INT15 + NMIE (needed for any maskable interrupt) */
    sel_prev = sel_down; wheel_delta = 0;
    lp_running = 1;
    REG(TIMER1 + 0x20) = 0x80;                         /* TCR: ENAMODE12 = continuous */
    _enable_interrupts();
    return got;
}
void lp_stop(void)
{
    if (!lp_running) return;
    mst_t = 0.0f;
    u32 t0 = TSCL; while (mst != 0.0f && TSCL - t0 < CPU_HZ / 4) ;   /* the ISR fades out (~100 ms) */
    _disable_interrupts();
    IER &= ~(1u << 15);
    REG(TIMER1 + 0x20) = 0;
    mcasp_off();
    lp_running = 0;
    if (lp_rec_track >= 0) lp_rec_stop();
    rec_armed = 0; rec_led = -1; rec_led_set(0);
}
void lp_fader(int f, int v)
{
    if (f < 0 || f >= NFADERS) return;
    if (v < 0) v = 0; else if (v > 127) v = 127;
    unsigned cs = _disable_interrupts();
    fader[f] = (u8)v; fader_changed |= 1u << f; fader_known |= 1u << f;
    if (!lp_running) apply(f, v);
    _restore_interrupts(cs);
}
int lp_fader_get(int f) { return f >= 0 && f < NFADERS ? fader[f] : 0; }
void lp_out_rate(void)                       /* after clk_set_rate: resample every loaded voice for the new rate */
{
    for (int i = 0; i < LP_VOICES; i++) if (lp_rate[i]) voice_out_rate(&vc[i], lp_rate[i], clk_rate);
}
float lp_speed(int i) { return vc[i].rate ? vc[i].target / vc[i].rate : 0.0f; }
void lp_restart_loops(void) { restart_req = 1; }

/* ---- input: monitor and record */
void lp_monitor(int v) { if (v < 0) v = 0; else if (v > 127) v = 127; mon_t = curve(v); }
static int rec_start(int i, int mix);
int lp_rec_start(int i) { return rec_start(i, 0); }         /* record the inputs into track i */
int lp_rec_start_mix(int i) { return rec_start(i, 1); }     /* record the mix of the other tracks into track i */
static int rec_start(int i, int mix)        /* track i stops playing and records; 0 or error */
{
    if (!lp_running) return -1;
    if (i < 0 || i >= LP_VOICES) return -2;
    if (lp_rec_track >= 0) return -3;
    unsigned cs = _disable_interrupts();
    vstate[i] = V_REC; lp_len[i] = 0; lp_names[i][0] = 0;
    lp_rec_n = 0; lp_rec_max = (SLOT_BYTES - REC_OFF) / 4; lp_rec_mix = mix;
    lp_rec_track = i;
    _restore_interrupts(cs);
    return 0;
}
int lp_rec_stop(void)                        /* end the recording; the track loops it. Returns frames, or <0 */
{
    int i = lp_rec_track;
    if (i < 0) return -1;
    unsigned cs = _disable_interrupts();
    lp_rec_track = -1;
    u32 n = lp_rec_n;
    if (n >= 4) {
        voice_init(&vc[i], (const short *)(slot(i) + REC_OFF), n, 2, clk_rate, clk_rate);
        vc[i].smooth = 0.00625f;
        voice_speed(&vc[i], 1.0f);
        reapply(i);
        lp_len[i] = n; lp_rate[i] = clk_rate; lp_ch[i] = 2;
        const char *nm = "REC 1"; for (int c = 0; c < 6; c++) lp_names[i][c] = nm[c];
        lp_names[i][4] = (char)('1' + i);
        vcur[i] = vsel[i] = 0;
        is_rec[i] = 1; lp_err[i] = 0;
        vstate[i] = V_PLAY;
    } else vstate[i] = V_EMPTY;
    _restore_interrupts(cs);
    return (int)n;
}

/* ---- saving a recording as ZOOM_R8/LOOP/RECnnnnn.WAV (16-bit stereo at the rate it was recorded) */
static int rec_number(const char *nm)       /* "REC00012.WAV" -> 12, else -1 */
{
    if (up(nm[0]) != 'R' || up(nm[1]) != 'E' || up(nm[2]) != 'C') return -1;
    int v = 0;
    for (int i = 3; i < 8; i++) { if (nm[i] < '0' || nm[i] > '9') return -1; v = v * 10 + nm[i] - '0'; }
    return nm[8] == '.' && up(nm[9]) == 'W' && up(nm[10]) == 'A' && up(nm[11]) == 'V' && !nm[12] ? v : -1;
}
const char *lp_save(int i)
{
    if (i < 0 || i >= LP_VOICES) return "track 1-4";
    if (lp_rec_track == i) return "still recording";
    if (!is_rec[i] || vstate[i] != V_PLAY || !lp_len[i]) return "no recording on that track";
    if (msc_present && msc_writable) return "the card is writable over USB (MSC-RW): eject it on the computer first";
    if (msc_in_use) return "the computer has the card mounted: eject it there first";
    fat_ent dir, e; u32 pos = 0;
    if (fat_mount() || fat_find("ZOOM_R8/LOOP", &dir) || !(dir.attr & 0x10)) return "no ZOOM_R8/LOOP folder";
    int top = 1;                                           /* first one is REC00001 */
    while (fat_next(&dir, &pos, &e)) { int v = rec_number(e.name); if (v >= top) top = v + 1; }
    if (top > 99999) return "no free RECnnnnn name";
    char name83[11] = { 'R', 'E', 'C', 0, 0, 0, 0, 0, 'W', 'A', 'V' };
    for (int k = 7, v = top; k >= 3; k--, v /= 10) name83[k] = (char)('0' + v % 10);
    u32 frames = lp_len[i], data = frames * 4, rate = lp_rate[i];
    u8 *h = slot(i) + REC_OFF - 44, *end = slot(i) + REC_OFF + data;
    static const u8 fmt[16] = { 1, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 4, 0, 16, 0 };
    u32 riff = 36 + data;
    const u8 hdr0[4] = { 'R', 'I', 'F', 'F' }, wave[8] = { 'W', 'A', 'V', 'E', 'f', 'm', 't', ' ' }, dat[4] = { 'd', 'a', 't', 'a' };
    for (int k = 0; k < 4; k++) h[k] = hdr0[k];
    for (int k = 0; k < 4; k++) h[4 + k] = (u8)(riff >> 8 * k);
    for (int k = 0; k < 8; k++) h[8 + k] = wave[k];
    h[16] = 16; h[17] = h[18] = h[19] = 0;
    for (int k = 0; k < 16; k++) h[20 + k] = fmt[k];
    for (int k = 0; k < 4; k++) { h[24 + k] = (u8)(rate >> 8 * k); h[28 + k] = (u8)(rate * 4 >> 8 * k); }
    for (int k = 0; k < 4; k++) h[36 + k] = dat[k];
    for (int k = 0; k < 4; k++) h[40 + k] = (u8)(data >> 8 * k);
    while ((u32)(end - h) & 511) *end++ = 0;               /* the last sector: zeros after the samples */
    msc_offline = 1;                                       /* the Mac sees the card removed while we write */
    int r = fat_create(&dir, name83, h, 44 + data, lp_pollfn);
    msc_offline = 0; msc_media_changed();
    if (r == FAT_EXISTS) return "name exists or the FAT changed: not saved";
    if (r == FAT_TOOBIG) return "card or folder full: not saved";
    if (r) return "card error: not saved";
    for (int k = 0; k < 8; k++) lp_names[i][k] = name83[k];
    lp_names[i][8] = '.'; lp_names[i][9] = 'W'; lp_names[i][10] = 'A'; lp_names[i][11] = 'V'; lp_names[i][12] = 0;
    lp_rescan();                                           /* the new file joins the list the wheel picks from */
    return 0;
}
/* Re-read ZOOM_R8/LOOP (files added or removed from a computer). Tracks keep playing what they hold; only the list
   the wheel picks from changes. Not while a key under the screen is held or a load is pending. Returns the count. */
int lp_rescan(void)
{
    for (int t = 0; t < LP_VOICES; t++) if (vstate[t] == V_SELECT || vstate[t] == V_LOAD) return -1;
    if (scan_files(noop) < 0) return -1;
    for (int t = 0; t < LP_VOICES; t++) {                  /* file indices may have moved: find each track's */
        vcur[t] = 0;
        for (int k = 0; k < nfiles; k++) {
            int same = 1; for (int c = 0; c < 40 && same; c++) { same = up(files[k].name[c]) == up(lp_names[t][c]); if (!files[k].name[c]) break; }
            if (same) { vcur[t] = k; break; }
        }
    }
    return nfiles;
}
