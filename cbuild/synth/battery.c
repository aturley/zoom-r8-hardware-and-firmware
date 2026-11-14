/* Low-battery detection for apps: see battery.h. */
#include "battery.h"
#include "panel.h"
#define BATT_SETTING ((volatile u32 *)0xC05B60C8u)      /* stock 1.17: the BATTERY setting word */
#define GP6_1 (1u << 1)                                 /* in the bank 6/7 IN register */
int batt_type = 1, batt_on_usb = 1;
u32 batt_empty_us = 4000000u;
static u32 last, shift = 7, below_us, t_frac;
static int state;
void battery_init(void)
{
    u32 v = *BATT_SETTING;
    batt_type = v == 0 ? 0 : 1;
    last = TSCL;
}
int battery_poll(void)
{
    u32 now = TSCL, d = now - last;
    if (d < CPU_HZ / 10) return state;                  /* every 100 ms */
    last = now;
    shift = (shift << 1 | ((REG(GPIO_B67 + G_IN) & GP6_1) ? 1 : 0)) & 7;   /* three equal samples, as stock */
    if (shift == 7) batt_on_usb = 1; else if (shift == 0) batt_on_usb = 0;
    u32 c1 = panel_c1;
    if (batt_on_usb || !c1) { below_us = 0; t_frac = 0; return state = BATT_OK; }
    u32 low = batt_type ? 233 : 213, empty = batt_type ? 228 : 203;
    if (c1 < empty) {
        t_frac += d; below_us += t_frac / (CPU_HZ / 1000000); t_frac %= CPU_HZ / 1000000;
        if (below_us >= batt_empty_us) return state = BATT_EMPTY;
    } else { below_us = 0; t_frac = 0; }
    return state = c1 < low ? BATT_LOW : BATT_OK;
}
