/* Same as the hand-assembled loader self-test: returns 0x600DC0D0 + strlen("REVERSE"). */
#include "r8.h"
static u32 calls;          /* .far / .bss: checks zero-init and state */
static const char word[] = "REVERSE";


u32 r8_main(u32 a, u32 b)
{
    calls++;
    return 0x600DC0D0u + r8_strlen(word) + (calls - 1) * 0x100;
}
