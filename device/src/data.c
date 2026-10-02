#include "data.h"
#include "jsonutil.h"
#include "framing.h"
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sqlite3.h"

#define MAX_TOKENS 8192

static int name_ok(const char *s) {
    size_t n = strlen(s);
    if (n < 2 || n > 40 || !islower((unsigned char)s[0])) return 0;
    for (size_t i = 0; i < n; i++) if (!islower((unsigned char)s[i]) && !isdigit((unsigned char)s[i]) && s[i] != '_') return 0;
    return 1;
}
static int forbidden_sql(const char *sql) {
    static const char *const words[] = {"ATTACH", "DETACH", "PRAGMA", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER",
                                        "REPLACE", "VACUUM", "REINDEX", "WITH", "LOAD_EXTENSION", "RANDOMBLOB", "WRITEFILE", "READFILE"};
    size_t n = strlen(sql);
    char *upper = malloc(n + 1);
    if (!upper) return 1;
    for (size_t i = 0; i <= n; i++) upper[i] = (char)toupper((unsigned char)sql[i]);
    int bad = strchr(upper, ';') || strstr(upper, "--") || strstr(upper, "/*") || strncmp(upper + strspn(upper, " \t"), "SELECT ", 7);
    for (size_t w = 0; !bad && w < sizeof(words) / sizeof(words[0]); w++) {
        const char *p = upper;
        while ((p = strstr(p, words[w]))) {
            size_t len = strlen(words[w]);
            int before = p == upper || !(isalnum((unsigned char)p[-1]) || p[-1] == '_');
            int after = !(isalnum((unsigned char)p[len]) || p[len] == '_');
            if (before && after) { bad = 1; break; }
            p += len;
        }
    }
    free(upper);
    return bad;
}
static int parse_param(const char *json, const jsmntok_t *t, int value, disc_query_param *p) {
    char type[8], pattern[520]; long long v; int i;
    if (t[value].type != JSMN_OBJECT) return -1;
    if ((i = disc_json_find(json, t, value, "name")) < 0 || disc_json_string(json, &t[i], p->name, sizeof(p->name)) <= 0 || !name_ok(p->name)) return -1;
    if ((i = disc_json_find(json, t, value, "type")) < 0 || disc_json_string(json, &t[i], type, sizeof(type)) <= 0) return -1;
    if (!strcmp(type, "int")) {
        p->is_int = 1;
        if ((i = disc_json_find(json, t, value, "min")) < 0 || disc_json_number(json, &t[i], -9007199254740991LL, 9007199254740991LL, &p->min)) return -1;
        if ((i = disc_json_find(json, t, value, "max")) < 0 || disc_json_number(json, &t[i], p->min, 9007199254740991LL, &p->max)) return -1;
        return 0;
    }
    if (strcmp(type, "text")) return -1;
    p->is_int = 0;
    if ((i = disc_json_find(json, t, value, "max_length")) < 0 || disc_json_number(json, &t[i], 1, 255, &v)) return -1;
    p->max_length = (size_t)v;
    if ((i = disc_json_find(json, t, value, "pattern")) < 0 || disc_json_string(json, &t[i], pattern, sizeof(pattern)) < 0 || strstr(pattern, "(?")) return -1;
    char anchored[528]; snprintf(anchored, sizeof(anchored), "^(%s)$", pattern);
    return regcomp(&p->pattern, anchored, REG_EXTENDED | REG_NOSUB) ? -1 : 0;
}
static int parse_query(const char *json, const jsmntok_t *t, int key, int value, disc_query *q, const disc_queries *qs) {
    long long v; int i;
    if (disc_json_string(json, &t[key], q->name, sizeof(q->name)) <= 0 || !name_ok(q->name) || t[value].type != JSMN_OBJECT) return -1;
    if ((i = disc_json_find(json, t, value, "db")) < 0 || disc_json_string(json, &t[i], q->db, sizeof(q->db)) <= 0) return -1;
    int known = 0;
    for (size_t d = 0; d < qs->db_count; d++) if (!strcmp(qs->databases[d].key, q->db)) known = 1;
    if (!known) return -1;
    if ((i = disc_json_find(json, t, value, "sql")) < 0 || disc_json_string(json, &t[i], q->sql, sizeof(q->sql)) <= 0 || forbidden_sql(q->sql)) return -1;
    if ((i = disc_json_find(json, t, value, "max_rows")) < 0 || disc_json_number(json, &t[i], 1, 1000, &v)) return -1;
    q->max_rows = (unsigned)v;
    int params = disc_json_find(json, t, value, "params");
    if (params < 0 || t[params].type != JSMN_ARRAY || t[params].size > DISC_QUERY_PARAMS) return -1;
    size_t placeholders = 0;
    for (const char *c = q->sql; *c; c++) if (*c == '?') placeholders++;
    if ((size_t)t[params].size != placeholders) return -1;
    int k = params + 1;
    for (int n = 0; n < t[params].size; n++) {
        if (parse_param(json, t, k, &q->params[q->param_count])) return -1;
        q->param_count++;
        k = disc_json_skip(t, k);
    }
    return 0;
}

int disc_queries_parse(disc_queries *qs, const char *json, size_t length, const char *expected) {
    memset(qs, 0, sizeof(*qs));
    if (!json || !length || length > DISC_DATA_MAX_JSON || !expected || strlen(expected) != 64 || memchr(json, 0, length)) return -1;
    jsmntok_t *t = NULL;
    if (disc_json_parse(json, length, &t, MAX_TOKENS) < 0) return -1;
    int ok = 0, i; long long v;
    if ((i = disc_json_find(json, t, 0, "schema_version")) < 0 || disc_json_number(json, &t[i], 1, 1, &v)) goto done;
    if ((i = disc_json_find(json, t, 0, "api")) < 0 || disc_json_number(json, &t[i], 1, 1, &v)) goto done;
    if ((i = disc_json_find(json, t, 0, "profile_sha256")) < 0 || disc_json_sha256(json, &t[i], qs->profile_sha256) || strcmp(qs->profile_sha256, expected)) goto done;
    int dbs = disc_json_find(json, t, 0, "databases");
    if (dbs < 0 || t[dbs].type != JSMN_OBJECT || t[dbs].size == 0 || t[dbs].size > 8) goto done;
    int k = dbs + 1;
    for (int n = 0; n < t[dbs].size; n++) {
        if (disc_json_string(json, &t[k], qs->databases[n].key, 41) <= 0 || !name_ok(qs->databases[n].key)) goto done;
        if (disc_json_string(json, &t[k + 1], qs->databases[n].file, 41) <= 0 || strchr(qs->databases[n].file, '/') || strstr(qs->databases[n].file, "..")) goto done;
        qs->db_count++;
        k = disc_json_skip(t, k + 1);
    }
    int queries = disc_json_find(json, t, 0, "queries");
    if (queries < 0 || t[queries].type != JSMN_OBJECT || t[queries].size == 0 || t[queries].size > DISC_QUERIES_MAX) goto done;
    k = queries + 1;
    for (int n = 0; n < t[queries].size; n++) {
        if (parse_query(json, t, k, k + 1, &qs->queries[qs->count], qs)) goto done;
        qs->count++;
        for (size_t m = 0; m + 1 < qs->count; m++) if (!strcmp(qs->queries[m].name, qs->queries[qs->count - 1].name)) goto done;
        k = disc_json_skip(t, k + 1);
    }
    ok = 1;
done:
    free(t);
    if (!ok) { disc_queries_free(qs); return -1; }
    return 0;
}

void disc_queries_free(disc_queries *qs) {
    for (size_t i = 0; i < qs->count; i++)
        for (size_t p = 0; p < qs->queries[i].param_count; p++)
            if (!qs->queries[i].params[p].is_int) regfree(&qs->queries[i].params[p].pattern);
    memset(qs, 0, sizeof(*qs));
}

const disc_query *disc_queries_find(const disc_queries *qs, const char *name) {
    for (size_t i = 0; i < qs->count; i++) if (!strcmp(qs->queries[i].name, name)) return &qs->queries[i];
    return NULL;
}

typedef struct { char *data; size_t used, capacity, limit; int overflow; } buffer;
static void put(buffer *b, const char *s, size_t n) {
    if (b->overflow) return;
    if (b->used + n + 1 > b->capacity) {
        size_t next = b->capacity ? b->capacity * 2 : 4096;
        while (next < b->used + n + 1) next *= 2;
        if (next > b->limit) { b->overflow = 1; return; }
        char *grown = realloc(b->data, next);
        if (!grown) { b->overflow = 1; return; }
        b->data = grown; b->capacity = next;
    }
    memcpy(b->data + b->used, s, n); b->used += n; b->data[b->used] = 0;
}
static void put_text(buffer *b, const char *s) { put(b, s, strlen(s)); }
static void put_json_string(buffer *b, const unsigned char *s, size_t n) {
    if (!disc_utf8(s, n)) { put_text(b, "null"); return; }
    put_text(b, "\"");
    for (size_t i = 0; i < n; i++) {
        unsigned char c = s[i];
        char esc[8];
        if (c == '"' || c == '\\') { esc[0] = '\\'; esc[1] = (char)c; put(b, esc, 2); }
        else if (c < 0x20) { snprintf(esc, sizeof(esc), "\\u%04x", c); put_text(b, esc); }
        else put(b, (const char *)&s[i], 1);
    }
    put_text(b, "\"");
}

int disc_data_execute(const disc_queries *qs, const disc_query *q, const char *data_root, disc_database *service,
                      const char *const *values, char **out, size_t *out_length) {
    *out = NULL; *out_length = 0;
    const char *file = NULL;
    for (size_t d = 0; d < qs->db_count; d++) if (!strcmp(qs->databases[d].key, q->db)) file = qs->databases[d].file;
    int own = file && !strcmp(file, DISC_DATA_SERVICE_DB);
    if (!file || (own ? !service || !service->path : !data_root)) return 503;
    /* Validate every parameter before touching the database. */
    long long ints[DISC_QUERY_PARAMS];
    for (size_t p = 0; p < q->param_count; p++) {
        const char *raw = values[p];
        if (!raw) return 400;
        const disc_query_param *def = &q->params[p];
        if (def->is_int) {
            size_t n = strlen(raw);
            if (n == 0 || n > 11) return 400;
            for (size_t i = (raw[0] == '-'); i < n; i++) if (!isdigit((unsigned char)raw[i])) return 400;
            if (raw[0] == '-' && n == 1) return 400;
            char *end; long long v = strtoll(raw, &end, 10);
            if (*end || v < def->min || v > def->max) return 400;
            ints[p] = v;
        } else if (strlen(raw) > def->max_length || memchr(raw, 0, def->max_length) || regexec(&def->pattern, raw, 0, NULL, 0)) return 400;
    }
    sqlite3 *db = NULL;
    if (own) {
        /* The service's own database, through its lock and checks; created when
         * missing so its tables exist for the statement. */
        if (disc_database_open(service, 1, &db) != DISC_DATABASE_OK) return 503;
        sqlite3_exec(db, "PRAGMA query_only=ON", NULL, NULL, NULL);
    } else {
        char path[512];
        if (snprintf(path, sizeof(path), "%s/%s", data_root, file) >= (int)sizeof(path)) return 503;
        if (sqlite3_open_v2(path, &db, SQLITE_OPEN_READONLY | SQLITE_OPEN_NOMUTEX, NULL) != SQLITE_OK) { if (db) sqlite3_close(db); return 503; }
        sqlite3_busy_timeout(db, 300);
    }
    sqlite3_stmt *stmt = NULL;
    int status = 500;
    if (sqlite3_prepare_v2(db, q->sql, -1, &stmt, NULL) != SQLITE_OK) {
        /* A table the statement names is gone, not busy: stock drops LIST_SONG_0 when a scan
         * removes a queued file (combined-009). A client stops at 409 instead of retrying. */
        const char *why = sqlite3_errmsg(db);
        status = why && !strncmp(why, "no such table", 13) ? 409 : 503;
        goto done;
    }
    if (sqlite3_stmt_readonly(stmt) == 0) { status = 503; goto done; }
    for (size_t p = 0; p < q->param_count; p++) {
        int rc = q->params[p].is_int ? sqlite3_bind_int64(stmt, (int)p + 1, ints[p]) : sqlite3_bind_text(stmt, (int)p + 1, values[p], -1, SQLITE_TRANSIENT);
        if (rc != SQLITE_OK) goto done;
    }
    buffer b = {0}; b.limit = DISC_DATA_MAX_OUTPUT;
    int columns = sqlite3_column_count(stmt);
    put_text(&b, "{\"query\":"); put_json_string(&b, (const unsigned char *)q->name, strlen(q->name));
    put_text(&b, ",\"columns\":[");
    for (int c = 0; c < columns; c++) { if (c) put_text(&b, ","); const char *n = sqlite3_column_name(stmt, c); put_json_string(&b, (const unsigned char *)n, strlen(n)); }
    put_text(&b, "],\"rows\":[");
    unsigned rows = 0; int truncated = 0, rc;
    while ((rc = sqlite3_step(stmt)) == SQLITE_ROW) {
        if (rows >= q->max_rows) { truncated = 1; break; }
        if (rows) put_text(&b, ",");
        put_text(&b, "[");
        for (int c = 0; c < columns; c++) {
            if (c) put_text(&b, ",");
            char num[40];
            switch (sqlite3_column_type(stmt, c)) {
            case SQLITE_INTEGER: snprintf(num, sizeof(num), "%lld", (long long)sqlite3_column_int64(stmt, c)); put_text(&b, num); break;
            case SQLITE_FLOAT: snprintf(num, sizeof(num), "%.17g", sqlite3_column_double(stmt, c)); put_text(&b, num); break;
            case SQLITE_TEXT: put_json_string(&b, sqlite3_column_text(stmt, c), (size_t)sqlite3_column_bytes(stmt, c)); break;
            default: put_text(&b, "null");
            }
        }
        put_text(&b, "]");
        rows++;
        if (b.overflow) { truncated = 1; break; }
    }
    if (rc != SQLITE_ROW && rc != SQLITE_DONE) { status = rc == SQLITE_BUSY || rc == SQLITE_LOCKED ? 503 : 500; free(b.data); goto done; }
    if (b.overflow) { /* keep a valid document: drop the partial row */
        free(b.data); b = (buffer){0}; b.limit = DISC_DATA_MAX_OUTPUT;
        put_text(&b, "{\"query\":"); put_json_string(&b, (const unsigned char *)q->name, strlen(q->name)); put_text(&b, ",\"columns\":[],\"rows\":[");
        truncated = 1;
    }
    char tail[64]; snprintf(tail, sizeof(tail), "],\"rows_returned\":%u,\"truncated\":%s}", rows, truncated ? "true" : "false");
    put_text(&b, tail);
    if (b.overflow) { free(b.data); status = 500; goto done; }
    *out = b.data; *out_length = b.used; status = 200;
done:
    if (stmt) sqlite3_finalize(stmt);
    if (own) disc_database_close(service, db);
    else sqlite3_close(db);
    return status;
}

/* The statement V2.57's own screen (mq_ui) uses to favorite a library track. */
#define FAVORITE_COLUMNS "PATH, NAME, TITLE, ALBUM, ARTIST, GENRE, DISC, TRACK, IS_CUE, IS_ISO, IS_DSD, OFFSET, DURATION, " \
    "NAME_CODE, TITLE_CODE, ALBUM_CODE, ARTIST_CODE, GENRE_CODE, ADD_TIME, SAMPLE_RATE, BIT_PER_SAMPLE, CHANNELS, BIT_RATE, " \
    "SONG_MIMETYPE, SONG_PRODUCTION_YEAR, IS_SELECT, ALBUM_ARTIST, ALBUM_ARTIST_CODE, IS_M3U, M3U_PATH, NAME_CODE_JP, " \
    "TITLE_CODE_JP, ALBUM_CODE_JP, ARTIST_CODE_JP, GENRE_CODE_JP, ALBUM_ARTIST_CODE_JP"
static const char *const FAVORITE_ADD =
    "INSERT INTO MY_LOVE (" FAVORITE_COLUMNS " ) SELECT " FAVORITE_COLUMNS " FROM SONG WHERE ID = ?";

/* The favorite of this file and track in the library's context: stock tells favorites
 * apart by path, track number and IS_M3U (combined-009, docs/m3u.md), so one set while a
 * list plays is not the library's. A track number stock left empty is NULL on both sides. */
static long long love_row(sqlite3 *db, const unsigned char *path, long long track, int track_null, long long m3u, int *status) {
    sqlite3_stmt *q = NULL;
    long long id = 0;
    *status = 500;
    if (sqlite3_prepare_v2(db, "SELECT ID FROM MY_LOVE WHERE PATH = ? AND TRACK IS ? AND IFNULL(IS_M3U, 0) = ? LIMIT 1", -1, &q, NULL) != SQLITE_OK)
        return 0;
    sqlite3_bind_text(q, 1, (const char *)path, -1, SQLITE_TRANSIENT);
    if (track_null) sqlite3_bind_null(q, 2);
    else sqlite3_bind_int64(q, 2, track);
    sqlite3_bind_int64(q, 3, m3u);
    int rc = sqlite3_step(q);
    if (rc == SQLITE_ROW) { id = sqlite3_column_int64(q, 0); *status = 200; }
    else if (rc == SQLITE_DONE) *status = 404;
    else if (rc == SQLITE_BUSY || rc == SQLITE_LOCKED) *status = 503;
    sqlite3_finalize(q);
    return id;
}

int disc_data_favorite_add(const char *data_root, long long song_id, long long *love_id, int *already) {
    char path[512];
    *love_id = 0; *already = 0;
    if (!data_root || song_id < 1 || snprintf(path, sizeof(path), "%s/song.db", data_root) >= (int)sizeof(path)) return 503;
    sqlite3 *db = NULL;
    if (sqlite3_open_v2(path, &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_NOMUTEX, NULL) != SQLITE_OK) { if (db) sqlite3_close(db); return 503; }
    sqlite3_busy_timeout(db, 2000);
    int status = 500, begun = 0;
    sqlite3_stmt *song = NULL, *insert = NULL;
    unsigned char *file = NULL;
    int rc = sqlite3_exec(db, "BEGIN IMMEDIATE", NULL, NULL, NULL);
    if (rc != SQLITE_OK) { status = rc == SQLITE_BUSY || rc == SQLITE_LOCKED ? 503 : 500; goto done; }
    begun = 1;
    if (sqlite3_prepare_v2(db, "SELECT PATH, TRACK, IFNULL(IS_M3U, 0) FROM SONG WHERE ID = ?", -1, &song, NULL) != SQLITE_OK) goto done;
    sqlite3_bind_int64(song, 1, song_id);
    rc = sqlite3_step(song);
    if (rc != SQLITE_ROW) { status = rc == SQLITE_DONE ? 404 : rc == SQLITE_BUSY ? 503 : 500; goto done; }
    const unsigned char *text = sqlite3_column_text(song, 0);
    long long track = sqlite3_column_int64(song, 1);
    int track_null = sqlite3_column_type(song, 1) == SQLITE_NULL;
    long long m3u = sqlite3_column_int64(song, 2);
    if (!text) { status = 404; goto done; }
    file = (unsigned char *)strdup((const char *)text);
    if (!file) goto done;
    /* An existing favorite in the library's context is simply confirmed. */
    long long existing = love_row(db, file, track, track_null, m3u, &status);
    if (status == 200) { *already = 1; *love_id = existing; goto done; }
    if (status != 404) goto done;
    status = 500;
    if (sqlite3_prepare_v2(db, FAVORITE_ADD, -1, &insert, NULL) != SQLITE_OK) goto done;
    sqlite3_bind_int64(insert, 1, song_id);
    rc = sqlite3_step(insert);
    /* MY_LOVE is UNIQUE(PATH, TRACK): a favorite set while a list or a folder of the same file
     * played (track number 0 there) takes the place of one without a track number (409). */
    if (rc != SQLITE_DONE || sqlite3_changes(db) != 1) { status = rc == SQLITE_BUSY ? 503 : rc == SQLITE_CONSTRAINT ? 409 : 500; goto done; }
    if (sqlite3_exec(db, "COMMIT", NULL, NULL, NULL) != SQLITE_OK) { status = 503; goto done; }
    begun = 0;
    /* Confirmation is the row read back after the commit, not the insert's success. */
    *love_id = love_row(db, file, track, track_null, m3u, &status);
    if (status == 404) status = 500;
done:
    if (song) sqlite3_finalize(song);
    if (insert) sqlite3_finalize(insert);
    if (begun) sqlite3_exec(db, "ROLLBACK", NULL, NULL, NULL);
    free(file);
    sqlite3_close(db);
    return status;
}
