#ifndef DISC_SETTINGS_H
#define DISC_SETTINGS_H
/* The service's settings file (owner, 2026-10-02): $DISC_BOOT_DATA/server.env,
 * inside the package's own data so it is there when the service starts (the
 * card comes later). KEY=VALUE lines, known keys only, read as data and never
 * executed; "#" starts a comment line. A file that breaks a rule is refused
 * whole and the defaults apply. */
#include <stddef.h>
#include "webroot.h"

#define DISC_DEFAULT_PORT 7870
#define DISC_DEFAULT_MANAGER_PORT 7871
#define DISC_SETTINGS_MAX 4096

typedef struct {
    int port, manager_port;                    /* 0 when the file does not set it */
    char default_app[DISC_APP_NAME_MAX + 1];   /* "" when the file does not set it */
} disc_settings;

/* A port the service may take: 1024-65535 and none of stock's (12100, 12101, 12103). */
int disc_port_ok(int port);
/* 0 when there is no file, 1 when it was read, -1 when it was refused (problem says why). */
int disc_settings_read(const char *path, disc_settings *out, char *problem, size_t capacity);
/* Writes the keys that are set, beside the file first, then renames it into place. */
int disc_settings_write(const char *path, const disc_settings *settings);
#endif
