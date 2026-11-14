/* Creating a utility's output file next to its source. */
#ifndef OUTFILE_H
#define OUTFILE_H
#include "r8.h"
/* Creates "<lname without extension><suffix><lname's extension>" in src's folder (" (2)" ... " (9)" before the
   extension if taken); if the file system will not create that name, an 8.3 name from src's 8.3 stem and tag
   (TRACK01R.WAV, TRACK0R2.WAV ...). dst (BROWSE_PATH + 128 bytes) gets the path, *name points at its file name.
   Returns 1 (long name), 2 (8.3 name) or 0 (no free name, or the create failed); *fd is open for writing. */
int out_create(int *fd, char *dst, const char **name, const char *src, const char *lname, const char *suffix, char tag);
#endif
