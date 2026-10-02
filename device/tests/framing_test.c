#include "framing.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
static unsigned calls;
static size_t last_len;
static int receive(unsigned char *p, size_t n, void *ctx) {
    (void)ctx; calls++; last_len = n;
    assert(n >= 8); assert(p[0] != 'A'); return 0;
}
int main(void) {
    disc_frames *f = calloc(1, sizeof(*f)); assert(f);
    const char *s = "A599000c030605010008";
    for (size_t i = 0; i < strlen(s); i++) assert(!disc_feed(f, s+i, 1, receive, NULL));
    assert(calls == 2 && last_len == 8 && f->used == 0);
    assert(!disc_feed(f, "a202000C\xd0\x81\xd0\xb9", 12, receive, NULL));
    assert(calls == 3 && last_len == 12);
    assert(disc_feed(f, "xxxx0008", 8, receive, NULL) == -1);
    f->used = 0; assert(disc_feed(f, "05010007", 8, receive, NULL) == -1);
    f->used = 0;
    unsigned char *max = malloc(DISC_FRAME_MAX); assert(max);
    memset(max, 'x', DISC_FRAME_MAX); memcpy(max, "a202FFFF", 8);
    assert(!disc_feed(f, max, DISC_FRAME_MAX, receive, NULL));
    assert(last_len == DISC_FRAME_MAX && f->used == 0);
    assert(disc_utf8((const unsigned char *)"Ёй🎵", strlen("Ёй🎵")));
    assert(!disc_utf8((const unsigned char *)"\xc0\xaf", 2));
    assert(!disc_utf8((const unsigned char *)"\xed\xa0\x80", 3));
    assert(!disc_utf8((const unsigned char *)"\xf4\x90\x80\x80", 4));
    assert(!disc_utf8((const unsigned char *)"\xd0", 1));
    assert(disc_read_command((const unsigned char *)"0599000C0000", 12));
    assert(!disc_read_command((const unsigned char *)"0502000C0078", 12));
    free(max); free(f); puts("framing: all assertions passed");
}
