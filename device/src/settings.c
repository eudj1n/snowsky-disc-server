/* The service's settings file: known keys, strict values, never executed. */
#define _XOPEN_SOURCE 700
#define _DARWIN_C_SOURCE
#include "settings.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

int disc_port_ok(int port) {
    return port >= 1024 && port <= 65535 && port != 12100 && port != 12101 && port != 12103;
}

static int port_value(const char *v) {
    if (!*v || strlen(v) > 5 || strspn(v, "0123456789") != strlen(v) || *v == '0') return 0;
    return atoi(v);
}

static int refuse(char *problem, size_t capacity, const char *text, int line) {
    if (problem) snprintf(problem, capacity, "server.env line %d: %s", line, text);
    return -1;
}

int disc_settings_read(const char *path, disc_settings *out, char *problem, size_t capacity) {
    memset(out, 0, sizeof(*out));
    if (problem && capacity) *problem = 0;
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return errno == ENOENT ? 0 : refuse(problem, capacity, "unreadable", 0);
    struct stat st;
    char text[DISC_SETTINGS_MAX + 1];
    ssize_t n = -1;
    if (!fstat(fd, &st) && S_ISREG(st.st_mode) && st.st_size <= DISC_SETTINGS_MAX) n = read(fd, text, (size_t)st.st_size);
    close(fd);
    if (n < 0 || n != st.st_size) return refuse(problem, capacity, "not a small regular file", 0);
    text[n] = 0;
    if (memchr(text, 0, (size_t)n)) return refuse(problem, capacity, "not text", 0);
    disc_settings read = {0};
    int line = 0;
    for (char *p = text; *p; ) {
        char *end = strchr(p, '\n');
        if (end) *end = 0;
        line++;
        size_t len = strlen(p);
        if (len && p[len - 1] == '\r') p[--len] = 0;
        if (len && p[0] != '#') {
            char *eq = strchr(p, '=');
            if (!eq) return refuse(problem, capacity, "not KEY=VALUE", line);
            *eq = 0;
            const char *key = p, *value = eq + 1;
            if (!strcmp(key, "PORT")) {
                if (read.port || !disc_port_ok(read.port = port_value(value))) return refuse(problem, capacity, "PORT must be 1024-65535 and not stock's, once", line);
            } else if (!strcmp(key, "MANAGER_PORT")) {
                if (read.manager_port || !disc_port_ok(read.manager_port = port_value(value))) return refuse(problem, capacity, "MANAGER_PORT must be 1024-65535 and not stock's, once", line);
            } else if (!strcmp(key, "DEFAULT_APP")) {
                if (read.default_app[0] || !disc_app_name_ok(value)) return refuse(problem, capacity, "DEFAULT_APP must be an app's name, once", line);
                snprintf(read.default_app, sizeof(read.default_app), "%s", value);
            } else return refuse(problem, capacity, "unknown key", line);
        }
        if (!end) break;
        p = end + 1;
    }
    int port = read.port ? read.port : DISC_DEFAULT_PORT, manager = read.manager_port ? read.manager_port : DISC_DEFAULT_MANAGER_PORT;
    if (port == manager) return refuse(problem, capacity, "PORT and MANAGER_PORT must differ", line);
    *out = read;
    return 1;
}

int disc_settings_write(const char *path, const disc_settings *settings) {
    char tmp[512], text[DISC_SETTINGS_MAX];
    int n = snprintf(text, sizeof(text), "# The DISC server's settings (KEY=VALUE; read at start, never executed).\n");
    if (settings->port) n += snprintf(text + n, sizeof(text) - (size_t)n, "PORT=%d\n", settings->port);
    if (settings->manager_port) n += snprintf(text + n, sizeof(text) - (size_t)n, "MANAGER_PORT=%d\n", settings->manager_port);
    if (settings->default_app[0]) n += snprintf(text + n, sizeof(text) - (size_t)n, "DEFAULT_APP=%s\n", settings->default_app);
    if (n <= 0 || (size_t)n >= sizeof(text) || snprintf(tmp, sizeof(tmp), "%s.new", path) >= (int)sizeof(tmp)) return -1;
    (void)unlink(tmp);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0644);
    if (fd < 0) return -1;
    int ok = write(fd, text, (size_t)n) == n && !fsync(fd);
    close(fd);
    if (!ok || rename(tmp, path)) { unlink(tmp); return -1; }
    char folder[512];
    snprintf(folder, sizeof(folder), "%s", path);
    char *slash = strrchr(folder, '/');
    if (slash && slash != folder) {
        *slash = 0;
        int dir = open(folder, O_RDONLY | O_CLOEXEC);
        if (dir >= 0) { (void)fsync(dir); close(dir); }
    }
    return 0;
}
