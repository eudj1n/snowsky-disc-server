#ifndef DISC_LISTS_H
#define DISC_LISTS_H
/* M3U lists of the service's own (combined-009, docs/m3u.md): stock plays an
 * .m3u file by its path through folder play and reads it when it is played,
 * so a list of any tracks becomes a queue on the player with one file write.
 * Each list is <folder>/<name>.m3u, UTF-8 with a BOM (so the player's screen
 * decodes it right), #EXTM3U and one entry per line relative to the card root,
 * as stock needs them. Every entry is an existing
 * regular music file on the card reached without links, dot or hidden parts
 * (the checks of the media routes). A write goes to a temporary file that is
 * synced, read back and renamed over the list, so stock never reads half a
 * list. The service keeps no definitions: what a list holds is the client's. */
#include <stddef.h>

#define DISC_LISTS_MAX 200             /* lists in the folder */
#define DISC_LISTS_MAX_ENTRIES 5000    /* entries per list (5,000 queued whole on the guest) */
#define DISC_LISTS_MAX_BODY (2u * 1024u * 1024u)
#define DISC_LISTS_NAME_MAX 96         /* bytes of UTF-8, without ".m3u" */

typedef struct {
    const char *card;    /* the music card mount, "/tmp/sdcard" */
    const char *folder;  /* where the lists live, "<card>/.disc/playlists" */
} disc_lists;

/* 1 when the name can be a list: UTF-8 without control characters, none of
 * / \ : * ? " < > |, no leading dot or space, no trailing dot or space. */
int disc_lists_name_ok(const char *name);
/* Every operation returns an HTTP status; on 200 or 201 *json is malloc'd,
 * otherwise *problem names the refusal. */
int disc_lists_index(const disc_lists *lists, char **json, size_t *length, const char **problem);
int disc_lists_read(const disc_lists *lists, const char *name, char **json, size_t *length, const char **problem);
/* body: {"entries":["<card>/A/01.flac", ...]} (absolute card paths). */
int disc_lists_write(const disc_lists *lists, const char *name, const char *body, size_t body_length,
                     char **json, size_t *length, const char **problem);
int disc_lists_delete(const disc_lists *lists, const char *name, char **json, size_t *length, const char **problem);

#endif
