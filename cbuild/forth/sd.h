#ifndef SD_H
#define SD_H
#include "hw.h"
/* SD card on MMCSD1 (the R8's card slot: PINMUX18/19 mode 2 = CLK, CMD, DAT0-3), polled, 512-byte blocks. */
enum { SD_OK, SD_ENOCARD, SD_ETIMEOUT, SD_ECRC, SD_EINIT, SD_EDATA, SD_ERANGE, SD_EWRITE };
int sd_init(void);
int sd_read(u32 block, void *buf);            /* buf: 512 bytes, word aligned */
int sd_read_n(u32 block, u32 count, void *buf);   /* runs of <= 128 blocks (CMD18 + CMD12, DMATRIG) */
int sd_read_dma(u32 block, u32 count, void *buf); /* same runs, data moved by EDMA3 CC1 event 28 */
extern u32 sd_dma_last;
/* Experiments (read-only): one CMD18 for 2..128 blocks, CPU (dma = 0) or EDMA (1), with the flags in sd_xflags
   (bits of MMCCMD: PPLEN 0x80, DCLR 0x8000, DMATRIG 0x10000; default DCLR | PPLEN). May hit the 4 KB fault. */
extern u32 sd_xflags;
int sd_read_long(u32 block, u32 count, void *buf, int dma);
int sd_set_clkrt(u32 clkrt);                  /* card clock 90 MHz / (CLKRT + 1), CLKRT 3..255; default 3 (22.5 MHz) */
u32 sd_clkrt(void);
int sd_write_long(u32 block, u32 count, const void *buf, u32 limit);   /* one CMD25, 2..128 blocks, only in 1..limit-1 (<= 8191) */
int sd_write(u32 block, u32 count, const void *buf);   /* count blocks from buf (word aligned); waits until programmed */
extern u32 sd_last_status;                   /* card status (R1) from the last CMD13 */
extern u32 sd_blocks, sd_hc, sd_rca, sd_ready, sd_ocr, sd_cid[4], sd_csd[4], sd_last_st;
#endif
