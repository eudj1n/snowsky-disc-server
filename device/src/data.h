#ifndef DISC_DATA_H
#define DISC_DATA_H
/* Read-only data level: reviewed named queries over the stock SQLite files
 * and, since combined-008, the service's own database (the database file
 * "@disc" in queries.json). The query catalog is data published with an SD
 * release (queries.json); the gateway executes only those statements,
 * read-only, with bounded parameters and output. Stock owns its databases;
 * nothing here writes them except the favorite below. */
#include "database.h"
#include <regex.h>
#include <stddef.h>

#define DISC_QUERIES_MAX 32
#define DISC_QUERY_PARAMS 8
#define DISC_DATA_MAX_JSON (256 * 1024)
#define DISC_DATA_MAX_OUTPUT (1024 * 1024)
/* The queries.json file name that stands for the service's database. */
#define DISC_DATA_SERVICE_DB "@disc"

typedef struct {
    char name[41];
    int is_int;
    long long min, max;
    size_t max_length;
    regex_t pattern;
} disc_query_param;
typedef struct {
    char name[41], db[41], sql[2001];
    disc_query_param params[DISC_QUERY_PARAMS];
    size_t param_count;
    unsigned max_rows;
} disc_query;
typedef struct {
    disc_query queries[DISC_QUERIES_MAX];
    size_t count;
    struct { char key[41], file[41]; } databases[8];
    size_t db_count;
    char profile_sha256[65];
} disc_queries;

int disc_queries_parse(disc_queries *queries, const char *json, size_t length, const char *expected_profile_sha256);
void disc_queries_free(disc_queries *queries);
const disc_query *disc_queries_find(const disc_queries *queries, const char *name);
/* values[i] is the raw text of parameter i (NULL when absent). On success the
 * result is a JSON document in *out (caller frees). Returns an HTTP status:
 * 200 ok, 400 invalid parameters, 503 database busy or unavailable, 500 error. */
int disc_data_execute(const disc_queries *queries, const disc_query *query, const char *data_root, disc_database *service,
                      const char *const *values, char **out, size_t *out_length);
/* Favorites for any track (next image): adds SONG.ID to MY_LOVE with the player
 * screen's own statement inside one immediate transaction and reads the row
 * back. Returns 200 (added, or *already set), 404 (no such song), 503 (busy or
 * unavailable) or 500; *love_id is the favorite's MY_LOVE.ID. */
int disc_data_favorite_add(const char *data_root, long long song_id, long long *love_id, int *already);
#endif
