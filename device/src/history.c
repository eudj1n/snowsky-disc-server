#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#include "history.h"
#include "framing.h"
#include "jsonutil.h"
#include "media.h"
#include "sqlite3.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif

#define HISTORY_MIN_MS 30000LL      /* a play: 30 s of sound ... */
#define HISTORY_HALF_MIN_MS 5000LL  /* ... or half the file after at least 5 s */
/* Stock reads ahead in 32 KiB steps, so a slow stream's position may stand for
 * seconds while it plays: it counts as sounding while it advanced this recently
 * (a pause therefore counts for at most this long). */
#define HISTORY_PLAYING_MS 10000LL
#define HISTORY_KEEP_ROWS 100000LL  /* years of listening; older plays are dropped */
#define HISTORY_SERVE (256 * 1024)  /* the route serves the newest records up to this */
#define HISTORY_SERVE_ROWS 4000     /* ... and never reads more rows than this */
#define HISTORY_LINE 4096
#define HISTORY_LATEST 4102444800LL /* 2100-01-01: later start times are not ours */
#define QUEUE_MAX_ROWS 20000
#define CUE_MAX 99                  /* tracks of one CUE image in the queue */

typedef struct {
    int type;
    unsigned count;
    unsigned long long hash;
    int has_hash;
    char album[256], artist[256], genre[128], folder[512];
} play_context;

/* One track of a CUE image as stock's queue lists it (LIST_SONG_0: OFFSET and
 * DURATION in milliseconds). */
typedef struct { long long id, offset, duration; char title[256]; } cue_track;

struct disc_history {
    disc_history_config config;
    long long next_ms;
    int pid;
    char path[1024];
    int fd;
    long long pos, size, listened_ms, last_ms, advanced_ms;
    int recorded, advances;
    time_t started;
    play_context context;
    /* A CUE image counts per track (combined-008): its tracks, and which one sounds (-1: a whole file). */
    cue_track cue[CUE_MAX];
    int cue_count, cue_index;
    /* MEMORY_PLAY as first seen for this file, and whether stock has changed it
     * since (then it is live: stock writes it at each track change). */
    long long memory_seen;
    int memory_live;
};

static long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

unsigned long long disc_history_path_hash(const char *path) {
    unsigned long long h = 1469598103934665603ULL;
    for (const unsigned char *p = (const unsigned char *)path; *p; p++) {
        h ^= *p;
        h *= 1099511628211ULL;
    }
    return h;
}

static int read_small(const char *path, char *out, size_t capacity) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    ssize_t n;
    do n = read(fd, out, capacity - 1); while (n < 0 && errno == EINTR);
    close(fd);
    if (n <= 0) return 0;
    out[n] = 0;
    return 1;
}

/* The player's pid: kept while its comm still matches, else searched again. */
static int player_pid(disc_history *h) {
    char path[256], comm[64];
    size_t want = strlen(h->config.process);
    if (h->pid > 0) {
        snprintf(path, sizeof(path), "%s/%d/comm", h->config.proc_root, h->pid);
        if (read_small(path, comm, sizeof(comm)) && !strncmp(comm, h->config.process, want) &&
            (comm[want] == '\n' || !comm[want])) return h->pid;
        h->pid = 0;
    }
    DIR *dir = opendir(h->config.proc_root);
    if (!dir) return 0;
    struct dirent *entry;
    while ((entry = readdir(dir))) {
        char *end;
        long pid = strtol(entry->d_name, &end, 10);
        if (*end || pid <= 0 || pid > 4194304) continue;
        snprintf(path, sizeof(path), "%s/%ld/comm", h->config.proc_root, pid);
        if (read_small(path, comm, sizeof(comm)) && !strncmp(comm, h->config.process, want) &&
            (comm[want] == '\n' || !comm[want])) { h->pid = (int)pid; break; }
    }
    closedir(dir);
    return h->pid;
}

int disc_history_player_holds(const char *proc_root, const char *process, const char *path) {
    disc_history probe;
    memset(&probe, 0, sizeof(probe));
    probe.config.proc_root = proc_root;
    probe.config.process = process;
    int pid = player_pid(&probe);
    if (!pid) return 0;
    char dir_path[256];
    snprintf(dir_path, sizeof(dir_path), "%s/%d/fd", proc_root, pid);
    DIR *dir = opendir(dir_path);
    if (!dir) return 0;
    size_t n = strlen(path);
    int held = 0;
    struct dirent *entry;
    while (!held && (entry = readdir(dir))) {
        char link[600], target[1100];
        if (entry->d_name[0] == '.') continue;
        snprintf(link, sizeof(link), "%s/%s", dir_path, entry->d_name);
        ssize_t length = readlink(link, target, sizeof(target) - 1);
        if (length <= 0) continue;
        target[length] = 0;
        held = !strncmp(target, path, n) && (target[n] == 0 || target[n] == '/');
    }
    closedir(dir);
    return held;
}

static const char *const AUDIO[] = {"flac", "wav", "mp3", "m4a", "aac", "ogg", "opus", "ape", "wv",
                                    "dsf", "dff", "aif", "aiff", "alac", "wma", "iso", NULL};
static int audio_path(const char *path) {
    const char *dot = strrchr(path, '.');
    for (size_t i = 0; dot && AUDIO[i]; i++) if (!strcasecmp(dot + 1, AUDIO[i])) return 1;
    return 0;
}

/* The music file the player holds open (the lowest descriptor), or 0. */
static int open_music(disc_history *h, int pid, char *path, size_t capacity, int *fd_out) {
    char dir_path[256];
    snprintf(dir_path, sizeof(dir_path), "%s/%d/fd", h->config.proc_root, pid);
    DIR *dir = opendir(dir_path);
    if (!dir) return 0;
    size_t root = strlen(h->config.music_root);
    int best = -1;
    struct dirent *entry;
    while ((entry = readdir(dir))) {
        char *end;
        long fd = strtol(entry->d_name, &end, 10);
        if (*end || fd < 0 || fd > 65535 || (best >= 0 && fd > best)) continue;
        char link[300], target[1024];
        snprintf(link, sizeof(link), "%s/%ld", dir_path, fd);
        ssize_t n = readlink(link, target, sizeof(target) - 1);
        if (n <= 0) continue;
        target[n] = 0;
        if (strncmp(target, h->config.music_root, root) || target[root] != '/' || !audio_path(target)) continue;
        best = (int)fd;
        snprintf(path, capacity, "%s", target);
    }
    closedir(dir);
    if (best < 0) return 0;
    *fd_out = best;
    return 1;
}

static long long read_pos(disc_history *h, int pid, int fd) {
    char path[256], text[256];
    snprintf(path, sizeof(path), "%s/%d/fdinfo/%d", h->config.proc_root, pid, fd);
    if (!read_small(path, text, sizeof(text))) return -1;
    const char *pos = strstr(text, "pos:");
    return pos ? strtoll(pos + 4, NULL, 10) : -1;
}

static void common(char *into, size_t capacity, const unsigned char *value, int *differs, unsigned row) {
    if (*differs) return;
    const char *text = value ? (const char *)value : "";
    /* A value too long to keep whole is not shared: a cut could split a character. */
    if (strlen(text) >= capacity) { *differs = 1; into[0] = 0; return; }
    if (!row) snprintf(into, capacity, "%s", text);
    else if (strcmp(into, text)) { *differs = 1; into[0] = 0; }
}

/* Stock's current queue (LIST_SONG_0), read-only: its kind, size, shared
 * album/artist/genre/folder and the order-independent hash of its paths. */
static void capture_context(disc_history *h, const char *current) {
    play_context *c = &h->context;
    memset(c, 0, sizeof(*c));
    c->type = -1;
    sqlite3 *db = NULL;
    if (!h->config.song_db || sqlite3_open_v2(h->config.song_db, &db, SQLITE_OPEN_READONLY | SQLITE_OPEN_NOMUTEX, NULL) != SQLITE_OK) {
        if (db) sqlite3_close(db);
        return;
    }
    sqlite3_busy_timeout(db, 500);
    sqlite3_stmt *stmt = NULL;
    if (sqlite3_prepare_v2(db, "SELECT PATH, ALBUM, ARTIST, GENRE, SONG_TYPE FROM LIST_SONG_0 LIMIT 20001", -1, &stmt, NULL) == SQLITE_OK) {
        int album = 0, artist = 0, genre = 0, folder = 0, found = 0;
        unsigned long long sum = 0;
        unsigned rows = 0;
        while (sqlite3_step(stmt) == SQLITE_ROW && rows < QUEUE_MAX_ROWS + 1) {
            const char *path = (const char *)sqlite3_column_text(stmt, 0);
            if (!path) continue;
            sum += disc_history_path_hash(path);
            common(c->album, sizeof(c->album), sqlite3_column_text(stmt, 1), &album, rows);
            common(c->artist, sizeof(c->artist), sqlite3_column_text(stmt, 2), &artist, rows);
            common(c->genre, sizeof(c->genre), sqlite3_column_text(stmt, 3), &genre, rows);
            char parent[1024];
            const char *slash = strrchr(path, '/');
            size_t n = slash ? (size_t)(slash - path) : 0;
            if (n >= sizeof(parent)) n = sizeof(parent) - 1;
            memcpy(parent, path, n); parent[n] = 0;
            common(c->folder, sizeof(c->folder), (const unsigned char *)parent, &folder, rows);
            if (!found && !strcmp(path, current)) { c->type = sqlite3_column_int(stmt, 4); found = 1; }
            else if (!rows && !found) c->type = sqlite3_column_int(stmt, 4);
            rows++;
        }
        c->count = rows;
        c->has_hash = rows && rows <= QUEUE_MAX_ROWS;
        c->hash = sum;
    }
    if (stmt) sqlite3_finalize(stmt);
    sqlite3_close(db);
}

static void json_string(char *out, size_t capacity, size_t *used, const char *text) {
    static const char hex[] = "0123456789abcdef";
    if (*used + 2 >= capacity) { *used = capacity; return; }
    out[(*used)++] = '"';
    for (const unsigned char *p = (const unsigned char *)text; *p && *used + 8 < capacity; p++) {
        if (*p == '"' || *p == '\\') { out[(*used)++] = '\\'; out[(*used)++] = (char)*p; }
        else if (*p < 0x20) { memcpy(out + *used, "\\u00", 4); *used += 4; out[(*used)++] = hex[*p >> 4]; out[(*used)++] = hex[*p & 15]; }
        else out[(*used)++] = (char)*p;
    }
    if (*used + 1 < capacity) out[(*used)++] = '"';
    else *used = capacity;
}
static void json_raw(char *out, size_t capacity, size_t *used, const char *text) {
    size_t n = strlen(text);
    if (*used + n < capacity) { memcpy(out + *used, text, n); *used += n; }
    else *used = capacity;
}
static void json_optional(char *out, size_t capacity, size_t *used, const char *key, const char *value) {
    json_raw(out, capacity, used, key);
    if (value && value[0]) json_string(out, capacity, used, value);
    else json_raw(out, capacity, used, "null");
}

/* One record as a JSON object (also how the route re-emits parsed lines). */
static size_t record_json(char *out, size_t capacity, long long t, const char *path, const char *title, long long seconds,
                          const play_context *c, const char *source) {
    size_t used = 0;
    char number[64];
    snprintf(number, sizeof(number), "{\"v\":1,\"t\":%lld,\"path\":", t);
    json_raw(out, capacity, &used, number);
    json_string(out, capacity, &used, path);
    json_optional(out, capacity, &used, ",\"title\":", title);
    snprintf(number, sizeof(number), ",\"s\":%lld,\"source\":\"%s\",\"ctx\":{\"type\":", seconds, source);
    json_raw(out, capacity, &used, number);
    if (c->type >= 0) { snprintf(number, sizeof(number), "%d", c->type); json_raw(out, capacity, &used, number); }
    else json_raw(out, capacity, &used, "null");
    snprintf(number, sizeof(number), ",\"count\":%u,\"hash\":", c->count);
    json_raw(out, capacity, &used, number);
    if (c->has_hash) { snprintf(number, sizeof(number), "\"%016llx\"", c->hash); json_raw(out, capacity, &used, number); }
    else json_raw(out, capacity, &used, "null");
    json_optional(out, capacity, &used, ",\"album\":", c->album);
    json_optional(out, capacity, &used, ",\"artist\":", c->artist);
    json_optional(out, capacity, &used, ",\"genre\":", c->genre);
    json_optional(out, capacity, &used, ",\"folder\":", c->folder);
    json_raw(out, capacity, &used, "}}");
    if (used >= capacity) return 0;
    out[used] = 0;
    return used;
}

static int bind_text(sqlite3_stmt *stmt, int index, const char *text) {
    return text && text[0] ? sqlite3_bind_text(stmt, index, text, -1, SQLITE_TRANSIENT) : sqlite3_bind_null(stmt, index);
}

/* One play in the plays table; the oldest rows beyond HISTORY_KEEP_ROWS go in
 * the same transaction. */
static void append_record(disc_history *h) {
    sqlite3 *db = NULL;
    int opened = disc_database_open(h->config.database, 1, &db);
    if (opened != DISC_DATABASE_OK) { disc_database_write_outcome(h->config.database, disc_database_reason(opened, 0)); return; }
    const play_context *c = &h->context;
    sqlite3_stmt *stmt = NULL;
    int rc = sqlite3_exec(db, "BEGIN IMMEDIATE", NULL, NULL, NULL);
    if (rc == SQLITE_OK)
        rc = sqlite3_prepare_v2(db, "INSERT INTO plays(started_at, path, heard_seconds, queue_type, queue_count, queue_hash,"
                                    " queue_album, queue_artist, queue_genre, queue_folder, title) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                -1, &stmt, NULL);
    if (rc == SQLITE_OK) {
        char hash[20];
        snprintf(hash, sizeof(hash), "%016llx", c->hash);
        sqlite3_bind_int64(stmt, 1, (long long)h->started);
        sqlite3_bind_text(stmt, 2, h->path, -1, SQLITE_TRANSIENT);
        sqlite3_bind_int64(stmt, 3, h->listened_ms / 1000);
        if (c->type >= 0) sqlite3_bind_int(stmt, 4, c->type);
        else sqlite3_bind_null(stmt, 4);
        sqlite3_bind_int64(stmt, 5, c->count);
        bind_text(stmt, 6, c->has_hash ? hash : NULL);
        bind_text(stmt, 7, c->album);
        bind_text(stmt, 8, c->artist);
        bind_text(stmt, 9, c->genre);
        bind_text(stmt, 10, c->folder);
        bind_text(stmt, 11, h->cue_index >= 0 ? h->cue[h->cue_index].title : NULL);
        rc = sqlite3_step(stmt) == SQLITE_DONE ? SQLITE_OK : SQLITE_ERROR;
    }
    sqlite3_finalize(stmt);
    if (rc == SQLITE_OK) {
        char sql[128];
        snprintf(sql, sizeof(sql), "DELETE FROM plays WHERE id <= (SELECT max(id) FROM plays) - %lld", HISTORY_KEEP_ROWS);
        rc = sqlite3_exec(db, sql, NULL, NULL, NULL);
    }
    rc = rc == SQLITE_OK ? sqlite3_exec(db, "COMMIT", NULL, NULL, NULL) : rc;
    int failed = rc != SQLITE_OK ? sqlite3_extended_errcode(db) : SQLITE_OK;
    if (rc != SQLITE_OK) sqlite3_exec(db, "ROLLBACK", NULL, NULL, NULL);
    disc_database_close(h->config.database, db);
    disc_database_write_outcome(h->config.database, failed == SQLITE_OK ? NULL : disc_database_reason(DISC_DATABASE_OK, failed));
}

static sqlite3 *open_song_db(disc_history *h) {
    sqlite3 *db = NULL;
    if (!h->config.song_db || sqlite3_open_v2(h->config.song_db, &db, SQLITE_OPEN_READONLY | SQLITE_OPEN_NOMUTEX, NULL) != SQLITE_OK) {
        if (db) sqlite3_close(db);
        return NULL;
    }
    sqlite3_busy_timeout(db, 300);
    return db;
}

/* The CUE tracks of the file in stock's queue, by offset (none for a whole file). */
static void capture_cue(disc_history *h, const char *path) {
    h->cue_count = 0;
    h->cue_index = -1;
    sqlite3 *db = open_song_db(h);
    sqlite3_stmt *stmt = NULL;
    if (db && sqlite3_prepare_v2(db, "SELECT ID, TITLE, OFFSET, DURATION FROM LIST_SONG_0 WHERE PATH = ? AND IS_CUE = 1"
                                     " ORDER BY OFFSET LIMIT 99", -1, &stmt, NULL) == SQLITE_OK) {
        sqlite3_bind_text(stmt, 1, path, -1, SQLITE_STATIC);
        while (sqlite3_step(stmt) == SQLITE_ROW && h->cue_count < CUE_MAX) {
            cue_track *track = &h->cue[h->cue_count];
            const unsigned char *title = sqlite3_column_text(stmt, 1);
            int n = sqlite3_column_bytes(stmt, 1);
            track->id = sqlite3_column_int64(stmt, 0);
            track->offset = sqlite3_column_int64(stmt, 2);
            track->duration = sqlite3_column_int64(stmt, 3);
            /* A title that cannot be kept whole, as valid UTF-8, is no title. */
            if (!title || n <= 0 || (size_t)n >= sizeof(track->title) || !disc_utf8(title, (size_t)n)) track->title[0] = 0;
            else memcpy(track->title, title, (size_t)n + 1);
            if (track->offset >= 0 && track->duration > 0) h->cue_count++;
        }
    }
    if (stmt) sqlite3_finalize(stmt);
    if (db) sqlite3_close(db);
}

/* Which CUE track sounds. The read position gives an estimate (bytes over the
 * image's length in time; stock reads a little ahead). Stock itself writes the
 * track it plays to MEMORY_PLAY.MUSIC_ID (a queue row) at each track change when
 * memory play is on (guest evidence 2026-09-28). The row is trusted only once
 * stock has changed it while this file plays (a row left from before, with
 * memory play off, never counts) and only within one track of the estimate. */
static int current_cue(disc_history *h, long long pos) {
    if (!h->cue_count) return -1;
    int estimate = 0;
    const cue_track *last = &h->cue[h->cue_count - 1];
    long long total = last->offset + last->duration;
    if (h->size > 0 && pos > 0 && total > 0) {
        long long ms = (long long)((double)pos / (double)h->size * (double)total);
        for (int i = 0; i < h->cue_count; i++) if (h->cue[i].offset <= ms) estimate = i;
    }
    int remembered = -1;
    sqlite3 *db = open_song_db(h);
    sqlite3_stmt *stmt = NULL;
    if (db && sqlite3_prepare_v2(db, "SELECT MUSIC_ID FROM MEMORY_PLAY LIMIT 1", -1, &stmt, NULL) == SQLITE_OK &&
        sqlite3_step(stmt) == SQLITE_ROW) {
        long long id = sqlite3_column_int64(stmt, 0);
        if (id != h->memory_seen) { h->memory_seen = id; h->memory_live = 1; }
        for (int i = 0; h->memory_live && i < h->cue_count; i++) if (h->cue[i].id == id) remembered = i;
    }
    if (stmt) sqlite3_finalize(stmt);
    if (db) sqlite3_close(db);
    return remembered >= 0 && abs(remembered - estimate) <= 1 ? remembered : estimate;
}

/* A new file, or a new track of a CUE image: it starts from nothing. */
static void start_segment(disc_history *h, long long now) {
    h->listened_ms = 0;
    h->recorded = 0;
    h->advances = 0;
    h->started = time(NULL);
    h->last_ms = now;
    h->advanced_ms = -HISTORY_PLAYING_MS;
}

static void observe(disc_history *h) {
    long long now = now_ms();
    int pid = player_pid(h), fd = -1;
    char path[1024];
    if (!pid || !open_music(h, pid, path, sizeof(path), &fd)) { h->path[0] = 0; return; }
    long long pos = read_pos(h, pid, fd);
    if (strcmp(path, h->path)) {
        /* A new file: it starts from nothing; the queue tells where it was started from. */
        snprintf(h->path, sizeof(h->path), "%s", path);
        h->fd = fd;
        h->pos = pos;
        start_segment(h, now);
        struct stat st;
        h->size = stat(path, &st) ? 0 : (long long)st.st_size;
        capture_context(h, path);
        capture_cue(h, path);
        h->memory_seen = -1;
        h->memory_live = 0;
        current_cue(h, pos); /* the row as it stands now is not yet news */
        h->memory_live = 0;
        h->cue_index = current_cue(h, pos);
        return;
    }
    if (h->cue_count) {
        int index = current_cue(h, pos);
        if (index != h->cue_index) { h->cue_index = index; start_segment(h, now); }
    }
    long long elapsed = now - h->last_ms, cap = 2LL * h->config.interval_ms;
    h->last_ms = now;
    if (pos > h->pos) {
        h->advanced_ms = now;
        const char *title = h->cue_index >= 0 && h->cue[h->cue_index].title[0] ? h->cue[h->cue_index].title : NULL;
        if (++h->advances == 2 && h->config.sounding && h->config.sounding(h->config.arg, h->path, title)) h->recorded = 1;
    }
    if (now - h->advanced_ms <= HISTORY_PLAYING_MS) h->listened_ms += elapsed < cap ? elapsed : cap;
    if (pos >= 0) h->pos = pos;
    /* Half of a CUE track is half its duration heard; half of a file, half its bytes read. */
    int half = h->cue_index >= 0 ? h->listened_ms * 2 >= h->cue[h->cue_index].duration
                                 : h->size > 0 && h->pos * 2 >= h->size;
    if (!h->recorded && (h->listened_ms >= HISTORY_MIN_MS || (half && h->listened_ms >= HISTORY_HALF_MIN_MS))) {
        h->recorded = 1;
        append_record(h);
    }
}

int disc_history_start(disc_history **out, const disc_history_config *config) {
    disc_history *h = calloc(1, sizeof(*h));
    if (!h) return -1;
    h->config = *config;
    if (!h->config.interval_ms) h->config.interval_ms = 2000;
    *out = h;
    return 0;
}

void disc_history_poll(disc_history *h) {
    long long now = now_ms();
    if (!h || now < h->next_ms) return;
    h->next_ms = now + h->config.interval_ms;
    observe(h);
}

void disc_history_stop(disc_history *h) {
    free(h);
}

static const char *row_text(sqlite3_stmt *stmt, int column, size_t capacity) {
    if (sqlite3_column_type(stmt, column) != SQLITE_TEXT) return NULL;
    const unsigned char *text = sqlite3_column_text(stmt, column);
    size_t n = (size_t)sqlite3_column_bytes(stmt, column);
    return text && n < capacity && strlen((const char *)text) == n && disc_utf8(text, n) ? (const char *)text : NULL;
}

static int row_number(sqlite3_stmt *stmt, int column, long long min, long long max, long long *out) {
    if (sqlite3_column_type(stmt, column) != SQLITE_INTEGER) return 0;
    long long value = sqlite3_column_int64(stmt, column);
    if (value < min || value > max) return 0;
    *out = value;
    return 1;
}

/* One row as a record, or 0 when it is not one the observer could have written. */
static size_t row_record(sqlite3_stmt *stmt, char *out, size_t capacity) {
    long long when, seconds, value;
    const char *path = row_text(stmt, 1, 1024), *title = row_text(stmt, 10, 256);
    if (!row_number(stmt, 0, 0, HISTORY_LATEST, &when) || !path || !path[0] || !row_number(stmt, 2, 0, 86400, &seconds)) return 0;
    play_context c;
    memset(&c, 0, sizeof(c));
    c.type = row_number(stmt, 3, 0, 255, &value) ? (int)value : -1;
    c.count = row_number(stmt, 4, 0, QUEUE_MAX_ROWS, &value) ? (unsigned)value : 0;
    const char *hash = row_text(stmt, 5, 17);
    if (hash && strlen(hash) == 16 && strspn(hash, "0123456789abcdef") == 16) {
        c.has_hash = 1;
        c.hash = strtoull(hash, NULL, 16);
    }
    const struct { int column; char *to; size_t cap; } fields[] = {
        {6, c.album, sizeof(c.album)}, {7, c.artist, sizeof(c.artist)},
        {8, c.genre, sizeof(c.genre)}, {9, c.folder, sizeof(c.folder)}};
    for (size_t i = 0; i < 4; i++) {
        const char *text = row_text(stmt, fields[i].column, fields[i].cap);
        if (text) memcpy(fields[i].to, text, strlen(text) + 1);
    }
    const char *source = row_text(stmt, 11, 16);
    return record_json(out, capacity, when, path, title, seconds, &c, source && !strcmp(source, "browser") ? "browser" : "player");
}

int disc_history_json(disc_database *database, char **json, size_t *length) {
    static const char head[] = "{\"records\":[";
    size_t capacity = HISTORY_SERVE + 4096;
    char *out = malloc(capacity);
    if (!out) return 503;
    sqlite3 *db = NULL;
    int status = disc_database_open(database, 0, &db);
    if (status != DISC_DATABASE_OK && status != DISC_DATABASE_ABSENT) { free(out); return 503; }
    /* Newest first, written from the end of the buffer backwards, so the
     * response lists them oldest first like the play order. */
    size_t start = capacity - 64, end = start;
    int records = 0, truncated = 0;
    if (db) {
        sqlite3_stmt *stmt = NULL;
        if (sqlite3_prepare_v2(db, "SELECT started_at, path, heard_seconds, queue_type, queue_count, queue_hash,"
                                    " queue_album, queue_artist, queue_genre, queue_folder, title, source FROM plays ORDER BY id DESC LIMIT ?",
                               -1, &stmt, NULL) == SQLITE_OK &&
            sqlite3_bind_int(stmt, 1, HISTORY_SERVE_ROWS + 1) == SQLITE_OK) {
            int rows = 0, rc;
            while ((rc = sqlite3_step(stmt)) == SQLITE_ROW) {
                if (++rows > HISTORY_SERVE_ROWS) { truncated = 1; break; }
                char record[HISTORY_LINE];
                size_t r = row_record(stmt, record, sizeof(record));
                if (!r) continue;
                if (start < sizeof(head) + r + 1 || end - start + r + 1 > HISTORY_SERVE) { truncated = 1; break; }
                if (records++) out[--start] = ',';
                start -= r;
                memcpy(out + start, record, r);
            }
            if (rc != SQLITE_ROW && rc != SQLITE_DONE) status = DISC_DATABASE_FAILED;
        } else status = DISC_DATABASE_FAILED;
        sqlite3_finalize(stmt);
        disc_database_close(database, db);
    }
    if (status == DISC_DATABASE_FAILED) { free(out); return 503; }
    memcpy(out, head, sizeof(head) - 1);
    memmove(out + sizeof(head) - 1, out + start, end - start);
    size_t used = sizeof(head) - 1 + end - start;
    used += (size_t)snprintf(out + used, capacity - used, "],\"truncated\":%s}", truncated ? "true" : "false");
    *json = out;
    *length = used;
    return 200;
}

static int is_null(const char *json, const jsmntok_t *tok) {
    return tok->type == JSMN_PRIMITIVE && tok->end - tok->start == 4 && !memcmp(json + tok->start, "null", 4);
}

/* A text field of the body: absent or null leaves it empty; otherwise a JSON
 * string that fits (the observer's bounds). Returns 0 when it is not one. */
static int body_text(const char *json, const jsmntok_t *t, int object, const char *key, char *out, size_t capacity) {
    out[0] = 0;
    int i = disc_json_find(json, t, object, key);
    if (i <= 0 || is_null(json, &t[i])) return 1;
    int n = t[i].type == JSMN_STRING ? disc_json_string(json, &t[i], out, capacity) : -1;
    return n >= 0 && disc_utf8((const unsigned char *)out, (size_t)n);
}

static int only_keys(const char *json, const jsmntok_t *t, int object, const char *const *keys, size_t count) {
    for (int i = object + 1, k = 0; k < t[object].size; k++, i = disc_json_skip(t, i + 1)) {
        int known = 0;
        for (size_t j = 0; j < count; j++) if (disc_json_eq(json, &t[i], keys[j])) known = 1;
        if (!known) return 0;
    }
    return 1;
}

int disc_history_add_json(disc_database *database, const char *music_root, const char *body, size_t length, long long now,
                          char **json, size_t *json_length, const char **problem) {
    static const char *const KEYS[] = {"path", "seconds", "title", "ctx"};
    static const char *const CONTEXT[] = {"type", "count", "hash", "album", "artist", "genre", "folder"};
    jsmntok_t *t = NULL;
    int tokens = disc_json_parse(body, length, &t, 64), next, i, ctx = -1;
    char path[DISC_MEDIA_MAX_PATH + 1], title[256], hash[20];
    long long seconds = 0, type = -1, count = 0;
    play_context c;
    memset(&c, 0, sizeof(c));
    *problem = "The body is {\"path\",\"seconds\",\"title\"?,\"ctx\"?} as the history reports plays";
    int ok = tokens > 0 && disc_json_valid(body, t, 0, &next) && next == tokens && t[0].type == JSMN_OBJECT &&
             only_keys(body, t, 0, KEYS, 4) &&
             (i = disc_json_find(body, t, 0, "path")) > 0 && t[i].type == JSMN_STRING &&
             disc_json_string(body, &t[i], path, sizeof(path)) > 0 &&
             (i = disc_json_find(body, t, 0, "seconds")) > 0 && !disc_json_number(body, &t[i], 1, 86400, &seconds) &&
             body_text(body, t, 0, "title", title, sizeof(title));
    if (ok && (ctx = disc_json_find(body, t, 0, "ctx")) > 0) {
        ok = t[ctx].type == JSMN_OBJECT && only_keys(body, t, ctx, CONTEXT, 7) &&
             body_text(body, t, ctx, "album", c.album, sizeof(c.album)) && body_text(body, t, ctx, "artist", c.artist, sizeof(c.artist)) &&
             body_text(body, t, ctx, "genre", c.genre, sizeof(c.genre)) && body_text(body, t, ctx, "folder", c.folder, sizeof(c.folder)) &&
             body_text(body, t, ctx, "hash", hash, sizeof(hash));
        if (ok && hash[0]) {
            ok = strlen(hash) == 16 && strspn(hash, "0123456789abcdef") == 16;
            c.has_hash = ok;
            c.hash = ok ? strtoull(hash, NULL, 16) : 0;
        }
        if (ok && (i = disc_json_find(body, t, ctx, "type")) > 0 && !is_null(body, &t[i]))
            ok = !disc_json_number(body, &t[i], 0, 255, &type);
        if (ok && (i = disc_json_find(body, t, ctx, "count")) > 0) ok = !disc_json_number(body, &t[i], 0, QUEUE_MAX_ROWS, &count);
    }
    free(t);
    if (!ok) return 400;
    c.type = (int)type;
    c.count = (unsigned)count;
    /* An existing music file on the card, reached as the media routes reach one. */
    size_t root = strlen(music_root);
    if (strncmp(path, music_root, root) || path[root] != '/' || strstr(path + root, "/.")) { *problem = "Not a music file on the card"; return 400; }
    int dir = -1, fd = -1;
    char name[256];
    int opened = disc_media_open(music_root, path, &dir, &fd, name, sizeof(name));
    if (opened) { *problem = opened == 404 ? "No such music file" : opened == 503 ? "Card unavailable" : "Not a music file on the card"; return opened == 503 ? 503 : opened == 404 ? 404 : 400; }
    disc_media_info info;
    disc_media_probe(fd, name, &info);
    close(fd);
    close(dir);
    if (info.has_duration && (unsigned long long)seconds * 1000ull > info.duration_ms + 5000ull) { *problem = "Longer than the file"; return 400; }
    sqlite3 *db = NULL;
    int status = disc_database_open(database, 1, &db);
    if (status != DISC_DATABASE_OK) {
        disc_database_write_outcome(database, disc_database_reason(status, 0));
        *problem = status == DISC_DATABASE_CARD_AWAY ? "Card is not owned by the player" : "History unavailable";
        return 503;
    }
    long long id = 0, started = now - seconds;
    sqlite3_stmt *stmt = NULL;
    int rc = sqlite3_exec(db, "BEGIN IMMEDIATE", NULL, NULL, NULL);
    if (rc == SQLITE_OK)
        rc = sqlite3_prepare_v2(db, "INSERT INTO plays(started_at, path, heard_seconds, queue_type, queue_count, queue_hash,"
                                    " queue_album, queue_artist, queue_genre, queue_folder, title, source)"
                                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,'browser')", -1, &stmt, NULL);
    if (rc == SQLITE_OK) {
        char text[20];
        snprintf(text, sizeof(text), "%016llx", c.hash);
        sqlite3_bind_int64(stmt, 1, started);
        sqlite3_bind_text(stmt, 2, path, -1, SQLITE_TRANSIENT);
        sqlite3_bind_int64(stmt, 3, seconds);
        if (c.type >= 0) sqlite3_bind_int(stmt, 4, c.type);
        else sqlite3_bind_null(stmt, 4);
        sqlite3_bind_int64(stmt, 5, c.count);
        bind_text(stmt, 6, c.has_hash ? text : NULL);
        bind_text(stmt, 7, c.album);
        bind_text(stmt, 8, c.artist);
        bind_text(stmt, 9, c.genre);
        bind_text(stmt, 10, c.folder);
        bind_text(stmt, 11, title);
        rc = sqlite3_step(stmt) == SQLITE_DONE ? SQLITE_OK : SQLITE_ERROR;
        id = sqlite3_last_insert_rowid(db);
    }
    sqlite3_finalize(stmt);
    if (rc == SQLITE_OK) {
        char sql[128];
        snprintf(sql, sizeof(sql), "DELETE FROM plays WHERE id <= (SELECT max(id) FROM plays) - %lld", HISTORY_KEEP_ROWS);
        rc = sqlite3_exec(db, sql, NULL, NULL, NULL);
    }
    rc = rc == SQLITE_OK ? sqlite3_exec(db, "COMMIT", NULL, NULL, NULL) : rc;
    int failed = rc != SQLITE_OK ? sqlite3_extended_errcode(db) : SQLITE_OK;
    if (rc != SQLITE_OK) sqlite3_exec(db, "ROLLBACK", NULL, NULL, NULL);
    disc_database_close(database, db);
    const char *reason = failed == SQLITE_OK ? NULL : disc_database_reason(DISC_DATABASE_OK, failed);
    disc_database_write_outcome(database, reason);
    if (reason) { *problem = !strcmp(reason, "card full") ? "The card is full; the play was not written" : "The play was not written"; return 503; }
    char *out = malloc(128);
    if (!out) { *problem = "Memory unavailable"; return 503; }
    int n = snprintf(out, 128, "{\"id\":%lld,\"t\":%lld,\"s\":%lld,\"source\":\"browser\"}", id, started, seconds);
    *json = out;
    *json_length = (size_t)n;
    *problem = NULL;
    return 201;
}
