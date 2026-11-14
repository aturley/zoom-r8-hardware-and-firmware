/* R8 app chooser: started by RUN APP as A:\APP.BIN. Lists the apps (*.BIN) in A:\R8APPS by file name; the cursor
   keys (or the wheel) move the selection, ENTER runs the app, EXIT restarts into the stock R8 firmware.
   Running an app: its R8AP header and CRC are checked (reload.c, as RUN APP does), the path is left at LAUNCH_INFO
   for the app, and the image is copied over this program from a trampoline in DDR. */
#include "hw.h"
#include "lcd.h"
#include "panel.h"
#include "battery.h"
#include "sd.h"
#include "fat.h"
#include "reload.h"
#include "font.h"

void audio_pump(void) {}                                   /* lcd.c calls it; no audio here */
extern u8 __r8_bss_start[], __r8_bss_end[];

#define DIR      "R8APPS"
#define MAXAPPS  64
#define LIST_TOP 1                                         /* LCD rows 1..6 hold the list */
#define LIST_N   6
#define K_EXIT   0x93
#define K_UP     0x94
#define K_DOWN   0x95
#define K_RIGHT  0x96
#define K_LEFT   0x97
#define K_ENTER  0x98

static fat_ent apps[MAXAPPS];
static int napps, sel, top;
static const char *msg;                                    /* bottom row: a message, or the key hint */

static void row(int r, const char *s, int inverse)        /* 21 characters of 6 pixels */
{
    for (int c = 0; c < 21; c++) {
        int ch = *s ? (u8)*s++ : ' ';
        if (ch < 32 || ch > 126) ch = '?';
        for (int k = 0; k < 6; k++) fb[r * 128 + c * 6 + k] = (u8)(FONT6[ch - 32][k] ^ (inverse ? 0xFF : 0));
    }
    fb[r * 128 + 126] = fb[r * 128 + 127] = inverse ? 0xFF : 0;
    lcd_mark(r);
}
static int up(int c) { return c >= 'a' && c <= 'z' ? c - 32 : c; }
static int name_lt(const char *a, const char *b)
{
    for (; *a && up(*a) == up(*b); a++, b++) ;
    return up(*a) < up(*b);
}
static int is_app(const fat_ent *e)
{
    if (e->attr & 0x18 || e->name[0] == '.') return 0;    /* folders, labels, "._x" Mac files */
    int n = 0; while (e->name[n]) n++;
    return n > 4 && e->name[n - 4] == '.' && up(e->name[n - 3]) == 'B' && up(e->name[n - 2]) == 'I' && up(e->name[n - 1]) == 'N';
}
static void scan(void)
{
    fat_ent d, e; u32 pos = 0;
    napps = sel = top = 0;
    if (fat_mount()) { msg = "no SD card"; return; }
    if (fat_find(DIR, &d) || !(d.attr & 0x10)) { msg = "no R8APPS folder"; return; }
    while (fat_next(&d, &pos, &e)) {
        if (!is_app(&e) || napps >= MAXAPPS) continue;
        int k = napps++;
        while (k && name_lt(e.name, apps[k - 1].name)) { fat_copy(&apps[k], &apps[k - 1]); k--; }
        fat_copy(&apps[k], &e);
    }
    if (!napps) msg = "no apps in R8APPS";
}
static void draw(void)
{
    char t[24] = "R8 APPS";
    if (napps) {                                           /* "R8 APPS        3/12" */
        int n = 7; t[n++] = ' ';
        char num[8]; int p = 0, v = sel + 1; do num[p++] = (char)('0' + v % 10); while (v /= 10);
        num[p++] = '/'; char d2[4]; int q = 0; v = napps; do d2[q++] = (char)('0' + v % 10); while (v /= 10);
        int len = p + q; while (n < 21 - len) t[n++] = ' ';
        for (int i = p - 2; i >= 0; i--) t[n++] = num[i];
        t[n++] = '/';
        for (int i = q - 1; i >= 0; i--) t[n++] = d2[i];
        t[n] = 0;
    }
    row(0, t, 0);
    for (int i = 0; i < LIST_N; i++) {
        int k = top + i;
        row(LIST_TOP + i, k < napps ? apps[k].name : "", k < napps && k == sel);
    }
    row(7, msg ? msg : "ENTER run  EXIT quit", 0);
}
static void move(int d)
{
    if (!napps) return;
    sel += d;
    if (sel < 0) sel = 0;
    if (sel >= napps) sel = napps - 1;
    if (sel < top) top = sel;
    if (sel >= top + LIST_N) top = sel - LIST_N + 1;
    msg = 0;
}
static void show_now(void) { draw(); lcd_flush(); }
static void launch(void)
{
    if (!napps) return;
    static char path[64];
    int n = 0; for (const char *s = DIR "/"; *s; ) path[n++] = *s++;
    for (const char *s = apps[sel].name; *s && n < 63; ) path[n++] = *s++;
    path[n] = 0;
    msg = "loading..."; show_now();
    const char *e = reload_check(path, 0);
    if (e) { msg = e; return; }
    reload_set_launched(path);
    reload_go();                                           /* never returns */
}
/* power switch (as in every app): GP8[12] = on, GP7[0] holds the power */
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
    for (int i = 0; i < 1024; i++) fb[i] = 0;
    if (empty) row(3, "   BATTERY EMPTY", 0);
    for (int r = 0; r < 8; r++) lcd_mark(r);
    lcd_flush();
    REG(GPIO_B67 + G_CLR) = PWR_HOLD;
    for (;;) ;
}
int main(void)
{
    for (u8 *p = __r8_bss_start; p < __r8_bss_end; p++) *p = 0;
    TSCL = 0;
    battery_init();                                          /* stock's BATTERY setting, before DDR is reused */
    psc_enable(PSC1, 3); psc_enable(PSC1, 10); psc_enable(PSC1, 13);
    pinmux_stock();
    lcd_init();
    for (int r = 0; r < 8; r++) row(r, "", 0);
    row(0, "R8 APPS", 0); row(3, "  reading the card...", 0); lcd_flush();
    panel_init();
    scan();
    draw();
    u32 rd = panel_keyq_w;
    for (;;) {
        panel_poll();
        power_check();
        int changed = 0;
        while (rd != panel_keyq_w) {
            u8 k = panel_keyq[rd++ & 15]; changed = 1;
            if (k == K_UP) move(-1);
            else if (k == K_DOWN) move(1);
            else if (k == K_LEFT) move(-LIST_N);
            else if (k == K_RIGHT) move(LIST_N);
            else if (k == K_ENTER) launch();
            else if (k == K_EXIT) { msg = "restarting..."; show_now(); delay_us(200000); r8_restart(); }
        }
        if (wheel_delta) { int d = wheel_delta; wheel_delta = 0; move(d); changed = 1; }
        if (changed) draw();
        for (int i = 0; i < 16 && lcd_step(); i++) ;
    }
}
