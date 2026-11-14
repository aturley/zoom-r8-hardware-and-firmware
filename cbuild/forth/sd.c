/* MMCSD1 SD card driver: identification at 400 kHz, then 4-bit bus at 22.5 MHz (CLKRT 3, as stock for a 25 MHz card; 18 MHz before 2026-11-14). Multi-block data commands carry
   DMATRIG (bit 16), as stock's do: without it the controller fails every multi-block transfer of 4 KB or more
   (found on the R8 2026-11-14), with it runs of up to 128 blocks are exact in both directions.
   Controller clock: PLL0 SYSCLK2 = 180 MHz (PERIPH.BIN: PLLM 30, POSTDIV 2, PLLDIV2 2); card clock =
   180 MHz / (2 * (CLKRT + 1)). MMCST0 bits clear when read, so the status is accumulated while waiting. */
#include "sd.h"
#define SD 0x01E1B000u
#define S(o) REG(SD + (o))
#define MMCCTL 0x00
#define MMCCLK 0x04
#define MMCST0 0x08
#define MMCST1 0x0C
#define MMCIM 0x10
#define MMCTOR 0x14
#define MMCTOD 0x18
#define MMCBLEN 0x1C
#define MMCNBLK 0x20
#define MMCDRR 0x28
#define MMCCMD 0x30
#define MMCARGHL 0x34
#define MMCRSP(n) (0x38 + 4 * (n))      /* RSP01, RSP23, RSP45, RSP67 */
#define MMCFIFOCTL 0x74
/* MMCST0 */
#define DATDNE (1u << 0)
#define BSYDNE (1u << 1)
#define RSPDNE (1u << 2)
#define TOUTRD (1u << 3)
#define TOUTRS (1u << 4)
#define CRCWR  (1u << 5)
#define CRCRD  (1u << 6)
#define CRCRS  (1u << 7)
#define DRRDY  (1u << 10)
/* MMCST1 (live) */
#define FIFOEMP (1u << 5)
#define FIFOFUL (1u << 6)                  /* live; the FIFO holds 64 bytes (measured on the R8) */
#define MMCDXR 0x2C
/* MMCCMD */
#define PPLEN  (1u << 7)
#define BSYEXP (1u << 8)
#define R1 (1u << 9)                    /* 48-bit with CRC: R1, R1b, R6, R7 */
#define R2 (2u << 9)                    /* 136-bit */
#define R3 (3u << 9)                    /* 48-bit, no CRC */
#define DTRW   (1u << 11)                  /* 1 = write */
#define WDATX  (1u << 13)                  /* data transfer (verified on hardware; Linux davinci_mmc agrees) */
#define INITCK (1u << 14)
#define DCLR   (1u << 15)
#define DMATRIG (1u << 16)                 /* stock sets it on every command (firmware, 2026-11-13) */
#define DATA_FLAGS DMATRIG                 /* flags for multi-block data commands (hardware 2026-11-14: DMATRIG alone) */
#define CLK_INIT 224u                   /* 400 kHz */
#define CLK_FAST 3u                     /* 22.5 MHz: stock's CLKRT for TRAN_SPEED 0x32 (25 MHz) */

static void ctl_reset(void);
u32 sd_last_status;
u32 sd_blocks, sd_hc, sd_rca, sd_ready, sd_ocr, sd_cid[4], sd_csd[4], sd_last_st;

static int cmd(u32 c, u32 arg, u32 flags)
{
    (void)S(MMCST0);                                       /* clear stale status */
    S(MMCARGHL) = arg;
    S(MMCCMD) = c | flags | PPLEN;
    u32 st = 0, t0 = TSCL;
    for (;;) {
        st |= S(MMCST0);
        if (st & (TOUTRS | CRCRS)) break;
        if ((st & RSPDNE) && (!(flags & BSYEXP) || (st & BSYDNE))) break;
        if (TSCL - t0 > CPU_HZ / 10) { sd_last_st = st; return SD_ETIMEOUT; }
    }
    sd_last_st = st;
    if (st & TOUTRS) return SD_ETIMEOUT;
    if ((st & CRCRS) && (flags & (3u << 9)) != R3) return SD_ECRC;
    return SD_OK;
}
static void ctl_reset(void);
static u32 rsp(void) { return S(MMCRSP(3)); }             /* 48-bit responses: card bits 39..8 */
static int acmd(u32 c, u32 arg, u32 flags)
{
    int e = cmd(55, sd_rca << 16, R1);
    return e ? e : cmd(c, arg, flags);
}
static void set_clock(u32 clkrt)
{
    S(MMCCLK) = clkrt;                                     /* clock off while changing */
    S(MMCCLK) = clkrt | (1u << 8);                         /* CLKEN */
}

int sd_init(void)
{
    sd_ready = 0; sd_rca = 0; sd_hc = 0; sd_blocks = 0;
    if ((REG(PSC1 + 0x800 + 4 * 18) & 0x1F) != 3) {       /* MMCSD1 = PSC1 module 18 */
        u32 t0 = TSCL;
        while ((REG(PSC1 + 0x128) & 1) && TSCL - t0 < CPU_HZ / 100) ;
        REG(PSC1 + 0xA00 + 4 * 18) = (REG(PSC1 + 0xA00 + 4 * 18) & ~0x1Fu) | 3;
        REG(PSC1 + 0x120) = 1;
        while ((REG(PSC1 + 0x800 + 4 * 18) & 0x1F) != 3 && TSCL - t0 < CPU_HZ / 100) ;
    }
    S(MMCCTL) = 3;                                         /* DATRST | CMDRST */
    set_clock(CLK_INIT);
    S(MMCTOR) = 0x1FFF;                                    /* response timeout 255 clocks, data timeout high bits */
    S(MMCTOD) = 0xFFFF;
    S(MMCIM) = 0;
    delay_us(10);
    S(MMCCTL) = 0;                                         /* out of reset, 1-bit bus */
    delay_us(1000);
    cmd(0, 0, INITCK);                                     /* GO_IDLE_STATE after >= 74 clocks; no response */
    int v2 = cmd(8, 0x1AA, R1) == SD_OK && (rsp() & 0xFFF) == 0x1AA;   /* SEND_IF_COND: SD 2.0 card */
    u32 t0 = TSCL;
    for (;;) {                                             /* ACMD41 until the card is ready (<= 1 s) */
        int e = acmd(41, (v2 ? 0x40000000u : 0) | 0x00FF8000u, R3);
        if (e == SD_ETIMEOUT && TSCL - t0 > CPU_HZ / 10 && !sd_ocr) return SD_ENOCARD;
        sd_ocr = rsp();
        if (!e && (sd_ocr & 0x80000000u)) break;
        if (TSCL - t0 > CPU_HZ) return SD_EINIT;
        delay_us(10000);
    }
    sd_hc = (sd_ocr >> 30) & 1;
    if (cmd(2, 0, R2)) return SD_EINIT;                    /* ALL_SEND_CID */
    for (int i = 0; i < 4; i++) sd_cid[i] = S(MMCRSP(3 - i));
    if (cmd(3, 0, R1)) return SD_EINIT;                    /* SEND_RELATIVE_ADDR */
    sd_rca = rsp() >> 16;
    if (cmd(9, sd_rca << 16, R2)) return SD_EINIT;         /* SEND_CSD */
    for (int i = 0; i < 4; i++) sd_csd[i] = S(MMCRSP(3 - i));
    if ((sd_csd[0] >> 30) == 1) {                          /* CSD 2.0: C_SIZE bits 69..48 */
        u32 c_size = ((sd_csd[1] & 0x3F) << 16) | (sd_csd[2] >> 16);
        sd_blocks = (c_size + 1) * 1024;
    } else {                                               /* CSD 1.0 */
        u32 read_bl_len = (sd_csd[1] >> 16) & 0xF;
        u32 c_size = ((sd_csd[1] & 0x3FF) << 2) | (sd_csd[2] >> 30);
        u32 mult = (sd_csd[2] >> 15) & 7;
        sd_blocks = ((c_size + 1) << (mult + 2)) << read_bl_len >> 9;
    }
    if (cmd(7, sd_rca << 16, R1 | BSYEXP)) return SD_EINIT;    /* SELECT_CARD */
    if (acmd(6, 2, R1)) return SD_EINIT;                   /* 4-bit bus */
    S(MMCCTL) = 1u << 2;                                   /* WIDTH0 */
    if (!sd_hc && cmd(16, 512, R1)) return SD_EINIT;       /* SET_BLOCKLEN (fixed at 512 on SDHC) */
    set_clock(CLK_FAST);
    sd_ready = 1;
    return SD_OK;
}

int sd_read(u32 block, void *buf)
{
    if (!sd_ready) return SD_ENOCARD;
    if (block >= sd_blocks) return SD_ERANGE;
    u32 *p = (u32 *)buf, n = 0;
    S(MMCBLEN) = 512; S(MMCNBLK) = 1;
    S(MMCFIFOCTL) = 1;                                     /* FIFORST */
    S(MMCFIFOCTL) = 1u << 2;                               /* read, 32-byte level, 4-byte access */
    (void)S(MMCST0);
    S(MMCARGHL) = sd_hc ? block : block * 512;
    S(MMCCMD) = 17 | R1 | WDATX | DCLR | PPLEN;            /* READ_SINGLE_BLOCK */
    u32 st = 0, t0 = TSCL;
    for (;;) {                                             /* DRRDY is a latched one-shot, so drain on MMCST1.FIFOEMP */
        st |= S(MMCST0);
        while (n < 128 && !(S(MMCST1) & FIFOEMP)) p[n++] = S(MMCDRR);
        if (st & (TOUTRS | CRCRS | TOUTRD | CRCRD)) break;
        if ((st & DATDNE) && n >= 128) break;
        if (TSCL - t0 > CPU_HZ / 4) { sd_last_st = st; ctl_reset(); return SD_ETIMEOUT; }
    }
    sd_last_st = st;
    if (st & (TOUTRS | TOUTRD)) return SD_ETIMEOUT;
    if (st & (CRCRS | CRCRD)) return SD_ECRC;
    return n == 128 ? SD_OK : SD_EDATA;
}

/* Multi-block read: CMD18 with MMCNBLK = n, drain n * 512 bytes, then CMD12, in runs of at most SD_RMAX blocks.
   With DATA_FLAGS (DMATRIG) a run can be long: measured on the R8 2026-11-14, one CMD18 of 128 blocks is exact at
   8.3-8.6 MB/s (18 MHz). Without DMATRIG the controller stalls any run of 4 KB or more (one byte at 4094 goes
   missing), whoever drains the FIFO; that is why runs used to be capped at 7 blocks (3.45 MB/s). */
#define SD_RMAX 128u                       /* 64 KB: the largest run tested on the R8 */
static int read_run(u32 block, u32 count, void *buf, u32 flags);
int sd_read_n(u32 block, u32 count, void *buf)
{
    if (!sd_ready) return SD_ENOCARD;
    if (!count || block >= sd_blocks || count > sd_blocks - block) return SD_ERANGE;
    u8 *p = (u8 *)buf;
    while (count) {
        u32 n = count < SD_RMAX ? count : SD_RMAX;
        int e = n == 1 ? sd_read(block, p) : read_run(block, n, p, DATA_FLAGS);
        if (e) return e;
        block += n; count -= n; p += n * 512;
    }
    return SD_OK;
}
static int read_run(u32 block, u32 count, void *buf, u32 flags)
{
    u32 *p = (u32 *)buf, n = 0, total = count * 128;
    S(MMCBLEN) = 512; S(MMCNBLK) = count;
    S(MMCFIFOCTL) = 1;                                     /* FIFORST */
    S(MMCFIFOCTL) = 1u << 2;                               /* read, 32-byte level, 4-byte access */
    (void)S(MMCST0);
    S(MMCARGHL) = sd_hc ? block : block * 512;
    S(MMCCMD) = 18 | R1 | WDATX | flags;                   /* READ_MULTIPLE_BLOCK */
    u32 st = 0, t0 = TSCL;
    for (;;) {
        st |= S(MMCST0);
        while (n < total && !(S(MMCST1) & FIFOEMP)) p[n++] = S(MMCDRR);
        if (st & (TOUTRS | CRCRS | TOUTRD | CRCRD)) break;
        if ((st & DATDNE) && n >= total) break;
        if (TSCL - t0 > CPU_HZ / 4 + CPU_HZ / 1000 * count) { st |= TOUTRD; break; }
    }
    sd_last_st = st;
    int e = cmd(12, 0, R1);                                /* STOP_TRANSMISSION, also after an error */
    if (st & (TOUTRS | TOUTRD | CRCRS | CRCRD) || e) ctl_reset();
    if (st & (TOUTRS | TOUTRD)) return SD_ETIMEOUT;
    if (st & (CRCRS | CRCRD)) return SD_ECRC;
    if (e) return e;
    return n == total ? SD_OK : SD_EDATA;
}

/* ---- EDMA3 CC1 (0x01E30000) for the data phase. MMCSD1 raises CC1 event 28 (receive; confirmed on the R8: one
   CPU-drained block read latches ER bit 28) whenever the FIFO reaches its 32-byte level. CC1 has no channel map, so
   channel 28 uses PaRAM set 28. Each event moves 16 words from MMCDRR (A-B synchronised, source fixed). Completion is
   polled in IPR (TCC 28), no interrupt. The buffer is in cacheable DDR: L1D is written back and invalidated before
   (no dirty line may later land on top of DMA data) and after (no stale line may hide it). */
#define CC1      0x01E30000u
#define CC_ECR   (CC1 + 0x1008)
#define CC_EECR  (CC1 + 0x1028)
#define CC_EESR  (CC1 + 0x1030)
#define CC_SECR  (CC1 + 0x1040)
#define CC_IPR   (CC1 + 0x1068)
#define CC_ICR   (CC1 + 0x1070)
#define CC_EMR   (CC1 + 0x0300)
#define CC_EMCR  (CC1 + 0x0308)
#define PARAM(n) (CC1 + 0x4000 + 32 * (n))
#define EV_RX    28u
#define L1DWBINV 0x01845044u
static void l1d_wbinv(void) { REG(L1DWBINV) = 1; while (REG(L1DWBINV)) ; }
u32 sd_dma_last;                                           /* debug: events left (CCNT) when a DMA read ended */
static int wait_ready(u32 ms);
static int dma_run(u32 block, u32 count, void *buf, u32 flags);
int sd_read_dma(u32 block, u32 count, void *buf)
{
    if (!sd_ready) return SD_ENOCARD;
    if (!count || block >= sd_blocks || count > sd_blocks - block) return SD_ERANGE;
    u8 *p = (u8 *)buf;
    while (count) {
        u32 n = count < SD_RMAX ? count : SD_RMAX;
        int e = dma_run(block, n, p, DATA_FLAGS);
        if (e) return e;
        block += n; count -= n; p += n * 512;
    }
    return SD_OK;
}
/* FIFOCTL bit 2 set = a 64-byte threshold on this controller (not 32: with 32 bytes per event the DMA stalls after
   one event, measured), so each event moves the full 64 bytes. */
static int dma_run(u32 block, u32 count, void *buf, u32 flags)
{
    u32 ev = 64, m = 1u << EV_RX, total32 = count * 512 / ev;
    volatile u32 *pa = (volatile u32 *)PARAM(EV_RX);
    l1d_wbinv();
    REG(CC_EECR) = m; REG(CC_ECR) = m; REG(CC_SECR) = m; REG(CC_ICR) = m; REG(CC_EMCR) = m;
    pa[0] = 1u << 2 | 1u << 20 | EV_RX << 12;              /* OPT: SYNCDIM (A-B), TCINTEN, TCC 28 */
    pa[1] = SD + MMCDRR;                                   /* SRC */
    pa[2] = (ev / 4) << 16 | 4u;                           /* BCNT = words per event, ACNT 4 */
    pa[3] = (u32)buf;                                      /* DST */
    pa[4] = 4u << 16 | 0u;                                 /* DBIDX 4, SBIDX 0 */
    pa[5] = (ev / 4) << 16 | 0xFFFFu;                      /* BCNTRLD, no link */
    pa[6] = ev << 16 | 0u;                                 /* DCIDX = bytes per event, SCIDX 0 */
    pa[7] = total32;                                       /* CCNT */
    REG(CC_EESR) = m;
    S(MMCBLEN) = 512; S(MMCNBLK) = count;
    S(MMCFIFOCTL) = 1;                                     /* FIFORST */
    S(MMCFIFOCTL) = 1u << 2;                               /* read, 64-byte threshold, 4-byte access */
    (void)S(MMCST0);
    S(MMCARGHL) = sd_hc ? block : block * 512;
    S(MMCCMD) = (count > 1 ? 18 : 17) | R1 | WDATX | flags;
    u32 st = 0, t0 = TSCL, lim = CPU_HZ / 4 + CPU_HZ / 1000 * count;
    for (;;) {
        st |= S(MMCST0);
        if (st & (TOUTRS | CRCRS | TOUTRD | CRCRD)) break;
        if ((st & DATDNE) && (REG(CC_IPR) & m)) break;
        if (TSCL - t0 > lim) { st |= S(MMCST0) | TOUTRD; break; }
    }
    sd_dma_last = pa[7];
    REG(CC_EECR) = m;
    sd_last_st = st;
    int bad = (st & (TOUTRS | TOUTRD | CRCRS | CRCRD)) != 0;
    int e = (count > 1 || bad) ? cmd(12, 0, R1) : 0;       /* STOP also after a failed single block: card back to tran */
    if (bad || e) { ctl_reset(); wait_ready(100); }
    l1d_wbinv();
    if (REG(CC_EMR) & m) { REG(CC_EMCR) = m; return SD_EDATA; }   /* an event was missed */
    if (st & (TOUTRS | TOUTRD)) return SD_ETIMEOUT;
    if (st & (CRCRS | CRCRD)) return SD_ECRC;
    return e ? e : SD_OK;
}

/* ---- writes. The whole transfer is in memory before the command goes out; the FIFO is filled after MMCCMD is
   written (DCLR in the command empties the FIFO, so a pre-fill is lost: found on the R8) and topped up while
   MMCST1.FIFOFUL is clear. When the FIFO runs dry the controller stops the card clock (CLKSTP) and waits.
   After any failure the controller's data and command logic is reset, or the next transfer hangs.
   Without DMATRIG the R8's controller corrupts multi-block writes of 4 KB or more: a zero byte is inserted at byte
   4095 of the transfer and the rest shifts by one (measured 2026-11-07). With DATA_FLAGS (DMATRIG) runs of up to 128
   blocks are exact (measured 2026-11-14, 5.4-6.4 MB/s at 18 MHz). Requests are split into runs of SD_WMAX blocks.
   CMD24 for one block, CMD25 + CMD12 for several; then CMD13 until the card is back in the transfer state with
   READY_FOR_DATA, which covers the programming time, and its error bits are checked. */
static void ctl_reset(void)         /* DATRST | CMDRST pulse, bus width kept */
{
    u32 w = S(MMCCTL) & (1u << 2);
    S(MMCCTL) = w | 3; delay_us(10); S(MMCCTL) = w;
}
#define ST_ERRORS 0x7DF80000u               /* card status error bits 30..26, 24..19 (31 OUT_OF_RANGE: we range-check) */
static int wait_ready(u32 ms)
{
    u32 t0 = TSCL;
    for (;;) {
        int e = cmd(13, sd_rca << 16, R1);
        if (!e) {
            u32 s = sd_last_status = rsp();
            if (s & ST_ERRORS) return SD_EWRITE;
            if (((s >> 9) & 15) == 4 && (s & 0x100)) return SD_OK;
        }
        if (TSCL - t0 > CPU_HZ / 1000 * ms) return SD_ETIMEOUT;
    }
}
#define SD_WMAX 128u                        /* 64 KB: the largest run tested on the R8 */
static int write_run(u32 block, u32 count, const void *buf, u32 flags)
{
    int e = wait_ready(500);
    if (e) return e;
    const u32 *p = (const u32 *)buf;
    u32 n = 0, total = count * 128;
    S(MMCBLEN) = 512; S(MMCNBLK) = count;
    S(MMCFIFOCTL) = 1;                                     /* FIFORST */
    S(MMCFIFOCTL) = 2u | 1u << 2;                          /* write direction, 4-byte access */
    (void)S(MMCST0);
    S(MMCARGHL) = sd_hc ? block : block * 512;
    S(MMCCMD) = (count > 1 ? 25 : 24) | R1 | WDATX | DTRW | flags;
    u32 st = 0, t0 = TSCL, late = 0;
    for (;;) {
        st |= S(MMCST0);
        while (n < total && !(S(MMCST1) & FIFOFUL)) S(MMCDXR) = p[n++];
        if (st & (TOUTRS | CRCRS | CRCWR | TOUTRD)) break;
        if ((st & DATDNE) && n >= total) break;
        if (TSCL - t0 > CPU_HZ) { late = 1; break; }
    }
    int stop = count > 1 || (st & (CRCWR | TOUTRD)) || late || n < total;   /* the card may still be receiving */
    st |= S(MMCST0);                                       /* before cmd() clears it */
    if (stop) cmd(12, 0, R1);                              /* STOP_TRANSMISSION (busy covered by CMD13 below) */
    int r = wait_ready(1000);
    st |= S(MMCST0);                                       /* a CRC status error can come after DATDNE */
    sd_last_st = st;
    int bad = late || (st & (TOUTRS | TOUTRD | CRCRS | CRCWR)) || n < total;
    if (bad) ctl_reset();
    S(MMCFIFOCTL) = 1; S(MMCFIFOCTL) = 1u << 2;            /* FIFO back to read direction */
    if (late || (st & (TOUTRS | TOUTRD))) return SD_ETIMEOUT;
    if (st & (CRCRS | CRCWR)) return SD_ECRC;
    if (n < total) return SD_EDATA;
    return r;
}
int sd_write(u32 block, u32 count, const void *buf)
{
    if (!sd_ready) return SD_ENOCARD;
    if (!count || block >= sd_blocks || count > sd_blocks - block) return SD_ERANGE;
    const u8 *p = (const u8 *)buf;
    while (count) {
        u32 n = count < SD_WMAX ? count : SD_WMAX;
        int e = write_run(block, n, p, DATA_FLAGS);
        if (e) return e;
        block += n; count -= n; p += n * 512;
    }
    return SD_OK;
}

/* ---- experiments (SD-RCMP, SD-FLAGS, SD-CLK in the Forth core): one CMD18 for the whole request, as stock does
   (firmware, 2026-11-13: block read 0xC07A0428 sends one CMD18 with NBLK = the full count, command word 0x2212 = no
   DCLR, no PPLEN, plus DMATRIG on every command, DMA for the data, card in high-speed mode). Our runs stay <= 7 blocks
   because of the 4 KB fault; these let us test whether stock's flags avoid it. Read-only. */
u32 sd_xflags = DCLR | PPLEN;                              /* data-command flags for sd_read_long (our usual ones) */
int sd_read_long(u32 block, u32 count, void *buf, int dma)
{
    if (!sd_ready) return SD_ENOCARD;
    if (count < 2 || count > 128 || block >= sd_blocks || count > sd_blocks - block) return SD_ERANGE;
    u32 f = sd_xflags & (DCLR | PPLEN | DMATRIG);
    return dma ? dma_run(block, count, buf, f) : read_run(block, count, buf, f);
}
int sd_set_clkrt(u32 clkrt)                                /* card clock = 90 MHz / (CLKRT + 1); 3..255 (<= 22.5 MHz) */
{
    if (clkrt < 3 || clkrt > 255) return -1;
    set_clock(clkrt);
    return 0;
}
u32 sd_clkrt(void) { return S(MMCCLK) & 0xFF; }
/* One CMD25 for 2..128 blocks with the sd_xflags flags (write experiment). Refused outside blocks 1..limit-1, where
   the caller passes the first partition's start (at most 8192): it can never touch the filesystem. */
int sd_write_long(u32 block, u32 count, const void *buf, u32 limit)
{
    if (!sd_ready) return SD_ENOCARD;
    if (limit > 8192) limit = 8192;
    if (count < 2 || count > 128 || block < 1 || block + count > limit) return SD_ERANGE;
    return write_run(block, count, buf, sd_xflags & (DCLR | PPLEN | DMATRIG));
}
