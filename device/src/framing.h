#ifndef DISC_FRAMING_H
#define DISC_FRAMING_H
#include <stddef.h>
#include <stdint.h>
#define DISC_FRAME_MAX 65535
typedef struct { unsigned char data[DISC_FRAME_MAX]; size_t used; } disc_frames;
typedef int (*disc_record_fn)(unsigned char *, size_t, void *);
/* Callback owns no bytes after return. Header is normalized in place. */
int disc_feed(disc_frames *, const void *, size_t, disc_record_fn, void *);
int disc_utf8(const unsigned char *, size_t);
int disc_read_command(const unsigned char *, size_t);
#endif
