#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#include "tree.h"
#include "framing.h"
#include "jsonutil.h"
#include "media.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define PIECE 16384

static long long clock_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static int visible_name(const char *name) {
    size_t n = strlen(name);
    if (!n || name[0] == '.' || !disc_utf8((const unsigned char *)name, n)) return 0;
    for (size_t i = 0; i < n; i++) if ((unsigned char)name[i] < 0x20 || name[i] == 0x7f) return 0;
    return 1;
}

int disc_tree_open(const char *card, const char *relative, int *status) {
    *status = 503;
    int dir = open(card, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (dir < 0) return -1;
    size_t n = strlen(relative);
    *status = 400;
    if (n > DISC_MEDIA_MAX_PATH || !disc_utf8((const unsigned char *)relative, n)) { close(dir); return -1; }
    const char *part = relative;
    char name[256];
    while (*part) {
        const char *slash = strchr(part, '/');
        size_t k = slash ? (size_t)(slash - part) : strlen(part);
        if (!k || k >= sizeof(name)) { close(dir); return -1; }
        memcpy(name, part, k);
        name[k] = 0;
        if (!visible_name(name)) { close(dir); return -1; }
        int next = openat(dir, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        int saved = errno;
        struct stat st;
        /* A link is refused alike on every system (Linux says ELOOP, macOS ENOTDIR). */
        int link = next < 0 && !fstatat(dir, name, &st, AT_SYMLINK_NOFOLLOW) && S_ISLNK(st.st_mode);
        close(dir);
        if (next < 0) { *status = !link && (saved == ENOENT || saved == ENOTDIR) ? 404 : 400; return -1; }
        dir = next;
        part = slash ? slash + 1 : part + k;
    }
    *status = 200;
    return dir;
}

typedef struct { char *name; int dir; long long bytes, modified; } item;
static int by_name(const void *a, const void *b) { return strcmp(((const item *)a)->name, ((const item *)b)->name); }
static void free_items(item *items, size_t count) {
    for (size_t i = 0; i < count; i++) free(items[i].name);
    free(items);
}

/* A folder's visible folders and regular files, sorted; links and hidden names are skipped. */
static int read_folder(int folder, item **items, size_t *count, int *truncated, size_t max) {
    *items = NULL; *count = 0; *truncated = 0;
    int copy = dup(folder);
    DIR *d = copy < 0 ? NULL : fdopendir(copy);
    if (!d) { if (copy >= 0) close(copy); return -1; }
    size_t capacity = 0;
    struct dirent *e;
    while ((e = readdir(d))) {
        struct stat st;
        if (!visible_name(e->d_name) || fstatat(folder, e->d_name, &st, AT_SYMLINK_NOFOLLOW)) continue;
        if (!S_ISDIR(st.st_mode) && !S_ISREG(st.st_mode)) continue;
        if (*count == max) { *truncated = 1; break; }
        if (*count == capacity) {
            capacity = capacity ? capacity * 2 : 32;
            item *grown = realloc(*items, capacity * sizeof(**items));
            if (!grown) { closedir(d); free_items(*items, *count); *items = NULL; *count = 0; return -1; }
            *items = grown;
        }
        item *it = &(*items)[*count];
        it->name = strdup(e->d_name);
        if (!it->name) continue;
        it->dir = S_ISDIR(st.st_mode);
        it->bytes = it->dir ? 0 : (long long)st.st_size;
        it->modified = (long long)st.st_mtime;
        (*count)++;
    }
    closedir(d);
    if (*count) qsort(*items, *count, sizeof(**items), by_name);
    return 0;
}

static const char *kind_of(const char *name) {
    if (disc_media_audio_name(name)) return "audio";
    const char *dot = strrchr(name, '.');
    if (!dot || dot == name) return "other";
    dot++;
    if (!strcasecmp(dot, "lrc")) return "lyrics";
    if (!strcasecmp(dot, "jpg") || !strcasecmp(dot, "jpeg") || !strcasecmp(dot, "png")) return "image";
    if (!strcasecmp(dot, "cue")) return "cue";
    if (!strcasecmp(dot, "m3u") || !strcasecmp(dot, "m3u8")) return "playlist";
    return "other";
}

int disc_tree_folder(int folder, const char *relative, char **json, size_t *length) {
    item *items;
    size_t count;
    int truncated;
    int failed = read_folder(folder, &items, &count, &truncated, DISC_TREE_FOLDER_MAX);
    close(folder);
    if (failed) return 503;
    disc_buffer b = {.limit = 4u * 1024u * 1024u};
    disc_buffer_text(&b, "{\"path\":");
    disc_buffer_string(&b, relative, strlen(relative));
    disc_buffer_text(&b, ",\"entries\":[");
    for (size_t i = 0; i < count; i++) {
        disc_buffer_text(&b, i ? ",{\"name\":" : "{\"name\":");
        disc_buffer_string(&b, items[i].name, strlen(items[i].name));
        if (items[i].dir) disc_buffer_text(&b, ",\"dir\":true");
        else {
            disc_buffer_text(&b, ",\"dir\":false,\"kind\":\"");
            disc_buffer_text(&b, kind_of(items[i].name));
            disc_buffer_text(&b, "\",\"bytes\":");
            disc_buffer_int(&b, items[i].bytes);
        }
        disc_buffer_text(&b, ",\"modified\":");
        disc_buffer_int(&b, items[i].modified);
        disc_buffer_text(&b, "}");
    }
    disc_buffer_text(&b, "],\"count\":");
    disc_buffer_int(&b, (long long)count);
    disc_buffer_text(&b, truncated ? ",\"truncated\":true}" : ",\"truncated\":false}");
    free_items(items, count);
    if (b.overflow || !b.data) { free(b.data); return 500; }
    *json = b.data;
    *length = b.used;
    return 200;
}

typedef struct {
    int (*emit)(void *arg, const char *data, size_t length);
    void *arg;
    disc_buffer out;
    long long started, entries, folders, files, bytes;
    int truncated, stopped;
} walker;

static void flush(walker *w, int force) {
    if (w->stopped || !w->out.used || (!force && w->out.used < PIECE)) return;
    if (w->emit(w->arg, w->out.data, w->out.used)) w->stopped = 1;
    w->out.used = 0;
}

static void put_optional(disc_buffer *b, const char *key, long long value) {
    disc_buffer_text(b, key);
    if (value > 0) disc_buffer_int(b, value);
    else disc_buffer_text(b, "null");
}

static void put_file(walker *w, int folder, const char *path, const item *it) {
    disc_buffer *b = &w->out;
    const char *kind = kind_of(it->name);
    disc_buffer_text(b, w->entries++ ? ",{\"path\":" : "{\"path\":");
    disc_buffer_string(b, path, strlen(path));
    disc_buffer_text(b, ",\"bytes\":");
    disc_buffer_int(b, it->bytes);
    disc_buffer_text(b, ",\"modified\":");
    disc_buffer_int(b, it->modified);
    disc_buffer_text(b, ",\"kind\":\"");
    disc_buffer_text(b, kind);
    disc_buffer_text(b, "\"");
    int fd = strcmp(kind, "audio") ? -1 : openat(folder, it->name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd >= 0) {
        disc_media_info info;
        disc_media_probe_quick(fd, it->name, &info);
        close(fd);
        disc_buffer_text(b, ",\"format\":");
        disc_buffer_string(b, info.format, strlen(info.format));
        put_optional(b, ",\"sampleRate\":", info.sample_rate);
        put_optional(b, ",\"bitDepth\":", info.bits);
        put_optional(b, ",\"channels\":", info.channels);
        put_optional(b, ",\"bitRate\":", info.bit_rate);
        put_optional(b, ",\"durationMs\":", info.has_duration ? (long long)info.duration_ms : 0);
        /* The year as the tags give it: its first four digits. */
        int year = strlen(info.date) >= 4 && strspn(info.date, "0123456789") >= 4;
        disc_buffer_text(b, ",\"year\":");
        if (year) disc_buffer_string(b, info.date, 4);
        else disc_buffer_text(b, "null");
    }
    disc_buffer_text(b, "}");
    w->files++;
    w->bytes += it->bytes;
}

static void walk(walker *w, int folder, const char *relative, int depth) {
    item *items;
    size_t count;
    int truncated;
    if (read_folder(folder, &items, &count, &truncated, DISC_TREE_FOLDER_MAX)) return;
    if (truncated) w->truncated = 1;
    char path[DISC_MEDIA_MAX_PATH + 256];
    for (size_t i = 0; i < count && !w->stopped; i++) {
        if (w->entries >= DISC_TREE_MAX_ENTRIES || clock_ms() - w->started > DISC_TREE_BUDGET_MS) { w->truncated = 1; break; }
        int n = snprintf(path, sizeof(path), "%s%s%s", relative, *relative ? "/" : "", items[i].name);
        if (n <= 0 || (size_t)n >= sizeof(path) || (size_t)n > DISC_MEDIA_MAX_PATH) { w->truncated = 1; continue; }
        if (!items[i].dir) {
            put_file(w, folder, path, &items[i]);
            flush(w, 0);
            continue;
        }
        disc_buffer_text(&w->out, w->entries++ ? ",{\"path\":" : "{\"path\":");
        disc_buffer_string(&w->out, path, (size_t)n);
        disc_buffer_text(&w->out, ",\"dir\":true,\"modified\":");
        disc_buffer_int(&w->out, items[i].modified);
        disc_buffer_text(&w->out, "}");
        w->folders++;
        flush(w, 0);
        if (depth + 1 >= DISC_TREE_MAX_DEPTH) { w->truncated = 1; continue; }
        int child = openat(folder, items[i].name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (child < 0) continue;
        walk(w, child, path, depth + 1);
        close(child);
    }
    free_items(items, count);
}

int disc_tree_walk(int folder, const char *card, const char *relative, long long now_ms,
                   int (*emit)(void *arg, const char *data, size_t length), void *arg) {
    (void)now_ms;
    walker w = {.emit = emit, .arg = arg, .out = {.limit = 2 * PIECE + 8192}, .started = clock_ms()};
    disc_buffer_text(&w.out, "{\"root\":");
    disc_buffer_string(&w.out, card, strlen(card));
    disc_buffer_text(&w.out, ",\"path\":");
    disc_buffer_string(&w.out, relative, strlen(relative));
    disc_buffer_text(&w.out, ",\"entries\":[");
    walk(&w, folder, relative, 0);
    close(folder);
    const struct { const char *key; long long value; } totals[] = {
        {"],\"folders\":", w.folders}, {",\"files\":", w.files}, {",\"bytes\":", w.bytes}};
    for (size_t i = 0; i < 3; i++) {
        disc_buffer_text(&w.out, totals[i].key);
        disc_buffer_int(&w.out, totals[i].value);
    }
    disc_buffer_text(&w.out, w.truncated ? ",\"truncated\":true,\"elapsedMs\":" : ",\"truncated\":false,\"elapsedMs\":");
    disc_buffer_int(&w.out, clock_ms() - w.started);
    disc_buffer_text(&w.out, "}");
    int overflow = w.out.overflow;
    flush(&w, 1);
    free(w.out.data);
    return w.stopped || overflow ? -1 : 0;
}
