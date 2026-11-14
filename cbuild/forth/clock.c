/* Audio master clock, as the stock AudioClkMng task sets it (0xC07EAE94 stop, 0xC07EAD4C start):
   board ID 7 (the straps GP0[7], GP1[5], GP1[4] all high) programs an I2C clock chip at 0x54 on I2C0;
   other boards enable one of two oscillators with GP1[15] (44.1 kHz family) or GP0[9] (48 kHz family).
   The McASP setup does not change with the rate: on hardware the switch works while audio keeps running.
   I2C0 is used as the stock boot left it (master, about 100 kHz); this code only polls it. */
#include "clock.h"

#define I2C0    0x01C22000u
#define ICSTR   (I2C0 + 0x08)
#define ICCNT   (I2C0 + 0x14)
#define ICSAR   (I2C0 + 0x1C)
#define ICDXR   (I2C0 + 0x20)
#define ICMDR   (I2C0 + 0x24)
#define ST_NACK 0x02u
#define ST_XRDY 0x10u
#define ST_SCD  0x20u
#define ST_BB   0x1000u
#define MDR_GO  0x6E20u                  /* FREE | STT | STP | MST | TRX | IRS */
#define MDR_STP 0x0800u
#define MDR_IDLE 0x4220u                 /* FREE | MST | IRS: how the stock boot leaves it */
#define CHIP    0x54u
#define PAIR01  (GPIO + 0x10)            /* banks 0/1 */

u32 clk_rate = 44100;

static int wait_st(u32 mask)             /* wait (<= 10 ms) for any of mask in ICSTR; returns ICSTR or 0 */
{
    u32 t0 = TSCL;
    for (;;) {
        u32 s = REG(ICSTR);
        if (s & mask) return (int)s;
        if (TSCL - t0 > CPU_HZ / 100) return 0;
    }
}
static int wait_mdr_clear(u32 bits)      /* wait (<= 10 ms) until those ICMDR bits clear */
{
    u32 t0 = TSCL;
    while (REG(ICMDR) & bits) if (TSCL - t0 > CPU_HZ / 100) return 0;
    return 1;
}
static void i2c_reset(void) { REG(ICMDR) = 0; REG(ICMDR) = MDR_IDLE; }   /* after a timeout: back to idle master */
static int finish(int e)
{
    /* As stock (0xC07E3564): the transfer is over only when ICMDR.STP clears. Starting the next one earlier loses
       its START: the controller then drops out of master mode with STT left pending (seen on hardware). */
    if (!wait_mdr_clear(MDR_STP)) e = -3;
    if (e == -3) i2c_reset();
    return e;
}
static int i2c_write(u32 reg, const u8 *d, int n)
{
    u32 t0 = TSCL;
    while (REG(ICSTR) & ST_BB) if (TSCL - t0 > CPU_HZ / 100) { i2c_reset(); return -3; }
    REG(ICSTR) = ST_NACK | ST_SCD;                       /* clear stale flags (write 1 to clear) */
    REG(ICSAR) = CHIP; REG(ICCNT) = (u32)n + 1; REG(ICMDR) = MDR_GO;
    for (int i = -1; i < n; i++) {
        int s = wait_st(ST_XRDY | ST_NACK);
        if (!s) return finish(-3);
        if (s & ST_NACK) { REG(ICMDR) |= MDR_STP; return finish(-2); }
        REG(ICDXR) = i < 0 ? reg : d[i];
    }
    int s = wait_st(ST_SCD | ST_NACK);
    if (!s) return finish(-3);
    if (s & ST_NACK) { REG(ICMDR) |= MDR_STP; return finish(-2); }
    return finish(0);
}
int clk_board(void)
{
    u32 in = REG(PAIR01 + G_IN);
    return (int)((in >> 7 & 1) | (in >> 21 & 1) << 1 | (in >> 20 & 1) << 2);
}
static int chip_start(u32 family, u32 v)
{
    static const u8 stop[1] = { 0x10 };
    int e = i2c_write(0xF7, stop, 1);
    if (e) return e;
    u8 b[11] = { 0x01, 0x0E, 0x00, 0x00, 0x05, 0x3B, 0x72, (u8)family, (u8)v, (u8)(v >> 8), (u8)(v >> 16 & 3) };
    return i2c_write(0xF5, b, 11);
}
int clk_raw(u32 family, u32 v)
{
    if (clk_board() != 7) return -1;
    return chip_start(family, v);
}
int clk_set_rate(u32 hz)
{
    if (hz != 44100 && hz != 48000) return -1;
    int e = 0;
    if (clk_board() == 7) e = hz == 44100 ? chip_start(0x41, 0x01CC5) : chip_start(0x47, 0x31D93);
    else {                                               /* break before make, as stock */
        u32 off = hz == 44100 ? 1u << 9 : 1u << 31, on = hz == 44100 ? 1u << 31 : 1u << 9;
        REG(PAIR01 + G_CLR) = off; delay_us(2000);
        REG(PAIR01 + G_SET) = on;  delay_us(35000);
    }
    if (!e) clk_rate = hz;
    return e;
}
