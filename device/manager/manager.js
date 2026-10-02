// The DISC application manager: the server's own page on its own port (owner, 2026-10-02).
// No app can reach it (another origin); every change needs the player's serial number.
const TEXT = {
  en: {
    title: 'Apps', apps: 'Installed apps', serialTitle: 'Serial number',
    serialHelp: "Changes need the player's serial number (Settings → About on the player).",
    remember: 'Remember on this device', system: 'System', none: 'No app is installed.',
    servedAt: '{app} opens at the player\'s address:', noDefault: 'Several apps are installed: choose the one that opens at the player\'s address.',
    open: 'Open', makeDefault: 'Open at the address', chosen: 'Opens at the address', unset: 'Do not choose',
    remove: 'Remove', removeAsk: 'Remove {app} and its files from the card?', cancel: 'Cancel', removed: '{app} is removed.',
    installTitle: 'Install an app',
    installHelp: 'A zip with one folder named after the app and its index.html inside, at most 40 MB. An installed app of that name is replaced.',
    install: 'Install', uploading: 'Sending {done} of {total}…', unpacking: 'Checking and unpacking on the card…',
    installed: '{app} is installed: {files} files, {size}.',
    tooLarge: 'The zip is larger than 40 MB.', refused: 'The zip was refused: {why}',
    noRoom: 'The card does not have room for the app and the play history. Nothing was installed.',
    lost: 'The connection was lost. The list shows what the player has now.',
    needSerial: 'Enter the serial number first.', wrongSerial: 'The serial number was not accepted. After several wrong tries the player waits a while.',
    busy: 'Another change is running.', noCard: 'The card is not available in the player.',
    saved: 'Saved.', failed: 'Not done: {why}', unreachable: 'The server does not answer.',
    server: 'Server', boot: 'Boot layer', package: 'Package', ports: 'Ports', card: 'Card', cardIn: 'in the player', cardOut: 'not available',
    portsValue: 'apps {apps}, manager {manager}', notBoot: 'not under the boot layer', confirmed: 'confirmed', tentative: 'not confirmed yet',
    updateTitle: 'Server updates',
    updateHelp: 'A server release as a .update file. It is checked as it arrives and kept beside the running version; switching is a separate step that restarts the server, not the music. Uploading replaces the version kept for a return.',
    upload: 'Upload', checking: 'Checking the signature and the files…',
    runningConfirmed: 'Version {version} runs, confirmed.', runningTentative: 'Version {version} runs; it is confirmed after three minutes of steady work.',
    waitConfirmed: 'An update waits until the running version is confirmed.',
    notUnderBoot: 'This server does not run under the boot layer: it takes no updates here.', noKeys: 'This build takes no updates over the network.',
    activate: 'Restart into {version}', activateNone: 'Restart into an uploaded version', rollback: 'Return to {version}', rollbackNone: 'Return to the previous version',
    staged: '{version} is ready: {files} files, {size}. Restart into it when convenient.',
    askActivate: 'Restart the server into {version}? The music does not stop; this page reconnects.',
    askRollback: 'Return the server to {version}? The music does not stop; this page reconnects.',
    confirm: 'Restart', restarting: 'Restarting into {version}…', switched: 'Switched to {version}.',
    confirmedNow: '{version} runs, confirmed.', returned: '{version} did not take over; {running} runs. The boot layer says: {why}',
    noAnswer: 'The server does not answer. A version that fails gives way to the previous one by itself; reload this page in a minute.',
    updateRefused: 'The update was refused: {why}', updateNoRoom: 'The player does not have room for the update. Nothing was changed.',
  },
  ru: {
    title: 'Приложения', apps: 'Установленные приложения', serialTitle: 'Серийный номер',
    serialHelp: 'Для изменений нужен серийный номер плеера (Настройки → Об устройстве на плеере).',
    remember: 'Запомнить на этом устройстве', system: 'Система', none: 'Ни одного приложения не установлено.',
    servedAt: '{app} открывается по адресу плеера:', noDefault: 'Установлено несколько приложений: выберите, какое открывается по адресу плеера.',
    open: 'Открыть', makeDefault: 'Открывать по адресу', chosen: 'Открывается по адресу', unset: 'Не выбирать',
    remove: 'Удалить', removeAsk: 'Удалить {app} и его файлы с карты?', cancel: 'Отмена', removed: '{app} удалено.',
    installTitle: 'Установить приложение',
    installHelp: 'Zip с одной папкой, названной по приложению, и его index.html внутри, не больше 40 МБ. Установленное приложение с тем же именем заменяется.',
    install: 'Установить', uploading: 'Отправлено {done} из {total}…', unpacking: 'Проверка и распаковка на карте…',
    installed: '{app} установлено: файлов {files}, {size}.',
    tooLarge: 'Zip больше 40 МБ.', refused: 'Zip не принят: {why}',
    noRoom: 'На карте не хватает места для приложения и истории прослушиваний. Ничего не установлено.',
    lost: 'Соединение прервалось. Список показывает, что сейчас есть на плеере.',
    needSerial: 'Сначала введите серийный номер.', wrongSerial: 'Серийный номер не принят. После нескольких ошибок плеер какое-то время не принимает попытки.',
    busy: 'Уже идёт другое изменение.', noCard: 'Карта недоступна в плеере.',
    saved: 'Сохранено.', failed: 'Не выполнено: {why}', unreachable: 'Сервер не отвечает.',
    server: 'Сервер', boot: 'Слой загрузки', package: 'Пакет', ports: 'Порты', card: 'Карта', cardIn: 'в плеере', cardOut: 'недоступна',
    portsValue: 'приложения {apps}, менеджер {manager}', notBoot: 'не под слоем загрузки', confirmed: 'подтверждён', tentative: 'ещё не подтверждён',
    updateTitle: 'Обновления сервера',
    updateHelp: 'Выпуск сервера в виде файла .update. Он проверяется по мере загрузки и хранится рядом с работающей версией; переключение — отдельный шаг, который перезапускает сервер, но не музыку. Загрузка заменяет версию, сохранённую для возврата.',
    upload: 'Загрузить', checking: 'Проверка подписи и файлов…',
    runningConfirmed: 'Работает версия {version}, подтверждена.', runningTentative: 'Работает версия {version}; она подтверждается после трёх минут стабильной работы.',
    waitConfirmed: 'Обновление ждёт, пока работающая версия не будет подтверждена.',
    notUnderBoot: 'Сервер работает не под слоем загрузки: обновления здесь не принимаются.', noKeys: 'Эта сборка не принимает обновления по сети.',
    activate: 'Перезапустить в {version}', activateNone: 'Перезапустить в загруженную версию', rollback: 'Вернуться к {version}', rollbackNone: 'Вернуться к предыдущей версии',
    staged: '{version} готова: файлов {files}, {size}. Перезапустите в неё, когда удобно.',
    askActivate: 'Перезапустить сервер в {version}? Музыка не прервётся; страница переподключится.',
    askRollback: 'Вернуть сервер к {version}? Музыка не прервётся; страница переподключится.',
    confirm: 'Перезапустить', restarting: 'Перезапуск в {version}…', switched: 'Переключено на {version}.',
    confirmedNow: 'Работает {version}, подтверждена.', returned: '{version} не заработала; работает {running}. Слой загрузки сообщает: {why}',
    noAnswer: 'Сервер не отвечает. Неудачная версия сама уступает место предыдущей; обновите страницу через минуту.',
    updateRefused: 'Обновление не принято: {why}', updateNoRoom: 'На плеере не хватает места для обновления. Ничего не изменено.',
  },
}
const ru = (navigator.language || '').toLowerCase().startsWith('ru')
const t = (key, values = {}) => (ru ? TEXT.ru : TEXT.en)[key].replace(/\{(\w+)\}/g, (_, name) => values[name] ?? '')
document.documentElement.lang = ru ? 'ru' : 'en'
document.title = `DISC · ${t('title')}`
for (const element of document.querySelectorAll('[data-text]')) element.textContent = t(element.dataset.text)

const $ = (id) => document.getElementById(id)
// Each section answers in its own line: the list's changes under the list, an installation under its form.
const say = (text, bad = false, where = 'apps-status') => {
  const line = $(where)
  line.textContent = text
  line.className = bad ? 'status bad' : 'status'
}
const size = (bytes) => {
  const units = ru ? ['Б', 'КБ', 'МБ'] : ['B', 'KB', 'MB']
  let value = bytes, unit = 0
  while (value >= 1000 && unit < units.length - 1) { value /= 1000; unit++ }
  return `${value.toLocaleString(ru ? 'ru' : 'en', { maximumFractionDigits: unit ? 1 : 0 })} ${units[unit]}`
}

const SERIAL_KEY = 'disc-manager.serial'
try {
  const saved = localStorage.getItem(SERIAL_KEY)
  if (saved) { $('serial').value = saved; $('remember').checked = true }
} catch { /* storage may be unavailable */ }
const keepSerial = () => {
  try {
    const value = $('serial').value.trim()
    if ($('remember').checked && value) localStorage.setItem(SERIAL_KEY, value)
    else localStorage.removeItem(SERIAL_KEY)
  } catch { /* storage may be unavailable */ }
}
$('remember').addEventListener('change', keepSerial)
$('serial').addEventListener('change', keepSerial)
$('serial-form').addEventListener('submit', (event) => event.preventDefault())

// One change at a time: while one runs, every control that changes something waits, disabled.
let busy = false
const setBusy = (value) => {
  busy = value
  for (const element of document.querySelectorAll('[data-change]')) element.disabled = busy
  $('archive').disabled = busy
  $('install').disabled = busy || !$('archive').files.length
  renderUpdate()
}

const requestId = () => {
  const random = new Uint32Array(2)
  crypto.getRandomValues(random)
  return `manager-${Date.now().toString(36)}-${random[0].toString(36)}${random[1].toString(36)}`
}
const serial = (where) => {
  const value = $('serial').value.trim()
  if (!value) { say(t('needSerial'), true, where); $('serial').focus() }
  return value
}
// The page's words for what the server answered; its own text where it names a rule.
const KNOWN = {
  'Another change is running': 'busy', 'This server does not run under the boot layer': 'notUnderBoot',
  'This build carries no update keys': 'noKeys', 'The running version is not confirmed yet; an update waits for it': 'waitConfirmed',
}
function failure(code, text, kind = 'app') {
  if (code === 403) return t('wrongSerial')
  if (KNOWN[text]) return t(KNOWN[text])
  if (code === 413 && kind === 'app') return t('tooLarge')
  if (code === 422) return t(kind === 'app' ? 'refused' : 'updateRefused', { why: text })
  if (code === 503) return t('noCard')
  if (code === 507) return t(kind === 'app' ? 'noRoom' : 'updateNoRoom')
  return t('failed', { why: text || code })
}
async function get(path) {
  const response = await fetch(path, { cache: 'no-store' })
  if (!response.ok) throw new Error(`${response.status}`)
  return response.json()
}
async function change(method, path, body, where = 'apps-status') {
  say('', false, where)
  const token = serial(where)
  if (!token) return null
  setBusy(true)
  try {
    const response = await fetch(path, {
      method, cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-Disc-Token': token, 'X-Disc-Request': requestId() },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (!response.ok) { say(failure(response.status, (await response.text()).trim(), where === 'update-status' ? 'update' : 'app'), true, where); return null }
    return await response.json()
  } catch {
    say(t('lost'), true, where)
    await load()
    return null
  } finally { setBusy(false) }
}

let about = null
const appsAddress = (path) => `${location.protocol}//${location.hostname}:${about?.ports?.apps ?? 7870}${path}`

function fact(list, term, value) {
  const dt = document.createElement('dt'); dt.textContent = term
  const dd = document.createElement('dd'); dd.textContent = value
  list.append(dt, dd)
}
function renderFacts() {
  const list = $('facts'); list.replaceChildren()
  const service = about.service
  $('server').textContent = `${service.name} ${service.version} · ${service.build}`
  fact(list, t('server'), `${service.version} (${service.build})`)
  const boot = about.boot
  if (!boot) fact(list, t('boot'), t('notBoot'))
  else {
    const decision = boot.decision
    if (decision) fact(list, t('boot'), `${decision.mode} · ${decision.reason}`)
    const role = boot.service
    if (role) fact(list, t('package'), [role.name, role.version, role.slot, role.confirmed ? t('confirmed') : t('tentative')].filter(Boolean).join(' · '))
  }
  fact(list, t('ports'), t('portsValue', about.ports))
  fact(list, t('card'), about.card?.owned ? t('cardIn') : t('cardOut'))
}
function button(label, action, kind = '', settled = false) {
  const element = document.createElement('button')
  element.type = 'button'; element.textContent = label
  if (kind) element.className = kind
  // A choice already in effect stays in place, disabled; the others wait while a change runs.
  if (settled) element.disabled = true
  else { element.dataset.change = ''; element.disabled = busy }
  element.addEventListener('click', action)
  return element
}
function renderApps(apps) {
  const list = $('apps'); list.replaceChildren()
  const note = $('apps-note')
  note.replaceChildren(!apps.apps.length ? t('none') : apps.default ? t('servedAt', { app: apps.default }) : t('noDefault'))
  if (apps.default) {
    const link = document.createElement('a'); link.href = appsAddress('/'); link.textContent = link.href
    note.append(' ', link)
  }
  for (const app of apps.apps) {
    const item = document.createElement('li')
    const name = document.createElement('span'); name.className = 'name'; name.textContent = app.name
    const version = document.createElement('span'); version.className = 'version'; version.textContent = app.version ?? ''
    item.append(name, version)
    if (app.default) { const mark = document.createElement('span'); mark.className = 'mark'; mark.textContent = t('chosen'); item.append(mark) }
    const actions = document.createElement('span'); actions.className = 'actions'
    const open = document.createElement('a'); open.textContent = t('open'); open.href = appsAddress(`/apps/${encodeURIComponent(app.name)}/`)
    actions.append(open)
    if (apps.chosen === app.name) actions.append(button(t('unset'), () => choose(null)))
    else actions.append(button(t('makeDefault'), () => choose(app.name), '', app.default))
    actions.append(button(t('remove'), () => ask(item, actions, app.name), 'quiet'))
    item.append(actions)
    list.append(item)
  }
}
// Removing asks once, in place: the row's actions give way to the question until answered.
function ask(item, actions, app) {
  const question = document.createElement('span'); question.className = 'actions confirm'
  const text = document.createElement('span'); text.textContent = t('removeAsk', { app })
  const back = () => { question.replaceWith(actions); actions.querySelector('button.quiet')?.focus() }
  const no = button(t('cancel'), back)
  question.append(text, button(t('remove'), () => remove(app), 'danger'), no)
  actions.replaceWith(question)
  no.focus()
}
async function remove(app) {
  const apps = await change('DELETE', `/api/apps/${encodeURIComponent(app)}`)
  if (apps) { renderApps(apps); say(t('removed', { app })) } else await load()
}
async function choose(name) {
  const apps = await change('PUT', '/api/apps/default', { name })
  if (apps) { renderApps(apps); say(t('saved')) }
}

// A file goes up with its progress shown, then the server checks it; a lost connection is not taken
// for a failure or a success: what the player has is read again.
function send({ path, file, type, token, row, bar, text, checking, where, kind, done }) {
  setBusy(true); say('', false, where)
  row.hidden = false; bar.value = 0
  text.textContent = t('uploading', { done: size(0), total: size(file.size) })
  const xhr = new XMLHttpRequest()
  xhr.open('POST', path)
  xhr.setRequestHeader('Content-Type', type)
  xhr.setRequestHeader('X-Disc-Token', token)
  xhr.setRequestHeader('X-Disc-Request', requestId())
  xhr.upload.addEventListener('progress', (progress) => {
    bar.value = progress.lengthComputable ? progress.loaded / progress.total : 0
    text.textContent = t('uploading', { done: size(progress.loaded), total: size(file.size) })
  })
  xhr.upload.addEventListener('load', () => { bar.removeAttribute('value'); text.textContent = checking })
  const finish = async (message, bad) => {
    row.hidden = true
    setBusy(false)
    await load()
    say(message, bad, where)
  }
  xhr.addEventListener('load', () => {
    if (xhr.status === 200) finish(done(JSON.parse(xhr.responseText)))
    else finish(failure(xhr.status, xhr.responseText.trim(), kind), true)
  })
  xhr.addEventListener('error', () => finish(t('lost'), true))
  xhr.addEventListener('abort', () => finish(t('lost'), true))
  xhr.send(file)
}

$('archive').addEventListener('change', () => { setBusy(busy); say('', false, 'install-status') })
$('install-form').addEventListener('submit', (event) => {
  event.preventDefault()
  const file = $('archive').files[0]
  const token = file && serial('install-status')
  if (!token || busy) return
  if (file.size > 40 * 1024 * 1024) { say(t('tooLarge'), true, 'install-status'); return }
  send({
    path: '/api/apps', file, type: 'application/zip', token, where: 'install-status', kind: 'app',
    row: $('progress-row'), bar: $('progress'), text: $('progress-text'), checking: t('unpacking'),
    done: (result) => {
      $('install-form').reset()
      const app = result.version ? `${result.name} ${result.version}` : result.name
      return t('installed', { app, files: result.files, size: size(result.bytes) })
    },
  })
})

// The server's updates: upload, then a restart into the new version or back to the previous one.
let update = null
function renderUpdate() {
  if (!update) return
  const running = update.running
  const note = !update.available ? (update.why?.includes('boot layer') ? t('notUnderBoot') : t('noKeys'))
    : !running ? '' : running.confirmed ? t('runningConfirmed', { version: running.version })
    : `${t('runningTentative', { version: running.version })} ${t('waitConfirmed')}`
  $('update-note').textContent = note
  const canUpload = update.available && running?.confirmed
  $('update-file').disabled = busy || !canUpload
  $('update-send').disabled = busy || !canUpload || !$('update-file').files.length
  const activate = $('update-activate'), rollback = $('update-rollback')
  if (!activate.isConnected) return  // a question is open in their place
  activate.textContent = update.staged ? t('activate', { version: update.staged.version }) : t('activateNone')
  activate.disabled = busy || !update.staged
  rollback.textContent = update.previous ? t('rollback', { version: update.previous.version }) : t('rollbackNone')
  rollback.disabled = busy || !update.previous
}
$('update-file').addEventListener('change', () => { renderUpdate(); say('', false, 'update-status') })
$('update-form').addEventListener('submit', (event) => {
  event.preventDefault()
  const file = $('update-file').files[0]
  const token = file && serial('update-status')
  if (!token || busy) return
  send({
    path: '/api/update', file, type: 'application/octet-stream', token, where: 'update-status', kind: 'update',
    row: $('update-progress-row'), bar: $('update-progress'), text: $('update-progress-text'), checking: t('checking'),
    done: (result) => {
      $('update-form').reset()
      return t('staged', { version: result.version, files: result.files, size: size(result.bytes) })
    },
  })
})
// Switching asks once, in place of the two buttons.
function askSwitch(action, version) {
  const buttons = $('update-switches')
  const kept = [...buttons.childNodes]
  const text = document.createElement('span')
  text.textContent = t(action === 'activate' ? 'askActivate' : 'askRollback', { version })
  const back = () => { buttons.replaceChildren(...kept); renderUpdate() }
  const no = button(t('cancel'), back)
  buttons.replaceChildren(text, button(t('confirm'), async () => { back(); await switchTo(action, version) }, 'danger'), no)
  no.focus()
}
$('update-activate').addEventListener('click', () => askSwitch('activate', update.staged.version))
$('update-rollback').addEventListener('click', () => askSwitch('rollback', update.previous.version))
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
// The outcome is the version that answers after the restart, not the request's success: the old
// server may answer a moment longer, so only the boot layer's new answer counts.
async function switchTo(action, version) {
  const before = update.lastRequest
  const answer = await change('POST', `/api/update/${action}`, undefined, 'update-status')
  if (!answer) return
  setBusy(true)
  say(t('restarting', { version }), false, 'update-status')
  let seen = null
  for (const until = Date.now() + 3 * 60 * 1000; Date.now() < until; await pause(1500)) {
    try {
      const now = await get('/api/update')
      if (now.running?.version === version || now.lastRequest !== before) { seen = now; break }
    } catch { /* restarting */ }
  }
  setBusy(false)
  if (!seen) { say(t('noAnswer'), true, 'update-status'); return }
  update = seen
  await load()
  for (const until = Date.now() + 5 * 60 * 1000; ; await pause(5000)) {
    const running = update.running
    if (running?.version !== version) {
      say(t('returned', { version, running: running?.version ?? '—', why: update.lastRequest ?? '—' }), true, 'update-status')
      return
    }
    if (running.confirmed) { say(t('confirmedNow', { version }), false, 'update-status'); return }
    say(t('switched', { version }), false, 'update-status')
    if (Date.now() > until) return
    try { update = await get('/api/update'); renderUpdate() } catch { /* restarting again */ }
  }
}

async function load() {
  try {
    const [nextAbout, apps, nextUpdate] = await Promise.all([get('/api/about'), get('/api/apps'), get('/api/update')])
    about = nextAbout; update = nextUpdate
    renderFacts(); renderApps(apps); renderUpdate()
  } catch { say(t('unreachable'), true) }
}
load()
