/* The Forth core's apps (FORTH, SAMPLER and forks): L2 RAM for the entry, interrupt path and everything not marked
   otherwise; the DDR window 0xC0000000-0xC00FFFFF for sections an app places there by name:
     #pragma CODE_SECTION(fn, ".ddrtext")   code that isn't time-critical
     #pragma DATA_SECTION(x, ".ddrdata")    initialised data (loaded from the file)
     #pragma DATA_SECTION(x, ".ddrbss")     uninitialised data (zeroed by core_init, like .bss)
   DDR is cached (L1D 16 KB, L1P 32 KB), so DDR code runs at near L2 speed once warm, but a cold miss costs DDR
   latency: keep interrupt handlers and per-sample loops in L2. A file with DDR sections is an app file version 2
   (tools/mkapp.py), loaded by reload.c (the chooser and RELOAD), never by the flash launcher. */
--entry_point=r8_boot
--zero_init=off
--ram_model
--diag_suppress=10063
MEMORY
{
    L2  : origin = 0x11800000, length = 0x00030000    /* stay below the boot ROM's area at the top of L2 */
    DDR : origin = 0xC0000000, length = 0x00100000    /* the app DDR window (see writeup section 7, memory map) */
}
SECTIONS
{
    GROUP > L2
    {
        .text:entry
        .vecs   ALIGN(1024)
        .text
        .const
        .switch
        .rodata
        .fardata
        .neardata
        .data
        .cinit
        GROUP : RUN_START(__r8_bss_start), RUN_END(__r8_bss_end)
        {
            .far
            .bss
        }
    }
    .stack : {} > L2
    GROUP > DDR
    {
        .ddrtext
        .ddrdata
        GROUP : RUN_START(__r8_ddrbss_start), RUN_END(__r8_ddrbss_end)
        {
            .ddrbss
        }
    }
}
__r8_stack_top = 0x11830000;
