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
    busy: 'Another installation is running.', noCard: 'The card is not available in the player.',
    saved: 'Saved.', failed: 'Not done: {why}', unreachable: 'The server does not answer.',
    server: 'Server', boot: 'Boot layer', package: 'Package', ports: 'Ports', card: 'Card', cardIn: 'in the player', cardOut: 'not available',
    portsValue: 'apps {apps}, manager {manager}', notBoot: 'not under the boot layer', confirmed: 'confirmed', tentative: 'not confirmed yet',
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
    busy: 'Уже идёт другая установка.', noCard: 'Карта недоступна в плеере.',
    saved: 'Сохранено.', failed: 'Не выполнено: {why}', unreachable: 'Сервер не отвечает.',
    server: 'Сервер', boot: 'Слой загрузки', package: 'Пакет', ports: 'Порты', card: 'Карта', cardIn: 'в плеере', cardOut: 'недоступна',
    portsValue: 'приложения {apps}, менеджер {manager}', notBoot: 'не под слоем загрузки', confirmed: 'подтверждён', tentative: 'ещё не подтверждён',
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
function failure(code, text) {
  if (code === 403) return t('wrongSerial')
  if (code === 409) return t('busy')
  if (code === 413) return t('tooLarge')
  if (code === 422) return t('refused', { why: text })
  if (code === 503) return t('noCard')
  if (code === 507) return t('noRoom')
  return t('failed', { why: text || code })
}
async function get(path) {
  const response = await fetch(path, { cache: 'no-store' })
  if (!response.ok) throw new Error(`${response.status}`)
  return response.json()
}
async function change(method, path, body) {
  say('')
  const token = serial()
  if (!token) return null
  setBusy(true)
  try {
    const response = await fetch(path, {
      method, cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-Disc-Token': token, 'X-Disc-Request': requestId() },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (!response.ok) { say(failure(response.status, (await response.text()).trim()), true); return null }
    return await response.json()
  } catch {
    say(t('lost'), true)
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

// Installing: the zip goes up with its progress shown, then the server checks and unpacks it.
// A lost connection is not taken for a failure or a success: the list is read again.
$('archive').addEventListener('change', () => { setBusy(busy); say('', false, 'install-status') })
$('install-form').addEventListener('submit', (event) => {
  event.preventDefault()
  const file = $('archive').files[0]
  const token = file && serial('install-status')
  if (!token || busy) return
  if (file.size > 40 * 1024 * 1024) { say(t('tooLarge'), true, 'install-status'); return }
  setBusy(true); say('', false, 'install-status')
  const row = $('progress-row'), bar = $('progress'), text = $('progress-text')
  row.hidden = false; bar.value = 0
  text.textContent = t('uploading', { done: size(0), total: size(file.size) })
  const xhr = new XMLHttpRequest()
  xhr.open('POST', '/api/apps')
  xhr.setRequestHeader('Content-Type', 'application/zip')
  xhr.setRequestHeader('X-Disc-Token', token)
  xhr.setRequestHeader('X-Disc-Request', requestId())
  xhr.upload.addEventListener('progress', (progress) => {
    bar.value = progress.lengthComputable ? progress.loaded / progress.total : 0
    text.textContent = t('uploading', { done: size(progress.loaded), total: size(file.size) })
  })
  xhr.upload.addEventListener('load', () => { bar.removeAttribute('value'); text.textContent = t('unpacking') })
  const finish = async (message, bad) => {
    row.hidden = true
    setBusy(false)
    await load()
    say(message, bad, 'install-status')
  }
  xhr.addEventListener('load', () => {
    if (xhr.status === 200) {
      const result = JSON.parse(xhr.responseText)
      $('install-form').reset()
      const app = result.version ? `${result.name} ${result.version}` : result.name
      finish(t('installed', { app, files: result.files, size: size(result.bytes) }))
    } else finish(failure(xhr.status, xhr.responseText.trim()), true)
  })
  xhr.addEventListener('error', () => finish(t('lost'), true))
  xhr.addEventListener('abort', () => finish(t('lost'), true))
  xhr.send(file)
})

async function load() {
  try {
    const [nextAbout, apps] = await Promise.all([get('/api/about'), get('/api/apps')])
    about = nextAbout
    renderFacts(); renderApps(apps)
  } catch { say(t('unreachable'), true) }
}
load()
