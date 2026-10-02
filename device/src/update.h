#ifndef DISC_UPDATE_H
#define DISC_UPDATE_H
/* The server's updates through the application manager (owner, 2026-10-02): a release's
 * .update file (scripts/update_file.py) is checked as it streams in and written into the
 * boot layer's inactive slot; boot switches to it on request (snowsky-disc-boot
 * docs/contract.md, "Requests from a package"). The format:
 *   "DISCUPD1" | Ed25519 signature over package.json (64) | its length, u32 LE |
 *   package.json | every listed file's bytes, in the manifest's order. */
#include <stddef.h>

#define DISC_UPDATE_MANIFEST_MAX (64 * 1024)
#define DISC_UPDATE_PACKAGE_MAX (32LL * 1024 * 1024)
#define DISC_UPDATE_FILES_MAX 256
#define DISC_UPDATE_HEADER 76
#define DISC_UPDATE_MAX (DISC_UPDATE_HEADER + DISC_UPDATE_MANIFEST_MAX + DISC_UPDATE_PACKAGE_MAX)
/* What boot keeps free in /usr/data for stock. */
#define DISC_UPDATE_RESERVE (16LL * 1024 * 1024)

enum { DISC_UPDATE_OK = 0, DISC_UPDATE_REFUSED = -1, DISC_UPDATE_NO_ROOM = -2, DISC_UPDATE_FAILED = -3, DISC_UPDATE_SHORT = -4 };

typedef struct {
    const char *slot;          /* $DISC_BOOT_INACTIVE */
    const char *work;          /* a folder of the package's own data on the same file system, where the
                                  update is written and checked before it takes the slot's place */
    const char *keys;          /* the public keys the running package trusts, hex, one per line */
    const char *name;          /* the running package's name: only it replaces itself */
    const char *boot_program;  /* $DISC_BOOT_PROGRAM, for its second opinion (verify) */
} disc_update_config;

typedef struct { char name[33], version[65]; int files; long long bytes; } disc_update_result;

/* Reads up to n bytes: the count, 0 at the end, < 0 on an error. */
typedef int (*disc_update_reader)(void *context, void *buffer, size_t n);

/* The number of valid keys in a keys file (0 when there is none). */
int disc_update_keys(const char *path);
/* Checks the update of the given length as it arrives, writes it into the work folder, has boot
 * check it there, then lets it take the slot's place: the slot (the version a rollback returns to)
 * changes only for a complete, checked update. problem names the first refusal or failure. */
int disc_update_stage(const disc_update_config *config, long long length, disc_update_reader read, void *context,
                      disc_update_result *out, char *problem, size_t capacity);
/* The package in a slot: its name, version and the SHA-256 of its package.json; 0 when there is one. */
int disc_update_slot(const char *slot, char name[33], char version[65], char sha256[65]);
/* boot's own check of a slot (disc-boot verify service SLOT); 0 when it passes. */
int disc_update_verify(const char *boot_program, const char *slot, char *problem, size_t capacity);
/* A request for boot ({"action": "activate" | "rollback"}), written atomically. */
int disc_update_request(const char *path, const char *action);
#endif
