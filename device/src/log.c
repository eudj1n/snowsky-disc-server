#include "log.h"
#include "framing.h"
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

static struct { long long at; char text[DISC_LOG_LINE]; } kept[DISC_LOG_KEEP];
static unsigned next, count;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;

void disc_log(const char *format, ...) {
    char text[DISC_LOG_LINE];
    va_list args;
    va_start(args, format);
    vsnprintf(text, sizeof(text), format, args);
    va_end(args);
    size_t n = strlen(text);
    while (n && text[n - 1] == '\n') text[--n] = 0;
    fprintf(stderr, "%s\n", text);
    pthread_mutex_lock(&lock);
    kept[next].at = (long long)time(NULL);
    memcpy(kept[next].text, text, n + 1);
    next = (next + 1) % DISC_LOG_KEEP;
    if (count < DISC_LOG_KEEP) count++;
    pthread_mutex_unlock(&lock);
}

void disc_log_json(disc_buffer *out) {
    disc_buffer_text(out, "[");
    pthread_mutex_lock(&lock);
    for (unsigned i = 0; i < count; i++) {
        unsigned slot = (next + DISC_LOG_KEEP - count + i) % DISC_LOG_KEEP;
        const char *text = kept[slot].text;
        size_t n = strlen(text);
        if (i) disc_buffer_text(out, ",");
        disc_buffer_text(out, "{\"t\":");
        disc_buffer_int(out, kept[slot].at);
        disc_buffer_text(out, ",\"m\":");
        /* A cut may split a character: such a message is shown without its text. */
        if (disc_utf8((const unsigned char *)text, n)) disc_buffer_string(out, text, n);
        else disc_buffer_text(out, "null");
        disc_buffer_text(out, "}");
    }
    pthread_mutex_unlock(&lock);
    disc_buffer_text(out, "]");
}
