/* R8UT utility image (util): loaded into idle DDR by RUN UTIL and called inside the running stock firmware.
   One ordered group: entry (r8rt.c r8_entry) first, then code, constants, initialised data, then uninitialised data
   (zeroed by r8_entry on its first call after a load). */
--entry_point=r8_entry
--zero_init=off
--ram_model
--diag_suppress=10063
../r8_stock.cmd
MEMORY
{
    UTIL : origin = 0xC0110000, length = 0x00130000
}
SECTIONS
{
    GROUP > UTIL
    {
        .text:r8_entry
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
        .stack
    }
}
