/* The server's updates: a signed .update stream into the boot layer's inactive slot (update.h). */
#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#include "update.h"
#include "jsonutil.h"
#include "monocypher-ed25519.h"
#include "sha256.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define KEYS_MAX 8
#define PATH_MAX_PACKAGE 200

typedef struct { char path[PATH_MAX_PACKAGE + 1]; long long size; char sha256[65]; int mode; } item;

static int refuse(char *problem, size_t capacity, int code, const char *format, ...) {
    va_list args;
    va_start(args, format);
    vsnprintf(problem, capacity, format, args);
    va_end(args);
    return code;
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
    *slash = 0;
    for (char *p = path + strlen(root) + 1; *p; p++) {
        if (*p != '/') continue;
        *p = 0;
        if (mkdir(path, 0755) && errno != EEXIST) return -1;
        *p = '/';
    }
    return mkdir(path, 0755) && errno != EEXIST ? -1 : 0;
}

static int hex_value(char c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1; }

static int load_keys(const char *path, uint8_t keys[KEYS_MAX][32]) {
    FILE *f = path ? fopen(path, "r") : NULL;
    if (!f) return 0;
    char line[160];
    int n = 0;
    while (fgets(line, sizeof(line), f)) {
        size_t len = strcspn(line, "\r\n");
        line[len] = 0;
        if (!len || line[0] == '#') continue;
        if (len != 64 || n >= KEYS_MAX) { n = 0; break; }
        int ok = 1;
        for (int i = 0; i < 32 && ok; i++) {
            int hi = hex_value(line[2 * i]), lo = hex_value(line[2 * i + 1]);
            if (hi < 0 || lo < 0) ok = 0;
            else keys[n][i] = (uint8_t)(hi << 4 | lo);
        }
        if (!ok) { n = 0; break; }
        n++;
    }
    fclose(f);
    return n;
}

int disc_update_keys(const char *path) {
    uint8_t keys[KEYS_MAX][32];
    return load_keys(path, keys);
}

/* Boot's path rule (contract, "package.json"): relative, at most 8 folders deep and 200 bytes,
 * letters, digits and ._+@-, without . or .. parts; package.json is the manifest's own name. */
static int path_ok(const char *p) {
    size_t n = strlen(p);
    int depth = 0;
    if (!n || n > PATH_MAX_PACKAGE || p[0] == '/' || p[n - 1] == '/' || !strcmp(p, "package.json")) return 0;
    for (const char *c = p; *c; c++) {
        char x = *c;
        int ok = (x >= 'A' && x <= 'Z') || (x >= 'a' && x <= 'z') || (x >= '0' && x <= '9') || strchr("._+@-/", x);
        if (!ok) return 0;
        if (x == '/' && ++depth > 8) return 0;
    }
    for (const char *part = p; part; ) {
        const char *slash = strchr(part, '/');
        size_t len = slash ? (size_t)(slash - part) : strlen(part);
        if (!len || (len == 1 && part[0] == '.') || (len == 2 && part[0] == '.' && part[1] == '.')) return 0;
        part = slash ? slash + 1 : NULL;
    }
    return 1;
}

/* The manifest's name, version, role and files, in its order. */
static int read_manifest(const char *text, size_t length, const char *expected_name, disc_update_result *out,
                         item **items, int *count, long long *total, char *problem, size_t capacity) {
    jsmntok_t *t = NULL;
    int tokens = disc_json_object(text, length) ? disc_json_parse(text, length, &t, 8 * DISC_UPDATE_FILES_MAX + 64) : -1;
    char role[16] = "";
    int i;
    if (tokens <= 0) { free(t); return refuse(problem, capacity, DISC_UPDATE_REFUSED, "The update's package.json is not valid JSON"); }
    if ((i = disc_json_find(text, t, 0, "name")) < 0 || disc_json_string(text, &t[i], out->name, sizeof(out->name)) <= 0 ||
        (i = disc_json_find(text, t, 0, "version")) < 0 || disc_json_string(text, &t[i], out->version, sizeof(out->version)) <= 0 ||
        (i = disc_json_find(text, t, 0, "role")) < 0 || disc_json_string(text, &t[i], role, sizeof(role)) <= 0) {
        free(t); return refuse(problem, capacity, DISC_UPDATE_REFUSED, "The update's package.json lacks its name, version or role");
    }
    if (strcmp(out->name, expected_name)) { free(t); return refuse(problem, capacity, DISC_UPDATE_REFUSED, "This update is for %s, not %s", out->name, expected_name); }
    if (strcmp(role, "service")) { free(t); return refuse(problem, capacity, DISC_UPDATE_REFUSED, "The update is not a service package"); }
    int files = disc_json_find(text, t, 0, "files");
    if (files < 0 || t[files].type != JSMN_OBJECT || t[files].size < 1 || t[files].size > DISC_UPDATE_FILES_MAX) {
        free(t); return refuse(problem, capacity, DISC_UPDATE_REFUSED, "The update lists no files, or too many");
    }
    item *list = calloc((size_t)t[files].size, sizeof(item));
    if (!list) { free(t); return refuse(problem, capacity, DISC_UPDATE_FAILED, "Out of memory"); }
    int n = 0, code = DISC_UPDATE_OK;
    *total = 0;
    for (int k = files + 1, m = 0; m < t[files].size && code == DISC_UPDATE_OK; m++) {
        item *x = &list[n];
        char mode[8] = "";
        int value = k + 1, f;
        if (disc_json_string(text, &t[k], x->path, sizeof(x->path)) <= 0 || !path_ok(x->path))
            code = refuse(problem, capacity, DISC_UPDATE_REFUSED, "The update lists an unsafe path");
        else if (t[value].type != JSMN_OBJECT ||
                 (f = disc_json_find(text, t, value, "size")) < 0 || disc_json_number(text, &t[f], 0, DISC_UPDATE_PACKAGE_MAX, &x->size) ||
                 (f = disc_json_find(text, t, value, "sha256")) < 0 || disc_json_sha256(text, &t[f], x->sha256) ||
                 (f = disc_json_find(text, t, value, "mode")) < 0 || disc_json_string(text, &t[f], mode, sizeof(mode)) <= 0 ||
                 (strcmp(mode, "0755") && strcmp(mode, "0644")))
            code = refuse(problem, capacity, DISC_UPDATE_REFUSED, "%s: its size, sha256 or mode is missing or invalid", x->path);
        else {
            x->mode = !strcmp(mode, "0755") ? 0755 : 0644;
            for (int j = 0; j < n; j++)
                if (!strcmp(list[j].path, x->path)) code = refuse(problem, capacity, DISC_UPDATE_REFUSED, "%s is listed twice", x->path);
            *total += x->size;
            if (*total > DISC_UPDATE_PACKAGE_MAX) code = refuse(problem, capacity, DISC_UPDATE_REFUSED, "The update exceeds 32 MiB");
            n++;
        }
        k = disc_json_skip(t, value);
    }
    free(t);
    if (code != DISC_UPDATE_OK) { free(list); return code; }
    *items = list; *count = n;
    return DISC_UPDATE_OK;
}

typedef struct { disc_update_reader read; void *context; long long left; } stream;

/* Exactly n bytes of the stream, or -1 when it ends early. */
static int take(stream *s, void *buffer, size_t n) {
    size_t got = 0;
    while (got < n) {
        int r = s->read(s->context, (char *)buffer + got, n - got);
        if (r <= 0) return -1;
        got += (size_t)r;
    }
    s->left -= (long long)n;
    return 0;
}

static int write_file(const char *path, const void *data, size_t n, int mode) {
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return -1;
    int r = write_all(fd, data, n) || fchmod(fd, (mode_t)mode) || fsync(fd) ? -1 : 0;
    return close(fd) || r ? -1 : 0;
}

int disc_update_stage(const disc_update_config *config, long long length, disc_update_reader read, void *context,
                      disc_update_result *out, char *problem, size_t capacity) {
    memset(out, 0, sizeof(*out));
    if (length < DISC_UPDATE_HEADER + 2) return refuse(problem, capacity, DISC_UPDATE_REFUSED, "Not a server update (.update) file");
    if (length > DISC_UPDATE_MAX) return refuse(problem, capacity, DISC_UPDATE_REFUSED, "The update is too large");
    stream s = {read, context, length};
    uint8_t header[DISC_UPDATE_HEADER];
    if (take(&s, header, sizeof(header))) return refuse(problem, capacity, DISC_UPDATE_SHORT, "The upload ended early");
    if (memcmp(header, "DISCUPD1", 8)) return refuse(problem, capacity, DISC_UPDATE_REFUSED, "Not a server update (.update) file");
    uint32_t manifest_length = (uint32_t)header[72] | (uint32_t)header[73] << 8 | (uint32_t)header[74] << 16 | (uint32_t)header[75] << 24;
    if (manifest_length < 2 || manifest_length > DISC_UPDATE_MANIFEST_MAX || DISC_UPDATE_HEADER + (long long)manifest_length > length)
        return refuse(problem, capacity, DISC_UPDATE_REFUSED, "The update's package.json is out of bounds");
    char *manifest = malloc(manifest_length + 1);
    if (!manifest) return refuse(problem, capacity, DISC_UPDATE_FAILED, "Out of memory");
    if (take(&s, manifest, manifest_length)) { free(manifest); return refuse(problem, capacity, DISC_UPDATE_SHORT, "The upload ended early"); }
    manifest[manifest_length] = 0;
    /* The signature first: nothing of an unsigned stream is read further, nothing is written. */
    uint8_t keys[KEYS_MAX][32];
    int key_count = load_keys(config->keys, keys), trusted = 0;
    for (int k = 0; k < key_count && !trusted; k++) trusted = !crypto_ed25519_check(header + 8, keys[k], (const uint8_t *)manifest, manifest_length);
    if (!trusted) { free(manifest); return refuse(problem, capacity, DISC_UPDATE_REFUSED, "The update is not signed by a key this server trusts"); }
    item *items = NULL;
    int count = 0;
    long long total = 0;
    int code = read_manifest(manifest, manifest_length, config->name, out, &items, &count, &total, problem, capacity);
    if (code == DISC_UPDATE_OK && DISC_UPDATE_HEADER + (long long)manifest_length + total != length)
        code = refuse(problem, capacity, DISC_UPDATE_REFUSED, "The update's length does not match its package.json");
    /* Room: the slot keeps its version until the new one is complete, so both must fit with stock's
     * reserve (a leftover work folder goes first). */
    struct statvfs v;
    (void)remove_tree(config->work, 0);
    if (code == DISC_UPDATE_OK && (statvfs(config->slot, &v) ||
        (long long)v.f_bavail * (long long)v.f_frsize < total + manifest_length + 64 * 1024 + DISC_UPDATE_RESERVE))
        code = refuse(problem, capacity, DISC_UPDATE_NO_ROOM, "Not enough room in /usr/data for the update. Nothing was changed.");
    if (code == DISC_UPDATE_OK && mkdir(config->work, 0755)) code = refuse(problem, capacity, DISC_UPDATE_FAILED, "The update's work folder could not be made");
    if (code != DISC_UPDATE_OK) { free(items); free(manifest); return code; }
    enum { CHUNK = 16384 };
    unsigned char *buffer = malloc(CHUNK);
    if (!buffer) code = refuse(problem, capacity, DISC_UPDATE_FAILED, "Out of memory");
    for (int i = 0; i < count && code == DISC_UPDATE_OK; i++) {
        item *x = &items[i];
        char path[PATH_MAX];
        int fd = -1;
        if (snprintf(path, sizeof(path), "%s/%s", config->work, x->path) >= (int)sizeof(path) || make_parents(config->work, x->path) ||
            (fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600)) < 0) {
            code = refuse(problem, capacity, DISC_UPDATE_FAILED, "%s could not be written", x->path);
            break;
        }
        sha256_ctx hash;
        sha256_init(&hash);
        for (long long left = x->size; left > 0 && code == DISC_UPDATE_OK; ) {
            size_t n = left < CHUNK ? (size_t)left : CHUNK;
            if (take(&s, buffer, n)) code = refuse(problem, capacity, DISC_UPDATE_SHORT, "The upload ended early");
            else if (write_all(fd, buffer, n)) code = refuse(problem, capacity, DISC_UPDATE_FAILED, "%s could not be written", x->path);
            else { sha256_update(&hash, buffer, n); left -= (long long)n; }
        }
        uint8_t digest[32];
        char hex[65];
        sha256_final(&hash, digest);
        sha256_hex(digest, hex);
        if (code == DISC_UPDATE_OK && strcmp(hex, x->sha256)) code = refuse(problem, capacity, DISC_UPDATE_REFUSED, "%s does not match its sha256", x->path);
        if (code == DISC_UPDATE_OK && (fchmod(fd, (mode_t)x->mode) || fsync(fd))) code = refuse(problem, capacity, DISC_UPDATE_FAILED, "%s could not be written", x->path);
        if (close(fd) && code == DISC_UPDATE_OK) code = refuse(problem, capacity, DISC_UPDATE_FAILED, "%s could not be written", x->path);
    }
    free(buffer);
    char path[PATH_MAX];
    if (code == DISC_UPDATE_OK && (snprintf(path, sizeof(path), "%s/package.json", config->work) >= (int)sizeof(path) ||
                                   write_file(path, manifest, manifest_length, 0644)))
        code = refuse(problem, capacity, DISC_UPDATE_FAILED, "package.json could not be written");
    if (code == DISC_UPDATE_OK) {
        sync();
        char why[200];
        if (disc_update_verify(config->boot_program, config->work, why, sizeof(why)))
            code = refuse(problem, capacity, DISC_UPDATE_REFUSED, "The boot layer refused the update: %s", why);
    }
    /* The swap: one rename on the same file system once the slot's old version is gone. */
    if (code == DISC_UPDATE_OK && (remove_tree(config->slot, 0) || rename(config->work, config->slot))) {
        (void)mkdir(config->slot, 0755);
        code = refuse(problem, capacity, DISC_UPDATE_FAILED, "The update could not take the slot's place");
    }
    if (code == DISC_UPDATE_OK) { sync(); out->files = count; out->bytes = total; }
    else (void)remove_tree(config->work, 0);
    free(items);
    free(manifest);
    return code;
}

int disc_update_slot(const char *slot, char name[33], char version[65], char sha256[65]) {
    char path[PATH_MAX];
    if (!slot || snprintf(path, sizeof(path), "%s/package.json", slot) >= (int)sizeof(path)) return -1;
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
    char *text = malloc(DISC_UPDATE_MANIFEST_MAX + 1);
    ssize_t n = text ? read(fd, text, DISC_UPDATE_MANIFEST_MAX + 1) : -1;
    close(fd);
    if (n <= 0 || n > DISC_UPDATE_MANIFEST_MAX) { free(text); return -1; }
    text[n] = 0;
    sha256_ctx hash;
    uint8_t digest[32];
    sha256_init(&hash); sha256_update(&hash, text, (size_t)n); sha256_final(&hash, digest); sha256_hex(digest, sha256);
    jsmntok_t *t = NULL;
    int i, ok = disc_json_parse(text, (size_t)n, &t, 8 * DISC_UPDATE_FILES_MAX + 64) > 0 &&
                (i = disc_json_find(text, t, 0, "name")) > 0 && disc_json_string(text, &t[i], name, 33) > 0 &&
                (i = disc_json_find(text, t, 0, "version")) > 0 && disc_json_string(text, &t[i], version, 65) > 0;
    free(t); free(text);
    return ok ? 0 : -1;
}

int disc_update_verify(const char *boot_program, const char *slot, char *problem, size_t capacity) {
    int pipefd[2];
    if (!boot_program || !slot) return refuse(problem, capacity, -1, "no boot program to check with");
    if (pipe(pipefd)) return refuse(problem, capacity, -1, "the check could not start");
    pid_t child = fork();
    if (child < 0) { close(pipefd[0]); close(pipefd[1]); return refuse(problem, capacity, -1, "the check could not start"); }
    if (child == 0) {
        dup2(pipefd[1], 1);
        int null = open("/dev/null", O_RDWR);
        if (null >= 0) { dup2(null, 0); dup2(null, 2); }
        close(pipefd[0]); close(pipefd[1]);
        execl(boot_program, boot_program, "verify", "service", slot, (char *)NULL);
        _exit(127);
    }
    close(pipefd[1]);
    /* A 32 MiB package is hashed again; on the player that takes seconds, not minutes. */
    char answer[1024];
    size_t used = 0;
    struct timespec start;
    clock_gettime(CLOCK_MONOTONIC, &start);
    for (;;) {
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        long elapsed = (long)(now.tv_sec - start.tv_sec) * 1000 + (now.tv_nsec - start.tv_nsec) / 1000000;
        if (elapsed > 120000) { kill(child, SIGKILL); break; }
        struct pollfd p = {pipefd[0], POLLIN, 0};
        int ready = poll(&p, 1, 500);
        if (ready < 0 && errno == EINTR) continue;
        if (ready <= 0) continue;
        ssize_t n = read(pipefd[0], answer + used, sizeof(answer) - 1 - used);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        used += (size_t)n;
        if (used == sizeof(answer) - 1) break;
    }
    close(pipefd[0]);
    int status = 0;
    while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
    answer[used] = 0;
    jsmntok_t *t = NULL;
    int ok = 0, i;
    if (disc_json_parse(answer, used, &t, 32) > 0 && (i = disc_json_find(answer, t, 0, "ok")) > 0 && !disc_json_boolean(answer, &t[i], &ok) && !ok &&
        (i = disc_json_find(answer, t, 0, "error")) > 0 && disc_json_string(answer, &t[i], problem, capacity) > 0) {
        free(t);
        return -1;
    }
    free(t);
    if (ok && WIFEXITED(status) && WEXITSTATUS(status) == 0) return 0;
    return refuse(problem, capacity, -1, "the check did not answer");
}

int disc_update_request(const char *path, const char *action) {
    char temp[PATH_MAX], text[64];
    int n = snprintf(text, sizeof(text), "{\"action\":\"%s\"}\n", action);
    if (!path || snprintf(temp, sizeof(temp), "%s.tmp", path) >= (int)sizeof(temp)) return -1;
    (void)unlink(temp);
    if (write_file(temp, text, (size_t)n, 0644)) { (void)unlink(temp); return -1; }
    if (rename(temp, path)) { (void)unlink(temp); return -1; }
    return 0;
}
