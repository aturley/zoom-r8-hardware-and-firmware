/* Snapshot of the audio-related peripherals while the stock firmware is running, to A:\PERIPH.BIN.
   Format: 'R8PD', then records { u32 addr, u32 len, u8 data[len] } ; len 0 marks a skipped (powered-down) block.
   Only reads registers without side effects: no McASP XBUF/RBUF (0x200-0x2FF), no data port, no I2C ICDRR,
   no SPI/UART data registers. A block is read only if its PSC module is enabled. */
#include "r8.h"
#define REG(a) (*(volatile u32 *)(a))
#define PSC0 0x01C10000u
#define PSC1 0x01E27000u
static u32 buf[1100];
static int fd, nrec;
static u32 written;

static int psc_on(u32 psc, int lpsc) { return lpsc < 0 || (REG(psc + 0x800 + 4 * lpsc) & 0x3f) == 3; }
static void put(const void *p, u32 n) { u32 w; r8_write(fd, p, n, &w); written += w; }
static void block(u32 addr, u32 len, u32 psc, int lpsc, u32 skip_lo, u32 skip_hi)
{
    int i2c = (addr == 0x01C22000u || addr == 0x01E28000u);    /* ICIVR (+0x28) read clears a flag: skip too */
    u32 hdr[2] = { addr, 0 };
    if (!psc_on(psc, lpsc)) { put(hdr, 8); nrec++; return; }
    hdr[1] = len; put(hdr, 8);
    for (u32 o = 0; o < len; o += 4) {
        buf[(o / 4) % 1024] = ((o >= skip_lo && o < skip_hi) || (i2c && o == 0x28)) ? 0xDEADDEADu : REG(addr + o);
        if ((o / 4) % 1024 == 1023 || o + 4 >= len) put(buf, ((o / 4) % 1024 + 1) * 4);
    }
    nrec++;
}
static u32 tscl(void) { extern cregister volatile unsigned int TSCL; return TSCL; }

u32 r8_main(u32 a, u32 b)
{
    if (r8_open(&fd, "A:\\PERIPH.BIN", 0x101, 0x80)) return 0xBAD00001u;
    put("R8PD", 4);
    block(PSC0 + 0x800, 0x40, 0, -1, 0, 0);            /* PSC0 MDSTAT */
    block(PSC0 + 0xA00, 0x40, 0, -1, 0, 0);            /* PSC0 MDCTL */
    block(PSC1 + 0x800, 0x80, 0, -1, 0, 0);            /* PSC1 MDSTAT */
    block(PSC1 + 0xA00, 0x80, 0, -1, 0, 0);            /* PSC1 MDCTL */
    block(0x01C11000u, 0x1A0, 0, -1, 0, 0);            /* PLL0 */
    block(0x01E1A000u, 0x1A0, 0, -1, 0, 0);            /* PLL1 */
    block(0x01C14000u, 0x200, 0, -1, 0, 0);            /* SYSCFG0: pinmux, CFGCHIPn */
    block(0x01E2C000u, 0x20, 0, -1, 0, 0);             /* SYSCFG1 */
    block(0x01D00000u, 0x200, PSC1, 7, 0, 0);          /* McASP0 control registers (not XBUF/RBUF) */
    block(0x01D01000u, 0x20, PSC1, 7, 0, 0);           /* McASP0 AFIFO */
    block(0x01E26000u, 0xD0, PSC1, 3, 0, 0);           /* GPIO */
    block(0x01C22000u, 0x64, 0, -1, 0x18, 0x1C);       /* I2C0: ICDRR (read side effect) skipped */
    block(0x01E28000u, 0x64, PSC1, 11, 0x18, 0x1C);    /* I2C1 */
    block(0x01800000u, 0x200, 0, -1, 0, 0);            /* DSP interrupt controller (event mux) */
    block(0x01C00000u, 0x1100, PSC0, 0, 0, 0);         /* EDMA3 CC0 registers */
    block(0x01C04000u, 0x1000, PSC0, 0, 0, 0);         /* EDMA3 CC0 PaRAM (128 sets) */
    block(0x01E30000u, 0x1100, PSC1, 0, 0, 0);         /* EDMA3 CC1 registers */
    block(0x01E34000u, 0x1000, PSC1, 0, 0, 0);         /* EDMA3 CC1 PaRAM */
    block(0x01C20000u, 0x80, 0, -1, 0, 0);             /* Timer64P0 */
    block(0x01C21000u, 0x80, 0, -1, 0, 0);             /* Timer64P1 */
    block(0x01D10000u, 0x80, PSC1, 14, 0x00, 0x08);    /* McBSP0 (skip DRR/DXR) */
    block(0x01D11000u, 0x80, PSC1, 15, 0x00, 0x08);    /* McBSP1 */
    /* McASP0 status and the first PaRAM sets again ~2 ms later, to see what is moving */
    u32 t0 = tscl(); for (volatile u32 i = 0; i < 400000u && tscl() - t0 < 720000u; i++) ;
    block(0x01D00000u, 0x200, PSC1, 7, 0, 0);
    block(0x01C04000u, 0x1000, PSC0, 0, 0, 0);
    r8_close(fd);
    return 0x600D0000u | (u32)nrec;
}
