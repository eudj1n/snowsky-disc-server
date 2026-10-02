#ifndef DISC_LOG_H
#define DISC_LOG_H
/* The service's own messages (combined-008): written to standard error as
 * before and kept, the newest DISC_LOG_KEEP, for the diagnostics document.
 * Messages name no track, path of a user's file or credential. */
#include "jsonutil.h"

#define DISC_LOG_KEEP 32
#define DISC_LOG_LINE 200

void disc_log(const char *format, ...) __attribute__((format(printf, 1, 2)));
/* The kept messages as a JSON array [{"t":<unix>,"m":"..."}], oldest first. */
void disc_log_json(disc_buffer *out);

#endif
