#ifndef DISC_TREE_H
#define DISC_TREE_H
/* The card's folders and files for the page's Card section (combined-009,
 * the owner's proposal): one folder in one request, or a whole tree with the
 * facts of every audio file read from its headers, instead of stock's
 * transfer browser page by page and one metadata read per file. Hidden names
 * (the service's .disc, macOS leftovers) are never listed and links never
 * followed, as in stock's own browsers. */
#include <stddef.h>

#define DISC_TREE_MAX_ENTRIES 50000  /* folders and files of one tree */
#define DISC_TREE_MAX_DEPTH 16
#define DISC_TREE_FOLDER_MAX 5000    /* entries of one folder */
#define DISC_TREE_BUDGET_MS 120000LL /* a walk stops (truncated) after this */

/* Opens a folder below the card root, one component at a time without links,
 * dot or hidden parts ("" is the root). Returns the folder's fd, or -1 with
 * *status 404 (missing), 400 (not admitted) or 503 (the card). */
int disc_tree_open(const char *card, const char *relative, int *status);
/* One folder, sorted by name: {"path","entries":[{"name","dir","kind","bytes",
 * "modified"}],"count","truncated"}. Returns an HTTP status; on 200 *json is
 * malloc'd. The folder fd is closed. */
int disc_tree_folder(int folder, const char *relative, char **json, size_t *length);
/* The folder and everything below it, depth first and sorted by name, as a
 * JSON document written in pieces through emit (a piece at a time of about
 * 16 KiB; a nonzero result stops the walk): {"root","path","entries":[{"path",
 * "dir":true,"modified"} | {"path","bytes","modified","kind", and for audio
 * "format","sampleRate","bitDepth","channels","bitRate","durationMs","year"}],
 * "folders","files","bytes","truncated","elapsedMs"}. The folder fd is closed.
 * Returns 0, or -1 when emit stopped it. */
int disc_tree_walk(int folder, const char *card, const char *relative, long long now_ms,
                   int (*emit)(void *arg, const char *data, size_t length), void *arg);

#endif
