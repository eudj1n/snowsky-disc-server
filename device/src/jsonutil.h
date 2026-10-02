#ifndef DISC_JSONUTIL_H
#define DISC_JSONUTIL_H
/* Small helpers over jsmn tokens shared by the catalog parsers. */
#include <stddef.h>
#define JSMN_HEADER
#include "jsmn.h"

int disc_json_skip(const jsmntok_t *t, int i);
int disc_json_eq(const char *json, const jsmntok_t *tok, const char *s);
int disc_json_find(const char *json, const jsmntok_t *t, int object, const char *key);
/* Copies a string token with the common escapes; rejects control characters and \u. */
int disc_json_string(const char *json, const jsmntok_t *tok, char *out, size_t capacity);
int disc_json_number(const char *json, const jsmntok_t *tok, long long min, long long max, long long *out);
int disc_json_boolean(const char *json, const jsmntok_t *tok, int *out);
int disc_json_sha256(const char *json, const jsmntok_t *tok, char out[65]);
/* Parses bounded JSON text into tokens sized by the input; caller frees *tokens. */
int disc_json_parse(const char *json, size_t length, jsmntok_t **tokens, size_t max_tokens);

/* 1 when token i (and everything below it) is strict JSON: valid UTF-8
 * strings with valid escapes, exact literals, numbers; *next is the token after it. */
int disc_json_valid(const char *json, const jsmntok_t *t, int i, int *next);
/* 1 when the text is exactly one strict JSON object. */
int disc_json_object(const char *json, size_t length);

/* A growing, NUL-terminated output buffer for JSON documents. Growth stops at
 * limit bytes (DISC_BUFFER_LIMIT when 0) and sets overflow; callers free data. */
#define DISC_BUFFER_LIMIT (1024 * 1024 + 1024)
typedef struct { char *data; size_t used, capacity, limit; int overflow; } disc_buffer;
void disc_buffer_put(disc_buffer *b, const char *s, size_t n);
void disc_buffer_text(disc_buffer *b, const char *s);
void disc_buffer_int(disc_buffer *b, long long v);
/* A JSON string of UTF-8 text: quotes and backslashes escaped, control characters as \u00XX. */
void disc_buffer_string(disc_buffer *b, const char *s, size_t n);
#endif
