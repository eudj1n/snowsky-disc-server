#ifndef DISC_ORIGINS_H
#define DISC_ORIGINS_H
/* External origins of the page (combined-008): the card's reviewed
 * origins.json names https origins the page may connect to or load images
 * from; the service adds them to the Content-Security-Policy of the page it
 * serves. The header is built from card data, so every origin is checked
 * character by character (https, lower-case host labels, a wildcard only as
 * the first label, an optional port, nothing else). */
#include <stddef.h>

#define DISC_ORIGINS_MAX 16
#define DISC_ORIGIN_MAX 100
/* The policy without a catalog: the page stays same-origin. */
#define DISC_PAGE_POLICY "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'"
#define DISC_POLICY_MAX 2048

typedef struct {
    char origin[DISC_ORIGIN_MAX + 1];
    int connect, image;
} disc_origin;

typedef struct {
    disc_origin origins[DISC_ORIGINS_MAX];
    size_t count;
} disc_origins;

/* Hosted pages (2026-09-30): the reviewed hosted.json of the image, or the card's override, names
 * the exact https origins of pages elsewhere (a public site) that may use the API from the
 * browser; the service answers them with CORS. No wildcard: each origin is one site. */
#define DISC_HOSTED_MAX 4

typedef struct {
    char origins[DISC_HOSTED_MAX][DISC_ORIGIN_MAX + 1];
    size_t count;
} disc_hosted;

int disc_hosted_parse(disc_hosted *hosted, const char *json, size_t length, const char *expected_profile_sha256);

/* expected_profile_sha256 NULL: an app's own file, whose profile is not checked (combined-009). */
int disc_origins_parse(disc_origins *origins, const char *json, size_t length, const char *expected_profile_sha256);
/* 1 when the text is an origin the policy may name. */
int disc_origin_valid(const char *origin);
/* The page's policy with the origins (DISC_PAGE_POLICY when there are none). */
void disc_origins_policy(const disc_origins *origins, char *out, size_t capacity);

#endif
