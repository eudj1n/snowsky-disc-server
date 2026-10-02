#ifndef DISC_STORE_H
#define DISC_STORE_H
/* The store (combined-008): collections that the card's reviewed store.json
 * declares, kept in the records table of the service's database. Each record
 * is checked against its collection's declaration (key, fields and types,
 * limits) and kept as canonical JSON; the service builds the declared indexes
 * itself. Every collection has the same fixed operations and clients never
 * send SQL, so a collection added to the card catalog needs no image. */
#include "database.h"
#include <regex.h>
#include <stddef.h>

#define DISC_STORE_MAX_JSON (64 * 1024)
#define DISC_STORE_MAX_BODY (256 * 1024)
#define DISC_STORE_MAX_COLLECTIONS 16
#define DISC_STORE_MAX_FIELDS 16
#define DISC_STORE_MAX_KEY 3

enum { DISC_FIELD_TEXT, DISC_FIELD_INT, DISC_FIELD_BOOL, DISC_FIELD_PATH, DISC_FIELD_JSON };

typedef struct {
    char name[41];
    int type, required, has_pattern;
    long long min, max;
    size_t max_length;
    regex_t pattern;
} disc_store_field;

typedef struct {
    char name[41];
    disc_store_field fields[DISC_STORE_MAX_FIELDS];
    size_t field_count;
    size_t key[DISC_STORE_MAX_KEY], key_count;
    size_t index[DISC_STORE_MAX_FIELDS], index_count;
    long long max_records;
    size_t max_record_bytes;
    int skip;
} disc_collection;

typedef struct {
    disc_collection collections[DISC_STORE_MAX_COLLECTIONS];
    size_t count;
} disc_store;

int disc_store_parse(disc_store *store, const char *json, size_t length, const char *expected_profile_sha256);
void disc_store_free(disc_store *store);

typedef struct {
    const char *method;     /* GET, PUT, DELETE or POST */
    const char *path;       /* after "/api/store": "" or "/<collection>/<operation>" */
    const char *query;      /* the query string, or NULL */
    const char *body;       /* PUT and POST only */
    size_t body_length;
    const char *music_root; /* path fields lie below it */
    long long now;          /* device clock, Unix seconds */
} disc_store_request;

/* 1 when the request would change the store (the caller applies the mutation
 * guards first). */
int disc_store_writes(const char *method);
/* One store operation. Returns an HTTP status; *out is malloc'd: JSON on
 * success, one line of text otherwise (*json tells which). */
int disc_store_handle(const disc_store *store, disc_database *database, const disc_store_request *request,
                      char **out, size_t *length, int *json);
/* The skip rule: 1 when a collection marked skip holds the track: the whole
 * file at path (its key without a title), or with title the CUE track of that
 * name (collections keyed by path and title only). */
int disc_store_skips(const disc_store *store, disc_database *database, const char *path, const char *title);

#endif
