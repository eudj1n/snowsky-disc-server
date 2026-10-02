#include "framing.h"
#include <string.h>

static int hex(unsigned char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

int disc_feed(disc_frames *f, const void *input, size_t len, disc_record_fn emit, void *ctx) {
    const unsigned char *p = input;
    while (len) {
        size_t target = 8;
        if (f->used >= 8) {
            target = 0;
            for (size_t i = 0; i < 8; i++) {
                int v = hex(f->data[i]);
                if (v < 0) return -1;
                if (i >= 4) target = target * 16 + (size_t)v;
            }
            if (target < 8) return -1;
        }
        size_t n = target - f->used;
        if (n > len) n = len;
        memcpy(f->data + f->used, p, n);
        f->used += n; p += n; len -= n;
        if (f->used < 8) continue;
        target = 0;
        for (size_t i = 0; i < 8; i++) {
            int v = hex(f->data[i]);
            if (v < 0) return -1;
            f->data[i] = (unsigned char)(i < 4 ? "0123456789abcdef"[v] : "0123456789ABCDEF"[v]);
            if (i >= 4) target = target * 16 + (size_t)v;
        }
        if (target < 8) return -1;
        if (f->used == target) {
            int rc = emit(f->data, target, ctx);
            f->used = 0;
            if (rc) return rc;
        }
    }
    return 0;
}

int disc_read_command(const unsigned char *p, size_t n) {
    return (n == 12 && !memcmp(p, "0599000C0000", 12)) ||
           (n == 8 && (!memcmp(p, "05010008", 8) || !memcmp(p, "02020008", 8) || !memcmp(p, "01050008", 8)));
}

int disc_utf8(const unsigned char *p, size_t n) {
    for (size_t i = 0; i < n;) {
        uint32_t c = p[i++], min;
        unsigned extra;
        if (c < 0x80) continue;
        if (c >= 0xc2 && c <= 0xdf) { extra = 1; min = 0x80; c &= 0x1f; }
        else if (c >= 0xe0 && c <= 0xef) { extra = 2; min = 0x800; c &= 0xf; }
        else if (c >= 0xf0 && c <= 0xf4) { extra = 3; min = 0x10000; c &= 7; }
        else return 0;
        if (extra > n - i) return 0;
        while (extra--) {
            unsigned char b = p[i++];
            if ((b & 0xc0) != 0x80) return 0;
            c = (c << 6) | (b & 0x3f);
        }
        if (c < min || c > 0x10ffff || (c >= 0xd800 && c <= 0xdfff)) return 0;
    }
    return 1;
}
