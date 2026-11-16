/* R8 Forth: the core Forth app, run by RUN APP or the chooser (R8APPS/FORTH.BIN). A prompt on a USB serial port
   (/dev/cu.usbmodem* on a Mac) with the core words only (see core.c): SD and storage, panel, LCD, audio pass-through,
   audio clock, RELOAD, BYE. This is the place for ad hoc programs: add a C word below with forth_word(), or type
   Forth at the prompt. The looper lives in the sampler app (cbuild/sampler). */
#include "core.h"
#include "forth.h"
#include "sd.h"
#include "fat.h"
#define puts_ core_puts

static void sd_rep(int e) { if (e) { puts_(" SD error "); forth_dot(e); puts_("(MMCST0 "); forth_hex(sd_last_st, 4); puts_(") "); } }

/* ---- SD read experiments (read-only). SD-RCMP ( block n mode -- bad ): read n blocks (2..128) one at a time
   (CMD17) into 0xC0100000, then with ONE CMD18 (mode 0 = CPU, 1 = EDMA) into 0xC0200000 using the flags set by
   SD-FLAGS, compare. Prints both times and rates. bad = -1 if identical, else the first differing byte offset
   (-2: refused, -3: reference read failed, -4: long read failed). SD-FLAGS ( mask -- ): MMCCMD flags for the long
   read, any of PPLEN 0x80, DCLR 0x8000, DMATRIG 0x10000 (ours: 8080; stock: 10000). SD-CLK ( clkrt -- ): card clock
   90 MHz / (clkrt + 1), 3..255 (3 = 22.5 MHz, the default since 2026-11-14; 4 = 18 MHz). SD-INIT puts the clock back to 3. */
/* These experiment words are rarely used, so they live in the DDR window (l2.cmd): an example of .ddrtext. */
#pragma CODE_SECTION(rate, ".ddrtext")
#pragma CODE_SECTION(w_sd_rcmp, ".ddrtext")
#pragma CODE_SECTION(w_sd_flags, ".ddrtext")
#pragma CODE_SECTION(w_sd_clk, ".ddrtext")
#pragma CODE_SECTION(w_sd_wcmp, ".ddrtext")
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

/* FSEEK-TEST ( -- ): fat_map / fat_read_some / fat_read_at on a real file, A:\R8SYS.BIN (read-only). Maps it, reads it
   sequentially in 128-block steps into DDR with a CRC-32 (compare with the computer's), then reads 50 random unaligned
   pieces with fat_read_at and compares each with the sequential copy. Prints extents, rates and the slowest seek. */
#define FT_EXT ((fat_ext *)0xC1F00000u)        /* 512 extents, in the free DDR above 0xC0400000 */
#define FT_ALL ((u8 *)0xC0400000u)             /* the whole file (the Forth app leaves DDR from here free) */
#define FT_PIECE ((u8 *)0xC2000000u)
#pragma CODE_SECTION(w_fseek_test, ".ddrtext")
static void w_fseek_test(void)
{
    static fat_ent e; static fat_map_t m;
    if (fat_find("R8SYS.BIN", &e)) { puts_(" no R8SYS.BIN in the card root "); return; }
    if (e.size > 0x1800000u) { puts_(" file too big for the test buffer "); return; }
    u32 t = TSCL;
    int r = fat_map(&e, &m, FT_EXT, 512);
    puts_(" map "); forth_dot((int)((TSCL - t) / 360)); puts_("us, "); forth_dot((int)m.n); puts_("extents;");
    if (r) { puts_(" fat_map error "); forth_dot(r); return; }
    u32 off = 0, got, crc = ~0u; t = TSCL;
    while (off < e.size) {
        r = fat_read_some(&m, off, FT_ALL + off, e.size - off, 128, &got);
        if (r || !got) { puts_(" read error at "); forth_dot((int)off); return; }
        off += got; forth_poll();
    }
    u32 us = (TSCL - t) / 360;
    for (u32 i = 0; i < e.size; i++) { crc ^= FT_ALL[i]; for (int k = 0; k < 8; k++) crc = crc >> 1 ^ (0xEDB88320u & -(crc & 1)); }
    crc = ~crc;
    puts_(" sequential "); forth_dot((int)e.size); puts_("bytes in "); forth_dot((int)us); puts_("us = ");
    forth_dot(us ? (int)(e.size / us) : 0); puts_("bytes/us, crc32 "); forth_hex(crc, 8); puts_(";");
    u32 x = 12345, worst = 0, bad = 0;
    for (int k = 0; k < 50; k++) {
        x = x * 1103515245u + 12345u; u32 o = (x >> 2) % (e.size - 70000);
        x = x * 1103515245u + 12345u; u32 n = 1 + (x >> 8) % 65536;
        t = TSCL;
        r = fat_read_at(&m, o, FT_PIECE + (k & 3), n);
        u32 dt = TSCL - t; if (dt > worst) worst = dt;
        if (r) { puts_(" fat_read_at error "); forth_dot(r); return; }
        for (u32 i = 0; i < n; i++) if (FT_PIECE[(k & 3) + i] != FT_ALL[o + i]) { bad++; break; }
        forth_poll();
    }
    puts_(" 50 random reads (1..64 KB, unaligned): "); forth_dot((int)bad); puts_("differ, slowest ");
    forth_dot((int)(worst / 360)); puts_("us ");
}

/* FWRITE-TEST ( -- ): the streaming writer on the real card. Writes a new 8 MB file A:\STRMTEST.BIN (or STRMTST2.BIN
   .. STRMTST9.BIN if that exists) in 64-block chunks with fat_wwrite, retrying on FAT_AGAIN; patches its first 16 bytes; closes it step by
   step; reads it back through a map and compares. Prints retries, the longest single call, close steps and rates.
   It creates a file in the card's filesystem: delete it from the computer afterwards. */
#define WT_SIZE (8u << 20)
#define WT_SRC ((u8 *)0xC0400000u)
#define WT_BACK ((u8 *)0xC1000000u)
#define WT_EXT ((fat_ext *)0xC1F00000u)
#define WT_MEXT ((fat_ext *)0xC1F10000u)
#pragma CODE_SECTION(w_fwrite_test, ".ddrtext")
static void w_fwrite_test(void)
{
    static fat_ent root, e; static fat_wr_t w; static fat_map_t m;
    if (fat_find("", &root)) { puts_(" no FAT32 card "); return; }
    static char path[13] = "STRMTEST.BIN", n83[12] = "STRMTESTBIN";
    for (int k = 1; ; k++) {                                   /* the first free name of STRMTEST, STRMTST2 .. STRMTST9 */
        if (k > 1) { path[5] = n83[5] = 'S'; path[6] = n83[6] = 'T'; path[7] = n83[7] = (char)('0' + k); }   /* STRMTST2 */
        if (fat_find(path, &e)) break;
        if (k == 9) { puts_(" STRMTEST.BIN .. STRMTST9.BIN all exist: delete them "); return; }
    }
    puts_(" "); puts_(path);
    u32 x = TSCL | 1; for (u32 i = 0; i < WT_SIZE + 100; i++) { x = x * 1103515245u + 12345u; WT_SRC[i] = (u8)(x >> 16); }
    int r = fat_wopen(&w, &root, n83, WT_EXT, 512);
    if (r) { puts_(" fat_wopen error "); forth_dot(r); return; }
    u32 off = 0, again = 0, calls = 0, worst = 0, taken, t0 = TSCL, total = WT_SIZE + 100;
    while (off < total) {
        u32 n = total - off; if (n > 32768) n = 32768; if (n >= 512) n -= n % 512;
        u32 t = TSCL;
        r = fat_wwrite(&w, WT_SRC + off, n, &taken);
        t = TSCL - t; if (t > worst) worst = t; calls++;
        if (r == FAT_AGAIN) { again++; continue; }
        if (r) { puts_(" fat_wwrite error "); forth_dot(r); fat_wabort(&w); return; }
        off += taken;
        if ((calls & 63) == 0) forth_poll();
    }
    u32 wus = (TSCL - t0) / 360;
    const char tag[16] = { 'S', 'T', 'R', 'E', 'A', 'M', ' ', 'T', 'E', 'S', 'T', ' ', 'O', 'K', '!', '!' };
    for (int i = 0; i < 16; i++) WT_SRC[i] = (u8)tag[i];
    r = fat_wpatch(&w, 0, WT_SRC, 16);
    if (r) { puts_(" fat_wpatch error "); forth_dot(r); fat_wabort(&w); return; }
    u32 steps = 0, cworst = 0;
    do { u32 t = TSCL; r = fat_wclose(&w); t = TSCL - t; if (t > cworst) cworst = t; steps++; } while (r == FAT_AGAIN);
    if (r) { puts_(" fat_wclose error "); forth_dot(r); return; }
    puts_(" wrote "); forth_dot((int)total); puts_("bytes in "); forth_dot((int)wus); puts_("us = ");
    forth_dot(wus ? (int)(total / wus) : 0); puts_("bytes/us; "); forth_dot((int)calls); puts_("calls, ");
    forth_dot((int)again); puts_("FAT_AGAIN, longest call "); forth_dot((int)(worst / 360)); puts_("us; ");
    forth_dot((int)w.n); puts_("runs; close "); forth_dot((int)steps); puts_("steps, longest "); forth_dot((int)(cworst / 360)); puts_("us;");
    if (fat_find(path, &e) || e.size != total) { puts_(" not found after close "); return; }
    if (fat_map(&e, &m, WT_MEXT, 512) || fat_read_at(&m, 0, WT_BACK, total)) { puts_(" read back failed "); return; }
    u32 bad = 0; for (u32 i = 0; i < total; i++) if (WT_BACK[i] != WT_SRC[i]) { bad++; break; }
    puts_(bad ? " read back DIFFERS " : " read back same ");
}

static const struct core_app forth_app = { "FORTH", "R8 Forth" };   /* no hooks: the console owns everything */

int main(void)
{
    core_init(&forth_app);
    /* ad hoc words go here, e.g. forth_word("MY-TEST", w_my_test); */
    forth_word("SD-RCMP", w_sd_rcmp); forth_word("SD-FLAGS", w_sd_flags); forth_word("SD-CLK", w_sd_clk); forth_word("SD-WCMP", w_sd_wcmp);
    forth_word("FSEEK-TEST", w_fseek_test); forth_word("FWRITE-TEST", w_fwrite_test);
    core_run();
}
