/* R8 Sampler: the four-loop variable-speed looper (looper.c) on the Forth core (forth/core.c). Run by the chooser
   (R8APPS/SAMPLER.BIN). At start-up it loads the first four WAVs in ZOOM_R8/LOOP and plays them; the Forth prompt
   on the USB serial port stays available for the looper words below and every core word.
   Panel while playing: faders 1/3/5/7 = speed of loops 1-4 (middle stopped, top +4x, bottom -4x), 2/4/6/8 = level,
   MASTER = output; STOP restarts the loops; hold a key under the screen (track 1-4) and turn the wheel to pick a file. */
#include "core.h"
#include "forth.h"
#include "looper.h"
#include "aio.h"
#include "clock.h"
#include "lcd.h"
#define puts_ core_puts

/* ---- looper screen: two rows per loop, "1 name" then speed and a level bar */
static void lp_screen(void)
{
    for (int i = 0; i < LP_VOICES; i++) {
        int r = 2 * i;
        for (int c = 0; c < 128; c++) fb[r * 128 + c] = fb[(r + 1) * 128 + c] = 0;
        char t[16]; t[0] = (char)('1' + i); t[1] = 0; core_glyphs(r, 0, t);
        int st = lp_track_state(i);
        if (st == 2 || st == 3) {                           /* choosing (key held) or loading the choice */
            core_glyphs(r, 6, ">"); core_glyphs(r, 12, lp_file_name(lp_track_pick(i)));
            if (st == 3) core_glyphs(r + 1, 6, "loading");
            else {
                int k = lp_track_pick(i) + 1, n = lp_nfiles(), p = 0;
                char *q = t; int d = 100; while (d > k && d > 1) d /= 10;
                for (; d; d /= 10) q[p++] = (char)('0' + k / d % 10);
                q[p++] = '/'; d = 100; while (d > n && d > 1) d /= 10;
                for (; d; d /= 10) q[p++] = (char)('0' + n / d % 10);
                q[p] = 0; core_glyphs(r + 1, 6, "pick"); core_glyphs(r + 1, 36, q);
            }
            lcd_mark(r); lcd_mark(r + 1); continue;
        }
        if (st == 0) { core_glyphs(r, 12, "-"); core_glyphs(r + 1, 6, lp_err[i] ? lp_err[i] : "empty"); lcd_mark(r); lcd_mark(r + 1); continue; }
        if (st == 4) {                                      /* recording the input */
            u32 ds = clk_rate ? lp_rec_n * 10 / clk_rate : 0;
            char *q = t; int p = 0, d = 1000; while (d > (int)(ds / 10) && d > 1) d /= 10;
            for (; d; d /= 10) q[p++] = (char)('0' + ds / 10 / d % 10);
            q[p++] = '.'; q[p++] = (char)('0' + ds % 10); q[p++] = 's'; q[p] = 0;
            core_glyphs(r, 12, "REC"); core_glyphs(r + 1, 6, q); lcd_mark(r); lcd_mark(r + 1); continue;
        }
        core_glyphs(r, 12, lp_names[i]);
        float sp = lp_speed(i);
        int h = (int)((sp < 0 ? -sp : sp) * 100.0f + 0.5f);   /* hundredths */
        if (!h) core_glyphs(r + 1, 6, "stop");
        else {
            t[0] = sp < 0 ? '-' : '+'; t[1] = (char)('0' + h / 100); t[2] = '.';
            t[3] = (char)('0' + h / 10 % 10); t[4] = (char)('0' + h % 10); t[5] = 'x'; t[6] = 0;
            core_glyphs(r + 1, 6, t);
        }
        int lv = lp_fader_get(2 * i + 1), x0 = 54, w = 72, fill = lv * (w - 2) / 127;
        u8 *b = fb + (r + 1) * 128;
        b[x0] = b[x0 + w - 1] = 0x7E;
        for (int c = 1; c < w - 1; c++) b[x0 + c] = c <= fill ? 0x7E : 0x42;
        lcd_mark(r); lcd_mark(r + 1);
    }
}

/* ---- hooks for the core */
static int  s_busy(void) { return lp_running; }
static void s_poll(void) { lp_service(); }                 /* loads picked with the keys under the screen */
static void s_stop(void) { lp_stop(); }                    /* fades the loops out */
static void s_rate(void) { lp_out_rate(); }                /* loops keep their pitch */
static void s_released(void) { if (lp_running) lp_rescan(); }   /* the computer let go of the card: re-read the folder */
static const struct core_app sampler_app = { "SAMPLER", "R8 Sampler (Forth)", s_busy, s_poll, lp_screen, s_stop, s_rate, s_released };

/* ---- looper words */
static void w_looper(void)                                  /* ( -- ) load ZOOM_R8/LOOP, start playing */
{
    aio_ok = 0;                                             /* PASS needs AUDIO-INIT again afterwards */
    int n = lp_start(puts_, core_service);
    if (n >= 0) { puts_("\r\n "); forth_dot(n); puts_("loops playing "); lp_screen(); }
}
static void w_looper_stop(void) { lp_stop(); core_console_back(); }
static void w_fader_store(void)                             /* ( value fader -- ) fader 1-8, 9 = MASTER */
{
    int f = forth_pop(), v = forth_pop();
    if (forth_failed()) return;
    lp_fader(f - 1, v);
}
static void w_loop_restart(void) { lp_restart_loops(); }
static void w_monitor(void) { int v = forth_pop(); if (!forth_failed()) lp_monitor(v); }   /* ( 0-127 -- ) */
static void w_rec(void)                                     /* ( track -- ) record the input into track 1-4 */
{
    int t = forth_pop();
    if (forth_failed()) return;
    int e = lp_rec_start(t - 1);
    if (e == -1) puts_(" LOOPER first "); else if (e == -2) puts_(" track 1-4 "); else if (e == -3) puts_(" already recording ");
    else puts_(" recording ");
}
static void w_rec_mix(void)                                 /* ( track -- ) record the other tracks' mix into track 1-4 */
{
    int t = forth_pop();
    if (forth_failed()) return;
    int e = lp_rec_start_mix(t - 1);
    if (e == -1) puts_(" LOOPER first "); else if (e == -2) puts_(" track 1-4 "); else if (e == -3) puts_(" already recording ");
    else puts_(" recording the mix ");
}
static void w_rec_stop(void)                                /* ( -- ) end the recording; the track loops it */
{
    int n = lp_rec_stop();
    if (n < 0) { puts_(" not recording "); return; }
    forth_dot(n); puts_("frames ");
}
static void w_save(void)                                    /* ( track -- ) save its recording to the card */
{
    int t = forth_pop();
    if (forth_failed()) return;
    const char *e = lp_save(t - 1);
    if (e) { puts_(" "); puts_(e); puts_(" "); return; }
    puts_(" saved as "); puts_(lp_names[t - 1]); puts_(" ");
}
static void w_rescan(void)                                  /* ( -- ) re-read ZOOM_R8/LOOP into the pick list */
{
    if (!lp_running) { puts_(" LOOPER first "); return; }
    int n = lp_rescan();
    if (n < 0) puts_(" not now (a key is held, or no folder) "); else { forth_dot(n); puts_("files "); }
}
static void w_input(void)                                   /* ( -- ) input peaks since the last INPUT, counters */
{
    puts_(" peak L "); forth_dot((int)lp_in_peak[0]); puts_("R "); forth_dot((int)lp_in_peak[1]);
    lp_in_peak[0] = lp_in_peak[1] = 0;
    puts_("frames "); forth_dot((int)lp_in_frames); puts_("overruns "); forth_dot((int)lp_in_overruns);
    puts_("short "); forth_dot((int)lp_in_short); puts_("drops "); forth_dot((int)lp_in_drops);
    puts_("align tx "); forth_dot((int)lp_tx_fixes); puts_("rx "); forth_dot((int)lp_rx_fixes);
}
static void w_loopfiles(void)                               /* list ZOOM_R8/LOOP as numbered at LOOPER start */
{
    if (!lp_nfiles()) { puts_(" none (LOOPER scans the folder) "); return; }
    for (int k = 0; k < lp_nfiles(); k++) {
        puts_("\r\n "); forth_dot(k + 1); puts_(lp_file_name(k));
        for (int i = 0; i < LP_VOICES; i++) if (lp_track_state(i) == 1 && lp_track_file(i) == k) { puts_("  <- track "); forth_dot(i + 1); }
    }
}
static void w_load_loop(void)                               /* ( file track -- ) both from 1 */
{
    int t = forth_pop(), f = forth_pop();
    if (forth_failed()) return;
    if (!lp_running) { puts_(" LOOPER first "); return; }
    if (t < 1 || t > LP_VOICES || f < 1 || f > lp_nfiles()) { puts_(" no such file/track "); return; }
    lp_load_request(t - 1, f - 1);
    puts_(lp_track_state(t - 1) == 1 ? " loaded " : " failed ");
}
static u32 st_t, st_cyc, st_int;
static void w_loops(void)                                   /* status */
{
    puts_(lp_running ? " running" : " stopped");
    for (int i = 0; i < LP_VOICES; i++) {
        puts_("\r\n "); forth_dot(i + 1);
        if (lp_track_state(i) == 4) { puts_("recording, frames "); forth_dot((int)lp_rec_n); continue; }
        if (!lp_len[i]) { puts_("-"); continue; }
        puts_(lp_names[i]); puts_(" speed% "); forth_dot((int)(lp_speed(i) * 100.0f + (lp_speed(i) < 0 ? -0.5f : 0.5f)));
        puts_("level "); forth_dot(lp_fader_get(2 * i + 1));
    }
    puts_("\r\n master "); forth_dot(lp_fader_get(8));
    u32 now = TSCL, dt = now - st_t, dc = lp_isr_cycles - st_cyc, di = lp_ints - st_int;
    if (lp_ints - st_int > 8000u * 11) st_t = 0;            /* TSCL wraps every 11.9 s: no rate over longer gaps */
    puts_("ints "); forth_dot((int)lp_ints); puts_("underruns "); forth_dot((int)lp_underruns);
    puts_("frames "); forth_dot((int)lp_frames);
    if (st_t && dt) { puts_("isr-cpu% "); forth_dot((int)((unsigned long long)dc * 1000 / dt) / 10); puts_("ints/s "); forth_dot((int)((unsigned long long)di * CPU_HZ / dt)); }
    puts_("isr-max-cycles "); forth_dot((int)lp_isr_max);
    st_t = now; st_cyc = lp_isr_cycles; st_int = lp_ints;
}

int main(void)
{
    core_init(&sampler_app);
    forth_word("LOOPER", w_looper); forth_word("LOOPER-STOP", w_looper_stop); forth_word("LOOPS", w_loops);
    forth_word("FADER!", w_fader_store); forth_word("LOOPFILES", w_loopfiles); forth_word("LOAD-LOOP", w_load_loop);
    forth_word("LOOP-RESTART", w_loop_restart); forth_word("MONITOR", w_monitor); forth_word("REC", w_rec);
    forth_word("REC-MIX", w_rec_mix); forth_word("REC-STOP", w_rec_stop); forth_word("SAVE", w_save);
    forth_word("RESCAN", w_rescan); forth_word("INPUT", w_input);
    w_looper();                                             /* boot into the sampler */
    core_run();
}
