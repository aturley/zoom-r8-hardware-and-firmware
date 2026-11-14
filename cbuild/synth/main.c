/* Standalone R8 chord synth: faders 1-8 = voice levels, MASTER = level, STOP = mute; bars on the LCD. */
#include "hw.h"
#include "lcd.h"
#include "panel.h"
#include "battery.h"
#include "audio.h"
#include "synth.h"
extern u8 __r8_bss_start[], __r8_bss_end[];

#define RING 512                    /* stereo frames buffered ahead of the McASP (~11.6 ms) */
static s32 ring[RING * 2];
static u32 rd, wr;                  /* slot indices into ring (mod 2*RING) */
static int muted;
static u32 off_at;                  /* TSCL when power-off started (0 = running) */

void audio_pump(void)              /* XBUF holds one slot (no FIFO): anything slower than a slot must call this */
{
    while (audio_ready()) { audio_put(ring[rd]); rd = (rd + 1) % (RING * 2); }
}

static void draw_bar(int f, int v)
{
    int x = 12 + 12 * f, top = 63 - (v * 53) / 127;          /* value 127 reaches row 10 */
    for (int band = 1; band < 8; band++) {
        u8 m = 0;
        for (int bit = 0; bit < 8; bit++) if (band * 8 + bit >= top) m |= (u8)(1u << bit);
        for (int c = 0; c < 8; c++) fb[band * 128 + x + c] = m;
        lcd_mark(band);
        audio_pump();
    }
}
/* Power switch (from the stock shutdown routine): GP8[12] reads 1 while the switch is on; driving GP7[0] low
   cuts the power (the stock firmware's last step). Without this the unit stays on with the switch off. */
#define PWR_SWITCH (1u << 12)               /* bank 8 */
#define PWR_HOLD   (1u << 16)               /* bank 7 pin 0, in the bank 6/7 pair */
static u32 sw_off_at;                       /* TSCL when the switch was first seen off (0 = on) */
static int power_switch_off(void)           /* off for 20 ms in a row */
{
    if (REG(GPIO_B8 + G_IN) & PWR_SWITCH) { sw_off_at = 0; return 0; }
    if (!sw_off_at) { sw_off_at = TSCL | 1; return 0; }
    return TSCL - sw_off_at > CPU_HZ / 50;
}
static void power_off(void)
{
    for (int i = 0; i < 1024; i++) fb[i] = 0;
    lcd_text(3, 55, "OFF");                 /* stays up if something else keeps the power on */
    lcd_flush();
    REG(GPIO_B67 + G_CLR) = PWR_HOLD;
    for (;;) ;
}

static char rate_txt[6] = "     ";
static void draw_title(void)
{
    for (int i = 0; i < 128; i++) fb[i] = 0;
    audio_pump();
    lcd_text(0, 2, "R8 CHORD");
    lcd_text(0, 58, rate_txt);                              /* measured sample rate, or NOCLK */
    if (muted) lcd_text(0, 98, "MUTE");
}
static void show_rate(u32 hz)
{
    if (!hz) { const char *t = "NOCLK"; for (int i = 0; i < 5; i++) rate_txt[i] = t[i]; }
    else for (int i = 4; i >= 0; i--) { rate_txt[i] = (char)('0' + hz % 10); hz /= 10; audio_pump(); }
    draw_title();
}

int main(void)
{
    for (u8 *p = __r8_bss_start; p < __r8_bss_end; p++) *p = 0;
    TSCL = 0;
    battery_init();                                          /* stock's BATTERY setting, before DDR is reused */                                                /* start the time-stamp counter */
    psc_enable(PSC1, 3); psc_enable(PSC1, 10); psc_enable(PSC1, 13);
    pinmux_stock();
    board_state_stock();                                     /* oscillator / DAC control pins as stock runs them */
    lcd_init();
    for (int i = 0; i < 1024; i++) fb[i] = 0;
    draw_title();
    for (int f = 0; f < NFADERS; f++) draw_bar(f, 0);
    lcd_flush();
    panel_init();
    synth_init(FS);
    for (wr = 0; wr < RING * 2 - 2; wr += 2) synth_frame(&ring[wr], &ring[wr + 1]);   /* prefill */
    delay_us(50000);                                         /* let the oscillator settle */
    audio_init();
    u32 t_rate = TSCL, slots0 = audio_slots;
    for (;;) {
        if (TSCL - t_rate >= CPU_HZ) {                       /* once a second: measured frame rate */
            show_rate((audio_slots - slots0) / 2);
            t_rate += CPU_HZ; slots0 = audio_slots;
        }
        audio_pump();
        if ((wr - rd + RING * 2) % (RING * 2) < RING * 2 - 2) {        /* room for a frame */
            synth_frame(&ring[wr], &ring[wr + 1]); wr = (wr + 2) % (RING * 2);
            continue;                                               /* keep the ring full before anything else */
        }
        panel_poll();
        if (fader_changed) {
            for (int f = 0; f < NFADERS; f++)
                if (fader_changed & (1u << f)) {
                    fader_changed &= ~(1u << f);
                    if (f < 8) synth_set(f, fader[f]); else synth_master(fader[f]);
                    draw_bar(f, fader[f]);
                }
        }
        if (!off_at && (power_switch_off() || battery_poll() == BATT_EMPTY)) { off_at = TSCL | 1; synth_mute(1); }     /* fade out first */
        if (off_at && TSCL - off_at > CPU_HZ / 20) power_off();
        if (key_pressed) { key_pressed = 0; muted = !muted; synth_mute(muted); draw_title(); }
        lcd_step();
    }
}
