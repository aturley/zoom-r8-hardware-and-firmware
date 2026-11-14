/* KEYLOG.BIN: what the UI event reader (r8_ui_event = 0xC07B5B58) returns, to find the codes of keys (STOP etc.)
   and what a timeout returns. Polls with a 20-tick timeout, up to 750 times or 400 results, and saves each result
   as (poll number, value) to A:\KEYLOG.DAT. Press keys while "Please Wait..." is up (about 15 s if ticks are 1 ms). */
#include "r8.h"
static u32 log[400][2];
u32 r8_main(u32 path, u32 b)
{
    (void)path; (void)b;
    r8_pre_msg();
    r8_show_msg(R8_STR_PLEASE_WAIT);
    int n = 0;
    for (u32 i = 0; i < 750 && n < 400; i++) {
        int ev = r8_ui_event(20);
        log[n][0] = i; log[n][1] = (u32)ev; n++;
    }
    r8_dialog_close(1);
    int fd; u32 w;
    if (!r8_open(&fd, "A:\\KEYLOG.DAT", 0x101, 0x80)) { r8_write(fd, log, (u32)n * 8, &w); r8_close(fd); }
    r8_popup("KEYLOG SAVED");
    return 0;
}
