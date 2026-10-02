#include "origins.h"
#include "jsonutil.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_TOKENS 1024

static int label_char(char c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-'; }

int disc_origin_valid(const char *origin) {
    size_t n = strlen(origin);
    if (n > DISC_ORIGIN_MAX || strncmp(origin, "https://", 8)) return 0;
    const char *p = origin + 8, *end = origin + n;
    int wildcard = 0, labels = 0;
    if (p[0] == '*' && p[1] == '.') { wildcard = 1; p += 2; }
    for (;;) {
        const char *start = p;
        while (p < end && label_char(*p)) p++;
        size_t length = (size_t)(p - start);
        if (!length || length > 63 || start[0] == '-' || p[-1] == '-') return 0;
        labels++;
        if (p < end && *p == '.') { p++; continue; }
        break;
    }
    if (labels < 2) return 0;
    (void)wildcard; /* "*." counts no label: at least two follow it */
    if (p < end && *p == ':') {
        const char *digits = ++p;
        long port = 0;
        while (p < end && *p >= '0' && *p <= '9' && p - digits < 5) port = port * 10 + (*p++ - '0');
        if (p == digits || port < 1 || port > 65535) return 0;
    }
    return p == end;
}

int disc_origins_parse(disc_origins *o, const char *json, size_t length, const char *expected) {
    memset(o, 0, sizeof(*o));
    /* An app's own origins.json (combined-009) names no firmware profile: expected is NULL. */
    if (!json || !length || length > 64 * 1024 || (expected && strlen(expected) != 64) || memchr(json, 0, length)) return -1;
    jsmntok_t *t = NULL;
    if (disc_json_parse(json, length, &t, MAX_TOKENS) < 0) return -1;
    int ok = 0, i;
    long long v;
    char profile[65];
    if ((i = disc_json_find(json, t, 0, "schema_version")) < 0 || disc_json_number(json, &t[i], 1, 1, &v)) goto done;
    if ((i = disc_json_find(json, t, 0, "api")) < 0 || disc_json_number(json, &t[i], 1, 1, &v)) goto done;
    if (expected && ((i = disc_json_find(json, t, 0, "profile_sha256")) < 0 || disc_json_sha256(json, &t[i], profile) || strcmp(profile, expected)))
        goto done;
    int origins = disc_json_find(json, t, 0, "origins");
    if (origins < 0 || t[origins].type != JSMN_OBJECT || t[origins].size > DISC_ORIGINS_MAX) goto done;
    int k = origins + 1;
    for (int n = 0; n < t[origins].size; n++) {
        disc_origin *entry = &o->origins[o->count];
        int value = k + 1, directives;
        if (t[value].type != JSMN_OBJECT || (i = disc_json_find(json, t, value, "origin")) < 0 ||
            disc_json_string(json, &t[i], entry->origin, sizeof(entry->origin)) <= 0 || !disc_origin_valid(entry->origin)) goto done;
        for (size_t m = 0; m < o->count; m++) if (!strcmp(o->origins[m].origin, entry->origin)) goto done;
        if ((directives = disc_json_find(json, t, value, "directives")) < 0 || t[directives].type != JSMN_ARRAY ||
            t[directives].size < 1 || t[directives].size > 2) goto done;
        for (int d = 0; d < t[directives].size; d++) {
            const jsmntok_t *directive = &t[directives + 1 + d];
            int *flag = disc_json_eq(json, directive, "connect-src") ? &entry->connect : disc_json_eq(json, directive, "img-src") ? &entry->image : NULL;
            if (!flag || *flag) goto done;
            *flag = 1;
        }
        o->count++;
        k = disc_json_skip(t, k + 1);
    }
    ok = 1;
done:
    free(t);
    if (!ok) { memset(o, 0, sizeof(*o)); return -1; }
    return 0;
}

int disc_hosted_parse(disc_hosted *h, const char *json, size_t length, const char *expected) {
    memset(h, 0, sizeof(*h));
    if (!json || !length || length > 64 * 1024 || !expected || strlen(expected) != 64 || memchr(json, 0, length)) return -1;
    jsmntok_t *t = NULL;
    if (disc_json_parse(json, length, &t, MAX_TOKENS) < 0) return -1;
    int ok = 0, i;
    long long v;
    char profile[65];
    if ((i = disc_json_find(json, t, 0, "schema_version")) < 0 || disc_json_number(json, &t[i], 1, 1, &v)) goto done;
    if ((i = disc_json_find(json, t, 0, "api")) < 0 || disc_json_number(json, &t[i], 1, 1, &v)) goto done;
    if ((i = disc_json_find(json, t, 0, "profile_sha256")) < 0 || disc_json_sha256(json, &t[i], profile) || strcmp(profile, expected))
        goto done;
    int pages = disc_json_find(json, t, 0, "pages");
    if (pages < 0 || t[pages].type != JSMN_OBJECT || t[pages].size > DISC_HOSTED_MAX) goto done;
    int k = pages + 1;
    for (int n = 0; n < t[pages].size; n++) {
        char *origin = h->origins[h->count];
        int value = k + 1;
        if (t[value].type != JSMN_OBJECT || (i = disc_json_find(json, t, value, "origin")) < 0 ||
            disc_json_string(json, &t[i], origin, DISC_ORIGIN_MAX + 1) <= 0 || !disc_origin_valid(origin) || strchr(origin, '*'))
            goto done;
        for (size_t m = 0; m < h->count; m++) if (!strcmp(h->origins[m], origin)) goto done;
        h->count++;
        k = disc_json_skip(t, k + 1);
    }
    ok = 1;
done:
    free(t);
    if (!ok) { memset(h, 0, sizeof(*h)); return -1; }
    return 0;
}

void disc_origins_policy(const disc_origins *o, char *out, size_t capacity) {
    size_t used = (size_t)snprintf(out, capacity, "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'");
    int images = 0;
    for (size_t i = 0; o && i < o->count; i++) {
        if (o->origins[i].connect && used < capacity) used += (size_t)snprintf(out + used, capacity - used, " %s", o->origins[i].origin);
        images |= o->origins[i].image;
    }
    if (images && used < capacity) {
        used += (size_t)snprintf(out + used, capacity - used, "; img-src 'self'");
        for (size_t i = 0; i < o->count; i++)
            if (o->origins[i].image && used < capacity) used += (size_t)snprintf(out + used, capacity - used, " %s", o->origins[i].origin);
    }
    if (used < capacity) used += (size_t)snprintf(out + used, capacity - used, "; frame-ancestors 'none'");
    if (used >= capacity) snprintf(out, capacity, "%s", DISC_PAGE_POLICY);
}
