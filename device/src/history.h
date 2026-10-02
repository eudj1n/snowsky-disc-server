#ifndef DISC_HISTORY_H
#define DISC_HISTORY_H

#include "database.h"
#include <stddef.h>

/* Play history of our own (stock V2.57 never fills RECORD_SONG). A read-only
 * observer watches which music file the player process holds open and whether
 * its read position advances (/proc/<pid>/fd and fdinfo; it sounds while the
 * position moved within the last 10 s, as stock reads ahead in 32 KiB steps); a
 * file that has sounded 30 s, or half its bytes after at least 5 s, is recorded once, with
 * the context it was started from as stock's queue table shows it (it and the
 * skip rule run from the service's main loop). A CUE
 * image counts per track (its title recorded), the track taken from stock's
 * MEMORY_PLAY or estimated from the read position. Records
 * go into the plays table of the service's database on the music card
 * (combined-008), only while the player owns the card, and the oldest are
 * dropped past HISTORY_KEEP_ROWS. */

typedef struct {
    const char *proc_root;    /* "/proc", or a fake tree in host tests */
    const char *process;      /* the player's process name, "mq_player" */
    const char *music_root;   /* the card mount, "/tmp/sdcard" */
    const char *song_db;      /* stock's song.db (queue table), read-only */
    disc_database *database;  /* "<card>/.disc/disc.db", shared with the routes */
    unsigned interval_ms;
    /* Called from the observer thread once per file (or per track of a CUE
     * image, with its title), when its position has advanced twice (it sounds,
     * not only opened or read ahead): the skip rule. A nonzero result means the
     * service skipped it: it never counts as a play. */
    int (*sounding)(void *arg, const char *path, const char *title);
    void *arg;
} disc_history_config;

typedef struct disc_history disc_history;

/* The observer has no thread of its own (combined-008 headroom): the service's
 * main loop polls it, and each poll observes once the interval has passed. */
int disc_history_start(disc_history **history, const disc_history_config *config);
void disc_history_poll(disc_history *history);
void disc_history_stop(disc_history *history);
/* The newest records as {"records":[...],"truncated":bool}, oldest first,
 * each row checked so an edited database never yields invalid JSON. Returns
 * an HTTP status; on 200 *json is malloc'd. */
int disc_history_json(disc_database *database, char **json, size_t *length);
/* A play in a page that played a card file itself (combined-009): the body
 * {"path","seconds","title"?,"ctx"?} is checked (an existing music file on the
 * card, 1..86400 seconds and no longer than the file, the observer's context
 * bounds) and written with source "browser" and the service's clock, started
 * `seconds` before now. Returns an HTTP status; on 201 *json is malloc'd,
 * otherwise *problem names the refusal. */
int disc_history_add_json(disc_database *database, const char *music_root, const char *body, size_t length, long long now,
                          char **json, size_t *json_length, const char **problem);
/* 1 when the player process holds the file at path, or a file below the
 * folder at path, open (the trash refuses to move it). */
int disc_history_player_holds(const char *proc_root, const char *process, const char *path);
/* Order-independent fingerprint of a set of card paths: the sum of their
 * FNV-1a 64-bit hashes; the page computes the same for albums, playlists... */
unsigned long long disc_history_path_hash(const char *path);

#endif
