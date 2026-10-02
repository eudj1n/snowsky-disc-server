#define _POSIX_C_SOURCE 200809L
#include "store.h"
#include "civetweb.h"
#include "framing.h"
#include "jsonutil.h"
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_TOKENS 16384
#define MAX_BATCH 100
#define MAX_LIST 500
#define DEFAULT_LIST 100
#define MAX_OUTPUT (1024 * 1024) /* a page of records; the buffer allows a little more */
#define MAX_QUERY 4096
#define MAX_PARAMS 16
#define MAX_PATH 1023

/* ---- catalog ---- */

static int name_ok(const char *s) {
    size_t n = strlen(s);
    if (n < 2 || n > 40 || !islower((unsigned char)s[0])) return 0;
    for (size_t i = 0; i < n; i++)
        if (!islower((unsigned char)s[i]) && !isdigit((unsigned char)s[i]) && s[i] != '_') return 0;
    return 1;
}

static int field_index(const disc_collection *c, const char *name) {
    for (size_t i = 0; i < c->field_count; i++) if (!strcmp(c->fields[i].name, name)) return (int)i;
    return -1;
}

static int parse_field(const char *json, const jsmntok_t *t, int key, int value, disc_store_field *f) {
    char type[8], pattern[520];
    long long v;
    int i;
    if (disc_json_string(json, &t[key], f->name, sizeof(f->name)) <= 0 || !name_ok(f->name) || t[value].type != JSMN_OBJECT) return -1;
    if ((i = disc_json_find(json, t, value, "type")) < 0 || disc_json_string(json, &t[i], type, sizeof(type)) <= 0) return -1;
    if (!strcmp(type, "text")) f->type = DISC_FIELD_TEXT;
    else if (!strcmp(type, "int")) f->type = DISC_FIELD_INT;
    else if (!strcmp(type, "bool")) f->type = DISC_FIELD_BOOL;
    else if (!strcmp(type, "path")) f->type = DISC_FIELD_PATH;
    else if (!strcmp(type, "json")) f->type = DISC_FIELD_JSON;
    else return -1;
    if ((i = disc_json_find(json, t, value, "required")) >= 0 && disc_json_boolean(json, &t[i], &f->required)) return -1;
    if (f->type == DISC_FIELD_TEXT || f->type == DISC_FIELD_JSON) {
        long long limit = f->type == DISC_FIELD_TEXT ? 4096 : 16384;
        if ((i = disc_json_find(json, t, value, "max_length")) < 0 || disc_json_number(json, &t[i], 1, limit, &v)) return -1;
        f->max_length = (size_t)v;
    }
    if (f->type == DISC_FIELD_PATH) f->max_length = MAX_PATH;
    if (f->type == DISC_FIELD_INT) {
        const long long bound = 9007199254740991LL;
        if ((i = disc_json_find(json, t, value, "min")) < 0 || disc_json_number(json, &t[i], -bound, bound, &f->min)) return -1;
        if ((i = disc_json_find(json, t, value, "max")) < 0 || disc_json_number(json, &t[i], f->min, bound, &f->max)) return -1;
    }
    if ((i = disc_json_find(json, t, value, "pattern")) >= 0) {
        if (f->type != DISC_FIELD_TEXT || disc_json_string(json, &t[i], pattern, sizeof(pattern)) < 0 || strstr(pattern, "(?")) return -1;
        char anchored[528];
        snprintf(anchored, sizeof(anchored), "^(%s)$", pattern);
        if (regcomp(&f->pattern, anchored, REG_EXTENDED | REG_NOSUB)) return -1;
        f->has_pattern = 1;
    }
    return 0;
}

static int parse_names(const char *json, const jsmntok_t *t, int array, const disc_collection *c, size_t *out, size_t max, size_t *count) {
    if (array < 0 || t[array].type != JSMN_ARRAY || (size_t)t[array].size > max) return -1;
    for (int n = 0; n < t[array].size; n++) {
        char name[41];
        int field;
        if (disc_json_string(json, &t[array + 1 + n], name, sizeof(name)) <= 0 || (field = field_index(c, name)) < 0) return -1;
        for (size_t k = 0; k < *count; k++) if (out[k] == (size_t)field) return -1;
        out[(*count)++] = (size_t)field;
    }
    return 0;
}

static int parse_collection(const char *json, const jsmntok_t *t, int key, int value, disc_collection *c) {
    long long v;
    int i;
    if (disc_json_string(json, &t[key], c->name, sizeof(c->name)) <= 0 || !name_ok(c->name) || t[value].type != JSMN_OBJECT) return -1;
    int fields = disc_json_find(json, t, value, "fields");
    if (fields < 0 || t[fields].type != JSMN_OBJECT || t[fields].size < 1 || t[fields].size > DISC_STORE_MAX_FIELDS) return -1;
    int k = fields + 1;
    for (int n = 0; n < t[fields].size; n++) {
        disc_store_field *f = &c->fields[c->field_count];
        if (parse_field(json, t, k, k + 1, f)) { if (f->has_pattern) regfree(&f->pattern); return -1; }
        c->field_count++;
        if (field_index(c, f->name) != (int)c->field_count - 1) return -1;
        k = disc_json_skip(t, k + 1);
    }
    if (parse_names(json, t, disc_json_find(json, t, value, "key"), c, c->key, DISC_STORE_MAX_KEY, &c->key_count) || !c->key_count) return -1;
    for (size_t n = 0; n < c->key_count; n++) {
        int type = c->fields[c->key[n]].type;
        if (type != DISC_FIELD_TEXT && type != DISC_FIELD_INT && type != DISC_FIELD_PATH) return -1;
    }
    if (!c->fields[c->key[0]].required) return -1;
    if ((i = disc_json_find(json, t, value, "index")) >= 0 &&
        parse_names(json, t, i, c, c->index, DISC_STORE_MAX_FIELDS, &c->index_count)) return -1;
    for (size_t n = 0; n < c->index_count; n++) if (c->fields[c->index[n]].type == DISC_FIELD_JSON) return -1;
    if ((i = disc_json_find(json, t, value, "max_records")) < 0 || disc_json_number(json, &t[i], 1, 100000, &c->max_records)) return -1;
    if ((i = disc_json_find(json, t, value, "max_record_bytes")) < 0 || disc_json_number(json, &t[i], 64, 16384, &v)) return -1;
    c->max_record_bytes = (size_t)v;
    if ((i = disc_json_find(json, t, value, "skip")) >= 0 && disc_json_boolean(json, &t[i], &c->skip)) return -1;
    /* The skip rule matches a track: a path key, optionally with the CUE title. */
    if (c->skip && (c->fields[c->key[0]].type != DISC_FIELD_PATH || c->key_count > 2 ||
                    (c->key_count == 2 && (strcmp(c->fields[c->key[1]].name, "title") || c->fields[c->key[1]].type != DISC_FIELD_TEXT))))
        return -1;
    return 0;
}

void disc_store_free(disc_store *store) {
    for (size_t c = 0; c < DISC_STORE_MAX_COLLECTIONS; c++)
        for (size_t f = 0; f < DISC_STORE_MAX_FIELDS; f++)
            if (store->collections[c].fields[f].has_pattern) regfree(&store->collections[c].fields[f].pattern);
    memset(store, 0, sizeof(*store));
}

int disc_store_parse(disc_store *store, const char *json, size_t length, const char *expected) {
    memset(store, 0, sizeof(*store));
    if (!json || !length || length > DISC_STORE_MAX_JSON || !expected || strlen(expected) != 64 || memchr(json, 0, length)) return -1;
    jsmntok_t *t = NULL;
    if (disc_json_parse(json, length, &t, MAX_TOKENS) < 0) return -1;
    int ok = 0, i;
    long long v;
    char profile[65];
    if ((i = disc_json_find(json, t, 0, "schema_version")) < 0 || disc_json_number(json, &t[i], 1, 1, &v)) goto done;
    if ((i = disc_json_find(json, t, 0, "api")) < 0 || disc_json_number(json, &t[i], 1, 1, &v)) goto done;
    if ((i = disc_json_find(json, t, 0, "profile_sha256")) < 0 || disc_json_sha256(json, &t[i], profile) || strcmp(profile, expected)) goto done;
    int collections = disc_json_find(json, t, 0, "collections");
    if (collections < 0 || t[collections].type != JSMN_OBJECT || t[collections].size < 1 ||
        t[collections].size > DISC_STORE_MAX_COLLECTIONS) goto done;
    int k = collections + 1;
    for (int n = 0; n < t[collections].size; n++) {
        if (parse_collection(json, t, k, k + 1, &store->collections[store->count])) goto done;
        store->count++;
        for (size_t m = 0; m + 1 < store->count; m++)
            if (!strcmp(store->collections[m].name, store->collections[store->count - 1].name)) goto done;
        k = disc_json_skip(t, k + 1);
    }
    ok = 1;
done:
    free(t);
    if (!ok) { disc_store_free(store); return -1; }
    return 0;
}

static const disc_collection *find_collection(const disc_store *store, const char *name, size_t length) {
    for (size_t i = 0; i < store->count; i++)
        if (strlen(store->collections[i].name) == length && !memcmp(store->collections[i].name, name, length)) return &store->collections[i];
    return NULL;
}

/* ---- values ---- */

/* One field of a record: a number, or text (decoded), or raw JSON (a slice). */
typedef struct {
    int present, is_null;
    long long number;
    char *text;
    size_t length;
    const char *raw;
    size_t raw_length;
} slot;

static void free_slots(slot *slots, size_t n) {
    for (size_t i = 0; i < n; i++) free(slots[i].text);
    memset(slots, 0, n * sizeof(*slots));
}

static int no_controls(const char *s, size_t n) {
    for (size_t i = 0; i < n; i++) if ((unsigned char)s[i] < 0x20 || s[i] == 0x7f) return 0;
    return 1;
}

/* A path on the card: absolute below the music root, no empty, dot or hidden components. */
static int card_path(const char *root, const char *p, size_t n) {
    size_t r = root ? strlen(root) : 0;
    if (!r || n <= r + 1 || n > MAX_PATH || strncmp(p, root, r) || p[r] != '/') return 0;
    if (strstr(p + r, "//") || strstr(p + r, "/.") || p[n - 1] == '/') return 0;
    return 1;
}

static const char *check_text(const disc_store_field *f, const char *root, const char *s, size_t n) {
    if (!disc_utf8((const unsigned char *)s, n) || !no_controls(s, n) || strlen(s) != n) return "text must be UTF-8 without control characters";
    if (n > f->max_length) return "text too long";
    if (f->type == DISC_FIELD_PATH) return card_path(root, s, n) ? NULL : "not a path on the card";
    if (f->has_pattern && regexec(&f->pattern, s, 0, NULL, 0)) return "text does not match its pattern";
    return NULL;
}

/* Exactly one JSON object made of valid values; returns its token count, or -1. */
static int one_object(const char *json, size_t length, jsmntok_t **t) {
    int count = disc_json_parse(json, length, t, MAX_TOKENS), next;
    if (count > 0 && disc_json_valid(json, *t, 0, &next) && next == count) return count;
    free(*t);
    *t = NULL;
    return -1;
}

/* One field's JSON value into its slot; NULL on success, else the problem. */
static const char *json_slot(const disc_store_field *f, const char *root, const char *json, const jsmntok_t *t, int i, slot *s) {
    s->present = 1;
    if (t[i].type == JSMN_PRIMITIVE && t[i].end - t[i].start == 4 && !memcmp(json + t[i].start, "null", 4) && f->type != DISC_FIELD_JSON) {
        s->is_null = 1;
        return f->required ? "a required field is null" : NULL;
    }
    switch (f->type) {
    case DISC_FIELD_INT:
        return disc_json_number(json, &t[i], f->min, f->max, &s->number) ? "integer out of range" : NULL;
    case DISC_FIELD_BOOL: {
        int b;
        if (disc_json_boolean(json, &t[i], &b)) return "not a boolean";
        s->number = b;
        return NULL;
    }
    case DISC_FIELD_JSON: {
        int next;
        if (!disc_json_valid(json, t, i, &next)) return "not valid JSON";
        s->raw = json + t[i].start - (t[i].type == JSMN_STRING);
        s->raw_length = (size_t)(t[i].end - t[i].start) + 2 * (t[i].type == JSMN_STRING);
        return s->raw_length > f->max_length ? "value too long" : NULL;
    }
    default: {
        size_t capacity = (size_t)(t[i].end - t[i].start) + 1;
        if (t[i].type != JSMN_STRING) return "not a string";
        s->text = malloc(capacity);
        if (!s->text) return "out of memory";
        int n = disc_json_string(json, &t[i], s->text, capacity);
        if (n < 0) return "string with control characters or \\u escapes";
        s->length = (size_t)n;
        return check_text(f, root, s->text, s->length);
    }
    }
}

/* A record (or with key_only, a key) as a JSON object of declared fields. */
static const char *record_slots(const disc_collection *c, const char *root, const char *json, const jsmntok_t *t, int object,
                                int key_only, slot *slots) {
    if (t[object].type != JSMN_OBJECT) return "a record is a JSON object";
    int k = object + 1;
    for (int m = 0; m < t[object].size; m++) {
        char name[41];
        int field = -1;
        if (disc_json_string(json, &t[k], name, sizeof(name)) > 0) field = field_index(c, name);
        if (field < 0) return "unknown field";
        if (key_only) {
            int in_key = 0;
            for (size_t n = 0; n < c->key_count; n++) if (c->key[n] == (size_t)field) in_key = 1;
            if (!in_key) return "only key fields name a record";
        }
        if (slots[field].present) return "duplicate field";
        const char *problem = json_slot(&c->fields[field], root, json, t, k + 1, &slots[field]);
        if (problem) return problem;
        k = disc_json_skip(t, k + 1);
    }
    for (size_t f = 0; f < c->field_count; f++) {
        int in_key = 0;
        for (size_t n = 0; n < c->key_count; n++) if (c->key[n] == f) in_key = 1;
        if ((key_only ? in_key : 1) && c->fields[f].required && (!slots[f].present || slots[f].is_null)) return "a required field is missing";
    }
    return NULL;
}

static void put_slot(disc_buffer *b, const disc_store_field *f, const slot *s) {
    if (!s->present || s->is_null) disc_buffer_text(b, "null");
    else if (f->type == DISC_FIELD_INT) disc_buffer_int(b, s->number);
    else if (f->type == DISC_FIELD_BOOL) disc_buffer_text(b, s->number ? "true" : "false");
    else if (f->type == DISC_FIELD_JSON) disc_buffer_put(b, s->raw, s->raw_length);
    else disc_buffer_string(b, s->text, s->length);
}

/* The canonical key: a JSON array of the key fields, absent ones null. */
static void put_key(disc_buffer *b, const disc_collection *c, const slot *slots) {
    disc_buffer_text(b, "[");
    for (size_t n = 0; n < c->key_count; n++) {
        if (n) disc_buffer_text(b, ",");
        put_slot(b, &c->fields[c->key[n]], &slots[c->key[n]]);
    }
    disc_buffer_text(b, "]");
}

/* The canonical value: declared fields in declaration order, absent ones left out. */
static void put_value(disc_buffer *b, const disc_collection *c, const slot *slots) {
    disc_buffer_text(b, "{");
    int first = 1;
    for (size_t f = 0; f < c->field_count; f++) {
        if (!slots[f].present || slots[f].is_null) continue;
        if (!first) disc_buffer_text(b, ",");
        first = 0;
        disc_buffer_string(b, c->fields[f].name, strlen(c->fields[f].name));
        disc_buffer_text(b, ":");
        put_slot(b, &c->fields[f], &slots[f]);
    }
    disc_buffer_text(b, "}");
}

/* ---- query strings ---- */

typedef struct { char name[41]; char *value; } param;
typedef struct { param items[MAX_PARAMS]; size_t count; char *storage; } params;

static void free_params(params *p) { free(p->storage); memset(p, 0, sizeof(*p)); }

/* name=value pairs with distinct lower-case names; values URL-decoded. */
static int parse_params(const char *query, params *p) {
    memset(p, 0, sizeof(*p));
    if (!query || !*query) return 0;
    size_t n = strlen(query);
    if (n > MAX_QUERY) return -1;
    p->storage = malloc(n + 1);
    if (!p->storage) return -1;
    size_t used = 0;
    const char *part = query;
    while (*part) {
        const char *end = strchr(part, '&');
        size_t length = end ? (size_t)(end - part) : strlen(part);
        const char *equals = memchr(part, '=', length);
        if (!equals || p->count == MAX_PARAMS || (size_t)(equals - part) >= sizeof(p->items[0].name)) return -1;
        param *item = &p->items[p->count];
        memcpy(item->name, part, (size_t)(equals - part));
        item->name[equals - part] = 0;
        if (!name_ok(item->name)) return -1;
        for (size_t k = 0; k < p->count; k++) if (!strcmp(p->items[k].name, item->name)) return -1;
        int decoded = mg_url_decode(equals + 1, (int)(part + length - equals - 1), p->storage + used, (int)(n + 1 - used), 1);
        if (decoded < 0) return -1;
        item->value = p->storage + used;
        used += (size_t)decoded + 1;
        p->count++;
        if (!end) break;
        part = end + 1;
    }
    return 0;
}

static const char *param_value(const params *p, const char *name) {
    for (size_t i = 0; i < p->count; i++) if (!strcmp(p->items[i].name, name)) return p->items[i].value;
    return NULL;
}

/* A field's value from text (a query parameter) into its slot. */
static const char *text_slot(const disc_store_field *f, const char *root, const char *raw, slot *s) {
    s->present = 1;
    if (f->type == DISC_FIELD_INT) {
        char *end;
        size_t n = strlen(raw);
        if (!n || n > 17 || !(isdigit((unsigned char)raw[0]) || raw[0] == '-')) return "not an integer";
        s->number = strtoll(raw, &end, 10);
        return *end || s->number < f->min || s->number > f->max ? "integer out of range" : NULL;
    }
    if (f->type == DISC_FIELD_BOOL) {
        if (!strcmp(raw, "true")) s->number = 1;
        else if (!strcmp(raw, "false")) s->number = 0;
        else return "not a boolean";
        return NULL;
    }
    if (f->type == DISC_FIELD_JSON) return "JSON fields are not compared";
    s->length = strlen(raw);
    s->text = strdup(raw);
    if (!s->text) return "out of memory";
    return check_text(f, root, s->text, s->length);
}

/* ---- database ---- */

/* The declared indexes, and no others of the store's. */
static int sync_indexes(sqlite3 *db, const disc_store *store) {
    char sql[256];
    for (size_t c = 0; c < store->count; c++)
        for (size_t i = 0; i < store->collections[c].index_count; i++) {
            const char *collection = store->collections[c].name, *field = store->collections[c].fields[store->collections[c].index[i]].name;
            snprintf(sql, sizeof(sql), "CREATE INDEX IF NOT EXISTS \"store_%s_%s\" ON records(collection, (value ->> '$.%s'))",
                     collection, field, field);
            if (sqlite3_exec(db, sql, NULL, NULL, NULL) != SQLITE_OK) return -1;
        }
    sqlite3_stmt *stmt = NULL;
    char stale[32][96];
    size_t count = 0;
    if (sqlite3_prepare_v2(db, "SELECT name FROM sqlite_schema WHERE type = 'index' AND name LIKE 'store\\_%' ESCAPE '\\'",
                           -1, &stmt, NULL) != SQLITE_OK) return -1;
    while (sqlite3_step(stmt) == SQLITE_ROW && count < 32) {
        const char *name = (const char *)sqlite3_column_text(stmt, 0);
        if (!name || strlen(name) >= sizeof(stale[0]) || strspn(name, "abcdefghijklmnopqrstuvwxyz0123456789_") != strlen(name)) continue;
        int declared = 0;
        for (size_t c = 0; c < store->count && !declared; c++)
            for (size_t i = 0; i < store->collections[c].index_count; i++) {
                snprintf(sql, sizeof(sql), "store_%s_%s", store->collections[c].name,
                         store->collections[c].fields[store->collections[c].index[i]].name);
                if (!strcmp(sql, name)) declared = 1;
            }
        if (!declared) snprintf(stale[count++], sizeof(stale[0]), "%s", name);
    }
    sqlite3_finalize(stmt);
    for (size_t i = 0; i < count; i++) {
        snprintf(sql, sizeof(sql), "DROP INDEX IF EXISTS \"%.95s\"", stale[i]);
        if (sqlite3_exec(db, sql, NULL, NULL, NULL) != SQLITE_OK) return -1;
    }
    return 0;
}

static long long count_rows(sqlite3 *db, const char *collection, const char *filter_field, const disc_store_field *f, const slot *filter) {
    char sql[160];
    sqlite3_stmt *stmt = NULL;
    long long n = -1;
    snprintf(sql, sizeof(sql), "SELECT count(*) FROM records WHERE collection = ?%s%s%s",
             filter_field ? " AND value ->> '$." : "", filter_field ? filter_field : "", filter_field ? "' = ?" : "");
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, NULL) == SQLITE_OK) {
        sqlite3_bind_text(stmt, 1, collection, -1, SQLITE_STATIC);
        if (filter_field) {
            if (f->type == DISC_FIELD_INT || f->type == DISC_FIELD_BOOL) sqlite3_bind_int64(stmt, 2, filter->number);
            else sqlite3_bind_text(stmt, 2, filter->text, (int)filter->length, SQLITE_STATIC);
        }
        if (sqlite3_step(stmt) == SQLITE_ROW) n = sqlite3_column_int64(stmt, 0);
    }
    sqlite3_finalize(stmt);
    return n;
}

/* A stored key or value is embedded only when it is still valid JSON. */
static int stored_json(const unsigned char *text, int bytes, int array) {
    if (!text || bytes <= 1 || bytes > 32768 || strlen((const char *)text) != (size_t)bytes) return 0;
    size_t n = (size_t)bytes + (array ? 6 : 0);
    char *wrapped = malloc(n + 1);
    if (!wrapped) return 0;
    if (array) snprintf(wrapped, n + 1, "{\"k\":%s}", (const char *)text);
    else memcpy(wrapped, text, n + 1);
    jsmntok_t *t = NULL;
    int ok = one_object(wrapped, n, &t) > 0 && (!array || (t[0].size == 1 && t[2].type == JSMN_ARRAY));
    free(t);
    free(wrapped);
    return ok;
}

static void put_row(disc_buffer *b, sqlite3_stmt *stmt, int key_column) {
    disc_buffer_text(b, "{\"key\":");
    disc_buffer_put(b, (const char *)sqlite3_column_text(stmt, key_column), (size_t)sqlite3_column_bytes(stmt, key_column));
    disc_buffer_text(b, ",\"value\":");
    disc_buffer_put(b, (const char *)sqlite3_column_text(stmt, key_column + 1), (size_t)sqlite3_column_bytes(stmt, key_column + 1));
    disc_buffer_text(b, ",\"updated\":");
    disc_buffer_int(b, sqlite3_column_int64(stmt, key_column + 2));
    disc_buffer_text(b, "}");
}

static int row_valid(sqlite3_stmt *stmt, int key_column) {
    return sqlite3_column_type(stmt, key_column + 2) == SQLITE_INTEGER &&
           stored_json(sqlite3_column_text(stmt, key_column), sqlite3_column_bytes(stmt, key_column), 1) &&
           stored_json(sqlite3_column_text(stmt, key_column + 1), sqlite3_column_bytes(stmt, key_column + 1), 0);
}

/* ---- operations ---- */

typedef struct {
    const disc_store *store;
    const disc_collection *c;
    disc_database *database;
    const disc_store_request *r;
    disc_buffer *out;
    const char *problem;
} context;

static void begin_reply(context *x) {
    disc_buffer_text(x->out, "{\"collection\":");
    disc_buffer_string(x->out, x->c->name, strlen(x->c->name));
}

static int op_list(context *x, sqlite3 *db, const params *p) {
    static const char *const allowed[] = {"limit", "offset", "order", "desc", "field", "value"};
    for (size_t i = 0; i < p->count; i++) {
        int known = 0;
        for (size_t k = 0; k < 6; k++) if (!strcmp(p->items[i].name, allowed[k])) known = 1;
        if (!known) { x->problem = "unknown parameter"; return 400; }
    }
    long long limit = DEFAULT_LIST, offset = 0;
    const char *text;
    char *end;
    if ((text = param_value(p, "limit")) && ((limit = strtoll(text, &end, 10)) < 1 || limit > MAX_LIST || *end || !*text)) { x->problem = "limit is 1..500"; return 400; }
    if ((text = param_value(p, "offset")) && ((offset = strtoll(text, &end, 10)) < 0 || offset > 1000000 || *end || !*text)) { x->problem = "offset is 0..1000000"; return 400; }
    const char *desc = param_value(p, "desc");
    if (desc && strcmp(desc, "0") && strcmp(desc, "1")) { x->problem = "desc is 0 or 1"; return 400; }
    const char *direction = desc && !strcmp(desc, "1") ? "DESC" : "ASC";
    const char *order = param_value(p, "order"), *field = param_value(p, "field"), *value = param_value(p, "value");
    char order_by[128];
    if (!order) snprintf(order_by, sizeof(order_by), "id %s", direction);
    else if (!strcmp(order, "updated")) snprintf(order_by, sizeof(order_by), "updated_at %s, id %s", direction, direction);
    else {
        int f = field_index(x->c, order);
        if (f < 0 || x->c->fields[f].type == DISC_FIELD_JSON) { x->problem = "order names a scalar field or updated"; return 400; }
        snprintf(order_by, sizeof(order_by), "value ->> '$.%s' %s, id %s", order, direction, direction);
    }
    slot filter = {0};
    int f = -1;
    if (!!field != !!value) { x->problem = "field and value go together"; return 400; }
    if (field) {
        f = field_index(x->c, field);
        if (f < 0) { x->problem = "unknown field"; return 400; }
        if ((x->problem = text_slot(&x->c->fields[f], x->r->music_root, value, &filter))) { free_slots(&filter, 1); return 400; }
    }
    long long total = db ? count_rows(db, x->c->name, field, f >= 0 ? &x->c->fields[f] : NULL, &filter) : 0;
    if (total < 0) { free_slots(&filter, 1); return 503; }
    begin_reply(x);
    disc_buffer_text(x->out, ",\"records\":[");
    long long returned = 0;
    int truncated = 0;
    if (db && total > offset) {
        char sql[320];
        snprintf(sql, sizeof(sql), "SELECT key, value, updated_at FROM records WHERE collection = ?1%s%s%s ORDER BY %s LIMIT ?3 OFFSET ?4",
                 field ? " AND value ->> '$." : "", field ? field : "", field ? "' = ?2" : "", order_by);
        sqlite3_stmt *stmt = NULL;
        if (sqlite3_prepare_v2(db, sql, -1, &stmt, NULL) != SQLITE_OK) { free_slots(&filter, 1); return 503; }
        sqlite3_bind_text(stmt, 1, x->c->name, -1, SQLITE_STATIC);
        if (field) {
            if (x->c->fields[f].type == DISC_FIELD_INT || x->c->fields[f].type == DISC_FIELD_BOOL) sqlite3_bind_int64(stmt, 2, filter.number);
            else sqlite3_bind_text(stmt, 2, filter.text, (int)filter.length, SQLITE_STATIC);
        }
        sqlite3_bind_int64(stmt, 3, limit);
        sqlite3_bind_int64(stmt, 4, offset);
        int rc;
        while ((rc = sqlite3_step(stmt)) == SQLITE_ROW) {
            if (!row_valid(stmt, 0)) continue;
            size_t before = x->out->used;
            if (returned) disc_buffer_text(x->out, ",");
            put_row(x->out, stmt, 0);
            if (x->out->overflow || x->out->used > MAX_OUTPUT) {
                x->out->overflow = 0;
                x->out->used = before;
                x->out->data[before] = 0;
                truncated = 1;
                break;
            }
            returned++;
        }
        sqlite3_finalize(stmt);
        if (rc != SQLITE_ROW && rc != SQLITE_DONE) { free_slots(&filter, 1); return 503; }
    }
    free_slots(&filter, 1);
    if (offset + returned < total) truncated = 1;
    disc_buffer_text(x->out, "],\"total\":");
    disc_buffer_int(x->out, total);
    disc_buffer_text(x->out, ",\"offset\":");
    disc_buffer_int(x->out, offset);
    disc_buffer_text(x->out, truncated ? ",\"truncated\":true}" : ",\"truncated\":false}");
    return 200;
}

static int op_count(context *x, sqlite3 *db, const params *p) {
    for (size_t i = 0; i < p->count; i++)
        if (strcmp(p->items[i].name, "field") && strcmp(p->items[i].name, "value")) { x->problem = "unknown parameter"; return 400; }
    const char *field = param_value(p, "field"), *value = param_value(p, "value");
    if (!!field != !!value) { x->problem = "field and value go together"; return 400; }
    slot filter = {0};
    int f = -1;
    if (field) {
        f = field_index(x->c, field);
        if (f < 0) { x->problem = "unknown field"; return 400; }
        if ((x->problem = text_slot(&x->c->fields[f], x->r->music_root, value, &filter))) { free_slots(&filter, 1); return 400; }
    }
    long long n = db ? count_rows(db, x->c->name, field, f >= 0 ? &x->c->fields[f] : NULL, &filter) : 0;
    free_slots(&filter, 1);
    if (n < 0) return 503;
    begin_reply(x);
    disc_buffer_text(x->out, ",\"count\":");
    disc_buffer_int(x->out, n);
    disc_buffer_text(x->out, "}");
    return 200;
}

/* The key named by the query parameters (key fields only). */
static int key_from_params(context *x, const params *p, disc_buffer *key) {
    slot slots[DISC_STORE_MAX_FIELDS] = {0};
    for (size_t i = 0; i < p->count; i++) {
        int f = field_index(x->c, p->items[i].name), in_key = 0;
        for (size_t n = 0; f >= 0 && n < x->c->key_count; n++) if (x->c->key[n] == (size_t)f) in_key = 1;
        if (!in_key) { x->problem = "only key fields name a record"; free_slots(slots, DISC_STORE_MAX_FIELDS); return 400; }
        if ((x->problem = text_slot(&x->c->fields[f], x->r->music_root, p->items[i].value, &slots[f]))) {
            free_slots(slots, DISC_STORE_MAX_FIELDS);
            return 400;
        }
    }
    for (size_t n = 0; n < x->c->key_count; n++)
        if (x->c->fields[x->c->key[n]].required && !slots[x->c->key[n]].present) {
            x->problem = "a required key field is missing";
            free_slots(slots, DISC_STORE_MAX_FIELDS);
            return 400;
        }
    put_key(key, x->c, slots);
    free_slots(slots, DISC_STORE_MAX_FIELDS);
    return key->overflow ? 500 : 200;
}

static int op_get(context *x, sqlite3 *db, const params *p) {
    disc_buffer key = {0};
    int status = key_from_params(x, p, &key);
    if (status != 200) { free(key.data); return status; }
    status = 404;
    x->problem = "no such record";
    if (db) {
        sqlite3_stmt *stmt = NULL;
        if (sqlite3_prepare_v2(db, "SELECT key, value, updated_at FROM records WHERE collection = ? AND key = ?", -1, &stmt, NULL) != SQLITE_OK) status = 503;
        else {
            sqlite3_bind_text(stmt, 1, x->c->name, -1, SQLITE_STATIC);
            sqlite3_bind_text(stmt, 2, key.data, (int)key.used, SQLITE_STATIC);
            int rc = sqlite3_step(stmt);
            if (rc == SQLITE_ROW && row_valid(stmt, 0)) {
                begin_reply(x);
                disc_buffer_text(x->out, ",\"record\":");
                put_row(x->out, stmt, 0);
                disc_buffer_text(x->out, "}");
                status = 200;
            } else if (rc != SQLITE_ROW && rc != SQLITE_DONE) status = 503;
        }
        sqlite3_finalize(stmt);
    }
    free(key.data);
    return status;
}

static int exec_bound(sqlite3 *db, const char *sql, const char *collection, const disc_buffer *key, const disc_buffer *value, long long now) {
    sqlite3_stmt *stmt = NULL;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, NULL) != SQLITE_OK) return -1;
    sqlite3_bind_text(stmt, 1, collection, -1, SQLITE_STATIC);
    sqlite3_bind_text(stmt, 2, key->data, (int)key->used, SQLITE_STATIC);
    if (value) {
        sqlite3_bind_text(stmt, 3, value->data, (int)value->used, SQLITE_STATIC);
        sqlite3_bind_int64(stmt, 4, now);
    }
    int rc = sqlite3_step(stmt);
    int result = rc == SQLITE_ROW ? 1 : rc == SQLITE_DONE ? 0 : -1;
    sqlite3_finalize(stmt);
    return result;
}

static const char EXISTS[] = "SELECT 1 FROM records WHERE collection = ?1 AND key = ?2";
static const char UPSERT[] = "INSERT INTO records(collection, key, value, updated_at) VALUES (?1, ?2, ?3, ?4) "
                             "ON CONFLICT(collection, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at";
static const char REMOVE[] = "DELETE FROM records WHERE collection = ?1 AND key = ?2 RETURNING 1";

/* A validated put or delete of one batch or single request. */
typedef struct { disc_buffer key, value; int remove; } change;

static void free_changes(change *changes, size_t n) {
    for (size_t i = 0; i < n; i++) { free(changes[i].key.data); free(changes[i].value.data); }
}

static int parse_change(context *x, const char *json, const jsmntok_t *t, int object, int remove, change *out) {
    slot slots[DISC_STORE_MAX_FIELDS] = {0};
    x->problem = record_slots(x->c, x->r->music_root, json, t, object, remove, slots);
    if (!x->problem) {
        put_key(&out->key, x->c, slots);
        if (!remove) put_value(&out->value, x->c, slots);
        out->remove = remove;
        if (!remove && out->value.used > x->c->max_record_bytes) x->problem = "record too large";
        if (out->key.overflow || out->value.overflow) x->problem = "out of memory";
    }
    free_slots(slots, DISC_STORE_MAX_FIELDS);
    return x->problem ? 400 : 200;
}

/* Applies validated changes in one transaction; the collection's limit holds at the end. */
static int apply(context *x, sqlite3 *db, change *changes, size_t n, long long *created, long long *deleted) {
    if (sync_indexes(db, x->store) || sqlite3_exec(db, "BEGIN IMMEDIATE", NULL, NULL, NULL) != SQLITE_OK) return 503;
    *created = *deleted = 0;
    int status = 200;
    for (size_t i = 0; i < n && status == 200; i++) {
        change *ch = &changes[i];
        if (ch->remove) {
            int r = exec_bound(db, REMOVE, x->c->name, &ch->key, NULL, 0);
            if (r < 0) status = 503;
            else *deleted += r;
            continue;
        }
        int existed = exec_bound(db, EXISTS, x->c->name, &ch->key, NULL, 0);
        if (existed < 0 || exec_bound(db, UPSERT, x->c->name, &ch->key, &ch->value, x->r->now) < 0) status = 503;
        else if (!existed) (*created)++;
    }
    if (status == 200) {
        long long total = count_rows(db, x->c->name, NULL, NULL, NULL);
        if (total < 0) status = 503;
        else if (total > x->c->max_records) { status = 409; x->problem = "the collection is full"; }
    }
    if (status == 200 && sqlite3_exec(db, "COMMIT", NULL, NULL, NULL) != SQLITE_OK) status = 503;
    if (status != 200) sqlite3_exec(db, "ROLLBACK", NULL, NULL, NULL);
    return status;
}

static int op_put(context *x, sqlite3 *db) {
    jsmntok_t *t = NULL;
    change ch = {0};
    if (!x->r->body || one_object(x->r->body, x->r->body_length, &t) < 0) { x->problem = "a record is a JSON object"; return 400; }
    int status = parse_change(x, x->r->body, t, 0, 0, &ch);
    long long created = 0, deleted = 0;
    if (status == 200) status = apply(x, db, &ch, 1, &created, &deleted);
    if (status == 200) {
        begin_reply(x);
        disc_buffer_text(x->out, ",\"key\":");
        disc_buffer_put(x->out, ch.key.data, ch.key.used);
        disc_buffer_text(x->out, created ? ",\"created\":true,\"updated\":" : ",\"created\":false,\"updated\":");
        disc_buffer_int(x->out, x->r->now);
        disc_buffer_text(x->out, "}");
    }
    free_changes(&ch, 1);
    free(t);
    return status;
}

static int op_delete(context *x, sqlite3 *db, const params *p) {
    change ch = {0};
    ch.remove = 1;
    int status = key_from_params(x, p, &ch.key);
    long long created = 0, deleted = 0;
    if (status == 200) status = apply(x, db, &ch, 1, &created, &deleted);
    if (status == 200) {
        begin_reply(x);
        disc_buffer_text(x->out, ",\"key\":");
        disc_buffer_put(x->out, ch.key.data, ch.key.used);
        disc_buffer_text(x->out, deleted ? ",\"deleted\":true}" : ",\"deleted\":false}");
    }
    free_changes(&ch, 1);
    return status;
}

static int op_batch(context *x, sqlite3 *db) {
    jsmntok_t *t = NULL;
    if (!x->r->body || one_object(x->r->body, x->r->body_length, &t) < 0) { x->problem = "a batch is a JSON object"; return 400; }
    const char *json = x->r->body;
    int puts = -1, deletes = -1, k = 1;
    for (int m = 0; m < t[0].size; m++) {
        if (disc_json_eq(json, &t[k], "put") && puts < 0) puts = k + 1;
        else if (disc_json_eq(json, &t[k], "delete") && deletes < 0) deletes = k + 1;
        else { free(t); x->problem = "a batch has only put and delete"; return 400; }
        k = disc_json_skip(t, k + 1);
    }
    size_t n = (puts > 0 && t[puts].type == JSMN_ARRAY ? (size_t)t[puts].size : 0) +
               (deletes > 0 && t[deletes].type == JSMN_ARRAY ? (size_t)t[deletes].size : 0);
    if ((puts > 0 && t[puts].type != JSMN_ARRAY) || (deletes > 0 && t[deletes].type != JSMN_ARRAY) || !n || n > MAX_BATCH) {
        free(t);
        x->problem = "put and delete are arrays of 1..100 items together";
        return 400;
    }
    change *changes = calloc(n, sizeof(*changes));
    if (!changes) { free(t); return 503; }
    size_t used = 0;
    int status = 200;
    /* Deletes first, so one batch can replace a record under a new key. */
    for (int pass = 0; pass < 2 && status == 200; pass++) {
        int array = pass == 0 ? deletes : puts;
        if (array < 0) continue;
        int item = array + 1;
        for (int m = 0; m < t[array].size && status == 200; m++) {
            status = parse_change(x, json, t, item, pass == 0, &changes[used++]);
            item = disc_json_skip(t, item);
        }
    }
    long long created = 0, deleted = 0;
    if (status == 200) status = apply(x, db, changes, used, &created, &deleted);
    if (status == 200) {
        begin_reply(x);
        disc_buffer_text(x->out, ",\"put\":");
        disc_buffer_int(x->out, puts > 0 ? t[puts].size : 0);
        disc_buffer_text(x->out, ",\"created\":");
        disc_buffer_int(x->out, created);
        disc_buffer_text(x->out, ",\"deleted\":");
        disc_buffer_int(x->out, deleted);
        disc_buffer_text(x->out, "}");
    }
    free_changes(changes, used);
    free(changes);
    free(t);
    return status;
}

static int summary(const disc_store *store, sqlite3 *db, disc_buffer *out) {
    disc_buffer_text(out, "{\"collections\":{");
    for (size_t i = 0; i < store->count; i++) {
        const disc_collection *c = &store->collections[i];
        long long n = db ? count_rows(db, c->name, NULL, NULL, NULL) : 0;
        if (n < 0) return 503;
        if (i) disc_buffer_text(out, ",");
        disc_buffer_string(out, c->name, strlen(c->name));
        disc_buffer_text(out, ":{\"records\":");
        disc_buffer_int(out, n);
        disc_buffer_text(out, ",\"max_records\":");
        disc_buffer_int(out, c->max_records);
        disc_buffer_text(out, c->skip ? ",\"skip\":true}" : ",\"skip\":false}");
    }
    disc_buffer_text(out, "}}");
    return 200;
}

int disc_store_writes(const char *method) { return strcmp(method, "GET") != 0; }

int disc_store_handle(const disc_store *store, disc_database *database, const disc_store_request *r,
                      char **out, size_t *length, int *json) {
    disc_buffer b = {0};
    context x = {.store = store, .database = database, .r = r, .out = &b};
    params p;
    *out = NULL;
    *length = 0;
    *json = 0;
    int status;
    const char *operation = NULL;
    if (*r->path) {
        const char *name = r->path + 1, *slash = strchr(name, '/');
        if (r->path[0] != '/' || !slash || !(x.c = find_collection(store, name, (size_t)(slash - name)))) {
            status = 404; x.problem = "no such collection or operation"; goto text;
        }
        operation = slash + 1;
    }
    int writes = disc_store_writes(r->method);
    const char *method = !operation ? "GET" : !strcmp(operation, "records") || !strcmp(operation, "count") ? "GET"
                         : !strcmp(operation, "batch") ? "POST" : !strcmp(operation, "record") ? NULL : "";
    if (method && !*method) { status = 404; x.problem = "no such operation"; goto text; }
    if (method ? strcmp(r->method, method) : strcmp(r->method, "GET") && strcmp(r->method, "PUT") && strcmp(r->method, "DELETE")) {
        status = 405; x.problem = "method not allowed for this operation"; goto text;
    }
    if (parse_params(r->query, &p)) { free_params(&p); status = 400; x.problem = "invalid query string"; goto text; }
    if ((!operation || !strcmp(operation, "batch") || !strcmp(r->method, "PUT")) && p.count) {
        free_params(&p); status = 400; x.problem = "this operation takes no query"; goto text;
    }
    sqlite3 *db = NULL;
    int opened = disc_database_open(database, writes, &db);
    if (opened != DISC_DATABASE_OK && opened != DISC_DATABASE_ABSENT) { free_params(&p); status = 503; goto text; }
    if (!operation) status = summary(store, db, &b);
    else if (!strcmp(operation, "records")) status = op_list(&x, db, &p);
    else if (!strcmp(operation, "count")) status = op_count(&x, db, &p);
    else if (!strcmp(r->method, "GET")) status = op_get(&x, db, &p);
    else if (!strcmp(r->method, "PUT")) status = op_put(&x, db);
    else if (!strcmp(r->method, "DELETE")) status = op_delete(&x, db, &p);
    else status = op_batch(&x, db);
    if (db) disc_database_close(database, db);
    free_params(&p);
    if (status == 200 && !b.overflow) {
        *out = b.data;
        *length = b.used;
        *json = 1;
        return 200;
    }
    if (status == 200) status = 500;
text:
    free(b.data);
    b = (disc_buffer){0};
    disc_buffer_text(&b, status == 503 ? "Store unavailable" : status == 500 ? "Store failed" : x.problem ? x.problem : "Store request refused");
    disc_buffer_text(&b, "\n");
    if (b.data) b.data[0] = (char)toupper((unsigned char)b.data[0]);
    *out = b.data;
    *length = b.used;
    return status;
}

int disc_store_skips(const disc_store *store, disc_database *database, const char *path, const char *title) {
    sqlite3 *db = NULL;
    int skip = 0;
    for (size_t i = 0; i < store->count && !skip; i++) {
        const disc_collection *c = &store->collections[i];
        if (!c->skip || (title && c->key_count != 2)) continue;
        if (!db && disc_database_open(database, 0, &db) != DISC_DATABASE_OK) return 0;
        disc_buffer key = {0};
        disc_buffer_text(&key, "[");
        disc_buffer_string(&key, path, strlen(path));
        if (c->key_count == 2 && title) {
            disc_buffer_text(&key, ",");
            disc_buffer_string(&key, title, strlen(title));
            disc_buffer_text(&key, "]");
        } else disc_buffer_text(&key, c->key_count == 2 ? ",null]" : "]");
        skip = !key.overflow && exec_bound(db, EXISTS, c->name, &key, NULL, 0) == 1;
        free(key.data);
    }
    if (db) disc_database_close(database, db);
    return skip;
}
