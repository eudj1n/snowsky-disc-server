#ifndef DISC_APPS_H
#define DISC_APPS_H
/* Installing and removing apps on the card through the application manager
 * (owner, 2026-10-02). The rules are scripts/app_bundle.py's: one top folder
 * named after the app; names [A-Za-z0-9_-][A-Za-z0-9_.-]{0,79}, at most six
 * deep; the served types only; 4 MiB a file, 32 MiB and 512 files an app;
 * gzip twins of text files beside them; no reviewed catalog; index.html at
 * the root; no inline script or style, no root-absolute reference. */
#include <stddef.h>
#include "webroot.h"

#define DISC_APP_MAX_FILE DISC_WEBROOT_MAX_FILE
#define DISC_APP_MAX_TOTAL (32LL * 1024 * 1024)
#define DISC_APP_MAX_FILES 512
#define DISC_APP_MAX_DEPTH 6
/* A zip upload: the files' bound plus the zip's own records. */
#define DISC_APP_ZIP_MAX (40LL * 1024 * 1024)
/* Room the card keeps after an installation for the service's database. */
#define DISC_APP_RESERVE (8LL * 1024 * 1024)

enum { DISC_APP_OK = 0, DISC_APP_REFUSED = -1, DISC_APP_NO_ROOM = -2, DISC_APP_FAILED = -3 };

typedef struct {
    char name[DISC_APP_NAME_MAX + 1];
    int files;
    long long bytes;
} disc_app_result;

/* Installs the app a zip holds into apps_root (<card>/Apps): every entry is checked and
 * unpacked beside (.<name>.installing), the copy is checked, then swapped in for the
 * app's previous folder, which goes only then. problem names the first refusal. */
int disc_app_install_zip(const char *apps_root, const char *zip_path, disc_app_result *out, char *problem, size_t capacity);
/* Removes an installed app's folder (renamed aside first, then deleted). */
int disc_app_remove(const char *apps_root, const char *name, char *problem, size_t capacity);
#endif
