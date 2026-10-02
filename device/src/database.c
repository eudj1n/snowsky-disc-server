#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#include "database.h"
#include "log.h"
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <sys/stat.h>
#include <unistd.h>

#define TEXT(x) #x
#define NUMBER(x) TEXT(x)

/* Schema 1: the play history (history.c writes it, GET /api/history reads
 * it). Schema 2: the store's records (store.c), one row per record of a
 * collection the card catalog declares, its value canonical JSON. Schema 3:
 * the trash manifest (trash.c), one row per file or folder moved into
 * .disc/trash/<id>, state "moving" until the move completed; ids are never
 * reused, so a client's id cannot name a later entry. Schema 4: plays.title,
 * the CUE track a play was of (NULL for a whole file). Schema 5: plays.source,
 * "browser" for a play in a page, NULL for the player. Column names
 * are part of the published contract: reviewed queries read them. Every
 * statement is idempotent, so an older file is brought up to date in place. */
static const char SCHEMA[] =
    "CREATE TABLE IF NOT EXISTS plays("
    "id INTEGER PRIMARY KEY,"
    "started_at INTEGER NOT NULL,"
    "path TEXT NOT NULL,"
    "heard_seconds INTEGER NOT NULL,"
    "queue_type INTEGER,"
    "queue_count INTEGER NOT NULL,"
    "queue_hash TEXT,"
    "queue_album TEXT,"
    "queue_artist TEXT,"
    "queue_genre TEXT,"
    "queue_folder TEXT,"
    "title TEXT,"
    "source TEXT) STRICT;"
    "CREATE INDEX IF NOT EXISTS plays_path ON plays(path);"
    "CREATE TABLE IF NOT EXISTS records("
    "id INTEGER PRIMARY KEY,"
    "collection TEXT NOT NULL,"
    "key TEXT NOT NULL,"
    "value TEXT NOT NULL,"
    "updated_at INTEGER NOT NULL,"
    "UNIQUE(collection, key)) STRICT;"
    "CREATE TABLE IF NOT EXISTS trash("
    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "original TEXT NOT NULL,"
    "kind TEXT NOT NULL,"
    "bytes INTEGER NOT NULL,"
    "files INTEGER NOT NULL,"
    "trashed_at INTEGER NOT NULL,"
    "state TEXT NOT NULL) STRICT;";

void disc_database_init(disc_database *d, const char *path, int (*card_owned)(void *), void *arg) {
    memset(d, 0, sizeof(*d));
    d->path = path;
    d->card_owned = card_owned;
    d->arg = arg;
    pthread_mutex_init(&d->lock, NULL);
}

static long long modified_ns(const struct stat *st) {
#ifdef __APPLE__
    return (long long)st->st_mtimespec.tv_sec * 1000000000LL + st->st_mtimespec.tv_nsec;
#else
    return (long long)st->st_mtim.tv_sec * 1000000000LL + st->st_mtim.tv_nsec;
#endif
}

static int known_file(const disc_database *d, const struct stat *st) {
    return d->known && d->device == st->st_dev && d->inode == st->st_ino && d->size == st->st_size &&
           d->modified_ns == modified_ns(st);
}

static void remember(disc_database *d) {
    struct stat st;
    d->known = !lstat(d->path, &st) && S_ISREG(st.st_mode);
    if (!d->known) return;
    d->device = st.st_dev;
    d->inode = st.st_ino;
    d->size = st.st_size;
    d->modified_ns = modified_ns(&st);
}

static long long scalar(sqlite3 *db, const char *sql, int *rc) {
    sqlite3_stmt *stmt = NULL;
    long long value = -1;
    *rc = sqlite3_prepare_v2(db, sql, -1, &stmt, NULL);
    if (*rc == SQLITE_OK) {
        *rc = sqlite3_step(stmt);
        if (*rc == SQLITE_ROW) { value = sqlite3_column_int64(stmt, 0); *rc = SQLITE_OK; }
        else if (*rc == SQLITE_DONE) *rc = SQLITE_OK;
    }
    sqlite3_finalize(stmt);
    return value;
}

/* A file from a removable card is untrusted: no schema functions with side
 * effects, no writes to the schema itself, rollback journal and full sync. */
static int configure(sqlite3 *db) {
    sqlite3_db_config(db, SQLITE_DBCONFIG_DEFENSIVE, 1, NULL);
    sqlite3_db_config(db, SQLITE_DBCONFIG_TRUSTED_SCHEMA, 0, NULL);
    sqlite3_busy_timeout(db, 1000);
    int rc = sqlite3_exec(db, "PRAGMA cell_size_check=ON; PRAGMA synchronous=FULL;", NULL, NULL, NULL);
    /* journal_mode reads the header: a file that is not a database fails here. */
    if (rc == SQLITE_OK) rc = sqlite3_exec(db, "PRAGMA journal_mode=DELETE;", NULL, NULL, NULL);
    return rc;
}

/* PRAGMA quick_check, and no triggers or views (the service creates none). */
static int verify(sqlite3 *db) {
    sqlite3_stmt *stmt = NULL;
    int rc = sqlite3_prepare_v2(db, "PRAGMA quick_check(1)", -1, &stmt, NULL);
    if (rc == SQLITE_OK) {
        rc = sqlite3_step(stmt);
        if (rc == SQLITE_ROW) {
            const unsigned char *result = sqlite3_column_text(stmt, 0);
            rc = result && !strcmp((const char *)result, "ok") ? SQLITE_OK : SQLITE_CORRUPT;
        }
    }
    sqlite3_finalize(stmt);
    if (rc != SQLITE_OK) return rc;
    long long foreign = scalar(db, "SELECT count(*) FROM sqlite_schema WHERE type IN ('trigger','view')", &rc);
    return rc != SQLITE_OK ? rc : foreign ? SQLITE_CORRUPT : SQLITE_OK;
}

/* Schema 4 (combined-008): a play of a CUE track names it. Files of schema 1
 * to 3 get the column before the idempotent statements run. */
static const char UPGRADE_4[] = "ALTER TABLE plays ADD COLUMN title TEXT;";
/* Schema 5 (combined-009): where a play sounded, NULL for the player and
 * "browser" for a page playing card files itself. */
static const char UPGRADE_5[] = "ALTER TABLE plays ADD COLUMN source TEXT;";

static int damaged(int rc) {
    rc &= 0xff;
    return rc == SQLITE_CORRUPT || rc == SQLITE_NOTADB;
}

/* Keeps the last damaged file for diagnostics; its journal cannot help it. */
static void set_aside(const char *path) {
    char aside[512], journal[512];
    snprintf(aside, sizeof(aside), "%s.damaged", path);
    snprintf(journal, sizeof(journal), "%s-journal", path);
    if (rename(path, aside)) unlink(path);
    unlink(journal);
}

static int make_folder(const char *path) {
    char folder[512];
    snprintf(folder, sizeof(folder), "%s", path);
    char *slash = strrchr(folder, '/');
    if (!slash || slash == folder) return 1;
    *slash = 0;
    struct stat st;
    if (!lstat(folder, &st)) return S_ISDIR(st.st_mode);
    return !mkdir(folder, 0755) || errno == EEXIST;
}

static int open_locked(disc_database *d, int create, sqlite3 **out) {
    if (d->card_owned && !d->card_owned(d->arg)) { d->known = 0; return DISC_DATABASE_CARD_AWAY; }
    for (int attempt = 0; attempt < 2; attempt++) {
        struct stat st;
        int exists = !lstat(d->path, &st);
        if (exists && !S_ISREG(st.st_mode)) return DISC_DATABASE_FAILED;
        if (!exists) {
            d->known = 0;
            if (errno != ENOENT) return DISC_DATABASE_FAILED;
            if (!create) return DISC_DATABASE_ABSENT;
            if (!make_folder(d->path)) return DISC_DATABASE_FAILED;
        }
        /* The lstat above refuses a link in the file's place (a FAT or exFAT card
         * has none); SQLITE_OPEN_NOFOLLOW would also refuse links above the card,
         * such as macOS's /var in host tests. */
        sqlite3 *db = NULL;
        int flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_NOMUTEX | (exists ? 0 : SQLITE_OPEN_CREATE);
        int rc = sqlite3_open_v2(d->path, &db, flags, NULL);
        if (rc == SQLITE_OK) rc = configure(db);
        if (rc == SQLITE_OK && exists && !known_file(d, &st)) rc = verify(db);
        long long version = rc == SQLITE_OK ? scalar(db, "PRAGMA user_version", &rc) : -1;
        if (rc == SQLITE_OK && version > DISC_DATABASE_VERSION) {
            sqlite3_close(db);
            disc_log("The card database has schema %lld, newer than %d; left untouched", version, DISC_DATABASE_VERSION);
            return DISC_DATABASE_NEWER;
        }
        if (rc == SQLITE_OK && version < DISC_DATABASE_VERSION) {
            rc = sqlite3_exec(db, "BEGIN IMMEDIATE", NULL, NULL, NULL);
            if (rc == SQLITE_OK && version >= 1 && version < 4) rc = sqlite3_exec(db, UPGRADE_4, NULL, NULL, NULL);
            if (rc == SQLITE_OK && version >= 1 && version < 5) rc = sqlite3_exec(db, UPGRADE_5, NULL, NULL, NULL);
            if (rc == SQLITE_OK) rc = sqlite3_exec(db, SCHEMA, NULL, NULL, NULL);
            if (rc == SQLITE_OK) rc = sqlite3_exec(db, "PRAGMA user_version=" NUMBER(DISC_DATABASE_VERSION) "; COMMIT", NULL, NULL, NULL);
            if (rc != SQLITE_OK) sqlite3_exec(db, "ROLLBACK", NULL, NULL, NULL);
        }
        if (rc == SQLITE_OK) { *out = db; return DISC_DATABASE_OK; }
        sqlite3_close(db);
        d->known = 0;
        if (!exists || !damaged(rc)) { disc_log("The card database could not be opened (SQLite %d)", rc); return DISC_DATABASE_FAILED; }
        disc_log("The card database failed its check (SQLite %d); set aside as disc.db.damaged", rc);
        set_aside(d->path);
        if (!create) return DISC_DATABASE_ABSENT;
    }
    return DISC_DATABASE_FAILED;
}

int disc_database_open(disc_database *d, int create, sqlite3 **db) {
    *db = NULL;
    if (!d || !d->path) return DISC_DATABASE_FAILED;
    pthread_mutex_lock(&d->lock);
    int status = open_locked(d, create, db);
    if (status != DISC_DATABASE_OK) pthread_mutex_unlock(&d->lock);
    return status;
}

void disc_database_facts_read(disc_database *d, disc_database_facts *f) {
    memset(f, 0, sizeof(*f));
    f->schema = f->bytes = f->plays = f->records = f->trash = -1;
    sqlite3 *db = NULL;
    f->state = disc_database_open(d, 0, &db);
    if (f->state != DISC_DATABASE_OK) return;
    int rc;
    f->schema = scalar(db, "PRAGMA user_version", &rc);
    f->plays = scalar(db, "SELECT count(*) FROM plays", &rc);
    f->records = scalar(db, "SELECT count(*) FROM records", &rc);
    f->trash = scalar(db, "SELECT count(*) FROM trash", &rc);
    struct stat st;
    if (!stat(d->path, &st)) f->bytes = (long long)st.st_size;
    disc_database_close(d, db);
}

const char *disc_database_reason(int open_status, int sqlite_rc) {
    if (open_status == DISC_DATABASE_CARD_AWAY) return "card away";
    if (open_status == DISC_DATABASE_NEWER) return "newer schema";
    if (open_status != DISC_DATABASE_OK) return "failed";
    switch (sqlite_rc & 0xff) {
    case SQLITE_FULL: return "card full";
    case SQLITE_IOERR: case SQLITE_CANTOPEN: case SQLITE_READONLY: return "input/output";
    default: return "failed";
    }
}

void disc_database_write_outcome(disc_database *d, const char *reason) {
    if (!d) return;
    long long now = (long long)time(NULL);
    pthread_mutex_lock(&d->lock);
    if (reason) {
        /* Logged when writes start failing or fail for another reason, not at every play. */
        if (!d->write_failing || d->write_reason != reason)
            disc_log("A play could not be written to the card database: %s", reason);
        d->write_failures++;
        d->write_failed_at = now;
        d->write_reason = reason;
        d->write_failing = 1;
    } else {
        d->write_succeeded_at = now;
        d->write_failing = 0;
    }
    pthread_mutex_unlock(&d->lock);
}

void disc_database_writes_read(disc_database *d, disc_database_writes *w) {
    pthread_mutex_lock(&d->lock);
    *w = (disc_database_writes){.failures = d->write_failures, .failed_at = d->write_failed_at,
                                .succeeded_at = d->write_succeeded_at, .reason = d->write_reason,
                                .failing = d->write_failing};
    pthread_mutex_unlock(&d->lock);
}

void disc_database_close(disc_database *d, sqlite3 *db) {
    sqlite3_close(db);
    remember(d);
    pthread_mutex_unlock(&d->lock);
}
