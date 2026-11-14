/* A file picker for utilities, built from stock menus (see utilmenu.c for the descriptor and record layout).

   Stock menu rows are two columns split by a line at x = 60 (drawn by 0xC078EB0C): a label on the left (about 8
   characters; longer ones run over the line) and a value on the right from x = 62, which the row drawer
   (0xC078E684) gets from the record: if +32 (a function) is set it is called with a small structure whose word 3 is
   the record's +28, and returns the string. Values are cut to 11 characters, except on the selected row, which
   scrolls when longer than 10.

   browse(root, title, fn) shows a folder as such a menu: one row per entry, the label is FOLDER or the size, the
   value is the name (the long name when there is one). Folders come first, then files, each sorted by name ignoring
   case; names starting with '.' and hidden or system entries (the Mac's .Trashes appears as TRASH~16) are left out.
   The folder is listed with pattern *: the R8 matches *.* DOS-style, which needs a dot in the name and so drops
   folders like ZOOM_R8 (LISTDIAG 2026-11-12). Picking a folder opens it as a new menu, EXIT goes back up a level,
   EXIT at the top returns from browse(). Picking a file calls fn(path, entry) and stays in the folder when it
   returns. Paths are built from the 8.3 names. The menu engine stores the chosen item's index in the descriptor's
   first state word (+20) before calling the item's handler (0xC078F3F8), so one handler per level is enough. */
#ifndef BROWSE_H
#define BROWSE_H
#include "r8.h"
/* the folder entry as find_first/find_next fill it (confirmed on the R8, UTILDIAG/LISTDIAG 2026-11-12) */
#define F_NAME   0x12          /* NUL-terminated 8.3 name */
#define F_LFN    0x1F          /* long name, NUL-terminated; first byte 0 when there is none (later bytes are stale) */
#define F_ATTR   0x11F         /* 0x01 read-only, 0x02 hidden, 0x04 system, 0x10 folder, 0x20 archive */
#define F_SIZE   0x120         /* u32 */
#define F_CDATE  0x124         /* created: day, month, year (u16), 2 bytes, hour, minute, second, 1/100 s */
#define F_MDATE  0x138         /* modified: day, month, year (u16), 2 bytes, hour, minute, second */
#define F_KEEP   0x150         /* bytes of the entry kept per item */
#define BROWSE_PATH 160
typedef void (*browse_fn)(const char *path, const u8 *entry);
void browse(const char *root, const char *title, browse_fn fn);
/* the name to show for an entry: its long name, or the 8.3 name */
const char *entry_name(const u8 *entry);
/* a menu of label/value rows (values may be 0); picking row i calls fn(i) (fn may be 0), and the menu stays open
   until EXIT. Up to MAXMENU rows; up to 3 such menus open at once. */
#define MAXMENU 24
typedef void (*menu_fn)(int index);
void show_menu(const char *title, const char *const *labels, const char *const *values, int n, menu_fn fn);
/* A menu of rows built from items, opened with the cursor on row start. A row's value is either fixed (value(s)
   with s[3] = user) or a number the wheel changes: the stock engine adds the wheel's movement to *edit, keeps it
   within min..max, writes it back only if it changed, then calls edited(s) (s[3] = edit, s[4] = the movement) and
   redraws (0xC078EFE4: wheel event 0x10000000). Rows without edit are marked read-only for the wheel (record +24
   bit 3). Picking a row (ENTER) calls fn(row). */
typedef struct {
    const char *label;
    const char *(*value)(const u32 *s);
    const void *user;
    int *edit;
    int min, max;
    void (*edited)(const u32 *s);
} menu_item;
void show_items(const char *title, const menu_item *items, int n, menu_fn fn, int start);
/* show_menu with no actions, e.g. to show information */
void show_info(const char *title, const char *const *labels, const char *const *values, int n);
/* small string helpers */
char *put_s(char *d, const char *s);
char *put_u(char *d, u32 v, int width);           /* decimal, zero-padded to width (0 = none) */
char *put_size(char *d, u32 bytes);               /* 123B, 45K, 6.7M, 890M, 1.2G */
#endif
