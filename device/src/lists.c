#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#include "lists.h"
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
#include <sys/statvfs.h>
#include <time.h>
#include <unistd.h>

/* Room a write leaves on the card for the service's database (the publisher keeps the same). */
#define LISTS_RESERVE (8ull * 1024 * 1024)
#define LISTS_MAX_FILE ((size_t)DISC_LISTS_MAX_ENTRIES * (DISC_MEDIA_MAX_PATH + 2) + 16)
#define LISTS_INDEX_MAX 500
#define LISTS_TEXT(x) #x
#define LISTS_NUMBER(x) LISTS_TEXT(x)

int disc_lists_name_ok(const char *name) {
    size_t n = name ? strlen(name) : 0;
    if (!n || n > DISC_LISTS_NAME_MAX || !disc_utf8((const unsigned char *)name, n)) return 0;
    if (name[0] == '.' || name[0] == ' ' || name[n - 1] == '.' || name[n - 1] == ' ') return 0;
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)name[i];
        if (c < 0x20 || c == 0x7f || strchr("/\\:*?\"<>|", c)) return 0;
    }
    return 1;
}

static int file_name(const char *name, char *out, size_t capacity) {
    return disc_lists_name_ok(name) && (size_t)snprintf(out, capacity, "%s.m3u", name) < capacity;
}

static int open_folder(const disc_lists *l) {
    return open(l->folder, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
}

/* The folder, created when missing (and its parent, the service's .disc). */
static int make_folder(const disc_lists *l) {
    int dir = open_folder(l);
    if (dir >= 0 || errno != ENOENT) return dir;
    char parent[512];
    snprintf(parent, sizeof(parent), "%s", l->folder);
    char *slash = strrchr(parent, '/');
    if (slash && slash != parent) {
        *slash = 0;
        if (mkdir(parent, 0755) && errno != EEXIST) return -1;
    }
    if (mkdir(l->folder, 0755) && errno != EEXIST) return -1;
    return open_folder(l);
}

static int reply(disc_buffer *b, char **json, size_t *length, int status) {
    if (b->overflow || !b->data) { free(b->data); return 500; }
    *json = b->data;
    *length = b->used;
    return status;
}

static int refuse(const char *message, char **json, size_t *length, int status, long index, const char *entry) {
    disc_buffer b = {0};
    disc_buffer_text(&b, "{\"error\":");
    disc_buffer_string(&b, message, strlen(message));
    if (index >= 0) {
        disc_buffer_text(&b, ",\"entry\":");
        disc_buffer_int(&b, index);
        disc_buffer_text(&b, ",\"path\":");
        disc_buffer_string(&b, entry, strlen(entry));
    }
    disc_buffer_text(&b, "}");
    return reply(&b, json, length, status);
}

typedef struct { char name[DISC_LISTS_NAME_MAX + 1]; long long bytes, modified; } list_row;
static int by_name(const void *a, const void *b) { return strcmp(((const list_row *)a)->name, ((const list_row *)b)->name); }

/* The lists in the folder: our own .m3u files, by name. */
static int collect(const disc_lists *l, list_row **rows, size_t *count, int *truncated) {
    *rows = NULL; *count = 0; *truncated = 0;
    int dir = open_folder(l);
    if (dir < 0) return errno == ENOENT ? 0 : -1;
    DIR *d = fdopendir(dir);
    if (!d) { close(dir); return -1; }
    size_t capacity = 0;
    struct dirent *e;
    while ((e = readdir(d))) {
        size_t n = strlen(e->d_name);
        if (e->d_name[0] == '.' || n <= 4 || strcasecmp(e->d_name + n - 4, ".m3u")) continue;
        char name[DISC_LISTS_NAME_MAX + 8];
        if (n - 4 >= sizeof(name)) continue;
        memcpy(name, e->d_name, n - 4);
        name[n - 4] = 0;
        struct stat st;
        if (!disc_lists_name_ok(name) || fstatat(dirfd(d), e->d_name, &st, AT_SYMLINK_NOFOLLOW) || !S_ISREG(st.st_mode)) continue;
        if (*count == LISTS_INDEX_MAX) { *truncated = 1; break; }
        if (*count == capacity) {
            capacity = capacity ? capacity * 2 : 16;
            list_row *grown = realloc(*rows, capacity * sizeof(**rows));
            if (!grown) { closedir(d); free(*rows); *rows = NULL; return -1; }
            *rows = grown;
        }
        list_row *row = &(*rows)[(*count)++];
        memcpy(row->name, name, n - 3);
        row->bytes = (long long)st.st_size;
        row->modified = (long long)st.st_mtime;
    }
    closedir(d);
    if (*count) qsort(*rows, *count, sizeof(**rows), by_name);
    return 0;
}

static void put_path(disc_buffer *b, const disc_lists *l, const char *file) {
    char path[1200];
    int n = snprintf(path, sizeof(path), "%s/%s", l->folder, file);
    disc_buffer_string(b, path, n > 0 && (size_t)n < sizeof(path) ? (size_t)n : 0);
}

int disc_lists_index(const disc_lists *l, char **json, size_t *length, const char **problem) {
    list_row *rows;
    size_t count;
    int truncated;
    if (collect(l, &rows, &count, &truncated)) { *problem = "The lists folder cannot be read"; return 503; }
    disc_buffer b = {0};
    disc_buffer_text(&b, "{\"folder\":");
    disc_buffer_string(&b, l->folder, strlen(l->folder));
    disc_buffer_text(&b, ",\"lists\":[");
    for (size_t i = 0; i < count; i++) {
        char file[DISC_LISTS_NAME_MAX + 8];
        snprintf(file, sizeof(file), "%s.m3u", rows[i].name);
        disc_buffer_text(&b, i ? ",{\"name\":" : "{\"name\":");
        disc_buffer_string(&b, rows[i].name, strlen(rows[i].name));
        disc_buffer_text(&b, ",\"path\":");
        put_path(&b, l, file);
        disc_buffer_text(&b, ",\"bytes\":");
        disc_buffer_int(&b, rows[i].bytes);
        disc_buffer_text(&b, ",\"modified\":");
        disc_buffer_int(&b, rows[i].modified);
        disc_buffer_text(&b, "}");
    }
    disc_buffer_text(&b, "],\"count\":");
    disc_buffer_int(&b, (long long)count);
    disc_buffer_text(&b, truncated ? ",\"truncated\":true" : ",\"truncated\":false");
    disc_buffer_text(&b, ",\"max\":" LISTS_NUMBER(DISC_LISTS_MAX) ",\"maxEntries\":" LISTS_NUMBER(DISC_LISTS_MAX_ENTRIES) "}");
    free(rows);
    return reply(&b, json, length, 200);
}

/* Reads a whole regular file (bounded) opened without following links. */
static char *read_list(int dir, const char *file, size_t *size, struct stat *st, int *status) {
    int fd = openat(dir, file, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) { *status = errno == ENOENT ? 404 : 409; return NULL; }
    char *data = NULL;
    *status = 409;
    if (!fstat(fd, st) && S_ISREG(st->st_mode) && (size_t)st->st_size <= LISTS_MAX_FILE) {
        *size = (size_t)st->st_size;
        data = malloc(*size + 1);
        size_t used = 0;
        while (data && used < *size) {
            ssize_t n = read(fd, data + used, *size - used);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) break;
            used += (size_t)n;
        }
        if (data && used == *size) { data[used] = 0; *status = 200; }
        else { free(data); data = NULL; *status = 503; }
    }
    close(fd);
    return data;
}

int disc_lists_read(const disc_lists *l, const char *name, char **json, size_t *length, const char **problem) {
    char file[DISC_LISTS_NAME_MAX + 8];
    if (!file_name(name, file, sizeof(file))) { *problem = "Not a list name"; return 404; }
    int dir = open_folder(l);
    if (dir < 0) { *problem = "No such list"; return errno == ENOENT ? 404 : 503; }
    size_t size = 0;
    struct stat st;
    int status;
    char *data = read_list(dir, file, &size, &st, &status);
    close(dir);
    if (!data) { *problem = status == 404 ? "No such list" : status == 409 ? "Not a list file" : "The list cannot be read"; return status; }
    disc_buffer b = {.limit = LISTS_MAX_FILE + 256 * 1024};
    disc_buffer_text(&b, "{\"name\":");
    disc_buffer_string(&b, name, strlen(name));
    disc_buffer_text(&b, ",\"path\":");
    put_path(&b, l, file);
    disc_buffer_text(&b, ",\"bytes\":");
    disc_buffer_int(&b, (long long)size);
    disc_buffer_text(&b, ",\"modified\":");
    disc_buffer_int(&b, (long long)st.st_mtime);
    disc_buffer_text(&b, ",\"entries\":[");
    char *line = data;
    if (size >= 3 && !memcmp(line, "\xef\xbb\xbf", 3)) line += 3;
    long count = 0;
    while (*line) {
        char *end = strchr(line, '\n');
        size_t n = end ? (size_t)(end - line) : strlen(line);
        size_t text = n;
        if (text && line[text - 1] == '\r') text--;
        if (text && line[0] != '#') {
            const char *entry = line[0] == '/' ? line + 1 : line;
            size_t entry_length = text - (size_t)(entry - line);
            char path[DISC_MEDIA_MAX_PATH + 256];
            int k = snprintf(path, sizeof(path), "%s/%.*s", l->card, (int)entry_length, entry);
            if (k > 0 && (size_t)k < sizeof(path) && disc_utf8((const unsigned char *)path, (size_t)k)) {
                disc_buffer_text(&b, count++ ? "," : "");
                disc_buffer_string(&b, path, (size_t)k);
            }
        }
        if (!end) break;
        line = end + 1;
    }
    disc_buffer_text(&b, "],\"count\":");
    disc_buffer_int(&b, count);
    disc_buffer_text(&b, "}");
    free(data);
    return reply(&b, json, length, 200);
}

/* An existing regular music file on the card, reached as the media routes reach one. */
static const char *entry_problem(const disc_lists *l, const char *path, size_t n) {
    size_t card = strlen(l->card);
    if (n > DISC_MEDIA_MAX_PATH || strncmp(path, l->card, card) || path[card] != '/' || !path[card + 1])
        return "not an absolute path on the card";
    if (!disc_utf8((const unsigned char *)path, n) || strlen(path) != n) return "not UTF-8";
    for (size_t i = 0; i < n; i++)
        if ((unsigned char)path[i] < 0x20 || path[i] == 0x7f) return "control characters";
    if (strstr(path + card, "/.")) return "a hidden or dot part";
    int dir = -1, fd = -1;
    char name[256];
    int opened = disc_media_open(l->card, path, &dir, &fd, name, sizeof(name));
    if (fd >= 0) close(fd);
    if (dir >= 0) close(dir);
    return opened == 0 ? NULL : opened == 404 ? "no such music file" : opened == 503 ? "the card is unavailable"
                                              : "not a music file (or a link)";
}

static int write_all_fd(int fd, const char *data, size_t n) {
    size_t done = 0;
    while (done < n) {
        ssize_t w = write(fd, data + done, n - done);
        if (w < 0 && errno == EINTR) continue;
        if (w <= 0) return -1;
        done += (size_t)w;
    }
    return 0;
}

int disc_lists_write(const disc_lists *l, const char *name, const char *body, size_t body_length,
                     char **json, size_t *length, const char **problem) {
    char file[DISC_LISTS_NAME_MAX + 8];
    if (!file_name(name, file, sizeof(file))) {
        *problem = "A list name is UTF-8 of at most 96 bytes, without / \\ : * ? \" < > | or control characters, "
                   "and does not start or end with a dot or a space";
        return 400;
    }
    jsmntok_t *t = NULL;
    int tokens = disc_json_parse(body, body_length, &t, 2 * DISC_LISTS_MAX_ENTRIES + 8), next, array;
    if (tokens <= 0 || !disc_json_valid(body, t, 0, &next) || next != tokens || t[0].type != JSMN_OBJECT || t[0].size != 1 ||
        (array = disc_json_find(body, t, 0, "entries")) <= 0 || t[array].type != JSMN_ARRAY) {
        free(t);
        *problem = "The body is {\"entries\":[\"<a music file on the card>\", ...]}";
        return 400;
    }
    int count = t[array].size;
    if (count > DISC_LISTS_MAX_ENTRIES) { free(t); *problem = "Too many entries (at most 5000)"; return 413; }
    size_t card = strlen(l->card), capacity = 16 + (size_t)count * (DISC_MEDIA_MAX_PATH + 2);
    char *content = malloc(capacity), *entry = malloc(DISC_MEDIA_MAX_PATH + 2);
    if (!content || !entry) { free(t); free(content); free(entry); *problem = "Memory unavailable"; return 503; }
    /* A UTF-8 BOM first: the player guesses a list's encoding with a character-set detector, which
     * took a short list with a few accented names for cp1251 on its screen; with the BOM it shows
     * them right (the owner's player, 2026-09-29; .m3u8 did not help). Stock plays it the same. */
    size_t used = (size_t)snprintf(content, capacity, "\xef\xbb\xbf#EXTM3U\n");
    int status = 200;
    for (int i = 0, k = array + 1; i < count; i++, k = disc_json_skip(t, k)) {
        int n = t[k].type == JSMN_STRING ? disc_json_string(body, &t[k], entry, DISC_MEDIA_MAX_PATH + 2) : -1;
        const char *why = n <= 0 ? "not a path" : entry_problem(l, entry, (size_t)n);
        if (why) {
            status = refuse(why, json, length, 400, i, n > 0 ? entry : "");
            break;
        }
        memcpy(content + used, entry + card + 1, (size_t)n - card - 1);
        used += (size_t)n - card - 1;
        content[used++] = '\n';
    }
    free(t);
    free(entry);
    if (status != 200) { free(content); *problem = NULL; return status; }
    int dir = make_folder(l);
    if (dir < 0) { free(content); *problem = "The lists folder cannot be created"; return 503; }
    struct stat st;
    int replaced = !fstatat(dir, file, &st, AT_SYMLINK_NOFOLLOW);
    if (replaced && !S_ISREG(st.st_mode)) { close(dir); free(content); *problem = "Something that is not a list has that name"; return 409; }
    list_row *rows = NULL;
    size_t lists = 0;
    int truncated = 0;
    if (!replaced && (collect(l, &rows, &lists, &truncated) || lists >= DISC_LISTS_MAX)) {
        free(rows); close(dir); free(content);
        *problem = "Too many lists (at most 200); delete one first";
        return 409;
    }
    free(rows);
    struct statvfs space;
    if (fstatvfs(dir, &space) || (unsigned long long)space.f_bavail * space.f_frsize < used + LISTS_RESERVE) {
        close(dir); free(content);
        *problem = "Not enough free space on the card (8 MiB stay free for the service's database)";
        return 507;
    }
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    char temp[64];
    snprintf(temp, sizeof(temp), ".list-%ld-%lld.part", (long)getpid(), (long long)now.tv_sec * 1000 + now.tv_nsec / 1000000);
    int fd = openat(dir, temp, O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0644);
    status = 503;
    *problem = "The list could not be written; the previous one stays";
    if (fd >= 0) {
        char *check = malloc(used ? used : 1);
        size_t got = 0;
        if (check && !write_all_fd(fd, content, used) && !fsync(fd)) {
            while (got < used) {
                ssize_t n = pread(fd, check + got, used - got, (off_t)got);
                if (n < 0 && errno == EINTR) continue;
                if (n <= 0) break;
                got += (size_t)n;
            }
        }
        int same = check && got == used && !memcmp(check, content, used);
        free(check);
        close(fd);
        if (same && !renameat(dir, temp, dir, file)) {
            fsync(dir);
            status = replaced ? 200 : 201;
        } else unlinkat(dir, temp, 0);
    }
    close(dir);
    free(content);
    if (status >= 300) return status;
    disc_buffer b = {0};
    disc_buffer_text(&b, "{\"name\":");
    disc_buffer_string(&b, name, strlen(name));
    disc_buffer_text(&b, ",\"path\":");
    put_path(&b, l, file);
    disc_buffer_text(&b, ",\"entries\":");
    disc_buffer_int(&b, count);
    disc_buffer_text(&b, ",\"bytes\":");
    disc_buffer_int(&b, (long long)used);
    disc_buffer_text(&b, replaced ? ",\"replaced\":true}" : ",\"replaced\":false}");
    *problem = NULL;
    return reply(&b, json, length, status);
}

int disc_lists_delete(const disc_lists *l, const char *name, char **json, size_t *length, const char **problem) {
    char file[DISC_LISTS_NAME_MAX + 8];
    if (!file_name(name, file, sizeof(file))) { *problem = "Not a list name"; return 404; }
    int dir = open_folder(l);
    if (dir < 0) { *problem = "No such list"; return errno == ENOENT ? 404 : 503; }
    struct stat st;
    int status;
    if (fstatat(dir, file, &st, AT_SYMLINK_NOFOLLOW)) { status = errno == ENOENT ? 404 : 503; *problem = "No such list"; }
    else if (!S_ISREG(st.st_mode)) { status = 409; *problem = "Not a list file"; }
    else if (unlinkat(dir, file, 0)) { status = 503; *problem = "The list could not be deleted"; }
    else { fsync(dir); status = 200; }
    close(dir);
    if (status != 200) return status;
    disc_buffer b = {0};
    disc_buffer_text(&b, "{\"name\":");
    disc_buffer_string(&b, name, strlen(name));
    disc_buffer_text(&b, ",\"deleted\":true}");
    *problem = NULL;
    return reply(&b, json, length, 200);
}
