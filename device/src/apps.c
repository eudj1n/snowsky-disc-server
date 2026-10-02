/* Installing and removing apps on the card through the application manager. */
#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#include "apps.h"
#define MINIZ_NO_STDIO
#define MINIZ_NO_TIME
#define MINIZ_NO_ARCHIVE_APIS
#define MINIZ_NO_ARCHIVE_WRITING_APIS
#define MINIZ_NO_ZLIB_APIS
#define MINIZ_NO_ZLIB_COMPATIBLE_NAMES
#define MINIZ_NO_DEFLATE_APIS
#include "miniz.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <unistd.h>

static const char *const ALLOWED[] = {".html", ".css", ".js", ".mjs", ".json", ".map", ".webmanifest", ".txt", ".svg", ".png",
                                      ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff2", ".woff", ".ttf", ".wasm"};
static const char *const COMPRESSIBLE[] = {".html", ".css", ".js", ".mjs", ".json", ".svg", ".wasm", ".webmanifest", ".txt", ".map"};
static const char *const CATALOGS[] = {"compatibility.json", "commands.json", "queries.json", "store.json", "hosted.json"};
#define ENTRIES_MAX (DISC_APP_MAX_FILES * 2 + 64)
#define PATH_MAX_APP 512

typedef struct {
    char path[PATH_MAX_APP];   /* inside the app's folder */
    uint32_t crc, compressed, size, offset;
    uint16_t method;
    int twin;                  /* a gzip twin of a text file beside it */
} entry;

static int refuse(char *problem, size_t capacity, int code, const char *format, ...) {
    if (problem && capacity) {
        va_list ap; va_start(ap, format);
        vsnprintf(problem, capacity, format, ap);
        va_end(ap);
    }
    return code;
}

static uint16_t le16(const unsigned char *p) { return (uint16_t)(p[0] | p[1] << 8); }
static uint32_t le32(const unsigned char *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }

static int ends_with(const char *s, const char *suffix) {
    size_t n = strlen(s), m = strlen(suffix);
    return n >= m && !strcasecmp(s + n - m, suffix);
}
static int listed(const char *name, const char *const *list, size_t count) {
    for (size_t i = 0; i < count; i++) if (ends_with(name, list[i])) return 1;
    return 0;
}
static int component_ok(const char *c, size_t n) {
    if (n < 1 || n > 80) return 0;
    for (size_t i = 0; i < n; i++) {
        char x = c[i];
        int ok = (x >= 'A' && x <= 'Z') || (x >= 'a' && x <= 'z') || (x >= '0' && x <= '9') || x == '_' || x == '-' || (x == '.' && i);
        if (!ok) return 0;
    }
    return 1;
}

static int pread_all(int fd, void *buf, size_t n, off_t at) {
    size_t got = 0;
    while (got < n) {
        ssize_t r = pread(fd, (char *)buf + got, n - got, at + (off_t)got);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) return -1;
        got += (size_t)r;
    }
    return 0;
}

static int write_all(int fd, const void *data, size_t n) {
    const char *p = data;
    while (n) {
        ssize_t w = write(fd, p, n);
        if (w < 0 && errno == EINTR) continue;
        if (w <= 0) return -1;
        p += w; n -= (size_t)w;
    }
    return 0;
}

static int remove_tree(const char *path, int depth) {
    struct stat st;
    if (lstat(path, &st)) return errno == ENOENT ? 0 : -1;
    if (!S_ISDIR(st.st_mode)) return unlink(path);
    if (depth > 12) return -1;
    DIR *d = opendir(path);
    if (!d) return -1;
    struct dirent *e;
    int r = 0;
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        char child[PATH_MAX];
        if (snprintf(child, sizeof(child), "%s/%s", path, e->d_name) >= (int)sizeof(child) || remove_tree(child, depth + 1)) r = -1;
    }
    closedir(d);
    return r || rmdir(path) ? -1 : 0;
}

static int make_parents(const char *root, const char *relative) {
    char path[PATH_MAX];
    if (snprintf(path, sizeof(path), "%s/%s", root, relative) >= (int)sizeof(path)) return -1;
    char *slash = strrchr(path, '/');
    if (!slash) return -1;
    *slash = 0;
    for (char *p = path + strlen(root) + 1; *p; p++) {
        if (*p != '/') continue;
        *p = 0;
        if (mkdir(path, 0755) && errno != EEXIST) return -1;
        *p = '/';
    }
    return mkdir(path, 0755) && errno != EEXIST ? -1 : 0;
}

/* The app's folder and its entries from the zip's central directory. */
static int read_directory(int fd, off_t size, entry **entries, int *count, char name[DISC_APP_NAME_MAX + 1], char *problem, size_t capacity) {
    /* The end of the central directory with the longest comment; on the heap (a worker's stack keeps its pages). */
    enum { TAIL = 65557 };
    unsigned char *tail = malloc(TAIL);
    size_t span = size < (off_t)TAIL ? (size_t)size : TAIL;
    if (!tail) return refuse(problem, capacity, DISC_APP_FAILED, "Out of memory");
    if (size < 22 || pread_all(fd, tail, span, size - (off_t)span)) { free(tail); return refuse(problem, capacity, DISC_APP_REFUSED, "Not a zip archive"); }
    long eocd = -1;
    for (long i = (long)span - 22; i >= 0; i--)
        if (le32(tail + i) == 0x06054b50) { eocd = i; break; }
    if (eocd < 0) { free(tail); return refuse(problem, capacity, DISC_APP_REFUSED, "Not a zip archive"); }
    const unsigned char *e = tail + eocd;
    uint16_t disk = le16(e + 4), start = le16(e + 6), here = le16(e + 8), total = le16(e + 10);
    uint32_t cd_size = le32(e + 12), cd_offset = le32(e + 16);
    free(tail);
    if (disk || start || here != total || total == 0xffff || cd_size == 0xffffffffu || cd_offset == 0xffffffffu)
        return refuse(problem, capacity, DISC_APP_REFUSED, "Split or zip64 archives are not supported");
    if (total > ENTRIES_MAX || (off_t)cd_offset + cd_size > size || cd_size > 4u * 1024 * 1024)
        return refuse(problem, capacity, DISC_APP_REFUSED, "The archive lists too many entries");
    unsigned char *cd = malloc(cd_size ? cd_size : 1);
    entry *list = calloc(total ? total : 1, sizeof(entry));
    if (!cd || !list || pread_all(fd, cd, cd_size, cd_offset)) { free(cd); free(list); return refuse(problem, capacity, DISC_APP_FAILED, "The archive could not be read"); }
    int n = 0, code = DISC_APP_OK;
    name[0] = 0;
    size_t at = 0;
    for (int i = 0; i < total; i++) {
        if (at + 46 > cd_size || le32(cd + at) != 0x02014b50) { code = refuse(problem, capacity, DISC_APP_REFUSED, "The archive's directory is damaged"); break; }
        const unsigned char *h = cd + at;
        uint16_t made = le16(h + 4), flags = le16(h + 8), method = le16(h + 10), name_len = le16(h + 28), extra = le16(h + 30), comment = le16(h + 32);
        uint32_t attrs = le32(h + 38);
        if (at + 46 + name_len + extra + comment > cd_size) { code = refuse(problem, capacity, DISC_APP_REFUSED, "The archive's directory is damaged"); break; }
        char raw[PATH_MAX_APP];
        if (!name_len || name_len >= sizeof(raw)) { code = refuse(problem, capacity, DISC_APP_REFUSED, "An entry's name is too long"); break; }
        memcpy(raw, h + 46, name_len); raw[name_len] = 0;
        at += 46u + name_len + extra + comment;
        if (memchr(raw, 0, name_len) || strchr(raw, '\\') || raw[0] == '/') { code = refuse(problem, capacity, DISC_APP_REFUSED, "Unexpected entry in the zip: %s", raw); break; }
        int utf8 = (flags >> 11) & 1, ascii = 1;
        for (size_t k = 0; k < name_len; k++) if ((unsigned char)raw[k] >= 0x80) ascii = 0;
        if (!ascii && !utf8) { code = refuse(problem, capacity, DISC_APP_REFUSED, "An entry's name is not UTF-8"); break; }
        /* Components: none empty, "." or ".."; a trailing slash is a folder. */
        int folder = raw[name_len - 1] == '/';
        if (folder) raw[--name_len] = 0;
        char *slash = strchr(raw, '/');
        size_t top = slash ? (size_t)(slash - raw) : strlen(raw);
        int bad = !top;
        for (char *p = raw; !bad && *p; ) {
            char *q = strchr(p, '/');
            size_t len = q ? (size_t)(q - p) : strlen(p);
            if (!len || (len == 1 && p[0] == '.') || (len == 2 && p[0] == '.' && p[1] == '.')) bad = 1;
            p = q ? q + 1 : p + len;
        }
        if (bad) { code = refuse(problem, capacity, DISC_APP_REFUSED, "Unexpected entry in the zip: %s", raw); break; }
        if (top == 8 && !strncmp(raw, "__MACOSX", 8)) continue;  /* macOS leftovers */
        char top_name[PATH_MAX_APP];
        memcpy(top_name, raw, top); top_name[top] = 0;
        /* A file outside the app's folder has no place. */
        if (!slash && !folder) { code = refuse(problem, capacity, DISC_APP_REFUSED, "Unexpected entry in the zip: %s", raw); break; }
        if (top > DISC_APP_NAME_MAX || !disc_app_name_ok(top_name)) { code = refuse(problem, capacity, DISC_APP_REFUSED, "Not an app name: %s", top_name); break; }
        if (!name[0]) memcpy(name, top_name, top + 1);
        else if (strcmp(name, top_name)) { code = refuse(problem, capacity, DISC_APP_REFUSED, "The zip must hold exactly one app folder"); break; }
        if (folder || !slash) continue;
        const char *relative = slash + 1;
        /* Hidden names (macOS leftovers among them) are never installed. */
        int hidden = relative[0] == '.' || strstr(relative, "/.") != NULL;
        if (hidden) continue;
        if ((flags & 1) || (method != 0 && method != 8)) { code = refuse(problem, capacity, DISC_APP_REFUSED, "%s: encrypted or an unsupported compression", relative); break; }
        int kind = made >> 8 == 3 ? (int)((attrs >> 16) & 0170000) : 0;
        if (kind && kind != 0100000) { code = refuse(problem, capacity, DISC_APP_REFUSED, "Unsupported file in the app: %s", relative); break; }
        if (n >= DISC_APP_MAX_FILES) { code = refuse(problem, capacity, DISC_APP_REFUSED, "The app exceeds the file count or total size limit"); break; }
        entry *x = &list[n++];
        snprintf(x->path, sizeof(x->path), "%s", relative);
        x->method = method; x->crc = le32(h + 16); x->compressed = le32(h + 20); x->size = le32(h + 24); x->offset = le32(h + 42);
    }
    free(cd);
    if (code == DISC_APP_OK && !name[0]) code = refuse(problem, capacity, DISC_APP_REFUSED, "The zip must hold exactly one app folder");
    if (code != DISC_APP_OK) { free(list); return code; }
    *entries = list; *count = n;
    return DISC_APP_OK;
}

/* The app_bundle.py rules for one file's name and size. */
static int check_name(const entry *list, int count, entry *x, char *problem, size_t capacity) {
    const char *p = x->path;
    int depth = 0;
    for (const char *c = p; *c; ) {
        const char *q = strchr(c, '/');
        size_t len = q ? (size_t)(q - c) : strlen(c);
        if (!component_ok(c, len) || ++depth > DISC_APP_MAX_DEPTH) return refuse(problem, capacity, DISC_APP_REFUSED, "Unsupported file in the app: %s", p);
        c = q ? q + 1 : c + len;
    }
    /* A pre-compressed twin of a text file beside it; the gateway serves it for that file only
     * (its gzip header is checked once it is unpacked). */
    if (ends_with(p, ".gz")) {
        char original[PATH_MAX_APP];
        snprintf(original, sizeof(original), "%.*s", (int)(strlen(p) - 3), p);
        for (int i = 0; i < count; i++)
            if (!strcmp(list[i].path, original)) x->twin = listed(original, COMPRESSIBLE, sizeof(COMPRESSIBLE) / sizeof(*COMPRESSIBLE));
        if (x->twin && x->size > DISC_APP_MAX_FILE) return refuse(problem, capacity, DISC_APP_REFUSED, "Not a gzip twin: %s", p);
    }
    if (!x->twin) {
        for (size_t i = 0; i < sizeof(CATALOGS) / sizeof(*CATALOGS); i++)
            if (!strcmp(p, CATALOGS[i])) return refuse(problem, capacity, DISC_APP_REFUSED, "%s: the reviewed catalogs come from the service, not from an app", p);
        if (!listed(p, ALLOWED, sizeof(ALLOWED) / sizeof(*ALLOWED)) || x->size > DISC_APP_MAX_FILE)
            return refuse(problem, capacity, DISC_APP_REFUSED, "Unsupported file in the app: %s", p);
    }
    for (int i = 0; i < count && &list[i] != x; i++)
        if (!strcasecmp(list[i].path, p)) return refuse(problem, capacity, DISC_APP_REFUSED, "Case-insensitive name collision on a FAT card: %s", p);
    return DISC_APP_OK;
}

/* One entry, stored or deflated, into its file; its size and CRC-32 must match. */
static int unpack(int zip, off_t zip_size, const entry *x, const char *target, char *problem, size_t capacity) {
    unsigned char local[30];
    if ((off_t)x->offset + 30 > zip_size || pread_all(zip, local, 30, x->offset) || le32(local) != 0x04034b50)
        return refuse(problem, capacity, DISC_APP_REFUSED, "%s: damaged entry", x->path);
    off_t data = (off_t)x->offset + 30 + le16(local + 26) + le16(local + 28);
    if (data + (off_t)x->compressed > zip_size || (x->method == 0 && x->compressed != x->size))
        return refuse(problem, capacity, DISC_APP_REFUSED, "%s: damaged entry", x->path);
    int out = open(target, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0644);
    if (out < 0) return refuse(problem, capacity, DISC_APP_FAILED, "%s could not be written", x->path);
    enum { CHUNK = 16384 };
    unsigned char *in = malloc(CHUNK), *window = x->method == 8 ? malloc(TINFL_LZ_DICT_SIZE) : NULL;
    tinfl_decompressor *inflator = x->method == 8 ? malloc(sizeof(tinfl_decompressor)) : NULL;
    int code = DISC_APP_OK;
    mz_ulong crc = MZ_CRC32_INIT;
    uint64_t written = 0;
    if (!in || (x->method == 8 && (!window || !inflator))) code = refuse(problem, capacity, DISC_APP_FAILED, "Out of memory");
    else if (x->method == 0) {
        for (uint32_t done = 0; done < x->size && code == DISC_APP_OK; ) {
            size_t n = x->size - done < CHUNK ? x->size - done : CHUNK;
            if (pread_all(zip, in, n, data + done) || write_all(out, in, n)) code = refuse(problem, capacity, DISC_APP_FAILED, "%s could not be copied", x->path);
            crc = mz_crc32(crc, in, n); done += (uint32_t)n; written += n;
        }
    } else {
        tinfl_init(inflator);
        uint32_t left = x->compressed;
        size_t avail = 0, pos = 0, out_free = TINFL_LZ_DICT_SIZE;
        unsigned char *next = window;
        for (;;) {
            if (!avail && left) {
                size_t n = left < CHUNK ? left : CHUNK;
                if (pread_all(zip, in, n, data + (x->compressed - left))) { code = refuse(problem, capacity, DISC_APP_FAILED, "%s could not be read", x->path); break; }
                avail = n; pos = 0; left -= (uint32_t)n;
            }
            size_t in_bytes = avail, out_bytes = out_free;
            tinfl_status status = tinfl_decompress(inflator, in + pos, &in_bytes, window, next, &out_bytes, left ? TINFL_FLAG_HAS_MORE_INPUT : 0);
            avail -= in_bytes; pos += in_bytes; out_free -= out_bytes; next += out_bytes;
            if (status <= TINFL_STATUS_DONE || !out_free) {
                size_t produced = (size_t)(next - window);
                written += produced;
                if (written > x->size) { code = refuse(problem, capacity, DISC_APP_REFUSED, "%s is larger than its record", x->path); break; }
                if (produced && write_all(out, window, produced)) { code = refuse(problem, capacity, DISC_APP_FAILED, "%s could not be written", x->path); break; }
                crc = mz_crc32(crc, window, produced);
                next = window; out_free = TINFL_LZ_DICT_SIZE;
            }
            if (status == TINFL_STATUS_DONE) break;
            if (status < TINFL_STATUS_DONE || (status == TINFL_STATUS_NEEDS_MORE_INPUT && !avail && !left)) {
                code = refuse(problem, capacity, DISC_APP_REFUSED, "%s: damaged compressed data", x->path); break;
            }
        }
    }
    if (code == DISC_APP_OK && (written != x->size || crc != x->crc)) code = refuse(problem, capacity, DISC_APP_REFUSED, "%s does not match its size or CRC", x->path);
    if (code == DISC_APP_OK && fsync(out)) code = refuse(problem, capacity, DISC_APP_FAILED, "%s could not be synced", x->path);
    close(out);
    free(in); free(window); free(inflator);
    return code;
}

/* app_bundle.py's checks on what an app holds: the gateway serves apps with script-src and style-src
 * 'self', and at / and at /apps/<App>/, so a root-absolute reference resolves at one only. */
static int word(unsigned char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_'; }
static int space(unsigned char c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v'; }
static int starts(const unsigned char *p, const unsigned char *end, const char *word_ci) {
    size_t n = strlen(word_ci);
    return (size_t)(end - p) >= n && !strncasecmp((const char *)p, word_ci, n);
}
static const unsigned char *skip_space(const unsigned char *p, const unsigned char *end) { while (p < end && space(*p)) p++; return p; }
/* <tag ...> not carrying src= (for script) followed by something other than space. */
static int inline_block(const unsigned char *d, const unsigned char *end, const char *tag, int needs_no_src) {
    size_t n = strlen(tag);
    for (const unsigned char *p = d; p < end; p++) {
        if (*p != '<' || !starts(p + 1, end, tag) || (p + 1 + n < end && word(p[1 + n]))) continue;
        const unsigned char *close = memchr(p, '>', (size_t)(end - p));
        if (!close) return 0;
        int has_src = 0;
        for (const unsigned char *q = p + 1 + n; needs_no_src && q < close; q++)
            if ((q == p + 1 + n || !word(q[-1])) && starts(q, close, "src=")) has_src = 1;
        if (has_src) continue;
        const unsigned char *after = skip_space(close + 1, end);
        if (after < end) return 1;
    }
    return 0;
}
/* \b<name>\s*=\s*["'] then, when slash is set, "/" not followed by "api/" or "/". */
static int attribute(const unsigned char *d, const unsigned char *end, const char *name, int need_space_before, int on_prefix, int slash) {
    for (const unsigned char *p = d; p < end; p++) {
        const unsigned char *q;
        if (on_prefix) {
            if (!space(*p) || !starts(p + 1, end, "on")) continue;
            q = p + 3;
            const unsigned char *letters = q;
            while (q < end && ((*q >= 'a' && *q <= 'z') || (*q >= 'A' && *q <= 'Z'))) q++;
            if (q == letters) continue;
        } else {
            if (!starts(p, end, name) || (p > d && word(p[-1])) || (need_space_before && (p == d || !space(p[-1])))) continue;
            q = p + strlen(name);
        }
        q = skip_space(q, end);
        if (q >= end || *q != '=') continue;
        q = skip_space(q + 1, end);
        if (q >= end || (*q != '"' && *q != '\'')) continue;
        if (!slash) return 1;
        q++;
        if (q < end && *q == '/' && !starts(q + 1, end, "api/") && !(q + 1 < end && q[1] == '/')) return 1;
    }
    return 0;
}
static const char *const ROOT_TYPES[] = {"js", "mjs", "css", "json", "svg", "png", "jpg", "jpeg", "gif", "webp", "ico", "woff2", "woff", "ttf", "wasm", "webmanifest"};
static int path_char(unsigned char c) { return word(c) || c == '.' || c == '-'; }
static int root_asset(const unsigned char *d, const unsigned char *end, char *found, size_t capacity) {
    for (const unsigned char *p = d; p + 1 < end; p++) {
        if ((*p != '"' && *p != '\'' && *p != '(') || p[1] != '/') continue;
        const unsigned char *s = p + 2;
        /* This pattern, unlike the HTML ones, is case-sensitive: "/API/x.js" is an asset. */
        if ((end - s >= 4 && !memcmp(s, "api/", 4)) || (s < end && *s == '/')) continue;
        /* (?:seg/)*seg\.ext\b: segments of [A-Za-z0-9_.-]+ split by "/", a dot and a known type ending at a word boundary.
         * Any segment may be the last one, and any of its dots may start the type, as the regex backtracks; the
         * rightmost such dot is the one Python reports. */
        const unsigned char *q = s, *segment = s;
        while (q < end && (path_char(*q) || *q == '/')) {
            if (*q == '/') { if (q == segment) break; segment = q + 1; }
            q++;
        }
        for (const unsigned char *dot = q - 1; dot > s; dot--) {
            if (*dot != '.' || dot[-1] == '/') continue;
            for (size_t t = 0; t < sizeof(ROOT_TYPES) / sizeof(*ROOT_TYPES); t++) {
                size_t n = strlen(ROOT_TYPES[t]);
                if ((size_t)(end - dot - 1) >= n && !strncmp((const char *)dot + 1, ROOT_TYPES[t], n) && (dot + 1 + n >= end || !word(dot[1 + n]))) {
                    snprintf(found, capacity, "%.*s", (int)(dot + 1 + n - p - 1), (const char *)p + 1);
                    return 1;
                }
            }
        }
    }
    return 0;
}
static int lint(const char *root, const entry *x, char *problem, size_t capacity) {
    int html = ends_with(x->path, ".html"), code = ends_with(x->path, ".js") || ends_with(x->path, ".mjs") || ends_with(x->path, ".css");
    if (!html && !code && !x->twin) return DISC_APP_OK;
    char path[PATH_MAX];
    if (snprintf(path, sizeof(path), "%s/%s", root, x->path) >= (int)sizeof(path)) return refuse(problem, capacity, DISC_APP_FAILED, "Path too long");
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    size_t want = x->twin && x->size > 2 ? 2 : x->size;
    unsigned char *d = malloc(want ? want : 1);
    int r = DISC_APP_OK;
    if (fd < 0 || !d || pread_all(fd, d, want, 0)) r = refuse(problem, capacity, DISC_APP_FAILED, "%s could not be checked", x->path);
    else if (x->twin) {
        if (want < 2 || d[0] != 0x1f || d[1] != 0x8b) r = refuse(problem, capacity, DISC_APP_REFUSED, "Not a gzip twin: %s", x->path);
    } else if (html) {
        const unsigned char *end = d + x->size;
        const char *what = inline_block(d, end, "script", 1) ? "inline script"
                         : inline_block(d, end, "style", 0) || attribute(d, end, "style", 0, 0, 0) ? "inline style"
                         : attribute(d, end, NULL, 1, 1, 0) ? "inline event handler" : NULL;
        if (!what) for (const unsigned char *p = d; p < end && !what; p++) if (starts(p, end, "javascript:")) what = "javascript: URL";
        if (what) r = refuse(problem, capacity, DISC_APP_REFUSED, "%s: %s is blocked by the gateway CSP; move it into a file", x->path, what);
        else if (attribute(d, end, "href", 0, 0, 1) || attribute(d, end, "src", 0, 0, 1))
            r = refuse(problem, capacity, DISC_APP_REFUSED, "%s: a root-absolute href or src resolves only at /; use relative references (Vite: base \"./\")", x->path);
    } else {
        char found[256];
        if (root_asset(d, d + x->size, found, sizeof(found)))
            r = refuse(problem, capacity, DISC_APP_REFUSED, "%s: root-absolute asset reference %s resolves only at /; use relative references", x->path, found);
    }
    if (fd >= 0) close(fd);
    free(d);
    return r;
}

int disc_app_install_zip(const char *apps_root, const char *zip_path, disc_app_result *out, char *problem, size_t capacity) {
    memset(out, 0, sizeof(*out));
    int zip = open(zip_path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    struct stat st;
    if (zip < 0 || fstat(zip, &st) || !S_ISREG(st.st_mode)) {
        if (zip >= 0) close(zip);
        return refuse(problem, capacity, DISC_APP_FAILED, "The upload could not be read");
    }
    if (st.st_size > DISC_APP_ZIP_MAX) { close(zip); return refuse(problem, capacity, DISC_APP_REFUSED, "The archive is too large"); }
    entry *list = NULL;
    int count = 0, code = read_directory(zip, st.st_size, &list, &count, out->name, problem, capacity);
    long long total = 0;
    int index = 0;
    for (int i = 0; code == DISC_APP_OK && i < count; i++) {
        code = check_name(list, count, &list[i], problem, capacity);
        total += list[i].size;
        if (!strcmp(list[i].path, "index.html")) index = 1;
        if (code == DISC_APP_OK && total > DISC_APP_MAX_TOTAL) code = refuse(problem, capacity, DISC_APP_REFUSED, "The app exceeds the file count or total size limit");
    }
    if (code == DISC_APP_OK && !index) code = refuse(problem, capacity, DISC_APP_REFUSED, "An app needs index.html at its root");
    char fresh[PATH_MAX], target[PATH_MAX], previous[PATH_MAX];
    if (code == DISC_APP_OK && (snprintf(fresh, sizeof(fresh), "%s/.%s.installing", apps_root, out->name) >= (int)sizeof(fresh) ||
                                snprintf(target, sizeof(target), "%s/%s", apps_root, out->name) >= (int)sizeof(target) ||
                                snprintf(previous, sizeof(previous), "%s/.%s.previous", apps_root, out->name) >= (int)sizeof(previous)))
        code = refuse(problem, capacity, DISC_APP_FAILED, "Path too long");
    if (code == DISC_APP_OK) {
        struct statvfs v;
        if (mkdir(apps_root, 0755) && errno != EEXIST) code = refuse(problem, capacity, DISC_APP_FAILED, "The apps folder could not be made");
        else if (remove_tree(fresh, 0) || remove_tree(previous, 0)) code = refuse(problem, capacity, DISC_APP_FAILED, "A leftover of an earlier installation could not be removed");
        else if (statvfs(apps_root, &v) || (long long)v.f_bavail * (long long)v.f_frsize < total + 64 * 1024 + DISC_APP_RESERVE)
            code = refuse(problem, capacity, DISC_APP_NO_ROOM, "Not enough free space on the card for the app and the service's database. Nothing was written.");
        else if (mkdir(fresh, 0755)) code = refuse(problem, capacity, DISC_APP_FAILED, "The app's folder could not be made");
    }
    for (int i = 0; code == DISC_APP_OK && i < count; i++) {
        char path[PATH_MAX];
        if (snprintf(path, sizeof(path), "%s/%s", fresh, list[i].path) >= (int)sizeof(path)) code = refuse(problem, capacity, DISC_APP_FAILED, "Path too long");
        else if (make_parents(fresh, list[i].path)) code = refuse(problem, capacity, DISC_APP_FAILED, "%s: its folder could not be made", list[i].path);
        else code = unpack(zip, st.st_size, &list[i], path, problem, capacity);
    }
    for (int i = 0; code == DISC_APP_OK && i < count; i++) code = lint(fresh, &list[i], problem, capacity);
    close(zip);
    if (code == DISC_APP_OK) {
        sync();
        struct stat t;
        int had = !lstat(target, &t);
        if ((had && rename(target, previous)) || rename(fresh, target)) {
            if (had && lstat(target, &t)) (void)rename(previous, target);
            code = refuse(problem, capacity, DISC_APP_FAILED, "The installation failed; the card keeps what it had");
        } else {
            sync();
            (void)remove_tree(previous, 0);
            out->files = count;
            out->bytes = total;
        }
    }
    if (code != DISC_APP_OK && code != DISC_APP_NO_ROOM && out->name[0]) (void)remove_tree(fresh, 0);
    free(list);
    return code;
}

int disc_app_remove(const char *apps_root, const char *name, char *problem, size_t capacity) {
    char target[PATH_MAX], aside[PATH_MAX], index[PATH_MAX];
    struct stat st;
    if (!disc_app_name_ok(name) || snprintf(target, sizeof(target), "%s/%s", apps_root, name) >= (int)sizeof(target) ||
        snprintf(aside, sizeof(aside), "%s/.%s.removing", apps_root, name) >= (int)sizeof(aside) ||
        snprintf(index, sizeof(index), "%s/index.html", target) >= (int)sizeof(index) || lstat(target, &st) || !S_ISDIR(st.st_mode) || lstat(index, &st))
        return refuse(problem, capacity, DISC_APP_REFUSED, "No such app");
    (void)remove_tree(aside, 0);
    if (rename(target, aside)) return refuse(problem, capacity, DISC_APP_FAILED, "The app could not be removed");
    sync();
    if (remove_tree(aside, 0)) return refuse(problem, capacity, DISC_APP_FAILED, "The app's files could not all be deleted");
    sync();
    return DISC_APP_OK;
}
