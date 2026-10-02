#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#include "trash.h"
#include "framing.h"
#include "history.h"
#include "jsonutil.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif
#define FOLDER_FLAGS (O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
#define MAX_DEPTH 16
#define MAX_PATH 1023
#define MAX_LISTED 1000

/* A path on the card: absolute below the mount (not the mount itself), no
 * empty, dot or hidden components (so never the service's .disc), UTF-8
 * without control characters. */
static int card_path(const char *card, const char *path) {
    size_t c = strlen(card), n = strlen(path);
    if (n <= c + 1 || n > MAX_PATH || strncmp(path, card, c) || path[c] != '/') return 0;
    if (strstr(path + c, "//") || strstr(path + c, "/.") || path[n - 1] == '/') return 0;
    if (!disc_utf8((const unsigned char *)path, n)) return 0;
    for (size_t i = 0; i < n; i++) if ((unsigned char)path[i] < 0x20) return 0;
    return 1;
}

/* The folder holding a card path, walked component by component without
 * following links (created when asked), and the path's last name. */
static int open_parent(const char *card, const char *path, int create, char *name, size_t capacity) {
    int dir = open(card, FOLDER_FLAGS & ~O_NOFOLLOW);
    const char *rel = path + strlen(card) + 1;
    while (dir >= 0) {
        const char *slash = strchr(rel, '/');
        size_t n = slash ? (size_t)(slash - rel) : strlen(rel);
        if (!n || n >= capacity) break;
        memcpy(name, rel, n);
        name[n] = 0;
        if (!slash) return dir;
        if (create && mkdirat(dir, name, 0755) && errno != EEXIST) break;
        int next = openat(dir, name, FOLDER_FLAGS);
        close(dir);
        dir = next;
        rel = slash + 1;
    }
    if (dir >= 0) close(dir);
    return -1;
}

/* <card>/.disc/trash, created when asked. */
static int open_bin(const disc_trash *t, int create) {
    if (create) {
        char parent[512];
        snprintf(parent, sizeof(parent), "%s", t->folder);
        char *slash = strrchr(parent, '/');
        if (slash && slash != parent) { *slash = 0; if (mkdir(parent, 0755) && errno != EEXIST) return -1; }
        if (mkdir(t->folder, 0755) && errno != EEXIST) return -1;
    }
    return open(t->folder, FOLDER_FLAGS);
}

/* The names in a folder, without "." and "..". */
static char **names(int dir, size_t *count) {
    int fresh = openat(dir, ".", FOLDER_FLAGS & ~O_NOFOLLOW);
    DIR *d = fresh >= 0 ? fdopendir(fresh) : NULL;
    if (!d) { if (fresh >= 0) close(fresh); return NULL; }
    char **list = NULL;
    size_t used = 0, capacity = 0;
    struct dirent *entry;
    int failed = 0;
    while (!failed && (entry = readdir(d))) {
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) continue;
        if (used == capacity) {
            capacity = capacity ? capacity * 2 : 16;
            char **grown = used < DISC_TRASH_MAX_ENTRIES + 1 ? realloc(list, capacity * sizeof(*list)) : NULL;
            if (!grown) { failed = 1; break; }
            list = grown;
        }
        if (!(list[used] = strdup(entry->d_name))) failed = 1;
        else used++;
    }
    closedir(d);
    if (failed) { for (size_t i = 0; i < used; i++) free(list[i]); free(list); return NULL; }
    *count = used;
    return list ? list : calloc(1, sizeof(*list));
}

static void free_names(char **list, size_t count) {
    for (size_t i = 0; i < count; i++) free(list[i]);
    free(list);
}

typedef struct { long long files, entries, bytes; int refused; } tally;

/* Regular files below a folder and their bytes; a link, a device or too many
 * entries refuse the whole folder. */
static void count_tree(int dir, int depth, tally *t) {
    size_t count = 0;
    char **list = depth > MAX_DEPTH ? NULL : names(dir, &count);
    if (!list) { t->refused = 1; return; }
    for (size_t i = 0; i < count && !t->refused; i++) {
        struct stat st;
        if (++t->entries > DISC_TRASH_MAX_ENTRIES || fstatat(dir, list[i], &st, AT_SYMLINK_NOFOLLOW)) t->refused = 1;
        else if (S_ISREG(st.st_mode)) { t->files++; t->bytes += st.st_size; }
        else if (S_ISDIR(st.st_mode)) {
            int sub = openat(dir, list[i], FOLDER_FLAGS);
            if (sub < 0) t->refused = 1;
            else { count_tree(sub, depth + 1, t); close(sub); }
        } else t->refused = 1;
    }
    free_names(list, count);
}

/* A name as it waits in the trash. Stock V2.57's scanner indexes any file
 * whose name merely contains an audio extension (".flac" in "x.flac.trashed",
 * guest evidence 2026-09-28), inside .disc too, so every dot is escaped ("%"
 * as %25, "." as %2E) and files get the suffix; folders are escaped the same
 * way. 0 when the result would pass 255 bytes. */
static int trash_name(const char *name, int file, char *out, size_t capacity) {
    size_t used = 0;
    for (const char *p = name; *p; p++) {
        const char *piece = *p == '%' ? "%25" : *p == '.' ? "%2E" : NULL;
        size_t n = piece ? 3 : 1;
        if (used + n >= capacity || used + n > 255) return 0;
        memcpy(out + used, piece ? piece : p, n);
        used += n;
    }
    if (file) {
        size_t n = strlen(DISC_TRASH_SUFFIX);
        if (used + n >= capacity || used + n > 255) return 0;
        memcpy(out + used, DISC_TRASH_SUFFIX, n);
        used += n;
    }
    out[used] = 0;
    return 1;
}

/* The original name back; 0 when the name is not one the trash wrote. */
static int original_name(const char *stored, int file, char *out, size_t capacity) {
    size_t n = strlen(stored), suffix = strlen(DISC_TRASH_SUFFIX), used = 0;
    if (file) {
        if (n <= suffix || strcmp(stored + n - suffix, DISC_TRASH_SUFFIX)) return 0;
        n -= suffix;
    }
    for (size_t i = 0; i < n; i++) {
        char c = stored[i];
        if (c == '.') return 0;
        if (c == '%') {
            if (i + 2 >= n) return 0;
            if (!strncmp(stored + i, "%25", 3)) c = '%';
            else if (!strncmp(stored + i, "%2E", 3)) c = '.';
            else return 0;
            i += 2;
        }
        if (used + 1 >= capacity) return 0;
        out[used++] = c;
    }
    out[used] = 0;
    return used > 0;
}

/* Renames everything below a folder into its trash name (add), or back; never replaces a name. */
static int rename_tree(int dir, int depth, int add) {
    size_t count = 0;
    char **list = depth > MAX_DEPTH ? NULL : names(dir, &count);
    if (!list) return -1;
    int failed = 0;
    for (size_t i = 0; i < count && !failed; i++) {
        struct stat st;
        if (fstatat(dir, list[i], &st, AT_SYMLINK_NOFOLLOW) || (!S_ISDIR(st.st_mode) && !S_ISREG(st.st_mode))) { failed = 1; continue; }
        int folder = S_ISDIR(st.st_mode);
        if (folder) {
            int sub = openat(dir, list[i], FOLDER_FLAGS);
            failed = sub < 0 || rename_tree(sub, depth + 1, add);
            if (sub >= 0) close(sub);
            if (failed) continue;
        }
        char renamed[300];
        if (add ? !trash_name(list[i], !folder, renamed, sizeof(renamed)) : !original_name(list[i], !folder, renamed, sizeof(renamed))) {
            if (add) failed = 1; /* back from the trash, a name it did not write stays as it is */
            continue;
        }
        if (!strcmp(renamed, list[i])) continue;
        failed = !fstatat(dir, renamed, &st, AT_SYMLINK_NOFOLLOW) || errno != ENOENT || renameat(dir, list[i], dir, renamed);
    }
    free_names(list, count);
    return failed ? -1 : 0;
}

static int remove_tree(int dir, int depth) {
    size_t count = 0;
    char **list = depth > MAX_DEPTH ? NULL : names(dir, &count);
    if (!list) return -1;
    int failed = 0;
    for (size_t i = 0; i < count; i++) {
        struct stat st;
        if (fstatat(dir, list[i], &st, AT_SYMLINK_NOFOLLOW)) { failed = 1; continue; }
        if (S_ISDIR(st.st_mode)) {
            int sub = openat(dir, list[i], FOLDER_FLAGS);
            if (sub < 0 || remove_tree(sub, depth + 1)) failed = 1;
            if (sub >= 0) close(sub);
            if (unlinkat(dir, list[i], AT_REMOVEDIR)) failed = 1;
        } else if (unlinkat(dir, list[i], 0)) failed = 1;
    }
    free_names(list, count);
    return failed ? -1 : 0;
}

/* ---- the manifest ---- */

static int manifest_exec(const disc_trash *t, const char *sql, long long id, const char *text, const char *kind,
                         const tally *tl, long long now, long long *inserted) {
    sqlite3 *db = NULL;
    if (disc_database_open(t->database, 1, &db) != DISC_DATABASE_OK) return 503;
    sqlite3_stmt *stmt = NULL;
    int status = 503;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, NULL) == SQLITE_OK) {
        if (text) {
            sqlite3_bind_text(stmt, 1, text, -1, SQLITE_STATIC);
            sqlite3_bind_text(stmt, 2, kind, -1, SQLITE_STATIC);
            sqlite3_bind_int64(stmt, 3, tl->bytes);
            sqlite3_bind_int64(stmt, 4, tl->files);
            sqlite3_bind_int64(stmt, 5, now);
        } else sqlite3_bind_int64(stmt, 1, id);
        if (sqlite3_step(stmt) == SQLITE_DONE) {
            status = 200;
            if (inserted) *inserted = sqlite3_last_insert_rowid(db);
        }
    }
    sqlite3_finalize(stmt);
    disc_database_close(t->database, db);
    return status;
}

enum { KIND_FILE, KIND_FOLDER, KIND_LEFTOVERS };
/* One entry of the manifest: its original path and kind; 404 when there is none. */
static int manifest_get(const disc_trash *t, long long id, char *original, size_t capacity, int *kind_code) {
    sqlite3 *db = NULL;
    int opened = disc_database_open(t->database, 0, &db);
    if (opened == DISC_DATABASE_ABSENT) return 404;
    if (opened != DISC_DATABASE_OK) return 503;
    sqlite3_stmt *stmt = NULL;
    int status = 503;
    if (sqlite3_prepare_v2(db, "SELECT original, kind FROM trash WHERE id = ?", -1, &stmt, NULL) == SQLITE_OK) {
        sqlite3_bind_int64(stmt, 1, id);
        int rc = sqlite3_step(stmt);
        const char *text = rc == SQLITE_ROW ? (const char *)sqlite3_column_text(stmt, 0) : NULL;
        const char *kind = rc == SQLITE_ROW ? (const char *)sqlite3_column_text(stmt, 1) : NULL;
        if (rc == SQLITE_DONE) status = 404;
        else if (text && kind && strlen(text) < capacity) {
            snprintf(original, capacity, "%s", text);
            *kind_code = !strcmp(kind, "folder") ? KIND_FOLDER : !strcmp(kind, "leftovers") ? KIND_LEFTOVERS : KIND_FILE;
            status = 200;
        }
    }
    sqlite3_finalize(stmt);
    disc_database_close(t->database, db);
    return status;
}

static void reply(disc_buffer *b, char **json, size_t *length) {
    *json = b->overflow ? NULL : b->data;
    *length = b->overflow ? 0 : b->used;
    if (b->overflow) free(b->data);
}

/* ---- operations ---- */

int disc_trash_move(const disc_trash *t, const char *path, long long now, char **json, size_t *length, const char **problem) {
    *json = NULL;
    *length = 0;
    if (!card_path(t->card, path)) { *problem = "Not a file or folder on the card"; return 400; }
    char name[256], id_name[24], stored[300];
    struct stat st;
    int parent = open_parent(t->card, path, 0, name, sizeof(name)), bin = -1, box = -1, status = 503;
    long long id = 0;
    if (parent < 0 || fstatat(parent, name, &st, AT_SYMLINK_NOFOLLOW)) { *problem = "No such file or folder"; status = 404; goto out; }
    int folder = S_ISDIR(st.st_mode);
    tally tl = {0};
    if (folder) {
        int d = openat(parent, name, FOLDER_FLAGS);
        if (d < 0) tl.refused = 1;
        else { count_tree(d, 0, &tl); close(d); }
    } else if (S_ISREG(st.st_mode)) { tl.files = 1; tl.bytes = st.st_size; }
    else tl.refused = 1;
    if (tl.refused) { *problem = "Only plain files and folders of at most 10000 entries go to the trash"; status = 400; goto out; }
    if (!trash_name(name, !folder, stored, sizeof(stored))) { *problem = "The name is too long for the trash"; status = 400; goto out; }
    if (t->process && disc_history_player_holds(t->proc_root, t->process, path)) {
        *problem = "The player has it open"; status = 409; goto out;
    }
    *problem = "Trash unavailable";
    if (manifest_exec(t, "INSERT INTO trash(original, kind, bytes, files, trashed_at, state) VALUES (?, ?, ?, ?, ?, 'moving')",
                      0, path, folder ? "folder" : "file", &tl, now, &id) != 200) goto out;
    snprintf(id_name, sizeof(id_name), "%lld", id);
    if ((bin = open_bin(t, 1)) < 0 || (mkdirat(bin, id_name, 0755) && errno != EEXIST) || (box = openat(bin, id_name, FOLDER_FLAGS)) < 0 ||
        renameat(parent, name, box, stored)) {
        if (bin >= 0) unlinkat(bin, id_name, AT_REMOVEDIR);
        manifest_exec(t, "DELETE FROM trash WHERE id = ?", id, NULL, NULL, NULL, 0, NULL);
        goto out;
    }
    if (folder) {
        int moved = openat(box, stored, FOLDER_FLAGS);
        if (moved < 0 || rename_tree(moved, 0, 1)) {
            /* Put it back as it was; the entry goes. */
            if (moved >= 0) rename_tree(moved, 0, 0);
            if (!renameat(box, stored, parent, name)) {
                unlinkat(bin, id_name, AT_REMOVEDIR);
                manifest_exec(t, "DELETE FROM trash WHERE id = ?", id, NULL, NULL, NULL, 0, NULL);
            }
            if (moved >= 0) close(moved);
            goto out;
        }
        close(moved);
    }
    fsync(box);
    manifest_exec(t, "UPDATE trash SET state = 'trashed' WHERE id = ?", id, NULL, NULL, NULL, 0, NULL);
    disc_buffer b = {0};
    disc_buffer_text(&b, "{\"id\":");
    disc_buffer_int(&b, id);
    disc_buffer_text(&b, ",\"path\":");
    disc_buffer_string(&b, path, strlen(path));
    disc_buffer_text(&b, folder ? ",\"kind\":\"folder\",\"bytes\":" : ",\"kind\":\"file\",\"bytes\":");
    disc_buffer_int(&b, tl.bytes);
    disc_buffer_text(&b, ",\"files\":");
    disc_buffer_int(&b, tl.files);
    disc_buffer_text(&b, ",\"trashed\":");
    disc_buffer_int(&b, now);
    disc_buffer_text(&b, "}");
    reply(&b, json, length);
    status = *json ? 200 : 503;
    *problem = NULL;
out:
    if (box >= 0) close(box);
    if (bin >= 0) close(bin);
    if (parent >= 0) close(parent);
    return status;
}

int disc_trash_list(const disc_trash *t, char **json, size_t *length, const char **problem) {
    *json = NULL;
    *length = 0;
    *problem = "Trash unavailable";
    sqlite3 *db = NULL;
    int opened = disc_database_open(t->database, 0, &db);
    if (opened != DISC_DATABASE_OK && opened != DISC_DATABASE_ABSENT) return 503;
    disc_buffer b = {0};
    long long count = 0, bytes = 0;
    int ok = 1;
    disc_buffer_text(&b, "{\"entries\":[");
    if (db) {
        sqlite3_stmt *stmt = NULL;
        ok = sqlite3_prepare_v2(db, "SELECT id, original, kind, bytes, files, trashed_at, state FROM trash ORDER BY id DESC", -1, &stmt, NULL) == SQLITE_OK;
        int rc;
        while (ok && (rc = sqlite3_step(stmt)) == SQLITE_ROW) {
            const char *original = (const char *)sqlite3_column_text(stmt, 1), *kind = (const char *)sqlite3_column_text(stmt, 2),
                       *state = (const char *)sqlite3_column_text(stmt, 6);
            int leftovers = kind && !strcmp(kind, "leftovers");
            if (!original || !kind || !state || (leftovers ? strcmp(original, t->card) : !card_path(t->card, original))) continue;
            count++;
            bytes += sqlite3_column_int64(stmt, 3);
            if (count > MAX_LISTED) continue;
            if (count > 1) disc_buffer_text(&b, ",");
            disc_buffer_text(&b, "{\"id\":");
            disc_buffer_int(&b, sqlite3_column_int64(stmt, 0));
            disc_buffer_text(&b, ",\"path\":");
            disc_buffer_string(&b, original, strlen(original));
            disc_buffer_text(&b, leftovers ? ",\"kind\":\"leftovers\",\"bytes\":"
                                 : !strcmp(kind, "folder") ? ",\"kind\":\"folder\",\"bytes\":" : ",\"kind\":\"file\",\"bytes\":");
            disc_buffer_int(&b, sqlite3_column_int64(stmt, 3));
            disc_buffer_text(&b, ",\"files\":");
            disc_buffer_int(&b, sqlite3_column_int64(stmt, 4));
            disc_buffer_text(&b, ",\"trashed\":");
            disc_buffer_int(&b, sqlite3_column_int64(stmt, 5));
            disc_buffer_text(&b, !strcmp(state, "trashed") ? ",\"complete\":true}" : ",\"complete\":false}");
        }
        if (ok && rc != SQLITE_DONE) ok = 0;
        sqlite3_finalize(stmt);
        disc_database_close(t->database, db);
    }
    disc_buffer_text(&b, "],\"count\":");
    disc_buffer_int(&b, count);
    disc_buffer_text(&b, ",\"bytes\":");
    disc_buffer_int(&b, bytes);
    disc_buffer_text(&b, count > MAX_LISTED ? ",\"truncated\":true}" : ",\"truncated\":false}");
    if (!ok) { free(b.data); return 503; }
    reply(&b, json, length);
    *problem = NULL;
    return *json ? 200 : 503;
}

/* ---- macOS leftovers ---- */

/* What macOS leaves on a FAT or exFAT card: AppleDouble companions ("._" plus
 * the name, extended attributes and resource forks), Finder's .DS_Store, and
 * at the root the volume's trash, Spotlight index, file-system events and
 * temporary items. Stock's scanner skips hidden files, so they only take
 * space; the Finder trash can hold whole deleted albums. */
static const char *const LEFTOVER_FOLDERS[] = {".Trashes", ".Spotlight-V100", ".fseventsd", ".TemporaryItems"};
#define LEFTOVERS_MAX 20000
#define LEFTOVERS_WALK 100000
#define LEFTOVERS_LISTED 100

typedef struct { char *path; int folder; long long files, bytes; } leftover;
typedef struct { leftover *items; size_t count; long long entries, files, bytes; int truncated; } leftovers;

static void free_leftovers(leftovers *l) {
    for (size_t i = 0; i < l->count; i++) free(l->items[i].path);
    free(l->items);
    memset(l, 0, sizeof(*l));
}

static int leftover_file(const char *name) { return !strncmp(name, "._", 2) || !strcmp(name, ".DS_Store"); }

static int add_leftover(leftovers *l, const char *rel, const char *name, int folder, long long files, long long bytes) {
    if (l->count == LEFTOVERS_MAX) { l->truncated = 1; return 0; }
    if (!(l->count & 255)) {
        leftover *grown = realloc(l->items, (l->count + 256) * sizeof(*grown));
        if (!grown) return -1;
        l->items = grown;
    }
    size_t n = strlen(rel) + strlen(name) + 2;
    char *path = malloc(n);
    if (!path) return -1;
    snprintf(path, n, "%s%s%s", rel, *rel ? "/" : "", name);
    l->items[l->count++] = (leftover){path, folder, files, bytes};
    l->files += files;
    l->bytes += bytes;
    return 0;
}

/* Finds the leftovers below a card folder (rel is its path from the card root):
 * never inside .disc or another hidden folder, except the root folders above. */
static int find_leftovers(int dir, const char *rel, int depth, leftovers *l) {
    size_t count = 0;
    char **list = depth > MAX_DEPTH ? NULL : names(dir, &count);
    if (!list) return 0;
    int failed = 0;
    for (size_t i = 0; i < count && !failed; i++) {
        struct stat st;
        if (++l->entries > LEFTOVERS_WALK) { l->truncated = 1; break; }
        if (fstatat(dir, list[i], &st, AT_SYMLINK_NOFOLLOW)) continue;
        if (S_ISREG(st.st_mode) && leftover_file(list[i])) failed = add_leftover(l, rel, list[i], 0, 1, st.st_size) < 0;
        else if (S_ISDIR(st.st_mode)) {
            int root_folder = 0;
            for (size_t f = 0; !depth && f < sizeof(LEFTOVER_FOLDERS) / sizeof(LEFTOVER_FOLDERS[0]); f++)
                if (!strcmp(list[i], LEFTOVER_FOLDERS[f])) root_folder = 1;
            if (list[i][0] == '.' && !root_folder) continue;
            int sub = openat(dir, list[i], FOLDER_FLAGS);
            if (sub < 0) continue;
            if (root_folder) {
                tally tl = {0};
                count_tree(sub, 0, &tl);
                if (!tl.refused) failed = add_leftover(l, rel, list[i], 1, tl.files, tl.bytes) < 0;
            } else {
                char child[MAX_PATH + 1];
                if ((size_t)snprintf(child, sizeof(child), "%s%s%s", rel, *rel ? "/" : "", list[i]) < sizeof(child))
                    failed = find_leftovers(sub, child, depth + 1, l) < 0;
            }
            close(sub);
        }
    }
    free_names(list, count);
    return failed ? -1 : 0;
}

static int scan_leftovers(const disc_trash *t, leftovers *l) {
    memset(l, 0, sizeof(*l));
    int root = open(t->card, FOLDER_FLAGS & ~O_NOFOLLOW);
    if (root < 0) return -1;
    int result = find_leftovers(root, "", 0, l);
    close(root);
    return result;
}

int disc_trash_leftovers(const disc_trash *t, char **json, size_t *length, const char **problem) {
    *json = NULL;
    *length = 0;
    leftovers l;
    if (scan_leftovers(t, &l)) { free_leftovers(&l); *problem = "Card unavailable"; return 503; }
    disc_buffer b = {0};
    disc_buffer_text(&b, "{\"files\":");
    disc_buffer_int(&b, l.files);
    disc_buffer_text(&b, ",\"bytes\":");
    disc_buffer_int(&b, l.bytes);
    disc_buffer_text(&b, ",\"items\":[");
    for (size_t i = 0; i < l.count && i < LEFTOVERS_LISTED; i++) {
        char path[MAX_PATH + 300];
        snprintf(path, sizeof(path), "%s/%s", t->card, l.items[i].path);
        if (i) disc_buffer_text(&b, ",");
        disc_buffer_text(&b, "{\"path\":");
        if (disc_utf8((const unsigned char *)path, strlen(path))) disc_buffer_string(&b, path, strlen(path));
        else disc_buffer_text(&b, "null");
        disc_buffer_text(&b, l.items[i].folder ? ",\"kind\":\"folder\",\"files\":" : ",\"kind\":\"file\",\"files\":");
        disc_buffer_int(&b, l.items[i].files);
        disc_buffer_text(&b, ",\"bytes\":");
        disc_buffer_int(&b, l.items[i].bytes);
        disc_buffer_text(&b, "}");
    }
    disc_buffer_text(&b, "],\"count\":");
    disc_buffer_int(&b, (long long)l.count);
    disc_buffer_text(&b, l.truncated || l.count > LEFTOVERS_LISTED ? ",\"truncated\":true}" : ",\"truncated\":false}");
    free_leftovers(&l);
    reply(&b, json, length);
    *problem = NULL;
    return *json ? 200 : 503;
}

/* The folder in the entry that mirrors a card folder (rel), created on the way. */
static int mirror_folder(int box, const char *rel) {
    int dir = openat(box, ".", FOLDER_FLAGS & ~O_NOFOLLOW);
    char part[256], stored[300];
    for (const char *p = rel; dir >= 0 && *p;) {
        const char *slash = strchr(p, '/');
        size_t n = slash ? (size_t)(slash - p) : strlen(p);
        if (!n || n >= sizeof(part)) { close(dir); return -1; }
        memcpy(part, p, n);
        part[n] = 0;
        int next = -1;
        if (trash_name(part, 0, stored, sizeof(stored)) && (!mkdirat(dir, stored, 0755) || errno == EEXIST))
            next = openat(dir, stored, FOLDER_FLAGS);
        close(dir);
        dir = next;
        p += n + (slash ? 1 : 0);
    }
    return dir;
}

int disc_trash_leftovers_move(const disc_trash *t, long long now, char **json, size_t *length, const char **problem) {
    *json = NULL;
    *length = 0;
    leftovers l;
    if (scan_leftovers(t, &l)) { free_leftovers(&l); *problem = "Card unavailable"; return 503; }
    if (!l.count) { free_leftovers(&l); *problem = "No macOS leftovers on the card"; return 404; }
    long long id = 0;
    tally total = {.files = l.files, .bytes = l.bytes};
    int bin = -1, box = -1, root = -1, status = 503;
    long long moved = 0, skipped = 0;
    *problem = "Trash unavailable";
    if (manifest_exec(t, "INSERT INTO trash(original, kind, bytes, files, trashed_at, state) VALUES (?, ?, ?, ?, ?, 'moving')",
                      0, t->card, "leftovers", &total, now, &id) != 200) goto out;
    char id_name[24];
    snprintf(id_name, sizeof(id_name), "%lld", id);
    if ((bin = open_bin(t, 1)) < 0 || (mkdirat(bin, id_name, 0755) && errno != EEXIST) || (box = openat(bin, id_name, FOLDER_FLAGS)) < 0 ||
        (root = open(t->card, FOLDER_FLAGS & ~O_NOFOLLOW)) < 0) goto out;
    for (size_t i = 0; i < l.count; i++) {
        char full[MAX_PATH + 300], rel[MAX_PATH + 1], name[256], stored[300];
        const char *slash = strrchr(l.items[i].path, '/');
        size_t parent_length = slash ? (size_t)(slash - l.items[i].path) : 0;
        snprintf(full, sizeof(full), "%s/%s", t->card, l.items[i].path);
        snprintf(name, sizeof(name), "%s", slash ? slash + 1 : l.items[i].path);
        snprintf(rel, sizeof(rel), "%.*s", (int)parent_length, l.items[i].path);
        int from = parent_length ? -1 : root, to = -1;
        if (parent_length) {
            char parent_path[MAX_PATH + 300], unused[256];
            snprintf(parent_path, sizeof(parent_path), "%s/%s/x", t->card, rel);
            from = open_parent(t->card, parent_path, 0, unused, sizeof(unused));
        }
        int ok = from >= 0 && trash_name(name, !l.items[i].folder, stored, sizeof(stored)) &&
                 !(t->process && disc_history_player_holds(t->proc_root, t->process, full)) &&
                 (to = mirror_folder(box, rel)) >= 0 && !renameat(from, name, to, stored);
        if (ok && l.items[i].folder) {
            int moved_folder = openat(to, stored, FOLDER_FLAGS);
            if (moved_folder < 0 || rename_tree(moved_folder, 0, 1)) ok = 0;
            if (moved_folder >= 0) close(moved_folder);
        }
        if (ok) moved += l.items[i].files; else skipped += l.items[i].files;
        if (to >= 0) close(to);
        if (from >= 0 && from != root) close(from);
    }
    fsync(box);
    manifest_exec(t, "UPDATE trash SET state = 'trashed' WHERE id = ?", id, NULL, NULL, NULL, 0, NULL);
    disc_buffer b = {0};
    disc_buffer_text(&b, "{\"id\":");
    disc_buffer_int(&b, id);
    disc_buffer_text(&b, ",\"kind\":\"leftovers\",\"files\":");
    disc_buffer_int(&b, moved);
    disc_buffer_text(&b, ",\"skipped\":");
    disc_buffer_int(&b, skipped);
    disc_buffer_text(&b, ",\"bytes\":");
    disc_buffer_int(&b, l.bytes);
    disc_buffer_text(&b, ",\"trashed\":");
    disc_buffer_int(&b, now);
    disc_buffer_text(&b, "}");
    reply(&b, json, length);
    status = *json ? 200 : 503;
    *problem = NULL;
out:
    if (status != 200 && id && !moved) {
        /* Nothing moved: no entry either. */
        char id_name[24];
        snprintf(id_name, sizeof(id_name), "%lld", id);
        if (bin >= 0) unlinkat(bin, id_name, AT_REMOVEDIR);
        manifest_exec(t, "DELETE FROM trash WHERE id = ?", id, NULL, NULL, NULL, 0, NULL);
    }
    if (root >= 0) close(root);
    if (box >= 0) close(box);
    if (bin >= 0) close(bin);
    free_leftovers(&l);
    return status;
}

/* Puts a leftovers entry back file by file; a name taken again stays in the trash. */
static void restore_mirror(int from, int to, int depth, long long *restored, long long *kept) {
    size_t count = 0;
    char **list = depth > MAX_DEPTH + 1 ? NULL : names(from, &count);
    if (!list) return;
    for (size_t i = 0; i < count; i++) {
        struct stat st;
        char name[256];
        if (fstatat(from, list[i], &st, AT_SYMLINK_NOFOLLOW)) continue;
        int folder = S_ISDIR(st.st_mode);
        if (!original_name(list[i], !folder, name, sizeof(name)) || (!depth && !strcmp(name, ".disc")) ||
            !strcmp(name, ".") || !strcmp(name, "..")) { (*kept)++; continue; }
        if (folder) {
            int sub = openat(from, list[i], FOLDER_FLAGS), target = -1;
            if (sub >= 0 && (!mkdirat(to, name, 0755) || errno == EEXIST)) target = openat(to, name, FOLDER_FLAGS);
            if (sub >= 0 && target >= 0) restore_mirror(sub, target, depth + 1, restored, kept);
            else (*kept)++;
            if (sub >= 0) close(sub);
            if (target >= 0) close(target);
            unlinkat(from, list[i], AT_REMOVEDIR); /* only when emptied */
        } else if (S_ISREG(st.st_mode) && fstatat(to, name, &st, AT_SYMLINK_NOFOLLOW) && errno == ENOENT &&
                   !renameat(from, list[i], to, name)) (*restored)++;
        else (*kept)++;
    }
    free_names(list, count);
}

static int restore_leftovers(const disc_trash *t, long long id, char **json, size_t *length, const char **problem) {
    char id_name[24];
    snprintf(id_name, sizeof(id_name), "%lld", id);
    int bin = open_bin(t, 0), box = bin >= 0 ? openat(bin, id_name, FOLDER_FLAGS) : -1, root = open(t->card, FOLDER_FLAGS & ~O_NOFOLLOW);
    long long restored = 0, kept = 0;
    int status = 503;
    *problem = "Trash unavailable";
    if (box < 0) { *problem = "The entry's files are missing; purge it"; status = 409; }
    else if (root >= 0) {
        restore_mirror(box, root, 0, &restored, &kept);
        close(box);
        box = -1;
        /* Everything back: the entry goes; otherwise it keeps what could not return. */
        if (!unlinkat(bin, id_name, AT_REMOVEDIR)) manifest_exec(t, "DELETE FROM trash WHERE id = ?", id, NULL, NULL, NULL, 0, NULL);
        disc_buffer b = {0};
        disc_buffer_text(&b, "{\"id\":");
        disc_buffer_int(&b, id);
        disc_buffer_text(&b, ",\"path\":");
        disc_buffer_string(&b, t->card, strlen(t->card));
        disc_buffer_text(&b, ",\"restored\":");
        disc_buffer_text(&b, kept ? "false" : "true");
        disc_buffer_text(&b, ",\"files\":");
        disc_buffer_int(&b, restored);
        disc_buffer_text(&b, ",\"kept\":");
        disc_buffer_int(&b, kept);
        disc_buffer_text(&b, "}");
        reply(&b, json, length);
        status = *json ? 200 : 503;
        *problem = NULL;
    }
    if (box >= 0) close(box);
    if (root >= 0) close(root);
    if (bin >= 0) close(bin);
    return status;
}

int disc_trash_restore(const disc_trash *t, long long id, char **json, size_t *length, const char **problem) {
    *json = NULL;
    *length = 0;
    char original[MAX_PATH + 1], name[256], id_name[24], stored[300];
    int kind = KIND_FILE, status = manifest_get(t, id, original, sizeof(original), &kind);
    int folder = kind == KIND_FOLDER;
    if (status == 404) { *problem = "No such entry in the trash"; return 404; }
    if (status != 200) { *problem = "Trash unavailable"; return 503; }
    if (kind == KIND_LEFTOVERS) return restore_leftovers(t, id, json, length, problem);
    if (!card_path(t->card, original)) { *problem = "The entry's original path is not on the card"; return 409; }
    int bin = open_bin(t, 0), box = -1, parent = -1;
    struct stat st;
    snprintf(id_name, sizeof(id_name), "%lld", id);
    status = 503;
    *problem = "Trash unavailable";
    if (bin < 0 || (box = openat(bin, id_name, FOLDER_FLAGS)) < 0) { *problem = "The entry's files are missing; purge it"; status = 409; goto out; }
    if ((parent = open_parent(t->card, original, 1, name, sizeof(name))) < 0) goto out;
    if (!fstatat(parent, name, &st, AT_SYMLINK_NOFOLLOW) || errno != ENOENT) { *problem = "The name is taken again"; status = 409; goto out; }
    if (!trash_name(name, !folder, stored, sizeof(stored)) || fstatat(box, stored, &st, AT_SYMLINK_NOFOLLOW)) {
        *problem = "The entry's files are missing; purge it"; status = 409; goto out;
    }
    int moved = folder ? openat(box, stored, FOLDER_FLAGS) : -1;
    if (folder && (moved < 0 || rename_tree(moved, 0, 0))) {
        /* Never leave audio names inside the trash, where stock's scanner looks. */
        if (moved >= 0) { rename_tree(moved, 0, 1); close(moved); }
        goto out;
    }
    if (renameat(box, stored, parent, name)) {
        if (moved >= 0) { rename_tree(moved, 0, 1); close(moved); }
        goto out;
    }
    if (moved >= 0) close(moved);
    fsync(parent);
    unlinkat(bin, id_name, AT_REMOVEDIR);
    manifest_exec(t, "DELETE FROM trash WHERE id = ?", id, NULL, NULL, NULL, 0, NULL);
    disc_buffer b = {0};
    disc_buffer_text(&b, "{\"id\":");
    disc_buffer_int(&b, id);
    disc_buffer_text(&b, ",\"path\":");
    disc_buffer_string(&b, original, strlen(original));
    disc_buffer_text(&b, ",\"restored\":true}");
    reply(&b, json, length);
    status = *json ? 200 : 503;
    *problem = NULL;
out:
    if (parent >= 0) close(parent);
    if (box >= 0) close(box);
    if (bin >= 0) close(bin);
    return status;
}

/* Removes one entry's files and its manifest row. */
static int purge_one(const disc_trash *t, int bin, long long id) {
    char id_name[24];
    snprintf(id_name, sizeof(id_name), "%lld", id);
    int box = bin >= 0 ? openat(bin, id_name, FOLDER_FLAGS) : -1;
    if (box >= 0) {
        int failed = remove_tree(box, 0);
        close(box);
        if (failed || unlinkat(bin, id_name, AT_REMOVEDIR)) return 503;
    }
    return manifest_exec(t, "DELETE FROM trash WHERE id = ?", id, NULL, NULL, NULL, 0, NULL);
}

int disc_trash_purge(const disc_trash *t, long long id, char **json, size_t *length, const char **problem) {
    *json = NULL;
    *length = 0;
    char original[MAX_PATH + 1];
    int kind = KIND_FILE, status = manifest_get(t, id, original, sizeof(original), &kind);
    if (status == 404) { *problem = "No such entry in the trash"; return 404; }
    int bin = status == 200 ? open_bin(t, 0) : -1;
    if (status == 200) status = purge_one(t, bin, id);
    if (bin >= 0) close(bin);
    if (status != 200) { *problem = "Trash unavailable"; return 503; }
    disc_buffer b = {0};
    disc_buffer_text(&b, "{\"id\":");
    disc_buffer_int(&b, id);
    disc_buffer_text(&b, ",\"purged\":true}");
    reply(&b, json, length);
    *problem = NULL;
    return *json ? 200 : 503;
}

int disc_trash_empty(const disc_trash *t, char **json, size_t *length, const char **problem) {
    *json = NULL;
    *length = 0;
    *problem = "Trash unavailable";
    sqlite3 *db = NULL;
    long long ids[256], purged = 0;
    size_t count;
    int bin = open_bin(t, 0);
    /* In rounds of 256 entries: the manifest rows name the folders to remove. */
    do {
        count = 0;
        int opened = disc_database_open(t->database, 0, &db);
        if (opened == DISC_DATABASE_ABSENT) break;
        if (opened != DISC_DATABASE_OK) { if (bin >= 0) close(bin); return 503; }
        sqlite3_stmt *stmt = NULL;
        if (sqlite3_prepare_v2(db, "SELECT id FROM trash ORDER BY id LIMIT 256", -1, &stmt, NULL) == SQLITE_OK)
            while (sqlite3_step(stmt) == SQLITE_ROW && count < 256) ids[count++] = sqlite3_column_int64(stmt, 0);
        sqlite3_finalize(stmt);
        disc_database_close(t->database, db);
        for (size_t i = 0; i < count; i++) {
            if (purge_one(t, bin, ids[i]) != 200) { if (bin >= 0) close(bin); return 503; }
            purged++;
        }
    } while (count == 256);
    /* Folders no manifest row names (an interrupted move) go as well. */
    size_t left = 0;
    char **list = bin >= 0 ? names(bin, &left) : NULL;
    for (size_t i = 0; list && i < left; i++) {
        if (strspn(list[i], "0123456789") != strlen(list[i])) continue;
        int box = openat(bin, list[i], FOLDER_FLAGS);
        if (box >= 0) { remove_tree(box, 0); close(box); unlinkat(bin, list[i], AT_REMOVEDIR); }
    }
    if (list) free_names(list, left);
    if (bin >= 0) close(bin);
    disc_buffer b = {0};
    disc_buffer_text(&b, "{\"purged\":");
    disc_buffer_int(&b, purged);
    disc_buffer_text(&b, "}");
    reply(&b, json, length);
    *problem = NULL;
    return *json ? 200 : 503;
}
