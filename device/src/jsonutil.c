#define JSMN_STATIC
#define JSMN_STRICT
#include "jsmn.h" /* implementation first; the header's guarded re-include is a no-op */
#include "jsonutil.h"
#include "framing.h"
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int disc_json_skip(const jsmntok_t *t, int i) {
    int end = i + 1;
    if (t[i].type == JSMN_OBJECT) { for (int k = 0; k < t[i].size; k++) { end = disc_json_skip(t, end); end = disc_json_skip(t, end); } }
    else if (t[i].type == JSMN_ARRAY) { for (int k = 0; k < t[i].size; k++) end = disc_json_skip(t, end); }
    return end;
}
int disc_json_eq(const char *json, const jsmntok_t *tok, const char *s) {
    size_t n = (size_t)(tok->end - tok->start);
    return tok->type == JSMN_STRING && strlen(s) == n && !memcmp(json + tok->start, s, n);
}
int disc_json_find(const char *json, const jsmntok_t *t, int object, const char *key) {
    if (object < 0 || t[object].type != JSMN_OBJECT) return -1;
    int i = object + 1;
    for (int k = 0; k < t[object].size; k++) {
        if (disc_json_eq(json, &t[i], key)) return i + 1;
        i = disc_json_skip(t, i + 1);
    }
    return -1;
}
int disc_json_string(const char *json, const jsmntok_t *tok, char *out, size_t capacity) {
    if (tok->type != JSMN_STRING) return -1;
    size_t n = (size_t)(tok->end - tok->start), used = 0;
    for (size_t i = 0; i < n; i++) {
        char c = json[tok->start + i];
        if (c == '\\') {
            if (++i >= n) return -1;
            c = json[tok->start + i];
            if (c == 'n') c = '\n'; else if (c == 't') c = '\t';
            else if (c != '\\' && c != '"' && c != '/') return -1;
        }
        if ((unsigned char)c < 0x20 || used + 1 >= capacity) return -1;
        out[used++] = c;
    }
    out[used] = 0;
    return (int)used;
}
int disc_json_number(const char *json, const jsmntok_t *tok, long long min, long long max, long long *out) {
    if (tok->type != JSMN_PRIMITIVE) return -1;
    char buf[24]; size_t n = (size_t)(tok->end - tok->start);
    if (n == 0 || n >= sizeof(buf)) return -1;
    if (!isdigit((unsigned char)json[tok->start]) && json[tok->start] != '-') return -1;
    memcpy(buf, json + tok->start, n); buf[n] = 0;
    char *end; long long v = strtoll(buf, &end, 10);
    if (*end || v < min || v > max) return -1;
    *out = v; return 0;
}
int disc_json_boolean(const char *json, const jsmntok_t *tok, int *out) {
    if (tok->type != JSMN_PRIMITIVE) return -1;
    size_t n = (size_t)(tok->end - tok->start);
    if (n == 4 && !memcmp(json + tok->start, "true", 4)) { *out = 1; return 0; }
    if (n == 5 && !memcmp(json + tok->start, "false", 5)) { *out = 0; return 0; }
    return -1;
}
int disc_json_sha256(const char *json, const jsmntok_t *tok, char out[65]) {
    if (disc_json_string(json, tok, out, 65) != 64) return -1;
    for (int i = 0; i < 64; i++) if (!isxdigit((unsigned char)out[i]) || isupper((unsigned char)out[i])) return -1;
    return 0;
}
int disc_json_parse(const char *json, size_t length, jsmntok_t **tokens, size_t max_tokens) {
    size_t count = length / 4 + 64;
    if (count > max_tokens) count = max_tokens;
    jsmntok_t *t = calloc(count, sizeof(*t));
    if (!t) return -1;
    jsmn_parser parser; jsmn_init(&parser);
    int n = jsmn_parse(&parser, json, length, t, (unsigned)count);
    if (n <= 0 || t[0].type != JSMN_OBJECT) { free(t); return -1; }
    *tokens = t;
    return n;
}

/* A JSON value made only of valid strings, exact literals and numbers. */
int disc_json_valid(const char *json, const jsmntok_t *t, int i, int *next) {
    const char *s = json + t[i].start;
    size_t n = (size_t)(t[i].end - t[i].start);
    if (t[i].type == JSMN_OBJECT || t[i].type == JSMN_ARRAY) {
        int k = i + 1;
        for (int m = 0; m < t[i].size * (t[i].type == JSMN_OBJECT ? 2 : 1); m++) {
            if (t[i].type == JSMN_OBJECT && m % 2 == 0 && t[k].type != JSMN_STRING) return 0;
            if (!disc_json_valid(json, t, k, &k)) return 0;
        }
        *next = k;
        return 1;
    }
    *next = i + 1;
    if (t[i].type == JSMN_STRING) {
        if (!disc_utf8((const unsigned char *)s, n)) return 0;
        for (size_t k = 0; k < n; k++) {
            if ((unsigned char)s[k] < 0x20) return 0;
            if (s[k] != '\\') continue;
            if (++k >= n) return 0;
            if (s[k] == 'u') {
                for (int h = 1; h <= 4; h++) if (k + (size_t)h >= n || !isxdigit((unsigned char)s[k + (size_t)h])) return 0;
                k += 4;
            } else if (!strchr("\"\\/bfnrt", s[k])) return 0;
        }
        return 1;
    }
    if ((n == 4 && !memcmp(s, "true", 4)) || (n == 5 && !memcmp(s, "false", 5)) || (n == 4 && !memcmp(s, "null", 4))) return 1;
    /* -?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)? */
    size_t k = 0;
    if (k < n && s[k] == '-') k++;
    if (k < n && s[k] == '0') k++;
    else if (k < n && s[k] >= '1' && s[k] <= '9') while (k < n && isdigit((unsigned char)s[k])) k++;
    else return 0;
    if (k < n && s[k] == '.') { k++; if (k >= n || !isdigit((unsigned char)s[k])) return 0; while (k < n && isdigit((unsigned char)s[k])) k++; }
    if (k < n && (s[k] == 'e' || s[k] == 'E')) {
        k++;
        if (k < n && (s[k] == '+' || s[k] == '-')) k++;
        if (k >= n || !isdigit((unsigned char)s[k])) return 0;
        while (k < n && isdigit((unsigned char)s[k])) k++;
    }
    return k == n;
}


int disc_json_object(const char *json, size_t length) {
    jsmntok_t *t = NULL;
    int count = disc_json_parse(json, length, &t, 4096), next = 0;
    int ok = count > 0 && disc_json_valid(json, t, 0, &next) && next == count;
    free(t);
    return ok;
}

void disc_buffer_put(disc_buffer *b, const char *s, size_t n) {
    if (b->overflow) return;
    size_t limit = b->limit ? b->limit : DISC_BUFFER_LIMIT;
    if (b->used + n + 1 > b->capacity) {
        size_t next = b->capacity ? b->capacity * 2 : 1024;
        while (next < b->used + n + 1) next *= 2;
        if (next > limit) next = limit;
        if (next < b->used + n + 1) { b->overflow = 1; return; }
        char *grown = realloc(b->data, next);
        if (!grown) { b->overflow = 1; return; }
        b->data = grown;
        b->capacity = next;
    }
    memcpy(b->data + b->used, s, n);
    b->used += n;
    b->data[b->used] = 0;
}
void disc_buffer_text(disc_buffer *b, const char *s) { disc_buffer_put(b, s, strlen(s)); }
void disc_buffer_int(disc_buffer *b, long long v) {
    char number[24];
    snprintf(number, sizeof(number), "%lld", v);
    disc_buffer_text(b, number);
}
void disc_buffer_string(disc_buffer *b, const char *s, size_t n) {
    disc_buffer_text(b, "\"");
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"' || c == '\\') { char escaped[2] = {'\\', (char)c}; disc_buffer_put(b, escaped, 2); }
        else if (c < 0x20) { char escaped[8]; snprintf(escaped, sizeof(escaped), "\\u%04x", c); disc_buffer_text(b, escaped); }
        else disc_buffer_put(b, s + i, 1);
    }
    disc_buffer_text(b, "\"");
}
