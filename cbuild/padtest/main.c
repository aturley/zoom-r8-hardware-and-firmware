/* PADTEST.BIN: shows what the pads and keys send. Started from the app chooser (R8APPS).
   Screen: row 1 the pads held now; rows 2-7 a running list of panel events, newest at the top: pads ("P3 ON  v 87",
   "P3 OFF 0.42s" = how long it was held), keys ("A7 dn REPEAT/STOP", "A7 up REPEAT/STOP": status byte, down/up, name from the key
   map), the wheel, and any message of an unknown kind in hex. Faders, the periodic C1 and the start-up replies are
   left out of the list (but not the log).
   Every message from the panel is logged with a timestamp. EXIT twice (within 2 s) saves the log as PADnnnn.TXT in
   the card's root (the next free number) and restarts into the stock firmware; a single EXIT only shows in the
   list. The power switch turns the R8 off. */
#include "hw.h"
#include "lcd.h"
#include "panel.h"
#include "battery.h"
#include "fat.h"
#include "font.h"

void audio_pump(void) {}
extern u8 __r8_bss_start[], __r8_bss_end[];

/* ---- time: TSCL wraps every 12 s, so keep a running microsecond count */
static u32 t_last, t_us, t_frac;
static u32 now_us(void)
{
    u32 t = TSCL, d = t - t_last; t_last = t;
    t_frac += d; t_us += t_frac / (CPU_HZ / 1000000); t_frac %= CPU_HZ / 1000000;
    return t_us;
}

/* ---- the log: one line per panel message, "  12.345678 F0 00 01 57" (DDR, 4 MB) */
#define LOG_MEM ((char *)0xC0400000u)
#define LOG_MAX (4u << 20)
static u32 log_n, log_full;
static char *put(char *d, const char *s) { while (*s) *d++ = *s++; return d; }
static char *num(char *d, u32 v, int w, char pad)   /* right-aligned in w */
{
    char t[12]; int k = 0;
    do t[k++] = (char)('0' + v % 10); while (v /= 10);
    while (k < w) t[k++] = pad;
    while (k) *d++ = t[--k];
    return d;
}
static char *hex2(char *d, u32 v) { const char *h = "0123456789ABCDEF"; *d++ = h[v >> 4 & 15]; *d++ = h[v & 15]; return d; }
static void log_text(const char *s, int n)
{
    if (log_n + n > LOG_MAX) { log_full = 1; return; }
    for (int i = 0; i < n; i++) LOG_MEM[log_n++] = s[i];
}
/* the current message (status byte + data), for the log and for row 7 */
static u8 msg[24]; static int msg_n; static u32 msg_t, msg_last;   /* first and last byte times (us) */
static void list_msg(void);
static void msg_end(void)
{
    if (!msg_n) return;
    char line[96], *e = line;
    u32 t = msg_t;
    e = num(e, t / 1000000, 4, ' '); *e++ = '.'; e = num(e, t % 1000000, 6, '0');
    for (int i = 0; i < msg_n; i++) { *e++ = ' '; e = hex2(e, msg[i]); }
    *e++ = '\n';
    if (msg[0] != 0xC1) log_text(line, (int)(e - line));      /* C1 arrives every second: left out */
    list_msg();
    msg_n = 0;
}
static void raw(u8 b)                                  /* every byte from the panel (panel_raw) */
{
    if (b & 0x80) msg_end();
    u32 t = now_us();
    if (!msg_n) msg_t = t;
    msg_last = t;
    if (msg_n < (int)sizeof msg) msg[msg_n++] = b;
    if (msg_n == (int)sizeof msg) msg_end();
}

/* ---- screen */
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
/* the running list, newest first */
#define NEV 6
static char ev[NEV][22];
static int list_changed;
static void add_line(const char *l)
{
    for (int i = NEV - 1; i > 0; i--) for (int c = 0; c < 22; c++) ev[i][c] = ev[i - 1][c];
    int c = 0; while (l[c] && c < 21) { ev[0][c] = l[c]; c++; } ev[0][c] = 0;
    list_changed = 1;
}
static const char *const kname[0x28] = {     /* key IDs (status 0x80 + id), as in the work log's panel map */
    "ON/OFF 1", "EFFECT", "USB", "TOOL", "PROJECT", "ON/OFF 2", "?86", "RHYTHM", "TRACK", "PAN/EQ",
    "F1 AUTO PUNCH", "F2 A-B", "F3 |<<", "F4 >>|", "F5 MARK/CLR", "TRK1 status", "TRK2 status", "TRK3 status", "TRK4 status", "EXIT",
    "UP", "DOWN", "RIGHT", "LEFT", "ENTER", "TRK5 status", "TRK6 status", "TRK7 status", "TRK8 status", "MASTER status",
    "REW", "FF", "STOP", "PLAY", "REC", "SOFT1", "SOFT2", "SOFT3", "SOFT4", "REPEAT/STOP" };
static u32 down_at[9];
static u32 exit_at; static int exit_armed;
static void list_msg(void)                             /* one panel message (msg[0..msg_n)) -> list line(s) */
{
    char l[24], *e = l;
    u8 st = msg[0];
    if ((st >= 0xC1 && st <= 0xCB) || (st >= 0xE0 && st <= 0xEF) || !(st & 0x80)) return;   /* faders, C1, replies */
    if (st == 0xF0 && msg_n >= 4 && (msg_n - 1) % 3 == 0) {   /* F0 hi lo vv (hi lo vv ... with running status) */
      for (int g = 1; g < msg_n; g += 3) {
        u32 mask = ((u32)msg[g] << 4 | msg[g + 1]) & 0xFF, vel = msg[g + 2];
        for (u32 p = 1; p <= 8; p++) if (mask & (1u << (p - 1))) {
            e = l; *e++ = 'P'; e = num(e, p, 1, ' ');
            if (vel) { e = put(e, " ON  v "); e = num(e, vel, 3, ' '); down_at[p] = msg_t; }
            else {
                u32 ms = (msg_t - down_at[p]) / 1000;
                e = put(e, " OFF "); e = num(e, ms / 1000, 1, ' '); *e++ = '.'; e = num(e, ms % 1000, 3, '0'); *e++ = 's';
            }
            *e = 0; add_line(l);
        }
        if (!mask) { e = put(l, "P? mask "); e = hex2(e, msg[g]); *e++ = ' '; e = hex2(e, msg[g + 1]); *e = 0; add_line(l); }
      }
      return;
    }
    if (st >= 0x80 && st <= 0xA7 && msg_n == 2) {
        e = hex2(l, st); *e++ = ' ';
        e = msg[1] == 3 ? put(e, "dn") : msg[1] == 4 ? put(e, "up") : hex2(e, msg[1]);
        *e++ = ' '; e = put(e, kname[st - 0x80]); *e = 0; add_line(l);
        if (st == 0x93 && msg[1] == 3) {                /* EXIT: twice within 2 s quits */
            if (exit_armed && msg_t - exit_at < 2000000) exit_armed = 2;
            else { exit_armed = 1; exit_at = msg_t; }
        }
        return;
    }
    if (st == 0xD0 && msg_n >= 2) {
        e = put(l, "wheel "); int d = msg[1] < 64 ? msg[1] : msg[1] - 128;
        if (d < 0) { *e++ = '-'; d = -d; } else *e++ = '+';
        e = num(e, (u32)d, 1, ' '); *e = 0; add_line(l);
        return;
    }
    e = put(l, "?");                                   /* anything else, in hex */
    for (int i = 0; i < msg_n && e < l + 19; i++) { *e++ = ' '; e = hex2(e, msg[i]); }
    *e = 0; add_line(l);
}
static void draw(void)
{
    char t[24], *e = put(t, "held:");
    u32 d = pad_down;
    for (u32 p = 1; p <= 8; p++) if (d & (1u << p)) { *e++ = ' '; e = num(e, p, 1, ' '); }
    if (!d) e = put(e, " none");
    *e = 0;
    row(1, t, 0);
    for (int i = 0; i < NEV; i++) row(2 + i, ev[i], 0);
    if (exit_armed == 1 && now_us() - exit_at < 2000000) row(0, "EXIT again: save+quit", 1);
    else row(0, "PAD+KEY TEST  EXITx2", 1);
}

/* ---- saving the log as PADnnnn.TXT in the root */
static void save_log(void)
{
    msg_end();
    row(7, "saving the log...", 0); lcd_flush();
    fat_ent dir, e; u32 pos = 0;
    if (fat_mount() || fat_find("", &dir)) { row(7, "no card: not saved", 0); lcd_flush(); delay_us(2000000); return; }
    int top = 1;
    while (fat_next(&dir, &pos, &e)) {
        const char *n = e.name; int v = 0, ok = (n[0] & ~0x20) == 'P' && (n[1] & ~0x20) == 'A' && (n[2] & ~0x20) == 'D';
        for (int i = 3; i < 7 && ok; i++) { ok = n[i] >= '0' && n[i] <= '9'; v = v * 10 + n[i] - '0'; }
        if (ok && n[7] == '.' && v >= top) top = v + 1;
    }
    char n83[11] = { 'P', 'A', 'D', 0, 0, 0, 0, ' ', 'T', 'X', 'T' }, shown[13] = "PAD0000.TXT";
    for (int k = 6, v = top; k >= 3; k--, v /= 10) n83[k] = shown[k] = (char)('0' + v % 10);
    if (log_full) log_text("(log full)\n", 11);
    u32 n = log_n;
    while (log_n & 511) LOG_MEM[log_n++] = 0;          /* fat_create reads whole sectors */
    int r = fat_create(&dir, n83, LOG_MEM, n, 0);
    char t[24], *x = put(t, r ? "card error: not saved" : "saved "); if (!r) x = put(x, shown); *x = 0;
    row(7, t, 0); lcd_flush();
    delay_us(1500000);
}

/* power switch (as in every app): GP8[12] = on, GP7[0] holds the power */
#define PWR_SWITCH (1u << 12)
#define PWR_HOLD   (1u << 16)
static u32 sw_off_at;
static void save_log(void);
static void power_check(void)
{
    int empty = battery_poll() == BATT_EMPTY;              /* on batteries, below stock's cut-off for 4 s */
    if (!empty) {
        if (REG(GPIO_B8 + G_IN) & PWR_SWITCH) { sw_off_at = 0; return; }
        if (!sw_off_at) { sw_off_at = TSCL | 1; return; }
        if (TSCL - sw_off_at < CPU_HZ / 50) return;
    } else { row(0, "BATTERY EMPTY", 1); save_log(); }     /* keep the log, then switch off */
    for (int i = 0; i < 1024; i++) fb[i] = 0;
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
    row(0, "PAD+KEY TEST  EXITx2", 1); lcd_flush();
    t_last = TSCL;
    panel_raw = raw;
    { static const char hd[] = "time (s)    bytes (hex) from the panel, C1 left out\n"; log_text(hd, sizeof hd - 1); }
    panel_init();
    draw();
    u32 tdraw = TSCL;
    for (;;) {
        panel_poll();
        if (msg_n && now_us() - msg_last > 2000) msg_end();   /* a message also ends after 2 ms of quiet */
        power_check();
        if (exit_armed == 2) { save_log(); row(7, "restarting...", 0); lcd_flush(); delay_us(200000); r8_restart(); }
        now_us();                                      /* keeps the clock from missing a TSCL wrap */
        if (list_changed || TSCL - tdraw > CPU_HZ / 10) { list_changed = 0; tdraw = TSCL; draw(); }
        for (int i = 0; i < 16 && lcd_step(); i++) ;
    }
}
