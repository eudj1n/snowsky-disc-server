#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#include "facts.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif

/* One small attribute file (sysfs, procfs) into a NUL-terminated buffer. */
static int attribute(int dir, const char *name, char *out, size_t capacity) {
    int fd = openat(dir, name, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    size_t used = 0;
    while (used + 1 < capacity) {
        ssize_t n = read(fd, out + used, capacity - 1 - used);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        used += (size_t)n;
    }
    close(fd);
    out[used] = 0;
    return used > 0;
}

static int integer(int dir, const char *name, long long *value) {
    char text[32], *end;
    if (!attribute(dir, name, text, sizeof(text))) return 0;
    errno = 0;
    long long parsed = strtoll(text, &end, 10);
    if (errno || end == text || (*end && *end != '\n')) return 0;
    *value = parsed;
    return 1;
}

int disc_battery_read(const char *path, disc_battery *battery) {
    memset(battery, 0, sizeof(*battery));
    if (!path) return 0;
    /* The class entry is a sysfs link to the device; it is opened as the profile names it. */
    int dir = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (dir < 0) return 0;
    long long value;
    if (integer(dir, "capacity", &value) && value >= 0 && value <= 100) {
        battery->present = 1;
        battery->capacity = (int)value;
        if (integer(dir, "voltage_now", &value) && value > 0 && value < 100000000) {
            battery->has_voltage = 1;
            battery->voltage_uv = value;
        }
        if (integer(dir, "temp", &value) && value > -1000 && value < 2000) {
            battery->has_temperature = 1;
            battery->temperature_dc = (int)value;
        }
        if (integer(dir, "cycle_count", &value) && value >= 0 && value < 100000) {
            battery->has_cycles = 1;
            battery->cycles = (int)value;
        }
    }
    close(dir);
    return 0;
}

/* "key: value" of a procfs status or hw_params text. */
static const char *field(const char *text, const char *key) {
    size_t n = strlen(key);
    for (const char *line = text; line && *line; line = strchr(line, '\n') ? strchr(line, '\n') + 1 : NULL)
        if (!strncmp(line, key, n) && line[n] == ':') {
            line += n + 1;
            while (*line == ' ' || *line == '\t') line++;
            return line;
        }
    return NULL;
}
static void word(const char *from, char *out, size_t capacity) {
    size_t n = 0;
    while (from && from[n] && from[n] != '\n' && from[n] != ' ' && n + 1 < capacity) {
        char c = from[n];
        out[n++] = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' ? c : '?';
    }
    out[n] = 0;
}

int disc_output_read(const char *card, disc_output *output) {
    memset(output, 0, sizeof(*output));
    if (!card) return 0;
    DIR *dir = opendir(card);
    if (!dir) return 0;
    output->present = 1;
    int fd = dirfd(dir), best = -1;
    struct dirent *entry;
    /* Playback streams are pcm<N>p; the lowest-numbered open one is reported
     * (a closed stream's status and hw_params read just "closed"). */
    while ((entry = readdir(dir))) {
        const char *name = entry->d_name;
        size_t n = strlen(name);
        if (n < 5 || n > 7 || strncmp(name, "pcm", 3) || name[n - 1] != 'p') continue;
        int number = 0;
        for (size_t i = 3; i + 1 < n; i++) {
            if (name[i] < '0' || name[i] > '9') { number = -1; break; }
            number = number * 10 + (name[i] - '0');
        }
        if (number < 0 || (best >= 0 && number > best)) continue;
        char path[32], text[512];
        snprintf(path, sizeof(path), "%.7s/sub0/status", name);
        if (!attribute(fd, path, text, sizeof(text))) continue;
        const char *state = field(text, "state");
        if (!state) continue;
        char parsed[16];
        word(state, parsed, sizeof(parsed));
        snprintf(path, sizeof(path), "%.7s/sub0/hw_params", name);
        if (!attribute(fd, path, text, sizeof(text)) || !field(text, "format")) continue;
        best = number;
        memset(output->state, 0, sizeof(output->state));
        output->active = 1;
        snprintf(output->device, sizeof(output->device), "%.7s", name);
        for (size_t i = 0; parsed[i] && i + 1 < sizeof(output->state); i++)
            output->state[i] = (char)(parsed[i] >= 'A' && parsed[i] <= 'Z' ? parsed[i] + 32 : parsed[i]);
        word(field(text, "format"), output->format, sizeof(output->format));
        const char *channels = field(text, "channels"), *rate = field(text, "rate");
        output->channels = channels ? (unsigned)strtoul(channels, NULL, 10) : 0;
        output->rate = rate ? (unsigned)strtoul(rate, NULL, 10) : 0;
    }
    closedir(dir);
    return 0;
}

size_t disc_facts_json(const disc_battery *battery, int has_card, unsigned long long card_total,
                       unsigned long long card_free, const disc_output *output, char *out, size_t capacity) {
    char battery_json[160] = "null", card_json[96] = "null", output_json[192] = "null";
    if (battery->present) {
        char voltage[24] = "null", temperature[24] = "null", cycles[16] = "null";
        if (battery->has_voltage) snprintf(voltage, sizeof(voltage), "%lld", battery->voltage_uv / 1000);
        if (battery->has_temperature) {
            int t = battery->temperature_dc, whole = t / 10, tenth = t % 10;
            snprintf(temperature, sizeof(temperature), "%s%d.%d", t < 0 && whole == 0 ? "-" : "", whole, tenth < 0 ? -tenth : tenth);
        }
        if (battery->has_cycles) snprintf(cycles, sizeof(cycles), "%d", battery->cycles);
        snprintf(battery_json, sizeof(battery_json), "{\"capacity\":%d,\"voltageMv\":%s,\"temperatureC\":%s,\"cycles\":%s}",
                 battery->capacity, voltage, temperature, cycles);
    }
    if (has_card) snprintf(card_json, sizeof(card_json), "{\"totalBytes\":%llu,\"freeBytes\":%llu}", card_total, card_free);
    if (output->present && !output->active) snprintf(output_json, sizeof(output_json), "{\"active\":false}");
    else if (output->active)
        snprintf(output_json, sizeof(output_json),
                 "{\"active\":true,\"device\":\"%s\",\"state\":\"%s\",\"format\":\"%s\",\"rate\":%u,\"channels\":%u}",
                 output->device, output->state, output->format, output->rate, output->channels);
    int n = snprintf(out, capacity, "{\"battery\":%s,\"card\":%s,\"output\":%s}", battery_json, card_json, output_json);
    return n > 0 && (size_t)n < capacity ? (size_t)n : 0;
}
