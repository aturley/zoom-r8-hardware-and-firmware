/* R8 Forth: the core Forth app, run by RUN APP or the chooser (R8APPS/FORTH.BIN). A prompt on a USB serial port
   (/dev/cu.usbmodem* on a Mac) with the core words only (see core.c): SD and storage, panel, LCD, audio pass-through,
   audio clock, RELOAD, BYE. This is the place for ad hoc programs: add a C word below with forth_word(), or type
   Forth at the prompt. The looper lives in the sampler app (cbuild/sampler). */
#include "core.h"
#include "forth.h"
#include "sd.h"
#define puts_ core_puts

static void sd_rep(int e) { if (e) { puts_(" SD error "); forth_dot(e); puts_("(MMCST0 "); forth_hex(sd_last_st, 4); puts_(") "); } }

/* ---- SD read experiments (read-only). SD-RCMP ( block n mode -- bad ): read n blocks (2..128) one at a time
   (CMD17) into 0xC0100000, then with ONE CMD18 (mode 0 = CPU, 1 = EDMA) into 0xC0200000 using the flags set by
   SD-FLAGS, compare. Prints both times and rates. bad = -1 if identical, else the first differing byte offset
   (-2: refused, -3: reference read failed, -4: long read failed). SD-FLAGS ( mask -- ): MMCCMD flags for the long
   read, any of PPLEN 0x80, DCLR 0x8000, DMATRIG 0x10000 (ours: 8080; stock: 10000). SD-CLK ( clkrt -- ): card clock
   90 MHz / (clkrt + 1), 3..255 (3 = 22.5 MHz, the default since 2026-11-14; 4 = 18 MHz). SD-INIT puts the clock back to 3. */
static void rate(u32 bytes, u32 cyc)
{
    u32 us = cyc / (CPU_HZ / 1000000u);
    forth_dot((int)us); puts_("us ");
    if (us) { forth_dot((int)(bytes / us)); puts_("bytes/us "); }
}
static void w_sd_rcmp(void)
{
    int mode = forth_pop(); u32 n = (u32)forth_pop(), b = (u32)forth_pop();
    if (forth_failed()) return;
    if (n < 2 || n > 128 || mode < 0 || mode > 1) { puts_(" n 2..128, mode 0 (CPU) or 1 (DMA) "); forth_push(-2); return; }
    if (!sd_ready) { int e = sd_init(); sd_rep(e); if (e) { forth_push(-3); return; } }
    if (b >= sd_blocks || n > sd_blocks - b) { puts_(" beyond the card "); forth_push(-2); return; }
    u8 *ref = (u8 *)0xC0100000u, *lg = (u8 *)0xC0200000u;
    for (u32 i = 0; i < n * 512; i++) { ref[i] = 0x5A; lg[i] = 0xA5; }   /* different fills: an unwritten byte shows */
    u32 t0 = TSCL;
    int e = 0;
    for (u32 k = 0; k < n && !e; k++) e = sd_read(b + k, ref + k * 512);   /* reference: single blocks (CMD17) */
    u32 t1 = TSCL;
    if (e) { sd_rep(e); forth_push(-3); return; }
    puts_(" safe "); rate(n * 512, t1 - t0);
    t0 = TSCL;
    e = sd_read_long(b, n, lg, mode);
    t1 = TSCL;
    puts_(" long "); rate(n * 512, t1 - t0);
    if (e) { sd_rep(e); forth_push(-4); return; }
    for (u32 i = 0; i < n * 512; i++) if (ref[i] != lg[i]) { puts_(" differ at "); forth_dot((int)i); forth_push((int)i); return; }
    puts_(" same "); forth_push(-1);
}
static void w_sd_flags(void)
{
    u32 m = (u32)forth_pop();
    if (forth_failed()) return;
    if (m & ~(0x80u | 0x8000u | 0x10000u)) { puts_(" only 80 (PPLEN), 8000 (DCLR), 10000 (DMATRIG) "); return; }
    sd_xflags = m;
}
static void w_sd_clk(void)
{
    int c = forth_pop();
    if (forth_failed()) return;
    if (!sd_ready) { puts_(" SD-INIT first "); return; }
    if (sd_set_clkrt((u32)c)) puts_(" 3..255 ");
}

/* SD-WCMP ( block n seed -- bad ): write n blocks (2..128) of a seeded pattern with ONE CMD25 using the SD-FLAGS
   flags, read them back one block at a time (CMD17) and compare. Only in the unpartitioned gap: block >= 1 and
   block + n <= the first partition's start (read from the MBR now) and <= 8192. bad = -1 if every byte matches, else
   the first differing byte offset (-2: refused, -3: MBR or read-back failed, -4: write failed). */
static u32 mbr[128];
static void w_sd_wcmp(void)
{
    u32 seed = (u32)forth_pop(), n = (u32)forth_pop(), b = (u32)forth_pop();
    if (forth_failed()) return;
    if (!sd_ready) { int e = sd_init(); sd_rep(e); if (e) { forth_push(-3); return; } }
    int e = sd_read(0, mbr);
    if (e) { sd_rep(e); forth_push(-3); return; }
    const u8 *m = (const u8 *)mbr;
    u32 start = m[0x1C6] | m[0x1C7] << 8 | m[0x1C8] << 16 | (u32)m[0x1C9] << 24;   /* partition 1 first LBA */
    if (m[510] != 0x55 || m[511] != 0xAA || !start) { puts_(" no MBR partition table: refused "); forth_push(-2); return; }
    if (n < 2 || n > 128 || b < 1 || b + n > start || b + n > 8192) {
        puts_(" only blocks 1.."); forth_dot((int)((start < 8192 ? start : 8192) - 1)); puts_("(the gap), n 2..128 "); forth_push(-2); return;
    }
    u8 *w = (u8 *)0xC0100000u, *r = (u8 *)0xC0200000u;
    u32 x = seed * 2654435761u + 1;
    for (u32 i = 0; i < n * 512; i++) { x = x * 1103515245u + 12345u; w[i] = (u8)(x >> 16); r[i] = (u8)~w[i]; }
    u32 t0 = TSCL;
    e = sd_write_long(b, n, w, start);
    u32 t1 = TSCL;
    puts_(" write "); rate(n * 512, t1 - t0);
    if (e) { sd_rep(e); forth_push(-4); return; }
    for (u32 k = 0; k < n; k++) { e = sd_read(b + k, r + k * 512); if (e) { sd_rep(e); forth_push(-3); return; } }
    for (u32 i = 0; i < n * 512; i++) if (r[i] != w[i]) { puts_(" differ at "); forth_dot((int)i); forth_push((int)i); return; }
    puts_(" same "); forth_push(-1);
}

static const struct core_app forth_app = { "FORTH", "R8 Forth" };   /* no hooks: the console owns everything */

int main(void)
{
    core_init(&forth_app);
    /* ad hoc words go here, e.g. forth_word("MY-TEST", w_my_test); */
    forth_word("SD-RCMP", w_sd_rcmp); forth_word("SD-FLAGS", w_sd_flags); forth_word("SD-CLK", w_sd_clk); forth_word("SD-WCMP", w_sd_wcmp);
    core_run();
}
