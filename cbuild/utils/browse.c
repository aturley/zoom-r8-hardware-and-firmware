/* The file picker: see browse.h. */
#include "browse.h"
#define MAXD   8               /* folder levels */
#define MAXE   128             /* entries shown per folder */
#define TAGN   8               /* the label: FOLDER or a size */
#define TITLEN 48
#define REC_PAD_STR      0xC06E995Du   /* what stock records hold at +48..+60 */
#define SD_CARD_CALLBACK 0xC07ADF74u   /* SD CARD's event callback */

typedef struct {
    u32  desc[12], state[2], list[MAXE + 1], recs[MAXE][20];
    char tags[MAXE][TAGN], path[BROWSE_PATH], title[TITLEN];
    const char *names[MAXE];
    u8   ent[MAXE][F_KEEP];
    int  n;
} level;
static level lv[MAXD];
static u8 find[0x200];
static char fpath[BROWSE_PATH];
static browse_fn action;
static const char s_empty[] = "(EMPTY)", s_folder[] = "FOLDER";

char *put_s(char *d, const char *s) { while (*s) *d++ = *s++; *d = 0; return d; }
char *put_u(char *d, u32 v, int width)
{
    char t[12]; int k = 0;
    do { t[k++] = (char)('0' + v % 10); v /= 10; } while (v);
    while (k < width) t[k++] = '0';
    while (k) *d++ = t[--k];
    *d = 0; return d;
}
static char *put_tenths(char *d, u32 t, const char *unit)   /* t in tenths: 6.7, or 89 from 10 up */
{
    if (t < 100) { d = put_u(d, t / 10, 0); d = put_s(d, "."); d = put_u(d, t % 10, 0); }
    else d = put_u(d, (t + 5) / 10, 0);
    return put_s(d, unit);
}
char *put_size(char *d, u32 b)
{
    if (b < 1024) return put_s(put_u(d, b, 0), "B");
    u32 kb = b >> 10;
    if (kb < 1000) return put_s(put_u(d, (b + 512) >> 10, 0), "K");
    u32 m10 = (kb * 10 + 512) >> 10;                         /* kb < 4M, so no overflow */
    if (m10 < 10000) return put_tenths(d, m10, "M");
    return put_tenths(d, (kb * 10 + (1u << 19)) >> 20, "G");
}
const char *entry_name(const u8 *e) { return (const char *)e + (e[F_LFN] ? F_LFN : F_NAME); }

static int upper(int c) { return c >= 'a' && c <= 'z' ? c - 32 : c; }
static int icmp(const char *a, const char *b)
{
    while (*a && upper(*a) == upper(*b)) { a++; b++; }
    return upper((unsigned char)*a) - upper((unsigned char)*b);
}
static int before(const u8 *a, const u8 *b)        /* folders first, then by name */
{
    int fa = a[F_ATTR] & 0x10, fb = b[F_ATTR] & 0x10;
    if (fa != fb) return fa > fb;
    return icmp(entry_name(a), entry_name(b)) < 0;
}
static void copy(u8 *d, const u8 *s, int n) { while (n--) *d++ = *s++; }

/* The value column: the row drawer (0xC078E684) calls the record's +32 with a structure whose word 3 is the
   record's +28, from 0xC078E7C4 for the selected row and 0xC078E980 for the others (rowval.asm passes the return
   address). A selected value longer than 10 characters scrolls: the drawer starts the stock scroller (0xC07EC120 with
   the row's position) and sets the flag at B14[796] (0xC0813790); but moving the cursor does not stop it, and when
   the newly selected value is long too, the drawer sees the flag and only resumes the scroller, at the old row (the
   new row is left as a black bar; seen on the R8 2026-11-12). So: on the selected row's call, if the scroller is for
   a different item, stop it (0xC07EC184) and clear the flag, and the drawer starts it afresh for this row. */
#define SCROLL_FLAG   (*(volatile u32 *)0xC0813790u)
#define DRAW_SELECTED 0xC078E7C4u
static void (*const scroll_stop)(void) = (void (*)(void))0xC07EC184u;
static int scroll_item = -1;
extern const char *value_of(const u32 *s);         /* rowval.asm */
/* after a menu closes: whatever scrolls next belongs to the menu underneath, so forget the item */
static void scroll_reset(void) { if (SCROLL_FLAG) { scroll_stop(); SCROLL_FLAG = 0; } scroll_item = -1; }
const char *value_of_c(const u32 *s, u32 ret)
{
    if (ret == DRAW_SELECTED) {
        if (SCROLL_FLAG && (int)s[1] != scroll_item) { scroll_stop(); SCROLL_FLAG = 0; }
        scroll_item = (int)s[1];
    }
    return (const char *)s[3];
}

/* a menu descriptor in the stock SD CARD form, with records like FORMAT's; values may be 0 (no value column) */
static void make_menu(u32 *desc, u32 *state, u32 *list, u32 (*recs)[20], const char *title, int n,
                      const char *const *labels, const char *const *values, void (*handler)(void))
{
    for (int i = 0; i < n; i++) {
        u32 *r = recs[i];
        for (int k = 0; k < 20; k++) r[k] = 0;
        r[0] = (u32)labels[i]; r[4] = (u32)handler;
        r[6] = 1 | 8;                              /* bit 3: the wheel does not edit this row (else it writes *r[7]) */
        if (values && values[i]) { r[7] = (u32)values[i]; r[8] = (u32)value_of; }
        r[12] = r[13] = r[14] = r[15] = REC_PAD_STR;
        list[i] = (u32)r;
    }
    list[n] = 0;
    state[0] = state[1] = 0;
    for (int k = 0; k < 12; k++) desc[k] = 0;
    desc[0] = (u32)n; desc[1] = (u32)title; desc[5] = (u32)&state[0]; desc[6] = (u32)&state[1];
    desc[7] = 0x10013; desc[8] = (u32)list; desc[9] = SD_CARD_CALLBACK;
}

static void scan(level *L)
{
    char pat[BROWSE_PATH + 4];
    put_s(put_s(pat, L->path), "\\*");          /* not *.*: the R8 matches it DOS-style (needs a dot) */
    L->n = 0;
    int r = r8_find_first(pat, 0x17, find);
    while (r == 0) {
        const char *nm = (const char *)find + F_NAME;
        find[F_ATTR - 1] = 0;                      /* end the long name inside the entry */
        if (nm[0] && nm[0] != '.' && find[F_LFN] != '.' && !(find[F_ATTR] & 0x06) && L->n < MAXE) {
            int i = L->n++;                        /* insertion sort */
            while (i > 0 && before(find, L->ent[i - 1])) { copy(L->ent[i], L->ent[i - 1], F_KEEP); i--; }
            copy(L->ent[i], find, F_KEEP);
            L->ent[i][F_NAME + 12] = 0;            /* 8.3 at most */
        }
        r = r8_find_next(find);
    }
    r8_find_close(find);
    for (int i = 0; i < L->n; i++) {
        const u8 *e = L->ent[i];
        if (e[F_ATTR] & 0x10) put_s(L->tags[i], s_folder);
        else put_size(L->tags[i], e[F_SIZE] | e[F_SIZE + 1] << 8 | e[F_SIZE + 2] << 16 | (u32)e[F_SIZE + 3] << 24);
        L->names[i] = entry_name(e);
    }
}

static void set_title(char *t, const char *s)
{
    int k = 0; for (; s[k] && k < TITLEN - 1; k++) t[k] = s[k];
    t[k] = 0;
}
static void open_level(int d);
static void fill_level(int d);
static void pick(int d)
{
    level *L = &lv[d];
    int i = (int)L->state[0];
    if (L->n == 0 || i < 0 || i >= L->n) return;
    const u8 *e = L->ent[i];
    u32 len = r8_strlen(L->path) + 1 + r8_strlen((const char *)e + F_NAME);
    if (len >= BROWSE_PATH) { r8_popup("PATH TOO LONG"); return; }
    if (e[F_ATTR] & 0x10) {
        if (d + 1 >= MAXD) { r8_popup("TOO DEEP"); return; }
        level *N = &lv[d + 1];
        put_s(put_s(put_s(N->path, L->path), "\\"), (const char *)e + F_NAME);
        set_title(N->title, entry_name(e));
        open_level(d + 1);
    } else {
        put_s(put_s(put_s(fpath, L->path), "\\"), (const char *)e + F_NAME);
        action(fpath, e);
        fill_level(d);                             /* the action may have added files */
    }
}
#define H(n) static void h##n(void) { pick(n); }
H(0) H(1) H(2) H(3) H(4) H(5) H(6) H(7)
static void (*const handlers[MAXD])(void) = { h0, h1, h2, h3, h4, h5, h6, h7 };

/* list the folder into its menu. The first time (open) the state words are reset; on a refresh, while the engine
   has the menu open, only the records, the list and the count change (the engine reads the count each time round
   its loop; the cursor stays where it was). */
static void fill_level(int d)
{
    level *L = &lv[d];
    static const char *labels[MAXE];
    u32 st0 = L->state[0], st1 = L->state[1], refresh = L->desc[0] != 0;
    scan(L);
    int n = L->n;
    if (n == 0) { labels[0] = s_empty; make_menu(L->desc, L->state, L->list, L->recs, L->title, 1, labels, 0, handlers[d]); }
    else {
        for (int i = 0; i < n; i++) labels[i] = L->tags[i];
        make_menu(L->desc, L->state, L->list, L->recs, L->title, n, labels, L->names, handlers[d]);
    }
    if (refresh) { L->state[0] = st0; L->state[1] = st1; }
}
static void open_level(int d)
{
    lv[d].desc[0] = 0;
    fill_level(d);
    r8_menu_engine(lv[d].desc, 0, 0, 0);
    scroll_reset();
}

void browse(const char *root, const char *title, browse_fn fn)
{
    action = fn;
    put_s(lv[0].path, root);
    set_title(lv[0].title, title);
    open_level(0);
}

/* plain menus (not folders): up to 3 open at once (a menu's handler may open another) */
#define MAXM 3
static u32 mdesc[MAXM][12], mstate[MAXM][2], mlist[MAXM][MAXMENU + 1], mrecs[MAXM][MAXMENU][20];
static menu_fn mfn[MAXM];
static int mdepth;
static void mpick(int m) { int i = (int)mstate[m][0]; if (mfn[m]) mfn[m](i); }
static void m0(void) { mpick(0); }
static void m1(void) { mpick(1); }
static void m2(void) { mpick(2); }
static void (*const mhandlers[MAXM])(void) = { m0, m1, m2 };
void show_menu(const char *title, const char *const *labels, const char *const *values, int n, menu_fn fn)
{
    if (mdepth >= MAXM) { r8_popup("TOO DEEP"); return; }
    if (n > MAXMENU) n = MAXMENU;
    int m = mdepth++;
    mfn[m] = fn;
    make_menu(mdesc[m], mstate[m], mlist[m], mrecs[m], title, n, labels, values, mhandlers[m]);
    r8_menu_engine(mdesc[m], 0, 0, 0);
    scroll_reset();
    mdepth--;
}
void show_items(const char *title, const menu_item *it, int n, menu_fn fn, int start)
{
    if (mdepth >= MAXM) { r8_popup("TOO DEEP"); return; }
    if (n > MAXMENU) n = MAXMENU;
    int m = mdepth++;
    mfn[m] = fn;
    static const char *labels[MAXMENU];
    for (int i = 0; i < n; i++) labels[i] = it[i].label;
    make_menu(mdesc[m], mstate[m], mlist[m], mrecs[m], title, n, labels, 0, mhandlers[m]);
    for (int i = 0; i < n; i++) {
        u32 *r = mrecs[m][i];
        r[8] = (u32)it[i].value;
        if (it[i].edit) {
            r[6] = 1; r[7] = (u32)it[i].edit; r[9] = (u32)it[i].min; r[10] = (u32)it[i].max; r[11] = (u32)it[i].edited;
        } else r[7] = (u32)it[i].user;
    }
    mstate[m][0] = (u32)(start >= 0 && start < n ? start : 0);     /* the engine starts its cursor here (0xC078EE40) */
    r8_menu_engine(mdesc[m], 0, 0, 0);
    scroll_reset();
    mdepth--;
}
void show_info(const char *title, const char *const *labels, const char *const *values, int n)
{
    show_menu(title, labels, values, n, 0);
}
