#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#include "civetweb.h"
#include "framing.h"
#include "webroot.h"
#include "settings.h"
#include "apps.h"
#include "update.h"
#include "catalog.h"
#include "data.h"
#include "media.h"
#include "facts.h"
#include "history.h"
#include "store.h"
#include "trash.h"
#include "lists.h"
#include "tree.h"
#include "origins.h"
#include "log.h"
#include "jsonutil.h"
#include "manager_assets.h"
#include <arpa/inet.h>
#include <ctype.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <errno.h>
#include <ifaddrs.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* The service's version (the combined image it belongs to) and the source
 * commit it was built from (scripts/build.sh passes it). */
#define DISC_SERVICE_VERSION "0.9.0"
#ifndef DISC_BUILD
#define DISC_BUILD "unknown"
#endif
#define DISC_PACING_CLASSES 16
typedef struct { char class[17]; long long last_ms; } disc_pace;
typedef struct {
    const char *listen, *authority, *upstream;
    const char *commands_profile_sha256, *upload_root, *data_root, *raw_marker, *current_lyrics;
    /* Read-only device facts from the reviewed OS profile's sources. */
    const char *battery_dir, *asound_dir;
    /* The service's database on the card (combined-008) and the play observer:
     * the player process and the proc root. */
    const char *database_file, *player_process, *proc_root;
    disc_database database;
    /* The trash on the card (combined-008): <card>/.disc/trash. */
    const char *trash_dir;
    /* M3U lists (combined-009): internal ones, the service's own, hidden in
     * <card>/.disc/playlists, and external ones, which the player's own file browser
     * shows, in <card>/Playlists (owner, 2026-09-29). */
    const char *lists_dir, *external_lists_dir;
    /* Under the boot layer (snowsky-disc-boot docs/contract.md): the file whose
     * creation tells boot the service listens, and the folder of boot's status
     * files (boot.json, service.json) the diagnostics show. */
    const char *ready_file, *boot_status;
    /* The application manager (owner, 2026-10-02): its own listener and origin; the settings file
     * (ports, the app served at "/") as last read or written, guarded by lock. */
    int manager_port, manager_busy;
    const char *manager_authority, *settings_file;
    /* The server's updates (owner, 2026-10-02): the boot layer's inactive slot and request file,
     * the public keys this package trusts and the boot program that checks a staged update. */
    const char *update_slot, *update_work, *update_request, *update_keys, *boot_program;
    disc_settings settings;
    /* The reviewed catalogs (combined-009): the image's (catalog_dir), the
     * card's queries.json and store.json (card_catalog_dir) and, on the
     * engineering image only, the card's commands.json (card_commands). */
    const char *catalog_dir, *card_catalog_dir, *card_commands;
    long long started_ms;
    /* The player's mDNS host label, the only .local name admitted. */
    const char *mdns_name;
    disc_trash trash;
    unsigned observer_interval_ms;
    /* Pairing: the player's serial number, the only credential (combined-008). */
    const char *serial_file;
    int port, tcp_port, http_port, active, catalog_active, upload_active, media_active, audio_active, tree_active;
    /* Scan guard: stock reports a scan on the owner session (a60a 000F, a622
     * counts, a60a 0005 at the end); mutations wait until it ends. */
    int scanning;
    long long scan_seen_ms;
    /* Failed credential attempts per client address (token or SN), and all
     * failures together: many addresses cannot share out the SN guesses. */
    struct { char ip[48]; int failures; long long first_ms, locked_until_ms; } attempts[32];
    int sn_failures;
    long long sn_first_ms;
    disc_webroot webroot;
    pthread_mutex_t lock;
    /* Bounded replay memory for mutation request IDs, shared by every channel. */
    char request_ids[256][65];
    unsigned request_next;
    disc_pace http_pacing[DISC_PACING_CLASSES];
    /* One parsed catalog shared by every session and stock request; re-read
     * only when the active release's commands.json identity changes. The same
     * for queries.json and store.json. */
    struct catalog_cache *catalog_cache, *queries_cache, *store_cache, *origins_cache, *hosted_cache;
    /* The card's overrides (combined-009), kept apart so a rejected one leaves the image's in force. */
    struct catalog_cache *card_caches[4];
    /* The skip rule: consecutive skips without a track heard in between. */
    int skip_streak;
    pthread_mutex_t catalog_lock;
} server;
/* One cached card file (commands.json, queries.json or store.json) shared by reference count. */
typedef struct catalog_cache {
    disc_catalog catalog;
    disc_queries queries;
    disc_store store;
    disc_origins origins;
    disc_hosted hosted;
    int refs, usable, from_card;
    dev_t dev; ino_t ino; off_t size; long long mtime;
} catalog_cache;
typedef struct {
    server *srv;
    struct mg_connection *ws;
    int fd, started, fragmented, message_kind;
    pthread_t reader;
    atomic_int stop;
    disc_frames incoming, outgoing;
    unsigned char message[DISC_FRAME_MAX];
    size_t message_len;
    catalog_cache *catalog;
    int authenticated;
    char pending_request[65];
    disc_pace pacing[DISC_PACING_CLASSES];
} session;
static volatile sig_atomic_t stopping;
static void stop_signal(int sig) { (void)sig; stopping = 1; }
static long long monotonic_ms(void);
static int local_address(const char *address);
static int port_value(const char *v);

/* Cross-origin pages (2026-09-30): a hosted HTTPS page may use the API when its
 * exact origin is in the reviewed hosted.json (or listed with --cors-origin for
 * a lab); none is by default.
 * The Host must still be the player's own address or name, so DNS rebinding
 * stays closed, and changes still need the serial number. The browser asks
 * the user for local network access first (Local Network Access). */
#define DISC_CORS_MAX 4
static const char *cors_origins[DISC_CORS_MAX];
static int cors_count;
/* https://<lower-case host>[:port], nothing after it. */
static int cors_origin_valid(const char *v) {
    size_t n = strlen(v);
    if (n < 9 || n > DISC_ORIGIN_MAX || strncmp(v, "https://", 8)) return 0;
    const char *host = v + 8, *colon = strchr(host, ':');
    if (strspn(host, "abcdefghijklmnopqrstuvwxyz0123456789.-:") != strlen(host)) return 0;
    if (host[0] == '.' || host[0] == '-' || host[0] == ':') return 0;
    return !colon || (!strchr(colon + 1, ':') && port_value(colon + 1));
}
/* The listed origin of the request this worker handles, or "" (same-origin, no Origin, or not
 * listed). allowed() sets it first thing for every request and WebSocket upgrade. */
static __thread char request_origin[DISC_ORIGIN_MAX + 1];
static const char *cross_origin(void) { return request_origin[0] ? request_origin : NULL; }
static void note_cross_origin(const struct mg_connection *c, server *s);
/* What every answer to a listed origin carries: the origin, and the stock and media headers the
 * page reads. Empty for any other request. */
#define DISC_CORS_EXPOSE "total-num, mark-pos, is-exist, X-Lyrics-Source, X-Lyrics-Age, X-Catalog-Source, Content-Range, Accept-Ranges"
static const char *cors_headers(const struct mg_connection *c) {
    static __thread char headers[400];
    (void)c;
    const char *origin = cross_origin();
    if (!origin) return "";
    snprintf(headers, sizeof(headers),
             "Access-Control-Allow-Origin: %s\r\nVary: Origin\r\nAccess-Control-Expose-Headers: " DISC_CORS_EXPOSE "\r\n", origin);
    return headers;
}

static int response(struct mg_connection *c, int code, const char *type, const void *body, size_t n) {
    mg_printf(c, "HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %zu\r\n"
              "Connection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n%s"
              "Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'\r\n\r\n",
              code, mg_get_response_code_text(c, code), type, n, cors_headers(c));
    if (n) mg_write(c, body, n);
    return code;
}
static int error(struct mg_connection *c, int code, const char *msg) {
    return response(c, code, "text/plain; charset=utf-8", msg, strlen(msg));
}
/* Streaming to slow clients (owner's player over Wi-Fi, 2026-09-26: a fixed
 * 3-second budget cut large release scripts). A response may take as long as
 * the client keeps taking at least DISC_STREAM_MIN_BYTES per
 * DISC_STREAM_WINDOW_MS, up to DISC_STREAM_CAP_MS in all; a slower client is
 * stalled and its response closed at the end of that window. A stalled reader
 * still drips a few bytes through TCP window updates, so "no byte at all" would
 * never fire. */
#define DISC_STREAM_WINDOW_MS 5000
#define DISC_STREAM_MIN_BYTES (16u * 1024u)
#define DISC_STREAM_CAP_MS 120000LL
typedef struct { long long cap, window_end; size_t window_bytes; } disc_stream;
static void stream_start(disc_stream *st) {
    long long now = monotonic_ms();
    st->cap = now + DISC_STREAM_CAP_MS;
    st->window_end = now + DISC_STREAM_WINDOW_MS;
    st->window_bytes = 0;
}
/* Sets the connection's write budget to the end of the current window (or the
 * cap); a window that ended with too little progress stops the response. */
static int stream_budget(struct mg_connection *c, disc_stream *st) {
    long long now = monotonic_ms();
    if (now >= st->cap) return 0;
    if (now >= st->window_end) {
        if (st->window_bytes < DISC_STREAM_MIN_BYTES) return 0;
        st->window_end = now + DISC_STREAM_WINDOW_MS;
        st->window_bytes = 0;
    }
    long long until = st->window_end < st->cap ? st->window_end : st->cap;
    mg_disc_set_write_budget(c, (unsigned)(until - now));
    return 1;
}
static int write_all(struct mg_connection *c, const char *data, size_t n, disc_stream *st) {
    size_t sent = 0;
    while (sent < n && !stopping && stream_budget(c, st)) {
        int w = mg_write(c, data + sent, n - sent);
        if (w <= 0) {
            /* A budget that ran out at the window's end is judged by the window. */
            if (monotonic_ms() < st->window_end) break;
            continue;
        }
        sent += (size_t)w;
        st->window_bytes += (size_t)w;
    }
    return sent == n;
}
/* A file of an app, with its policy (same-origin, plus the app's own external
 * origins). HTML is never cached, a content-hashed name is kept for good,
 * anything else is revalidated (combined-009). A file from the card stops
 * when the card leaves the player. */
static int web_asset_response(struct mg_connection *c, const disc_webroot *from, disc_web_asset *asset,
                              int head, const char *policy) {
    disc_stream stream;
    stream_start(&stream);
    int code = 200;
    stream_budget(c, &stream);
    if (mg_printf(c, "HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %zu\r\n%s%s"
                     "Connection: close\r\nCache-Control: %s\r\nX-Content-Type-Options: nosniff\r\n"
                     "Content-Security-Policy: %s\r\n\r\n",
                  asset->mime, asset->size, asset->encoding ? "Content-Encoding: gzip\r\n" : "",
                  asset->varies ? "Vary: Accept-Encoding\r\n" : "",
                  asset->immutable > 0 ? "public, max-age=31536000, immutable" : asset->immutable < 0 ? "no-store" : "no-cache",
                  policy) <= 0) code = 500;
    if (!head && code == 200) {
        char buffer[8192]; size_t remaining = asset->size;
        while (remaining && !stopping) {
            if (!disc_webroot_available(from)) break;
            size_t requested = remaining < sizeof(buffer) ? remaining : sizeof(buffer);
            ssize_t n = read(asset->fd, buffer, requested);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0 || !write_all(c, buffer, (size_t)n, &stream)) break;
            remaining -= (size_t)n;
        }
        if (remaining) code = 500; /* Closing a partial response is the only safe outcome. */
    }
    close(asset->fd);
    return code;
}
static int header_count(const struct mg_connection *c, const char *name) {
    const struct mg_request_info *r = mg_get_request_info(c);
    int n = 0;
    for (int i = 0; i < r->num_headers; i++) if (!strcasecmp(r->http_headers[i].name, name)) n++;
    return n;
}
/* The browser takes gzip: a "gzip" coding listed without q=0 (RFC 9110 12.5.3). */
static int accepts_gzip(const struct mg_connection *c) {
    const char *v = mg_get_header(c, "Accept-Encoding");
    if (!v || header_count(c, "Accept-Encoding") != 1 || strlen(v) > 256) return 0;
    for (const char *p = v; *p;) {
        while (*p == ' ' || *p == '\t' || *p == ',') p++;
        const char *start = p;
        while (*p && *p != ',' && *p != ';' && *p != ' ' && *p != '\t') p++;
        int gzip = p - start == 4 && !strncasecmp(start, "gzip", 4);
        int refused = 0;
        while (*p && *p != ',') {
            while (*p == ' ' || *p == '\t' || *p == ';') p++;
            if ((p[0] == 'q' || p[0] == 'Q') && p[1] == '=') {
                const char *q = p + 2;
                /* q=0, q=0., q=0.0 ... refuse the coding; anything else accepts it. */
                if (*q == '0') {
                    q++;
                    if (*q == '.') { q++; while (*q == '0') q++; }
                    refused = !*q || *q == ',' || *q == ';' || *q == ' ' || *q == '\t';
                }
            }
            while (*p && *p != ',' && *p != ';') p++;
        }
        if (gzip) return !refused;
    }
    return 0;
}
/* The Host and Origin a listener admits: its authority, or the player's LAN address or mDNS name
 * with its port; a listed hosted origin only where cross is set (never the manager's). */
static int allowed_for(const struct mg_connection *c, server *s, const char *authority, int listener_port, int cross) {
    const char *h = mg_get_header(c, "Host"), *o = mg_get_header(c, "Origin");
    if (header_count(c, "Host") != 1 || !h || header_count(c, "Origin") > 1) return 0;
    char origin[160];
    if (!strcmp(h, authority)) {
        snprintf(origin, sizeof(origin), "http://%s", h);
        return (!o || !strcmp(o, origin) || (cross && cross_origin())) ? 1 : 0;
    }
    /* The Wi-Fi listener is on by default (combined-008): a LAN Host is the
     * player's address or its mDNS name with the service port; every change
     * still needs the player's serial number. */
    const char *colon = strrchr(h, ':');
    char port[8]; snprintf(port, sizeof(port), "%d", listener_port);
    if (!colon || strlen(h) > 140 || strcmp(colon + 1, port)) return 0;
    size_t n = (size_t)(colon - h);
    /* The player's own mDNS name (stock publishes ingenic.local; --mdns-name
     * from the OS profile) keeps the page's origin, and so its saved pairing,
     * when the LAN address changes. Only that name (combined-008 review): any
     * other .local name could be spoofed on the LAN to rebind a page there. */
    if (n > 6 && !strncasecmp(colon - 6, ".local", 6)) {
        if (!s->mdns_name || n - 6 != strlen(s->mdns_name) || strncasecmp(h, s->mdns_name, n - 6)) return 0;
        snprintf(origin, sizeof(origin), "http://%s", h);
        return (!o || !strcmp(o, origin) || (cross && cross_origin())) ? 2 : 0;
    }
    if (!n || n >= INET_ADDRSTRLEN) return 0;
    char ip[INET_ADDRSTRLEN]; memcpy(ip, h, n); ip[n] = 0;
    if (!local_address(ip)) return 0;
    snprintf(origin, sizeof(origin), "http://%s", h);
    return (!o || !strcmp(o, origin) || (cross && cross_origin())) ? 2 : 0;
}
static int allowed(const struct mg_connection *c, server *s) {
    note_cross_origin(c, s);
    return allowed_for(c, s, s->authority, s->port, 1);
}
static int bodyless(const struct mg_connection *c) {
    const struct mg_request_info *r = mg_get_request_info(c);
    const char *cl = mg_get_header(c, "Content-Length");
    return !strcmp(r->request_method, "GET") && (!cl || !strcmp(cl, "0")) &&
           !mg_get_header(c, "Transfer-Encoding") && !r->query_string;
}
static int local_address(const char *address) {
    struct in_addr target;
    if (inet_pton(AF_INET, address, &target) != 1) return 0;
    struct ifaddrs *all = NULL;
    if (getifaddrs(&all)) return 0;
    int found = 0;
    for (struct ifaddrs *i = all; i; i = i->ifa_next)
        if (i->ifa_addr && i->ifa_addr->sa_family == AF_INET &&
            ((struct sockaddr_in *)i->ifa_addr)->sin_addr.s_addr == target.s_addr) found = 1;
    freeifaddrs(all); return found;
}
static int connect_tcp_once(server *s, int timeout_ms) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    struct sockaddr_in addr = {.sin_family = AF_INET, .sin_port = htons((uint16_t)s->tcp_port)};
    inet_pton(AF_INET, s->upstream, &addr.sin_addr);
    /* Use the connect syscall's translated errno. In the current qemu-user
     * runtime SO_ERROR returns host ECONNREFUSED=111, while MIPS uses 146.
     * A bounded blocking connect avoids depending on that socket-option ABI. */
    struct timeval connect_timeout = {.tv_sec = timeout_ms / 1000, .tv_usec = (timeout_ms % 1000) * 1000};
    if (setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &connect_timeout, sizeof(connect_timeout)) ||
        connect(fd, (struct sockaddr *)&addr, sizeof(addr))) {
        int saved = errno; close(fd); errno = saved; return -1;
    }
    struct timeval timeout = {.tv_sec = 3};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    return fd;
}
static long long monotonic_ms(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static int connect_tcp(server *s) {
    const long long deadline = monotonic_ms() + 3000;
    for (;;) {
        int remaining = (int)(deadline - monotonic_ms());
        if (remaining <= 0) return -1;
        int fd = connect_tcp_once(s, remaining);
        if (fd >= 0 || errno != ECONNREFUSED) return fd;
        /* Stock briefly removes its listener after disconnect. Retry only
         * connection establishment, before any application byte is sent. */
        struct timespec delay = {.tv_sec = 0, .tv_nsec = 100000000};
        nanosleep(&delay, NULL);
    }
}
static int ws_close_code(session *s, unsigned code) {
    char bytes[2] = {(char)(code >> 8), (char)code};
    mg_websocket_write(s->ws, MG_WEBSOCKET_OPCODE_CONNECTION_CLOSE, bytes, 2);
    return 0;
}
/* A scan claim expires if stock never reports its end to an open session. */
#define DISC_SCAN_STALE_MS (15LL * 60 * 1000)
static void observe_scan(server *srv, const unsigned char *p, size_t n) {
    if (n < 8) return;
    int start = 0, end = 0;
    if (!memcmp(p, "a622", 4)) start = 1;
    else if (!memcmp(p, "a60a", 4) && n == 12) {
        if (!strncasecmp((const char *)p + 8, "000F", 4)) start = 1;
        else if (!strncasecmp((const char *)p + 8, "0005", 4)) end = 1;
    }
    if (!start && !end) return;
    pthread_mutex_lock(&srv->lock);
    srv->scanning = start;
    srv->scan_seen_ms = monotonic_ms();
    pthread_mutex_unlock(&srv->lock);
}
static int scan_active(server *srv) {
    pthread_mutex_lock(&srv->lock);
    int active = srv->scanning && monotonic_ms() - srv->scan_seen_ms < DISC_SCAN_STALE_MS;
    pthread_mutex_unlock(&srv->lock);
    return active;
}
static int to_ws(unsigned char *p, size_t n, void *arg) {
    session *s = arg;
    observe_scan(s->srv, p, n);
    int opcode = disc_utf8(p, n) ? MG_WEBSOCKET_OPCODE_TEXT : MG_WEBSOCKET_OPCODE_BINARY;
    return mg_websocket_write(s->ws, opcode, (char *)p, n) > 0 ? 0 : -1;
}
static int valid_request_id(const char *id, size_t n) {
    if (n < 16 || n > 64) return 0;
    for (size_t i = 0; i < n; i++) {
        char c = id[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_')) return 0;
    }
    return 1;
}
/* Stores a request ID unless it was seen recently; the ring bounds memory. */
static int remember_request(server *srv, const char *id) {
    int fresh = 1;
    pthread_mutex_lock(&srv->lock);
    for (size_t i = 0; i < 256; i++) if (!strcmp(srv->request_ids[i], id)) fresh = 0;
    if (fresh) { snprintf(srv->request_ids[srv->request_next % 256], 65, "%s", id); srv->request_next++; }
    pthread_mutex_unlock(&srv->lock);
    return fresh;
}
/* The player's serial number (first run of 6..32 letters or digits in the SN
 * file). It is never served; it only answers a pairing attempt. */
static int read_serial(const server *s, char out[33]) {
    out[0] = 0;
    if (!s->serial_file) return 0;
    int fd = open(s->serial_file, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return 0;
    char buffer[128];
    ssize_t n = read(fd, buffer, sizeof(buffer) - 1);
    close(fd);
    if (n <= 0) return 0;
    buffer[n] = 0;
    size_t start = 0;
    while (buffer[start] && !isalnum((unsigned char)buffer[start])) start++;
    size_t end = start;
    while (isalnum((unsigned char)buffer[end])) end++;
    if (end - start < 6 || end - start > 32) return 0;
    memcpy(out, buffer + start, end - start);
    out[end - start] = 0;
    return 1;
}
/* Pairing needs the player's serial number, readable from its SN file. */
static int sn_pairing(const server *s) {
    char sn[33];
    return s->serial_file && read_serial(s, sn);
}
#define DISC_ATTEMPT_LIMIT 5
#define DISC_SN_FAILURE_LIMIT 20
#define DISC_ATTEMPT_WINDOW_MS (10LL * 60 * 1000)
/* Five failed credentials from one address within ten minutes lock that
 * address out for ten minutes, even with a correct credential: an SN is short
 * and printed on the player, so it must not be guessable over the network.
 * Twenty failures from all addresses together suspend pairing until ten
 * minutes pass after the first of them, so the SN cannot be guessed from
 * many addresses either. */
static int credential_matches(server *srv, const char *ip, const char *presented, size_t n) {
    long long now = monotonic_ms();
    pthread_mutex_lock(&srv->lock);
    int slot = -1, spare = -1;
    for (int i = 0; i < 32; i++) {
        if (!strcmp(srv->attempts[i].ip, ip)) { slot = i; break; }
        /* A locked address keeps its entry; the stalest unlocked one is reused. */
        if (srv->attempts[i].locked_until_ms <= now &&
            (spare < 0 || srv->attempts[i].first_ms < srv->attempts[spare].first_ms)) spare = i;
    }
    int locked = (slot >= 0 && srv->attempts[slot].locked_until_ms > now) || (slot < 0 && spare < 0);
    if (now - srv->sn_first_ms > DISC_ATTEMPT_WINDOW_MS) { srv->sn_first_ms = now; srv->sn_failures = 0; }
    int sn_open = srv->sn_failures < DISC_SN_FAILURE_LIMIT;
    pthread_mutex_unlock(&srv->lock);
    if (locked) return 0;
    int ok = 0;
    char sn[33];
    if (sn_open && read_serial(srv, sn) && strlen(sn) == n) {
        unsigned diff = 0;
        for (size_t i = 0; i < n; i++) diff |= (unsigned)(sn[i] ^ presented[i]);
        ok = diff == 0;
    }
    pthread_mutex_lock(&srv->lock);
    if (ok) {
        if (slot >= 0) srv->attempts[slot].failures = 0;
    } else {
        if (++srv->sn_failures == DISC_SN_FAILURE_LIMIT)
            disc_log("Serial-number pairing suspended for ten minutes after repeated failures\n");
        if (slot < 0) {
            slot = spare;
            memset(&srv->attempts[slot], 0, sizeof(srv->attempts[slot]));
            snprintf(srv->attempts[slot].ip, sizeof(srv->attempts[slot].ip), "%s", ip);
        }
        if (now - srv->attempts[slot].first_ms > DISC_ATTEMPT_WINDOW_MS) {
            srv->attempts[slot].first_ms = now;
            srv->attempts[slot].failures = 0;
        }
        if (++srv->attempts[slot].failures >= DISC_ATTEMPT_LIMIT) {
            srv->attempts[slot].locked_until_ms = now + DISC_ATTEMPT_WINDOW_MS;
            disc_log("Pairing attempts from %s locked for ten minutes\n", ip);
        }
    }
    pthread_mutex_unlock(&srv->lock);
    return ok;
}
/* A mutation needs a session paired with the player's serial number (still
 * readable) and one fresh request ID; pacing then delays the send by the
 * catalog's class interval. */
static int authorize_mutation(session *s, const disc_command *cmd) {
    server *srv = s->srv;
    if (!s->authenticated || !s->pending_request[0]) return 0;
    if (!sn_pairing(srv)) return 0;
    if (!remember_request(srv, s->pending_request)) return 0;
    s->pending_request[0] = 0;
    disc_pace *slot = NULL;
    for (int i = 0; i < DISC_PACING_CLASSES; i++) {
        if (!strcmp(s->pacing[i].class, cmd->class)) { slot = &s->pacing[i]; break; }
        if (!slot && !s->pacing[i].class[0]) slot = &s->pacing[i];
    }
    if (!slot) return 0;
    if (!slot->class[0]) snprintf(slot->class, sizeof(slot->class), "%s", cmd->class);
    long long now = monotonic_ms(), wait = slot->last_ms + cmd->pacing_ms - now;
    if (slot->last_ms && wait > 0 && wait <= 10000) {
        struct timespec delay = {.tv_sec = wait / 1000, .tv_nsec = (wait % 1000) * 1000000};
        nanosleep(&delay, NULL);
    }
    slot->last_ms = monotonic_ms();
    return 1;
}
static void free_cache(catalog_cache *cache) {
    disc_catalog_free(&cache->catalog); disc_queries_free(&cache->queries); disc_store_free(&cache->store); free(cache);
}
static void drop_catalog(server *srv, catalog_cache *cache) {
    if (!cache) return;
    pthread_mutex_lock(&srv->catalog_lock);
    int last = --cache->refs == 0 && srv->catalog_cache != cache && srv->queries_cache != cache && srv->store_cache != cache &&
               srv->origins_cache != cache && srv->hosted_cache != cache && srv->card_caches[0] != cache &&
               srv->card_caches[1] != cache && srv->card_caches[2] != cache && srv->card_caches[3] != cache;
    pthread_mutex_unlock(&srv->catalog_lock);
    if (last) free_cache(cache);
}
enum { CARD_COMMANDS, CARD_QUERIES, CARD_STORE, CARD_HOSTED, CARD_COMPATIBILITY };
static const char *const CATALOG_NAMES[] = {"commands.json", "queries.json", "store.json", "hosted.json", "compatibility.json"};
/* The effective file of a catalog (combined-009): the card's override in
 * .disc/catalog when the image admits one there (queries and store on every
 * image, commands on the engineering image only) and the card has it, else
 * the image's reviewed one. *from_card says which. */
static int catalog_file(server *srv, int kind, disc_web_asset *asset, int *from_card) {
    const char *card = kind == CARD_COMMANDS ? srv->card_commands : kind == CARD_COMPATIBILITY ? NULL : srv->card_catalog_dir;
    *from_card = card && disc_folder_open(&srv->webroot, card, CATALOG_NAMES[kind], asset);
    return *from_card || (srv->catalog_dir && disc_folder_open(NULL, srv->catalog_dir, CATALOG_NAMES[kind], asset));
}
/* One catalog file from a folder (the card's override or the image's), parsed
 * once per file identity (device, inode, size, mtime) and shared afterwards;
 * a rejected file is not retried until it changes. */
static catalog_cache *load_catalog_from(server *srv, int kind, const char *folder, catalog_cache **slot, int from_card) {
    disc_web_asset asset;
    const char *name = CATALOG_NAMES[kind];
    if (!disc_folder_open(from_card ? &srv->webroot : NULL, folder, name, &asset)) return NULL;
    struct stat st;
    if (fstat(asset.fd, &st)) { close(asset.fd); return NULL; }
    long long mtime = (long long)st.st_mtime;
    pthread_mutex_lock(&srv->catalog_lock);
    catalog_cache *cache = *slot;
    if (cache && cache->dev == st.st_dev && cache->ino == st.st_ino && cache->size == st.st_size && cache->mtime == mtime) {
        int usable = cache->usable;
        if (usable) cache->refs++;
        pthread_mutex_unlock(&srv->catalog_lock);
        close(asset.fd);
        return usable ? cache : NULL;
    }
    pthread_mutex_unlock(&srv->catalog_lock);
    char *json = asset.size && asset.size <= DISC_CATALOG_MAX_JSON ? malloc(asset.size) : NULL;
    size_t used = 0;
    while (json && used < asset.size) {
        ssize_t n = read(asset.fd, json + used, asset.size - used);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        used += (size_t)n;
    }
    close(asset.fd);
    catalog_cache *fresh = calloc(1, sizeof(*fresh));
    if (!fresh) { free(json); return NULL; }
    fresh->dev = st.st_dev; fresh->ino = st.st_ino; fresh->size = st.st_size; fresh->mtime = mtime;
    int parsed = json && used == asset.size &&
                 (kind == CARD_QUERIES ? !disc_queries_parse(&fresh->queries, json, used, srv->commands_profile_sha256)
                  : kind == CARD_STORE ? !disc_store_parse(&fresh->store, json, used, srv->commands_profile_sha256)
                  : kind == CARD_HOSTED ? !disc_hosted_parse(&fresh->hosted, json, used, srv->commands_profile_sha256)
                                       : !disc_catalog_parse(&fresh->catalog, json, used, srv->commands_profile_sha256));
    if (!parsed) disc_log("%s%s rejected; built-in behavior stays in force\n", from_card ? "The card's " : "", name);
    fresh->usable = parsed;
    fresh->from_card = from_card;
    free(json);
    pthread_mutex_lock(&srv->catalog_lock);
    catalog_cache *old = *slot;
    *slot = fresh;
    fresh->refs = 1 + parsed;
    int free_old = old && old->refs == 1;
    if (old) old->refs--;
    pthread_mutex_unlock(&srv->catalog_lock);
    if (free_old) free_cache(old);
    return parsed ? fresh : NULL;
}
/* The effective, referenced catalog (commands.json, queries.json, store.json or hosted.json):
 * the card's override where the image admits one and it passes its checks,
 * else the image's. Neither leaves the built-in behavior (the read-only set). */
static catalog_cache *load_card_file(server *srv, int kind) {
    if (!srv->commands_profile_sha256 || kind > CARD_HOSTED) return NULL;
    catalog_cache **image[] = {&srv->catalog_cache, &srv->queries_cache, &srv->store_cache, &srv->hosted_cache};
    const char *card = kind == CARD_COMMANDS ? srv->card_commands : srv->card_catalog_dir;
    catalog_cache *found = card ? load_catalog_from(srv, kind, card, &srv->card_caches[kind], 1) : NULL;
    if (!found && srv->catalog_dir) found = load_catalog_from(srv, kind, srv->catalog_dir, image[kind], 0);
    return found;
}
static catalog_cache *load_catalog(server *srv) { return load_card_file(srv, CARD_COMMANDS); }
/* Whether the request's Origin is a listed hosted page: the --cors-origin list (labs), then the
 * effective hosted.json (the image's, or the card's override). Only https origins are looked up. */
static void note_cross_origin(const struct mg_connection *c, server *s) {
    request_origin[0] = 0;
    const char *o = mg_get_header(c, "Origin");
    if (!o || strlen(o) > DISC_ORIGIN_MAX || strncmp(o, "https://", 8)) return;
    for (int i = 0; i < cors_count; i++)
        if (!strcmp(o, cors_origins[i])) { snprintf(request_origin, sizeof(request_origin), "%s", o); return; }
    catalog_cache *hosted = load_card_file(s, CARD_HOSTED);
    for (size_t i = 0; hosted && i < hosted->hosted.count; i++)
        if (!strcmp(o, hosted->hosted.origins[i])) { snprintf(request_origin, sizeof(request_origin), "%s", o); break; }
    drop_catalog(s, hosted);
}
static int raw_mode(server *srv) {
    return srv->raw_marker && disc_webroot_marker(&srv->webroot, srv->raw_marker, "DISC_WEB_RAW_RECORDS\n");
}
static const disc_command RAW_RECORD = {.tag = "raw", .class = "raw", .mutation = 1, .pacing_ms = 250, .timeout_ms = 4000, .max_bytes = 65535};
static int to_tcp(unsigned char *p, size_t n, void *arg) {
    session *s = arg;
    if (!disc_read_command(p, n)) {
        const disc_command *cmd = s->catalog ? disc_catalog_admit(&s->catalog->catalog, (const char *)p, (const char *)p + 8, n - 8) : NULL;
        if (!cmd && p[0] == '0' && !disc_catalog_builtin_denied((const char *)p) && raw_mode(s->srv)) {
            /* Engineering raw mode: exact card marker, token, request ID and pacing; logged, never silent. */
            cmd = &RAW_RECORD;
            disc_log("Raw record admitted under the engineering marker: %.4s (%zu bytes)\n", p, n);
        }
        if (!cmd) return -2;
        /* Refused before the token/request-ID bookkeeping: nothing was sent. */
        if (cmd->mutation && scan_active(s->srv)) return -4;
        if (cmd->mutation && !authorize_mutation(s, cmd)) return -2;
    }
    for (size_t sent = 0; sent < n;) {
        ssize_t rc = send(s->fd, p + sent, n - sent, 0);
        if (rc < 0 && errno == EINTR) continue;
        if (rc <= 0) return -3;
        sent += (size_t)rc;
    }
    return 0;
}
static void *receive_tcp(void *arg) {
    session *s = arg; unsigned char buf[4096];
    while (!atomic_load(&s->stop)) {
        struct pollfd p = {.fd = s->fd, .events = POLLIN};
        int ready = poll(&p, 1, 200);
        if (ready < 0 && errno == EINTR) continue;
        if (ready == 0) continue;
        if (ready < 0) break;
        ssize_t n = recv(s->fd, buf, sizeof(buf), 0);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) {
            if (!atomic_load(&s->stop)) disc_log("Stock TCP read ended: bytes=%ld errno=%d\n", (long)n, n < 0 ? errno : 0);
            break;
        }
        int rc = disc_feed(&s->outgoing, buf, (size_t)n, to_ws, s);
        if (rc) { disc_log("Stock frame forwarding failed: result=%d buffered=%zu\n", rc, s->outgoing.used); break; }
    }
    if (!atomic_load(&s->stop)) ws_close_code(s, 1011);
    atomic_store(&s->stop, 1);
    shutdown(s->fd, SHUT_RDWR);
    return NULL;
}
static void release(const struct mg_connection *c) {
    session *s = mg_get_user_connection_data(c);
    if (!s) return;
    atomic_store(&s->stop, 1); shutdown(s->fd, SHUT_RDWR);
    if (s->started) pthread_join(s->reader, NULL);
    close(s->fd);
    pthread_mutex_lock(&s->srv->lock); s->srv->active = 0; pthread_mutex_unlock(&s->srv->lock);
    drop_catalog(s->srv, s->catalog);
    mg_set_user_connection_data(c, NULL); free(s);
}
static void ws_closed(const struct mg_connection *c, void *arg) { (void)arg; release(c); }
static int ws_connect(const struct mg_connection *c, void *arg) {
    server *srv = arg;
    struct mg_connection *w = (struct mg_connection *)c;
    int admission = allowed(c, srv);
    if (!admission) { error(w, 403, "Host or Origin rejected\n"); return 1; }
    if (!bodyless(c)) { error(w, 400, "Bodyless GET required\n"); return 1; }
    pthread_mutex_lock(&srv->lock);
    if (srv->active) { pthread_mutex_unlock(&srv->lock); error(w, 409, "Control channel already owned\n"); return 1; }
    srv->active = 1; srv->scanning = 0; pthread_mutex_unlock(&srv->lock);
    int fd = connect_tcp(srv);
    if (fd < 0) disc_log("Stock TCP admission failed: errno=%d (%s)\n", errno, strerror(errno));
    session *s = fd < 0 ? NULL : calloc(1, sizeof(*s));
    if (!s) {
        if (fd >= 0) close(fd);
        pthread_mutex_lock(&srv->lock); srv->active = 0; pthread_mutex_unlock(&srv->lock);
        error(w, 503, "Stock control unavailable\n"); return 1;
    }
    s->srv = srv; s->fd = fd; s->ws = w;
    s->catalog = load_catalog(srv);
    atomic_init(&s->stop, 0);
    mg_set_user_connection_data(c, s); return 0;
}
static void ws_ready(struct mg_connection *c, void *arg) {
    (void)arg; session *s = mg_get_user_connection_data(c);
    if (pthread_create(&s->reader, NULL, receive_tcp, s)) { atomic_store(&s->stop, 1); ws_close_code(s, 1011); }
    else s->started = 1;
}
static int ws_data(struct mg_connection *c, int bits, char *data, size_t n, void *arg) {
    (void)arg; session *s = mg_get_user_connection_data(c);
    int op = bits & 15, final = bits & 128;
    if (atomic_load(&s->stop)) return 0;
    if (op == 8) { ws_close_code(s, 1000); return 0; }
    if (op == 9) { mg_websocket_write(c, MG_WEBSOCKET_OPCODE_PONG, data, n); return 1; }
    if (op == 10) return 1;
    if ((op != 0 && op != 1 && op != 2) || (op == 0 && !s->fragmented) || (op != 0 && s->fragmented)) return ws_close_code(s, 1002);
    if (op != 0) { s->message_kind = op; s->message_len = 0; }
    if (n > DISC_FRAME_MAX - s->message_len) return ws_close_code(s, 1009);
    memcpy(s->message + s->message_len, data, n); s->message_len += n;
    s->fragmented = !final;
    if (!final) return 1;
    if (s->message_kind == 1 && !disc_utf8(s->message, s->message_len)) return ws_close_code(s, 1007);
    /* Control frames carry the pairing token and the next mutation's request ID;
     * they are never forwarded and must arrive as whole text messages. */
    if (s->message_kind == 1 && s->message_len > 6 && !memcmp(s->message, "token:", 6)) {
        if (s->incoming.used || s->message_len - 6 > 64 ||
            !credential_matches(s->srv, mg_get_request_info(c)->remote_addr, (const char *)s->message + 6, s->message_len - 6))
            return ws_close_code(s, 1008);
        s->authenticated = 1; s->message_len = 0; return 1;
    }
    if (s->message_kind == 1 && s->message_len > 8 && !memcmp(s->message, "request:", 8)) {
        size_t n = s->message_len - 8;
        if (s->incoming.used || s->pending_request[0] || !valid_request_id((const char *)s->message + 8, n)) return ws_close_code(s, 1008);
        memcpy(s->pending_request, s->message + 8, n); s->pending_request[n] = 0; s->message_len = 0; return 1;
    }
    int rc = disc_feed(&s->incoming, s->message, s->message_len, to_tcp, s);
    s->message_len = 0;
    /* 1013 Try Again Later: a mutation arrived while stock scans the library. */
    if (rc) return ws_close_code(s, rc == -2 ? 1008 : rc == -3 ? 1011 : rc == -4 ? 1013 : 1007);
    return 1;
}
static int fetch_catalog(struct mg_connection *c, server *s) {
    const long long deadline = monotonic_ms() + DISC_HTTP_CLIENT_TIMEOUT_MS;
    char err[128];
    struct mg_connection *up = mg_connect_client(s->upstream, s->http_port, 0, err, sizeof(err));
    if (!up) return error(c, 502, "Stock HTTP unavailable\n");
    mg_printf(up, "GET /song_category_tree/ HTTP/1.1\r\nHost: %s:%d\r\nConnection: close\r\ntype: all/song\r\nstart-pos: 0\r\nnum-max: 20\r\n\r\n", s->upstream, s->http_port);
    if (mg_get_response(up, err, sizeof(err), 3000) < 0) { mg_close_connection(up); return error(c, 502, "Stock HTTP response unavailable\n"); }
    const struct mg_response_info *info = mg_get_response_info(up);
    const size_t limit = 262144;
    if (info->content_length > (long long)limit) { mg_close_connection(up); return error(c, 502, "Stock HTTP response too large\n"); }
    unsigned char *buf = malloc(limit + 1);
    if (!buf) { mg_close_connection(up); return error(c, 503, "Memory unavailable\n"); }
    size_t used = 0; int n;
    while ((n = mg_read(up, buf + used, limit + 1 - used)) > 0) { used += (size_t)n; if (used > limit) break; }
    int code = info->status_code;
    int bad = n < 0 || used > limit || monotonic_ms() >= deadline || (info->content_length >= 0 && (long long)used != info->content_length);
    mg_close_connection(up);
    int result = bad ? error(c, 502, "Incomplete or oversized stock HTTP body\n") : response(c, code, "application/json; charset=utf-8", buf, used);
    free(buf); return result;
}
/* Keep browser input out of stock paths and headers except these bounded numbers. */
static int decimal(const char *value, unsigned max, unsigned *out) {
    if (!value || !*value) return 0;
    unsigned n = 0;
    const unsigned char *p = (const unsigned char *)value;
    while (*p == ' ' || *p == '\t') p++;
    if (*p < '0' || *p > '9') return 0;
    for (; *p >= '0' && *p <= '9'; p++) {
        if (n > max / 10 ||
            (n == max / 10 && (unsigned)(*p - '0') > max % 10)) return 0;
        n = n * 10 + (*p - '0');
    }
    /* V2.57 pads Content-Length with spaces; permit HTTP OWS, not junk. */
    while (*p == ' ' || *p == '\t') p++;
    if (*p) return 0;
    *out = n; return 1;
}
static int page_header(struct mg_connection *c, const char *name, unsigned max, unsigned *out) {
    const char *v = mg_get_header(c, name);
    return header_count(c, name) <= 1 && (!v || decimal(v, max, out));
}
static int reply_header_count(const struct mg_response_info *r, const char *name) {
    int count = 0;
    for (int i = 0; i < r->num_headers; i++) if (!strcasecmp(r->http_headers[i].name, name)) count++;
    return count;
}
/* Forwards a validated stock reply to the browser within the remaining budget.
 * Returns the downstream status, or 0 when an error body was already sent. */
static int forward_stock_response(struct mg_connection *c, struct mg_connection *up, long long deadline, const char *const *metadata_names, size_t metadata_count) {
    const size_t limit = 4 * 1024 * 1024;
    char metadata[256] = "";
    /* Images keep their type (the current cover); everything else stays JSON. */
    const char *upstream_type = mg_get_header(up, "Content-Type");
    const char *type = upstream_type && !strncasecmp(upstream_type, "image/jpeg", 10) ? "image/jpeg"
                     : upstream_type && !strncasecmp(upstream_type, "image/png", 9) ? "image/png"
                     : "application/json; charset=utf-8";
    int code = 502;
    const struct mg_response_info *info = mg_get_response_info(up);
    const char *te = mg_get_header(up, "Transfer-Encoding"), *cl = mg_get_header(up, "Content-Length");
    const char *encoding = mg_get_header(up, "Content-Encoding");
    unsigned length = 0;
    if (info->status_code < 200 || info->status_code > 599 ||
        reply_header_count(info, "Content-Length") > 1 || reply_header_count(info, "Transfer-Encoding") > 1 ||
        reply_header_count(info, "Content-Encoding") > 1 || (encoding && strcasecmp(encoding, "identity")) ||
        (te && (cl || strcasecmp(te, "chunked"))) || (cl && !decimal(cl, (unsigned)limit, &length))) goto invalid;
    int no_body = info->status_code == 204 || info->status_code == 304;
    /* Close-delimited replies cannot distinguish successful EOF from truncation. */
    if ((!cl && !te && !no_body) || (no_body && (te || length))) goto invalid;
    for (size_t i = 0; i < metadata_count; i++) {
        const char *v = mg_get_header(up, metadata_names[i]);
        if (reply_header_count(info, metadata_names[i]) > 1) goto invalid;
        if (!v) continue;
        /* Forward only short printable values; numbers and category names. */
        size_t vlen = strlen(v);
        if (!vlen || vlen > 64) goto invalid;
        for (size_t j = 0; j < vlen; j++) if (v[j] < 0x20 || v[j] > 0x7e) goto invalid;
        size_t used = strlen(metadata);
        int n = snprintf(metadata + used, sizeof(metadata) - used, "%s: %s\r\n", metadata_names[i], v);
        if (n < 0 || (size_t)n >= sizeof(metadata) - used) goto invalid;
    }
    long long remaining = deadline - monotonic_ms();
    if (remaining <= 0) goto invalid;
    code = info->status_code;
    mg_disc_set_write_budget(c, (unsigned)remaining);
    if (mg_printf(c, "HTTP/1.1 %d %s\r\nContent-Type: %s\r\n"
                     "%sConnection: close\r\nCache-Control: no-store\r\n"
                     "X-Content-Type-Options: nosniff\r\nContent-Security-Policy: default-src 'none'; frame-ancestors 'none'\r\n%s%s\r\n",
                     code, mg_get_response_code_text(c, code), type, no_body ? "" : "Transfer-Encoding: chunked\r\n", metadata,
                     cors_headers(c)) <= 0 || no_body) goto done;
    char buffer[8192];
    size_t used = 0;
    int n;
    while ((n = mg_read(up, buffer, sizeof(buffer))) > 0) {
        if ((size_t)n > limit - used || monotonic_ms() >= deadline || stopping) goto done;
        used += (size_t)n;
        if (mg_send_chunk(c, buffer, (unsigned)n) < 0) goto done;
    }
    /* Only a complete upstream reply earns the downstream chunk terminator.
     * After headers, failure must close the response, never append an error. */
    if (n == 0 && monotonic_ms() < deadline && !stopping &&
        (info->content_length < 0 || (long long)used == info->content_length)) mg_send_chunk(c, "", 0);
done:
    return code;
invalid:
    return error(c, 502, "Invalid or unavailable stock HTTP response\n");
}
static const char *const CATALOG_METADATA[] = {"total-num", "mark-pos", "type"};
/* Content-Type is set from the upstream type above, never duplicated as metadata. */
static const char *const STOCK_METADATA[] = {"total-num", "mark-pos", "type", "is-exist"};
static int stream_catalog(struct mg_connection *c, server *s, unsigned start, unsigned count) {
    const long long deadline = monotonic_ms() + DISC_HTTP_CLIENT_TIMEOUT_MS;
    char err[128];
    struct mg_connection *up = mg_connect_client(s->upstream, s->http_port, 0, err, sizeof(err));
    if (!up) return error(c, 502, "Stock HTTP unavailable\n");
    int code;
    if (mg_printf(up, "GET /song_category_tree/ HTTP/1.1\r\nHost: %s:%d\r\nConnection: close\r\ntype: all/song\r\nstart-pos: %u\r\nnum-max: %u\r\n\r\n", s->upstream, s->http_port, start, count) <= 0 ||
        mg_get_response(up, err, sizeof(err), DISC_HTTP_CLIENT_TIMEOUT_MS) < 0)
        code = error(c, 502, "Invalid or unavailable stock HTTP response\n");
    else code = forward_stock_response(c, up, deadline, CATALOG_METADATA, 3);
    mg_close_connection(up); return code;
}
/* One reviewed stock HTTP request described by the card catalog. Only the
 * described request headers are forwarded; mutations carry token and request
 * ID guards and are paced per class like WebSocket mutations. */
static int stock_proxy(struct mg_connection *c, server *s, const disc_route *route, const char *method, const char *path,
                       const disc_header *headers, size_t count, const char *body, size_t body_len) {
    const long long deadline = monotonic_ms() + DISC_HTTP_CLIENT_TIMEOUT_MS;
    char err[128];
    /* CivetWeb decoded the path; stock expects it percent-encoded again
     * (reference quote(path, safe='/')), so spaces and '%' survive. */
    char encoded[2200];
    if (disc_media_encode_path(path, encoded, sizeof(encoded))) return error(c, 414, "Stock path too long\n");
    struct mg_connection *up = mg_connect_client(s->upstream, s->http_port, 0, err, sizeof(err));
    if (!up) return error(c, 502, "Stock HTTP unavailable\n");
    int ok = mg_printf(up, "%s %s HTTP/1.1\r\nHost: %s:%d\r\nConnection: close\r\n", method, encoded, s->upstream, s->http_port) > 0;
    for (size_t i = 0; ok && i < route->header_count; i++)
        for (size_t j = 0; j < count; j++)
            if (!strcasecmp(headers[j].name, route->headers[i].name)) { ok = mg_printf(up, "%s: %s\r\n", route->headers[i].name, headers[j].value) > 0; break; }
    if (ok && route->body == 1) ok = mg_printf(up, "Content-Type: application/json\r\nContent-Length: %zu\r\n", body_len) > 0;
    if (ok && route->body == 0) ok = mg_printf(up, "Content-Length: 0\r\n") > 0;
    if (ok) ok = mg_printf(up, "\r\n") > 0;
    if (ok && body_len) ok = mg_write(up, body, body_len) == (int)body_len;
    int code;
    if (!ok || mg_get_response(up, err, sizeof(err), DISC_HTTP_CLIENT_TIMEOUT_MS) < 0)
        code = error(c, 502, "Invalid or unavailable stock HTTP response\n");
    else code = forward_stock_response(c, up, deadline, STOCK_METADATA, 4);
    mg_close_connection(up); return code;
}
static int pace_class(server *srv, const char *class, unsigned pacing_ms) {
    pthread_mutex_lock(&srv->lock);
    disc_pace *slot = NULL;
    for (int i = 0; i < DISC_PACING_CLASSES; i++) {
        if (!strcmp(srv->http_pacing[i].class, class)) { slot = &srv->http_pacing[i]; break; }
        if (!slot && !srv->http_pacing[i].class[0]) slot = &srv->http_pacing[i];
    }
    if (!slot) { pthread_mutex_unlock(&srv->lock); return 0; }
    if (!slot->class[0]) snprintf(slot->class, sizeof(slot->class), "%s", class);
    long long now = monotonic_ms(), wait = slot->last_ms + pacing_ms - now;
    slot->last_ms = (slot->last_ms && wait > 0 && wait <= 10000) ? slot->last_ms + pacing_ms : now;
    pthread_mutex_unlock(&srv->lock);
    if (slot->last_ms > now) {
        long long delay_ms = slot->last_ms - now;
        struct timespec delay = {.tv_sec = delay_ms / 1000, .tv_nsec = (delay_ms % 1000) * 1000000};
        nanosleep(&delay, NULL);
    }
    return 1;
}
/* Upload names the service admits whatever the card catalog says: music,
 * same-stem lyrics and cover images only, and no hidden component, so nothing
 * reaches the service's own .disc folder (page releases, database, switches).
 * FAT folds case, so the extension check does too. */
static const char *const UPLOAD_EXTENSIONS[] = {
    "flac", "wav", "mp3", "m4a", "aac", "ogg", "opus", "ape", "wv", "wma",
    "dsf", "dff", "aif", "aiff", "lrc", "jpg", "jpeg", "png"};
static int upload_name_admitted(const char *rel) {
    const char *part = rel, *last = rel;
    for (;;) {
        const char *slash = strchr(part, '/');
        size_t n = slash ? (size_t)(slash - part) : strlen(part);
        if (!n || part[0] == '.') return 0;
        last = part;
        if (!slash) break;
        part = slash + 1;
    }
    const char *dot = strrchr(last, '.');
    if (!dot || dot == last || strchr(dot, '/')) return 0;
    for (size_t i = 0; i < sizeof(UPLOAD_EXTENSIONS) / sizeof(UPLOAD_EXTENSIONS[0]); i++)
        if (!strcasecmp(dot + 1, UPLOAD_EXTENSIONS[i])) return 1;
    return 0;
}
/* Music upload: the gateway writes the file itself on the owned card under the
 * stock path (stock's own handler only writes bytes; indexing is the scan).
 * Exclusive create, streamed to a temporary name, fsync, rename; never overwrite. */
static const char *const REPLACEABLE[] = {"lrc", "jpg", "jpeg", "png"};
static int upload_request(struct mg_connection *c, server *s, const char *path, size_t declared) {
    const char *card = s->webroot.mount ? s->webroot.mount : "/tmp/sdcard";
    const char *root = s->upload_root ? s->upload_root : s->webroot.mount;
    size_t plen = strlen(card);
    if (!root) return error(c, 501, "No upload root configured\n");
    if (strncmp(path, "/audio", 6) || strncmp(path + 6, card, plen) || path[6 + plen] != '/' || !path[7 + plen])
        return error(c, 403, "Upload path must be below the music card\n");
    if (!upload_name_admitted(path + 7 + plen)) return error(c, 403, "Only music, lyrics and cover names can be uploaded\n");
    /* X-Disc-Replace: trash (combined-008) lets a cover or lyrics upload replace
     * the existing file, which goes to the trash first; audio is never replaced. */
    const char *replace = mg_get_header(c, "X-Disc-Replace");
    int replaceable = 0;
    if (replace) {
        const char *dot = strrchr(path, '.');
        for (size_t i = 0; dot && i < sizeof(REPLACEABLE) / sizeof(REPLACEABLE[0]); i++)
            if (!strcasecmp(dot + 1, REPLACEABLE[i])) replaceable = 1;
        if (header_count(c, "X-Disc-Replace") != 1 || strcmp(replace, "trash") || !s->trash_dir)
            return error(c, 400, "X-Disc-Replace takes \"trash\", with a trash configured\n");
        if (!replaceable) return error(c, 400, "Only covers and lyrics are replaced; audio never\n");
    }
    if (s->webroot.mount && !disc_webroot_available(&s->webroot)) return error(c, 503, "Card is not owned by the player\n");
    pthread_mutex_lock(&s->lock);
    if (s->upload_active) { pthread_mutex_unlock(&s->lock); return error(c, 503, "Another upload is active\n"); }
    s->upload_active = 1; pthread_mutex_unlock(&s->lock);
    int code = 500, dir = open(root, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC), fd = -1;
    char temp[64] = "", name[256] = "";
    const char *rel = path + 7 + plen;
    if (dir < 0) { code = error(c, 503, "Upload root unavailable\n"); goto out; }
    /* Walk and create parents; components were already checked by the catalog pattern. */
    for (;;) {
        const char *slash = strchr(rel, '/');
        size_t n = slash ? (size_t)(slash - rel) : strlen(rel);
        if (!n || n >= sizeof(name)) { code = error(c, 403, "Invalid upload path\n"); goto out; }
        memcpy(name, rel, n); name[n] = 0;
        if (!slash) break;
        if (mkdirat(dir, name, 0755) && errno != EEXIST) { code = error(c, 503, "Cannot create folder\n"); goto out; }
        int next = openat(dir, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        close(dir); dir = next;
        if (dir < 0) { code = error(c, 503, "Cannot open folder\n"); goto out; }
        rel = slash + 1;
    }
    struct stat st;
    int replaced = 0;
    if (!fstatat(dir, name, &st, AT_SYMLINK_NOFOLLOW) && (!replaceable || !S_ISREG(st.st_mode))) {
        code = error(c, 409, "File already exists; no overwrite\n"); goto out;
    }
    struct statvfs space;
    if (fstatvfs(dir, &space) || (unsigned long long)space.f_bavail * space.f_frsize < (unsigned long long)declared + (1u << 20)) {
        code = error(c, 507, "Not enough free space on the card\n"); goto out;
    }
    unsigned h = 2166136261u;
    for (const char *p = path; *p; p++) h = (h ^ (unsigned char)*p) * 16777619u;
    snprintf(temp, sizeof(temp), ".disc-upload-%08x-%lld.part", h, monotonic_ms());
    fd = openat(dir, temp, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0644);
    if (fd < 0) { code = error(c, 503, "Cannot stage upload\n"); goto out; }
    char buffer[65536]; size_t received = 0;
    while (received < declared && !stopping) {
        size_t want = declared - received < sizeof(buffer) ? declared - received : sizeof(buffer);
        int n = mg_read(c, buffer, want);
        if (n <= 0) break;
        for (size_t written = 0; written < (size_t)n;) {
            ssize_t w = write(fd, buffer + written, (size_t)n - written);
            if (w < 0 && errno == EINTR) continue;
            if (w <= 0) { n = -1; break; }
            written += (size_t)w;
        }
        if (n < 0) break;
        received += (size_t)n;
    }
    if (received != declared || fsync(fd) || fstat(fd, &st) || (size_t)st.st_size != declared) {
        code = error(c, 400, "Incomplete upload; nothing was published\n"); goto out;
    }
    close(fd); fd = -1;
    if (!fstatat(dir, name, &st, AT_SYMLINK_NOFOLLOW)) {
        if (!replaceable) { code = error(c, 409, "File appeared during upload; no overwrite\n"); goto out; }
        char target[1200], *moved = NULL;
        size_t moved_length = 0;
        const char *problem = NULL;
        snprintf(target, sizeof(target), "%s/%s", s->trash.card, path + 7 + plen);
        int status = disc_trash_move(&s->trash, target, (long long)time(NULL), &moved, &moved_length, &problem);
        free(moved);
        if (status != 200) {
            char message[160];
            snprintf(message, sizeof(message), "The existing file could not go to the trash (%s); nothing was published\n", problem ? problem : "unavailable");
            code = error(c, status == 503 ? 503 : 409, message); goto out;
        }
        replaced = 1;
    }
    if (renameat(dir, temp, dir, name)) { code = error(c, 503, "Cannot publish upload\n"); goto out; }
    temp[0] = 0; fsync(dir);
    char json[1200]; int n = snprintf(json, sizeof(json), "{\"path\":\"%s\",\"bytes\":%zu,\"indexed\":false,\"replaced\":%s}", path + 6, declared,
                                      replaced ? "true" : "false");
    code = n > 0 && (size_t)n < sizeof(json) ? response(c, 201, "application/json; charset=utf-8", json, (size_t)n) : error(c, 500, "Reply too long\n");
out:
    if (fd >= 0) close(fd);
    if (temp[0] && dir >= 0) unlinkat(dir, temp, 0);
    if (dir >= 0) close(dir);
    pthread_mutex_lock(&s->lock); s->upload_active = 0; pthread_mutex_unlock(&s->lock);
    return code;
}
/* GET /api/data/<query>?<param>=<value>: one reviewed read-only query from the
 * card's queries.json against the stock databases. Parameters travel in the
 * query string (never forwarded anywhere), are validated by the catalog, and
 * the JSON result is bounded. */
static int data_request(struct mg_connection *c, server *s, const char *uri) {
    const struct mg_request_info *request = mg_get_request_info(c);
    if (strcmp(request->request_method, "GET")) return error(c, 405, "Data queries are GET only\n");
    if (!s->data_root) return error(c, 501, "No data root configured\n");
    const char *name = uri + 10; /* after "/api/data/" */
    if (!*name || strlen(name) > 40 || strchr(name, '/')) return error(c, 404, "Unknown query\n");
    catalog_cache *cache = load_card_file(s, CARD_QUERIES);
    if (!cache) return error(c, 403, "No reviewed query catalog for this card\n");
    const disc_query *query = disc_queries_find(&cache->queries, name);
    int code;
    if (!query) { code = error(c, 404, "Unknown query\n"); goto out; }
    char decoded[DISC_QUERY_PARAMS][256];
    const char *values[DISC_QUERY_PARAMS] = {0};
    const char *qs = request->query_string ? request->query_string : "";
    if (strlen(qs) > 2048) { code = error(c, 400, "Query string too long\n"); goto out; }
    for (size_t i = 0; i < query->param_count; i++) {
        int n = mg_get_var(qs, strlen(qs), query->params[i].name, decoded[i], sizeof(decoded[i]));
        if (n < 0) { if (n == -2) { code = error(c, 400, "Parameter too long\n"); goto out; } continue; }
        values[i] = decoded[i];
    }
    char *json = NULL; size_t length = 0;
    int status = disc_data_execute(&cache->queries, query, s->data_root, &s->database, values, &json, &length);
    if (status != 200) {
        code = error(c, status, status == 400 ? "Invalid query parameters\n" : status == 503 ? "Database busy or unavailable\n"
                              : status == 409 ? "A table the query reads does not exist now\n" : "Query failed\n");
        goto out;
    }
    code = response(c, 200, "application/json; charset=utf-8", json, length);
    free(json);
out:
    drop_catalog(s, cache);
    return code;
}
/* Media reads (plan: next image): metadata, covers and lyrics of music files
 * on the card, read-only and bounded. Not forwarded to stock; the card path is
 * confined by disc_media_open (audio extensions only, no links, no dot parts). */
#define DISC_MEDIA_BUDGET_MS 10000
static int media_head_status(struct mg_connection *c, int status, const char *type, size_t length, const char *cache, const char *extra) {
    mg_disc_set_write_budget(c, DISC_MEDIA_BUDGET_MS);
    return mg_printf(c, "HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %zu\r\nConnection: close\r\n"
                        "Cache-Control: %s\r\nX-Content-Type-Options: nosniff\r\n"
                        "Content-Security-Policy: default-src 'none'; frame-ancestors 'none'\r\n%s%s\r\n",
                     status, mg_get_response_code_text(c, status), type, length, cache, extra, cors_headers(c)) > 0;
}
static int media_head(struct mg_connection *c, const char *type, size_t length, const char *cache, const char *extra) {
    return media_head_status(c, 200, type, length, cache, extra);
}
/* A file without a cover or lyrics is an ordinary answer, not an error: 204. */
static int no_content(struct mg_connection *c) {
    return mg_printf(c, "HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\nCache-Control: no-store\r\n"
                        "X-Content-Type-Options: nosniff\r\n%s\r\n", cors_headers(c)) > 0 ? 204 : 500;
}
static int media_stream_status(struct mg_connection *c, int status, int fd, long long offset, size_t length, const char *type,
                               const char *cache, const char *extra) {
    disc_stream stream;
    stream_start(&stream);
    if (!media_head_status(c, status, type, length, cache, extra)) return 500;
    char buffer[8192];
    size_t done = 0;
    while (done < length && !stopping) {
        size_t want = length - done < sizeof(buffer) ? length - done : sizeof(buffer);
        ssize_t n = pread(fd, buffer, want, (off_t)(offset + (long long)done));
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0 || !write_all(c, buffer, (size_t)n, &stream)) break;
        done += (size_t)n;
    }
    return done == length ? status : 500; /* A partial body is closed, never completed with an error. */
}
static int media_stream(struct mg_connection *c, int fd, long long offset, size_t length, const char *type, const char *extra) {
    return media_stream_status(c, 200, fd, offset, length, type, "private, max-age=3600", extra);
}
/* One byte range of a file from a Range header: 1 with *from and *length set,
 * 0 when there is none to honour (the whole file is served), -1 when it cannot
 * be satisfied (past the end, or several ranges, which a media element never asks). */
static int byte_range(const char *header, long long size, long long *from, long long *length) {
    if (!header || strncmp(header, "bytes=", 6)) return 0;
    const char *p = header + 6;
    char *end;
    if (strchr(p, ',')) return -1;
    if (*p == '-') {
        if (!isdigit((unsigned char)p[1])) return 0;
        long long n = strtoll(p + 1, &end, 10);
        if (*end) return 0;
        if (n <= 0 || size <= 0) return -1;
        if (n > size) n = size;
        *from = size - n;
        *length = n;
        return 1;
    }
    if (!isdigit((unsigned char)*p)) return 0;
    long long first = strtoll(p, &end, 10), last = size - 1;
    if (*end != '-') return 0;
    const char *q = end + 1;
    if (*q) {
        if (!isdigit((unsigned char)*q)) return 0;
        last = strtoll(q, &end, 10);
        if (*end || last < first) return 0;
        if (last >= size) last = size - 1;
    }
    if (first >= size) return -1;
    *from = first;
    *length = last - first + 1;
    return 1;
}
/* A media type a browser can recognise, by extension. */
static const char *audio_type(const char *name) {
    static const char *const types[][2] = {
        {"flac", "audio/flac"}, {"mp3", "audio/mpeg"}, {"wav", "audio/wav"}, {"m4a", "audio/mp4"}, {"alac", "audio/mp4"},
        {"aac", "audio/aac"}, {"ogg", "audio/ogg"}, {"opus", "audio/ogg"}, {"aif", "audio/aiff"}, {"aiff", "audio/aiff"},
        {"ape", "audio/x-ape"}, {"wv", "audio/x-wavpack"}, {"wma", "audio/x-ms-wma"}, {"dsf", "audio/x-dsf"}, {"dff", "audio/x-dff"}};
    const char *dot = strrchr(name, '.');
    for (size_t i = 0; dot && i < sizeof(types) / sizeof(types[0]); i++) if (!strcasecmp(dot + 1, types[i][0])) return types[i][1];
    return "application/octet-stream";
}
/* Lyrics bytes: UTF-8 is labelled so; anything else is left for the client to
 * decode. Embedded ID3 lyrics (a blob with its text encoding) become UTF-8 first. */
static int lyrics_reply(struct mg_connection *c, int fd, long long offset, size_t length, const char *extra,
                        const disc_media_blob *blob) {
    char *text = malloc(length ? length : 1);
    if (!text) return error(c, 503, "Memory unavailable\n");
    size_t used = 0;
    while (used < length) {
        ssize_t n = pread(fd, text + used, length - used, (off_t)(offset + (long long)used));
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        used += (size_t)n;
    }
    int code;
    size_t converted_length = 0;
    char *converted = used == length && blob ? disc_media_lyrics_utf8(blob, (const unsigned char *)text, length, &converted_length) : NULL;
    if (converted) { free(text); text = converted; length = converted_length; }
    if (used != length && !converted) code = error(c, 503, "Lyrics unavailable\n");
    else {
        const char *type = disc_utf8((const unsigned char *)text, length) ? "text/plain; charset=utf-8" : "application/octet-stream";
        code = media_head(c, type, length, "no-store", extra) && mg_write(c, text, length) == (int)length ? 200 : 500;
    }
    free(text);
    return code;
}
/* The lyrics stock prepared for the current track (a same-stem .lrc or embedded
 * text, converted to UTF-8). The file names no track, so its age is reported and
 * the client compares it with the track change it observed. */
static int current_lyrics(struct mg_connection *c, server *s) {
    if (!s->current_lyrics) return error(c, 404, "No current lyrics source configured\n");
    int fd = open(s->current_lyrics, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return no_content(c);
    struct stat st;
    int code;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || !st.st_size || st.st_size > (off_t)DISC_MEDIA_MAX_LYRICS)
        code = no_content(c);
    else {
        long long age = (long long)time(NULL) - (long long)st.st_mtime;
        char extra[96];
        snprintf(extra, sizeof(extra), "X-Lyrics-Source: player\r\nX-Lyrics-Age: %lld\r\n", age < 0 ? 0 : age);
        code = lyrics_reply(c, fd, 0, (size_t)st.st_size, extra, NULL);
    }
    close(fd);
    return code;
}
static const char *media_root(const server *s) { return s->webroot.mount ? s->webroot.mount : s->upload_root; }
static int media_request(struct mg_connection *c, server *s, const char *uri) {
    const struct mg_request_info *r = mg_get_request_info(c);
    const char *cl = mg_get_header(c, "Content-Length");
    if (strcmp(r->request_method, "GET") || r->query_string || (cl && strcmp(cl, "0")) || mg_get_header(c, "Transfer-Encoding"))
        return error(c, 405, "Media routes are bodyless GET without a query\n");
    if (!strcmp(uri, "/api/media/current-lyrics")) return current_lyrics(c, s);
    const char *root = media_root(s);
    if (!root) return error(c, 501, "No music card configured\n");
    int kind;
    const char *path;
    if (!strncmp(uri, "/api/media/info/", 16)) { kind = 0; path = uri + 15; }
    else if (!strncmp(uri, "/api/media/cover/", 17)) { kind = 1; path = uri + 16; }
    else if (!strncmp(uri, "/api/media/lyrics/", 18)) { kind = 2; path = uri + 17; }
    else if (!strncmp(uri, "/api/media/audio/", 17)) { kind = 3; path = uri + 16; }
    else return error(c, 404, "Unknown media route\n");
    if (!disc_utf8((const unsigned char *)path, strlen(path))) return error(c, 400, "Media path must be UTF-8\n");
    /* The audio itself never comes from a hidden folder (the service's .disc among them). */
    if (kind == 3 && strstr(path, "/.")) return error(c, 403, "Media path not admitted\n");
    if (s->webroot.mount && !disc_webroot_available(&s->webroot)) return error(c, 503, "Card is not owned by the player\n");
    /* Audio streams last as long as the browser reads; they have slots of their own. */
    int *slots = kind == 3 ? &s->audio_active : &s->media_active;
    pthread_mutex_lock(&s->lock);
    if (*slots >= 2) { pthread_mutex_unlock(&s->lock); return error(c, 503, kind == 3 ? "Audio streams busy\n" : "Media reads busy\n"); }
    (*slots)++;
    pthread_mutex_unlock(&s->lock);
    int dir = -1, fd = -1, other = -1, code;
    char name[256];
    int opened = disc_media_open(root, path, &dir, &fd, name, sizeof(name));
    if (opened) {
        code = error(c, opened, opened == 404 ? "No such music file\n" : opened == 503 ? "Card unavailable\n" : "Media path not admitted\n");
        goto out;
    }
    disc_media_info info;
    disc_media_blob folder = {0};
    size_t size = 0;
    if (kind == 3) {
        struct stat st;
        long long from = 0, length = 0;
        int range = fstat(fd, &st) ? -2 : byte_range(mg_get_header(c, "Range"), (long long)st.st_size, &from, &length);
        char extra[160];
        if (range == -2) code = error(c, 503, "Card unavailable\n");
        else if (range < 0) {
            snprintf(extra, sizeof(extra), "Accept-Ranges: bytes\r\nContent-Range: bytes */%lld\r\n", (long long)st.st_size);
            code = media_head_status(c, 416, "text/plain; charset=utf-8", 0, "no-store", extra) ? 416 : 500;
        } else {
            if (!range) { from = 0; length = (long long)st.st_size; snprintf(extra, sizeof(extra), "Accept-Ranges: bytes\r\n"); }
            else snprintf(extra, sizeof(extra), "Accept-Ranges: bytes\r\nContent-Range: bytes %lld-%lld/%lld\r\n", from, from + length - 1,
                          (long long)st.st_size);
            code = media_stream_status(c, range ? 206 : 200, fd, from, (size_t)length, audio_type(name), "no-store", extra);
        }
        goto out;
    }
    disc_media_probe(fd, name, &info);
    if (kind == 0) {
        int has_folder = !info.picture.present && disc_media_folder_cover(dir, &other, &folder);
        if (other >= 0) { close(other); other = -1; }
        int sidecar = disc_media_sidecar_lyrics(dir, name, &other, &size);
        char json[4096];
        size_t n = disc_media_json(&info, path, has_folder, sidecar, json, sizeof(json));
        code = !n ? error(c, 500, "Media document too large\n")
             : media_head(c, "application/json; charset=utf-8", n, "no-store", "") && mg_write(c, json, n) == (int)n ? 200 : 500;
    } else if (kind == 1) {
        if (info.picture.present)
            code = media_stream(c, fd, info.picture.offset, info.picture.length, info.picture.mime, "X-Cover-Source: embedded\r\n");
        else if (disc_media_folder_cover(dir, &other, &folder))
            code = media_stream(c, other, 0, folder.length, folder.mime, "X-Cover-Source: folder\r\n");
        else code = no_content(c);
    } else {
        if (disc_media_sidecar_lyrics(dir, name, &other, &size))
            code = lyrics_reply(c, other, 0, size, "X-Lyrics-Source: sidecar\r\n", NULL);
        else if (info.lyrics.present)
            code = lyrics_reply(c, fd, info.lyrics.offset, info.lyrics.length, "X-Lyrics-Source: embedded\r\n", &info.lyrics);
        else code = no_content(c);
    }
out:
    if (other >= 0) close(other);
    if (fd >= 0) close(fd);
    if (dir >= 0) close(dir);
    pthread_mutex_lock(&s->lock); (*slots)--; pthread_mutex_unlock(&s->lock);
    return code;
}
/* GET /api/device: live battery, card space and the playback stream's format,
 * each null when its source is not configured or not readable. */
static int device_facts(struct mg_connection *c, server *s) {
    disc_battery battery;
    disc_output output;
    disc_battery_read(s->battery_dir, &battery);
    disc_output_read(s->asound_dir, &output);
    const char *root = media_root(s);
    struct statvfs space;
    int has_card = root && (!s->webroot.mount || disc_webroot_available(&s->webroot)) && !statvfs(root, &space);
    char json[512];
    size_t n = disc_facts_json(&battery, has_card, has_card ? (unsigned long long)space.f_blocks * space.f_frsize : 0,
                               has_card ? (unsigned long long)space.f_bavail * space.f_frsize : 0, &output, json, sizeof(json));
    return n ? response(c, 200, "application/json; charset=utf-8", json, n) : error(c, 500, "Device facts too large\n");
}
static int card_owned(void *arg) {
    server *s = arg;
    return !s->webroot.mount || disc_webroot_available(&s->webroot);
}
/* GET /api/history: the newest plays the observer recorded on the card. */
static int history_route(struct mg_connection *c, server *s) {
    /* The database alone: a page's own plays are there without the observer (combined-009). */
    if (!s->database_file) return error(c, 404, "No play history configured\n");
    char *json = NULL;
    size_t length = 0;
    int status = disc_history_json(&s->database, &json, &length);
    if (status != 200) return error(c, status, "History unavailable\n");
    int code = response(c, 200, "application/json; charset=utf-8", json, length);
    free(json);
    return code;
}
/* POST /api/history (combined-009): a play in a page that played a card file
 * itself; the observer only sees the player's files. The usual guards; no
 * scan wait, as nothing reaches stock. */
#define DISC_HISTORY_PACING_MS 250
#define DISC_HISTORY_MAX_BODY 4096
static int history_add_route(struct mg_connection *c, server *s) {
    const struct mg_request_info *r = mg_get_request_info(c);
    const char *cl = mg_get_header(c, "Content-Length");
    if (!s->database_file || !media_root(s)) return error(c, 404, "No play history configured\n");
    if (r->query_string) return error(c, 405, "The history takes no query\n");
    unsigned length = 0;
    if (mg_get_header(c, "Transfer-Encoding") || header_count(c, "Content-Length") != 1 || !decimal(cl, 0x7fffffff, &length) || !length)
        return error(c, 411, "A play body with its Content-Length is required\n");
    if (length > DISC_HISTORY_MAX_BODY) return error(c, 413, "Body too large\n");
    const char *token = mg_get_header(c, "X-Disc-Token"), *request_id = mg_get_header(c, "X-Disc-Request");
    if (header_count(c, "X-Disc-Token") != 1 || !token || strlen(token) > 64 ||
        !credential_matches(s, r->remote_addr, token, strlen(token))) return error(c, 403, "Token required\n");
    if (header_count(c, "X-Disc-Request") != 1 || !request_id || !valid_request_id(request_id, strlen(request_id)))
        return error(c, 403, "Request ID required\n");
    if (!remember_request(s, request_id)) return error(c, 409, "Request ID already used\n");
    pace_class(s, "history", DISC_HISTORY_PACING_MS);
    if (s->webroot.mount && !disc_webroot_available(&s->webroot)) return error(c, 503, "Card is not owned by the player\n");
    char *body = malloc(length + 1), *out = NULL;
    size_t used = 0, n = 0;
    while (body && used < length) { int got = mg_read(c, body + used, length - used); if (got <= 0) break; used += (size_t)got; }
    if (!body || used != length) { free(body); return error(c, 400, "Incomplete body\n"); }
    body[length] = 0;
    const char *problem = NULL;
    int status = disc_history_add_json(&s->database, media_root(s), body, length, (long long)time(NULL), &out, &n, &problem);
    free(body);
    if (out) {
        int code = response(c, status, "application/json; charset=utf-8", out, n);
        free(out);
        return code;
    }
    char message[200];
    snprintf(message, sizeof(message), "%s\n", problem ? problem : "History unavailable");
    return error(c, status, message);
}
/* POST /api/favorites/<SONG.ID> (next image): favorite any library track with the
 * player screen's own statement. Stock's remote protocol can only favorite the
 * playing track; the card catalog must admit "favorite_add", and the request
 * carries the usual mutation guards. Confirmation is the MY_LOVE row read back. */
static int favorite_route(struct mg_connection *c, server *s, const char *uri) {
    const struct mg_request_info *r = mg_get_request_info(c);
    const char *cl = mg_get_header(c, "Content-Length");
    if (strcmp(r->request_method, "POST") || r->query_string || (cl && strcmp(cl, "0")) || mg_get_header(c, "Transfer-Encoding"))
        return error(c, 405, "Favorites take a bodyless POST without a query\n");
    unsigned id = 0;
    if (!decimal(uri + 15, 2147483647, &id) || !id || strchr(uri + 15, ' ')) return error(c, 404, "Unknown song\n");
    if (!s->data_root) return error(c, 501, "No data root configured\n");
    catalog_cache *cache = load_catalog(s);
    const disc_data_mutation *admitted = cache ? disc_catalog_data(&cache->catalog, "favorite_add") : NULL;
    int code;
    const char *token = mg_get_header(c, "X-Disc-Token"), *request_id = mg_get_header(c, "X-Disc-Request");
    if (!admitted) { code = error(c, 403, "Favorites for any track are not admitted by the card catalog\n"); goto out; }
    if (header_count(c, "X-Disc-Token") != 1 || !token || strlen(token) > 64 ||
        !credential_matches(s, r->remote_addr, token, strlen(token))) { code = error(c, 403, "Token required\n"); goto out; }
    if (header_count(c, "X-Disc-Request") != 1 || !request_id || !valid_request_id(request_id, strlen(request_id))) {
        code = error(c, 403, "Request ID required\n"); goto out;
    }
    if (scan_active(s)) { code = error(c, 503, "Library scan in progress; nothing was written\n"); goto out; }
    if (!remember_request(s, request_id)) { code = error(c, 409, "Request ID already used\n"); goto out; }
    pace_class(s, admitted->class, admitted->pacing_ms);
    long long love = 0;
    int already = 0, status = disc_data_favorite_add(s->data_root, id, &love, &already);
    if (status == 404) code = error(c, 404, "No such song in the library\n");
    else if (status == 503) code = error(c, 503, "Library busy; nothing was written\n");
    else if (status == 409) code = error(c, 409, "The file is a favorite in a list's or a folder's context, which stock keeps in its place\n");
    else if (status != 200) code = error(c, 500, "Favorite not confirmed; not retried\n");
    else {
        char json[160];
        int n = snprintf(json, sizeof(json), "{\"songId\":%u,\"favorite\":true,\"loveId\":%lld,\"already\":%s}", id, love, already ? "true" : "false");
        code = response(c, 200, "application/json; charset=utf-8", json, (size_t)n);
    }
out:
    drop_catalog(s, cache);
    return code;
}
/* /api/trash[/<id>[/restore]] (combined-008): GET lists the trash, POST with
 * {"path"} moves a file or folder of the card there, POST <id>/restore puts
 * it back, DELETE <id> purges one entry and DELETE empties the trash. Changes
 * carry the usual guards, wait for a library scan to end and exclude uploads. */
#define DISC_TRASH_PACING_MS 500
#define DISC_TRASH_MAX_BODY 4096
static int trash_route(struct mg_connection *c, server *s, const char *uri) {
    const struct mg_request_info *r = mg_get_request_info(c);
    const char *method = r->request_method, *rest = uri + 10, *cl = mg_get_header(c, "Content-Length");
    if (!s->trash_dir) return error(c, 404, "No trash configured\n");
    if (r->query_string) return error(c, 405, "The trash takes no query\n");
    long long id = 0;
    int restore = 0;
    if (*rest) {
        const char *p = rest + 1;
        if (rest[0] != '/' || *p < '1' || *p > '9') return error(c, 404, "No such entry in the trash\n");
        while (*p >= '0' && *p <= '9' && id < 1000000000000LL) id = id * 10 + (*p++ - '0');
        if (!strcmp(p, "/restore")) restore = 1;
        else if (*p) return error(c, 404, "No such entry in the trash\n");
    }
    int list = !*rest && !strcmp(method, "GET"), move = !*rest && !strcmp(method, "POST"), empty = !*rest && !strcmp(method, "DELETE");
    int purge = *rest && !restore && !strcmp(method, "DELETE"), back = restore && !strcmp(method, "POST");
    if (!(list || move || empty || purge || back)) return error(c, 405, "Method not allowed for this trash operation\n");
    unsigned length = 0;
    if (mg_get_header(c, "Transfer-Encoding") || header_count(c, "Content-Length") > 1 || (cl && !decimal(cl, 0x7fffffff, &length)))
        return error(c, 400, "One plain Content-Length, no transfer encoding\n");
    if (!move && length) return error(c, 400, "This trash operation takes no body\n");
    if (move && !length) return error(c, 411, "A body {\"path\":...} with its Content-Length is required\n");
    if (length > DISC_TRASH_MAX_BODY) return error(c, 413, "Body too large\n");
    char *out = NULL, *body = NULL;
    size_t n = 0;
    const char *problem = NULL;
    int status;
    if (list) {
        status = disc_trash_list(&s->trash, &out, &n, &problem);
        goto reply;
    }
    const char *token = mg_get_header(c, "X-Disc-Token"), *request_id = mg_get_header(c, "X-Disc-Request");
    if (header_count(c, "X-Disc-Token") != 1 || !token || strlen(token) > 64 ||
        !credential_matches(s, r->remote_addr, token, strlen(token))) return error(c, 403, "Token required\n");
    if (header_count(c, "X-Disc-Request") != 1 || !request_id || !valid_request_id(request_id, strlen(request_id)))
        return error(c, 403, "Request ID required\n");
    if (scan_active(s)) return error(c, 503, "Library scan in progress; nothing was moved\n");
    if (!remember_request(s, request_id)) return error(c, 409, "Request ID already used\n");
    pace_class(s, "trash", DISC_TRASH_PACING_MS);
    char path[1100] = "";
    if (move) {
        body = malloc(length + 1);
        size_t used = 0;
        while (body && used < length) { int got = mg_read(c, body + used, length - used); if (got <= 0) break; used += (size_t)got; }
        jsmntok_t *t = NULL;
        int i;
        int parsed = body && used == length && disc_json_parse(body, length, &t, 64) > 0 && t[0].size == 1 &&
                     (i = disc_json_find(body, t, 0, "path")) > 0 && disc_json_string(body, &t[i], path, sizeof(path)) > 0;
        free(t);
        free(body);
        if (!parsed) return error(c, 400, "The body is {\"path\":\"<a file or folder on the card>\"}\n");
    }
    if (s->webroot.mount && !disc_webroot_available(&s->webroot)) return error(c, 503, "Card is not owned by the player\n");
    pthread_mutex_lock(&s->lock);
    if (s->upload_active) { pthread_mutex_unlock(&s->lock); return error(c, 503, "Another card operation is active\n"); }
    s->upload_active = 1; pthread_mutex_unlock(&s->lock);
    if (move) status = disc_trash_move(&s->trash, path, (long long)time(NULL), &out, &n, &problem);
    else if (back) status = disc_trash_restore(&s->trash, id, &out, &n, &problem);
    else if (purge) status = disc_trash_purge(&s->trash, id, &out, &n, &problem);
    else status = disc_trash_empty(&s->trash, &out, &n, &problem);
    pthread_mutex_lock(&s->lock); s->upload_active = 0; pthread_mutex_unlock(&s->lock);
reply:
    if (status == 200 && out) {
        int code = response(c, 200, "application/json; charset=utf-8", out, n);
        free(out);
        return code;
    }
    free(out);
    char message[200];
    snprintf(message, sizeof(message), "%s\n", problem ? problem : "Trash unavailable");
    return error(c, status == 200 ? 503 : status, message);
}
/* GET /api/card/leftovers reports what macOS left on the card; POST
 * /api/card/leftovers/trash moves all of it into one trash entry (combined-008),
 * under the trash's guards. */
static int leftovers_route(struct mg_connection *c, server *s, const char *uri) {
    const struct mg_request_info *r = mg_get_request_info(c);
    const char *cl = mg_get_header(c, "Content-Length");
    int report = !strcmp(uri, "/api/card/leftovers"), move = !strcmp(uri, "/api/card/leftovers/trash");
    if (!report && !move) return error(c, 404, "Not found\n");
    if (!s->trash_dir) return error(c, 404, "No trash configured\n");
    if (strcmp(r->request_method, report ? "GET" : "POST") || r->query_string || (cl && strcmp(cl, "0")) || mg_get_header(c, "Transfer-Encoding"))
        return error(c, 405, report ? "The report is a bodyless GET without a query\n" : "The move is a bodyless POST without a query\n");
    if (s->webroot.mount && !disc_webroot_available(&s->webroot)) return error(c, 503, "Card is not owned by the player\n");
    char *out = NULL;
    size_t n = 0;
    const char *problem = NULL;
    int status;
    if (move) {
        const char *token = mg_get_header(c, "X-Disc-Token"), *request_id = mg_get_header(c, "X-Disc-Request");
        if (header_count(c, "X-Disc-Token") != 1 || !token || strlen(token) > 64 ||
            !credential_matches(s, r->remote_addr, token, strlen(token))) return error(c, 403, "Token required\n");
        if (header_count(c, "X-Disc-Request") != 1 || !request_id || !valid_request_id(request_id, strlen(request_id)))
            return error(c, 403, "Request ID required\n");
        if (scan_active(s)) return error(c, 503, "Library scan in progress; nothing was moved\n");
        if (!remember_request(s, request_id)) return error(c, 409, "Request ID already used\n");
        pace_class(s, "trash", DISC_TRASH_PACING_MS);
    }
    pthread_mutex_lock(&s->lock);
    if (s->upload_active) { pthread_mutex_unlock(&s->lock); return error(c, 503, "Another card operation is active\n"); }
    s->upload_active = 1; pthread_mutex_unlock(&s->lock);
    status = move ? disc_trash_leftovers_move(&s->trash, (long long)time(NULL), &out, &n, &problem)
                  : disc_trash_leftovers(&s->trash, &out, &n, &problem);
    pthread_mutex_lock(&s->lock); s->upload_active = 0; pthread_mutex_unlock(&s->lock);
    if (status == 200 && out) {
        int code = response(c, 200, "application/json; charset=utf-8", out, n);
        free(out);
        return code;
    }
    free(out);
    char message[200];
    snprintf(message, sizeof(message), "%s\n", problem ? problem : "Trash unavailable");
    return error(c, status == 200 ? 503 : status, message);
}
/* GET /api/card/folder[/<path>] and /api/card/tree[/<path>] (combined-009, the
 * owner's proposal): one folder of the card, or a folder and everything below
 * it with the facts of every audio file, each in one request instead of
 * stock's transfer browser page by page and a metadata read per file. The
 * tree is written in pieces as the walk goes; one walk at a time. */
typedef struct { struct mg_connection *c; disc_stream stream; } tree_out;
static int tree_emit(void *arg, const char *data, size_t n) {
    tree_out *t = arg;
    char head[24];
    int k = snprintf(head, sizeof(head), "%zx\r\n", n);
    return write_all(t->c, head, (size_t)k, &t->stream) && write_all(t->c, data, n, &t->stream) &&
           write_all(t->c, "\r\n", 2, &t->stream) ? 0 : -1;
}
static int card_listing_route(struct mg_connection *c, server *s, const char *uri, int tree) {
    const char *rest = uri + (tree ? 14 : 16);
    char relative[DISC_MEDIA_MAX_PATH + 1] = "";
    if (*rest) {
        size_t raw = strlen(rest + 1);
        /* CivetWeb hands over the path already URL-decoded (local_uri_raw). */
        if (rest[0] != '/' || !raw || raw >= sizeof(relative)) return error(c, 404, "No such folder\n");
        memcpy(relative, rest + 1, raw + 1);
    }
    const char *root = media_root(s);
    if (!root) return error(c, 501, "No music card configured\n");
    if (s->webroot.mount && !disc_webroot_available(&s->webroot)) return error(c, 503, "Card is not owned by the player\n");
    int status, folder = disc_tree_open(root, relative, &status);
    if (folder < 0) return error(c, status, status == 404 ? "No such folder\n" : status == 400 ? "Folder path not admitted\n" : "Card unavailable\n");
    if (!tree) {
        char *json = NULL;
        size_t n = 0;
        status = disc_tree_folder(folder, relative, &json, &n);
        if (status != 200) return error(c, status, "Folder unreadable\n");
        int code = response(c, 200, "application/json; charset=utf-8", json, n);
        free(json);
        return code;
    }
    pthread_mutex_lock(&s->lock);
    int busy = s->tree_active;
    if (!busy) s->tree_active = 1;
    pthread_mutex_unlock(&s->lock);
    if (busy) { close(folder); return error(c, 503, "The card is being read for another client\n"); }
    tree_out out = {.c = c};
    stream_start(&out.stream);
    stream_budget(c, &out.stream);
    int code = 200;
    if (mg_printf(c, "HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nTransfer-Encoding: chunked\r\n"
                     "Connection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n%s"
                     "Content-Security-Policy: default-src 'none'; frame-ancestors 'none'\r\n\r\n", cors_headers(c)) <= 0) { close(folder); code = 500; }
    /* Only a complete walk earns the chunk terminator; a partial one is closed. */
    else if (disc_tree_walk(folder, root, relative, monotonic_ms(), tree_emit, &out) || !write_all(c, "0\r\n\r\n", 5, &out.stream)) code = 500;
    pthread_mutex_lock(&s->lock); s->tree_active = 0; pthread_mutex_unlock(&s->lock);
    return code;
}
/* /api/lists/<scope>[/<name>] (combined-009): the scope "internal" is the
 * service's own lists, hidden in .disc/playlists (a live queue, "play next"),
 * "external" the ones the player's file browser shows, in Playlists/ at the
 * card root (automatic playlists). GET lists a scope or reads a list back, PUT
 * {"entries":[...]} writes one, DELETE removes it; GET /api/lists names the
 * scopes' folders. Changes carry the SN, a fresh request ID and pacing, wait
 * for a library scan to end and exclude uploads and trash moves; the body is
 * read only after those checks. */
#define DISC_LISTS_PACING_MS 300
static int lists_route(struct mg_connection *c, server *s, const char *uri) {
    const struct mg_request_info *r = mg_get_request_info(c);
    const char *method = r->request_method, *rest = uri + 10, *cl = mg_get_header(c, "Content-Length");
    if (!s->lists_dir && !s->external_lists_dir) return error(c, 404, "No lists configured\n");
    if (r->query_string) return error(c, 405, "Lists take no query\n");
    if (!*rest) {
        if (strcmp(method, "GET")) return error(c, 405, "Name a scope of lists\n");
        disc_buffer b = {0};
        disc_buffer_text(&b, "{\"scopes\":{\"internal\":");
        if (s->lists_dir) disc_buffer_string(&b, s->lists_dir, strlen(s->lists_dir));
        else disc_buffer_text(&b, "null");
        disc_buffer_text(&b, ",\"external\":");
        if (s->external_lists_dir) disc_buffer_string(&b, s->external_lists_dir, strlen(s->external_lists_dir));
        else disc_buffer_text(&b, "null");
        disc_buffer_text(&b, "}}");
        int code = b.overflow ? error(c, 500, "Lists unavailable\n") : response(c, 200, "application/json; charset=utf-8", b.data, b.used);
        free(b.data);
        return code;
    }
    const char *folder_dir = !strncmp(rest, "/internal", 9) && (!rest[9] || rest[9] == '/') ? s->lists_dir
                           : !strncmp(rest, "/external", 9) && (!rest[9] || rest[9] == '/') ? s->external_lists_dir : NULL;
    if (!folder_dir) return error(c, 404, "No such scope of lists\n");
    rest += 9;
    char name[DISC_LISTS_NAME_MAX * 3 + 8] = "";
    if (*rest) {
        size_t raw = strlen(rest + 1);
        /* CivetWeb hands over the path already URL-decoded (local_uri_raw). */
        if (rest[0] != '/' || !raw || raw >= sizeof(name)) return error(c, 404, "No such list\n");
        memcpy(name, rest + 1, raw + 1);
        if (!disc_lists_name_ok(name)) return error(c, 404, "No such list\n");
    }
    int index = !*rest && !strcmp(method, "GET"), read = *rest && !strcmp(method, "GET");
    int write = *rest && !strcmp(method, "PUT"), remove = *rest && !strcmp(method, "DELETE");
    if (!(index || read || write || remove)) return error(c, 405, "Method not allowed for lists\n");
    unsigned length = 0;
    if (mg_get_header(c, "Transfer-Encoding") || header_count(c, "Content-Length") > 1 || (cl && !decimal(cl, 0x7fffffff, &length)))
        return error(c, 400, "One plain Content-Length, no transfer encoding\n");
    if (!write && length) return error(c, 400, "This list operation takes no body\n");
    if (write && !length) return error(c, 411, "A body {\"entries\":[...]} with its Content-Length is required\n");
    if (length > DISC_LISTS_MAX_BODY) return error(c, 413, "Body too large\n");
    disc_lists lists = {.card = media_root(s), .folder = folder_dir};
    char *out = NULL, *body = NULL;
    size_t n = 0;
    const char *problem = NULL;
    int status;
    if (s->webroot.mount && !disc_webroot_available(&s->webroot)) return error(c, 503, "Card is not owned by the player\n");
    if (index || read) {
        status = index ? disc_lists_index(&lists, &out, &n, &problem) : disc_lists_read(&lists, name, &out, &n, &problem);
        goto reply;
    }
    const char *token = mg_get_header(c, "X-Disc-Token"), *request_id = mg_get_header(c, "X-Disc-Request");
    if (header_count(c, "X-Disc-Token") != 1 || !token || strlen(token) > 64 ||
        !credential_matches(s, r->remote_addr, token, strlen(token))) return error(c, 403, "Token required\n");
    if (header_count(c, "X-Disc-Request") != 1 || !request_id || !valid_request_id(request_id, strlen(request_id)))
        return error(c, 403, "Request ID required\n");
    if (scan_active(s)) return error(c, 503, "Library scan in progress; nothing was written\n");
    if (!remember_request(s, request_id)) return error(c, 409, "Request ID already used\n");
    pace_class(s, "lists", DISC_LISTS_PACING_MS);
    if (write) {
        body = malloc(length + 1);
        size_t used = 0;
        while (body && used < length) { int got = mg_read(c, body + used, length - used); if (got <= 0) break; used += (size_t)got; }
        if (!body || used != length) { free(body); return error(c, 400, "Incomplete body\n"); }
        body[length] = 0;
    }
    pthread_mutex_lock(&s->lock);
    if (s->upload_active) { pthread_mutex_unlock(&s->lock); free(body); return error(c, 503, "Another card operation is active\n"); }
    s->upload_active = 1; pthread_mutex_unlock(&s->lock);
    status = write ? disc_lists_write(&lists, name, body, length, &out, &n, &problem) : disc_lists_delete(&lists, name, &out, &n, &problem);
    pthread_mutex_lock(&s->lock); s->upload_active = 0; pthread_mutex_unlock(&s->lock);
    free(body);
reply:
    if (out) {
        int code = response(c, status, "application/json; charset=utf-8", out, n);
        free(out);
        return code;
    }
    char message[300];
    snprintf(message, sizeof(message), "%s\n", problem ? problem : "Lists unavailable");
    return error(c, status == 200 ? 503 : status, message);
}
/* /api/store[/<collection>/<operation>] (combined-008): the collections the
 * card's reviewed store.json declares, kept in the service's database. Reads
 * are open like the other data routes; a change carries the SN, a fresh
 * request ID and pacing, and its body is read only after those checks. */
#define DISC_STORE_PACING_MS 100
static int store_route(struct mg_connection *c, server *s, const char *uri) {
    const struct mg_request_info *r = mg_get_request_info(c);
    const char *method = r->request_method, *cl = mg_get_header(c, "Content-Length");
    if (!s->database_file) return error(c, 404, "No database configured\n");
    if (strcmp(method, "GET") && strcmp(method, "PUT") && strcmp(method, "DELETE") && strcmp(method, "POST"))
        return error(c, 405, "Store operations are GET, PUT, DELETE or POST\n");
    int with_body = !strcmp(method, "PUT") || !strcmp(method, "POST");
    unsigned length = 0;
    if (mg_get_header(c, "Transfer-Encoding") || header_count(c, "Content-Length") > 1 ||
        (cl && !decimal(cl, 0x7fffffff, &length))) return error(c, 400, "One plain Content-Length, no transfer encoding\n");
    if (!with_body && length) return error(c, 400, "This operation takes no body\n");
    if (with_body && !length) return error(c, 411, "A record body with its Content-Length is required\n");
    if (length > DISC_STORE_MAX_BODY) return error(c, 413, "Body too large\n");
    catalog_cache *cache = load_card_file(s, CARD_STORE);
    if (!cache) return error(c, 403, "No reviewed store catalog for this card\n");
    int code;
    char *body = NULL, *out = NULL;
    if (disc_store_writes(method)) {
        const char *token = mg_get_header(c, "X-Disc-Token"), *request_id = mg_get_header(c, "X-Disc-Request");
        if (header_count(c, "X-Disc-Token") != 1 || !token || strlen(token) > 64 ||
            !credential_matches(s, r->remote_addr, token, strlen(token))) { code = error(c, 403, "Token required\n"); goto out; }
        if (header_count(c, "X-Disc-Request") != 1 || !request_id || !valid_request_id(request_id, strlen(request_id))) {
            code = error(c, 403, "Request ID required\n"); goto out;
        }
        if (!remember_request(s, request_id)) { code = error(c, 409, "Request ID already used\n"); goto out; }
        pace_class(s, "store", DISC_STORE_PACING_MS);
    }
    if (with_body) {
        body = malloc(length + 1);
        size_t used = 0;
        while (body && used < length) {
            int n = mg_read(c, body + used, length - used);
            if (n <= 0) break;
            used += (size_t)n;
        }
        if (!body || used != length) { code = error(c, 400, "Incomplete body\n"); goto out; }
        body[length] = 0;
    }
    disc_store_request request = {.method = method, .path = uri + 10, .query = r->query_string, .body = body,
                                  .body_length = length, .music_root = media_root(s), .now = (long long)time(NULL)};
    size_t n = 0;
    int json = 0, status = disc_store_handle(&cache->store, &s->database, &request, &out, &n, &json);
    code = out ? response(c, status, json ? "application/json; charset=utf-8" : "text/plain; charset=utf-8", out, n)
               : error(c, 503, "Store unavailable\n");
out:
    free(body);
    free(out);
    drop_catalog(s, cache);
    return code;
}
/* The skip rule (combined-008): a track that starts to sound while it is in a
 * collection the store catalog marks "skip" is skipped, only while no client
 * holds the control channel (an open page applies the rule itself). The
 * service reserves the channel, sends stock's own "next" record (admitted by
 * the card's commands.json) over a short connection of its own after the
 * session handshake, and confirms it by the track that follows; it never
 * retries. At most DISC_SKIP_STREAK skips in a row, so a queue of nothing
 * else cannot spin. */
#define DISC_SKIP_STREAK 10
#define DISC_SKIP_BUDGET_MS 4000
typedef struct { const char *path, *title; int handshake, moved; } skip_watch;
/* Stock's a202 carries the track as a JSON string inside JSON:
 * {"song":"{\"song_file_path\":\"\\/tmp\\/sdcard\\/...\",...}","state":0,...}. */
static int skip_record(unsigned char *p, size_t n, void *arg) {
    skip_watch *w = arg;
    if (n >= 8 && !memcmp(p, "a599", 4)) w->handshake = 1;
    if (n <= 8 || memcmp(p, "a202", 4)) return 0;
    const char *json = (const char *)p + 8;
    jsmntok_t *outer = NULL, *inner = NULL;
    char *song = malloc(n), playing[1024];
    int i, length;
    if (song && disc_json_parse(json, n - 8, &outer, 256) > 0 && (i = disc_json_find(json, outer, 0, "song")) > 0 &&
        (length = disc_json_string(json, &outer[i], song, n)) > 0 && disc_json_parse(song, (size_t)length, &inner, 256) > 0 &&
        (i = disc_json_find(song, inner, 0, "song_file_path")) > 0 &&
        disc_json_string(song, &inner[i], playing, sizeof(playing)) > 0) {
        /* Another file, or within a CUE image another track (stock names it song_name). */
        if (strcmp(playing, w->path)) w->moved = 1;
        else if (w->title && (i = disc_json_find(song, inner, 0, "song_name")) > 0 &&
                 disc_json_string(song, &inner[i], playing, sizeof(playing)) > 0 && strcmp(playing, w->title)) w->moved = 1;
    }
    free(inner);
    free(outer);
    free(song);
    return 0;
}
static int skip_wait(int fd, disc_frames *frames, skip_watch *w, int *flag, long long deadline) {
    unsigned char chunk[4096];
    while (!*flag && monotonic_ms() < deadline) {
        ssize_t n = recv(fd, chunk, sizeof(chunk), 0);
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) continue;
        if (n <= 0 || disc_feed(frames, chunk, (size_t)n, skip_record, w) < 0) return 0;
    }
    return *flag;
}
static int skip_to_next(server *s, const char *path, const char *title) {
    int fd = connect_tcp(s);
    if (fd < 0) return 0;
    struct timeval poll = {.tv_sec = 0, .tv_usec = 250000};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &poll, sizeof(poll));
    disc_frames *frames = calloc(1, sizeof(*frames));
    skip_watch w = {.path = path, .title = title};
    int moved = 0;
    if (frames && send(fd, "0599000C0000", 12, 0) == 12 &&
        skip_wait(fd, frames, &w, &w.handshake, monotonic_ms() + DISC_SKIP_BUDGET_MS) &&
        send(fd, "0201000C0001", 12, 0) == 12) {
        moved = skip_wait(fd, frames, &w, &w.moved, monotonic_ms() + DISC_SKIP_BUDGET_MS);
        /* A push may name nothing new yet: read the state once more, never "next" again. */
        if (!moved && send(fd, "02020008", 8, 0) == 8) moved = skip_wait(fd, frames, &w, &w.moved, monotonic_ms() + 2000);
    }
    free(frames);
    close(fd);
    return moved;
}
static int track_sounding(void *arg, const char *path, const char *title) {
    server *s = arg;
    catalog_cache *store = load_card_file(s, CARD_STORE);
    int skip = store && disc_store_skips(&store->store, &s->database, path, title);
    drop_catalog(s, store);
    if (!skip) { s->skip_streak = 0; return 0; }
    catalog_cache *commands = load_catalog(s);
    int admitted = commands && disc_catalog_admit(&commands->catalog, "0201", "0001", 4);
    drop_catalog(s, commands);
    if (!admitted || s->skip_streak >= DISC_SKIP_STREAK) {
        disc_log("Skip rule: %s\n", admitted ? "too many skips in a row; left playing" : "next is not admitted by the card catalog");
        return 0;
    }
    pthread_mutex_lock(&s->lock);
    int owned = s->active;
    if (!owned) s->active = 1;
    pthread_mutex_unlock(&s->lock);
    if (owned) { disc_log("Skip rule: a client holds control and applies it\n"); return 0; }
    int moved = skip_to_next(s, path, title);
    pthread_mutex_lock(&s->lock); s->active = 0; pthread_mutex_unlock(&s->lock);
    s->skip_streak++;
    disc_log("Skip rule: %s\n", moved ? "skipped to the next track" : "next sent, not confirmed; not retried");
    return 1; /* "next" was sent: the track is not a play, confirmed or not */
}
/* The card's Apps folder changes only through the application manager (owner, 2026-10-02);
 * FAT folds case. */
static int under_apps(const char *stock_path) {
    const char *at = strstr(stock_path, "/tmp/sdcard/");
    return at && !strncasecmp(at + 12, "Apps", 4) && (at[16] == '/' || !at[16]);
}
static int stock_request(struct mg_connection *c, server *s, const char *uri) {
    const struct mg_request_info *request = mg_get_request_info(c);
    const char *method = request->request_method, *path = uri + 10; /* after "/api/stock" */
    if (strcmp(method, "GET") && strcmp(method, "POST") && strcmp(method, "DELETE")) return error(c, 405, "Method not admitted\n");
    if (request->query_string) return error(c, 405, "Query strings are never forwarded to stock\n");
    if (strlen(path) > 700) return error(c, 414, "Stock path too long\n");
    catalog_cache *cache = load_catalog(s);
    if (!cache) return error(c, 403, "No reviewed command catalog for this card\n");
    const disc_catalog *catalog = &cache->catalog;
    disc_header headers[64]; size_t count = 0;
    for (int i = 0; i < request->num_headers && count < 64; i++)
        headers[count++] = (disc_header){request->http_headers[i].name, request->http_headers[i].value};
    const disc_route *route = disc_catalog_admit_route(catalog, method, path, headers, count);
    int code;
    char *body = NULL; size_t body_len = 0;
    if (!route) { code = error(c, 403, "Stock request not admitted by the catalog\n"); goto out; }
    const char *cl = mg_get_header(c, "Content-Length"), *te = mg_get_header(c, "Transfer-Encoding");
    unsigned declared = 0;
    if (te || count > 63 || (cl && (count && !decimal(cl, 2147483647, &declared)))) { code = error(c, 400, "Invalid request body framing\n"); goto out; }
    if (route->body == 0 && declared) { code = error(c, 400, "This stock route takes no body\n"); goto out; }
    if (route->body != 0 && (!cl || !declared)) { code = error(c, 400, "Content-Length required\n"); goto out; }
    if (declared > route->max_body_bytes) { code = error(c, 413, "Body exceeds the catalog bound\n"); goto out; }
    if (route->mutation) {
        /* Hidden folders (the service's own .disc among them) are never changed through stock. */
        if (strstr(path, "/.")) { code = error(c, 403, "Hidden folders cannot be changed\n"); goto out; }
        if (under_apps(path)) { code = error(c, 403, "Apps change only through the application manager\n"); goto out; }
        const char *token = mg_get_header(c, "X-Disc-Token"), *request_id = mg_get_header(c, "X-Disc-Request");
        if (header_count(c, "X-Disc-Token") != 1 || !token || strlen(token) > 64 ||
            !credential_matches(s, request->remote_addr, token, strlen(token))) { code = error(c, 403, "Token required\n"); goto out; }
        if (header_count(c, "X-Disc-Request") != 1 || !request_id || !valid_request_id(request_id, strlen(request_id))) { code = error(c, 403, "Request ID required\n"); goto out; }
        if (scan_active(s)) { code = error(c, 503, "Library scan in progress; nothing was sent\n"); goto out; }
        if (!remember_request(s, request_id)) { code = error(c, 409, "Request ID already used\n"); goto out; }
    }
    if (route->body == 2) { pace_class(s, route->class, 500); code = upload_request(c, s, path, declared); goto out; }
    if (declared) {
        body = malloc(declared); size_t used = 0;
        while (body && used < declared) { int n = mg_read(c, body + used, declared - used); if (n <= 0) break; used += (size_t)n; }
        if (!body || used != declared) { code = error(c, 400, "Incomplete request body\n"); goto out; }
        body_len = declared;
    }
    pthread_mutex_lock(&s->lock);
    if (s->catalog_active) { pthread_mutex_unlock(&s->lock); code = error(c, 503, "Stock HTTP request already active\n"); goto out; }
    s->catalog_active = 1; pthread_mutex_unlock(&s->lock);
    if (route->mutation) pace_class(s, route->class, 500);
    code = stock_proxy(c, s, route, method, path, headers, count, body, body_len);
    pthread_mutex_lock(&s->lock); s->catalog_active = 0; pthread_mutex_unlock(&s->lock);
out:
    free(body);
    drop_catalog(s, cache);
    return code;
}
static int catalog(struct mg_connection *c, server *s, int stream) {
    unsigned start = 0, count = 20;
    if (stream && (!page_header(c, "start-pos", 2147483647, &start) ||
                   !page_header(c, "num-max", 200, &count) || !count)) return error(c, 400, "Invalid pagination\n");
    pthread_mutex_lock(&s->lock);
    if (s->catalog_active) { pthread_mutex_unlock(&s->lock); return error(c, 503, "Catalog request already active\n"); }
    s->catalog_active = 1; pthread_mutex_unlock(&s->lock);
    int result = stream ? stream_catalog(c, s, start, count) : fetch_catalog(c, s);
    pthread_mutex_lock(&s->lock); s->catalog_active = 0; pthread_mutex_unlock(&s->lock);
    return result;
}
/* GET /api/about (combined-008): what support needs to read without a
 * console. Versions, the image's identity file, which release serves the
 * page, the card and its database, the boot layer's status and the newest
 * service messages. Read-only; names no credential, track or user file. */
/* A small JSON object file as it is, or null. */
static void about_json_file(const char *path, disc_buffer *b) {
    char text[4097];
    ssize_t n = -1;
    int fd = path ? open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC) : -1;
    struct stat st;
    if (fd >= 0 && !fstat(fd, &st) && S_ISREG(st.st_mode) && st.st_size > 1 && st.st_size <= 4096) n = read(fd, text, (size_t)st.st_size);
    if (fd >= 0) close(fd);
    while (n > 0 && (text[n - 1] == '\n' || text[n - 1] == ' ')) n--;
    if (n > 1 && disc_json_object(text, (size_t)n)) disc_buffer_put(b, text, (size_t)n);
    else disc_buffer_text(b, "null");
}
/* The boot layer's decision and this service's role (its package, slot, confirmation), or null. */
static void about_boot(server *s, disc_buffer *b) {
    if (!s->boot_status) { disc_buffer_text(b, "null"); return; }
    char path[256];
    disc_buffer_text(b, "{\"decision\":");
    about_json_file(snprintf(path, sizeof(path), "%s/boot.json", s->boot_status) < (int)sizeof(path) ? path : NULL, b);
    disc_buffer_text(b, ",\"service\":");
    about_json_file(snprintf(path, sizeof(path), "%s/service.json", s->boot_status) < (int)sizeof(path) ? path : NULL, b);
    disc_buffer_text(b, "}");
}
/* An app's version as its optional app.json says ({"version":"<text>"}), for the diagnostics. */
static void app_version(const disc_webroot *from, const char *app, disc_buffer *b) {
    char text[1024], version[33];
    int n = disc_app_small_file(from, app, "app.json", text, sizeof(text));
    jsmntok_t *t = NULL;
    int i, ok = n > 1 && disc_json_parse(text, (size_t)n, &t, 64) > 0 && t[0].type == JSMN_OBJECT &&
                (i = disc_json_find(text, t, 0, "version")) > 0 && disc_json_string(text, &t[i], version, sizeof(version)) > 0;
    free(t);
    if (ok) disc_buffer_string(b, version, strlen(version));
    else disc_buffer_text(b, "null");
}
/* The app served at "/" (owner, 2026-10-02): the one chosen in the manager while it is installed,
 * else the only app installed; none when there are several and no choice. */
typedef struct { int count; char first[DISC_APP_NAME_MAX + 1]; } apps_count;
static void count_app(void *arg, const char *app) {
    apps_count *n = arg;
    if (!n->count++) snprintf(n->first, sizeof(n->first), "%s", app);
}
static int effective_default(server *s, char out[DISC_APP_NAME_MAX + 1]) {
    disc_web_asset asset;
    pthread_mutex_lock(&s->lock);
    snprintf(out, DISC_APP_NAME_MAX + 1, "%s", s->settings.default_app);
    pthread_mutex_unlock(&s->lock);
    if (out[0] && disc_app_open(&s->webroot, out, "", 0, &asset)) { close(asset.fd); return 1; }
    apps_count n = {0};
    disc_apps_list(&s->webroot, count_app, &n);
    if (n.count == 1) { snprintf(out, DISC_APP_NAME_MAX + 1, "%s", n.first); return 1; }
    out[0] = 0;
    return 0;
}
static int about_route(struct mg_connection *c, server *s) {
    static const char *const states[] = {"ok", "absent", "away", "newer", "failed"};
    disc_buffer b = {0};
    char app[DISC_APP_NAME_MAX + 1];
    const disc_webroot *page = effective_default(s, app) ? &s->webroot : NULL;
    disc_buffer_text(&b, "{\"service\":{\"name\":\"disc-native-probe\",\"version\":\"" DISC_SERVICE_VERSION "\",\"build\":");
    disc_buffer_string(&b, DISC_BUILD, strlen(DISC_BUILD));
    disc_buffer_text(&b, ",\"api\":1,\"uptime\":");
    disc_buffer_int(&b, (monotonic_ms() - s->started_ms) / 1000);
    /* Supervised by the boot layer when it runs as its package; the combined images' identity file
     * and restart log are gone (image is always null, restarts empty: boot's status tells). */
    disc_buffer_text(&b, s->boot_status ? ",\"supervised\":true},\"image\":null" : ",\"supervised\":false},\"image\":null");
    disc_buffer_text(&b, ",\"boot\":");
    about_boot(s, &b);
    disc_buffer_text(&b, ",\"ports\":{\"apps\":");
    disc_buffer_int(&b, s->port);
    disc_buffer_text(&b, ",\"manager\":");
    disc_buffer_int(&b, s->manager_port);
    disc_buffer_text(&b, "}");
    disc_buffer_text(&b, ",\"page\":{\"source\":");
    disc_buffer_text(&b, page ? "\"card\"" : "null");
    disc_buffer_text(&b, ",\"app\":");
    if (page) disc_buffer_string(&b, app, strlen(app));
    else disc_buffer_text(&b, "null");
    disc_buffer_text(&b, ",\"version\":");
    if (page) app_version(page, app, &b);
    else disc_buffer_text(&b, "null");
    disc_buffer_text(&b, card_owned(s) ? "},\"card\":{\"owned\":true}" : "},\"card\":{\"owned\":false}");
    disc_buffer_text(&b, ",\"database\":");
    if (!s->database_file) disc_buffer_text(&b, "null");
    else {
        disc_database_facts f;
        disc_database_facts_read(&s->database, &f);
        disc_buffer_text(&b, "{\"state\":\"");
        disc_buffer_text(&b, f.state >= 0 && f.state <= 4 ? states[f.state] : "failed");
        const struct { const char *key; long long value; } numbers[] = {
            {"\",\"schema\":", f.schema}, {",\"bytes\":", f.bytes}, {",\"plays\":", f.plays},
            {",\"records\":", f.records}, {",\"trash\":", f.trash}};
        for (size_t i = 0; i < sizeof(numbers) / sizeof(numbers[0]); i++) {
            disc_buffer_text(&b, numbers[i].key);
            if (numbers[i].value >= 0) disc_buffer_int(&b, numbers[i].value);
            else disc_buffer_text(&b, "null");
        }
        /* How plays reached the database since the service started (combined-009). */
        disc_database_writes w;
        disc_database_writes_read(&s->database, &w);
        disc_buffer_text(&b, ",\"writes\":{\"failed\":");
        disc_buffer_int(&b, w.failures);
        const struct { const char *key; long long value; } times[] = {{",\"lastFailure\":", w.failed_at}, {",\"lastSuccess\":", w.succeeded_at}};
        for (size_t i = 0; i < 2; i++) {
            disc_buffer_text(&b, times[i].key);
            if (times[i].value) disc_buffer_int(&b, times[i].value);
            else disc_buffer_text(&b, "null");
        }
        disc_buffer_text(&b, ",\"reason\":");
        if (w.reason) disc_buffer_string(&b, w.reason, strlen(w.reason));
        else disc_buffer_text(&b, "null");
        disc_buffer_text(&b, "}}");
    }
    disc_buffer_text(&b, ",\"restarts\":[]");
    disc_buffer_text(&b, ",\"log\":");
    disc_log_json(&b);
    disc_buffer_text(&b, "}");
    int code = b.overflow ? error(c, 500, "About document too large\n") : response(c, 200, "application/json; charset=utf-8", b.data, b.used);
    free(b.data);
    return code;
}
/* An app's policy: same-origin plus the external origins its own origins.json names. */
static void app_policy(const disc_webroot *from, const char *app, char *policy, size_t capacity) {
    char *json = malloc(64 * 1024 + 1);
    disc_origins origins;
    int n = json ? disc_app_small_file(from, app, "origins.json", json, 64 * 1024 + 1) : -1;
    int ok = n > 0 && !disc_origins_parse(&origins, json, (size_t)n, NULL);
    if (n > 0 && !ok) disc_log("The origins.json of an app was rejected; it stays same-origin\n");
    disc_origins_policy(ok ? &origins : NULL, policy, capacity);
    free(json);
}
/* GET /api/contract/<name>.json (combined-009): the effective reviewed catalog a page works
 * with, now that apps carry none: compatibility.json (the image's), commands.json, queries.json
 * and store.json (the card's override where admitted, else the image's). */
static int contract_route(struct mg_connection *c, server *s, const char *name) {
    int kind = -1;
    for (int i = 0; i <= CARD_COMPATIBILITY; i++) if (!strcmp(name, CATALOG_NAMES[i])) kind = i;
    if (kind < 0) return error(c, 404, "No such catalog\n");
    disc_web_asset asset;
    int from_card = 0;
    if (kind <= CARD_HOSTED) {
        /* The file the service works with: a card override that failed its checks is not it. */
        catalog_cache *effective = load_card_file(s, kind);
        from_card = effective && effective->from_card;
        drop_catalog(s, effective);
        const char *folder = from_card ? (kind == CARD_COMMANDS ? s->card_commands : s->card_catalog_dir) : s->catalog_dir;
        if (!effective || !disc_folder_open(from_card ? &s->webroot : NULL, folder, CATALOG_NAMES[kind], &asset))
            return error(c, 404, "No such catalog on this image\n");
    } else if (!catalog_file(s, kind, &asset, &from_card)) return error(c, 404, "No such catalog on this image\n");
    char *json = asset.size <= DISC_CATALOG_MAX_JSON ? malloc(asset.size ? asset.size : 1) : NULL;
    size_t used = 0;
    while (json && used < asset.size) {
        ssize_t n = read(asset.fd, json + used, asset.size - used);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        used += (size_t)n;
    }
    close(asset.fd);
    if (!json || used != asset.size) { free(json); return error(c, 503, "Catalog unreadable\n"); }
    mg_printf(c, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %zu\r\nConnection: close\r\n"
                 "Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nX-Catalog-Source: %s\r\n%s"
                 "Content-Security-Policy: default-src 'none'; frame-ancestors 'none'\r\n\r\n",
              used, from_card ? "card" : "image", cors_headers(c));
    mg_write(c, json, used);
    free(json);
    return 200;
}
typedef struct { disc_buffer *b; const disc_webroot *root; int count; const char *effective; } apps_listing;
static void list_app(void *arg, const char *app) {
    apps_listing *l = arg;
    disc_buffer_text(l->b, l->count++ ? ",{\"name\":" : "{\"name\":");
    disc_buffer_string(l->b, app, strlen(app));
    disc_buffer_text(l->b, ",\"version\":");
    app_version(l->root, app, l->b);
    disc_buffer_text(l->b, strcmp(app, l->effective) ? ",\"default\":false}" : ",\"default\":true}");
}
/* GET /api/apps (combined-009): the card's apps, each at /apps/<name>/; "default" is the one
 * served at / (null without one), "chosen" what the manager set (owner, 2026-10-02). */
static int apps_route(struct mg_connection *c, server *s) {
    disc_buffer b = {0};
    char effective[DISC_APP_NAME_MAX + 1], chosen[DISC_APP_NAME_MAX + 1];
    effective_default(s, effective);
    pthread_mutex_lock(&s->lock);
    snprintf(chosen, sizeof(chosen), "%s", s->settings.default_app);
    pthread_mutex_unlock(&s->lock);
    apps_listing l = {.b = &b, .root = &s->webroot, .effective = effective};
    disc_buffer_text(&b, "{\"default\":");
    if (effective[0]) disc_buffer_string(&b, effective, strlen(effective));
    else disc_buffer_text(&b, "null");
    disc_buffer_text(&b, ",\"chosen\":");
    if (chosen[0]) disc_buffer_string(&b, chosen, strlen(chosen));
    else disc_buffer_text(&b, "null");
    disc_buffer_text(&b, ",\"apps\":[");
    disc_apps_list(&s->webroot, list_app, &l);
    disc_buffer_text(&b, "],\"image\":false}");
    int code = b.overflow ? error(c, 500, "Apps unavailable\n") : response(c, 200, "application/json; charset=utf-8", b.data, b.used);
    free(b.data);
    return code;
}
/* A listed origin's preflight: the methods the API takes and the request's own header names
 * (the serial number and request ID headers, stock's parameters), when they are plain names. */
static int preflight(struct mg_connection *c) {
    const char *origin = cross_origin(), *method = mg_get_header(c, "Access-Control-Request-Method");
    const char *asked = mg_get_header(c, "Access-Control-Request-Headers");
    if (!origin || !method || (strcmp(method, "GET") && strcmp(method, "HEAD") && strcmp(method, "POST") &&
                               strcmp(method, "PUT") && strcmp(method, "DELETE")) ||
        (asked && (strlen(asked) > 256 ||
                   strspn(asked, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-, ") != strlen(asked))))
        return error(c, 403, "Preflight rejected\n");
    mg_printf(c, "HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\nCache-Control: no-store\r\n"
                 "Access-Control-Allow-Origin: %s\r\nVary: Origin\r\nAccess-Control-Allow-Methods: GET, HEAD, POST, PUT, DELETE\r\n"
                 "%s%s%sAccess-Control-Max-Age: 600\r\n\r\n",
              origin, asked ? "Access-Control-Allow-Headers: " : "", asked ? asked : "", asked ? "\r\n" : "");
    return 204;
}
/* "/" with no app to serve: the manager, on the host the request named (admitted by allowed()). */
static int manager_redirect(struct mg_connection *c, server *s) {
    const char *h = mg_get_header(c, "Host"), *colon = h ? strrchr(h, ':') : NULL;
    if (!h || !colon || colon - h > 120) return error(c, 404, "No app is installed\n");
    mg_printf(c, "HTTP/1.1 302 Found\r\nLocation: http://%.*s:%d/\r\nContent-Length: 0\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n",
              (int)(colon - h), h, s->manager_port);
    return 302;
}
static int http_request(struct mg_connection *c, void *arg) {
    server *s = arg;
    int admission = allowed(c, s);
    if (!admission) return error(c, 403, "Host or Origin rejected\n");
    const struct mg_request_info *request = mg_get_request_info(c);
    if (!strcmp(request->request_method, "OPTIONS")) return preflight(c);
    const char *uri = request->local_uri_raw;
    /* Reviewed stock HTTP requests have their own method and body rules. */
    if (uri && !strncmp(uri, "/api/stock/", 11)) return stock_request(c, s, uri);
    if (uri && !strncmp(uri, "/api/data/", 10)) return data_request(c, s, uri);
    if (uri && !strncmp(uri, "/api/media/", 11)) return media_request(c, s, uri);
    if (uri && !strncmp(uri, "/api/favorites/", 15)) return favorite_route(c, s, uri);
    if (uri && (!strcmp(uri, "/api/store") || !strncmp(uri, "/api/store/", 11))) return store_route(c, s, uri);
    if (uri && (!strcmp(uri, "/api/trash") || !strncmp(uri, "/api/trash/", 11))) return trash_route(c, s, uri);
    if (uri && !strncmp(uri, "/api/card/leftovers", 19)) return leftovers_route(c, s, uri);
    if (uri && (!strcmp(uri, "/api/lists") || !strncmp(uri, "/api/lists/", 11))) return lists_route(c, s, uri);
    int card_folder = uri && (!strcmp(uri, "/api/card/folder") || !strncmp(uri, "/api/card/folder/", 17));
    int card_tree = uri && (!strcmp(uri, "/api/card/tree") || !strncmp(uri, "/api/card/tree/", 15));
    if (uri && !strcmp(uri, "/api/history") && !strcmp(request->request_method, "POST")) return history_add_route(c, s);
    int head = !strcmp(request->request_method, "HEAD");
    const char *cl = mg_get_header(c, "Content-Length");
    /* A query string is ignored on documents and static files, so a single-page
     * application may keep its navigation in the URL; API routes never take one. */
    if ((!head && strcmp(request->request_method, "GET")) || (cl && strcmp(cl, "0")) ||
        mg_get_header(c, "Transfer-Encoding") || (request->query_string && uri && !strncmp(uri, "/api/", 5)))
        return error(c, 405, "Only bodyless GET or HEAD is supported; API routes take no query\n");
    if (!uri) return error(c, 404, "Not found\n");
    if (head && !strncmp(uri, "/api/", 5))
        return error(c, 405, "HEAD is only supported for static web files\n");
    if (!strcmp(uri, "/api/health")) {
        pthread_mutex_lock(&s->lock); int active = s->active; pthread_mutex_unlock(&s->lock);
        /* Whether plays reach the card database (combined-009): "failing" once the last write failed. */
        disc_database_writes writes = {0};
        if (s->database_file) disc_database_writes_read(&s->database, &writes);
        const char *history_writes = writes.failing ? "failing" : "ok";
        char json[512]; int n = snprintf(json, sizeof(json), "{\"service\":\"disc-native-probe\",\"api\":1,\"historyWrites\":\"%s\",\"controlActive\":%s,\"readOnly\":%s,\"media\":%s,\"snPairing\":%s,\"history\":%s,\"store\":%s,\"trash\":%s,\"internalLists\":%s,\"externalLists\":%s,\"favoriteAny\":true,\"upstream\":\"%s\"}", history_writes, active ? "true" : "false", s->commands_profile_sha256 ? "false" : "true", media_root(s) ? "true" : "false", sn_pairing(s) ? "true" : "false", s->database_file && s->player_process ? "true" : "false", s->database_file ? "true" : "false", s->trash_dir ? "true" : "false", s->lists_dir ? "true" : "false", s->external_lists_dir ? "true" : "false", s->upstream);
        return response(c, 200, "application/json", json, (size_t)n);
    }
    if (!strcmp(uri, "/api/device")) return device_facts(c, s);
    if (!strcmp(uri, "/api/history")) return history_route(c, s);
    if (card_folder || card_tree) return card_listing_route(c, s, uri, card_tree);
    if (!strcmp(uri, "/api/about")) return about_route(c, s);
    if (!strcmp(uri, "/api/catalog")) return catalog(c, s, 0);
    if (!strcmp(uri, "/api/catalog/stream")) return catalog(c, s, 1);
    if (!strncmp(uri, "/api/contract/", 14)) return contract_route(c, s, uri + 14);
    if (!strcmp(uri, "/api/apps")) return apps_route(c, s);
    if (!strncmp(uri, "/api/", 5)) return error(c, 404, "Not found\n");
    /* Apps (combined-009): "/apps/<App>/<path>" is that app's, anything else the default app's,
     * from the card's Apps folder or else, for the default app, the image's copy. */
    char app[DISC_APP_NAME_MAX * 3 + 1];
    app[0] = 0;
    const char *path = uri + 1;
    int named = !strncmp(uri, "/apps/", 6);
    if (!named) {
        char chosen[DISC_APP_NAME_MAX + 1];
        /* Without an app for "/" the manager is the place to install or choose one. */
        if (!effective_default(s, chosen)) return strcmp(uri, "/") ? error(c, 404, "Not found\n") : manager_redirect(c, s);
        snprintf(app, sizeof(app), "%s", chosen);
    }
    if (named) {
        /* CivetWeb hands over the path already URL-decoded (local_uri_raw). */
        const char *name = uri + 6, *slash = strchr(name, '/');
        size_t length = slash ? (size_t)(slash - name) : strlen(name);
        if (!length || length >= sizeof(app)) return error(c, 404, "No such app\n");
        memcpy(app, name, length);
        app[length] = 0;
        if (!disc_app_name_ok(app)) return error(c, 404, "No such app\n");
        if (!slash) {
            /* The app's relative URLs need its folder's trailing slash; the address is encoded again. */
            char location[DISC_APP_NAME_MAX * 3 + 16];
            if (disc_media_encode_path(uri, location, sizeof(location))) return error(c, 404, "No such app\n");
            mg_printf(c, "HTTP/1.1 301 Moved Permanently\r\nLocation: %s/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", location);
            return 301;
        }
        path = slash + 1;
    }
    disc_web_asset asset;
    const disc_webroot *from = NULL;
    const char *from_app = app;
    if (disc_app_open(&s->webroot, app, path, accepts_gzip(c), &asset)) from = &s->webroot;
    if (from) {
        char policy[DISC_POLICY_MAX];
        app_policy(from, from_app, policy, sizeof(policy));
        return web_asset_response(c, from, &asset, head, policy);
    }
    return error(c, 404, "Not found\n");
}
/* The application manager (owner, 2026-10-02): its own listener, port and origin, so no app can
 * drive it; its page, the diagnostics and the apps' management. Every change needs the player's
 * serial number, a request ID and pacing, as anywhere. */
#define DISC_MANAGER_POLICY "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; " \
                            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
#define DISC_MANAGER_BODY_MAX 512
static int manager_asset(struct mg_connection *c, const char *uri, int head) {
    for (size_t i = 0; i < sizeof(manager_assets) / sizeof(manager_assets[0]); i++)
        if (!strcmp(uri, manager_assets[i].path)) {
            mg_printf(c, "HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %zu\r\nConnection: close\r\nCache-Control: no-store\r\n"
                         "X-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: " DISC_MANAGER_POLICY "\r\n\r\n",
                      manager_assets[i].type, manager_assets[i].size);
            if (!head) mg_write(c, manager_assets[i].data, manager_assets[i].size);
            return 200;
        }
    return error(c, 404, "Not found\n");
}
/* The serial number, a fresh request ID and pacing: what every change through the manager needs. */
static int manager_mutation(struct mg_connection *c, server *s, const char *pacing) {
    const struct mg_request_info *r = mg_get_request_info(c);
    const char *token = mg_get_header(c, "X-Disc-Token"), *request_id = mg_get_header(c, "X-Disc-Request");
    if (header_count(c, "X-Disc-Token") != 1 || !token || strlen(token) > 64 ||
        !credential_matches(s, r->remote_addr, token, strlen(token))) return error(c, 403, "Token required\n");
    if (header_count(c, "X-Disc-Request") != 1 || !request_id || !valid_request_id(request_id, strlen(request_id)))
        return error(c, 403, "Request ID required\n");
    if (!remember_request(s, request_id)) return error(c, 409, "Request ID already used\n");
    pace_class(s, pacing, 250);
    return 0;
}
/* PUT /api/apps/default {"name": "<App>" | null}: the app served at "/", kept in the settings file. */
static int default_app_route(struct mg_connection *c, server *s) {
    const struct mg_request_info *r = mg_get_request_info(c);
    const char *cl = mg_get_header(c, "Content-Length");
    unsigned length = 0;
    if (r->query_string) return error(c, 405, "The route takes no query\n");
    if (mg_get_header(c, "Transfer-Encoding") || header_count(c, "Content-Length") != 1 || !decimal(cl, 0x7fffffff, &length) || !length)
        return error(c, 411, "A body with its Content-Length is required\n");
    if (length > DISC_MANAGER_BODY_MAX) return error(c, 413, "Body too large\n");
    int refused = manager_mutation(c, s, "apps");
    if (refused) return refused;
    if (!s->settings_file) return error(c, 409, "No settings file is configured\n");
    char body[DISC_MANAGER_BODY_MAX + 1], name[DISC_APP_NAME_MAX + 1] = "";
    size_t used = 0;
    while (used < length) { int got = mg_read(c, body + used, length - used); if (got <= 0) break; used += (size_t)got; }
    if (used != length) return error(c, 400, "Incomplete body\n");
    body[length] = 0;
    jsmntok_t *t = NULL;
    int count = disc_json_parse(body, length, &t, 16), i = -1;
    int ok = count > 0 && t[0].type == JSMN_OBJECT && t[0].size == 1 && (i = disc_json_find(body, t, 0, "name")) > 0;
    int clear = ok && t[i].type == JSMN_PRIMITIVE && body[t[i].start] == 'n';
    ok = ok && (clear || (disc_json_string(body, &t[i], name, sizeof(name)) > 0 && disc_app_name_ok(name)));
    free(t);
    if (!ok) return error(c, 400, "Expected {\"name\": <an app's name or null>}\n");
    disc_web_asset asset;
    if (!clear) {
        if (!disc_app_open(&s->webroot, name, "", 0, &asset)) return error(c, 404, "No such app\n");
        close(asset.fd);
    }
    pthread_mutex_lock(&s->lock);
    disc_settings next = s->settings;
    snprintf(next.default_app, sizeof(next.default_app), "%s", clear ? "" : name);
    int written = disc_settings_write(s->settings_file, &next) == 0;
    if (written) s->settings = next;
    pthread_mutex_unlock(&s->lock);
    if (!written) return error(c, 500, "The settings could not be written\n");
    disc_log(clear ? "The default app is no longer chosen\n" : "The default app was chosen\n");
    return apps_route(c, s);
}
/* POST /api/apps: a zip of an app (one top folder named after it), checked and installed
 * on the card by apps.c; written to <card>/.disc/app-upload.zip on the way. */
static int app_install_route(struct mg_connection *c, server *s) {
    const struct mg_request_info *r = mg_get_request_info(c);
    const char *cl = mg_get_header(c, "Content-Length");
    unsigned length = 0;
    if (r->query_string) return error(c, 405, "The route takes no query\n");
    if (mg_get_header(c, "Transfer-Encoding") || header_count(c, "Content-Length") != 1 || !decimal(cl, 0x7fffffff, &length) || !length)
        return error(c, 411, "A zip with its Content-Length is required\n");
    if (length > DISC_APP_ZIP_MAX) return error(c, 413, "The archive is too large\n");
    int refused = manager_mutation(c, s, "apps");
    if (refused) return refused;
    if (!s->webroot.root) return error(c, 409, "No apps folder is configured\n");
    if (s->webroot.mount && !disc_webroot_available(&s->webroot)) return error(c, 503, "Card is not owned by the player\n");
    pthread_mutex_lock(&s->lock);
    int busy = s->manager_busy;
    if (!busy) s->manager_busy = 1;
    pthread_mutex_unlock(&s->lock);
    if (busy) return error(c, 409, "Another change is running\n");
    char folder[256], upload[300], problem[256] = "";
    const char *slash = strrchr(s->webroot.root, '/');
    int code = 0;
    if (!slash || snprintf(folder, sizeof(folder), "%.*s/.disc", (int)(slash - s->webroot.root), s->webroot.root) >= (int)sizeof(folder) ||
        snprintf(upload, sizeof(upload), "%s/app-upload.zip", folder) >= (int)sizeof(upload)) code = error(c, 500, "Path too long\n");
    int fd = -1;
    if (!code) {
        (void)mkdir(folder, 0755);
        (void)unlink(upload);
        fd = open(upload, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0644);
        if (fd < 0) code = error(c, 500, "The upload could not be stored\n");
    }
    if (!code) {
        char buf[16384];
        size_t used = 0;
        while (used < length) {
            int got = mg_read(c, buf, length - used < sizeof(buf) ? length - used : sizeof(buf));
            if (got <= 0) break;
            if (write(fd, buf, (size_t)got) != got) { used = 0; break; }
            used += (size_t)got;
        }
        if (used != length || fsync(fd)) code = error(c, 400, "Incomplete upload\n");
        close(fd);
    }
    disc_app_result result;
    if (!code) {
        int installed = disc_app_install_zip(s->webroot.root, upload, &result, problem, sizeof(problem));
        if (installed != DISC_APP_OK) {
            char text[300];
            snprintf(text, sizeof(text), "%s\n", problem);
            code = error(c, installed == DISC_APP_REFUSED ? 422 : installed == DISC_APP_NO_ROOM ? 507 : 500, text);
        }
    }
    if (fd >= 0) (void)unlink(upload);
    if (!code) {
        disc_log("An app was installed through the manager\n");
        disc_buffer b = {0};
        disc_buffer_text(&b, "{\"name\":");
        disc_buffer_string(&b, result.name, strlen(result.name));
        disc_buffer_text(&b, ",\"version\":");
        app_version(&s->webroot, result.name, &b);
        disc_buffer_text(&b, ",\"files\":");
        disc_buffer_int(&b, result.files);
        disc_buffer_text(&b, ",\"bytes\":");
        disc_buffer_int(&b, result.bytes);
        disc_buffer_text(&b, "}");
        code = b.overflow ? error(c, 500, "Answer too large\n") : response(c, 200, "application/json; charset=utf-8", b.data, b.used);
        free(b.data);
    }
    pthread_mutex_lock(&s->lock); s->manager_busy = 0; pthread_mutex_unlock(&s->lock);
    return code;
}
/* DELETE /api/apps/<App>: removes an installed app; a removed chosen app is no longer chosen.
 * CivetWeb hands over the path already URL-decoded (local_uri_raw). */
static int app_remove_route(struct mg_connection *c, server *s, const char *name) {
    const struct mg_request_info *r = mg_get_request_info(c);
    const char *cl = mg_get_header(c, "Content-Length");
    if (r->query_string || (cl && strcmp(cl, "0")) || mg_get_header(c, "Transfer-Encoding")) return error(c, 405, "A bodyless DELETE without a query\n");
    int refused = manager_mutation(c, s, "apps");
    if (refused) return refused;
    if (!s->webroot.root || !disc_app_name_ok(name)) return error(c, 404, "No such app\n");
    if (s->webroot.mount && !disc_webroot_available(&s->webroot)) return error(c, 503, "Card is not owned by the player\n");
    pthread_mutex_lock(&s->lock);
    int busy = s->manager_busy;
    if (!busy) s->manager_busy = 1;
    pthread_mutex_unlock(&s->lock);
    if (busy) return error(c, 409, "Another change is running\n");
    char problem[160] = "";
    int removed = disc_app_remove(s->webroot.root, name, problem, sizeof(problem)), code = 0;
    if (removed == DISC_APP_REFUSED) code = error(c, 404, "No such app\n");
    else if (removed != DISC_APP_OK) code = error(c, 500, "The app could not be removed\n");
    else {
        pthread_mutex_lock(&s->lock);
        if (s->settings_file && !strcmp(s->settings.default_app, name)) {
            disc_settings next = s->settings;
            next.default_app[0] = 0;
            if (!disc_settings_write(s->settings_file, &next)) s->settings = next;
        }
        pthread_mutex_unlock(&s->lock);
        disc_log("An app was removed through the manager\n");
    }
    pthread_mutex_lock(&s->lock); s->manager_busy = 0; pthread_mutex_unlock(&s->lock);
    return code ? code : apps_route(c, s);
}
/* The boot layer's status of this service's role (service.json, snowsky-disc-boot docs/contract.md). */
typedef struct {
    int present, confirmed, previous;
    char name[33], version[65], slot[2], state[16], last_request[200];
    char previous_name[33], previous_version[65], previous_manifest[65];
} boot_role;
static void read_boot_role(server *s, boot_role *r) {
    memset(r, 0, sizeof(*r));
    char path[256], text[4097];
    if (!s->boot_status || snprintf(path, sizeof(path), "%s/service.json", s->boot_status) >= (int)sizeof(path)) return;
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    ssize_t n = fd >= 0 ? read(fd, text, sizeof(text) - 1) : -1;
    if (fd >= 0) close(fd);
    while (n > 0 && (text[n - 1] == '\n' || text[n - 1] == ' ')) n--;
    if (n <= 1 || !disc_json_object(text, (size_t)n)) return;
    text[n] = 0;
    jsmntok_t *t = NULL;
    int i, p;
    if (disc_json_parse(text, (size_t)n, &t, 64) <= 0) { free(t); return; }
    r->present = (i = disc_json_find(text, t, 0, "name")) > 0 && disc_json_string(text, &t[i], r->name, sizeof(r->name)) > 0 &&
                 (i = disc_json_find(text, t, 0, "version")) > 0 && disc_json_string(text, &t[i], r->version, sizeof(r->version)) > 0;
    if ((i = disc_json_find(text, t, 0, "slot")) > 0) (void)disc_json_string(text, &t[i], r->slot, sizeof(r->slot));
    if ((i = disc_json_find(text, t, 0, "state")) > 0) (void)disc_json_string(text, &t[i], r->state, sizeof(r->state));
    if ((i = disc_json_find(text, t, 0, "confirmed")) > 0) (void)disc_json_boolean(text, &t[i], &r->confirmed);
    if ((i = disc_json_find(text, t, 0, "lastRequest")) > 0) (void)disc_json_string(text, &t[i], r->last_request, sizeof(r->last_request));
    if ((p = disc_json_find(text, t, 0, "previous")) > 0 && t[p].type == JSMN_OBJECT)
        r->previous = (i = disc_json_find(text, t, p, "name")) > 0 && disc_json_string(text, &t[i], r->previous_name, sizeof(r->previous_name)) > 0 &&
                      (i = disc_json_find(text, t, p, "version")) > 0 && disc_json_string(text, &t[i], r->previous_version, sizeof(r->previous_version)) > 0 &&
                      (i = disc_json_find(text, t, p, "manifest")) > 0 && !disc_json_sha256(text, &t[i], r->previous_manifest);
    free(t);
}
/* What the inactive slot holds: the version a rollback returns to (while boot's fingerprint of it
 * matches), else an update staged there, else nothing. */
typedef struct { boot_role role; int under_boot, previous, staged; char name[33], version[65]; } update_view;
static void read_update_view(server *s, update_view *v) {
    memset(v, 0, sizeof(*v));
    read_boot_role(s, &v->role);
    v->under_boot = s->boot_status && s->update_slot && s->update_work && s->update_request && s->boot_program && v->role.present;
    char sha[65];
    if (!v->under_boot || disc_update_slot(s->update_slot, v->name, v->version, sha)) return;
    if (v->role.previous && !strcmp(sha, v->role.previous_manifest)) v->previous = 1;
    else v->staged = 1;
}
static void put_package(disc_buffer *b, const char *name, const char *version) {
    disc_buffer_text(b, "{\"name\":");
    disc_buffer_string(b, name, strlen(name));
    disc_buffer_text(b, ",\"version\":");
    disc_buffer_string(b, version, strlen(version));
    disc_buffer_text(b, "}");
}
/* GET /api/update: whether this server takes updates, what runs, what a rollback returns to and
 * what is staged. */
static int update_route(struct mg_connection *c, server *s) {
    update_view v;
    read_update_view(s, &v);
    const char *why = !v.under_boot ? "This server does not run under the boot layer"
                    : disc_update_keys(s->update_keys) <= 0 ? "This build carries no update keys" : NULL;
    disc_buffer b = {0};
    disc_buffer_text(&b, why ? "{\"available\":false,\"why\":" : "{\"available\":true,\"why\":null");
    if (why) disc_buffer_string(&b, why, strlen(why));
    disc_buffer_text(&b, ",\"running\":");
    if (v.role.present) {
        disc_buffer_text(&b, "{\"name\":");
        disc_buffer_string(&b, v.role.name, strlen(v.role.name));
        disc_buffer_text(&b, ",\"version\":");
        disc_buffer_string(&b, v.role.version, strlen(v.role.version));
        disc_buffer_text(&b, ",\"state\":");
        disc_buffer_string(&b, v.role.state, strlen(v.role.state));
        disc_buffer_text(&b, v.role.confirmed ? ",\"confirmed\":true}" : ",\"confirmed\":false}");
    } else disc_buffer_text(&b, "null");
    disc_buffer_text(&b, ",\"previous\":");
    if (v.previous) put_package(&b, v.name, v.version); else disc_buffer_text(&b, "null");
    disc_buffer_text(&b, ",\"staged\":");
    if (v.staged) put_package(&b, v.name, v.version); else disc_buffer_text(&b, "null");
    disc_buffer_text(&b, ",\"lastRequest\":");
    if (v.role.last_request[0]) disc_buffer_string(&b, v.role.last_request, strlen(v.role.last_request));
    else disc_buffer_text(&b, "null");
    disc_buffer_text(&b, "}");
    int code = b.overflow ? error(c, 500, "Answer too large\n") : response(c, 200, "application/json; charset=utf-8", b.data, b.used);
    free(b.data);
    return code;
}
static int take_busy(server *s) {
    pthread_mutex_lock(&s->lock);
    int busy = s->manager_busy;
    if (!busy) s->manager_busy = 1;
    pthread_mutex_unlock(&s->lock);
    return busy;
}
static void drop_busy(server *s) { pthread_mutex_lock(&s->lock); s->manager_busy = 0; pthread_mutex_unlock(&s->lock); }
static int read_request_body(void *context, void *buffer, size_t n) { return mg_read(context, buffer, n); }
/* POST /api/update: a .update file, checked as it arrives and written into the inactive slot. */
static int update_upload_route(struct mg_connection *c, server *s) {
    const struct mg_request_info *r = mg_get_request_info(c);
    const char *cl = mg_get_header(c, "Content-Length");
    unsigned length = 0;
    if (r->query_string) return error(c, 405, "The route takes no query\n");
    if (mg_get_header(c, "Transfer-Encoding") || header_count(c, "Content-Length") != 1 || !decimal(cl, 0x7fffffff, &length) || !length)
        return error(c, 411, "An update with its Content-Length is required\n");
    if (length > DISC_UPDATE_MAX) return error(c, 413, "The update is too large\n");
    int refused = manager_mutation(c, s, "apps");
    if (refused) return refused;
    update_view v;
    read_update_view(s, &v);
    if (!v.under_boot) return error(c, 409, "This server does not run under the boot layer\n");
    if (disc_update_keys(s->update_keys) <= 0) return error(c, 409, "This build carries no update keys\n");
    if (!v.role.confirmed) return error(c, 409, "The running version is not confirmed yet; an update waits for it\n");
    if (take_busy(s)) return error(c, 409, "Another change is running\n");
    disc_update_config config = {s->update_slot, s->update_work, s->update_keys, v.role.name, s->boot_program};
    disc_update_result result;
    char problem[300] = "", text[320];
    int staged = disc_update_stage(&config, length, read_request_body, c, &result, problem, sizeof(problem)), code;
    drop_busy(s);
    if (staged != DISC_UPDATE_OK) {
        snprintf(text, sizeof(text), "%s\n", problem);
        return error(c, staged == DISC_UPDATE_REFUSED ? 422 : staged == DISC_UPDATE_NO_ROOM ? 507 : staged == DISC_UPDATE_SHORT ? 400 : 500, text);
    }
    disc_log("A server update was staged\n");
    disc_buffer b = {0};
    disc_buffer_text(&b, "{\"name\":");
    disc_buffer_string(&b, result.name, strlen(result.name));
    disc_buffer_text(&b, ",\"version\":");
    disc_buffer_string(&b, result.version, strlen(result.version));
    disc_buffer_text(&b, ",\"files\":");
    disc_buffer_int(&b, result.files);
    disc_buffer_text(&b, ",\"bytes\":");
    disc_buffer_int(&b, result.bytes);
    disc_buffer_text(&b, "}");
    code = b.overflow ? error(c, 500, "Answer too large\n") : response(c, 200, "application/json; charset=utf-8", b.data, b.used);
    free(b.data);
    return code;
}
/* POST /api/update/activate and /rollback: a request for the boot layer, then this server exits so
 * boot applies it; the page confirms the outcome by the version that answers afterwards. */
static int update_switch_route(struct mg_connection *c, server *s, const char *action) {
    const struct mg_request_info *r = mg_get_request_info(c);
    const char *cl = mg_get_header(c, "Content-Length");
    if (r->query_string || (cl && strcmp(cl, "0")) || mg_get_header(c, "Transfer-Encoding")) return error(c, 405, "A bodyless POST without a query\n");
    int refused = manager_mutation(c, s, "apps");
    if (refused) return refused;
    update_view v;
    read_update_view(s, &v);
    if (!v.under_boot) return error(c, 409, "This server does not run under the boot layer\n");
    int activate = !strcmp(action, "activate");
    if (activate && !v.staged) return error(c, 409, "No update is staged\n");
    if (!activate && !v.previous) return error(c, 409, "There is no previous version to return to\n");
    if (take_busy(s)) return error(c, 409, "Another change is running\n");
    char problem[200], text[260];
    if (activate && disc_update_verify(s->boot_program, s->update_slot, problem, sizeof(problem))) {
        drop_busy(s);
        snprintf(text, sizeof(text), "The boot layer refused the update: %s\n", problem);
        return error(c, 422, text);
    }
    if (disc_update_request(s->update_request, action)) { drop_busy(s); return error(c, 500, "The request for the boot layer could not be written\n"); }
    disc_log(activate ? "Restarting into the staged update\n" : "Restarting into the previous version\n");
    disc_buffer b = {0};
    disc_buffer_text(&b, "{\"restarting\":true,\"version\":");
    disc_buffer_string(&b, v.version, strlen(v.version));
    disc_buffer_text(&b, "}");
    int code = response(c, 202, "application/json; charset=utf-8", b.data, b.used);
    free(b.data);
    /* The manager stays busy: nothing else changes before the exit. */
    stopping = 1;
    return code;
}
static int manager_request(struct mg_connection *c, void *arg) {
    server *s = arg;
    request_origin[0] = 0;  /* the manager never answers a cross-origin page */
    if (!allowed_for(c, s, s->manager_authority, s->manager_port, 0)) return error(c, 403, "Host or Origin rejected\n");
    const struct mg_request_info *request = mg_get_request_info(c);
    const char *uri = request->local_uri_raw, *method = request->request_method;
    if (!uri) return error(c, 404, "Not found\n");
    if (!strcmp(uri, "/api/apps/default") && strcmp(method, "DELETE")) return strcmp(method, "PUT") ? error(c, 405, "PUT only\n") : default_app_route(c, s);
    if (!strcmp(uri, "/api/apps") && !strcmp(method, "POST")) return app_install_route(c, s);
    if (!strcmp(uri, "/api/update") && !strcmp(method, "POST")) return update_upload_route(c, s);
    if (!strcmp(uri, "/api/update/activate") || !strcmp(uri, "/api/update/rollback"))
        return strcmp(method, "POST") ? error(c, 405, "POST only\n") : update_switch_route(c, s, uri + 12);
    if (!strncmp(uri, "/api/apps/", 10) && !strcmp(method, "DELETE")) return app_remove_route(c, s, uri + 10);
    int head = !strcmp(method, "HEAD");
    const char *cl = mg_get_header(c, "Content-Length");
    if ((!head && strcmp(method, "GET")) || (cl && strcmp(cl, "0")) || mg_get_header(c, "Transfer-Encoding") ||
        (request->query_string && !strncmp(uri, "/api/", 5)))
        return error(c, 405, "Only bodyless GET or HEAD is supported here; API routes take no query\n");
    if (!strncmp(uri, "/api/", 5) && head) return error(c, 405, "HEAD is only supported for the manager's page\n");
    if (!strcmp(uri, "/api/about")) return about_route(c, s);
    if (!strcmp(uri, "/api/apps")) return apps_route(c, s);
    if (!strcmp(uri, "/api/update")) return update_route(c, s);
    if (!strncmp(uri, "/api/", 5)) return error(c, 404, "Not found\n");
    return manager_asset(c, uri, head);
}
static int port_value(const char *v) { char *end; long p = strtol(v, &end, 10); return *v && !*end && p > 0 && p <= 65535 ? (int)p : 0; }
static int service_main(int argc, char **argv) {
    server s = {.listen = "127.0.0.1", .upstream = "127.0.0.1", .tcp_port = 12100, .http_port = 12103, .lock = PTHREAD_MUTEX_INITIALIZER, .catalog_lock = PTHREAD_MUTEX_INITIALIZER};
    int port_given = 0, manager_port_given = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--version")) { puts("disc-native-probe " DISC_SERVICE_VERSION " (" DISC_BUILD ")"); return 0; }
        if (i + 1 >= argc) { disc_log("Option requires a value\n"); return 2; }
        const char *key = argv[i++], *v = argv[i];
        if (!strcmp(key, "--listen")) s.listen = v;
        else if (!strcmp(key, "--authority")) s.authority = v;
        else if (!strcmp(key, "--upstream")) s.upstream = v;
        else if (!strcmp(key, "--port")) { s.port = port_value(v); port_given = 1; }
        else if (!strcmp(key, "--manager-port")) { s.manager_port = port_value(v); manager_port_given = 1; }
        else if (!strcmp(key, "--manager-authority")) s.manager_authority = v;
        else if (!strcmp(key, "--settings")) s.settings_file = v;
        else if (!strcmp(key, "--tcp-port")) s.tcp_port = port_value(v);
        else if (!strcmp(key, "--http-port")) s.http_port = port_value(v);
        else if (!strcmp(key, "--apps")) s.webroot.root = v;
        else if (!strcmp(key, "--sd-mount")) s.webroot.mount = v;
        else if (!strcmp(key, "--sd-source")) s.webroot.source = v;
        else if (!strcmp(key, "--commands-profile-sha256")) s.commands_profile_sha256 = v;
        else if (!strcmp(key, "--upload-root")) s.upload_root = v;
        else if (!strcmp(key, "--data-root")) s.data_root = v;
        else if (!strcmp(key, "--raw-marker")) s.raw_marker = v;
        else if (!strcmp(key, "--current-lyrics")) s.current_lyrics = v;
        else if (!strcmp(key, "--serial-file")) s.serial_file = v;
        else if (!strcmp(key, "--battery-dir")) s.battery_dir = v;
        else if (!strcmp(key, "--asound-dir")) s.asound_dir = v;
        else if (!strcmp(key, "--database")) s.database_file = v;
        else if (!strcmp(key, "--trash")) s.trash_dir = v;
        else if (!strcmp(key, "--internal-lists")) s.lists_dir = v;
        else if (!strcmp(key, "--external-lists")) s.external_lists_dir = v;
        else if (!strcmp(key, "--catalog")) s.catalog_dir = v;
        else if (!strcmp(key, "--card-catalog")) s.card_catalog_dir = v;
        else if (!strcmp(key, "--card-commands")) s.card_commands = v;
        else if (!strcmp(key, "--ready-file")) s.ready_file = v;
        else if (!strcmp(key, "--boot-status")) s.boot_status = v;
        else if (!strcmp(key, "--update-slot")) s.update_slot = v;
        else if (!strcmp(key, "--update-work")) s.update_work = v;
        else if (!strcmp(key, "--update-request")) s.update_request = v;
        else if (!strcmp(key, "--update-keys")) s.update_keys = v;
        else if (!strcmp(key, "--boot-program")) s.boot_program = v;
        else if (!strcmp(key, "--mdns-name")) s.mdns_name = v;
        else if (!strcmp(key, "--cors-origin")) {
            if (cors_count >= DISC_CORS_MAX || !cors_origin_valid(v)) { disc_log("Invalid --cors-origin\n"); return 2; }
            cors_origins[cors_count++] = v;
        }
        else if (!strcmp(key, "--player-process")) s.player_process = v;
        else if (!strcmp(key, "--proc-root")) s.proc_root = v;
        else if (!strcmp(key, "--observer-interval-ms")) s.observer_interval_ms = (unsigned)port_value(v);
        else { disc_log("Unknown option: %s\n", key); return 2; }
    }
    /* Ports: an explicit option, else the settings file, else 7870 and 7871; each authority
     * follows its port unless given (owner, 2026-10-02). */
    char settings_problem[160] = "";
    int settings_read = 0;
    if (s.settings_file && s.settings_file[0] == '/' && strlen(s.settings_file) <= 240)
        settings_read = disc_settings_read(s.settings_file, &s.settings, settings_problem, sizeof(settings_problem));
    if (!port_given) s.port = s.settings.port ? s.settings.port : DISC_DEFAULT_PORT;
    if (!manager_port_given) s.manager_port = s.settings.manager_port ? s.settings.manager_port : DISC_DEFAULT_MANAGER_PORT;
    static char authority[32], manager_authority[32];
    if (!s.authority) { snprintf(authority, sizeof(authority), "127.0.0.1:%d", s.port); s.authority = authority; }
    if (!s.manager_authority) { snprintf(manager_authority, sizeof(manager_authority), "127.0.0.1:%d", s.manager_port); s.manager_authority = manager_authority; }
    struct in_addr listen_addr;
    if (!s.port || !s.tcp_port || !s.http_port || inet_pton(AF_INET, s.listen, &listen_addr) != 1 || !local_address(s.upstream) || strlen(s.authority) > 120 ||
        (s.commands_profile_sha256 && (strlen(s.commands_profile_sha256) != 64 || strspn(s.commands_profile_sha256, "0123456789abcdef") != 64 || !s.catalog_dir)) ||
        (s.catalog_dir && (s.catalog_dir[0] != '/' || strlen(s.catalog_dir) > 240)) ||
        (s.card_catalog_dir && (s.card_catalog_dir[0] != '/' || strlen(s.card_catalog_dir) > 240 || !media_root(&s))) ||
        (s.card_commands && (s.card_commands[0] != '/' || strlen(s.card_commands) > 240 || !media_root(&s))) ||
        (s.upload_root && (s.upload_root[0] != '/' || strlen(s.upload_root) > 200)) ||
        (s.data_root && (s.data_root[0] != '/' || strlen(s.data_root) > 200)) ||
        (s.raw_marker && (s.raw_marker[0] != '/' || strlen(s.raw_marker) > 240)) ||
        (s.current_lyrics && (s.current_lyrics[0] != '/' || strlen(s.current_lyrics) > 240)) ||
        (s.serial_file && (s.serial_file[0] != '/' || strlen(s.serial_file) > 240)) ||
        (s.battery_dir && (s.battery_dir[0] != '/' || strlen(s.battery_dir) > 240)) ||
        (s.asound_dir && (s.asound_dir[0] != '/' || strlen(s.asound_dir) > 240)) ||
        (s.database_file && (s.database_file[0] != '/' || strlen(s.database_file) > 240)) ||
        (s.trash_dir && (s.trash_dir[0] != '/' || strlen(s.trash_dir) > 240 || !s.database_file || !media_root(&s))) ||
        (s.lists_dir && (s.lists_dir[0] != '/' || strlen(s.lists_dir) > 240 || !media_root(&s))) ||
        (s.external_lists_dir && (s.external_lists_dir[0] != '/' || strlen(s.external_lists_dir) > 240 || !media_root(&s))) ||
        (s.ready_file && (s.ready_file[0] != '/' || strlen(s.ready_file) > 240)) ||
        (s.settings_file && (s.settings_file[0] != '/' || strlen(s.settings_file) > 240)) ||
        !s.manager_port || s.manager_port == s.port || strlen(s.manager_authority) > 120 ||
        strspn(s.manager_authority, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.:-") != strlen(s.manager_authority) ||
        (s.boot_status && (s.boot_status[0] != '/' || strlen(s.boot_status) > 200)) ||
        (s.update_slot && (s.update_slot[0] != '/' || strlen(s.update_slot) > 200)) ||
        (s.update_work && (s.update_work[0] != '/' || strlen(s.update_work) > 200)) ||
        (s.update_request && (s.update_request[0] != '/' || strlen(s.update_request) > 200)) ||
        (s.update_keys && (s.update_keys[0] != '/' || strlen(s.update_keys) > 240)) ||
        (s.boot_program && (s.boot_program[0] != '/' || strlen(s.boot_program) > 240)) ||
        (s.mdns_name && (!*s.mdns_name || strlen(s.mdns_name) > 63 || s.mdns_name[0] == '-' ||
                         s.mdns_name[strlen(s.mdns_name) - 1] == '-' ||
                         strspn(s.mdns_name, "abcdefghijklmnopqrstuvwxyz0123456789-") != strlen(s.mdns_name))) ||
        (s.player_process && (!*s.player_process || strlen(s.player_process) > 15 || strchr(s.player_process, '/') ||
                              !s.database_file || !media_root(&s))) ||
        (s.proc_root && (s.proc_root[0] != '/' || strlen(s.proc_root) > 200)) ||
        (s.observer_interval_ms && (s.observer_interval_ms < 200 || s.observer_interval_ms > 10000)) || strspn(s.authority, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.:-") != strlen(s.authority) ||
        (!!s.webroot.mount != !!s.webroot.source) || ((s.webroot.mount || s.webroot.source) && !s.webroot.root) ||
        (s.webroot.root && (s.webroot.root[0] != '/' || strlen(s.webroot.root) > 240)) ||
        (s.webroot.mount && (s.webroot.mount[0] != '/' || strlen(s.webroot.mount) > 200)) ||
        (s.webroot.source && (s.webroot.source[0] != '/' || strlen(s.webroot.source) > 200))) {
        disc_log("Invalid options; upstream must be a local interface IPv4\n"); return 2;
    }
    signal(SIGPIPE, SIG_IGN); signal(SIGINT, stop_signal); signal(SIGTERM, stop_signal);
    s.started_ms = monotonic_ms();
    char bind[64]; snprintf(bind, sizeof(bind), "%s:%d", s.listen, s.port);
    /* Eight workers (combined-009): the control channel holds one while it is open and two audio
     * streams and two media reads may hold one each while a slow client reads; three stay for the
     * page's files and the API (with four, a page chunk waited more than 5 s behind covers). */
    /* CivetWeb's own CORS is off: by default it answered any preflight with "*" before the
     * Host and Origin checks; preflight() answers only a listed origin (2026-09-30). */
    const char *options[] = {"listening_ports", bind, "num_threads", "8", "max_request_size", "8192", "request_timeout_ms", "3000", "websocket_timeout_ms", "2000", "enable_websocket_ping_pong", "yes", "enable_keep_alive", "no", "enable_directory_listing", "no",
                             "access_control_allow_origin", "", "access_control_allow_methods", "", "access_control_allow_headers", "", NULL};
    struct mg_callbacks callbacks = {0}; callbacks.connection_close = release;
    mg_init_library(0);
    struct mg_context *ctx = mg_start(&callbacks, &s, options);
    if (!ctx) { disc_log("HTTP server startup failed\n"); return 1; }
    mg_set_websocket_handler(ctx, "/api/websocket", ws_connect, ws_ready, ws_data, ws_closed, &s);
    mg_set_request_handler(ctx, "/", http_request, &s);
    if (settings_read < 0) disc_log("The settings file was refused; the defaults apply (%s)\n", settings_problem);
    /* The manager's own listener and workers: it answers while the apps' port is busy, and a port it
     * cannot take leaves the apps' port serving (the diagnostics say so). */
    char manager_bind[64]; snprintf(manager_bind, sizeof(manager_bind), "%s:%d", s.listen, s.manager_port);
    const char *manager_options[] = {"listening_ports", manager_bind, "num_threads", "2", "max_request_size", "8192", "request_timeout_ms", "10000",
                                     "enable_keep_alive", "no", "enable_directory_listing", "no", "access_control_allow_origin", "",
                                     "access_control_allow_methods", "", "access_control_allow_headers", "", NULL};
    struct mg_callbacks manager_callbacks = {0};
    struct mg_context *manager_ctx = mg_start(&manager_callbacks, &s, manager_options);
    if (manager_ctx) mg_set_request_handler(manager_ctx, "/", manager_request, &s);
    else disc_log("The manager's port %d could not be taken; the apps' port serves without it\n", s.manager_port);
    disc_database_init(&s.database, s.database_file, card_owned, &s);
    s.trash = (disc_trash){.card = media_root(&s), .folder = s.trash_dir, .database = &s.database,
                           .proc_root = s.proc_root ? s.proc_root : "/proc", .process = s.player_process};
    disc_history *history = NULL;
    char song_db[256];
    if (s.player_process) {
        if (s.data_root) snprintf(song_db, sizeof(song_db), "%s/song.db", s.data_root);
        disc_history_config config = {.proc_root = s.proc_root ? s.proc_root : "/proc", .process = s.player_process,
                                      .music_root = media_root(&s), .song_db = s.data_root ? song_db : NULL,
                                      .database = &s.database, .interval_ms = s.observer_interval_ms,
                                      .sounding = track_sounding, .arg = &s};
        if (disc_history_start(&history, &config)) disc_log("Play observer did not start\n");
    }
    printf("DISC native probe listening on %s; authority %s; local upstream %s\n", bind, s.authority, s.upstream);
    if (s.ready_file) {
        /* Listening: the boot layer may count the service as ready. */
        int ready = open(s.ready_file, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW | O_CLOEXEC, 0644);
        if (ready < 0) disc_log("The ready file could not be written\n");
        else close(ready);
    }
    for (int i = 0; i < cors_count; i++) printf("Cross-origin page admitted: %s\n", cors_origins[i]);
    fflush(stdout);
    while (!stopping) {
        struct timespec delay = {.tv_sec = 0, .tv_nsec = 100000000};
        nanosleep(&delay, NULL);
        disc_history_poll(history);
    }
    disc_history_stop(history);
    if (manager_ctx) mg_stop(manager_ctx);
    mg_stop(ctx); mg_exit_library(); pthread_mutex_destroy(&s.lock); return 0;
}

int main(int argc, char **argv) { return service_main(argc, argv); }
