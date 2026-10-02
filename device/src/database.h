#ifndef DISC_DATABASE_H
#define DISC_DATABASE_H

#include "sqlite3.h"
#include <pthread.h>
#include <sys/types.h>

/* The service's own database on the music card, <card>/.disc/disc.db
 * (combined-008). It is opened for one operation and closed right after, so
 * the service never holds the card open when stock unmounts it for USB
 * storage mode; the process uses one connection at a time. Rollback journal
 * with full sync, as FAT and exFAT cards need. When the file is not the one
 * the service last closed (the card came back, or the file was changed
 * elsewhere), a quick check runs before use; a damaged file, or one carrying
 * triggers or views the service never creates, is moved to disc.db.damaged,
 * and a new file is created when something is written. */

/* PRAGMA user_version of the schema this service writes. */
#define DISC_DATABASE_VERSION 5

typedef struct {
    const char *path;               /* "<card>/.disc/disc.db"; NULL when not configured */
    int (*card_owned)(void *arg);   /* the player owns the card (it is mounted) */
    void *arg;
    pthread_mutex_t lock;
    /* The file as the service last closed it. */
    int known;
    /* How writes of plays went (combined-009 diagnostics): failures, the last
     * failure and success (Unix time) and the last failure's reason. */
    long long write_failures, write_failed_at, write_succeeded_at;
    const char *write_reason;
    int write_failing;  /* the last write failed */
    dev_t device;
    ino_t inode;
    off_t size;
    long long modified_ns;
} disc_database;

enum {
    DISC_DATABASE_OK = 0,
    DISC_DATABASE_ABSENT,    /* no file yet (and none was to be created) */
    DISC_DATABASE_CARD_AWAY, /* the card is not mounted for the player */
    DISC_DATABASE_NEWER,     /* written by a newer service: left untouched */
    DISC_DATABASE_FAILED     /* input/output error, or not a file */
};

void disc_database_init(disc_database *database, const char *path, int (*card_owned)(void *), void *arg);
/* Opens the database for one operation and holds the process-wide lock until
 * disc_database_close. With create, a missing or damaged file is created with
 * the current schema. Returns DISC_DATABASE_OK and *db, or a reason above. */
int disc_database_open(disc_database *database, int create, sqlite3 **db);
void disc_database_close(disc_database *database, sqlite3 *db);

/* A play's write succeeded (reason NULL) or failed for the reason (a static
 * text: "card away", "newer schema", "card full", "input/output", "failed"),
 * so the diagnostics can say why the history stands still. */
void disc_database_write_outcome(disc_database *database, const char *reason);
/* The reason for a failed open (a DISC_DATABASE_* value) or statement (an SQLite code). */
const char *disc_database_reason(int open_status, int sqlite_rc);
typedef struct { long long failures, failed_at, succeeded_at; const char *reason; int failing; } disc_database_writes;
void disc_database_writes_read(disc_database *database, disc_database_writes *writes);

/* What the diagnostics show of the database: its state (a DISC_DATABASE_*
 * value), schema version, file size and rows per table (-1 when unknown). */
typedef struct { int state; long long schema, bytes, plays, records, trash; } disc_database_facts;
void disc_database_facts_read(disc_database *database, disc_database_facts *facts);

#endif
