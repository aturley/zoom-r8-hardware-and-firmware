/* Small Forth for probing the R8. Cells are 32 bits; colon definitions are arrays of word indices.
   Words (case-insensitive):
   stack   DUP DROP SWAP OVER ROT -ROT NIP TUCK ?DUP 2DUP 2DROP DEPTH PICK >R R> R@
   math    + - * / MOD /MOD NEGATE ABS MIN MAX 1+ 1- 2* 2/ AND OR XOR INVERT LSHIFT RSHIFT
           = <> < > U< U> 0= 0< 0> 0<> TRUE FALSE
   memory  @ ! C@ C! W@ W! +! HERE ALLOT , C, CELLS CELL+ FILL MOVE DUMP
   output  . U. .X .S EMIT CR SPACE SPACES TYPE ." .( PAGE
   input   KEY KEY?
   number  HEX DECIMAL BASE   (literals: 123 -5 $1F #10 %101 0x1F)
   define  : ; VARIABLE CONSTANT CREATE IMMEDIATE ' EXECUTE FORGET WORDS
   control IF ELSE THEN BEGIN UNTIL AGAIN WHILE REPEAT DO LOOP +LOOP I J UNLOOP EXIT RECURSE ( \
   system  MS TICKS (360 MHz counter) TICKS/MS */
#include "forth.h"
typedef int cell;
typedef unsigned int ucell;

enum { K_PRIM, K_COLON, K_VAR, K_CONST, K_EXT };
#define NEXT 64                       /* words added with forth_word(); more are refused with a message */
static void (*ext_fn[NEXT])(void);
static int n_ext;
#define NW 512
struct word { char name[16]; unsigned char imm, kind, hidden, pad; cell p; };
#pragma DATA_SECTION(dict, ".ddrbss")       /* the three big buffers live in DDR (l2.cmd): 60 KB of L2 freed */
static struct word dict[NW];
static int nwords;
#define NCODE 8192                     /* 32 KB of threaded code */
#pragma DATA_SECTION(code, ".ddrbss")
static cell code[NCODE];
static int here_c;
#define NDATA 16384
#pragma DATA_SECTION(dspace, ".ddrbss")
static unsigned char dspace[NDATA] __attribute__((aligned(8)));
static ucell dhere;
static cell ds[256], rs[256];
static int sp, rp;
static cell ip;
static int state, err;
static cell base = 10;
volatile int forth_abort;
static const char *in; static int inlen, inpos;
static int def_start_w, def_start_c, latest;
static char errword[17];

enum { E_OK, E_UNDEF, E_UNDER, E_OVER, E_ABORT, E_COMPILE, E_FULL, E_NAME };
static const char *const errmsg[] = { "", "?", "stack empty", "stack full", "abort", "compile only", "dictionary full", "name?" };

/* ---- output helpers */
static void emit(int c) { forth_emit(c); }
static void type(const char *s) { while (*s) emit(*s++); }
static void cr(void) { emit('\r'); emit('\n'); }
static void put_num(ucell u, int neg, ucell b, int width)
{
    char t[34]; int n = 0;
    do { ucell d = u % b; t[n++] = (char)(d < 10 ? '0' + d : 'A' + d - 10); u /= b; } while (u);
    while (n < width) t[n++] = '0';
    if (neg) emit('-');
    while (n) emit(t[--n]);
}
static void dot(cell v) { if (base == 10 && v < 0) put_num((ucell)-v, 1, 10, 0); else put_num((ucell)v, 0, (ucell)base, 0); emit(' '); }

/* ---- stacks */
static void push(cell v) { if (sp >= 256) { err = E_OVER; return; } ds[sp++] = v; }
static cell pop(void) { if (sp <= 0) { err = E_UNDER; return 0; } return ds[--sp]; }
static void rpush(cell v) { if (rp >= 256) { err = E_OVER; return; } rs[rp++] = v; }
static cell rpop(void) { if (rp <= 0) { err = E_UNDER; return -1; } return rs[--rp]; }
static int need(int n) { if (sp < n) { err = E_UNDER; return 0; } return 1; }

/* ---- dictionary */
static int upc(int c) { return c >= 'a' && c <= 'z' ? c - 32 : c; }
static int name_eq(const char *a, const char *b, int n)
{
    for (int i = 0; i < n; i++) if (upc(a[i]) != upc(b[i]) || !b[i]) return 0;
    return b[n] == 0;
}
static int find(const char *s, int n)
{
    for (int i = nwords - 1; i >= 0; i--) if (!dict[i].hidden && name_eq(s, dict[i].name, n)) return i;
    return -1;
}
static int add_word(const char *s, int n, int kind, cell p)
{
    if (nwords >= NW) { err = E_FULL; return -1; }
    if (n < 1 || n > 15) { err = E_NAME; return -1; }
    struct word *w = &dict[nwords];
    for (int i = 0; i < n; i++) w->name[i] = (char)upc(s[i]);
    w->name[n] = 0; w->kind = (unsigned char)kind; w->p = p; w->imm = 0; w->hidden = 0;
    return nwords++;
}
static void comma_c(cell v) { if (here_c >= NCODE) { err = E_FULL; return; } code[here_c++] = v; }

/* ---- parsing */
static int parse_word(const char **s)
{
    while (inpos < inlen && (unsigned char)in[inpos] <= ' ') inpos++;
    *s = in + inpos;
    int st = inpos;
    while (inpos < inlen && (unsigned char)in[inpos] > ' ') inpos++;
    return inpos - st;
}
static int parse_until(char d, const char **s)
{
    if (inpos < inlen && in[inpos] == ' ') inpos++;
    *s = in + inpos; int st = inpos;
    while (inpos < inlen && in[inpos] != d) inpos++;
    int n = inpos - st;
    if (inpos < inlen) inpos++;
    return n;
}
static int number(const char *s, int n, cell *out)
{
    ucell b = (ucell)base, v = 0; int neg = 0, i = 0;
    if (i < n && s[i] == '-') { neg = 1; i++; }
    if (i < n && s[i] == '$') { b = 16; i++; }
    else if (i < n && s[i] == '#') { b = 10; i++; }
    else if (i < n && s[i] == '%') { b = 2; i++; }
    else if (i + 1 < n && s[i] == '0' && (s[i + 1] == 'x' || s[i + 1] == 'X')) { b = 16; i += 2; }
    if (i >= n) return 0;
    for (; i < n; i++) {
        int c = upc(s[i]); ucell d;
        if (c >= '0' && c <= '9') d = (ucell)(c - '0'); else if (c >= 'A' && c <= 'Z') d = (ucell)(c - 'A' + 10); else return 0;
        if (d >= b) return 0;
        v = v * b + d;
    }
    *out = neg ? -(cell)v : (cell)v;
    return 1;
}

/* ---- primitives: the order here is the order of the names in PRIMS[] */
enum {
    P_LIT, P_BRANCH, P_0BRANCH, P_EXIT, P_DO, P_LOOP, P_PLOOP, P_DOTQ,
    P_DUP, P_DROP, P_SWAP, P_OVER, P_ROT, P_MROT, P_NIP, P_TUCK, P_QDUP, P_2DUP, P_2DROP, P_DEPTH, P_PICK,
    P_TOR, P_RFROM, P_RAT,
    P_ADD, P_SUB, P_MUL, P_DIV, P_MOD, P_DIVMOD, P_NEG, P_ABS, P_MIN, P_MAX, P_INC, P_DEC, P_2MUL, P_2DIV,
    P_AND, P_OR, P_XOR, P_INV, P_LSH, P_RSH, P_EQ, P_NE, P_LT, P_GT, P_ULT, P_UGT, P_0EQ, P_0LT, P_0GT, P_0NE, P_TRUE, P_FALSE,
    P_FETCH, P_STORE, P_CFETCH, P_CSTORE, P_WFETCH, P_WSTORE, P_PSTORE, P_HERE, P_ALLOT, P_COMMA, P_CCOMMA,
    P_CELLS, P_CELLP, P_FILL, P_MOVE, P_DUMP,
    P_DOT, P_UDOT, P_DOTX, P_DOTS, P_EMIT, P_CR, P_SPACE, P_SPACES, P_TYPE, P_PAGE, P_KEY, P_KEYQ,
    P_HEX, P_DECIMAL, P_BASE, P_COLON, P_SEMI, P_VARIABLE, P_CONSTANT, P_CREATE, P_IMMEDIATE, P_TICK, P_EXECUTE,
    P_FORGET, P_WORDS, P_IF, P_ELSE, P_THEN, P_BEGIN, P_UNTIL, P_AGAIN, P_WHILE, P_REPEAT, P_DOI, P_LOOPI, P_PLOOPI,
    P_I, P_J, P_UNLOOP, P_RECURSE, P_PAREN, P_BSLASH, P_DOTQI, P_DOTP, P_MS, P_TICKS, P_TPMS, NPRIM
};
static const char *const PRIMS[NPRIM] = {
    "(LIT)", "(BRANCH)", "(0BRANCH)", "EXIT", "(DO)", "(LOOP)", "(+LOOP)", "(.\")",
    "DUP", "DROP", "SWAP", "OVER", "ROT", "-ROT", "NIP", "TUCK", "?DUP", "2DUP", "2DROP", "DEPTH", "PICK",
    ">R", "R>", "R@",
    "+", "-", "*", "/", "MOD", "/MOD", "NEGATE", "ABS", "MIN", "MAX", "1+", "1-", "2*", "2/",
    "AND", "OR", "XOR", "INVERT", "LSHIFT", "RSHIFT", "=", "<>", "<", ">", "U<", "U>", "0=", "0<", "0>", "0<>", "TRUE", "FALSE",
    "@", "!", "C@", "C!", "W@", "W!", "+!", "HERE", "ALLOT", ",", "C,",
    "CELLS", "CELL+", "FILL", "MOVE", "DUMP",
    ".", "U.", ".X", ".S", "EMIT", "CR", "SPACE", "SPACES", "TYPE", "PAGE", "KEY", "KEY?",
    "HEX", "DECIMAL", "BASE", ":", ";", "VARIABLE", "CONSTANT", "CREATE", "IMMEDIATE", "'", "EXECUTE",
    "FORGET", "WORDS", "IF", "ELSE", "THEN", "BEGIN", "UNTIL", "AGAIN", "WHILE", "REPEAT", "DO", "LOOP", "+LOOP",
    "I", "J", "UNLOOP", "RECURSE", "(", "\\", ".\"", ".(", "MS", "TICKS", "TICKS/MS",
};
static const unsigned char IMMEDIATES[] = { P_SEMI, P_IF, P_ELSE, P_THEN, P_BEGIN, P_UNTIL, P_AGAIN, P_WHILE, P_REPEAT,
    P_DOI, P_LOOPI, P_PLOOPI, P_RECURSE, P_PAREN, P_BSLASH, P_DOTQI, P_DOTP };
static const unsigned char COMPILE_ONLY[] = { P_SEMI, P_IF, P_ELSE, P_THEN, P_BEGIN, P_UNTIL, P_AGAIN, P_WHILE, P_REPEAT,
    P_DOI, P_LOOPI, P_PLOOPI, P_RECURSE, P_DOTQI };

extern cregister volatile unsigned int TSCL;
static int peeked = -1;             /* KEY? looks ahead one key */
static int key_wait(void)
{
    if (peeked >= 0) { int c = peeked; peeked = -1; return c; }
    for (;;) { int c = forth_key(); if (c >= 0) return c; forth_poll(); if (forth_abort) { err = E_ABORT; return 0; } }
}
static void dump(ucell a, cell n)
{
    for (cell o = 0; o < n && !err; o += 16) {
        put_num(a + (ucell)o, 0, 16, 8); type(": ");
        for (int i = 0; i < 16; i++) { if (o + i < n) { put_num(*(volatile unsigned char *)(a + o + i), 0, 16, 2); emit(' '); } else type("   "); }
        for (int i = 0; i < 16 && o + i < n; i++) { int c = *(volatile unsigned char *)(a + o + i); emit(c >= 32 && c < 127 ? c : '.'); }
        cr(); forth_poll(); if (forth_abort) err = E_ABORT;
    }
}
static void words(void)
{
    int col = 0;
    for (int i = nwords - 1; i >= 0; i--) {
        if (dict[i].hidden || dict[i].name[0] == '(') continue;
        int n = 0; while (dict[i].name[n]) n++;
        if (col + n + 1 > 64) { cr(); col = 0; }
        type(dict[i].name); emit(' '); col += n + 1;
    }
    cr();
}
static void execute(int w);

static void prim(int op)
{
    cell a, b, c;
    switch (op) {
    case P_LIT: push(code[ip++]); break;
    case P_BRANCH: ip = code[ip]; break;
    case P_0BRANCH: if (pop() == 0) ip = code[ip]; else ip++; break;
    case P_EXIT: ip = rpop(); break;
    case P_DO: b = pop(); a = pop(); rpush(a); rpush(b); break;                  /* ( limit index -- ) */
    case P_LOOP: case P_PLOOP: {                                /* ends when the index crosses limit-1 -> limit */
        cell inc = op == P_LOOP ? 1 : pop();
        if (rp < 2) { err = E_UNDER; break; }
        cell lim = rs[rp - 2], i0 = rs[rp - 1], i1 = i0 + inc;
        if (((i0 - lim) ^ (i1 - lim)) < 0) { rp -= 2; ip++; } else { rs[rp - 1] = i1; ip = code[ip]; }
        break;
    }
    case P_DOTQ: { cell n = code[ip++]; for (cell i = 0; i < n; i++) emit(code[ip++]); break; }
    case P_DUP: if (need(1)) push(ds[sp - 1]); break;
    case P_DROP: pop(); break;
    case P_SWAP: if (need(2)) { a = ds[sp - 1]; ds[sp - 1] = ds[sp - 2]; ds[sp - 2] = a; } break;
    case P_OVER: if (need(2)) push(ds[sp - 2]); break;
    case P_ROT: if (need(3)) { a = ds[sp - 3]; ds[sp - 3] = ds[sp - 2]; ds[sp - 2] = ds[sp - 1]; ds[sp - 1] = a; } break;
    case P_MROT: if (need(3)) { a = ds[sp - 1]; ds[sp - 1] = ds[sp - 2]; ds[sp - 2] = ds[sp - 3]; ds[sp - 3] = a; } break;
    case P_NIP: if (need(2)) { ds[sp - 2] = ds[sp - 1]; sp--; } break;
    case P_TUCK: if (need(2)) { a = ds[sp - 1]; ds[sp - 1] = ds[sp - 2]; ds[sp - 2] = a; push(a); } break;
    case P_QDUP: if (need(1) && ds[sp - 1]) push(ds[sp - 1]); break;
    case P_2DUP: if (need(2)) { push(ds[sp - 2]); push(ds[sp - 2]); } break;
    case P_2DROP: pop(); pop(); break;
    case P_DEPTH: push(sp); break;
    case P_PICK: a = pop(); if (!err) { if (a < 0 || a >= sp) err = E_UNDER; else push(ds[sp - 1 - a]); } break;
    case P_TOR: rpush(pop()); break;
    case P_RFROM: push(rpop()); break;
    case P_RAT: if (rp < 1) err = E_UNDER; else push(rs[rp - 1]); break;
    case P_ADD: b = pop(); a = pop(); push(a + b); break;
    case P_SUB: b = pop(); a = pop(); push(a - b); break;
    case P_MUL: b = pop(); a = pop(); push(a * b); break;
    case P_DIV: case P_MOD: case P_DIVMOD:
        b = pop(); a = pop();
        if (err) break;
        if (b == 0) { err = E_ABORT; type("divide by 0 "); break; }
        if (op == P_DIV) push(a / b); else if (op == P_MOD) push(a % b); else { push(a % b); push(a / b); }
        break;
    case P_NEG: push(-pop()); break;
    case P_ABS: a = pop(); push(a < 0 ? -a : a); break;
    case P_MIN: b = pop(); a = pop(); push(a < b ? a : b); break;
    case P_MAX: b = pop(); a = pop(); push(a > b ? a : b); break;
    case P_INC: push(pop() + 1); break;
    case P_DEC: push(pop() - 1); break;
    case P_2MUL: push(pop() << 1); break;
    case P_2DIV: push(pop() >> 1); break;
    case P_AND: b = pop(); a = pop(); push(a & b); break;
    case P_OR: b = pop(); a = pop(); push(a | b); break;
    case P_XOR: b = pop(); a = pop(); push(a ^ b); break;
    case P_INV: push(~pop()); break;
    case P_LSH: b = pop(); a = pop(); push(b >= 32 ? 0 : (cell)((ucell)a << b)); break;
    case P_RSH: b = pop(); a = pop(); push(b >= 32 ? 0 : (cell)((ucell)a >> b)); break;
    case P_EQ: b = pop(); a = pop(); push(a == b ? -1 : 0); break;
    case P_NE: b = pop(); a = pop(); push(a != b ? -1 : 0); break;
    case P_LT: b = pop(); a = pop(); push(a < b ? -1 : 0); break;
    case P_GT: b = pop(); a = pop(); push(a > b ? -1 : 0); break;
    case P_ULT: b = pop(); a = pop(); push((ucell)a < (ucell)b ? -1 : 0); break;
    case P_UGT: b = pop(); a = pop(); push((ucell)a > (ucell)b ? -1 : 0); break;
    case P_0EQ: push(pop() == 0 ? -1 : 0); break;
    case P_0LT: push(pop() < 0 ? -1 : 0); break;
    case P_0GT: push(pop() > 0 ? -1 : 0); break;
    case P_0NE: push(pop() != 0 ? -1 : 0); break;
    case P_TRUE: push(-1); break;
    case P_FALSE: push(0); break;
    case P_FETCH: a = pop(); if (!err) push(*(volatile cell *)(a & ~3)); break;
    case P_STORE: a = pop(); b = pop(); if (!err) *(volatile cell *)(a & ~3) = b; break;
    case P_CFETCH: a = pop(); if (!err) push(*(volatile unsigned char *)a); break;
    case P_CSTORE: a = pop(); b = pop(); if (!err) *(volatile unsigned char *)a = (unsigned char)b; break;
    case P_WFETCH: a = pop(); if (!err) push(*(volatile unsigned short *)(a & ~1)); break;
    case P_WSTORE: a = pop(); b = pop(); if (!err) *(volatile unsigned short *)(a & ~1) = (unsigned short)b; break;
    case P_PSTORE: a = pop(); b = pop(); if (!err) *(volatile cell *)(a & ~3) += b; break;
    case P_HERE: push((cell)(dspace + dhere)); break;
    case P_ALLOT: a = pop(); if (!err) { if ((cell)dhere + a < 0 || dhere + (ucell)a > NDATA) err = E_FULL; else dhere += (ucell)a; } break;
    case P_COMMA: a = pop(); if (!err) { dhere = (dhere + 3) & ~3u; if (dhere + 4 > NDATA) err = E_FULL; else { *(cell *)(dspace + dhere) = a; dhere += 4; } } break;
    case P_CCOMMA: a = pop(); if (!err) { if (dhere >= NDATA) err = E_FULL; else dspace[dhere++] = (unsigned char)a; } break;
    case P_CELLS: push(pop() * 4); break;
    case P_CELLP: push(pop() + 4); break;
    case P_FILL: c = pop(); b = pop(); a = pop(); if (!err) for (cell i = 0; i < b; i++) ((volatile unsigned char *)a)[i] = (unsigned char)c; break;
    case P_MOVE: c = pop(); b = pop(); a = pop(); if (!err) {
        volatile unsigned char *s = (volatile unsigned char *)a, *d = (volatile unsigned char *)b;
        if (d < s) for (cell i = 0; i < c; i++) d[i] = s[i]; else for (cell i = c - 1; i >= 0; i--) d[i] = s[i];
    } break;
    case P_DUMP: b = pop(); a = pop(); if (!err) dump((ucell)a, b); break;
    case P_DOT: a = pop(); if (!err) dot(a); break;
    case P_UDOT: a = pop(); if (!err) { put_num((ucell)a, 0, (ucell)base, 0); emit(' '); } break;
    case P_DOTX: a = pop(); if (!err) { put_num((ucell)a, 0, 16, 8); emit(' '); } break;
    case P_DOTS: emit('<'); put_num((ucell)sp, 0, 10, 0); type("> "); for (int i = 0; i < sp; i++) dot(ds[i]); break;
    case P_EMIT: a = pop(); if (!err) emit(a); break;
    case P_CR: cr(); break;
    case P_SPACE: emit(' '); break;
    case P_SPACES: a = pop(); while (a-- > 0) emit(' '); break;
    case P_TYPE: b = pop(); a = pop(); if (!err) for (cell i = 0; i < b; i++) emit(((volatile unsigned char *)a)[i]); break;
    case P_PAGE: forth_page(); break;
    case P_KEY: a = key_wait(); if (!err) push(a); break;
    case P_KEYQ: if (peeked < 0) peeked = forth_key(); push(peeked >= 0 ? -1 : 0); break;
    case P_HEX: base = 16; break;
    case P_DECIMAL: base = 10; break;
    case P_BASE: push((cell)&base); break;
    case P_COLON: {
        const char *s; int n = parse_word(&s);
        def_start_w = nwords; def_start_c = here_c;
        latest = add_word(s, n, K_COLON, here_c);
        if (latest >= 0) { dict[latest].hidden = 1; state = 1; }
        break;
    }
    case P_SEMI: comma_c(P_EXIT); if (latest >= 0) dict[latest].hidden = 0; state = 0; break;
    case P_VARIABLE: case P_CREATE: {
        const char *s; int n = parse_word(&s);
        dhere = (dhere + 3) & ~3u;
        if (dhere + 4 > NDATA) { err = E_FULL; break; }
        latest = add_word(s, n, K_VAR, (cell)(dspace + dhere));
        if (latest >= 0 && op == P_VARIABLE) { *(cell *)(dspace + dhere) = 0; dhere += 4; }
        break;
    }
    case P_CONSTANT: { a = pop(); const char *s; int n = parse_word(&s); if (!err) latest = add_word(s, n, K_CONST, a); break; }
    case P_IMMEDIATE: if (latest >= 0) dict[latest].imm = 1; break;
    case P_TICK: { const char *s; int n = parse_word(&s); int w = find(s, n);
        if (w < 0) { err = E_UNDEF; for (int i = 0; i < n && i < 16; i++) errword[i] = s[i]; errword[n < 16 ? n : 16] = 0; } else push(w); break; }
    case P_EXECUTE: a = pop(); if (!err) { if (a < 0 || a >= nwords) err = E_UNDEF; else execute(a); } break;
    case P_FORGET: { const char *s; int n = parse_word(&s); int w = find(s, n);
        if (w < NPRIM) { err = E_NAME; break; }
        if (dict[w].kind == K_COLON) here_c = dict[w].p;
        nwords = w; latest = -1; break; }
    case P_WORDS: words(); break;
    case P_IF: comma_c(P_0BRANCH); push(here_c); comma_c(0); break;
    case P_ELSE: comma_c(P_BRANCH); a = pop(); push(here_c); comma_c(0); if (!err) code[a] = here_c; break;
    case P_THEN: a = pop(); if (!err) code[a] = here_c; break;
    case P_BEGIN: push(here_c); break;
    case P_UNTIL: comma_c(P_0BRANCH); comma_c(pop()); break;
    case P_AGAIN: comma_c(P_BRANCH); comma_c(pop()); break;
    case P_WHILE: comma_c(P_0BRANCH); a = pop(); push(here_c); push(a); comma_c(0); break;
    case P_REPEAT: a = pop(); b = pop(); comma_c(P_BRANCH); comma_c(a); if (!err) code[b] = here_c; break;
    case P_DOI: comma_c(P_DO); push(here_c); break;
    case P_LOOPI: comma_c(P_LOOP); comma_c(pop()); break;
    case P_PLOOPI: comma_c(P_PLOOP); comma_c(pop()); break;
    case P_I: if (rp < 1) err = E_UNDER; else push(rs[rp - 1]); break;
    case P_J: if (rp < 3) err = E_UNDER; else push(rs[rp - 3]); break;
    case P_UNLOOP: if (rp < 2) err = E_UNDER; else rp -= 2; break;
    case P_RECURSE: comma_c(latest); break;
    case P_PAREN: { const char *s; parse_until(')', &s); break; }
    case P_BSLASH: inpos = inlen; break;
    case P_DOTQI: { const char *s; int n = parse_until('"', &s); comma_c(P_DOTQ); comma_c(n); for (int i = 0; i < n; i++) comma_c((unsigned char)s[i]); break; }
    case P_DOTP: { const char *s; int n = parse_until(')', &s); for (int i = 0; i < n; i++) emit(s[i]); break; }
    case P_MS: a = pop(); if (!err) { ucell t0 = TSCL; while (TSCL - t0 < (ucell)a * 360000u && !forth_abort) forth_poll(); if (forth_abort) err = E_ABORT; } break;
    case P_TICKS: push((cell)TSCL); break;
    case P_TPMS: push(360000); break;
    }
}

static void execute(int w)
{
    struct word *d = &dict[w];
    if (d->kind == K_VAR || d->kind == K_CONST) { push(d->p); return; }
    if (d->kind == K_PRIM) { prim(d->p); return; }
    if (d->kind == K_EXT) { ext_fn[d->p](); return; }
    int rp0 = rp; cell ip0 = ip;
    rpush(-1); ip = d->p;
    unsigned n = 0;
    while (!err && ip >= 0) {
        if ((++n & 1023) == 0) { forth_poll(); if (forth_abort) { err = E_ABORT; break; } }
        if (ip >= NCODE) { err = E_ABORT; break; }
        int t = code[ip++];
        if (t < 0 || t >= nwords) { err = E_ABORT; break; }
        struct word *x = &dict[t];
        if (x->kind == K_COLON) { rpush(ip); ip = x->p; }
        else if (x->kind == K_PRIM) prim(x->p);
        else if (x->kind == K_EXT) ext_fn[x->p]();
        else push(x->p);
    }
    if (err) rp = rp0;
    ip = ip0;
}

void forth_init(void)
{
    nwords = 0; here_c = 0; dhere = 0; sp = rp = 0; state = 0; err = 0; base = 10; latest = -1;
    for (int i = 0; i < NPRIM; i++) {
        const char *s = PRIMS[i]; int n = 0; while (s[n]) n++;
        int w = add_word(s, n, K_PRIM, i);
        (void)w;
    }
    for (unsigned i = 0; i < sizeof IMMEDIATES; i++) dict[IMMEDIATES[i]].imm = 1;
}

/* ---- host extensions */
void forth_word(const char *name, void (*fn)(void))
{
    if (n_ext >= NEXT) { type(" forth_word: table full, not added: "); type(name); return; }   /* never silently */
    int n = 0; while (name[n]) n++;
    if (add_word(name, n, K_EXT, n_ext) >= 0) ext_fn[n_ext++] = fn;
}
void forth_push(int v) { push(v); }
int  forth_pop(void) { return pop(); }
int  forth_failed(void) { return err != 0; }
void forth_type(const char *s) { type(s); }
void forth_dot(int v) { dot(v); }
void forth_hex(unsigned v, int digits) { put_num(v, 0, 16, digits); }

static int compile_only(int w) { for (unsigned i = 0; i < sizeof COMPILE_ONLY; i++) if (COMPILE_ONLY[i] == w) return 1; return 0; }

void forth_eval(const char *line, int n)
{
    in = line; inlen = n; inpos = 0; err = 0; forth_abort = 0;
    for (;;) {
        const char *s; int len = parse_word(&s);
        if (!len) break;
        int w = find(s, len);
        if (w >= 0) {
            if (state && !dict[w].imm) comma_c(w);
            else if (!state && compile_only(w)) err = E_COMPILE;
            else execute(w);
        } else {
            cell v;
            if (number(s, len, &v)) { if (state) { comma_c(P_LIT); comma_c(v); } else push(v); }
            else err = E_UNDEF;
        }
        if (err) { int k = len < 16 ? len : 16; for (int i = 0; i < k; i++) errword[i] = s[i]; errword[k] = 0; break; }
    }
    if (err) {
        emit(' '); type(errword); emit(' '); type(errmsg[err]); cr();
        if (state) { nwords = def_start_w; here_c = def_start_c; state = 0; latest = -1; }
        sp = 0; rp = 0; forth_abort = 0;
    } else type(state ? " compiled\r\n" : " ok\r\n");
}
