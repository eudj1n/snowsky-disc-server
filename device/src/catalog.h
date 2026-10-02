#ifndef DISC_CATALOG_H
#define DISC_CATALOG_H
/* Reviewed command catalog published with an SD release as commands.json.
 * The catalog is policy as data: which stock records a client may ask the
 * gateway to forward, with reply tags, mutation flags and payload bounds. The
 * guards (owner, token, request IDs, pacing, denylist) live in the service. */
#include <regex.h>
#include <stddef.h>

#define DISC_CATALOG_MAX_JSON (256 * 1024)
#define DISC_CATALOG_MAX_RECORDS 128

typedef struct {
    char tag[5], reply[5], class[17];
    int mutation, silent_ok;
    unsigned timeout_ms, pacing_ms;
    size_t max_bytes;
    regex_t payload;
} disc_command;

#define DISC_CATALOG_MAX_ROUTES 32
#define DISC_ROUTE_MAX_HEADERS 8

typedef struct { char name[41]; regex_t pattern; int required; } disc_route_header;
typedef struct {
    char name[41], method[8], class[17];
    int mutation, body; /* body: 0 none, 1 json, 2 raw */
    size_t max_body_bytes;
    regex_t path;
    disc_route_header headers[DISC_ROUTE_MAX_HEADERS];
    size_t header_count;
} disc_route;
typedef struct {
    char method[8];
    regex_t path;
    disc_route_header headers[DISC_ROUTE_MAX_HEADERS];
    size_t header_count;
} disc_denial;

/* A data-level mutation the service implements itself, admitted by name. */
typedef struct { char name[41], class[17]; unsigned pacing_ms; } disc_data_mutation;
#define DISC_CATALOG_MAX_DATA 8

typedef struct {
    disc_command records[DISC_CATALOG_MAX_RECORDS];
    size_t count;
    char denied[32][5];
    size_t denied_count;
    disc_route routes[DISC_CATALOG_MAX_ROUTES];
    size_t route_count;
    disc_denial denials[DISC_CATALOG_MAX_ROUTES];
    size_t denial_count;
    disc_data_mutation data[DISC_CATALOG_MAX_DATA];
    size_t data_count;
    char profile_sha256[65], catalog_sha256[65];
} disc_catalog;

typedef struct { const char *name, *value; } disc_header;
/* Admits one stock HTTP request: path hygiene, built-in denials, catalog
 * denials, then the first route whose method, path and header patterns match
 * and whose required headers are present. Only described headers are forwarded. */
const disc_route *disc_catalog_admit_route(const disc_catalog *catalog, const char *method, const char *path,
                                           const disc_header *headers, size_t header_count);

/* Parses and validates commands.json. The catalog must carry the profile
 * fingerprint the image was built for. Returns 0 and a usable catalog, or -1. */
int disc_catalog_parse(disc_catalog *catalog, const char *json, size_t length, const char *expected_profile_sha256);
void disc_catalog_free(disc_catalog *catalog);
/* Admits one normalized record (lowercase tag, payload after the 8-byte header).
 * Built-in denylist first, then catalog denials, then pattern and size. */
const disc_command *disc_catalog_admit(const disc_catalog *catalog, const char *tag, const char *payload, size_t payload_length);
int disc_catalog_builtin_denied(const char *tag);
/* The catalog's admission of one built-in data mutation, or NULL. */
const disc_data_mutation *disc_catalog_data(const disc_catalog *catalog, const char *name);

#endif
