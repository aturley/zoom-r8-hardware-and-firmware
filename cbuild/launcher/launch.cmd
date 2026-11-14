/* Flash-loaded code: copied by the cave loader from flash padding to 0xC0070000 and run there.
   One ordered group: entry first, then code, constants, initialised data (the image, <= flash padding),
   then uninitialised data (zeroed by r8_entry; not stored in flash). */
--entry_point=r8_entry
--zero_init=off
--ram_model
--diag_suppress=10063
../r8_stock.cmd
MEMORY
{
    CODEBUF : origin = 0xC0072000, length = 0x0000E000   /* after the REVERSE code; APP_STAGE (0xC0080000) above */
}
SECTIONS
{
    GROUP > CODEBUF
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
