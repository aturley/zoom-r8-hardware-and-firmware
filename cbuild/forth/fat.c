/* FAT32 (see fat.h): reading, plus fat_create() for new files. One-sector caches for the FAT and for directory reads. */
#include "fat.h"
#include "sd.h"
u32 fat_part, fat_spc;
static u32 fat_start, data_start, root_clus, mounted, nfats, fat_sz, fsinfo_lba, max_clus;
static u32 sec[128], fsec[128], fsec_no = ~0u;     /* scratch sector, cached FAT sector */
static u32 dsec[128], dsec_no = ~0u;               /* cached directory sector */
static u32 cur_dir, cur_k, cur_c;                  /* fat_next's cursor: cluster cur_c is the cur_k-th of directory cur_dir */
static const u8 *b8(const u32 *p) { return (const u8 *)p; }
static u32 le16(const u8 *p) { return p[0] | p[1] << 8; }
static u32 le32(const u8 *p) { return p[0] | p[1] << 8 | p[2] << 16 | (u32)p[3] << 24; }

int fat_mount(void)
{
    mounted = 0; fsec_no = dsec_no = ~0u; cur_dir = cur_c = 0;
    if (!sd_ready && sd_init()) return FAT_SD;
    if (sd_read(0, sec)) return FAT_SD;
    const u8 *m = b8(sec);
    if (m[510] != 0x55 || m[511] != 0xAA) return FAT_NOFS;
    fat_part = le32(m + 0x1C6);
    if (sd_read(fat_part, sec)) return FAT_SD;
    const u8 *b = b8(sec);
    if (le16(b + 11) != 512 || !b[13] || le16(b + 0x16) != 0 || b[510] != 0x55) return FAT_NOFS;   /* FAT32 only */
    fat_spc = b[13];
    fat_start = fat_part + le16(b + 14);
    data_start = fat_start + b[16] * le32(b + 0x24);
    root_clus = le32(b + 0x2C);
    nfats = b[16]; fat_sz = le32(b + 0x24);
    fsinfo_lba = fat_part + le16(b + 0x30);
    u32 tot = le32(b + 0x20);
    max_clus = (fat_part + tot - data_start) / fat_spc + 1;    /* highest valid cluster number */
    if (max_clus > fat_sz * 128 - 1) max_clus = fat_sz * 128 - 1;
    mounted = 1;
    return FAT_OK;
}
static u32 next_clus(u32 c)
{
    u32 s = fat_start + c / 128;
    if (s != fsec_no) { if (sd_read(s, fsec)) return 0x0FFFFFFF; fsec_no = s; }
    return fsec[c % 128] & 0x0FFFFFFF;
}
static int is_end(u32 c) { return c < 2 || c >= 0x0FFFFFF8; }
static u32 clus_lba(u32 c) { return data_start + (c - 2) * fat_spc; }

int fat_next(const fat_ent *dir, u32 *pos, fat_ent *e)
{
    if (!mounted) return 0;
    char lfn[FAT_NAME]; int have_lfn = 0;
    for (;;) {
        u32 idx = *pos, per_clus = fat_spc * 16;           /* 16 entries per sector */
        u32 k = idx / per_clus, c = dir->clus, from = 0;
        if (cur_dir == dir->clus && cur_c && cur_k <= k) { c = cur_c; from = cur_k; }   /* resume the walk */
        for (u32 j = from; j < k && !is_end(c); j++) c = next_clus(c);
        if (is_end(c)) return 0;
        cur_dir = dir->clus; cur_k = k; cur_c = c;
        u32 lba = clus_lba(c) + (idx % per_clus) / 16;
        if (lba != dsec_no) { if (sd_read(lba, dsec)) return 0; dsec_no = lba; }
        const u8 *d = b8(dsec) + (idx % 16) * 32;
        (*pos)++;
        if (d[0] == 0) return 0;
        if (d[0] == 0xE5) { have_lfn = 0; continue; }
        if (d[11] == 0x0F) {                                /* long name piece: 13 UCS-2 characters */
            int seq = (d[0] & 0x1F) - 1;
            if (d[0] & 0x40) { have_lfn = 1; for (int i = 0; i < FAT_NAME; i++) lfn[i] = 0; }
            static const u8 off[13] = { 1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30 };
            for (int i = 0; i < 13; i++) {
                int at = seq * 13 + i; u32 ch = le16(d + off[i]);
                if (at < FAT_NAME - 1 && ch != 0xFFFF) lfn[at] = ch == 0 ? 0 : ch < 128 ? (char)ch : '?';
            }
            continue;
        }
        if (d[11] & 0x08) { have_lfn = 0; continue; }       /* volume label */
        e->attr = d[11];
        e->clus = le16(d + 20) << 16 | le16(d + 26);
        e->size = le32(d + 28);
        e->time = (u16)le16(d + 22); e->date = (u16)le16(d + 24);
        int n = 0;
        for (int i = 0; i < 8 && d[i] != ' '; i++) e->name83[n++] = (char)d[i];
        if (d[8] != ' ') { e->name83[n++] = '.'; for (int i = 8; i < 11 && d[i] != ' '; i++) e->name83[n++] = (char)d[i]; }
        e->name83[n] = 0;
        if (have_lfn) { for (int i = 0; i < FAT_NAME; i++) e->name[i] = lfn[i]; e->name[FAT_NAME - 1] = 0; }
        else for (int i = 0; i <= n; i++) e->name[i] = e->name83[i];
        return 1;
    }
}
void fat_copy(fat_ent *d, const fat_ent *s) { u32 *a = (u32 *)d; const u32 *b = (const u32 *)s; for (unsigned i = 0; i < sizeof *d / 4; i++) a[i] = b[i]; }
static int up(int c) { return c >= 'a' && c <= 'z' ? c - 32 : c; }
static int name_eq(const char *a, const char *p, int n)
{
    for (int i = 0; i < n; i++) if (up(a[i]) != up(p[i])) return 0;
    return a[n] == 0;
}
int fat_find(const char *path, fat_ent *e)
{
    if (!mounted && fat_mount()) return FAT_NOFS;
    e->clus = root_clus; e->size = 0; e->attr = 0x10; e->name[0] = 0; e->name83[0] = 0; e->date = e->time = 0;
    while (*path) {
        while (*path == '/') path++;
        int n = 0; while (path[n] && path[n] != '/') n++;
        if (!n) break;
        fat_ent d, x; u32 pos = 0; int found = 0; fat_copy(&d, e);
        while (fat_next(&d, &pos, &x)) if (name_eq(x.name, path, n) || name_eq(x.name83, path, n)) { found = 1; break; }
        if (!found) return FAT_NOTFOUND;
        fat_copy(e, &x);
        if (!e->clus) e->clus = root_clus;                  /* ".." to the root */
        path += n;
    }
    return FAT_OK;
}
int fat_read(const fat_ent *e, void *dst, u32 n, void (*poll)(void))
{
    if (n > e->size) return FAT_TOOBIG;
    u8 *out = (u8 *)dst;
    u32 c = e->clus, done = 0;
    while (done < n) {
        if (is_end(c)) return FAT_NOFS;
        u32 lba = clus_lba(c);
        for (u32 s = 0; s < fat_spc && done < n; ) {      /* whole blocks in runs (sd_read_n splits at 7) */
            u32 s0 = s, whole = (n - done) / 512, k = fat_spc - s < 16 - (s & 15) ? fat_spc - s : 16 - (s & 15);
            if (whole) {
                if (k > whole) k = whole;
                if (sd_read_n(lba + s, k, out + done)) return FAT_SD;
                done += k * 512; s += k;
            } else {
                if (sd_read(lba + s, sec)) return FAT_SD;
                const u8 *q = b8(sec); while (done < n) { out[done] = *q++; done++; }
                s++;
            }
            if (poll && (s0 >> 4) != (s >> 4)) poll();        /* every 16 blocks, as before */
        }
        c = next_clus(c);
    }
    return FAT_OK;
}

/* ---- writing a new file (fat_create). Conservative by design, because it runs on the user's card:
   - only clusters whose FAT entry reads 0 (free) are used, and each is checked again just before its FAT sector
     is written; only an unused directory slot (first byte 0x00 or 0xE5) in the directory's existing clusters is
     used: the directory is never extended, nothing existing is modified;
   - order: file data, then the FAT chain (every FAT copy), then the directory entry, then FSInfo (a valid free
     count is reduced by the clusters used; an unknown one stays unknown). Interrupted part-way, the card can only lose free space to clusters no
     entry points at (fsck reclaims them); no existing file can be touched;
   - 8.3 name only (no long-name entries); a fixed date (there is no clock); attribute ARCHIVE. */
#define MAXCLUS 8192u
#define clist ((u32 *)0xC0380000u)        /* 32 KB in DDR (L2 is full): between RELOAD's stage and trampoline */
static u32 alloc_hint = 2;
u32 fat_last_lba, fat_last_err;                 /* debug: where a failed create stopped */
static u32 fat_get(u32 c, int *err)
{
    u32 s = fat_start + c / 128;
    if (s != fsec_no) { if (sd_read(s, fsec)) { *err = 1; return 0x0FFFFFFF; } fsec_no = s; }
    return fsec[c % 128];
}
static int put32(u8 *p, u32 v) { p[0] = (u8)v; p[1] = (u8)(v >> 8); p[2] = (u8)(v >> 16); p[3] = (u8)(v >> 24); return 0; }
static void put16(u8 *p, u32 v) { p[0] = (u8)v; p[1] = (u8)(v >> 8); }
static fat_wr_t *wr_active;                      /* the open streaming writer (one at a time), or 0 */
static int wr_owns(u32 c)                          /* a cluster the writer has claimed (still free in the FAT) */
{
    if (!wr_active) return 0;
    for (u32 i = 0; i < wr_active->n; i++)
        if (c >= wr_active->ext[i].clus && c < wr_active->ext[i].clus + wr_active->ext[i].n) return 1;
    return 0;
}
/* A free directory slot in dir for name83 (FAT_EXISTS if the name is taken, also by the open writer; FAT_TOOBIG if the
   directory is full: it is never extended). The writer's reserved slot counts as used. */
static int find_slot(const fat_ent *dir, const char *name83, u32 *dlba_out, u32 *doff_out)
{
    int err = 0;
    if (wr_active) { int same = 1; for (int i = 0; i < 11 && same; i++) same = wr_active->name83[i] == name83[i]; if (same) return FAT_EXISTS; }
    u32 dlba = 0, doff = 0, cl = dir->clus, per_dir = fat_spc * 16;
    for (u32 idx = 0; ; idx++) {                                /* whole directory: free slot + duplicate check */
        if (idx && idx % per_dir == 0) { cl = fat_get(cl, &err) & 0x0FFFFFFF; if (err) return FAT_SD; }
        if (cl < 2 || cl >= 0x0FFFFFF8) break;                  /* end of the chain */
        u32 lba = clus_lba(cl) + (idx % per_dir) / 16;
        if (lba != dsec_no) { if (sd_read(lba, dsec)) return FAT_SD; dsec_no = lba; }
        const u8 *d = b8(dsec) + (idx % 16) * 32;
        int reserved = wr_active && lba == wr_active->dlba && (idx % 16) * 32 == wr_active->doff;
        if ((d[0] == 0 || d[0] == 0xE5) && !reserved) { if (!dlba) { dlba = lba; doff = (idx % 16) * 32; } if (d[0] == 0) break; continue; }
        if (reserved) continue;
        int same = d[11] != 0x0F; for (int i = 0; i < 11 && same; i++) same = d[i] == (u8)name83[i];
        if (same) return FAT_EXISTS;
    }
    if (!dlba) return FAT_TOOBIG;
    *dlba_out = dlba; *doff_out = doff;
    return FAT_OK;
}
#define DIRBUF ((u8 *)0xC0388000u)        /* fat_mkdir's new directory cluster (up to 64 KB), after the cluster list */
static int create(const fat_ent *dir, const char *name83, const void *buf, u32 size, void (*poll)(void), int is_dir)
{
    if (!mounted && fat_mount()) return FAT_NOFS;
    if (!(dir->attr & 0x10)) return FAT_NOTFOUND;
    u32 per = fat_spc * 512, need = is_dir ? 1 : size ? (size + per - 1) / per : 0;
    if (is_dir) { buf = DIRBUF; size = per; }               /* one cluster: ".", "..", then zeros (end of directory) */
    if (need > MAXCLUS) return FAT_TOOBIG;
    int err = 0;
    /* 1. free clusters */
    u32 n = 0, c = alloc_hint < 2 || alloc_hint > max_clus ? 2 : alloc_hint, scanned = 0;
    while (n < need && scanned < max_clus) {
        if (!(fat_get(c, &err) & 0x0FFFFFFF) && !wr_owns(c)) clist[n++] = c;
        if (err) return FAT_SD;
        if (++c > max_clus) c = 2;
        scanned++;
    }
    if (n < need) return FAT_TOOBIG;                            /* card full */
    /* 2. a free directory slot, and the name must not exist yet */
    u32 dlba, doff;
    { int r = find_slot(dir, name83, &dlba, &doff); if (r) return r; }
    if (is_dir) {                                               /* the new directory's own entries */
        u8 *b = DIRBUF; for (u32 i = 0; i < per; i++) b[i] = 0;
        u32 date = (46u << 9) | (1u << 5) | 1u, parent = dir->clus == root_clus ? 0 : dir->clus;
        for (int k = 0; k < 2; k++) {
            u8 *d = b + 32 * k; for (int i = 0; i < 11; i++) d[i] = ' ';
            d[0] = '.'; if (k) d[1] = '.';
            d[11] = 0x10; put16(d + 16, date); put16(d + 18, date); put16(d + 24, date);
            u32 c = k ? parent : clist[0]; put16(d + 20, c >> 16); put16(d + 26, c & 0xFFFF);
        }
    }
    /* 3. data: whole sectors (the caller's buffer must be readable up to the next 512-byte boundary) */
    const u8 *src = (const u8 *)buf; u32 left = (size + 511) / 512;
    for (u32 k = 0; k < n && left; ) {
        u32 run = 1; while (k + run < n && clist[k + run] == clist[k + run - 1] + 1) run++;
        u32 secs = run * fat_spc < left ? run * fat_spc : left;
        if (sd_write(clus_lba(clist[k]), secs, src)) { fat_last_lba = clus_lba(clist[k]); return FAT_SD; }
        src += secs * 512; left -= secs; k += run;
        if (poll) poll();
    }
    /* 4. the chain, sector by sector, into every FAT copy */
    for (u32 k = 0; k < n; ) {
        u32 s = clist[k] / 128;
        if (sd_read(fat_start + s, sec)) return FAT_SD;
        for (; k < n && clist[k] / 128 == s; k++) {
            u32 *e = &sec[clist[k] % 128];
            if (*e & 0x0FFFFFFF) { fat_last_err = clist[k]; return FAT_EXISTS; }   /* no longer free: stop */
            *e = (*e & 0xF0000000u) | (k + 1 < n ? clist[k + 1] : 0x0FFFFFFF);
        }
        for (u32 f = 0; f < nfats; f++) if (sd_write(fat_start + f * fat_sz + s, 1, sec)) return FAT_SD;
    }
    fsec_no = ~0u;
    /* 5. the directory entry */
    if (sd_read(dlba, sec)) return FAT_SD;
    u8 *d = (u8 *)sec + doff;
    if (d[0] != 0 && d[0] != 0xE5) return FAT_EXISTS;           /* slot taken meanwhile: data stay unlinked */
    for (int i = 0; i < 32; i++) d[i] = 0;
    for (int i = 0; i < 11; i++) d[i] = (u8)name83[i];
    d[11] = is_dir ? 0x10 : 0x20;                               /* DIRECTORY or ARCHIVE */
    u32 date = (46u << 9) | (1u << 5) | 1u;                     /* 2026-01-01: no real-time clock */
    put16(d + 16, date); put16(d + 18, date); put16(d + 24, date);
    u32 first = n ? clist[0] : 0;
    put16(d + 20, first >> 16); put16(d + 26, first & 0xFFFF); put32(d + 28, is_dir ? 0 : size);
    if (sd_write(dlba, 1, sec)) return FAT_SD;
    dsec_no = ~0u;
    /* 6. FSInfo: free count unknown, next free after this file */
    if (n && !sd_read(fsinfo_lba, sec)) {
        u8 *f = (u8 *)sec;
        if (le32(f) == 0x41615252u && le32(f + 484) == 0x61417272u) {
            u32 freec = le32(f + 488);                          /* valid count: subtract ours; else leave unknown */
            put32(f + 488, freec != 0xFFFFFFFFu && freec <= max_clus && freec >= n ? freec - n : 0xFFFFFFFFu);
            put32(f + 492, clist[n - 1] + 1 <= max_clus ? clist[n - 1] + 1 : 2);
            sd_write(fsinfo_lba, 1, sec);
        }
        alloc_hint = clist[n - 1] + 1;
    }
    return FAT_OK;
}
int fat_create(const fat_ent *dir, const char *name83, const void *buf, u32 size, void (*poll)(void))
{
    return create(dir, name83, buf, size, poll, 0);
}
int fat_mkdir(const fat_ent *dir, const char *name83)
{
    return create(dir, name83, 0, 0, 0, 1);
}

/* ---- reading at any offset: an extent map per open file */
int fat_map(const fat_ent *e, fat_map_t *m, fat_ext *ext, u32 max)
{
    if (!mounted && fat_mount()) return FAT_NOFS;
    m->size = e->size; m->clus_bytes = fat_spc * 512; m->n = 0; m->max = max; m->ext = ext;
    u32 need = (e->size + m->clus_bytes - 1) / m->clus_bytes, c = e->clus;
    for (u32 i = 0; i < need; i++) {
        if (is_end(c) || c > max_clus) return FAT_NOFS;              /* chain shorter than the size says */
        if (m->n && ext[m->n - 1].clus + ext[m->n - 1].n == c) ext[m->n - 1].n++;
        else {
            if (m->n == max) return FAT_TOOBIG;                     /* more fragments than the map holds */
            ext[m->n].at = i; ext[m->n].clus = c; ext[m->n].n = 1; m->n++;
        }
        if (i + 1 < need) c = next_clus(c);                     /* a read error ends the chain: FAT_NOFS above */
    }
    return FAT_OK;
}
static const fat_ext *find_ext(const fat_map_t *m, u32 ci)       /* the extent holding file cluster ci */
{
    u32 lo = 0, hi = m->n;
    while (hi - lo > 1) { u32 mid = (lo + hi) / 2; if (m->ext[mid].at <= ci) lo = mid; else hi = mid; }
    return &m->ext[lo];
}
int fat_read_some(const fat_map_t *m, u32 off, void *dst, u32 n, u32 max_blocks, u32 *got)
{
    u8 *out = (u8 *)dst;
    u32 done = 0, used = 0;
    *got = 0;
    if (off >= m->size) return FAT_OK;
    if (n > m->size - off) n = m->size - off;
    if (!max_blocks) max_blocks = 1;
    while (done < n && used < max_blocks) {
        u32 pos = off + done, ci = pos / m->clus_bytes;
        const fat_ext *x = find_ext(m, ci);
        u32 lba = clus_lba(x->clus + (ci - x->at)) + (pos % m->clus_bytes) / 512, bo = pos % 512;
        if (bo == 0 && n - done >= 512 && !(((u32)(out + done)) & 3)) {     /* whole blocks straight to dst */
            u32 k = (n - done) / 512, left = ((x->at + x->n) * m->clus_bytes - pos) / 512;
            if (k > left) k = left;
            if (k > max_blocks - used) k = max_blocks - used;
            if (k > 128) k = 128;
            if (sd_read_n(lba, k, out + done)) return FAT_SD;
            done += k * 512; used += k;
        } else {                                                     /* a partial or unaligned block, through sec */
            if (sd_read(lba, sec)) return FAT_SD;
            u32 k = 512 - bo; if (k > n - done) k = n - done;
            const u8 *q = b8(sec) + bo; for (u32 i = 0; i < k; i++) out[done + i] = q[i];
            done += k; used++;
        }
        *got = done;
    }
    return FAT_OK;
}
int fat_read_at(const fat_map_t *m, u32 off, void *dst, u32 n)
{
    if (off > m->size || n > m->size - off) return FAT_TOOBIG;
    u32 done = 0, got;
    while (done < n) {
        int r = fat_read_some(m, off + done, (u8 *)dst + done, n - done, 128, &got);
        if (r) return r;
        if (!got) return FAT_NOFS;
        done += got;
    }
    return FAT_OK;
}

/* ---- the streaming writer (fat.h) */
#define WR_SCAN 8u                                     /* FAT blocks scanned per fat_wwrite call when it needs clusters */
int fat_wopen(fat_wr_t *w, const fat_ent *dir, const char *name83, fat_ext *ext, u32 max_ext)
{
    if (!mounted && fat_mount()) return FAT_NOFS;
    if (wr_active) return FAT_STATE;
    if (!(dir->attr & 0x10)) return FAT_NOTFOUND;
    u32 dlba, doff;
    int r = find_slot(dir, name83, &dlba, &doff);
    if (r) return r;
    if (sd_read(dlba, sec)) return FAT_SD;
    u8 *d = (u8 *)sec + doff;
    if (d[0] == 0) {                                   /* the end-of-directory slot: mark it deleted, so entries made
                                                          after it (fat_create meanwhile) stay visible to readers */
        for (int i = 0; i < 32; i++) d[i] = 0;
        d[0] = 0xE5;
        if (sd_write(dlba, 1, sec)) return FAT_SD;
        dsec_no = ~0u;
    }
    w->size = 0; w->state = 1; w->ck = 0; w->ext = ext; w->n = 0; w->max = max_ext; w->nclus = 0;
    w->scan = alloc_hint < 2 || alloc_hint > max_clus ? 2 : alloc_hint; w->scanned = 0;
    w->dlba = dlba; w->doff = doff;
    for (int i = 0; i < 11; i++) w->name83[i] = name83[i];
    w->name83[11] = 0;
    wr_active = w;
    return FAT_OK;
}
static int wr_grow(fat_wr_t *w)                       /* claim more clusters: FAT_OK, FAT_AGAIN (keep scanning), ... */
{
    int err = 0;
    for (u32 k = 0; k < WR_SCAN * 128; k++) {
        if (w->scanned >= max_clus) return FAT_TOOBIG;   /* card full */
        u32 c = w->scan;
        if (++w->scan > max_clus) w->scan = 2;
        w->scanned++;
        if (fat_get(c, &err) & 0x0FFFFFFF || wr_owns(c)) { if (err) return FAT_SD; continue; }
        if (err) return FAT_SD;
        if (w->n && w->ext[w->n - 1].clus + w->ext[w->n - 1].n == c) w->ext[w->n - 1].n++;
        else {
            if (w->n == w->max) return FAT_TOOBIG;      /* more fragments than the caller's array holds */
            w->ext[w->n].at = w->nclus; w->ext[w->n].clus = c; w->ext[w->n].n = 1; w->n++;
        }
        w->nclus++;
        while (w->scan <= max_clus && w->ext[w->n - 1].n < 2048) {    /* take the free run that follows, */
            u32 c2 = w->scan;
            if (c2 / 128 != c / 128) break;            /* but only within the FAT block in hand: no more reads now;
                                                          the next call goes on extending the same run */
            if (fat_get(c2, &err) & 0x0FFFFFFF || err || wr_owns(c2) || c2 != w->ext[w->n - 1].clus + w->ext[w->n - 1].n) break;
            w->ext[w->n - 1].n++; w->nclus++; w->scan++; w->scanned++;
        }
        alloc_hint = w->scan;
        return FAT_OK;
    }
    return FAT_AGAIN;
}
int fat_wwrite(fat_wr_t *w, const void *data, u32 n, u32 *taken)
{
    *taken = 0;
    if (w->state != 1) return FAT_STATE;
    if (!n) return FAT_OK;
    u32 cb = fat_spc * 512, ci = w->size / cb;
    if (ci >= w->nclus) { int r = wr_grow(w); if (r) return r; }
    const fat_ext *x = &w->ext[w->n - 1];
    for (u32 i = 0; i < w->n; i++) if (ci >= w->ext[i].at && ci < w->ext[i].at + w->ext[i].n) { x = &w->ext[i]; break; }
    u32 lba = clus_lba(x->clus + (ci - x->at)) + (w->size % cb) / 512;
    u32 left = ((x->at + x->n) * cb - w->size) / 512;           /* blocks to the end of this extent */
    const void *src = data; u32 blocks, bytes;
    if (n >= 512) {
        if (((u32)data) & 3) return FAT_STATE;                 /* whole blocks must come from a word-aligned buffer */
        blocks = n / 512; if (blocks > 128) blocks = 128; if (blocks > left) blocks = left;
        bytes = blocks * 512;
    } else {                                                   /* the last, partial block: zero-padded, then no more */
        u8 *t = (u8 *)w->tail; const u8 *q = (const u8 *)data;
        for (u32 i = 0; i < 512; i++) t[i] = i < n ? q[i] : 0;
        src = w->tail; blocks = 1; bytes = n;
    }
    int e = sd_write_start(lba, blocks, src);
    if (e == SD_EBUSY) return FAT_AGAIN;                       /* the previous chunk is still programming */
    if (e) return FAT_SD;
    w->size += bytes; *taken = bytes;
    if (bytes % 512) w->state = 2;                             /* ended: only fat_wpatch and fat_wclose now */
    return FAT_OK;
}
int fat_wpatch(fat_wr_t *w, u32 off, const void *data, u32 n)
{
    if (w->state != 1 && w->state != 2) return FAT_STATE;
    if (!n || off / 512 != (off + n - 1) / 512 || off + n > ((w->size + 511) & ~511u)) return FAT_TOOBIG;
    u32 cb = fat_spc * 512, ci = off / cb;
    const fat_ext *x = &w->ext[0];
    for (u32 i = 0; i < w->n; i++) if (ci >= w->ext[i].at && ci < w->ext[i].at + w->ext[i].n) { x = &w->ext[i]; break; }
    u32 lba = clus_lba(x->clus + (ci - x->at)) + (off % cb) / 512;
    if (sd_read(lba, sec)) return FAT_SD;                      /* (waits for a pending write first) */
    u8 *b = (u8 *)sec; const u8 *q = (const u8 *)data;
    for (u32 i = 0; i < n; i++) b[off % 512 + i] = q[i];
    if (sd_write(lba, 1, sec)) return FAT_SD;
    return FAT_OK;
}
int fat_wclose(fat_wr_t *w)
{
    if (w->state < 1 || w->state > 3) return FAT_STATE;
    if (sd_busy() == SD_EBUSY) return FAT_AGAIN;               /* let the last chunk finish programming */
    u32 cb = fat_spc * 512, used = (w->size + cb - 1) / cb;
    if (w->state != 3) { w->state = 3; w->ck = 0; }
    if (w->ck < used) {                                        /* one FAT block per call, into every FAT copy */
        u32 i = 0; while (!(w->ck >= w->ext[i].at && w->ck < w->ext[i].at + w->ext[i].n)) i++;
        u32 c = w->ext[i].clus + (w->ck - w->ext[i].at), s = c / 128;
        if (sd_read(fat_start + s, sec)) return FAT_SD;
        while (w->ck < used) {
            while (!(w->ck >= w->ext[i].at && w->ck < w->ext[i].at + w->ext[i].n)) i++;
            c = w->ext[i].clus + (w->ck - w->ext[i].at);
            if (c / 128 != s) break;
            u32 nx = 0x0FFFFFFF;
            if (w->ck + 1 < used) { u32 j = i; if (w->ck + 1 >= w->ext[j].at + w->ext[j].n) j++; nx = w->ext[j].clus + (w->ck + 1 - w->ext[j].at); }
            u32 *e = &sec[c % 128];
            if (*e & 0x0FFFFFFF) { fat_last_err = c; return FAT_EXISTS; }   /* no longer free: the file stays unlinked */
            *e = (*e & 0xF0000000u) | nx;
            w->ck++;
        }
        for (u32 f = 0; f < nfats; f++) if (sd_write(fat_start + f * fat_sz + s, 1, sec)) return FAT_SD;
        fsec_no = ~0u;
        return FAT_AGAIN;
    }
    /* the directory entry: the file appears */
    if (sd_read(w->dlba, sec)) return FAT_SD;
    u8 *d = (u8 *)sec + w->doff;
    if (d[0] != 0 && d[0] != 0xE5) return FAT_EXISTS;
    for (int i = 0; i < 32; i++) d[i] = 0;
    for (int i = 0; i < 11; i++) d[i] = (u8)w->name83[i];
    d[11] = 0x20;
    u32 date = (46u << 9) | (1u << 5) | 1u;
    put16(d + 16, date); put16(d + 18, date); put16(d + 24, date);
    u32 first = used ? w->ext[0].clus : 0;
    put16(d + 20, first >> 16); put16(d + 26, first & 0xFFFF); put32(d + 28, w->size);
    if (sd_write(w->dlba, 1, sec)) return FAT_SD;
    dsec_no = ~0u;
    if (used && !sd_read(fsinfo_lba, sec)) {                   /* FSInfo: free count, next free */
        u8 *f = (u8 *)sec;
        if (le32(f) == 0x41615252u && le32(f + 484) == 0x61417272u) {
            u32 freec = le32(f + 488);
            put32(f + 488, freec != 0xFFFFFFFFu && freec <= max_clus && freec >= used ? freec - used : 0xFFFFFFFFu);
            put32(f + 492, alloc_hint <= max_clus ? alloc_hint : 2);
            sd_write(fsinfo_lba, 1, sec);
        }
    }
    w->state = 0; wr_active = 0;
    return FAT_OK;
}
void fat_wabort(fat_wr_t *w)
{
    if (wr_active == w) wr_active = 0;                         /* the claimed clusters were never linked: still free */
    w->state = 0;
}
