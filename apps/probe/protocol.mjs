export function encode(tag, payload = '') {
  if (!/^[0-9a-f]{4}$/i.test(tag)) throw new Error('Invalid tag');
  const size = 8 + new TextEncoder().encode(payload).length;
  if (size > 65535) throw new Error('Record too large');
  return tag.toLowerCase() + size.toString(16).toUpperCase().padStart(4, '0') + payload;
}
export function compatible(profile, handshake, settings) {
  return profile && profile.schema === 1 && profile.api === 1 &&
    /^[0-9a-f]{4}$/.test(profile.protocol_identity) &&
    Number.isInteger(profile.main_os_version) && profile.main_os_version > 0 &&
    /^[0-9a-f]{64}$/.test(profile.profile_sha256) &&
    handshake === profile.protocol_identity &&
    settings && settings.soc_version === profile.main_os_version;
}
export function decode(text) {
  if (!/^[0-9a-f]{8}/i.test(text) || parseInt(text.slice(4, 8), 16) !== new TextEncoder().encode(text).length) throw new Error('Invalid record');
  return {tag: text.slice(0, 4).toLowerCase(), payload: text.slice(8)};
}
export function playbackObservation(payload, previous = {}) {
  const value = payload ? JSON.parse(payload) : {};
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid playback');
  const next = {...previous};
  if ('state' in value) {
    if (!Number.isInteger(value.state)) throw new Error('Invalid state');
    next.state = value.state;
  }
  if ('song' in value) {
    const song = typeof value.song === 'string' ? JSON.parse(value.song) : value.song;
    if (!song || typeof song !== 'object' || Array.isArray(song)) throw new Error('Invalid song');
    if ('song_name' in song && typeof song.song_name !== 'string') throw new Error('Invalid title');
    next.title = song.song_name ?? null;
  } else if (value.state === 2) next.title = null;
  return next;
}
export class ReadSession {
  constructor(socket, onEvent = () => {}) {
    this.socket = socket; this.onEvent = onEvent; this.pending = null; this.closed = false;
    socket.addEventListener('message', event => {
      if (this.closed) return;
      if (typeof event.data !== 'string') return this.onEvent({tag: 'binary', payload: 'Binary record received'});
      try {
        const record = decode(event.data);
        this.onEvent(record);
        if (this.pending?.expected === record.tag) {
          const pending = this.pending; this.pending = null; clearTimeout(pending.timer); pending.resolve(record.payload);
        }
      } catch (error) { this.fail(error); this.close(); }
    });
    socket.addEventListener('close', () => { this.closed = true; this.fail(new Error('Disconnected')); });
  }
  fail(error) { if (this.pending) { clearTimeout(this.pending.timer); this.pending.reject(error); this.pending = null; } }
  read(tag, expected, payload = '', timeout = 4000) {
    if (this.closed || this.socket.readyState !== 1) return Promise.reject(new Error('Disconnected'));
    if (this.pending) return Promise.reject(new Error('Busy'));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        // FiiO has no request IDs: a late reply cannot safely serve a new read,
        // so an unanswered query retires the connection. a202 is the exception,
        // as in the reference controller session: it is also an unsolicited
        // state push and stays silent while stopped or at final EOF, so the
        // observation becomes unknown and the connection stays open.
        this.fail(new Error('No observed response'));
        if (expected !== 'a202') this.close();
      }, timeout);
      this.pending = {expected, resolve, reject, timer};
      try { this.socket.send(encode(tag, payload)); } catch (error) { this.fail(error); }
    });
  }
  close() { this.closed = true; this.fail(new Error('Disconnected')); this.socket.close(); }
}
