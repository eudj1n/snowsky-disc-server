# Changelog

What changes for you in the DISC server, release by release. A release is
named after FiiO's firmware and our number for it: 2.57.5 is the fifth server
for FiiO's 2.57. Each published release is a `v<version>` tag with its package
on the [releases page](https://github.com/eudj1n/snowsky-disc-server/releases).
Entries stay short; how and why it was done is in the [plan](docs/plan.md).

## [Unreleased]

- "Player software" says where its packages are installed and removed: in the
  boot menu on the player, at power-on.

- "Player software" in the manager also lists the services the boot layer
  runs beside the server (from its next release on), with whether each runs.
- The server finds its own status where the coming boot layer keeps it, and
  where the boot layer installed on your player keeps it now.
- With disc-health installed, the manager's "System" shows the battery, the
  free space in the player and on the card, and the card errors, crashes and
  restarts of the interface since power-on.
- With disc-network installed, it also lists the Wi-Fi networks the player
  keeps, by name, the connected one marked.

## [2.57.5] — 2026-10-08

- The boot layer's installer now puts
  [Disc Player 1.0.0](https://github.com/eudj1n/snowsky-disc-player/releases/tag/v1.0.0),
  its first public release, on the card as the app that opens at the player's
  address. The server itself is 2.57.4's, the same build.

## [2.57.4] — 2026-10-07

The first published release. It holds the work of 2.57.1 to 2.57.3, which ran
only on the developer's player.

### Apps on the card

- Disc Player, or any web app, lives in the card's `Apps` folder and opens at
  the player's address (port 7870); with several apps, you choose which.
- The boot layer's installer puts the apps this server offers on the card;
  the server's package carries only their list.

### The player in the browser

- Apps browse the library and control playback, volume, the queue, play modes,
  favorites and the sound settings through the player's own protocol, only
  with the commands a reviewed list allows, every change checked against the
  player's answer; changes need the player's serial number.
- Play history, playlists as M3U files, pins, dislikes and the apps' own data
  are kept on the card for every browser.
- The card's files: uploads and folders, covers, lyrics and audio facts, a
  card's file played in the browser, and a trash that restores what was moved
  there.

### The application manager

- A page of its own on port 7871: the installed apps, installing one from a
  zip (replacing an app of the same name only once the new one is checked),
  removing apps and choosing the one at the address.
- "Player software": this server, the menu and the interfaces installed
  beside the player's own, as the boot layer reports them.
- "Server updates": a signed `.update` file is checked as it arrives, kept
  beside the running version and switched to on request, without stopping
  the music; the page reloads itself once the new version is confirmed, and
  returns to the previous one on request.
- English and Russian, the player's own language at first; the boot layer's
  state in words under "System"; links to each project's page.
