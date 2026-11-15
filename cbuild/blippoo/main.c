/* BLIPPOO.BIN: a Blippoo Box (bp.c) as an R8 app, started from the app chooser (R8APPS).

   Controls: two banks of eight faders, chosen with the two left keys under the screen; MASTER is the output level in
   both. Bank 1 (CORE): OSC A, OSC B, SWEEP A, SWEEP B, RUNGLER A, RUNGLER B, TIME WARP, COLOUR. Bank 2 (RESONATOR):
   PEAK 1, PEAK 2, RUNGLER -> PEAKS, SPREAD, EXT (inputs 1+2 into the resonator), PING (the pulses into it), two
   spares. A fader picks its parameter up only when it reaches the parameter's value (shown with an arrow until
   then), so changing bank never makes a parameter jump. EXIT restarts into the stock R8 firmware; the power switch
   turns the R8 off.

   Audio as the looper (sampler/looper.c): McASP0 as stock but through its FIFOs from the data port; timer 1 at 8 kHz
   raises INT15; the interrupt reads the panel, applies the faders, moves input frames from the read FIFO to a
   ring, and renders 4-frame blocks while the write FIFO has room. The same signal goes to serializers 2
   (headphones) and 4 (line out).

   REC records what is heard (after MASTER) into DDR as 24-bit stereo; REC again (or EXIT, or running out of room)
   stops it and saves it as a new file ZOOM_R8/LOOP/BLIPnnnn.WAV (the card's root if there is no such folder). The
   sound keeps playing while the file is written. */
#include "hw.h"
#include "lcd.h"
#include "panel.h"
#include "font.h"
#include "stockstate.h"
#include "fadergain.h"
#include "bp.h"
#include "fat.h"
#include "battery.h"
#include <c6x.h>

extern u8 __r8_bss_start[], __r8_bss_end[];
extern cregister volatile unsigned int ISTP;
extern void bp_vectors(void);
void audio_pump(void) {}                           /* lcd.c calls it; audio runs in the interrupt here */

/* ---- McASP0 through its FIFOs (as sampler/looper.c) */
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
#define STOCK_RMASK     0xFFFFFF00u
#define STOCK_RFMT      0x000180F0u
#define STOCK_AFSRCTL   0x00000113u
#define STOCK_ACLKRCTL  0x0000002Fu
#define STOCK_AHCLKRCTL 0x00000000u
#define STOCK_RTDM      0x00000003u
#define STOCK_RCLKCHK   0x02FF0008u
#define R_HCLKRST (1u << 1)
#define R_CLKRST  (1u << 0)
#define R_SRCLR   (1u << 2)
#define R_SMRST   (1u << 3)
#define R_FRST    (1u << 4)
#define X_HCLKRST (1u << 9)
#define X_CLKRST  (1u << 8)
#define X_SRCLR   (1u << 10)
#define X_SMRST   (1u << 11)
#define X_FRST    (1u << 12)
#define DATAPORT 0x01D02000u
#define TIMER1 0x01C21000u
#define INTC   0x01800000u
#define ISR_HZ 8000u
#define BLK 4
#define FIFO_WORDS 64
#define IN_RING 64

static volatile int tx_align_due, rx_align_due;
static s32 in_l[IN_RING], in_r[IN_RING];
static volatile u32 in_w, in_rd;
volatile u32 bp_ints, bp_underruns, bp_isr_max, bp_isr_cycles, bp_frames, bp_in_short;

static int xgbl(u32 bits)
{
    M(XGBLCTL) = (M(GBLCTL) & 0x1F00u) | bits;
    u32 t0 = TSCL;
    while ((M(GBLCTL) & bits) != bits) if (TSCL - t0 > CPU_HZ / 200) return 0;
    return 1;
}
static int tx_start(void)
{
    M(WFIFOCTL) = 0x1002;
    M(WFIFOCTL) = 0x11002;
    int ok = xgbl(X_HCLKRST) && xgbl(X_CLKRST);
    M(XSTAT) = 0xFFFF;
    ok = ok && xgbl(X_SRCLR);
    for (int i = 0; i < 32; i++) REG(DATAPORT) = 0;
    ok = ok && xgbl(X_SMRST) && xgbl(X_FRST);
    tx_align_due = 1;
    return ok;
}
static void tx_restart(void)
{
    M(XGBLCTL) = X_HCLKRST | X_CLKRST;
    u32 t0 = TSCL;
    while (M(GBLCTL) & (X_SRCLR | X_SMRST | X_FRST)) if (TSCL - t0 > CPU_HZ / 200) break;
    tx_start();
}
static int rgbl(u32 bits)
{
    M(RGBLCTL) = (M(GBLCTL) & 0x1Fu) | bits;
    u32 t0 = TSCL;
    while ((M(GBLCTL) & bits) != bits) if (TSCL - t0 > CPU_HZ / 200) return 0;
    return 1;
}
static int rx_start(void)
{
    M(RGBLCTL) = 0;
    M(RFIFOCTL) = 0x0101;
    M(RFIFOCTL) = 0x10101;
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
    M(XMASK) = STOCK_XMASK; M(XFMT) = STOCK_XFMT & ~(1u << 3);
    M(AFSXCTL) = STOCK_AFSXCTL; M(ACLKXCTL) = STOCK_ACLKXCTL; M(AHCLKXCTL) = STOCK_AHCLKXCTL;
    M(XTDM) = STOCK_XTDM; M(XINTCTL) = 0; M(XCLKCHK) = STOCK_XCLKCHK;
    M(XEVTCTL) = 0;
    M(RFIFOCTL) = 0;
    M(RMASK) = STOCK_RMASK; M(RFMT) = STOCK_RFMT & ~(1u << 3);
    M(AFSRCTL) = STOCK_AFSRCTL; M(ACLKRCTL) = STOCK_ACLKRCTL; M(AHCLKRCTL) = STOCK_AHCLKRCTL;
    M(RTDM) = STOCK_RTDM; M(RINTCTL) = 0; M(RCLKCHK) = STOCK_RCLKCHK;
    M(REVTCTL) = 0;
    for (int n = 0; n < 16; n++) M(SRCTL(n)) = 0;
    M(SRCTL(1)) = STOCK_SRCTL1; M(SRCTL(2)) = STOCK_SRCTL2; M(SRCTL(4)) = STOCK_SRCTL4;
    M(PFUNC) = STOCK_PFUNC; M(PDIR) = STOCK_PDIR;
    M(DITCTL) = 0; M(DLBCTL) = 0; M(AMUTE) = 0;
    return tx_start() && rx_start();
}
static void mcasp_off(void) { M(RGBLCTL) = 0; M(RFIFOCTL) = 0x0101; M(XGBLCTL) = 0; M(WFIFOCTL) = 0x1002; }
static void tx_align(void)
{
    for (int t = 0; t < 4; t++) {
        u32 x1 = M(XSTAT), w = M(WFIFOSTS) & 0xFF, x2 = M(XSTAT);
        if ((x1 ^ x2) & 8) continue;
        u32 s_odd = !(x1 & 8), next = (s_odd + w / 2) & 1;
        if (next) { REG(DATAPORT) = 0; REG(DATAPORT) = 0; }
        tx_align_due = 0;
        return;
    }
}
static void rx_align(void)
{
    for (int t = 0; t < 4; t++) {
        u32 r1 = M(RSTAT), n = M(RFIFOSTS) & 0xFF, r2 = M(RSTAT);
        if ((r1 ^ r2) & 8) continue;
        if (!n) return;
        u32 c_odd = !(r1 & 8), oldest = (c_odd + n) & 1;
        if (oldest) (void)REG(DATAPORT);
        rx_align_due = 0;
        return;
    }
}
static void input_poll(void)
{
    if (M(RSTAT) & 1) { rx_start(); return; }
    if (rx_align_due) { rx_align(); if (rx_align_due) return; }
    u32 n = (M(RFIFOSTS) & 0xFF) >> 1;
    while (n--) {
        s32 a = (s32)REG(DATAPORT) >> 8, b = (s32)REG(DATAPORT) >> 8;
        u32 w = in_w;
        in_l[w % IN_RING] = a; in_r[w % IN_RING] = b; in_w = w + 1;
    }
    if (in_w - in_rd > 24) in_rd = in_w - 8;
}

/* ---- parameters: two banks, pickup */
static bp_t bp;
static volatile float value[BP_NPARAM];           /* 0..1 */
static volatile u8 picked[BP_NPARAM];             /* the fader has reached the value */
static volatile int bank;                          /* 0 = core, 1 = resonator */
static u8 last_pos[8];
static volatile float mst_t;
static float mst;
static volatile int dirty;                         /* the screen needs redrawing */
static void set_param(int i, float v) { value[i] = v; bp_set(&bp, i, v); }
static void fader_moved(int f, int pos)            /* in the interrupt */
{
    if (f == 8) { mst_t = fader_gain[pos]; return; }
    int i = bank * 8 + f;
    float v = (float)pos * (1.0f / 127.0f), cur = value[i];
    if (!picked[i]) {                              /* picks up on reaching or crossing the value */
        float prev = (float)last_pos[f] * (1.0f / 127.0f), d = 1.5f / 127.0f;
        if ((prev - cur) * (v - cur) <= 0.0f || (v > cur - d && v < cur + d)) picked[i] = 1;
    }
    last_pos[f] = (u8)pos;
    if (picked[i]) set_param(i, v);
    dirty = 1;
}
static void set_bank(int k)                        /* main loop, interrupts off around it */
{
    bank = k;
    for (int f = 0; f < 8; f++) {
        int i = k * 8 + f;
        float v = (float)fader[f] * (1.0f / 127.0f);
        picked[i] = v > value[i] - 1.5f / 127.0f && v < value[i] + 1.5f / 127.0f;
        last_pos[f] = fader[f];
    }
    dirty = 1;
}

/* ---- recording: 24-bit stereo frames packed in DDR after a 44-byte WAV header */
#define REC_MEM 0xC0400000u                        /* DDR (the app itself runs from L2) */
#define REC_END 0xC4000000u
#define REC_RATE 44100u
static volatile u8 *rec_p, *rec_lim;               /* write pointer and limit; rec_p == 0: not recording */
static volatile int rec_full;
static void rec_put(s32 v)                         /* in the interrupt */
{
    u8 *p = (u8 *)rec_p;
    if (!p) return;
    if (p + 6 > rec_lim) { rec_full = 1; return; }
    p[0] = (u8)v; p[1] = (u8)(v >> 8); p[2] = (u8)(v >> 16);
    p[3] = (u8)v; p[4] = (u8)(v >> 8); p[5] = (u8)(v >> 16);
    rec_p = p + 6;
}

static char status[24]; static u32 status_t;     /* a message on row 5 for 3 s (kept while saving) */
static int saving;
static u32 rd, sel_prev, tdraw;                    /* main loop: key queue index, keys under the screen, last draw */

/* ---- the interrupt */
static void render(void)
{
    float in[BLK], out[BLK];
    for (int j = 0; j < BLK; j++) {
        s32 il = 0, ir = 0;
        if (in_rd != in_w) { il = in_l[in_rd % IN_RING]; ir = in_r[in_rd % IN_RING]; in_rd++; } else bp_in_short++;
        in[j] = (float)(il + ir) * (0.5f / 8388608.0f);
    }
    bp_render(&bp, in, out, BLK);
    for (int j = 0; j < BLK; j++) {
        float d = mst_t - mst;
        mst = d > -1e-5f && d < 1e-5f ? mst_t : mst + d * 0.002f;
        float a = out[j] * mst;
        if (a > 1.0f) a = 1.0f; else if (a < -1.0f) a = -1.0f;
        s32 v = (s32)(a * 8388607.0f);
        rec_put(v);
        u32 w = (u32)v << 8;
        REG(DATAPORT) = w; REG(DATAPORT) = w;      /* left slot: serializers 2 (headphones), 4 (line out) */
        REG(DATAPORT) = w; REG(DATAPORT) = w;      /* right slot */
    }
    bp_frames += BLK;
}
interrupt void bp_isr(void)
{
    u32 t0 = TSCL;
    bp_ints++;
    REG(TIMER1 + 0x44) = 3;
    panel_poll();
    u32 ch = fader_changed; fader_changed = 0;
    for (int f = 0; f < NFADERS; f++) if (ch & (1u << f)) fader_moved(f, fader[f]);
    input_poll();
    if (M(XSTAT) & 1) { bp_underruns++; tx_restart(); }
    if (tx_align_due) tx_align();
    int room = FIFO_WORDS - (int)(M(WFIFOSTS) & 0xFF);
    while (room >= 4 * BLK) { render(); room -= 4 * BLK; }
    u32 dt = TSCL - t0;
    bp_isr_cycles += dt; if (dt > bp_isr_max) bp_isr_max = dt;
}
static int audio_start(void)
{
    if (!mcasp_init()) { mcasp_off(); return 0; }
    REG(TIMER1 + 0x20) = 0;
    REG(TIMER1 + 0x24) |= 1;
    REG(TIMER1 + 0x10) = 0;
    REG(TIMER1 + 0x18) = 24000000u / ISR_HZ - 1;
    REG(TIMER1 + 0x44) = 3;
    REG(INTC + 0x10C) = (REG(INTC + 0x10C) & 0x00FFFFFFu) | (40u << 24);   /* INTMUX3: INT15 = event 40 */
    ISTP = (unsigned)bp_vectors;
    ICR = 1u << 15;
    IER |= (1u << 15) | 2;
    REG(TIMER1 + 0x20) = 0x80;
    _enable_interrupts();
    return 1;
}
static void audio_stop(void)
{
    mst_t = 0.0f;
    u32 t0 = TSCL; while (mst != 0.0f && TSCL - t0 < CPU_HZ / 4) ;      /* fade out */
    _disable_interrupts();
    IER &= ~(1u << 15);
    REG(TIMER1 + 0x20) = 0;
    mcasp_off();
}

/* ---- screen: row 0 title and bank, rows 1-4 two parameters each, row 6 load, row 7 key hints */
static const char *const pname[BP_NPARAM] = {
    "OSC A", "OSC B", "SWP A", "SWP B", "RUNG A", "RUNG B", "WARP", "COLOUR",
    "PEAK 1", "PEAK 2", "RNG>PK", "SPREAD", "EXT IN", "PING", "-", "-" };
static void row(int r, const char *s, int inverse)
{
    for (int c = 0; c < 21; c++) {
        int ch = *s ? (u8)*s++ : ' ';
        if (ch < 32 || ch > 126) ch = '?';
        for (int k = 0; k < 6; k++) fb[r * 128 + c * 6 + k] = (u8)(FONT6[ch - 32][k] ^ (inverse ? 0xFF : 0));
    }
    fb[r * 128 + 126] = fb[r * 128 + 127] = inverse ? 0xFF : 0;
    lcd_mark(r);
}
static char *put(char *d, const char *s) { while (*s) *d++ = *s++; return d; }
static char *num(char *d, int v, int w)             /* right-aligned */
{
    char t[8]; int k = 0;
    do t[k++] = (char)('0' + v % 10); while (v /= 10);
    while (k < w) t[k++] = ' ';
    while (k) *d++ = t[--k];
    return d;
}
static void cell(char *d, int i)                    /* 10 characters: "OSC A  64^" */
{
    char *e = put(d, pname[i]);
    while (e < d + 6) *e++ = ' ';
    int v = (int)(value[i] * 99.0f + 0.5f);
    e = num(e, v, 3);
    if (picked[i] || pname[i][0] == '-') *e++ = ' ';
    else *e++ = fader[i & 7] < (u8)(value[i] * 127.0f + 0.5f) ? '^' : 'v';   /* move the fader this way */
}
static void draw(void)
{
    char t[24];
    row(0, bank ? "BLIPPOO  2 RESONATOR" : "BLIPPOO  1 CORE", 1);
    for (int r = 0; r < 4; r++) {
        for (int k = 0; k < 21; k++) t[k] = ' ';
        t[21] = 0;
        cell(t, bank * 8 + r * 2);
        cell(t + 11, bank * 8 + r * 2 + 1);
        row(1 + r, t, 0);
    }
    char *e;
    if (status[0] && (saving || TSCL - status_t < 3 * CPU_HZ)) row(5, status, 0);
    else if (rec_p) {
        u32 sec = (u32)(rec_p - (u8 *)REC_MEM - 44) / (6 * REC_RATE), left = (u32)(rec_lim - rec_p) / (6 * REC_RATE);
        e = put(t, "REC "); e = num(e, (int)(sec / 60), 0); *e++ = ':'; *e++ = (char)('0' + sec % 60 / 10); *e++ = (char)('0' + sec % 10);
        e = put(e, "  left "); e = num(e, (int)(left / 60), 0); *e++ = ':'; *e++ = (char)('0' + left % 60 / 10); *e++ = (char)('0' + left % 10);
        *e = 0; row(5, t, 1);
    } else row(5, "", 0);
    e = put(t, "pulses ");
    static u32 last_cross, last_t; static int rate;
    u32 now = TSCL;
    if (now - last_t > CPU_HZ) { rate = (int)(bp.crossings - last_cross); last_cross = bp.crossings; last_t = now; }
    e = num(e, rate, 0); e = put(e, "/s  cpu ");
    u32 ints = bp_ints ? bp_ints : 1;
    e = num(e, (int)(bp_isr_cycles / ints / (CPU_HZ / ISR_HZ / 100)), 0); e = put(e, "%"); *e = 0;   /* average interrupt time */
    row(6, t, 0);
    row(7, "[CORE] [RES]     EXIT", 0);
}

/* ---- REC: start, stop and save */
static void set_status(const char *a, const char *b)
{
    char *e = put(status, a); if (b) e = put(e, b); *e = 0;
    status_t = TSCL;
    draw(); lcd_flush();
}
static int rec_led = -1;
static void rec_led_set(int on)                     /* panel command 88 (as the looper): REC LED = byte 4 bit 2 */
{
    if (on == rec_led) return;
    rec_led = on;
    u8 m[12] = { 0x88, 0x01, 0x08, 0, 0, 0, 0, 0, 0, 0, 0, 0xF7 };
    if (on) m[7] = 4;
    panel_send(m, 12);
}
static void rec_start(void)
{
    if (battery_poll() != BATT_OK) { set_status("battery low: no REC", 0); return; }
    set_status("card...", 0);
    if (fat_mount()) { set_status("REC: no card", 0); return; }
    u32 room = REC_END - REC_MEM - 44 - 512;
    u32 clus = 8192u * fat_spc * 512u - 44 - 512;  /* fat_create's limit (8192 clusters) */
    if (fat_spc && clus < room) room = clus;
    room -= room % 6;
    status[0] = 0;
    rec_full = 0;
    rec_lim = (u8 *)REC_MEM + 44 + room;
    rec_p = (u8 *)REC_MEM + 44;                     /* the interrupt starts recording */
    rec_led_set(1);
}
static void ui_poll(void);
static int is_blip(const char *nm)                  /* "BLIP0012.WAV" -> 12, else -1 */
{
    const char *k = "BLIP"; int v = 0;
    for (int i = 0; i < 4; i++) if ((nm[i] & ~0x20) != k[i]) return -1;
    for (int i = 4; i < 8; i++) { if (nm[i] < '0' || nm[i] > '9') return -1; v = v * 10 + nm[i] - '0'; }
    return nm[8] == '.' && (nm[9] & ~0x20) == 'W' && (nm[10] & ~0x20) == 'A' && (nm[11] & ~0x20) == 'V' && !nm[12] ? v : -1;
}
static void hput(u8 *h, u32 v, int n) { for (int k = 0; k < n; k++) h[k] = (u8)(v >> 8 * k); }
static void rec_stop_save(void)
{
    u8 *end = (u8 *)rec_p;
    rec_p = 0;                                      /* the interrupt stops recording */
    rec_led_set(0);
    u8 *h = (u8 *)REC_MEM;
    u32 data = (u32)(end - h - 44);
    if (!data) { set_status("REC: nothing recorded", 0); return; }
    set_status("saving...", 0);
    fat_ent dir, e; u32 pos = 0;
    if (fat_mount()) { set_status("no card: not saved", 0); return; }
    if (fat_find("ZOOM_R8/LOOP", &dir) || !(dir.attr & 0x10)) fat_find("", &dir);
    int top = 1;
    while (fat_next(&dir, &pos, &e)) { int v = is_blip(e.name); if (v >= top) top = v + 1; }
    if (top > 9999) { set_status("no free name", 0); return; }
    char nm[13] = "BLIP0000.WAV", n83[11] = { 'B', 'L', 'I', 'P', 0, 0, 0, 0, 'W', 'A', 'V' };
    for (int k = 7, v = top; k >= 4; k--, v /= 10) nm[k] = n83[k] = (char)('0' + v % 10);
    const char *hd = "RIFF    WAVEfmt ";
    for (int k = 0; k < 16; k++) h[k] = (u8)hd[k];
    hput(h + 4, 36 + data, 4);
    hput(h + 16, 16, 4); hput(h + 20, 1, 2); hput(h + 22, 2, 2);             /* PCM, 2 channels */
    hput(h + 24, REC_RATE, 4); hput(h + 28, REC_RATE * 6, 4); hput(h + 32, 6, 2); hput(h + 34, 24, 2);
    h[36] = 'd'; h[37] = 'a'; h[38] = 't'; h[39] = 'a'; hput(h + 40, data, 4);
    while ((u32)(end - h) & 511) *end++ = 0;        /* the last sector: zeros after the samples */
    saving = 1;
    int r = fat_create(&dir, n83, h, 44 + data, ui_poll);
    saving = 0;
    rd = panel_keyq_w;                              /* keys pressed while saving are dropped */
    if (r == FAT_TOOBIG) set_status("card full: not saved", 0);
    else if (r) set_status("card error: not saved", 0);
    else set_status("saved ", nm);
}

/* power switch (as in every app): GP8[12] = on, GP7[0] holds the power */
#define PWR_SWITCH (1u << 12)
#define PWR_HOLD   (1u << 16)
static u32 sw_off_at;
static void power_check(void)
{
    if (REG(GPIO_B8 + G_IN) & PWR_SWITCH) { sw_off_at = 0; return; }
    if (!sw_off_at) { sw_off_at = TSCL | 1; return; }
    if (TSCL - sw_off_at < CPU_HZ / 50) return;
    audio_stop();
    for (int i = 0; i < 1024; i++) fb[i] = 0;
    for (int r = 0; r < 8; r++) lcd_mark(r);
    lcd_flush();
    REG(GPIO_B67 + G_CLR) = PWR_HOLD;
    for (;;) ;
}
#define K_EXIT 0x93
#define K_REC 0xA2
static void battery_off(void)                       /* the battery is empty: as the power switch, with a message */
{
    audio_stop();
    for (int i = 0; i < 1024; i++) fb[i] = 0;
    row(3, "   BATTERY EMPTY", 0);
    for (int r = 0; r < 8; r++) lcd_mark(r);
    lcd_flush();
    delay_us(1000000);
    REG(GPIO_B67 + G_CLR) = PWR_HOLD;
    for (;;) ;
}
static void ui_poll(void)                           /* main loop, and between card writes while saving */
{
    power_check();
    static int batt_prev;
    int batt = battery_poll();
    if (batt != batt_prev) {
        batt_prev = batt;
        if (batt == BATT_LOW) {
            if (rec_p) { set_status("battery low: saving", 0); rec_stop_save(); }   /* save while there is margin */
            else set_status("battery low", 0);
        }
    }
    if (batt == BATT_EMPTY && !saving) battery_off();      /* while saving: finish first */
    u32 sd = sel_down, pressed = sd & ~sel_prev; sel_prev = sd;
    if (pressed & 3) {
        unsigned cs = _disable_interrupts();
        set_bank((pressed & 1) ? 0 : 1);
        _restore_interrupts(cs);
    }
    if (dirty || TSCL - tdraw > CPU_HZ / 4) { dirty = 0; tdraw = TSCL; draw(); }
    for (int i = 0; i < 16 && lcd_step(); i++) ;
}
int main(void)
{
    for (u8 *p = __r8_bss_start; p < __r8_bss_end; p++) *p = 0;
    TSCL = 0;
    battery_init();                                          /* the BATTERY setting the chooser saved (battery.h) */
    psc_enable(PSC1, 3); psc_enable(PSC1, 10); psc_enable(PSC1, 13);
    pinmux_stock();
    lcd_init();
    for (int r = 0; r < 8; r++) row(r, "", 0);
    row(0, "BLIPPOO", 1); row(3, "  starting...", 0); lcd_flush();
    bp_init(&bp, 44100.0f);
    for (int i = 0; i < BP_NPARAM; i++) value[i] = bp.target[i];
    mst_t = mst = 0.0f;
    panel_init();                                  /* includes the fader snapshot request */
    u32 t0 = TSCL; while (TSCL - t0 < CPU_HZ / 10) panel_poll();
    fader_changed = 0;
    /* start-up: bank 1 takes the fader positions, bank 2 keeps its defaults until picked up; MASTER as set */
    for (int f = 0; f < 8; f++) if (fader_known & (1u << f)) { picked[f] = 1; set_param(f, (float)fader[f] / 127.0f); }
    if (fader_known & (1u << 8)) mst_t = fader_gain[fader[8]];
    set_bank(0);
    for (int f = 0; f < 8; f++) picked[f] = 1;
    if (!audio_start()) { row(3, "  no audio clock", 0); lcd_flush(); }
    draw();
    rd = panel_keyq_w; sel_prev = sel_down; tdraw = TSCL;
    for (;;) {
        ui_poll();
        while (rd != panel_keyq_w) {
            u8 k = panel_keyq[rd++ & 15];
            if (k == K_REC) { if (rec_p) rec_stop_save(); else rec_start(); }
            if (k == K_EXIT) {
                if (rec_p) rec_stop_save();
                audio_stop();
                row(3, "  restarting...", 0); lcd_flush();
                delay_us(200000);
                r8_restart();
            }
        }
        if (rec_full && rec_p) rec_stop_save();
    }
}
