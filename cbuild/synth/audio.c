/* McASP0 transmit, polled through XBUF on the configuration bus, set up as the stock firmware has it
   (stockstate.h, captured from the running unit): I2S, two 32-bit slots, 24-bit left-justified, MSB first,
   bit clock = AHCLKX (external oscillator, input) / 4, frame sync generated, serializers 2 and 4 transmit. */
#include "audio.h"
#include "stockstate.h"
#define M(o) REG(MCASP0 + (o))
#define PFUNC 0x10
#define PDIR 0x14
#define GBLCTL 0x44
#define AMUTE 0x48
#define DLBCTL 0x4C
#define DITCTL 0x50
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
#define XBUF(n) (0x200 + 4 * (n))
#define XHCLKRST (1u << 9)
#define XCLKRST  (1u << 8)
#define XSRCLR   (1u << 10)
#define XSMRST   (1u << 11)
#define XFRST    (1u << 12)
int audio_clock_ok;
u32 audio_slots;

void board_state_stock(void)
{
    for (int i = 0; i < 5; i++) {                       /* output levels first, then directions */
        REG(GPIO + STOCK_GPIO[i][0] + G_OUT) = STOCK_GPIO[i][2];
        REG(GPIO + STOCK_GPIO[i][0] + G_DIR) = STOCK_GPIO[i][1];
    }
}
static int gbl(u32 bit)                                 /* set a transmit GBLCTL bit, wait (<= 5 ms) for it to latch */
{
    M(XGBLCTL) = (M(GBLCTL) & 0x1F00u) | bit;
    u32 t0 = TSCL;
    while (!(M(GBLCTL) & bit)) if (TSCL - t0 > CPU_HZ / 200) return 0;
    return 1;
}
void audio_init(void)
{
    psc_enable(PSC1, 7);
    M(GBLCTL) = 0; M(RGBLCTL) = 0; M(XGBLCTL) = 0;
    M(XMASK) = STOCK_XMASK;
    M(XFMT) = STOCK_XFMT | (1u << 3);                   /* XBUSEL: CPU writes XBUF on the configuration bus */
    M(AFSXCTL) = STOCK_AFSXCTL;
    M(ACLKXCTL) = STOCK_ACLKXCTL;
    M(AHCLKXCTL) = STOCK_AHCLKXCTL;                     /* AHCLKX is an input: never drive it */
    M(XTDM) = STOCK_XTDM;
    M(XINTCTL) = 0;
    M(XCLKCHK) = STOCK_XCLKCHK;
    M(XEVTCTL) = 1;                                     /* no DMA requests */
    for (int n = 0; n < 16; n++) M(SRCTL(n)) = 0;
    M(SRCTL(2)) = STOCK_SRCTL2; M(SRCTL(4)) = STOCK_SRCTL4;
    M(PFUNC) = STOCK_PFUNC; M(PDIR) = STOCK_PDIR;
    M(DITCTL) = 0; M(DLBCTL) = 0; M(AMUTE) = 0;
    int ok = gbl(XHCLKRST) && gbl(XCLKRST);
    M(XSTAT) = 0xFFFF;
    ok = ok && gbl(XSRCLR);
    M(XBUF(2)) = 0; M(XBUF(4)) = 0;                     /* service the first slot before starting */
    ok = ok && gbl(XSMRST) && gbl(XFRST);
    audio_clock_ok = ok;
}
int audio_ready(void) { return (M(XSTAT) >> 5) & 1; }  /* XDATA; cleared by the XBUF writes */
void audio_put(s32 s)
{
    u32 v = (u32)s << 8;
    M(XBUF(2)) = v; M(XBUF(4)) = v;
    audio_slots++;
}
