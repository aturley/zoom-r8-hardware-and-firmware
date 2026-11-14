/* Board state captured from the running stock firmware (data/PERIPH.BIN, 2026-11-07). */
#ifndef STOCKSTATE_H
#define STOCKSTATE_H
/* GPIO bank pairs: offset of DIR from the GPIO base, DIR (1 = input), OUT_DATA */
static const u32 STOCK_GPIO[5][3] = {
    { 0x10, 0x07BCFC80u, 0x00400000u },
    { 0x38, 0x00002400u, 0x00004000u },
    { 0x60, 0x00000000u, 0x00000000u },
    { 0x88, 0x001E4C3Fu, 0xE1C10000u },
    { 0xB0, 0xFFFFFCE1u, 0x00000300u },
};
/* McASP0 transmit setup as stock (XFMT gets XBUSEL = 1 for CPU writes; receive side left in reset) */
#define STOCK_PFUNC      0x0000FFE9u
#define STOCK_PDIR       0x14000014u
#define STOCK_XMASK      0xFFFFFF00u
#define STOCK_XFMT       0x000180F0u
#define STOCK_AFSXCTL    0x00000113u
#define STOCK_ACLKXCTL   0x000000A3u
#define STOCK_AHCLKXCTL  0x00004000u
#define STOCK_XTDM       0x00000003u
#define STOCK_XCLKCHK    0x02FF0008u
#define STOCK_SRCTL1  0x0000000Eu
#define STOCK_SRCTL2  0x0000000Du
#define STOCK_SRCTL4  0x0000000Du
#endif
