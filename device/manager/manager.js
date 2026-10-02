// The DISC application manager: the server's own page on its own port (owner, 2026-10-02).
// No app can reach it (another origin); every change needs the player's serial number.
const TEXT = {
  en: {
    title: 'Apps', apps: 'Installed apps', serialTitle: 'Serial number',
    serialHelp: "Changes need the player's serial number (Settings → About on the player).",
    remember: 'Remember on this device', system: 'System', none: 'No app is installed.',
    servedAt: '{app} opens at the player\'s address.', noDefault: 'Several apps are installed: choose the one that opens at the player\'s address.',
    open: 'Open', makeDefault: 'Open at the address', chosen: 'Opens at the address', unset: 'Do not choose',
    needSerial: 'Enter the serial number first.', saved: 'Saved.', failed: 'Not saved: {why}', unreachable: 'The server does not answer.',
    server: 'Server', boot: 'Boot layer', package: 'Package', ports: 'Ports', card: 'Card', cardIn: 'in the player', cardOut: 'not available',
    portsValue: 'apps {apps}, manager {manager}', notBoot: 'not under the boot layer', confirmed: 'confirmed', tentative: 'not confirmed yet',
  },
  ru: {
    title: 'Приложения', apps: 'Установленные приложения', serialTitle: 'Серийный номер',
    serialHelp: 'Для изменений нужен серийный номер плеера (Настройки → Об устройстве на плеере).',
    remember: 'Запомнить на этом устройстве', system: 'Система', none: 'Ни одного приложения не установлено.',
    servedAt: '{app} открывается по адресу плеера.', noDefault: 'Установлено несколько приложений: выберите, какое открывается по адресу плеера.',
    open: 'Открыть', makeDefault: 'Открывать по адресу', chosen: 'Открывается по адресу', unset: 'Не выбирать',
    needSerial: 'Сначала введите серийный номер.', saved: 'Сохранено.', failed: 'Не сохранено: {why}', unreachable: 'Сервер не отвечает.',
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
const status = $('status')
const say = (text, bad = false) => { status.textContent = text; status.className = bad ? 'bad' : '' }

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

const requestId = () => {
  const random = new Uint32Array(2)
  crypto.getRandomValues(random)
  return `manager-${Date.now().toString(36)}-${random[0].toString(36)}${random[1].toString(36)}`
}
async function get(path) {
  const response = await fetch(path, { cache: 'no-store' })
  if (!response.ok) throw new Error(`${response.status}`)
  return response.json()
}
async function change(method, path, body) {
  const serial = $('serial').value.trim()
  if (!serial) { say(t('needSerial'), true); $('serial').focus(); return null }
  const response = await fetch(path, {
    method, cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-Disc-Token': serial, 'X-Disc-Request': requestId() },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) { say(t('failed', { why: (await response.text()).trim() || response.status }), true); return null }
  return response.json()
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
function button(label, action, disabled = false) {
  const element = document.createElement('button')
  element.type = 'button'; element.textContent = label; element.disabled = disabled
  element.addEventListener('click', action)
  return element
}
function renderApps(apps) {
  const list = $('apps'); list.replaceChildren()
  $('apps-note').textContent = !apps.apps.length ? t('none') : apps.default ? t('servedAt', { app: apps.default }) : t('noDefault')
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
    else actions.append(button(t('makeDefault'), () => choose(app.name)))
    item.append(actions)
    list.append(item)
  }
}
async function choose(name) {
  const apps = await change('PUT', '/api/apps/default', { name })
  if (apps) { renderApps(apps); say(t('saved')) }
}
async function load() {
  try {
    const [nextAbout, apps] = await Promise.all([get('/api/about'), get('/api/apps')])
    about = nextAbout
    renderFacts(); renderApps(apps)
  } catch { say(t('unreachable'), true) }
}
load()
