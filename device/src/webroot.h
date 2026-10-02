#ifndef DISC_WEBROOT_H
#define DISC_WEBROOT_H
/* Apps as plain folders (combined-009): the card's <card>/Apps/<App>/ holds
 * an app's index.html and files, served read-only with the card's mount
 * checks; the image may carry its own copy of the default app. Names inside
 * an app are [A-Za-z0-9._-], a few folders deep; nothing is followed through
 * a link. The same confinement opens the catalogs' folders. */
#include <stddef.h>

#define DISC_WEBROOT_MAX_FILE (4 * 1024 * 1024)
#define DISC_APP_NAME_MAX 64
/* The app served at "/". */
#define DISC_DEFAULT_APP "Disc Player"

typedef struct {
    const char *root;    /* the folder of apps (<card>/Apps), or of the image's app's parent */
    const char *mount;   /* the card's mount point and its block device, both or neither */
    const char *source;
} disc_webroot;

typedef struct {
    int fd;
    size_t size;
    const char *mime;
    /* 1: a content-hashed name, cached for good; 0: revalidated; -1: an HTML document, never cached. */
    int immutable;
    /* "gzip" when the file's pre-compressed twin (<name>.gz) is served. */
    const char *encoding;
    /* A text type that has such twins: the response depends on Accept-Encoding. */
    int varies;
} disc_web_asset;

/* 1 when the name can be an app's folder: UTF-8 of at most 64 bytes without
 * control characters, / or \, not starting with a dot or a space. */
int disc_app_name_ok(const char *name);
/* Opens a file of the app (path "" is index.html). With accept_gzip a text
 * file is served from its "<name>.gz" twin when the app has one. A zero
 * result leaves the caller's fallback or 404 in control; on 1 the caller owns
 * asset->fd. */
int disc_app_open(const disc_webroot *config, const char *app, const char *path, int accept_gzip, disc_web_asset *asset);
/* Reads a small file of the app's root (origins.json, app.json) into out;
 * returns its length, or -1 when there is none. */
int disc_app_small_file(const disc_webroot *config, const char *app, const char *name, char *out, size_t capacity);
/* Calls visit for each app folder that has an index.html, by name. */
void disc_apps_list(const disc_webroot *config, void (*visit)(void *arg, const char *app), void *arg);
/* Opens a bounded regular file in a folder (a catalog folder), with the
 * card's mount checks when the folder is on the card. */
int disc_folder_open(const disc_webroot *config, const char *folder, const char *name, disc_web_asset *asset);
int disc_webroot_marker(const disc_webroot *config, const char *path, const char *expected);
int disc_webroot_available(const disc_webroot *config);

#endif
