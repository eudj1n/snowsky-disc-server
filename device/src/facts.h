#ifndef DISC_FACTS_H
#define DISC_FACTS_H

#include <stddef.h>

/* Read-only facts about the player itself, from the sources the reviewed OS
 * profile names (firmware/os/v<version>.json): the fuel gauge in sysfs and the
 * playback streams of the ALSA card in procfs. Nothing here writes. */

typedef struct {
    int present, capacity, has_voltage, has_temperature, has_cycles;
    long long voltage_uv;
    int temperature_dc; /* tenths of a degree Celsius, as the gauge reports */
    int cycles;
} disc_battery;

typedef struct {
    int present, active;
    char device[16], state[16], format[24];
    unsigned rate, channels;
} disc_output;

/* 0 and present=0 when the directory or its capacity is missing. */
int disc_battery_read(const char *dir, disc_battery *battery);
/* The first open playback stream of the card (pcm<N>p/sub0); active=0 when all are closed. */
int disc_output_read(const char *card, disc_output *output);
/* The device document for the given facts; returns its length or 0 when it does not fit. */
size_t disc_facts_json(const disc_battery *battery, int has_card, unsigned long long card_total,
                       unsigned long long card_free, const disc_output *output, char *out, size_t capacity);

#endif
