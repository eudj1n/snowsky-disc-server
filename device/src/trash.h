#ifndef DISC_TRASH_H
#define DISC_TRASH_H
/* The trash (combined-008): a file or folder on the music card is moved into
 * <card>/.disc/trash/<id>/ (a rename on the same card, reversible) instead of
 * being deleted. Every name moved there has its dots escaped and every file
 * the ".trashed" suffix, so stock's scanner, which indexes any name containing
 * an audio extension, inside .disc too, never lists it; the manifest
 * (original path, kind, size, time) is the trash table of the service's
 * database. Restore puts it back unless its name was taken again; purge and
 * empty delete for good. Stock's own deletion stays denied. */
#include "database.h"
#include <stddef.h>

#define DISC_TRASH_SUFFIX ".trashed"
#define DISC_TRASH_MAX_ENTRIES 10000 /* files and folders below one trashed folder */

typedef struct {
    const char *card;       /* the music card mount, "/tmp/sdcard" */
    const char *folder;     /* "<card>/.disc/trash" */
    disc_database *database;
    const char *proc_root;  /* the player's open files ... */
    const char *process;    /* ... or NULL: no player to ask */
} disc_trash;

/* Every operation returns an HTTP status; on 200 *json is malloc'd, otherwise
 * *problem names the refusal. */
int disc_trash_move(const disc_trash *trash, const char *path, long long now, char **json, size_t *length, const char **problem);
int disc_trash_list(const disc_trash *trash, char **json, size_t *length, const char **problem);
int disc_trash_restore(const disc_trash *trash, long long id, char **json, size_t *length, const char **problem);
int disc_trash_purge(const disc_trash *trash, long long id, char **json, size_t *length, const char **problem);
int disc_trash_empty(const disc_trash *trash, char **json, size_t *length, const char **problem);
/* macOS leftovers on the card (AppleDouble "._" files, .DS_Store, and at the
 * root .Trashes, .Spotlight-V100, .fseventsd, .TemporaryItems): the report,
 * and a move of all of them into one trash entry (kind "leftovers") that a
 * restore puts back file by file. */
int disc_trash_leftovers(const disc_trash *trash, char **json, size_t *length, const char **problem);
int disc_trash_leftovers_move(const disc_trash *trash, long long now, char **json, size_t *length, const char **problem);

#endif
