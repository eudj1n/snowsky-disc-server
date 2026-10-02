#ifndef DISC_MEDIA_H
#define DISC_MEDIA_H

#include <stddef.h>

/* Read-only metadata of music files on the card: FLAC STREAMINFO, Vorbis
 * comments, embedded pictures and lyrics, WAV format, MP3 (ID3v2 2.2-2.4,
 * ID3v1, MPEG frame headers with Xing/Info or VBRI), MP4/M4A atoms (iTunes
 * tags, AAC and ALAC sample entries), ADTS AAC, folder covers and same-stem
 * .lrc sidecars. Files are reached one component at a time below
 * the card root without following links, and only audio extensions are
 * admitted, so tokens, markers and releases on the card stay unreadable. */

#define DISC_MEDIA_MAX_IMAGE (8u * 1024u * 1024u)
#define DISC_MEDIA_MAX_LYRICS (256u * 1024u)
#define DISC_MEDIA_MAX_PATH 1024

typedef struct {
    int present;
    long long offset;
    size_t length;
    char mime[16];
    /* Lyrics text as stored: 0 bytes as they are (UTF-8 or unknown), or an
     * ID3v2 text encoding plus one (1..4); unsync marks ID3 unsynchronisation. */
    int encoding, unsync;
} disc_media_blob;

typedef struct {
    char format[8];
    long long bytes;
    unsigned sample_rate, bits, channels;
    /* Average kbit/s of lossy streams (MP3, AAC), 0 when unknown. */
    unsigned bit_rate;
    int has_duration;
    unsigned long long duration_ms;
    char title[256], artist[256], album[256], album_artist[256], genre[128];
    char track[16], disc[16], date[32];
    disc_media_blob picture;
    unsigned picture_type;
    disc_media_blob lyrics;
} disc_media_info;

/* Opens an audio file by its absolute card path ("<root>/A/B.flac"). On
 * success returns 0, the open file, its parent directory and the base name.
 * Returns 403 for a path outside the card, an unsafe component or a
 * non-audio extension, 404 when missing and 503 when the card is unavailable. */
int disc_media_open(const char *root, const char *path, int *dir_fd, int *file_fd, char *name, size_t name_capacity);
/* Reads what the format declares; unknown formats keep only size and name. */
void disc_media_probe(int fd, const char *name, disc_media_info *info);
/* The same from headers only: an ADTS AAC file's duration (which needs every
 * frame counted) stays unknown. For walks over the whole card (combined-009). */
void disc_media_probe_quick(int fd, const char *name, disc_media_info *info);
/* 1 when the name has an audio extension the media routes admit. */
int disc_media_audio_name(const char *name);
/* A folder cover (cover/folder/front, jpg or png) next to the file. */
int disc_media_folder_cover(int dir_fd, int *fd, disc_media_blob *blob);
/* Embedded lyrics bytes (read from the blob's offset) as UTF-8: decodes ID3
 * text encodings and unsynchronisation; returns a malloc'd copy or NULL when
 * the bytes stay as they are. */
char *disc_media_lyrics_utf8(const disc_media_blob *lyrics, const unsigned char *raw, size_t length, size_t *out_length);
/* The same-stem .lrc next to the file. */
int disc_media_sidecar_lyrics(int dir_fd, const char *name, int *fd, size_t *size);
/* The info document; returns its length or 0 when it does not fit. */
size_t disc_media_json(const disc_media_info *info, const char *path, int folder_cover, int sidecar, char *out, size_t capacity);
/* Percent-encodes a path for stock like Python quote(path, safe='/'). */
int disc_media_encode_path(const char *path, char *out, size_t capacity);

#endif
