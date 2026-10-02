/* The conformance tests' driver for update.c: stages an .update file as the manager does,
 * without HTTP (tests/conformance/test_update.py). One line on stdout:
 * "ok\t<name>\t<version>\t<files>\t<bytes>" or "<code>\t<problem>".
 * LENGTH, when given, is what the request would declare (a short upload declares more). */
#include "update.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int from_file(void *context, void *buffer, size_t n) {
    size_t got = fread(buffer, 1, n, context);
    return got ? (int)got : ferror((FILE *)context) ? -1 : 0;
}

int main(int argc, char **argv) {
    char problem[512] = "";
    if ((argc == 8 || argc == 9) && !strcmp(argv[1], "stage")) {
        disc_update_config config = {argv[2], argv[3], argv[4], argv[5], argv[6]};
        FILE *f = fopen(argv[7], "rb");
        if (!f) { perror(argv[7]); return 2; }
        fseek(f, 0, SEEK_END);
        long long length = argc == 9 ? atoll(argv[8]) : ftell(f);
        fseek(f, 0, SEEK_SET);
        disc_update_result r;
        int code = disc_update_stage(&config, length, from_file, f, &r, problem, sizeof(problem));
        fclose(f);
        if (code == DISC_UPDATE_OK) printf("ok\t%s\t%s\t%d\t%lld\n", r.name, r.version, r.files, r.bytes);
        else printf("%d\t%s\n", code, problem);
        return 0;
    }
    if (argc == 4 && !strcmp(argv[1], "request")) return disc_update_request(argv[2], argv[3]) ? 1 : 0;
    fprintf(stderr, "Usage: update-tool stage SLOT WORK KEYS NAME BOOT_PROGRAM FILE [LENGTH] | request FILE ACTION\n");
    return 2;
}
