/* R8 Forth core (see core.h). LCD: row 0 = app name and USB state, row 1 = counters (S setups, E EP0 events,
   R resets, X stalls) and the last request, rows 2-7 = the end of the terminal session. Power switch off = power off. */
#include "core.h"
#include "lcd.h"
#include "usb.h"
#include "msc.h"
#include "forth.h"
#include "font.h"
#include "sd.h"
#include "aio.h"
#include "reload.h"
#include "panel.h"
#include "battery.h"
#include "clock.h"
extern u8 __r8_bss_start[], __r8_bss_end[], __r8_ddrbss_start[], __r8_ddrbss_end[];
void audio_pump(void) {}            /* lcd.c calls it */

static const struct core_app *app;
static int app_busy(void) { return app && app->busy && app->busy(); }
static void app_stop(void) { if (app && app->stop) app->stop(); }

/* ---- text console on the LCD: 21 x 8 characters */
#define COLS 21
#define ROWS 8
#define TOP 2                       /* first terminal row */
static char scr[ROWS][COLS];
static int cx, cy = TOP;
static void draw_row(int r)
{
    if (app_busy()) return;                 /* the app owns the screen; scr[] keeps the console for later */
    for (int c = 0; c < COLS; c++) {
        int ch = (u8)scr[r][c]; if (ch < 32 || ch > 126) ch = ' ';
        for (int k = 0; k < 6; k++) fb[r * 128 + c * 6 + k] = FONT6[ch - 32][k];
    }
    fb[r * 128 + 126] = fb[r * 128 + 127] = 0;
    lcd_mark(r);
}
static void row_text(int r, const char *s)
{
    int c = 0;
    for (; *s && c < COLS; s++) scr[r][c++] = *s;
    while (c < COLS) scr[r][c++] = ' ';
    draw_row(r);
}
static void con_putc(int ch)
{
    if (ch == '\r') { cx = 0; return; }
    if (ch == '\b') { if (cx) cx--; return; }
    if (ch == '\n' || cx >= COLS) {
        cx = 0;
        if (++cy >= ROWS) {
            cy = ROWS - 1;
            for (int r = TOP; r < ROWS - 1; r++) { for (int c = 0; c < COLS; c++) scr[r][c] = scr[r + 1][c]; draw_row(r); }
            for (int c = 0; c < COLS; c++) scr[ROWS - 1][c] = ' ';
            draw_row(ROWS - 1);
        }
        if (ch == '\n') return;
    }
    scr[cy][cx++] = (char)ch;
    draw_row(cy);
}
void forth_page(void) { for (int r = TOP; r < ROWS; r++) row_text(r, ""); cx = 0; cy = TOP; }
void core_glyphs(int r, int col, const char *s)
{
    for (; *s && col <= 122; s++, col += 6) {
        int ch = (u8)*s; if (ch < 32 || ch > 126) ch = '?';
        for (int k = 0; k < 6; k++) fb[r * 128 + col + k] = FONT6[ch - 32][k];
    }
}

/* ---- small formatting for the status rows */
static char *put_u(char *p, u32 v) { char t[10]; int n = 0; do { t[n++] = (char)('0' + v % 10); v /= 10; } while (v); while (n) *p++ = t[--n]; return p; }
static char *put_h2(char *p, u32 v) { const char *h = "0123456789ABCDEF"; *p++ = h[(v >> 4) & 15]; *p++ = h[v & 15]; return p; }
static void status(void)
{
    char t[48], *p = t;                                   /* room past COLS: clipped below */
    const char *st = !usb_phy_ok ? "NO PHY CLOCK" : usb_dtr ? "OPEN" : usb_configured ? "CONFIGURED" : usb_addr ? "ADDRESSED" :
                     usb_resets ? "BUS RESET" : "WAITING";
    for (const char *q = app && app->name ? app->name : "FORTH"; *q && p < t + 12; ) *p++ = *q++;
    *p++ = ' ';
    while (*st && p < t + COLS) *p++ = *st++;
    if (usb_phy_ok && !usb_resets) { *p++ = ' '; *p++ = (char)('A' + usb_strategy); }   /* connect strategy */
    if (p > t + COLS) p = t + COLS;
    *p = 0; row_text(0, t);
    p = t;
    if (!usb_resets) {                                     /* not seen by a host yet: raw registers instead */
        const char *h = "0123456789ABCDEF";
        u32 v[4] = { usb_diag(0), usb_diag(1), usb_diag(2), usb_diag(3) };
        *p++ = 'P'; p = put_h2(p, v[0]); *p++ = 'D'; p = put_h2(p, v[1]); *p++ = ' ';      /* P40D99 C932+ I1000000 */
        *p++ = 'C'; for (int s = 8; s >= 0; s -= 4) *p++ = h[(v[2] >> s) & 15];
        if (v[2] & (1u << 17)) *p++ = '+';
        *p++ = ' '; *p++ = 'I'; for (int s = 24; s >= 0; s -= 4) *p++ = h[(v[3] >> s) & 15];
        *p = 0; row_text(1, t); return;
    }
    *p++ = 'S'; p = put_u(p, usb_setups); *p++ = ' '; *p++ = 'E'; p = put_u(p, usb_ep0_events); *p++ = ' ';
    *p++ = 'R'; p = put_u(p, usb_resets); *p++ = ' '; *p++ = 'X'; p = put_u(p, usb_stalls); *p++ = ' ';
    if (msc_cmds) {                                        /* storage: commands, blocks read, blocks written */
        *p++ = !msc_present ? '-' : msc_writable ? 'W' : 'M'; p = put_u(p, msc_cmds); *p++ = ' '; *p++ = 'B'; p = put_u(p, msc_blocks);
        if (msc_wblocks) { *p++ = ' '; *p++ = 'w'; p = put_u(p, msc_wblocks); }
    }
    else { p = put_h2(p, usb_last_req[1]); p = put_h2(p, usb_last_req[3]); }
    if (p > t + COLS) p = t + COLS;
    *p = 0; row_text(1, t);
}
void core_console_back(void) { for (int r = 0; r < ROWS; r++) draw_row(r); status(); }

/* ---- power switch (see the synth: GP8[12] = switch on, GP7[0] = power hold) */
#define PWR_SWITCH (1u << 12)
#define PWR_HOLD   (1u << 16)
static u32 sw_off_at;
static void power_check(void)
{
    int empty = battery_poll() == BATT_EMPTY;              /* on batteries, below stock's cut-off for 4 s */
    if (!empty) {
        if (REG(GPIO_B8 + G_IN) & PWR_SWITCH) { sw_off_at = 0; return; }
        if (!sw_off_at) { sw_off_at = TSCL | 1; return; }
        if (TSCL - sw_off_at < CPU_HZ / 50) return;
    }
    app_stop();                                            /* e.g. the sampler fades its loops out */
    usb_disconnect();
    for (int i = 0; i < 1024; i++) fb[i] = 0;
    for (int r = 0; r < 8; r++) lcd_mark(r);
    for (int r = 0; r < ROWS; r++) for (int c = 0; c < COLS; c++) scr[r][c] = ' ';
    if (empty) { const char *t = "BATTERY EMPTY"; for (int i = 0; t[i]; i++) scr[3][4 + i] = t[i]; }
    else { scr[3][9] = 'O'; scr[3][10] = 'F'; scr[3][11] = 'F'; }
    app = 0;                                               /* the console owns the screen again */
    draw_row(3);
    lcd_flush();
    REG(GPIO_B67 + G_CLR) = PWR_HOLD;
    for (;;) ;
}
void core_service(void) { usb_poll(); power_check(); }

/* ---- the panel link: opened once, shared by the core words and the app */
static int panel_up;
void core_panel_up(void) { if (!panel_up) { panel_init(); panel_up = 1; } }   /* UART + start-up, once */
int core_panel_is_up(void) { return panel_up; }
void core_faders_sync(void)                    /* ask the panel for every fader position and wait (~10 ms; up to 50) */
{
    if (!panel_up) { panel_init(); panel_up = 1; return; }   /* the start-up includes the snapshot request */
    panel_request();
    u32 t0 = TSCL;
    while (TSCL - t0 < CPU_HZ / 20) if (!app_busy()) panel_poll();
}

/* ---- polling: USB, power, LCD, status rows */
static u32 t_status, last_dtr;
static int running;                 /* forth_eval is executing a line */
static void banner(void);
static int pm_active;                                       /* PANEL-MON reads the panel UART itself */
static u32 was_present;                                     /* storage on before: going off may mean new files */
static void usb_key(void);
void forth_poll(void)
{
    usb_poll();
    if (!app_busy() && !pm_active && panel_up) panel_poll();   /* while the app is busy, it reads the panel */
    if (key_pressed & 4) { key_pressed &= ~4u; usb_key(); }
    if (was_present && !msc_present && app && app->storage_released) app->storage_released();
    was_present = msc_present;
    if (app && app->poll) app->poll();
    power_check();
    for (int i = 0; i < 16 && lcd_step(); i++) ;
    if (TSCL - t_status > CPU_HZ / 10) {
        t_status = TSCL;
        if (app_busy()) { if (app->screen) app->screen(); } else status();
    }
    if (running && usb_rx_take(3)) forth_abort = 1;        /* Ctrl-C while a word runs (typeahead up to it is dropped) */
}
void forth_emit(int c)
{
    con_putc(c);
    if (!usb_dtr) return;                                  /* nobody listening: screen only */
    u32 t0 = TSCL;
    while (!usb_putc((u8)c)) {                             /* wait for the host to read (<= 0.5 s) */
        usb_poll(); power_check();
        if (!usb_dtr || TSCL - t0 > CPU_HZ / 2) return;
    }
}
int forth_key(void) { return usb_getc(); }
void core_puts(const char *s) { while (*s) forth_emit(*s++); }
#define puts_ core_puts

/* ---- SD card words */
static u32 blkbuf[128];
static const char *const sd_err[] = { "ok", "no card", "timeout", "CRC error", "init failed", "data error", "beyond the card", "card reports write error", "card busy" };
static void sd_report(int e) { if (e) { puts_(" SD: "); puts_(sd_err[e]); puts_(" (MMCST0 "); forth_hex(sd_last_st, 4); puts_(") "); } }
static void w_sd_init(void) { int e = sd_init(); sd_report(e); forth_push(e); }       /* ( -- err ) */
static void w_sd_read(void)                                                           /* ( block addr -- err ) */
{
    u32 a = (u32)forth_pop(), b = (u32)forth_pop();
    if (forth_failed()) return;
    int e = sd_read(b, (void *)(a & ~3u)); sd_report(e); forth_push(e);
}
static void w_sd_readn(void)                                                          /* ( block count addr -- err ) */
{
    u32 a = (u32)forth_pop(), n = (u32)forth_pop(), b = (u32)forth_pop();
    if (forth_failed()) return;
    int e = sd_read_n(b, n, (void *)(a & ~3u)); sd_report(e); forth_push(e);
}
static void w_sd_readd(void)                                                          /* ( block count addr -- err ) */
{
    u32 a = (u32)forth_pop(), n = (u32)forth_pop(), b = (u32)forth_pop();
    if (forth_failed()) return;
    int e = sd_read_dma(b, n, (void *)(a & ~3u)); sd_report(e);
    if (e) { puts_(" ccnt left "); forth_dot((int)sd_dma_last); }
    forth_push(e);
}
static void w_blk(void)                                                               /* ( block -- addr ) */
{
    u32 b = (u32)forth_pop();
    if (forth_failed()) return;
    if (!sd_ready) sd_init();
    int e = sd_read(b, blkbuf); sd_report(e);
    forth_push((int)blkbuf);
}
static void w_sd_write(void)                                                          /* ( block addr -- err ) */
{
    u32 a = (u32)forth_pop(), b = (u32)forth_pop();
    if (forth_failed()) return;
    if (!sd_ready) sd_init();
    int e = sd_write(b, 1, (const void *)(a & ~3u)); sd_report(e); forth_push(e);
}
static void w_sd_writes(void)                                                         /* ( block addr n -- err ) */
{
    u32 n = (u32)forth_pop(), a = (u32)forth_pop(), b = (u32)forth_pop();
    if (forth_failed()) return;
    if (!sd_ready) sd_init();
    int e = sd_write(b, n, (const void *)(a & ~3u)); sd_report(e); forth_push(e);
}
/* ( block n seed -- bad ) write n blocks of a seeded pattern from DDR, read them back, compare. Only inside the
   unpartitioned gap (blocks 1..8191) so it can never touch the filesystem. bad = -1 if all bytes match, else the
   first mismatching byte offset (-2: refused, -3: read error, -4: write error). */
/* ( block blocks runs -- ) write `runs` bursts of `blocks` (1..128) blocks at block, each one CMD25 as sd_write sends
   it, and report the card's programming (busy) time: average and worst, plus throughput and the slowest whole write.
   Only in the unpartitioned gap (1 .. the first partition's start - 1, and below 8192), never while USB storage is
   on. Run it under an app's real interrupt load to see what a streaming scheduler must allow for. */
#pragma CODE_SECTION(w_sd_busy, ".ddrtext")
static void w_sd_busy(void)
{
    u32 runs = (u32)forth_pop(), n = (u32)forth_pop(), b = (u32)forth_pop();
    if (forth_failed()) return;
    if (msc_present) { puts_(" USB storage is on: MSC-OFF first "); return; }
    if (!sd_ready && sd_init()) { puts_(" no card "); return; }
    u32 *mbr = (u32 *)0xC0200000u;
    if (sd_read(0, mbr)) { puts_(" MBR read failed "); return; }
    const u8 *m = (const u8 *)mbr;
    u32 start = m[0x1C6] | m[0x1C7] << 8 | m[0x1C8] << 16 | (u32)m[0x1C9] << 24, lim = start < 8192 ? start : 8192;
    if (m[510] != 0x55 || m[511] != 0xAA || !start || !n || n > 128 || !runs || b < 1 || b + n > lim) {
        puts_(" only blocks 1.."); forth_dot((int)lim - 1); puts_("(the gap), 1..128 blocks "); return;
    }
    u8 *w = (u8 *)0xC0100000u; u32 x = b * 2654435761u + 1;
    for (u32 i = 0; i < n * 512; i++) { x = x * 1103515245u + 12345u; w[i] = (u8)(x >> 16); }
    sd_busy_max = 0;
    u32 sum = 0, worst = 0, t0 = TSCL;
    for (u32 k = 0; k < runs && !forth_abort; k++) {
        u32 t = TSCL;
        int e = sd_write(b, n, w);
        u32 dt = TSCL - t;
        if (e) { sd_report(e); return; }
        sum += sd_busy_last / 360; if (dt > worst) worst = dt;
        forth_poll();
    }
    u32 us = (TSCL - t0) / 360;
    puts_(" runs "); forth_dot((int)runs); puts_("x"); forth_dot((int)n); puts_("blocks: ");
    forth_dot((int)us); puts_("us, "); forth_dot(us ? (int)(runs * n * 512 / us) : 0); puts_("bytes/us; busy avg ");
    forth_dot((int)(sum / runs)); puts_("us, max "); forth_dot((int)(sd_busy_max / 360)); puts_("us; slowest write ");
    forth_dot((int)(worst / 360)); puts_("us ");
}
static void w_sd_test(void)
{
    u32 seed = (u32)forth_pop(), n = (u32)forth_pop(), b = (u32)forth_pop();
    if (forth_failed()) return;
    if (!n || n > 128 || b < 1 || b + n > 8192) { forth_push(-2); return; }
    if (!sd_ready) sd_init();
    u8 *w = (u8 *)0xC0100000u, *r = (u8 *)0xC0200000u;
    u32 x = seed * 2654435761u + 1;
    for (u32 i = 0; i < n * 512; i++) { x = x * 1103515245u + 12345u; w[i] = (u8)(x >> 16); r[i] = (u8)~w[i]; }
    int e = sd_write(b, n, w); sd_report(e);
    if (e) { forth_push(-4); return; }
    for (u32 k = 0; k < n; k++) {
        e = sd_read(b + k, r + k * 512); sd_report(e);
        if (e) { forth_push(-3); return; }
    }
    for (u32 i = 0; i < n * 512; i++) if (r[i] != w[i]) { forth_push((int)i); return; }
    forth_push(-1);
}
static void w_sd_blocks(void) { forth_push((int)sd_blocks); }                          /* ( -- n ) */
static void w_sd_info(void)
{
    if (!sd_ready) { puts_(" no card initialised (SD-INIT) "); return; }
    puts_(sd_hc ? "SDHC/SDXC" : "SDSC"); puts_(", ");
    forth_dot((int)(sd_blocks / 2048)); puts_("MB (");
    forth_dot((int)sd_blocks); puts_("blocks), RCA "); forth_hex(sd_rca, 4);
    char nm[6]; for (int i = 0; i < 5; i++) nm[i] = (char)(i == 0 ? sd_cid[0] & 0xFF : (sd_cid[1] >> (24 - 8 * (i - 1))) & 0xFF);
    nm[5] = 0;
    puts_(", maker "); forth_hex(sd_cid[0] >> 24, 2); puts_(", name "); puts_(nm);
    puts_("\r\n");
}

/* ---- audio pass-through (aio.c) */
static void w_audio_init(void)                                                        /* ( -- flag ) */
{
    if (app_busy()) { puts_(" the app is using the audio "); forth_push(0); return; }
    forth_push(aio_init() ? -1 : 0);
}
/* ( ms gain -- ) input -> output for ms milliseconds, gain Q8 (256 = unity); prints per-channel peaks (24-bit),
   frames and errors. USB is not serviced meanwhile: keep it short. */
static void w_pass(void)
{
    s32 g = forth_pop(); u32 ms = (u32)forth_pop();
    if (forth_failed()) return;
    if (app_busy()) { puts_(" the app is using the audio "); return; }
    if (!aio_ok) { puts_(" AUDIO-INIT first "); return; }
    if (ms > 60000) ms = 60000;
    aio_pass(ms, g);
    puts_(" peak0 "); forth_dot(aio_peak[0]); puts_(" peak1 "); forth_dot(aio_peak[1]);
    puts_(" frames "); forth_dot((int)aio_frames); puts_(" errors "); forth_dot((int)aio_errs);
}

/* ---- audio clock (clock.c) */
static void clk_report(int e)
{
    if (e == -1) puts_(" 44100 or 48000 only ");
    else if (e == -2) puts_(" clock chip did not answer (NACK) ");
    else if (e == -3) puts_(" I2C timeout ");
}
static void w_rate(void)                                    /* ( hz -- ) set the output rate */
{
    int hz = forth_pop();
    if (forth_failed()) return;
    int e = clk_set_rate((u32)hz);
    clk_report(e);
    if (!e && app && app->rate_changed) app->rate_changed();
}
static void w_dot_rate(void)                                /* ( -- ) output rate and board ID */
{
    forth_dot((int)clk_rate); puts_("Hz, board "); forth_dot(clk_board());
}
static void w_clk_raw(void)                                 /* ( family v -- ) board 7: raw clock-chip setting, for exploring */
{
    int v = forth_pop(), fam = forth_pop();
    if (forth_failed()) return;
    int e = clk_raw((u32)fam, (u32)v);
    if (e == -1) puts_(" board 7 only "); else clk_report(e);
}

/* ---- panel exploration: PANEL-MON shows what the panel sends, PANEL-TX sends raw bytes to it, FADERS reads them */
static const char *const pkey[0x28] = {   /* key IDs (status 0x80 + id): see the work log's panel map; ? = inferred */
    "ON/OFF 1", "EFFECT", "USB", "TOOL", "PROJECT", "ON/OFF 2", "?86", "RHYTHM", "TRACK", "PAN/EQ",
    "F1 AUTO PUNCH", "F2 A-B REPEAT", "F3 |<<", "F4 >>|", "F5 MARK/CLEAR", "TRACK1 status", "TRACK2 status", "TRACK3 status", "TRACK4 status", "EXIT",
    "UP", "DOWN", "RIGHT", "LEFT", "ENTER", "TRACK5 status", "TRACK6 status", "TRACK7 status", "TRACK8 status", "MASTER status",
    "REW", "FF", "STOP", "PLAY", "REC", "SOFT1", "SOFT2", "SOFT3", "SOFT4", "REPEAT/STOP" };   /* TEMPO is GP6[0], not here */
static void pm_show(const u8 *m, int n)
{
    for (int i = 0; i < n; i++) { forth_hex(m[i], 2); forth_emit(' '); }
    puts_(" ");
    u8 st = m[0];
    if (st >= 0x80 && st < 0x80 + 0x28 && n >= 2) { puts_(pkey[st - 0x80]); puts_(m[1] == 3 ? " press" : m[1] == 4 ? " release" : " ?"); }
    else if (st >= 0xC3 && st <= 0xCA && n >= 2) { puts_("fader "); forth_dot(st - 0xC2); puts_("= "); forth_dot(m[n - 1]); }
    else if (st == 0xCB && n >= 2) { puts_("MASTER fader = "); forth_dot(m[n - 1]); }
    else if (st == 0xC2 && n >= 2) { puts_("analog C2 = "); forth_dot(m[n - 1]); }
    else if (st == 0xC1 && n >= 3) { puts_("C1 14-bit = "); forth_dot(m[1] << 7 | m[2]); }
    else if (st == 0xC0 && n >= 3) { puts_("footswitch (CONTROL IN) = "); forth_dot(m[1] << 7 | m[2]); puts_(m[1] || m[2] >= 0x1C ? "pressed" : "released"); }
    else if (st == 0xD0) { puts_("wheel"); for (int i = 1; i < n; i++) { forth_emit(' '); forth_dot(m[i] < 64 ? m[i] : m[i] - 128); } }
    else if (st == 0xF0 && n >= 4) { puts_("pad "); forth_dot(m[1] << 4 | m[2]); puts_("velocity "); forth_dot(m[3]); }
    else if (st == 0xE0) puts_("snapshot (bytes 12-19 faders 1-8, 20 MASTER)");
    puts_("\r\n");
}
static void w_panel_mon(void)                               /* ( seconds -- ) print panel messages, decoded */
{
    int secs = forth_pop();
    if (forth_failed()) return;
    if (app_busy()) { puts_(" the app is reading the panel: stop it first "); return; }
    core_faders_sync();                                     /* panel UART up (first use sends the start-up) */
    pm_active = 1;
    u8 m[32]; int n = 0;
    u32 t0 = TSCL, last = TSCL, lim = (u32)secs * (CPU_HZ / 1000) * 1000;
    if (secs > 11) lim = 11u * CPU_HZ;                      /* TSCL wraps at 11.9 s */
    puts_("\r\n");
    while (TSCL - t0 < lim && !forth_abort) {
        forth_poll();
        while (REG(0x01D0D014) & 1) {
            u8 b = (u8)REG(0x01D0D000);
            if ((b & 0x80) && n) { pm_show(m, n); n = 0; }
            if (n < 32) m[n++] = b;
            last = TSCL;
        }
        if (n && TSCL - last > CPU_HZ / 50) { pm_show(m, n); n = 0; }
    }
    if (n) pm_show(m, n);
    pm_active = 0;
}
static void w_panel_tx(void)                                /* ( addr n -- ) raw bytes to the panel */
{
    int n = forth_pop(); u32 a = (u32)forth_pop();
    if (forth_failed()) return;
    if (n < 1 || n > 64) { puts_(" 1..64 bytes "); return; }
    core_panel_up();
    panel_send((const u8 *)a, n);
}
static void w_faders(void)                                  /* ( -- ) ask the panel, print faders 1-8 and MASTER */
{
    core_faders_sync();
    for (int f = 0; f < NFADERS; f++) forth_dot(fader[f]);
}

/* ---- LCD contrast. LCD-VOL ( v -- ) sends 81 v (0..63); CONTRAST ( n -- ) takes stock's setting 1..8 (5 = default) */
static void w_lcd_vol(void)
{
    int v = forth_pop();
    if (forth_failed()) return;
    if (v < 0 || v > 63) { puts_(" 0..63 "); return; }
    lcd_contrast((u32)v);
}
static void w_contrast(void)
{
    int n = forth_pop();
    if (forth_failed()) return;
    if (n < 1 || n > 8) { puts_(" 1..8 "); return; }
    lcd_contrast(LCD_CONTRAST_STOCK[n - 1]);
}

/* ---- RELOAD ( -- ) run the app's file again (after copying a new one over USB): checked like RUN APP does it */
static void w_reload(void)
{
    const char *path = reload_launched();                   /* started by the chooser: reload that file */
    if (!path) path = "APP.BIN";
    const char *e = reload_check(path, core_service);
    if (e) { puts_(" RELOAD: "); puts_(e); puts_(" "); return; }
    puts_(" "); puts_(path); puts_(" ok, restarting...\r\n");
    u32 t0 = TSCL; while (TSCL - t0 < CPU_HZ / 10) usb_poll();          /* let the text reach the host */
    app_stop();
    usb_disconnect();
    t0 = TSCL; while (TSCL - t0 < CPU_HZ / 5) ;                         /* the host sees the device go */
    reload_go();
}
/* USB storage: off by default (the host sees no card). MSC-RO / MSC-RW turn it on (re-connecting, so the host reads
   the write-protect bit afresh); MSC-OFF or the USB key turns it off, but only once the host has ejected it. */
static void w_msc_rw(void) { msc_writable = 1; msc_set_present(1); usb_reconnect(); }
static void w_msc_ro(void) { msc_writable = 0; msc_set_present(1); usb_reconnect(); }
static int storage_off(void)
{
    if (msc_present && msc_in_use) { puts_(" storage: eject the card on the computer first "); return 0; }
    msc_set_present(0); msc_writable = 0;
    return 1;
}
static void w_msc_off(void) { if (storage_off()) puts_(" storage off "); }
static void usb_key(void)                                   /* the panel's USB key toggles storage (read-only) */
{
    if (msc_present) { if (storage_off()) puts_("\r\n storage off (USB key)\r\n"); }
    else { msc_writable = 0; msc_set_present(1); puts_("\r\n storage on, read-only (USB key)\r\n"); }
}
static void hex8(u32 v) { char b[9]; for (int i = 0; i < 8; i++) b[i] = "0123456789ABCDEF"[v >> (28 - 4 * i) & 15]; b[8] = 0; puts_(b); }
static void w_msc_log(void)                                 /* ( -- ) the last 32 storage commands, oldest first */
{
    u32 n = msc_log_n, k = n > 32 ? n - 32 : 0;
    for (; k < n; k++) { puts_(" "); hex8(msc_log[k & 31]); }
}
static void w_bye(void)                                     /* ( -- ) back to the stock R8 firmware (restart) */
{
    puts_(" restarting into the R8 firmware...\r\n");
    u32 t0 = TSCL; while (TSCL - t0 < CPU_HZ / 10) usb_poll();
    app_stop(); usb_disconnect();
    r8_restart();
}
/* DDR-INFO ( -- ): the app DDR window (l2.cmd) and a check that the loader put .ddrdata in place. The word itself is
   .ddrtext and the check word is .ddrdata, so it exercises all three DDR section kinds with .ddrbss (forth.c). */
#pragma DATA_SECTION(ddr_check, ".ddrdata")
static u32 ddr_check[2] = { 0x52384444u, 0x0002A5A5u };   /* 'R8DD', format 2 */
#pragma CODE_SECTION(w_ddr_info, ".ddrtext")
static void w_ddr_info(void)
{
    puts_(" DDR window C0000000-C00FFFFF; .ddrbss "); forth_hex((u32)__r8_ddrbss_start, 8); puts_("-"); forth_hex((u32)__r8_ddrbss_end, 8);
    puts_("; .ddrdata check at "); forth_hex((u32)ddr_check, 8);
    puts_(ddr_check[0] == 0x52384444u && ddr_check[1] == 0x0002A5A5u ? " ok " : " WRONG ");
}
static void banner(void)
{
    puts_("\r\n"); puts_(app && app->title ? app->title : "R8 Forth");
    puts_(" - WORDS lists the words, Ctrl-C stops a running word\r\n");
}

void core_init(const struct core_app *a)
{
    for (u8 *p = __r8_bss_start; p < __r8_bss_end; p++) *p = 0;
    for (u8 *p = __r8_ddrbss_start; p < __r8_ddrbss_end; p++) *p = 0;     /* and the DDR window's .ddrbss */
    app = a;
    TSCL = 0;
    battery_init();                                          /* the BATTERY setting the chooser saved (battery.h) */
    psc_enable(PSC1, 3); psc_enable(PSC1, 10); psc_enable(PSC1, 13);
    pinmux_stock();
    lcd_init();
    for (int r = 0; r < ROWS; r++) row_text(r, "");
    status();
    lcd_flush();
    core_panel_up();                                         /* panel link from the start: the USB key works anywhere */
    usb_init();
    forth_init();
    forth_word("SD-INIT", w_sd_init); forth_word("SD-READ", w_sd_read); forth_word("SD-READN", w_sd_readn); forth_word("SD-READD", w_sd_readd); forth_word("BLK", w_blk);
    forth_word("SD-BLOCKS", w_sd_blocks); forth_word("SD-INFO", w_sd_info); forth_word("SD-WRITE", w_sd_write);
    forth_word("SD-WRITES", w_sd_writes); forth_word("SD-TEST", w_sd_test);
    forth_word("MSC-RW", w_msc_rw); forth_word("MSC-RO", w_msc_ro); forth_word("MSC-OFF", w_msc_off); forth_word("MSC-LOG", w_msc_log);
    forth_word("BYE", w_bye); forth_word("RELOAD", w_reload);
    forth_word("AUDIO-INIT", w_audio_init); forth_word("PASS", w_pass);
    forth_word("RATE", w_rate); forth_word(".RATE", w_dot_rate); forth_word("CLK-RAW", w_clk_raw);
    forth_word("PANEL-MON", w_panel_mon); forth_word("PANEL-TX", w_panel_tx); forth_word("FADERS", w_faders);
    forth_word("LCD-VOL", w_lcd_vol); forth_word("CONTRAST", w_contrast);
    forth_word("DDR-INFO", w_ddr_info); forth_word("SD-BUSY", w_sd_busy);
}

void core_run(void)
{
    static char line[256]; int n = 0;
    for (;;) {
        forth_poll();
        if (usb_dtr && !last_dtr) { banner(); n = 0; }     /* terminal opened */
        last_dtr = usb_dtr;
        int c = usb_getc();
        if (c < 0) continue;
        if (c == '\r' || c == '\n') {
            if (c == '\n' && n == 0) continue;             /* LF after CR */
            forth_emit(' ');
            running = 1; forth_eval(line, n); running = 0; n = 0;
        } else if (c == 8 || c == 127) {
            if (n) { n--; puts_("\b \b"); }
        } else if (c == 3) { n = 0; puts_(" ^C\r\n"); }
        else if (c >= 32 && n < (int)sizeof line - 1) { line[n++] = (char)c; forth_emit(c); }
    }
}
