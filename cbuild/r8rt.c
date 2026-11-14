/* Minimal runtime for flash-loaded code. The cave loader copies the image (code + initialised data)
   to CODEBUF and calls r8_entry with the menu handler's arguments; we zero the uninitialised data
   once, then call r8_main. Runs on the caller's stack; no TI runtime library is linked. */
#include "r8.h"
extern u32 r8_main(u32 a, u32 b);
extern u8 __r8_bss_start[], __r8_bss_end[];

#pragma DATA_SECTION(r8_need_init, ".data")
static volatile u32 r8_need_init = 1;          /* lives in the image, so it is 1 after every load */

#pragma CODE_SECTION(r8_entry, ".text:r8_entry")
u32 r8_entry(u32 a, u32 b)
{
    if (r8_need_init) {
        volatile u8 *p = __r8_bss_start;
        while (p < __r8_bss_end) *p++ = 0;
        r8_need_init = 0;
    }
    return r8_main(a, b);
}
