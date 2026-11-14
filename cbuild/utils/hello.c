/* HELLO.BIN: the smallest utility. Shows a popup and returns to the menu. */
#include "r8.h"
u32 r8_main(u32 path, u32 b)
{
    (void)path; (void)b;
    r8_popup("HELLO from R8UTILS");
    return 0;
}
