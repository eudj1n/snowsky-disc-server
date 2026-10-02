#include "catalog.h"
#include <ctype.h>
#include <stdio.h>
#include <strings.h>
#include <stdlib.h>
#include <string.h>
#include "jsonutil.h"

#define MAX_TOKENS 8192
static const char *const BUILTIN_DENIED[] = {"0621", "0800"};

int disc_catalog_builtin_denied(const char *tag) {
    for (size_t i = 0; i < sizeof(BUILTIN_DENIED) / sizeof(BUILTIN_DENIED[0]); i++)
        if (!strncmp(tag, BUILTIN_DENIED[i], 4)) return 1;
    return 0;
}
#define skip disc_json_skip
#define eq disc_json_eq
#define find disc_json_find
#define copy_string disc_json_string
#define boolean disc_json_boolean
#define sha_string disc_json_sha256
static int number(const char *json, const jsmntok_t *tok, long min, long max, long *out) {
    long long v; if (disc_json_number(json, tok, min, max, &v)) return -1; *out = (long)v; return 0;
}
static int hex_tag(const char *s, char first) {
    if (strlen(s) != 4 || s[0] != first) return 0;
    for (int i = 1; i < 4; i++) if (!isxdigit((unsigned char)s[i]) || isupper((unsigned char)s[i])) return 0;
    return 1;
}

static int parse_record(const char *json, const jsmntok_t *t, int key, int value, disc_command *cmd) {
    char kind[16], pattern[520], anchored[528];
    long v;
    if (copy_string(json, &t[key], cmd->tag, sizeof(cmd->tag)) != 4 || !hex_tag(cmd->tag, '0')) return -1;
    if (t[value].type != JSMN_OBJECT) return -1;
    int i;
    if ((i = find(json, t, value, "kind")) < 0 || copy_string(json, &t[i], kind, sizeof(kind)) < 0) return -1;
    if (!strcmp(kind, "read")) cmd->mutation = 0; else if (!strcmp(kind, "mutation")) cmd->mutation = 1; else return -1;
    if ((i = find(json, t, value, "class")) < 0 || copy_string(json, &t[i], cmd->class, sizeof(cmd->class)) <= 0) return -1;
    if ((i = find(json, t, value, "reply")) < 0) return -1;
    if (t[i].type == JSMN_PRIMITIVE && json[t[i].start] == 'n') cmd->reply[0] = 0;
    else if (copy_string(json, &t[i], cmd->reply, sizeof(cmd->reply)) != 4 || !hex_tag(cmd->reply, 'a')) return -1;
    if (!cmd->mutation && !cmd->reply[0]) return -1;
    if ((i = find(json, t, value, "silent_ok")) < 0 || boolean(json, &t[i], &cmd->silent_ok)) return -1;
    if ((i = find(json, t, value, "timeout_ms")) < 0 || number(json, &t[i], 100, 60000, &v)) return -1;
    cmd->timeout_ms = (unsigned)v;
    if ((i = find(json, t, value, "pacing_ms")) < 0 || number(json, &t[i], 0, 10000, &v)) return -1;
    cmd->pacing_ms = (unsigned)v;
    if (cmd->mutation && !cmd->pacing_ms) return -1;
    if ((i = find(json, t, value, "max_bytes")) < 0 || number(json, &t[i], 8, 65535, &v)) return -1;
    cmd->max_bytes = (size_t)v;
    if ((i = find(json, t, value, "payload")) < 0 || copy_string(json, &t[i], pattern, sizeof(pattern)) < 0) return -1;
    if (strstr(pattern, "(?") || strchr(pattern, '\n')) return -1;
    snprintf(anchored, sizeof(anchored), "^(%s)$", pattern);
    if (regcomp(&cmd->payload, anchored, REG_EXTENDED | REG_NOSUB)) return -1;
    return 0;
}

static int compile_anchored(regex_t *re, const char *pattern) {
    char anchored[528];
    if (strstr(pattern, "(?") || strchr(pattern, '\n') || strlen(pattern) > 512) return -1;
    snprintf(anchored, sizeof(anchored), "^(%s)$", pattern);
    return regcomp(re, anchored, REG_EXTENDED | REG_NOSUB) ? -1 : 0;
}
static int parse_headers(const char *json, const jsmntok_t *t, int object, disc_route_header *out, size_t *count) {
    if (object < 0) return 0;
    if (t[object].type != JSMN_OBJECT || t[object].size > DISC_ROUTE_MAX_HEADERS) return -1;
    int k = object + 1;
    for (int n = 0; n < t[object].size; n++) {
        disc_route_header *h = &out[*count];
        char pattern[520];
        if (copy_string(json, &t[k], h->name, sizeof(h->name)) <= 0 || copy_string(json, &t[k + 1], pattern, sizeof(pattern)) < 0) return -1;
        for (char *c = h->name; *c; c++) *c = (char)tolower((unsigned char)*c);
        if (compile_anchored(&h->pattern, pattern)) return -1;
        h->required = 0; (*count)++;
        k = skip(t, k + 1);
    }
    return 0;
}
static int method_string(const char *json, const jsmntok_t *tok, char out[8]) {
    if (copy_string(json, tok, out, 8) <= 0) return -1;
    return (!strcmp(out, "GET") || !strcmp(out, "POST") || !strcmp(out, "DELETE")) ? 0 : -1;
}
static int parse_route(const char *json, const jsmntok_t *t, int value, disc_route *r) {
    char kind[16], body[8], pattern[520];
    long v; int i;
    if (t[value].type != JSMN_OBJECT) return -1;
    if ((i = find(json, t, value, "name")) < 0 || copy_string(json, &t[i], r->name, sizeof(r->name)) <= 0) return -1;
    if ((i = find(json, t, value, "method")) < 0 || method_string(json, &t[i], r->method)) return -1;
    if ((i = find(json, t, value, "kind")) < 0 || copy_string(json, &t[i], kind, sizeof(kind)) < 0) return -1;
    if (!strcmp(kind, "read")) r->mutation = 0; else if (!strcmp(kind, "mutation")) r->mutation = 1; else return -1;
    if (r->mutation == !strcmp(r->method, "GET")) return -1;
    if ((i = find(json, t, value, "class")) < 0 || copy_string(json, &t[i], r->class, sizeof(r->class)) <= 0) return -1;
    if ((i = find(json, t, value, "path")) < 0 || copy_string(json, &t[i], pattern, sizeof(pattern)) <= 0 || pattern[0] != '/' ||
        strstr(pattern, "\\?") || compile_anchored(&r->path, pattern)) return -1;
    if ((i = find(json, t, value, "body")) < 0 || copy_string(json, &t[i], body, sizeof(body)) <= 0) return -1;
    if (!strcmp(body, "none")) r->body = 0; else if (!strcmp(body, "json")) r->body = 1; else if (!strcmp(body, "raw")) r->body = 2; else return -1;
    if ((i = find(json, t, value, "max_body_bytes")) < 0 || number(json, &t[i], 0, 1024L * 1024 * 1024, &v)) return -1;
    r->max_body_bytes = (size_t)v;
    if ((r->body == 0) != (r->max_body_bytes == 0)) return -1;
    r->header_count = 0;
    if (parse_headers(json, t, find(json, t, value, "headers"), r->headers, &r->header_count)) return -1;
    int req = find(json, t, value, "required_headers");
    if (req < 0 || t[req].type != JSMN_ARRAY) return -1;
    for (int n = 0, k = req + 1; n < t[req].size; n++, k++) {
        char name[41]; int found = 0;
        if (copy_string(json, &t[k], name, sizeof(name)) <= 0) return -1;
        for (char *c = name; *c; c++) *c = (char)tolower((unsigned char)*c);
        for (size_t h = 0; h < r->header_count; h++) if (!strcmp(r->headers[h].name, name)) { r->headers[h].required = 1; found = 1; }
        if (!found) return -1;
    }
    return 0;
}
static int parse_denial(const char *json, const jsmntok_t *t, int value, disc_denial *d) {
    char pattern[520]; int i;
    if (t[value].type != JSMN_OBJECT) return -1;
    if ((i = find(json, t, value, "method")) < 0 || method_string(json, &t[i], d->method)) return -1;
    if ((i = find(json, t, value, "path")) < 0 || copy_string(json, &t[i], pattern, sizeof(pattern)) <= 0 || compile_anchored(&d->path, pattern)) return -1;
    d->header_count = 0;
    return parse_headers(json, t, find(json, t, value, "headers"), d->headers, &d->header_count);
}
static void free_route_headers(disc_route_header *h, size_t n) { for (size_t i = 0; i < n; i++) regfree(&h[i].pattern); }

int disc_catalog_parse(disc_catalog *cat, const char *json, size_t length, const char *expected) {
    memset(cat, 0, sizeof(*cat));
    if (!json || length == 0 || length > DISC_CATALOG_MAX_JSON || !expected || strlen(expected) != 64) return -1;
    if (memchr(json, 0, length)) return -1;
    jsmntok_t *t = NULL;
    if (disc_json_parse(json, length, &t, MAX_TOKENS) < 0) return -1;
    int ok = 0, i; long v;
    if ((i = find(json, t, 0, "schema_version")) < 0 || number(json, &t[i], 1, 1, &v)) goto done;
    if ((i = find(json, t, 0, "api")) < 0 || number(json, &t[i], 1, 1, &v)) goto done;
    if ((i = find(json, t, 0, "profile_sha256")) < 0 || sha_string(json, &t[i], cat->profile_sha256)) goto done;
    if (strcmp(cat->profile_sha256, expected)) goto done;
    if ((i = find(json, t, 0, "catalog_sha256")) < 0 || sha_string(json, &t[i], cat->catalog_sha256)) goto done;
    int denied = find(json, t, 0, "denied");
    if (denied < 0) goto done;
    int denied_records = find(json, t, denied, "records");
    if (denied_records < 0 || t[denied_records].type != JSMN_OBJECT) goto done;
    int k = denied_records + 1;
    for (int n = 0; n < t[denied_records].size; n++) {
        char tag[5];
        if (cat->denied_count >= 32 || copy_string(json, &t[k], tag, sizeof(tag)) != 4 || !hex_tag(tag, '0')) goto done;
        memcpy(cat->denied[cat->denied_count++], tag, 5);
        k = skip(t, k + 1);
    }
    for (size_t d = 0; d < sizeof(BUILTIN_DENIED) / sizeof(BUILTIN_DENIED[0]); d++) {
        int listed = 0;
        for (size_t n = 0; n < cat->denied_count; n++) if (!strcmp(cat->denied[n], BUILTIN_DENIED[d])) listed = 1;
        if (!listed) goto done;
    }
    int denied_http = find(json, t, denied, "http");
    if (denied_http < 0 || t[denied_http].type != JSMN_ARRAY || t[denied_http].size > DISC_CATALOG_MAX_ROUTES) goto done;
    k = denied_http + 1;
    for (int n = 0; n < t[denied_http].size; n++) {
        if (parse_denial(json, t, k, &cat->denials[cat->denial_count])) goto done;
        cat->denial_count++;
        k = skip(t, k);
    }
    int routes = find(json, t, 0, "http");
    if (routes < 0 || t[routes].type != JSMN_ARRAY || t[routes].size > DISC_CATALOG_MAX_ROUTES) goto done;
    k = routes + 1;
    for (int n = 0; n < t[routes].size; n++) {
        disc_route *r = &cat->routes[cat->route_count];
        if (parse_route(json, t, k, r)) goto done;
        cat->route_count++;
        if (!strcmp(r->method, "DELETE") && !strncmp(r->name, "file", 4)) goto done;
        k = skip(t, k);
    }
    /* Optional: names of built-in data mutations the card admits (older images ignore the key). */
    int data = find(json, t, 0, "data");
    if (data >= 0) {
        if (t[data].type != JSMN_ARRAY || t[data].size > DISC_CATALOG_MAX_DATA) goto done;
        k = data + 1;
        for (int n = 0; n < t[data].size; n++) {
            disc_data_mutation *m = &cat->data[cat->data_count];
            int name = find(json, t, k, "name"), cls = find(json, t, k, "class"), pacing = find(json, t, k, "pacing_ms");
            long ms;
            if (t[k].type != JSMN_OBJECT || name < 0 || cls < 0 || pacing < 0 ||
                copy_string(json, &t[name], m->name, sizeof(m->name)) < 1 ||
                copy_string(json, &t[cls], m->class, sizeof(m->class)) < 1 || number(json, &t[pacing], 250, 10000, &ms)) goto done;
            m->pacing_ms = (unsigned)ms;
            cat->data_count++;
            k = skip(t, k);
        }
    }
    int records = find(json, t, 0, "records");
    if (records < 0 || t[records].type != JSMN_OBJECT || t[records].size == 0) goto done;
    k = records + 1;
    for (int n = 0; n < t[records].size; n++) {
        if (cat->count >= DISC_CATALOG_MAX_RECORDS) goto done;
        disc_command *cmd = &cat->records[cat->count];
        if (parse_record(json, t, k, k + 1, cmd)) goto done;
        cat->count++;
        if (disc_catalog_builtin_denied(cmd->tag)) goto done;
        for (size_t m = 0; m + 1 < cat->count; m++) if (!strcmp(cat->records[m].tag, cmd->tag)) goto done;
        k = skip(t, k + 1);
    }
    ok = 1;
done:
    free(t);
    if (!ok) { disc_catalog_free(cat); return -1; }
    return 0;
}

void disc_catalog_free(disc_catalog *cat) {
    for (size_t i = 0; i < cat->count; i++) regfree(&cat->records[i].payload);
    for (size_t i = 0; i < cat->route_count; i++) { regfree(&cat->routes[i].path); free_route_headers(cat->routes[i].headers, cat->routes[i].header_count); }
    for (size_t i = 0; i < cat->denial_count; i++) { regfree(&cat->denials[i].path); free_route_headers(cat->denials[i].headers, cat->denials[i].header_count); }
    memset(cat, 0, sizeof(*cat));
}

static const char *header_value(const disc_header *headers, size_t n, const char *name) {
    for (size_t i = 0; i < n; i++) if (!strcasecmp(headers[i].name, name)) return headers[i].value;
    return NULL;
}
static int headers_match(const disc_route_header *rules, size_t n, const disc_header *headers, size_t count, int require_all) {
    for (size_t i = 0; i < n; i++) {
        const char *value = header_value(headers, count, rules[i].name);
        if (!value) { if (require_all || rules[i].required) return 0; continue; }
        if (regexec(&rules[i].pattern, value, 0, NULL, 0)) return 0;
    }
    return 1;
}
const disc_route *disc_catalog_admit_route(const disc_catalog *cat, const char *method, const char *path,
                                           const disc_header *headers, size_t count) {
    if (!path || path[0] != '/' || strstr(path, "//") || strchr(path, '\\') || strchr(path, '?')) return NULL;
    for (const char *p = path; *p; ) {
        const char *end = strchr(p + 1, '/'); size_t n = end ? (size_t)(end - p - 1) : strlen(p + 1);
        if ((n == 1 && p[1] == '.') || (n == 2 && p[1] == '.' && p[2] == '.')) return NULL;
        if (!end) break;
        p = end;
    }
    if (!strcmp(method, "DELETE") && !strncmp(path, "/file/", 6)) return NULL;
    for (size_t i = 0; i < cat->denial_count; i++) {
        const disc_denial *d = &cat->denials[i];
        if (!strcmp(d->method, method) && !regexec(&d->path, path, 0, NULL, 0) &&
            headers_match(d->headers, d->header_count, headers, count, 1)) return NULL;
    }
    for (size_t i = 0; i < cat->route_count; i++) {
        const disc_route *r = &cat->routes[i];
        if (strcmp(r->method, method) || regexec(&r->path, path, 0, NULL, 0)) continue;
        if (headers_match(r->headers, r->header_count, headers, count, 0)) return r;
    }
    return NULL;
}

const disc_command *disc_catalog_admit(const disc_catalog *cat, const char *tag, const char *payload, size_t n) {
    char lower[5];
    for (int i = 0; i < 4; i++) lower[i] = (char)tolower((unsigned char)tag[i]);
    lower[4] = 0;
    if (disc_catalog_builtin_denied(lower)) return NULL;
    for (size_t i = 0; i < cat->denied_count; i++) if (!strcmp(cat->denied[i], lower)) return NULL;
    for (size_t i = 0; i < cat->count; i++) {
        const disc_command *cmd = &cat->records[i];
        if (strcmp(cmd->tag, lower)) continue;
        if (8 + n > cmd->max_bytes || memchr(payload, 0, n)) return NULL;
        char *text = malloc(n + 1);
        if (!text) return NULL;
        memcpy(text, payload, n); text[n] = 0;
        int match = regexec(&cmd->payload, text, 0, NULL, 0) == 0;
        free(text);
        return match ? cmd : NULL;
    }
    return NULL;
}

const disc_data_mutation *disc_catalog_data(const disc_catalog *catalog, const char *name) {
    for (size_t i = 0; catalog && i < catalog->data_count; i++)
        if (!strcmp(catalog->data[i].name, name)) return &catalog->data[i];
    return NULL;
}
