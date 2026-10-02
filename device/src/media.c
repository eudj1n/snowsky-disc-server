#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#include "media.h"
#include "framing.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>

/* Extensions the stock library indexes; nothing else on the card is readable here. */
static const char *const AUDIO[] = {"flac", "wav", "mp3", "m4a", "aac", "ogg", "opus", "ape", "wv",
                                    "dsf", "dff", "aif", "aiff", "alac", "wma", NULL};
static const char *const COVERS[] = {"cover.jpg", "folder.jpg", "front.jpg", "Cover.jpg", "Folder.jpg", "Front.jpg",
                                     "COVER.JPG", "FOLDER.JPG", "FRONT.JPG", "cover.jpeg", "folder.jpeg",
                                     "cover.png", "folder.png", "front.png", "Cover.png", "Folder.png", NULL};

static const char *extension(const char *name) {
    const char *dot = strrchr(name, '.');
    return dot && dot != name && dot[1] ? dot + 1 : NULL;
}

static int audio_name(const char *name) {
    const char *ext = extension(name);
    for (size_t i = 0; ext && AUDIO[i]; i++)
        if (!strcasecmp(ext, AUDIO[i])) return 1;
    return 0;
}

int disc_media_open(const char *root, const char *path, int *dir_fd, int *file_fd, char *name, size_t name_capacity) {
    size_t root_len = strlen(root), path_len = strlen(path);
    if (!root_len || root_len >= DISC_MEDIA_MAX_PATH) return 503;
    if (path_len > DISC_MEDIA_MAX_PATH || strncmp(path, root, root_len) || path[root_len] != '/' || !path[root_len + 1])
        return 403;
    for (size_t i = 0; i < path_len; i++)
        if ((unsigned char)path[i] < 0x20 || path[i] == 0x7f || path[i] == '\\') return 403;
    int dir = open(root, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (dir < 0) return 503;
    const char *rel = path + root_len + 1;
    char part[256];
    size_t n;
    for (;;) {
        const char *slash = strchr(rel, '/');
        n = slash ? (size_t)(slash - rel) : strlen(rel);
        if (!n || n >= sizeof(part)) { close(dir); return 403; }
        memcpy(part, rel, n);
        part[n] = 0;
        if (!strcmp(part, ".") || !strcmp(part, "..")) { close(dir); return 403; }
        if (!slash) break;
        int next = openat(dir, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        int saved = errno;
        close(dir);
        if (next < 0) return saved == ENOENT || saved == ENOTDIR ? 404 : 403;
        dir = next;
        rel = slash + 1;
    }
    if (!audio_name(part) || n >= name_capacity) { close(dir); return 403; }
    int fd = openat(dir, part, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) {
        int saved = errno;
        close(dir);
        return saved == ENOENT ? 404 : 403;
    }
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode)) { close(fd); close(dir); return 404; }
    memcpy(name, part, n + 1);
    *dir_fd = dir;
    *file_fd = fd;
    return 0;
}

static int read_at(int fd, long long offset, void *buffer, size_t length) {
    size_t used = 0;
    while (used < length) {
        ssize_t n = pread(fd, (char *)buffer + used, length - used, (off_t)(offset + (long long)used));
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return -1;
        used += (size_t)n;
    }
    return 0;
}
static unsigned be24(const unsigned char *p) { return (unsigned)p[0] << 16 | (unsigned)p[1] << 8 | p[2]; }
static unsigned be32(const unsigned char *p) { return (unsigned)p[0] << 24 | (unsigned)p[1] << 16 | (unsigned)p[2] << 8 | p[3]; }
static unsigned le16(const unsigned char *p) { return (unsigned)p[1] << 8 | p[0]; }
static unsigned le32(const unsigned char *p) { return (unsigned)p[3] << 24 | (unsigned)p[2] << 16 | (unsigned)p[1] << 8 | p[0]; }

/* The first valid value of a tag wins; values must be UTF-8 without control characters. */
static void copy_tag(char *destination, size_t capacity, const char *value, size_t length) {
    if (destination[0] || !length || length >= capacity || !disc_utf8((const unsigned char *)value, length)) return;
    for (size_t i = 0; i < length; i++)
        if ((unsigned char)value[i] < 0x20 || value[i] == 0x7f) return;
    memcpy(destination, value, length);
    destination[length] = 0;
}

static int key_is(const char *key, size_t length, const char *name) {
    return strlen(name) == length && !strncasecmp(key, name, length);
}

static void vorbis_comments(int fd, long long offset, size_t length, disc_media_info *info) {
    if (length < 8 || length > (1u << 20)) return;
    unsigned char *block = malloc(length);
    if (!block || read_at(fd, offset, block, length)) { free(block); return; }
    size_t p = 4 + (size_t)le32(block);
    if (p < 4 || p > length - 4) { free(block); return; }
    unsigned count = le32(block + p);
    p += 4;
    for (unsigned i = 0; i < count && i < 4096; i++) {
        if (p > length - 4) break;
        size_t size = le32(block + p);
        p += 4;
        if (size > length - p) break;
        const char *comment = (const char *)block + p, *equals = memchr(comment, '=', size);
        if (equals) {
            size_t key_length = (size_t)(equals - comment), value_length = size - key_length - 1;
            const char *value = equals + 1;
            if (key_is(comment, key_length, "TITLE")) copy_tag(info->title, sizeof(info->title), value, value_length);
            else if (key_is(comment, key_length, "ARTIST")) copy_tag(info->artist, sizeof(info->artist), value, value_length);
            else if (key_is(comment, key_length, "ALBUM")) copy_tag(info->album, sizeof(info->album), value, value_length);
            else if (key_is(comment, key_length, "ALBUMARTIST") || key_is(comment, key_length, "ALBUM ARTIST"))
                copy_tag(info->album_artist, sizeof(info->album_artist), value, value_length);
            else if (key_is(comment, key_length, "GENRE")) copy_tag(info->genre, sizeof(info->genre), value, value_length);
            else if (key_is(comment, key_length, "TRACKNUMBER")) copy_tag(info->track, sizeof(info->track), value, value_length);
            else if (key_is(comment, key_length, "DISCNUMBER")) copy_tag(info->disc, sizeof(info->disc), value, value_length);
            else if (key_is(comment, key_length, "DATE")) copy_tag(info->date, sizeof(info->date), value, value_length);
            else if ((key_is(comment, key_length, "LYRICS") || key_is(comment, key_length, "UNSYNCEDLYRICS") ||
                      key_is(comment, key_length, "UNSYNCED LYRICS")) &&
                     !info->lyrics.present && value_length && value_length <= DISC_MEDIA_MAX_LYRICS) {
                info->lyrics.present = 1;
                info->lyrics.offset = offset + (long long)(value - (const char *)block);
                info->lyrics.length = value_length;
                snprintf(info->lyrics.mime, sizeof(info->lyrics.mime), "text/plain");
            }
        }
        p += size;
    }
    free(block);
}

static void picture(int fd, long long offset, size_t length, disc_media_info *info) {
    unsigned char head[8], word[4], tail[20];
    if (length < 32 || read_at(fd, offset, head, sizeof(head))) return;
    unsigned type = be32(head), mime_length = be32(head + 4);
    if (!mime_length || mime_length >= 32 || 8 + (size_t)mime_length + 4 > length) return;
    char mime[32];
    if (read_at(fd, offset + 8, mime, mime_length)) return;
    mime[mime_length] = 0;
    if (read_at(fd, offset + 8 + mime_length, word, 4)) return;
    size_t description = be32(word), fixed = 8 + (size_t)mime_length + 4;
    if (description > length - fixed || length - fixed - description < 20) return;
    long long rest = offset + (long long)(fixed + description);
    if (read_at(fd, rest, tail, sizeof(tail))) return;
    size_t size = be32(tail + 16);
    if (!size || size > DISC_MEDIA_MAX_IMAGE || size > length - fixed - description - 20) return;
    const char *normalized = !strcasecmp(mime, "image/jpeg") || !strcasecmp(mime, "image/jpg") ? "image/jpeg"
                           : !strcasecmp(mime, "image/png") ? "image/png" : NULL;
    if (!normalized) return;
    /* Prefer the front cover (type 3); otherwise keep the first picture. */
    if (info->picture.present && (info->picture_type == 3 || type != 3)) return;
    info->picture.present = 1;
    info->picture.offset = rest + 20;
    info->picture.length = size;
    info->picture_type = type;
    snprintf(info->picture.mime, sizeof(info->picture.mime), "%s", normalized);
}

static void flac(int fd, disc_media_info *info) {
    unsigned char head[10];
    long long offset = 0;
    if (read_at(fd, 0, head, sizeof(head))) return;
    if (!memcmp(head, "ID3", 3)) {
        /* A leading ID3v2 tag (syncsafe size) is tolerated before the stream marker. */
        long long size = (long long)(head[6] & 0x7f) << 21 | (head[7] & 0x7f) << 14 | (head[8] & 0x7f) << 7 | (head[9] & 0x7f);
        offset = 10 + size + ((head[5] & 0x10) ? 10 : 0);
    }
    unsigned char marker[4];
    if (read_at(fd, offset, marker, 4) || memcmp(marker, "fLaC", 4)) return;
    offset += 4;
    for (int i = 0; i < 128; i++) {
        unsigned char block[4];
        if (read_at(fd, offset, block, 4)) return;
        int last = block[0] & 0x80, type = block[0] & 0x7f;
        size_t length = be24(block + 1);
        long long data = offset + 4;
        if (data + (long long)length > info->bytes) return;
        if (type == 0 && length >= 18) {
            unsigned char s[18];
            if (!read_at(fd, data, s, sizeof(s))) {
                unsigned rate = (unsigned)s[10] << 12 | (unsigned)s[11] << 4 | s[12] >> 4;
                unsigned long long total = (unsigned long long)(s[13] & 0x0f) << 32 | be32(s + 14);
                info->sample_rate = rate;
                info->channels = ((s[12] >> 1) & 7) + 1;
                info->bits = (((unsigned)s[12] & 1) << 4 | s[13] >> 4) + 1;
                if (rate && total) {
                    info->has_duration = 1;
                    info->duration_ms = total * 1000 / rate;
                }
            }
        } else if (type == 4) {
            vorbis_comments(fd, data, length, info);
        } else if (type == 6) {
            picture(fd, data, length, info);
        }
        if (last) return;
        offset = data + (long long)length;
    }
}

static void wav(int fd, disc_media_info *info) {
    unsigned char head[12];
    if (read_at(fd, 0, head, sizeof(head)) || memcmp(head, "RIFF", 4) || memcmp(head + 8, "WAVE", 4)) return;
    long long offset = 12;
    unsigned byte_rate = 0;
    for (int i = 0; i < 64 && offset + 8 <= info->bytes; i++) {
        unsigned char chunk[8];
        if (read_at(fd, offset, chunk, sizeof(chunk))) return;
        unsigned long long size = le32(chunk + 4);
        if (!memcmp(chunk, "fmt ", 4) && size >= 16) {
            unsigned char format[16];
            if (read_at(fd, offset + 8, format, sizeof(format))) return;
            info->channels = le16(format + 2);
            info->sample_rate = le32(format + 4);
            byte_rate = le32(format + 8);
            info->bits = le16(format + 14);
        } else if (!memcmp(chunk, "data", 4)) {
            if (byte_rate) {
                info->has_duration = 1;
                info->duration_ms = size * 1000 / byte_rate;
            }
            return;
        }
        offset += 8 + (long long)size + (long long)(size & 1);
    }
}

/* ---- ID3v2 / MP3 ------------------------------------------------------ */

static unsigned syncsafe(const unsigned char *p) {
    return (unsigned)(p[0] & 0x7f) << 21 | (unsigned)(p[1] & 0x7f) << 14 | (unsigned)(p[2] & 0x7f) << 7 | (p[3] & 0x7f);
}
static void put_utf8(char *out, size_t cap, size_t *used, unsigned code) {
    char b[4]; size_t n;
    if (code < 0x80) { b[0] = (char)code; n = 1; }
    else if (code < 0x800) { b[0] = (char)(0xc0 | code >> 6); b[1] = (char)(0x80 | (code & 0x3f)); n = 2; }
    else if (code < 0x10000) { b[0] = (char)(0xe0 | code >> 12); b[1] = (char)(0x80 | ((code >> 6) & 0x3f)); b[2] = (char)(0x80 | (code & 0x3f)); n = 3; }
    else { b[0] = (char)(0xf0 | code >> 18); b[1] = (char)(0x80 | ((code >> 12) & 0x3f)); b[2] = (char)(0x80 | ((code >> 6) & 0x3f)); b[3] = (char)(0x80 | (code & 0x3f)); n = 4; }
    if (*used + n < cap) { memcpy(out + *used, b, n); *used += n; }
}
/* ID3v2 text (encoding 0 ISO-8859-1, 1 UTF-16 with BOM, 2 UTF-16BE, 3 UTF-8)
 * to UTF-8. Several values (ID3v2.4 separates them by NUL) are joined by
 * "; ", the separator the page already splits joint credits on. */
static size_t id3_text(int encoding, const unsigned char *in, size_t n, char *out, size_t cap) {
    size_t used = 0, i = 0;
    int big = encoding == 2, pending = 0;
    if (!cap) return 0;
    while (i < n) {
        unsigned code;
        if (encoding == 1 || encoding == 2) {
            if (i + 1 >= n) break;
            unsigned unit = big ? (unsigned)in[i] << 8 | in[i + 1] : (unsigned)in[i + 1] << 8 | in[i];
            i += 2;
            if (unit == 0xfeff) continue;
            if (unit == 0xfffe) { big = !big; continue; }
            if (unit >= 0xd800 && unit < 0xdc00 && i + 1 < n) {
                unsigned low = big ? (unsigned)in[i] << 8 | in[i + 1] : (unsigned)in[i + 1] << 8 | in[i];
                if (low >= 0xdc00 && low < 0xe000) { i += 2; unit = 0x10000 + ((unit - 0xd800) << 10) + (low - 0xdc00); }
            }
            code = unit;
        } else if (encoding == 3) {
            code = in[i++];
            if (code) { if (used + 1 < cap) out[used++] = (char)code; pending = 1; continue; }
        } else code = in[i++];
        if (!code) {
            if (pending) { size_t mark = used; put_utf8(out, cap, &used, ';'); put_utf8(out, cap, &used, ' '); if (used < mark + 2) used = mark; }
            pending = 0;
            continue;
        }
        put_utf8(out, cap, &used, code);
        pending = 1;
    }
    /* No separator after the last value. */
    while (used >= 2 && out[used - 2] == ';' && out[used - 1] == ' ') used -= 2;
    out[used] = 0;
    return used;
}
/* Removes ID3 unsynchronisation (0xFF 0x00 -> 0xFF) in place. */
static size_t unsynchronise(unsigned char *p, size_t n) {
    size_t w = 0;
    for (size_t r = 0; r < n; r++) {
        p[w++] = p[r];
        if (p[r] == 0xff && r + 1 < n && p[r + 1] == 0) r++;
    }
    return w;
}
/* Length of a string terminated for its encoding (one NUL, or two aligned for UTF-16). */
static size_t terminated(int encoding, const unsigned char *p, size_t n, size_t *skip) {
    if (encoding == 1 || encoding == 2) {
        for (size_t i = 0; i + 1 < n; i += 2) if (!p[i] && !p[i + 1]) { *skip = i + 2; return i; }
    } else {
        for (size_t i = 0; i < n; i++) if (!p[i]) { *skip = i + 1; return i; }
    }
    *skip = n; return n;
}
static void set_tag(char *destination, size_t capacity, int encoding, const unsigned char *value, size_t n) {
    char text[512];
    size_t length = id3_text(encoding, value, n, text, sizeof(text));
    while (length && text[length - 1] == ' ') text[--length] = 0;
    copy_tag(destination, capacity, text, length);
}
static int frame_is(const char *id, const char *v23, const char *v22) {
    return !strcmp(id, v23) || (v22 && !strcmp(id, v22));
}
static void id3_frame(int fd, disc_media_info *info, const char *id, long long data, size_t length, int unsync) {
    int text = frame_is(id, "TIT2", "TT2") || frame_is(id, "TPE1", "TP1") || frame_is(id, "TALB", "TAL") ||
               frame_is(id, "TPE2", "TP2") || frame_is(id, "TCON", "TCO") || frame_is(id, "TRCK", "TRK") ||
               frame_is(id, "TPOS", "TPA") || frame_is(id, "TDRC", NULL) || frame_is(id, "TYER", "TYE");
    if (text) {
        if (length < 2 || length > 4096) return;
        unsigned char buffer[4096];
        if (read_at(fd, data, buffer, length)) return;
        if (unsync) length = unsynchronise(buffer, length);
        int encoding = buffer[0];
        if (encoding > 3) return;
        const unsigned char *value = buffer + 1;
        size_t n = length - 1;
        if (frame_is(id, "TIT2", "TT2")) set_tag(info->title, sizeof(info->title), encoding, value, n);
        else if (frame_is(id, "TPE1", "TP1")) set_tag(info->artist, sizeof(info->artist), encoding, value, n);
        else if (frame_is(id, "TALB", "TAL")) set_tag(info->album, sizeof(info->album), encoding, value, n);
        else if (frame_is(id, "TPE2", "TP2")) set_tag(info->album_artist, sizeof(info->album_artist), encoding, value, n);
        else if (frame_is(id, "TRCK", "TRK")) set_tag(info->track, sizeof(info->track), encoding, value, n);
        else if (frame_is(id, "TPOS", "TPA")) set_tag(info->disc, sizeof(info->disc), encoding, value, n);
        else if (frame_is(id, "TDRC", NULL) || frame_is(id, "TYER", "TYE")) set_tag(info->date, sizeof(info->date), encoding, value, n);
        else {
            /* ID3v2.3 genres may lead with a numeric reference: "(17)Rock" keeps "Rock". */
            char genre[256];
            size_t g = id3_text(encoding, value, n, genre, sizeof(genre));
            const char *start = genre;
            if (genre[0] == '(') {
                const char *close = strchr(genre, ')');
                if (close && close[1]) start = close + 1;
            }
            copy_tag(info->genre, sizeof(info->genre), start, g - (size_t)(start - genre));
        }
        return;
    }
    int apic = !strcmp(id, "APIC"), pic = !strcmp(id, "PIC");
    if ((apic || pic) && !unsync) {
        unsigned char head[160];
        size_t want = length < sizeof(head) ? length : sizeof(head);
        if (length < 8 || read_at(fd, data, head, want)) return;
        int encoding = head[0];
        size_t p = 1, skip;
        char mime[40] = "";
        if (pic) {
            if (want < 5) return;
            snprintf(mime, sizeof(mime), "%.3s", (const char *)head + 1);
            p = 4;
        } else {
            size_t m = terminated(0, head + 1, want - 1, &skip);
            if (m >= sizeof(mime) || skip == want - 1) return;
            memcpy(mime, head + 1, m); mime[m] = 0;
            p = 1 + skip;
        }
        if (p >= want || encoding > 3) return;
        unsigned type = head[p++];
        terminated(encoding, head + p, want - p, &skip);
        if (p + skip >= want) return; /* a description longer than the probe window is skipped */
        p += skip;
        const char *normalized = !strcasecmp(mime, "image/jpeg") || !strcasecmp(mime, "image/jpg") || !strcasecmp(mime, "JPG") ? "image/jpeg"
                               : !strcasecmp(mime, "image/png") || !strcasecmp(mime, "PNG") ? "image/png" : NULL;
        size_t size = length - p;
        if (!normalized || !size || size > DISC_MEDIA_MAX_IMAGE) return;
        if (info->picture.present && (info->picture_type == 3 || type != 3)) return;
        info->picture.present = 1;
        info->picture.offset = data + (long long)p;
        info->picture.length = size;
        info->picture_type = type;
        snprintf(info->picture.mime, sizeof(info->picture.mime), "%s", normalized);
        return;
    }
    if ((!strcmp(id, "USLT") || !strcmp(id, "ULT")) && !info->lyrics.present) {
        unsigned char head[132];
        size_t want = length < sizeof(head) ? length : sizeof(head);
        if (length < 5 || unsync || read_at(fd, data, head, want)) return;
        int encoding = head[0];
        size_t skip;
        if (encoding > 3) return;
        terminated(encoding, head + 4, want - 4, &skip);
        size_t start = 4 + skip;
        if (start >= want || length - start > DISC_MEDIA_MAX_LYRICS) return;
        info->lyrics.present = 1;
        info->lyrics.offset = data + (long long)start;
        info->lyrics.length = length - start;
        info->lyrics.encoding = encoding + 1;
        snprintf(info->lyrics.mime, sizeof(info->lyrics.mime), "text/plain");
    }
}
/* Reads a leading ID3v2 tag; returns where the audio starts (0 without a tag). */
static long long id3v2(int fd, disc_media_info *info) {
    unsigned char h[10];
    if (read_at(fd, 0, h, sizeof(h)) || memcmp(h, "ID3", 3) || h[3] < 2 || h[3] > 4 ||
        (h[6] | h[7] | h[8] | h[9]) & 0x80) return 0;
    unsigned major = h[3], flags = h[5];
    long long end = 10 + (long long)syncsafe(h + 6), audio = end + ((flags & 0x10) ? 10 : 0);
    if (end > info->bytes) return 0;
    long long p = 10;
    if (major >= 3 && (flags & 0x40)) {
        unsigned char e[4];
        if (read_at(fd, p, e, 4)) return audio;
        p += major == 4 ? (long long)syncsafe(e) : (long long)be32(e) + 4;
    }
    size_t header = major == 2 ? 6 : 10;
    for (int i = 0; i < 4096 && p + (long long)header <= end; i++) {
        unsigned char f[10];
        if (read_at(fd, p, f, header) || !f[0]) break;
        char id[5] = {0};
        memcpy(id, f, major == 2 ? 3 : 4);
        for (int k = 0; id[k]; k++) if (!((id[k] >= 'A' && id[k] <= 'Z') || (id[k] >= '0' && id[k] <= '9'))) return audio;
        long long length = major == 2 ? be24(f + 3) : major == 4 ? syncsafe(f + 4) : be32(f + 4);
        long long data = p + (long long)header;
        if (!length || data + length > end) break;
        unsigned format = major == 2 ? 0 : f[9];
        int unsync = major == 4 ? (format & 0x02) != 0 : (flags & 0x80) != 0;
        int packed = major == 3 ? (format & 0xc0) != 0 : major == 4 ? (format & 0x0c) != 0 : 0;
        long long body = data, body_length = length;
        if (major == 4 && (format & 0x01)) { body += 4; body_length -= 4; }
        if (!packed && body_length > 0) id3_frame(fd, info, id, body, (size_t)body_length, unsync);
        p = data + length;
    }
    return audio;
}
/* Latin-1 ID3v1 fields fill what ID3v2 left empty. */
static void id3v1(int fd, disc_media_info *info) {
    unsigned char t[128];
    if (info->bytes < 128 || read_at(fd, info->bytes - 128, t, sizeof(t)) || memcmp(t, "TAG", 3)) return;
    const struct { size_t at, n; char *to; size_t cap; } fields[] = {
        {3, 30, info->title, sizeof(info->title)}, {33, 30, info->artist, sizeof(info->artist)},
        {63, 30, info->album, sizeof(info->album)}, {93, 4, info->date, sizeof(info->date)}};
    for (size_t i = 0; i < sizeof(fields) / sizeof(fields[0]); i++) {
        size_t n = fields[i].n;
        while (n && (t[fields[i].at + n - 1] == ' ' || !t[fields[i].at + n - 1])) n--;
        size_t end = 0;
        while (end < n && t[fields[i].at + end]) end++;
        if (end) set_tag(fields[i].to, fields[i].cap, 0, t + fields[i].at, end);
    }
    if (!t[125] && t[126] && !info->track[0]) snprintf(info->track, sizeof(info->track), "%u", t[126]);
}
static const unsigned MPEG_RATE[4][3] = {{11025, 12000, 8000}, {0, 0, 0}, {22050, 24000, 16000}, {44100, 48000, 32000}};
static const unsigned short MPEG_KBPS[2][3][15] = {
    {{0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448},
     {0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384},
     {0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320}},
    {{0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256},
     {0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160},
     {0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160}}};
typedef struct { unsigned version, layer, kbps, rate, channels, samples, length; } mpeg_header;
static int mpeg_frame(const unsigned char *h, mpeg_header *m) {
    if (h[0] != 0xff || (h[1] & 0xe0) != 0xe0) return 0;
    unsigned version = (h[1] >> 3) & 3, layer = (h[1] >> 1) & 3, bitrate = h[2] >> 4, rate = (h[2] >> 2) & 3;
    if (version == 1 || layer == 0 || bitrate == 0 || bitrate == 15 || rate == 3) return 0;
    unsigned mpeg1 = version == 3, l = 3 - layer; /* 0: Layer I, 1: II, 2: III */
    m->version = version; m->layer = l;
    m->kbps = MPEG_KBPS[mpeg1 ? 0 : 1][l][bitrate];
    m->rate = MPEG_RATE[version][rate];
    m->channels = (h[3] >> 6) == 3 ? 1 : 2;
    m->samples = l == 0 ? 384 : l == 1 || mpeg1 ? 1152 : 576;
    unsigned padding = (h[2] >> 1) & 1;
    m->length = l == 0 ? (12 * m->kbps * 1000 / m->rate + padding) * 4 : m->samples / 8 * m->kbps * 1000 / m->rate + padding;
    return m->length >= 24;
}
static void mpeg_audio(int fd, disc_media_info *info, long long start) {
    unsigned char window[4096];
    long long end = info->bytes;
    unsigned char tail[3];
    if (end >= 128 && !read_at(fd, end - 128, tail, 3) && !memcmp(tail, "TAG", 3)) end -= 128;
    /* The first frame whose successor also syncs; padding and junk before it are skipped. */
    for (long long base = start; base < start + 65536 && base + 4 <= end; base += (long long)sizeof(window) - 3) {
        size_t n = (size_t)(end - base < (long long)sizeof(window) ? end - base : (long long)sizeof(window));
        if (read_at(fd, base, window, n)) return;
        for (size_t i = 0; i + 4 <= n; i++) {
            mpeg_header m, next;
            unsigned char h2[4];
            if (!mpeg_frame(window + i, &m)) continue;
            long long frame = base + (long long)i;
            if (frame + m.length + 4 <= end &&
                (read_at(fd, frame + m.length, h2, 4) || !mpeg_frame(h2, &next) || next.rate != m.rate)) continue;
            info->sample_rate = m.rate;
            info->channels = m.channels;
            unsigned side = m.version == 3 ? (m.channels == 1 ? 17 : 32) : (m.channels == 1 ? 9 : 17);
            unsigned char x[16];
            unsigned long long frames = 0, bytes = 0;
            if (!read_at(fd, frame + 4 + side, x, sizeof(x)) && (!memcmp(x, "Xing", 4) || !memcmp(x, "Info", 4))) {
                unsigned flags = be32(x + 4);
                size_t at = 8;
                if (flags & 1) { frames = be32(x + at); at += 4; }
                if (flags & 2) bytes = be32(x + at);
            } else if (!read_at(fd, frame + 36, x, sizeof(x)) && !memcmp(x, "VBRI", 4)) {
                bytes = be32(x + 10); frames = be32(x + 14);
            }
            if (frames) {
                info->has_duration = 1;
                info->duration_ms = frames * m.samples * 1000 / m.rate;
                if (!bytes) bytes = (unsigned long long)(end - frame);
                if (info->duration_ms) info->bit_rate = (unsigned)(bytes * 8 / info->duration_ms);
            } else {
                /* Constant bitrate: the stream's bytes at the first frame's rate. */
                info->bit_rate = m.kbps;
                info->has_duration = 1;
                info->duration_ms = (unsigned long long)(end - frame) * 8 / m.kbps;
            }
            return;
        }
    }
}
static void mp3(int fd, disc_media_info *info) {
    long long audio = id3v2(fd, info);
    id3v1(fd, info);
    mpeg_audio(fd, info, audio);
}

/* ---- ADTS AAC ---------------------------------------------------------- */

static const unsigned AAC_RATE[13] = {96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350};
static void adts(int fd, disc_media_info *info) {
    /* Every frame is counted (an encoder's first frames are far smaller than
     * the rest, so a sample of them misjudges the length); the file is read in
     * windows, one frame header after another. */
    enum { WINDOW = 65536 };
    unsigned char *window = malloc(WINDOW);
    if (!window) return;
    long long start = id3v2(fd, info), p = start, base = -1;
    size_t have = 0;
    unsigned long long frames = 0;
    unsigned rate = 0;
    while (p + 7 <= info->bytes && frames < 4000000) {
        if (base < 0 || p + 7 > base + (long long)have) {
            size_t n = (size_t)(info->bytes - p < WINDOW ? info->bytes - p : WINDOW);
            if (read_at(fd, p, window, n)) break;
            base = p;
            have = n;
        }
        const unsigned char *h = window + (p - base);
        if (h[0] != 0xff || (h[1] & 0xf6) != 0xf0) break;
        unsigned index = (h[2] >> 2) & 15, length = (unsigned)(h[3] & 3) << 11 | (unsigned)h[4] << 3 | h[5] >> 5;
        if (index >= 13 || length < 7) break;
        if (!frames) { rate = AAC_RATE[index]; info->channels = (unsigned)(h[2] & 1) << 2 | h[3] >> 6; }
        frames++;
        p += length;
    }
    free(window);
    if (!frames || !rate) return;
    info->sample_rate = rate;
    info->has_duration = 1;
    info->duration_ms = frames * 1024 * 1000 / rate;
    if (info->duration_ms) info->bit_rate = (unsigned)((unsigned long long)(p - start) * 8 / info->duration_ms);
}

/* ---- MP4 / M4A --------------------------------------------------------- */

typedef struct { long long offset, data, end; char type[5]; } mp4_box;
static int mp4_next(int fd, long long p, long long limit, mp4_box *box) {
    unsigned char h[16];
    if (p + 8 > limit || read_at(fd, p, h, 8)) return 0;
    unsigned long long size = be32(h);
    memcpy(box->type, h + 4, 4); box->type[4] = 0;
    box->offset = p; box->data = p + 8;
    if (size == 1) {
        if (p + 16 > limit || read_at(fd, p + 8, h + 8, 8)) return 0;
        size = (unsigned long long)be32(h + 8) << 32 | be32(h + 12);
        box->data = p + 16;
    } else if (size == 0) size = (unsigned long long)(limit - p);
    if (size < (unsigned long long)(box->data - p) || (long long)size > limit - p) return 0;
    box->end = p + (long long)size;
    return 1;
}
static void mp4_item(int fd, disc_media_info *info, const mp4_box *item) {
    mp4_box data;
    if (!mp4_next(fd, item->data, item->end, &data) || strcmp(data.type, "data") || data.end - data.data < 8) return;
    unsigned char h[8];
    if (read_at(fd, data.data, h, 8)) return;
    unsigned kind = be32(h) & 0xffffff;
    long long value = data.data + 8;
    size_t n = (size_t)(data.end - value);
    const char *t = item->type;
    if (!strcmp(t, "covr")) {
        const char *mime = kind == 13 ? "image/jpeg" : kind == 14 ? "image/png" : NULL;
        if (!mime || !n || n > DISC_MEDIA_MAX_IMAGE || info->picture.present) return;
        info->picture.present = 1; info->picture.offset = value; info->picture.length = n;
        info->picture_type = 3;
        snprintf(info->picture.mime, sizeof(info->picture.mime), "%s", mime);
        return;
    }
    if (!strcmp(t, "\xa9lyr")) {
        if (kind != 1 || !n || n > DISC_MEDIA_MAX_LYRICS || info->lyrics.present) return;
        info->lyrics.present = 1; info->lyrics.offset = value; info->lyrics.length = n;
        snprintf(info->lyrics.mime, sizeof(info->lyrics.mime), "text/plain");
        return;
    }
    if (!strcmp(t, "trkn") || !strcmp(t, "disk")) {
        unsigned char pair[6];
        if (n < 6 || read_at(fd, value, pair, sizeof(pair))) return;
        unsigned number = (unsigned)pair[2] << 8 | pair[3], total = (unsigned)pair[4] << 8 | pair[5];
        char *to = t[0] == 't' ? info->track : info->disc;
        if (number && !to[0]) {
            if (total) snprintf(to, 16, "%u/%u", number, total);
            else snprintf(to, 16, "%u", number);
        }
        return;
    }
    char *to = !strcmp(t, "\xa9nam") ? info->title : !strcmp(t, "\xa9" "ART") ? info->artist : !strcmp(t, "\xa9" "alb") ? info->album
             : !strcmp(t, "aART") ? info->album_artist : !strcmp(t, "\xa9gen") ? info->genre : !strcmp(t, "\xa9" "day") ? info->date : NULL;
    size_t cap = to == info->genre ? sizeof(info->genre) : to == info->date ? sizeof(info->date) : 256;
    if (!to || kind != 1 || !n || n >= cap) return;
    char text[256];
    if (read_at(fd, value, text, n)) return;
    copy_tag(to, cap, text, n);
}
static void mp4_walk(int fd, disc_media_info *info, long long p, long long end, int depth) {
    mp4_box box;
    for (int i = 0; i < 512 && mp4_next(fd, p, end, &box); i++, p = box.end) {
        const char *t = box.type;
        if (!strcmp(t, "moov") || !strcmp(t, "trak") || !strcmp(t, "mdia") || !strcmp(t, "minf") ||
            !strcmp(t, "stbl") || !strcmp(t, "udta")) {
            if (depth < 8) mp4_walk(fd, info, box.data, box.end, depth + 1);
        } else if (!strcmp(t, "meta")) {
            if (depth < 8) mp4_walk(fd, info, box.data + 4, box.end, depth + 1);
        } else if (!strcmp(t, "ilst")) {
            mp4_box item;
            for (long long q = box.data; mp4_next(fd, q, box.end, &item); q = item.end) mp4_item(fd, info, &item);
        } else if (!strcmp(t, "mvhd")) {
            unsigned char h[32];
            if (box.end - box.data < 32 || read_at(fd, box.data, h, sizeof(h))) continue;
            unsigned long long scale = h[0] == 1 ? be32(h + 20) : be32(h + 12);
            unsigned long long length = h[0] == 1 ? (unsigned long long)be32(h + 24) << 32 | be32(h + 28) : be32(h + 16);
            if (scale && length) { info->has_duration = 1; info->duration_ms = length * 1000 / scale; }
        } else if (!strcmp(t, "stsd")) {
            mp4_box entry;
            if (!mp4_next(fd, box.data + 8, box.end, &entry)) continue;
            unsigned char a[28];
            if (entry.end - entry.data < 28 || read_at(fd, entry.data, a, sizeof(a))) continue;
            if (!strcmp(entry.type, "mp4a") || !strcmp(entry.type, "alac")) {
                info->channels = (unsigned)a[16] << 8 | a[17];
                info->sample_rate = (unsigned)a[24] << 8 | a[25];
                if (!strcmp(entry.type, "alac")) info->bits = (unsigned)a[18] << 8 | a[19];
                mp4_box child;
                for (long long q = entry.data + 28; mp4_next(fd, q, entry.end, &child); q = child.end) {
                    unsigned char c[64];
                    size_t n = (size_t)(child.end - child.data < 64 ? child.end - child.data : 64);
                    if (read_at(fd, child.data, c, n)) break;
                    if (!strcmp(child.type, "alac") && n >= 28) {
                        info->bits = c[9];
                        info->sample_rate = be32(c + 24);
                    } else if (!strcmp(child.type, "esds")) {
                        /* DecoderConfigDescriptor (tag 4): type, stream, buffer size, max and average bitrate. */
                        for (size_t k = 4; k + 13 < n; k++) if (c[k] == 4) {
                            size_t at = k + 1;
                            while (at < n && (c[at] & 0x80)) at++;
                            at++;
                            if (at + 13 <= n) { unsigned average = be32(c + at + 9); if (average) info->bit_rate = average / 1000; }
                            break;
                        }
                    }
                }
            }
        }
    }
}
static int mp4(int fd, disc_media_info *info) {
    unsigned char h[8];
    if (read_at(fd, 0, h, sizeof(h)) || memcmp(h + 4, "ftyp", 4)) return 0;
    mp4_walk(fd, info, 0, info->bytes, 0);
    if (!info->bit_rate && info->has_duration && info->duration_ms && !info->bits)
        info->bit_rate = (unsigned)((unsigned long long)info->bytes * 8 / info->duration_ms);
    return 1;
}

char *disc_media_lyrics_utf8(const disc_media_blob *lyrics, const unsigned char *raw, size_t length, size_t *out_length) {
    if (!lyrics->encoding) return NULL;
    size_t capacity = length * 3 + 4;
    unsigned char *copy = malloc(length ? length : 1);
    char *out = malloc(capacity);
    if (!copy || !out) { free(copy); free(out); return NULL; }
    memcpy(copy, raw, length);
    size_t n = lyrics->unsync ? unsynchronise(copy, length) : length;
    /* Lyrics keep their line breaks: NUL is only a terminator here. */
    size_t used = 0, i = 0;
    int encoding = lyrics->encoding - 1;
    while (i < n) {
        size_t skip;
        size_t part = terminated(encoding, copy + i, n - i, &skip);
        used += id3_text(encoding, copy + i, part, out + used, capacity - used);
        i += skip;
        if (part == skip) break;
    }
    free(copy);
    *out_length = used;
    return out;
}

int disc_media_audio_name(const char *name) { return audio_name(name); }

static void probe(int fd, const char *name, disc_media_info *info, int quick);
void disc_media_probe(int fd, const char *name, disc_media_info *info) { probe(fd, name, info, 0); }
void disc_media_probe_quick(int fd, const char *name, disc_media_info *info) { probe(fd, name, info, 1); }

static void probe(int fd, const char *name, disc_media_info *info, int quick) {
    memset(info, 0, sizeof(*info));
    struct stat st;
    if (fstat(fd, &st)) return;
    info->bytes = (long long)st.st_size;
    const char *ext = extension(name);
    size_t n = 0;
    for (; ext && ext[n] && n + 1 < sizeof(info->format); n++)
        info->format[n] = (char)((ext[n] >= 'A' && ext[n] <= 'Z') ? ext[n] + 32 : ext[n]);
    info->format[n] = 0;
    /* MP4 is recognised by content (M4A, ALAC or AAC files named .aac or .mp4). */
    if (!strcmp(info->format, "flac")) flac(fd, info);
    else if (!strcmp(info->format, "wav")) wav(fd, info);
    else if (mp4(fd, info)) return;
    else if (!strcmp(info->format, "mp3")) mp3(fd, info);
    /* Counting ADTS frames reads the whole file; a quick probe leaves its duration unknown. */
    else if (!strcmp(info->format, "aac") && !quick) adts(fd, info);
}

int disc_media_folder_cover(int dir_fd, int *fd, disc_media_blob *blob) {
    for (size_t i = 0; COVERS[i]; i++) {
        int f = openat(dir_fd, COVERS[i], O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
        if (f < 0) continue;
        struct stat st;
        unsigned char magic[4];
        if (fstat(f, &st) || !S_ISREG(st.st_mode) || st.st_size < 8 || st.st_size > (off_t)DISC_MEDIA_MAX_IMAGE ||
            read_at(f, 0, magic, sizeof(magic))) { close(f); continue; }
        const char *mime = magic[0] == 0xff && magic[1] == 0xd8 && magic[2] == 0xff ? "image/jpeg"
                         : !memcmp(magic, "\x89PNG", 4) ? "image/png" : NULL;
        if (!mime) { close(f); continue; }
        blob->present = 1;
        blob->offset = 0;
        blob->length = (size_t)st.st_size;
        snprintf(blob->mime, sizeof(blob->mime), "%s", mime);
        *fd = f;
        return 1;
    }
    return 0;
}

int disc_media_sidecar_lyrics(int dir_fd, const char *name, int *fd, size_t *size) {
    const char *dot = strrchr(name, '.');
    size_t stem = dot ? (size_t)(dot - name) : strlen(name);
    const char *const suffixes[] = {".lrc", ".LRC", ".Lrc"};
    for (size_t i = 0; i < sizeof(suffixes) / sizeof(suffixes[0]); i++) {
        char sidecar[270];
        if (stem + 5 > sizeof(sidecar)) return 0;
        memcpy(sidecar, name, stem);
        memcpy(sidecar + stem, suffixes[i], 5);
        int f = openat(dir_fd, sidecar, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
        if (f < 0) continue;
        struct stat st;
        if (fstat(f, &st) || !S_ISREG(st.st_mode) || !st.st_size || st.st_size > (off_t)DISC_MEDIA_MAX_LYRICS) {
            close(f);
            continue;
        }
        *fd = f;
        *size = (size_t)st.st_size;
        return 1;
    }
    return 0;
}

typedef struct { char *out; size_t capacity, used; int overflow; } writer;

static void put(writer *w, const char *text, size_t length) {
    if (w->overflow || length >= w->capacity - w->used) { w->overflow = 1; return; }
    memcpy(w->out + w->used, text, length);
    w->used += length;
    w->out[w->used] = 0;
}
static void puts_(writer *w, const char *text) { put(w, text, strlen(text)); }
static void string(writer *w, const char *text) {
    put(w, "\"", 1);
    for (const char *p = text; *p; p++) {
        if (*p == '"' || *p == '\\') put(w, "\\", 1);
        put(w, p, 1);
    }
    put(w, "\"", 1);
}
static void number(writer *w, int present, unsigned long long value) {
    char buffer[32];
    if (!present) { puts_(w, "null"); return; }
    int n = snprintf(buffer, sizeof(buffer), "%llu", value);
    put(w, buffer, (size_t)n);
}
static void tag(writer *w, const char *name, const char *value, int first) {
    if (!first) puts_(w, ",");
    string(w, name);
    puts_(w, ":");
    if (value[0]) string(w, value);
    else puts_(w, "null");
}

size_t disc_media_json(const disc_media_info *info, const char *path, int folder_cover, int sidecar, char *out, size_t capacity) {
    if (!capacity) return 0;
    writer w = {out, capacity, 0, 0};
    out[0] = 0;
    puts_(&w, "{\"path\":");
    string(&w, path);
    puts_(&w, ",\"format\":");
    string(&w, info->format);
    puts_(&w, ",\"bytes\":");
    number(&w, 1, (unsigned long long)info->bytes);
    puts_(&w, ",\"durationMs\":");
    number(&w, info->has_duration, info->duration_ms);
    puts_(&w, ",\"sampleRate\":");
    number(&w, info->sample_rate != 0, info->sample_rate);
    puts_(&w, ",\"bitDepth\":");
    number(&w, info->bits != 0, info->bits);
    puts_(&w, ",\"channels\":");
    number(&w, info->channels != 0, info->channels);
    puts_(&w, ",\"bitRate\":");
    number(&w, info->bit_rate != 0, info->bit_rate);
    puts_(&w, ",\"tags\":{");
    tag(&w, "title", info->title, 1);
    tag(&w, "artist", info->artist, 0);
    tag(&w, "album", info->album, 0);
    tag(&w, "albumArtist", info->album_artist, 0);
    tag(&w, "genre", info->genre, 0);
    tag(&w, "track", info->track, 0);
    tag(&w, "disc", info->disc, 0);
    tag(&w, "date", info->date, 0);
    puts_(&w, "},\"cover\":");
    puts_(&w, info->picture.present ? "\"embedded\"" : folder_cover ? "\"folder\"" : "null");
    puts_(&w, ",\"lyrics\":");
    puts_(&w, sidecar ? "\"sidecar\"" : info->lyrics.present ? "\"embedded\"" : "null");
    puts_(&w, "}");
    return w.overflow ? 0 : w.used;
}

int disc_media_encode_path(const char *path, char *out, size_t capacity) {
    static const char hex[] = "0123456789ABCDEF";
    size_t used = 0;
    for (const unsigned char *p = (const unsigned char *)path; *p; p++) {
        int keep = (*p >= 'A' && *p <= 'Z') || (*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9') ||
                   *p == '-' || *p == '.' || *p == '_' || *p == '~' || *p == '/';
        if (used + (keep ? 1 : 3) >= capacity) return -1;
        if (keep) out[used++] = (char)*p;
        else {
            out[used++] = '%';
            out[used++] = hex[*p >> 4];
            out[used++] = hex[*p & 15];
        }
    }
    out[used] = 0;
    return 0;
}
