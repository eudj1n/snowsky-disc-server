#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#include "webroot.h"
#include "framing.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif

#define APP_DEPTH 6

/* A name inside an app: [A-Za-z0-9._-], not hidden (macOS leaves "._" files beside copied ones). */
static int token_name(const char *s, size_t n) {
    if (!n || n > 80 || s[0] == '.') return 0;
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.')) return 0;
    }
    return 1;
}

int disc_app_name_ok(const char *name) {
    size_t n = name ? strlen(name) : 0;
    if (!n || n > DISC_APP_NAME_MAX || name[0] == '.' || name[0] == ' ' || !disc_utf8((const unsigned char *)name, n)) return 0;
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)name[i];
        if (c < 0x20 || c == 0x7f || c == '/' || c == '\\') return 0;
    }
    return 1;
}

static int mounted(const disc_webroot *cfg, dev_t *device) {
    if (!cfg->mount && !cfg->source) return 1;
    if (!cfg->mount || !cfg->source || !cfg->root) return 0;
    size_t len = strlen(cfg->mount);
    if (strncmp(cfg->root, cfg->mount, len) || cfg->root[len] != '/' ||
        strstr(cfg->root + len + 1, "..")) return 0;
    FILE *f = fopen("/proc/mounts", "r");
    if (!f) return 0;
    char line[1024], source[256], target[256], type[64];
    int count = 0;
    while (fgets(line, sizeof(line), f)) {
        if (sscanf(line, "%255s %255s %63s", source, target, type) == 3 &&
            !strcmp(source, cfg->source) && !strcmp(target, cfg->mount) &&
            (!strcmp(type, "vfat") || !strcmp(type, "exfat") ||
             !strcmp(type, "ntfs") || !strcmp(type, "fuseblk"))) count++;
    }
    fclose(f);
    if (count != 1) return 0;
    struct stat st;
    if (stat(cfg->mount, &st) || !S_ISDIR(st.st_mode)) return 0;
    *device = st.st_dev;
    return 1;
}

static int directory(int parent, const char *name) {
    int fd = openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    struct stat st;
    if (fd < 0) return -1;
    if (fstat(fd, &st) || !S_ISDIR(st.st_mode)) { close(fd); return -1; }
    return fd;
}

static int regular(int parent, const char *name, size_t limit, size_t *size) {
    int fd = openat(parent, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    struct stat st;
    if (fd < 0) return -1;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size < 0 ||
        (unsigned long long)st.st_size > limit) { close(fd); return -1; }
    *size = (size_t)st.st_size;
    return fd;
}

static int exact_file(int dir, const char *name, char *out, size_t capacity) {
    size_t size;
    int fd = regular(dir, name, capacity - 1, &size);
    if (fd < 0) return -1;
    size_t used = 0;
    while (used < size) {
        ssize_t n = read(fd, out + used, size - used);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { close(fd); return -1; }
        used += (size_t)n;
    }
    close(fd); out[used] = 0;
    return (int)used;
}

static const char *mime(const char *name) {
    const char *dot = strrchr(name, '.');
    if (!dot) return NULL;
    if (!strcmp(dot, ".html")) return "text/html; charset=utf-8";
    if (!strcmp(dot, ".css")) return "text/css; charset=utf-8";
    if (!strcmp(dot, ".js") || !strcmp(dot, ".mjs")) return "text/javascript; charset=utf-8";
    if (!strcmp(dot, ".json") || !strcmp(dot, ".map")) return "application/json";
    if (!strcmp(dot, ".webmanifest")) return "application/manifest+json";
    if (!strcmp(dot, ".txt")) return "text/plain; charset=utf-8";
    if (!strcmp(dot, ".svg")) return "image/svg+xml";
    if (!strcmp(dot, ".png")) return "image/png";
    if (!strcmp(dot, ".jpg") || !strcmp(dot, ".jpeg")) return "image/jpeg";
    if (!strcmp(dot, ".gif")) return "image/gif";
    if (!strcmp(dot, ".webp")) return "image/webp";
    if (!strcmp(dot, ".ico")) return "image/x-icon";
    if (!strcmp(dot, ".woff2")) return "font/woff2";
    if (!strcmp(dot, ".woff")) return "font/woff";
    if (!strcmp(dot, ".ttf")) return "font/ttf";
    if (!strcmp(dot, ".wasm")) return "application/wasm";
    return NULL;
}

/* Text types a build may ship pre-compressed beside the file (<name>.gz). */
static int compressible(const char *type) {
    return !strncmp(type, "text/", 5) || !strcmp(type, "application/json") || !strcmp(type, "application/manifest+json") ||
           !strcmp(type, "image/svg+xml") || !strcmp(type, "application/wasm");
}

/* A bundler's content-hashed name ("index-Ab12Cd34.js"): the part after the stem's last
 * dash has at least 8 characters of [A-Za-z0-9_-] and a digit or a capital among them. */
static int hashed(const char *name) {
    const char *dot = strrchr(name, '.'), *dash = NULL;
    for (const char *p = name; p < dot; p++) if (*p == '-') dash = p;
    if (!dot || !dash || dot - dash - 1 < 8) return 0;
    int mixed = 0;
    for (const char *p = dash + 1; p < dot; p++) {
        char c = *p;
        if ((c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z')) mixed = 1;
        else if (!((c >= 'a' && c <= 'z') || c == '_' || c == '-')) return 0;
    }
    return mixed;
}

static int open_path(int folder, const char *path, int accept_gzip, disc_web_asset *asset) {
    if (!*path || strlen(path) > 512) return 0;
    int dir = folder, owned = -1, depth = 0;
    const char *part = path;
    for (;;) {
        const char *slash = strchr(part, '/');
        size_t n = slash ? (size_t)(slash - part) : strlen(part);
        if (!token_name(part, n) || ++depth > APP_DEPTH) break;
        char name[81]; memcpy(name, part, n); name[n] = 0;
        if (!slash) {
            const char *type = mime(name);
            if (!type) break;
            size_t size;
            int fd = -1;
            asset->encoding = NULL;
            asset->varies = compressible(type);
            /* The twin is only an alternative encoding of the same file: it is
             * never addressed by URL and needs the original beside it. */
            struct stat original;
            if (accept_gzip && asset->varies && n + 3 <= 80 &&
                !fstatat(dir, name, &original, AT_SYMLINK_NOFOLLOW) && S_ISREG(original.st_mode)) {
                char packed[84];
                memcpy(packed, name, n); memcpy(packed + n, ".gz", 4);
                fd = regular(dir, packed, DISC_WEBROOT_MAX_FILE, &size);
                if (fd >= 0) asset->encoding = "gzip";
            }
            if (fd < 0) fd = regular(dir, name, DISC_WEBROOT_MAX_FILE, &size);
            if (fd < 0) break;
            asset->fd = fd; asset->size = size; asset->mime = type;
            asset->immutable = !strncmp(type, "text/html", 9) ? -1 : hashed(name);
            if (owned >= 0) close(owned);
            return 1;
        }
        int next = directory(dir, name);
        if (owned >= 0) close(owned);
        if (next < 0) return 0;
        owned = dir = next; part = slash + 1;
    }
    if (owned >= 0) close(owned);
    return 0;
}

/* The app's folder under the root, after the mount checks; -1 when there is none. */
static int app_folder(const disc_webroot *cfg, const char *app, dev_t *device) {
    if (!cfg || !cfg->root || !disc_app_name_ok(app)) return -1;
    *device = 0;
    if (!mounted(cfg, device)) return -1;
    int root = open(cfg->root, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (root < 0) return -1;
    struct stat st;
    if (fstat(root, &st) || !S_ISDIR(st.st_mode) || ((cfg->mount || cfg->source) && st.st_dev != *device)) { close(root); return -1; }
    int folder = directory(root, app);
    close(root);
    return folder;
}

int disc_app_open(const disc_webroot *cfg, const char *app, const char *path, int accept_gzip, disc_web_asset *asset) {
    if (!asset || !path) return 0;
    asset->fd = -1; asset->encoding = NULL; asset->varies = 0; asset->immutable = 0;
    dev_t device;
    int folder = app_folder(cfg, app, &device);
    if (folder < 0) return 0;
    int found = open_path(folder, *path ? path : "index.html", accept_gzip, asset);
    close(folder);
    if (found && (cfg->mount || cfg->source) && !mounted(cfg, &device)) { close(asset->fd); asset->fd = -1; return 0; }
    return found;
}

int disc_app_small_file(const disc_webroot *cfg, const char *app, const char *name, char *out, size_t capacity) {
    dev_t device;
    int folder = app_folder(cfg, app, &device);
    if (folder < 0) return -1;
    int n = exact_file(folder, name, out, capacity);
    close(folder);
    return n;
}

void disc_apps_list(const disc_webroot *cfg, void (*visit)(void *arg, const char *app), void *arg) {
    dev_t device = 0;
    if (!cfg || !cfg->root || !mounted(cfg, &device)) return;
    int root = open(cfg->root, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    DIR *d = root < 0 ? NULL : fdopendir(root);
    if (!d) { if (root >= 0) close(root); return; }
    struct dirent *e;
    int count = 0;
    while ((e = readdir(d)) && count < 64) {
        if (!disc_app_name_ok(e->d_name)) continue;
        int folder = directory(dirfd(d), e->d_name);
        if (folder < 0) continue;
        size_t size;
        int index = regular(folder, "index.html", DISC_WEBROOT_MAX_FILE, &size);
        close(folder);
        if (index < 0) continue;
        close(index);
        visit(arg, e->d_name);
        count++;
    }
    closedir(d);
}

int disc_folder_open(const disc_webroot *cfg, const char *folder, const char *name, disc_web_asset *asset) {
    if (!folder || !name || !asset || strchr(name, '/') || !token_name(name, strlen(name))) return 0;
    asset->fd = -1; asset->encoding = NULL; asset->varies = 0; asset->immutable = 0;
    /* A folder on the card is read only while the player owns the card. */
    int on_card = cfg && cfg->mount && !strncmp(folder, cfg->mount, strlen(cfg->mount)) && folder[strlen(cfg->mount)] == '/';
    dev_t device = 0;
    if (on_card && (strstr(folder, "/..") || !mounted(cfg, &device))) return 0;
    int dir = open(folder, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dir < 0) return 0;
    struct stat st;
    size_t size;
    int fd = -1;
    if (!fstat(dir, &st) && S_ISDIR(st.st_mode) && (!on_card || st.st_dev == device))
        fd = regular(dir, name, DISC_WEBROOT_MAX_FILE, &size);
    close(dir);
    if (fd < 0) return 0;
    if (on_card && !mounted(cfg, &device)) { close(fd); return 0; }
    asset->fd = fd; asset->size = size; asset->mime = mime(name) ? mime(name) : "application/octet-stream";
    return 1;
}

int disc_webroot_marker(const disc_webroot *cfg, const char *path, const char *expected) {
    if (!cfg || !path || !expected || strlen(expected) > 64) return 0;
    const char *slash = strrchr(path, '/');
    if (!slash || !token_name(slash + 1, strlen(slash + 1))) return 0;
    char parent[256];
    size_t length = (size_t)(slash - path);
    if (!length || length >= sizeof(parent)) return 0;
    memcpy(parent, path, length); parent[length] = 0;
    /* The card root or a folder inside it (the service's .disc since combined-008). */
    if (cfg->mount) {
        size_t m = strlen(cfg->mount);
        if (strncmp(parent, cfg->mount, m) || (parent[m] && parent[m] != '/') || strstr(parent + m, "/..")) return 0;
    }
    dev_t device = 0;
    if (!mounted(cfg, &device)) return 0;
    int dir = open(parent, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dir < 0) return 0;
    struct stat st;
    if (fstat(dir, &st) || !S_ISDIR(st.st_mode) ||
        ((cfg->mount || cfg->source) && st.st_dev != device)) { close(dir); return 0; }
    char actual[65];
    int n = exact_file(dir, slash + 1, actual, sizeof(actual));
    close(dir);
    return n == (int)strlen(expected) && !memcmp(actual, expected, (size_t)n) &&
           mounted(cfg, &device);
}

int disc_webroot_available(const disc_webroot *cfg) {
    dev_t device = 0;
    return cfg && mounted(cfg, &device);
}
