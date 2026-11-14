/* Standalone firmware in L2 RAM: loaded by the AIS SectionLoad, entered at 0x11800000. */
--entry_point=r8_boot
--zero_init=off
--ram_model
--diag_suppress=10063
MEMORY
{
    L2  : origin = 0x11800000, length = 0x00030000    /* stay below the boot ROM's area at the top of L2 */
}
SECTIONS
{
    GROUP > L2
    {
        .text:entry
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
}
__r8_stack_top = 0x11830000;
