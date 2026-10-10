# M3U lists on stock V2.57 and automatic playlists

The owner asked whether automatic playlists (an artist's most played tracks,
first) could be M3U files in the service's own folder instead of stock
playlists kept up to date by position edits. This page records how the V2.57
player treats M3U lists, from the disposable guest and from its binary, and
what follows for us. Nothing here was run on a physical player.

Evidence: `tests/integration/m3u_playback.py` (`python3 scripts/integration.py
--m3u`, disposable guest, generated media only; the JSON lands in ignored
`work/m3u-research.json`), stock's own log `/work/mq_player.log` in the guest,
and the strings and command table of the fingerprinted V2.57 `mq_player`
(read from a copy of the reference project's Ghidra project in ignored
`work/m3u-re/`; the reference repository is unchanged).

## Stock plays an M3U by its path

`0101` with list type `0004` and the path of an `.m3u` or `.m3u8` file (the
folder play the page already uses; the card catalog's `play_all` admits it)
makes stock parse the file (`m3u_parse.c: m3u_open: parsed N songs`) into its
queue table and play the first entry. `0100` with a zero-based position and
the same argument starts at that entry (position 2 played the third). Next and
previous move within the list.

- The file is read when it is played. The queue it becomes is a snapshot: a
  list rewritten while it plays changes nothing until it is played again.
- No scan is involved. A scan never imports an M3U file (no playlist rows, no
  `SONG.IS_M3U` rows), and an entry need not be in the library: a file copied
  after the last scan played from a list.
- The service's `.disc/playlists/` works. Neither of stock's folder browsers
  (`/localdir`, the one the device's file view uses, nor `/dir`) lists `.disc`
  at the card root, so the device cannot open such a list itself.
- Stock's queue rows carry `SONG_TYPE` 4, `IS_M3U` 1, the full card path and an
  `M3U_PATH` of `<list path>/<file name>`.

## Forms stock accepts

| List | Result |
| --- | --- |
| `#EXTM3U` and entries relative to the card root (`Album/01.flac`, or `/Album/01.flac`) | Plays; the path comes back as `/tmp/sdcard/Album/01.flac` |
| No header, CRLF line ends, a UTF-8 BOM, `.m3u8` | Plays the same |
| `#EXTINF:25,Artist - Other name` lines | Ignored: stock names the entry from its tags |
| Cyrillic paths | Play |
| 600 entries | All 600 queued |
| Absolute paths (`/tmp/sdcard/Album/01.flac`) | Stock prepends the card root again (`/tmp/sdcard/tmp/sdcard/...`); nothing plays |
| Paths relative to the list (`../../Album/01.flac`) | Play, but come back as `/tmp/sdcard/.disc/playlists/../../Album/01.flac`, which matches no library path |
| An entry whose file is missing | Stays in the queue; next skips it |
| A CUE image (`Album/Image.flac`) | Plays the whole image, not its tracks |
| A CUE sheet (`Album/Image.cue`) | Does not play |

So entries are relative to the card root, and a CUE track cannot be listed
(an M3U addresses whole files).

## What stock and the service report

- Now playing (`a202`): `id` 700000000 plus the position, `pos_id`,
  `song_name` from the tags, `is_m3u` true, `m3u_file_path`
  `<list path>/<file name>`, and the `love` flag.
- The service's play history records a list play with context type 4, the
  queue's size and the order-independent hash of its paths, which equalled the
  hash of the list's entries: a client that knows its lists can name the
  source of such plays. `folder` is set when every entry shares one folder.
- Favorites: `0104` while a list plays stores the favorite with `IS_M3U` 1 and
  that `M3U_PATH`. Stock matches favorites on path and `IS_M3U`, so the same
  file played from its folder shows as not a favorite, and the two contexts
  keep separate favorites. Unsetting in the list context removes the row.

## Stock's own import and export

The binary has `comm_handler_import_m3u` (command `0111`: JSON `path`,
`start`, `end`, `auto`; it collects M3U files under a folder, creates one
custom playlist per file named after it, keeps its `M3U_PATH`, prefixes
relative entries with the card root and stops at "playlist full, imported
N/M") and `comm_handler_export_m3u` (`0112`: writes custom playlists to
`/tmp/sdcard/export_m3u`). Both sit in the player's command table
(`0111`/`0112` next to `0110`/`0117`), but the protocol link refuses them
(`fiio_link.c: cmd invalid, cmd=0111...`): they serve the device's own
interface. A client cannot turn an M3U into a stock playlist; the player's
owner may be able to from its menu (not checked).

## Stock cannot add to the current queue

The owner asked (2026-09-29) whether a client could add a track or an album
to the queue ("play next"); the device's own menus offer no such action. The
V2.57 `mq_player` (same Ghidra copy, read-only) has none either:

- Every play of a source, `0101` and the device's menus alike, goes through
  `add_songs_to_curlist_server` (`playlist_server.c`), which drops
  `LIST_SONG_0`, creates it again and fills it from one source: all songs,
  an artist, an album, a folder, a playlist, the favorites, an artist's or a
  genre's album, a genre, and three built-in orders (types 11 to 13: by
  `PLAY_COUNT`, by `LAST_PLAY_TIME`, by newest `ID`, each with a limit;
  stock never writes play counts, and whether the link reaches these types
  is not checked). A play always replaces the queue.
- The batch add behind "add to playlist" (`player_handle_add_song_to_list_batch`,
  JSON `src_list`, `dst_list`, `custom_list_id`, `start`, `end`, `artist`,
  `album`, `style`, `path`) accepts only a custom playlist (5, with
  `custom_list_id`) or the favorites (6) as its destination; anything else is
  `bad dst_list`. The link's `/add_custom_list/` reaches the same rule.
- No record of the link's command table edits the queue.

So "play next" on the player means a new queue: the current queue up to the
playing track, the new tracks, then the rest, as an M3U list (or a managed
playlist through `/add_custom_list/`, which accepts `curlist/song` ranges as
a source), played from the current track and sought to its position; the
current track restarts briefly. In a browser it is a plain queue edit, but a
queue built that way can return to the player only as such a list.

## Long lists, a visible folder, swaps and the queue over a pause (combined-009)

`python3 scripts/integration.py --queue-research` (`tests/integration/queue_research.py`,
2026-09-29, generated media only; evidence in ignored `work/queue-research.json`).
Three tracks start with a 3-second 1760 Hz marker and go on with a tone of
their own, so the PCM stock writes to the emulator's audio capture shows what
sounded and for how long (20 ms windows). Timings are the emulator's (stock
runs on the host CPU through qemu-user); the player is slower.

- **Long lists.** 2,000 and 5,000 entries (63 and 158 KB) were parsed whole
  (`m3u_open: parsed 5000 songs`), queued as 5,000 rows and played.
- **A visible folder.** Stock's playback browser (`/localdir/`, which the
  device's folder screen uses) lists `Playlists/Visible.m3u` with `is_m3u:
  true`; the transfer browser (`/dir/`) lists nothing there. Selecting it by
  its position in that folder (`0100` with list type `0004` and the folder),
  as the page plays a single file, and folder play of the folder both queue
  the list file itself as one row and play nothing. Played by its path, as
  from `.disc/playlists`, it plays. A scan imported nothing from it (no song,
  no playlist). On the owner's player (2026-09-29) the device's own file
  browser opened `Playlists/Disc Test.m3u` as a list of its six entries in
  order and played it from the first; it showed the entries' file names
  decoded as a single-byte code page (é as "й", ê as "к", UTF-8 read as
  cp1251) while it found the files. The player's interface and player both
  carry a character-set detector (Mozilla's, `nsUTF8Prober`,
  `nsSingleByteCharSetProber`); with a UTF-8 BOM the same list showed the
  names right and played, while an `.m3u8` copy without it was decoded
  wrongly as well, so the service writes every list with a BOM. Entries must be in the card's own
  Unicode form: macOS lists accented names decomposed (NFD), the card keeps
  them composed (NFC).
- **Swapping the queue.** From the folder's second track at 12 s to a list
  starting at the same track with a seek back to 12 s: with the seek sent in
  the same session right after the selection, nothing of the track's start
  was heard, only about 80 ms of silence; with the seek after stock's `a202`
  (22 ms here) or 500 ms later, about 0.4 s of the start was heard. Selection
  (`0100`, class `selection`) and seek (`0103`, class `seek`) pace
  separately, so the gateway does not delay the seek. The same order back to
  the folder's queue behaves alike. Stock ignores a selection that comes
  within about 2.1 s of the previous one (its navigation gate, known from the
  reference): in the service's acceptance a selection 0.1 s after the list
  started was dropped and only the seek applied, to the first track; after
  2.5 s it switched.
- **A pause.** After 20 s paused, `0100` with a position and list type `0000`
  plays that row of the same queue from its start; the queue is unchanged.
- **An idle power-off.** Paused, the guest powered itself off after 305 s
  (`POWER_SAVE` 300; the owner's setting may differ). Protocol reads do not
  keep it on (docs/idle-supervision.md). After a boot stock reported the same
  track paused, `LIST_SONG_0` kept the queue, `MEMORY_PLAY` held the queue row
  and the position (23,000 ms), and `0100` with the position and `0000` played
  that row of the same queue.

For the page's player/browser switch: back to the player is the current
queue's row selected and sought at once (no start heard); a browser playing
longer than the player's power-save time finds the player off, and a person
switches it on before the page can hand playback back; the queue and track
survive that. For "play next": a list of the current queue with the new
tracks after the playing one, selected at the playing row and sought to its
position at once, continues the track after about 80 ms of silence.

### Favorites by context (combined-009)

`queue_research.py favorites` (2026-09-29): the heart pressed on the player
while a list plays stored the file with `TRACK` 0, `IS_M3U` 1 and the list's
`M3U_PATH`; the library's row of that file had `TRACK` 1. Stock's queue rows
carry the tag's track number when a library source plays (album, artist,
genre) and 0 when a folder or a list plays, and its favorite is found by
path, track number and `IS_M3U`. MY_LOVE is `UNIQUE(PATH, TRACK)`, so:

- the library's favorite (track number n) and a list's (0) can both exist:
  on a copy of song.db the library form went in beside the list's row, and
  the service's `favorite_add` added it (`already: false`);
- a folder's and a list's favorite of the same file (both 0) cannot: with the
  list's row present, the heart pressed while the folder played stored
  nothing and stock kept `love: false`;
- a file without a track number has 0 in the library too, so its library
  favorite and a list's collide the same way.

`favorite_add` therefore matches the library's context (path, track number
and `IS_M3U`) and answers 409 when stock's constraint refuses the row.

## What this means for automatic playlists

An M3U file in `.disc/playlists/` is a better base than a managed stock
playlist:

- An update is one file write: no position edits, no pacing between them, no
  scan, no bound of the stock playlist editor, and entries may be files the
  library has not indexed yet.
- The lists stay in the service's folder: the card's own folders, stock's
  library and its playlist list are untouched.
- The page plays a list with the command it already sends for folders, and
  maps plays back to the list through the history hash.

Limits to design around:

- The device itself cannot see these lists (hidden folder, no import over the
  link). If the owner wants them on the device too, a visible folder such as
  `Playlists/` would show them in its file view, at the cost of files in the
  card's own space.
- CUE tracks cannot be entries; an automatic list leaves them out (or adds the
  whole image, which plays every track).
- A favorite set from the player while a list plays is a separate stock
  favorite (`IS_M3U` 1). The page should favorite such a track through the
  service's `favorite_add`, which copies the library's row (its `IS_M3U`),
  rather than stock's `0104`. That route matches an existing favorite on path
  and track only, so it reports a list-context favorite of the same file as
  already set; a next image should match stock's rule (`IS_M3U` too) so the
  library form is added.
- A list is a snapshot when played: a list refreshed during playback applies
  the next time it is played.

### The page's automatic playlists (2026-09-30, owner's decisions)

Five kinds, 50 tracks each, as external lists in `Playlists/`, which the
player's own file browser opens and plays without the page: an artist's most
played tracks (made from the artist page), the most played overall, recently
added, long not played, and a daily mix (owner, 2026-09-30: half loved, half
rarely or never heard, shuffled by the date, so it changes once a day). The page recomputes them from the library and the
service's history when it opens (paired) and on request, and writes only the
lists whose entries changed. A list stays as written for its period (owner,
2026-09-30: a day by default, or a week or a month), so new plays do not
reshuffle it within the period; each record keeps the day it was written. Which lists are automatic is the store's
`auto_playlists` collection, so the owner's own lists in that folder are
never rewritten. Disliked tracks and CUE tracks (an entry is a whole file)
stay out.

## What the service needs (next image)

Uploads admit only music, lyrics and covers, and stock cannot import lists
over the link, so writing a list needs a route of its own, for example:

- `GET /api/lists` (names, entry counts, times) and `PUT`/`DELETE
  /api/lists/<name>` with the player's serial number, a fresh request ID and
  pacing, like every change;
- a name of a bounded set of characters (the file becomes
  `.disc/playlists/<name>.m3u`); up to a bound of entries (1,000, say), each a
  path of an existing regular music file under the card root, stored
  root-relative, never `..` or a link;
- written as UTF-8 with `#EXTM3U` into a temporary file, read back and renamed
  over the old one, so stock never reads half a list.

The page then keeps the definitions (the store) and the membership (the play
history), writes lists through the route and plays them by path.
