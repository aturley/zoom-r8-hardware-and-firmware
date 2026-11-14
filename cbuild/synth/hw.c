#include "hw.h"
void delay_us(u32 us)
{
    u32 t0 = TSCL, n = us * (CPU_HZ / 1000000u);
    while (TSCL - t0 < n) ;
}
void psc_enable(u32 psc, int lpsc)
{
    if ((REG(psc + 0x800 + 4 * lpsc) & 0x3f) == 3) return;
    while (REG(psc + 0x128) & 1) ;                          /* PTSTAT: wait for idle */
    REG(psc + 0xA00 + 4 * lpsc) = (REG(psc + 0xA00 + 4 * lpsc) & ~0x1fu) | 3;
    REG(psc + 0x120) = 1;                                   /* PTCMD GO (power domain 0) */
    while (REG(psc + 0x128) & 1) ;                          /* PTSTAT */
    while ((REG(psc + 0x800 + 4 * lpsc) & 0x3f) != 3) ;
}
static const u32 PINMUX[20] = { 0x88111111, 0x88888888, 0x01141248, 0x44441111, 0, 0x88188188, 0x88888888,
    0x88888888, 0x88888888, 0x88888888, 0x88888888, 0x88888888, 0x88888888, 0x88888888, 0x00000088, 0x88888800,
    0x88888888, 0x00088888, 0x88822280, 0x18822288 };
void pinmux_stock(void)         /* the values stock R8SYS writes; PINMUX4 (board-revision dependent) left alone */
{
    REG(SYSCFG0 + 0x38) = 0x83E70B13; REG(SYSCFG0 + 0x3C) = 0x95A4F1E0;     /* KICK0/1 unlock */
    for (int i = 0; i < 20; i++) if (i != 4) REG(SYSCFG0 + 0x120 + 4 * i) = PINMUX[i];
}

/* Restart the R8 into its normal (stock) firmware, as at power-on: Timer64P1 as a watchdog with a short period,
   never serviced, resets the whole chip (confirmed on the R8). The PLL0 RSCTRL software reset is locked on this
   chip and does nothing. Timer 1 is also the looper's audio timer: stop audio first. Never returns. */
void r8_restart(void)
{
    const u32 T = 0x01C21000u;
    REG(T + 0x20) = 0; REG(T + 0x24) = 0;
    REG(T + 0x24) = 0xBu;                          /* TGCR: TIMMODE 2 (watchdog), TIM34RS, TIM12RS */
    REG(T + 0x10) = 0; REG(T + 0x14) = 0;
    REG(T + 0x18) = 0x6000u; REG(T + 0x1C) = 0;    /* about 1 ms at 24 MHz */
    REG(T + 0x28) = 0xA5C64000u;                   /* WDTCR: WDEN + first key */
    REG(T + 0x28) = 0xDA7E4000u;                   /* second key: the watchdog runs */
    for (;;) ;
}
