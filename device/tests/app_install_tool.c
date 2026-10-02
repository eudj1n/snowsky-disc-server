/* The conformance tests' driver for apps.c: installs a zip or removes an app as
 * the application manager does, without HTTP, so each zip rule can be checked
 * on its own (tests/conformance/test_app_install.py). One line on stdout:
 * "ok\t<name>\t<files>\t<bytes>" or "<code>\t<problem>". */
#include "apps.h"
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    char problem[512] = "";
    if (argc == 4 && !strcmp(argv[1], "install")) {
        disc_app_result r;
        int code = disc_app_install_zip(argv[2], argv[3], &r, problem, sizeof(problem));
        if (code == DISC_APP_OK) printf("ok\t%s\t%d\t%lld\n", r.name, r.files, r.bytes);
        else printf("%d\t%s\n", code, problem);
        return 0;
    }
    if (argc == 4 && !strcmp(argv[1], "remove")) {
        int code = disc_app_remove(argv[2], argv[3], problem, sizeof(problem));
        if (code == DISC_APP_OK) printf("ok\n");
        else printf("%d\t%s\n", code, problem);
        return 0;
    }
    fprintf(stderr, "Usage: app-install-tool install APPS ZIP | remove APPS NAME\n");
    return 2;
}
