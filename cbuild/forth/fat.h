#ifndef FAT_H
#define FAT_H
/* FAT32 on the SD card (partition 1): find files by path, list directories, read whole files or any part of one,
   create new files and folders. Names match the long name (if any) or the 8.3 name, case-insensitively, ASCII only.
   Everything here is synchronous and polled: call it from the main loop, never from an interrupt. */
#include "hw.h"
#define FAT_NAME 64
typedef struct {
    u32 clus, size;
    u8 attr;                        /* the raw attribute byte: 0x10 directory, 0x02 hidden, 0x04 system, 0x01 read-only */
    char name[FAT_NAME];            /* long name if present (clipped to 63 ASCII characters, others '?'), else name83 */
    char name83[13];                /* the 8.3 name as "NAME.EXT" ("." and ".." for those entries) */
    u16 date, time;                 /* modification date and time, FAT format (the R8 writes 2011-01-01; we 2026-01-01) */
} fat_ent;
enum { FAT_OK, FAT_SD, FAT_NOFS, FAT_NOTFOUND, FAT_TOOBIG, FAT_EXISTS, FAT_AGAIN, FAT_STATE };
int fat_mount(void);                                   /* FAT_OK, or FAT_SD / FAT_NOFS */
int fat_find(const char *path, fat_ent *e);            /* "ZOOM_R8/LOOP"; the root is "" */
/* Directory entries one by one: *pos starts at 0; returns 1 with the next entry (skipping deleted entries and
   volume labels; "." and ".." are returned), or 0 at the end. pos is an opaque position: keep any value it had
   between calls and pass it back later to resume from there (paging back and forth). Walking the directory's
   cluster chain is cached, so consecutive calls are cheap. */
int fat_next(const fat_ent *dir, u32 *pos, fat_ent *e);
/* Read the first n bytes of a file (n <= size) to dst (word aligned). poll() is called between sectors. */
int fat_read(const fat_ent *e, void *dst, u32 n, void (*poll)(void));
extern u32 fat_part, fat_spc;
void fat_copy(fat_ent *d, const fat_ent *s);           /* (the runtime library has no struct copy) */
/* Create a new file in dir with the 8.3 name name83 (11 characters, space padded, upper case) holding size bytes
   from buf (word aligned, readable up to the next 512-byte boundary). Only free clusters and an unused directory
   slot are written; FAT_EXISTS if the name exists, FAT_TOOBIG if the card or directory is full. */
int fat_create(const fat_ent *dir, const char *name83, const void *buf, u32 size, void (*poll)(void));
/* Create a folder in dir (name83 as above): one cluster with "." and "..", then the directory entry, in the same
   safe order as fat_create. Uses DDR 0xC0388000 (one cluster, up to 64 KB) as scratch. */
int fat_mkdir(const fat_ent *dir, const char *name83);

/* Reading any part of a file. fat_map walks the file's cluster chain once and stores it as extents (runs of
   consecutive clusters) in a caller-allocated array, so later reads need no FAT access: a file written in one go is
   usually one extent. Maps are independent: several may be open at once, on the same file too. FAT_TOOBIG if the
   file has more fragments than max; FAT_NOFS if the chain is shorter than the size. */
typedef struct { u32 at, clus, n; } fat_ext;          /* file clusters at..at+n-1 are clusters clus..clus+n-1 */
typedef struct { u32 size, clus_bytes, n, max; fat_ext *ext; } fat_map_t;
int fat_map(const fat_ent *e, fat_map_t *m, fat_ext *ext, u32 max);
/* Read up to n bytes at byte offset off, touching at most max_blocks 512-byte blocks (so the time per call is
   bounded: one multi-block command of up to 128 blocks, or one block); *got = bytes read (0 at or past the end).
   Fastest when off is a multiple of 512 and dst is word aligned: whole blocks go straight to dst; anything else goes
   a block at a time through a scratch block. Calls never loop beyond max_blocks. */
int fat_read_some(const fat_map_t *m, u32 off, void *dst, u32 n, u32 max_blocks, u32 *got);
/* Read exactly n bytes at off (FAT_TOOBIG if that runs past the end), in runs of up to 128 blocks. */
int fat_read_at(const fat_map_t *m, u32 off, void *dst, u32 n);

/* Streaming writer: one new file written in bounded steps while other work goes on (recording while audio plays).
   Nothing reaches the FAT or the directory until fat_wclose, in the same safe order as fat_create (data, then the
   cluster chain, then the directory entry), so an interrupted recording only leaves unlinked clusters (and possibly a
   deleted directory entry). One writer at a time; while it is open, fat_create avoids its clusters, slot and name.
   - fat_wopen: name83 as for fat_create; ext/max_ext: a caller-allocated array (DDR) for the file's runs of
     clusters, as fat_map's (each run is up to 2048 clusters; a full card fragments more).
   - fat_wwrite: writes the start of data[0..n): whole blocks (data word aligned) in one command of up to 128 blocks
     within one run of clusters, or, if n < 512, the final partial block (zero padded; after it only fat_wpatch and
     fat_wclose are allowed). *taken = bytes consumed; pass the rest again. FAT_AGAIN (nothing taken): the previous
     chunk is still programming, or more clusters are being found; call again later (each call is bounded).
   - fat_wpatch: change n bytes at off inside one block already written (e.g. the WAV header's sizes), before close.
   - fat_wclose: call until it returns something other than FAT_AGAIN; each call writes at most one FAT block (in
     every FAT copy) or the directory entry.
   - fat_wabort: forget the file; its clusters stay free. */
typedef struct {
    u32 size;                       /* bytes written so far */
    u32 state, ck, scan, scanned;   /* (internal) */
    fat_ext *ext; u32 n, max, nclus;
    u32 dlba, doff;
    char name83[12];
    u32 tail[128];                  /* the zero-padded last block */
} fat_wr_t;
int fat_wopen(fat_wr_t *w, const fat_ent *dir, const char *name83, fat_ext *ext, u32 max_ext);
int fat_wwrite(fat_wr_t *w, const void *data, u32 n, u32 *taken);
int fat_wpatch(fat_wr_t *w, u32 off, const void *data, u32 n);
int fat_wclose(fat_wr_t *w);
void fat_wabort(fat_wr_t *w);
#endif
