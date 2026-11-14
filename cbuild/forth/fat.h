#ifndef FAT_H
#define FAT_H
/* FAT32 on the SD card (partition 1): find files by path, list directories, read whole files, create new files.
   Names match the long name (if any) or the 8.3 name, case-insensitively, ASCII only. */
#include "hw.h"
typedef struct {
    u32 clus, size;
    u8 attr;                        /* 0x10 = directory */
    char name[40];                  /* long name if present (clipped), else the 8.3 name as "NAME.EXT" */
} fat_ent;
enum { FAT_OK, FAT_SD, FAT_NOFS, FAT_NOTFOUND, FAT_TOOBIG, FAT_EXISTS };
int fat_mount(void);                                   /* FAT_OK, or FAT_SD / FAT_NOFS */
int fat_find(const char *path, fat_ent *e);            /* "ZOOM_R8/LOOP"; the root is "" */
/* Directory entries one by one: *pos starts at 0; returns 1 with the next entry (skipping deleted entries and
   volume labels), or 0 at the end. */
int fat_next(const fat_ent *dir, u32 *pos, fat_ent *e);
/* Read the first n bytes of a file (n <= size) to dst (word aligned). poll() is called between sectors. */
int fat_read(const fat_ent *e, void *dst, u32 n, void (*poll)(void));
extern u32 fat_part, fat_spc;
void fat_copy(fat_ent *d, const fat_ent *s);           /* (the runtime library has no struct copy) */
/* Create a new file in dir with the 8.3 name name83 (11 characters, space padded, upper case) holding size bytes
   from buf (word aligned, readable up to the next 512-byte boundary). Only free clusters and an unused directory
   slot are written; FAT_EXISTS if the name exists, FAT_TOOBIG if the card or directory is full. */
int fat_create(const fat_ent *dir, const char *name83, const void *buf, u32 size, void (*poll)(void));
#endif
