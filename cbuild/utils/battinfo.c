/* BATTINFO.BIN: shows stock's battery state, live, as label/value rows (the menu engine redraws them):
   SETTING  the BATTERY setting word at 0xC05B60C8 (the row of the threshold table stock uses; whether 0 is
            Alkaline and 1 Ni-MH is what this utility is for: change the setting in stock's menu and run it again)
   C1       the supply value from the panel (B14[612])
   VOLTS    stock's conversion, mV = C1 * 19800 / 1024 + 457
   POWER    stock's debounced USB-power flag (B14[593]): USB or BATTERY
   GP6[1]   the pin itself (high = USB power)
   LEVEL    stock's battery level 0-4 (B14[590]; only updated while on batteries)
   All addresses are stock 1.17. B14 (stock's data pointer) = 0xC0812B20 (B14[796] = 0xC0813790, confirmed).
   Read-only: nothing is written. EXIT returns to the utility menu. */
#include "browse.h"

#define B14 0xC0812B20u
#define W(a) (*(volatile u32 *)(a))
#define BATT_SETTING 0xC05B60C8u
#define C1_RAW   (B14 + 4 * 612)
#define USB_FLAG (B14 + 4 * 593)
#define LEVEL    (B14 + 4 * 590)
#define GPIO67_IN 0x01E26098u                  /* GPIO bank 6/7 IN_DATA; bank 6 = bits 0-15 */

static char b_set[16], b_c1[16], b_v[16], b_pow[16], b_pin[16], b_lev[16];
static const char *set_text(const u32 *s) { (void)s; char *e = put_u(b_set, W(BATT_SETTING), 0); *e = 0; return b_set; }
static const char *c1_text(const u32 *s) { (void)s; char *e = put_u(b_c1, W(C1_RAW), 0); *e = 0; return b_c1; }
static const char *v_text(const u32 *s)
{
    (void)s;
    u32 cv = (W(C1_RAW) * 19800u / 1024u + 457u + 5u) / 10u;     /* stock's mV, rounded to 10 mV */
    char *e = put_u(b_v, cv / 100, 0); *e++ = '.'; e = put_u(e, cv % 100, 2); *e++ = 'V'; *e = 0;
    return b_v;
}
static const char *pow_text(const u32 *s) { (void)s; char *e = put_s(b_pow, W(USB_FLAG) ? "USB" : "BATTERY"); *e = 0; return b_pow; }
static const char *pin_text(const u32 *s) { (void)s; char *e = put_s(b_pin, W(GPIO67_IN) & 2 ? "1 (USB)" : "0"); *e = 0; return b_pin; }
static const char *lev_text(const u32 *s) { (void)s; char *e = put_u(b_lev, W(LEVEL), 0); *e = 0; return b_lev; }

static const menu_item rows[] = {
    { "SETTING", set_text, 0, 0, 0, 0, 0 },
    { "C1",      c1_text,  0, 0, 0, 0, 0 },
    { "VOLTS",   v_text,   0, 0, 0, 0, 0 },
    { "POWER",   pow_text, 0, 0, 0, 0, 0 },
    { "GP6[1]",  pin_text, 0, 0, 0, 0, 0 },
    { "LEVEL",   lev_text, 0, 0, 0, 0, 0 },
};

u32 r8_main(u32 path, u32 b)
{
    (void)path; (void)b;
    show_items("BATTERY", rows, 6, 0, 0);
    return 0;
}
