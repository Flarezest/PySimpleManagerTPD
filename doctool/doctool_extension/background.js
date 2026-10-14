/* PySimpleManager: фоновая часть расширения (service worker).
 *
 * 1) Держит WebSocket-соединение с doctool на этом компьютере: ws://127.0.0.1:<порт>/ext.
 * 2) Получает команды и выполняет их по очереди:
 *    lookup      — данные домена (счета, Sd, S, с 0.6.0 — идентификация через Госуслуги и JSON ЕСИА;
 *                  с 0.7.2 — и у юрлиц);
 *    account     — данные аккаунта и базовая анкета (0.6.0);
 *    fill_runic  — заполнить поля базовой анкеты во вкладке оператора (0.6.0; ИП и юрлица — 0.7.3).
 *    (0.7.6, рабочее место программы)
 *    gosuslugi_fill   — вписать серию, номер, фамилию и имя на странице «Проверка действительности паспорта»
 *                       (Госуслуги, вкладка оператора; «Продолжить» нажимает оператор);
 *    domain_move_fill — шаг 1 смены администратора в manager: домены, флажки операции, принимающий аккаунт,
 *                       документы в «Загрузить документы»;
 *    domain_move_read / domain_move_mark / domain_move_fill_contacts — шаг 2 («данные нового администратора»):
 *                       прочитать, подсветить итог сверки, вписать ФИО и контакты при «заполнить вручную».
 *    (0.8.0, строка «Поиск» рабочего места)
 *    site_open / site_hide — сайт рядом с рабочим местом: вторая половина окна Chrome (разделённый экран), окно
 *                       справа или вкладка; site_open с fill — вписать запрос в поле поиска (ЕГРЮЛ). Страница
 *                       «Проверка действительности паспорта» (gosuslugi_fill) открывается там же.
 *    site_open, site_hide и gosuslugi_fill выполняются сразу, не дожидаясь очереди запросов к manager.
 *    content/drop.js — файлы дела, перетащенные из рабочего места на страницы проверки ЭП (Госуслуги),
 *                       Контур.Крипто и Nextcloud: расширение берёт файл у программы и кладёт его на страницу.
 *    lookup и account читают страницы в отдельной фоновой вкладке manager.
 * 3) Если manager просит вход (окно «Войти»), показывает вкладку и сообщает doctool.
 *
 * Расширение читает страницы manager. Меняет оно только значения полей форм во вкладках, которые видит оператор,
 * и только по кнопке в программе: базовая анкета (с 0.7.3 — и переключатель «Физик / ИП / Юрик»), с 0.7.6 —
 * страницы смены администратора и форма проверки паспорта на Госуслугах, с 0.8.0 — поле поиска ЕГРЮЛ и файлы,
 * перетащенные оператором на страницы проверки ЭП, Контур.Крипто и Nextcloud. Кнопки («Сохранить», «Далее»,
 * «Продолжить», «Найти», «Изменить», «Удалить» и т. п.) оно не нажимает и формы не отправляет.
 */
const VERSION = chrome.runtime.getManifest().version;
const MANAGER = 'https://manager.reg.ru/';
const IDENTITY = 'https://identity.reg.ru/';
const GOSUSLUGI = 'https://www.gosuslugi.ru/';
const GOSUSLUGI_FORM = 'https://www.gosuslugi.ru/621102/1/form';   // «Проверка действительности паспорта»
const DOMAIN_MOVE = 'https://manager.reg.ru/manager/domain_move';
const DEFAULTS = { port: 8765, authWaitSec: 180, pauseMs: 300, closeTabAfterSec: 60 };

let ws = null;
let reconnectTimer = null;
let backoff = 2000;
let keepalive = null;
let managerTabId = null;
let createdTab = false;
let currentReq = null;
let closeTimer = null;
let queue = Promise.resolve();
const authHooks = new Map();   // tabId → функция «ждать дольше: идёт вход»
const state = { connected: false, port: DEFAULTS.port, busy: false, events: [], lastError: '' };

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function settings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) };
}

function note(msg) {
  state.events.unshift(`${new Date().toLocaleTimeString()}  ${msg}`);
  state.events.length = Math.min(state.events.length, 40);
}

function badge() {
  const [text, color] = !state.connected ? ['!', '#9b1c1c'] : state.busy ? ['…', '#2b6cb0'] : ['', '#1e6b3a'];
  chrome.action.setBadgeText({ text }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ color }).catch(() => {});
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

// ------------------------------------------------------------------ связь с doctool

async function connect() {
  if (ws && ws.readyState <= WebSocket.OPEN) return;
  clearTimeout(reconnectTimer);
  const { port } = await settings();
  state.port = port;
  let sock;
  try {
    sock = new WebSocket(`ws://127.0.0.1:${port}/ext`);
  } catch (e) {
    scheduleReconnect();
    return;
  }
  ws = sock;
  sock.onopen = () => {
    backoff = 2000;
    state.connected = true;
    state.lastError = '';
    send({ type: 'hello', version: VERSION, chrome: chromeMajor(), split_api: splitApi() });
    note(`Подключено к PySimpleManager (порт ${port})`);
    clearInterval(keepalive);
    keepalive = setInterval(() => send({ type: 'ping' }), 20000);   // не даёт service worker уснуть
    badge();
  };
  sock.onmessage = e => {
    let m;
    try { m = JSON.parse(e.data); } catch { return; }
    if (m.type === 'request') {
      if (FAST.has(m.cmd)) handleFast(m);      // (0.8.0) сайт рядом — не ждёт загрузки доменов из manager
      else enqueue(m);
    }
  };
  sock.onclose = () => {
    if (ws === sock) ws = null;
    clearInterval(keepalive);
    if (state.connected) note('Связь с PySimpleManager потеряна');
    state.connected = false;
    badge();
    scheduleReconnect();
  };
  sock.onerror = () => { state.lastError = `PySimpleManager не отвечает на порту ${port}`; };
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, backoff);
  backoff = Math.min(backoff * 2, 15000);
}

// ------------------------------------------------------------------ очередь команд

function enqueue(m) {
  queue = queue.then(() => handle(m)).catch(e => console.error('doctool:', e));
}

async function handle(m) {
  state.busy = true;
  currentReq = m.id;
  clearTimeout(closeTimer);
  badge();
  let reply;
  try {
    if (m.cmd === 'lookup') reply = await lookupDomain(m.id, m.params || {});
    else if (m.cmd === 'account') reply = await readAccount(m.id, m.params || {});
    else if (m.cmd === 'fill_runic') reply = await fillRunic(m.id, m.params || {});
    else if (m.cmd === 'check_manager') reply = await checkManager();
    else if (m.cmd === 'domain_move_fill') reply = await domainMoveFill(m.id, m.params || {});
    else if (m.cmd === 'domain_move_read') reply = await domainMoveStep2(m.id, 'domainMoveRead', []);
    else if (m.cmd === 'domain_move_mark') reply = await domainMoveStep2(m.id, 'domainMoveMark', [(m.params || {}).marks || {}]);
    else if (m.cmd === 'domain_move_fill_contacts') reply = await domainMoveStep2(m.id, 'domainMoveFillContacts', [(m.params || {}).fields || {}]);
    else reply = { ok: false, error: { code: 'unknown_cmd', message: `Неизвестная команда: ${m.cmd}` } };
  } catch (e) {
    reply = { ok: false, error: { code: e.code || 'error', message: e.message || String(e) } };
  }
  send({ type: 'result', id: m.id, ...reply });
  currentReq = null;
  state.busy = false;
  badge();
  scheduleTabClose();
}

// (0.8.0) команды, которые не трогают вкладку manager и выполняются сразу
const FAST = new Set(['site_open', 'site_hide', 'gosuslugi_fill']);

async function handleFast(m) {
  let reply;
  try {
    if (m.cmd === 'site_open') reply = await siteOpen(m.id, m.params || {});
    else if (m.cmd === 'site_hide') reply = await siteHide(m.params || {});
    else if (m.cmd === 'gosuslugi_fill') reply = await gosuslugiFill(m.id, m.params || {});
  } catch (e) {
    reply = { ok: false, error: { code: e.code || 'error', message: e.message || String(e) } };
  }
  send({ type: 'result', id: m.id, ...reply });
}

function fail(code, message) {
  const e = new Error(message);
  e.code = code;
  return e;
}

// Вызвать функцию content/manager.js (window.__doctoolManager[fn]) во вкладке manager.
async function runInTab(tabId, fn, args) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ['content/manager.js'] });
  const [res] = await chrome.scripting.executeScript({
    target: { tabId },
    func: (name, a) => window.__doctoolManager[name](...a),
    args: [fn, args],
  });
  return (res && res.result) || { ok: false, error: { code: 'error', message: 'Скрипт во вкладке manager не вернул результат' } };
}

// То же в фоновой вкладке manager; если вход истёк посреди работы — обновляем вкладку (появится окно «Войти»),
// ждём вход и пробуем ещё раз.
async function runWithLogin(fn, args) {
  const tabId = await ensureManagerTab();
  let out = await runInTab(tabId, fn, args);
  if (!out.ok && out.error && out.error.code === 'not_logged_in') {
    await chrome.tabs.reload(tabId).catch(() => {});
    await waitLoaded(tabId, 30000, 30000);
    await waitForLogin(tabId);
    out = await runInTab(tabId, fn, args);
  }
  return out;
}

async function lookupDomain(reqId, { domain, ascii, esia, task }) {
  if (!domain) throw fail('bad_request', 'Не указан домен');
  note(`Запрос: ${domain}`);
  const out = await runWithLogin('lookup', [reqId, domain, ascii || domain, { esia: esia !== false, task: task || '' }]);
  const e = out.ok && out.data && out.data.esia;
  if (e && e.file_url) {
    progressTo(reqId, 'esia_json', `${domain}: файл данных ЕСИА`);
    try {
      e.data = await esiaJson(e.file_url, task);
    } catch (err) {
      e.json_error = err.message || String(err);
    }
  }
  note(`${domain}: ${out.ok ? 'данные получены' : out.error.message}`);
  const { pauseMs } = await settings();
  await sleep(pauseMs);
  return out;
}

function progressTo(id, step, message) {
  send({ type: 'progress', id, step, message });
}

// ------------------------------------------------------------------ JSON ЕСИА (identity.reg.ru)

// Из файла ЕСИА в doctool передаются только поля для сверки с Sd и признаки проверки данных.
// Физлицо: ИНН, СНИЛС, адреса, телефон, e-mail, oid не передаются.
// Организация (0.7.2; в файле есть full_name / ogrn / legal_address): название, ИНН, КПП, ОГРН, признак ликвидации
// и юридический адрес. ФИО представителя (prs_auth_*), телефон, e-mail, почтовый адрес, oid не передаются.
function esiaFields(j) {
  const d = (j && j.data) || j || {};
  if (d.full_name !== undefined || d.ogrn !== undefined || d.legal_address) {
    const a = d.legal_address || {};
    return {
      status: (j && j.status) || '', kind: 'org',
      full_name: d.full_name || '', inn: d.inn || '', kpp: d.kpp || '', ogrn: d.ogrn || '',
      is_liquidated: d.is_liquidated, trusted: d.trusted,
      legal_address: d.legal_address ? {
        zip_code: a.zip_code || '', region: a.region || '', area: a.area || '', city: a.city || '',
        settlement: a.settlement || '', street: a.street || '', house: a.house || '', building: a.building || '',
        frame: a.frame || '', flat: a.flat || '', address_str: a.address_str || '',
      } : null,
    };
  }
  const p = d.rf_passport || {};
  return {
    status: (j && j.status) || '',
    first_name: d.first_name || '', last_name: d.last_name || '', middle_name: d.middle_name || '',
    birth_date: d.birth_date || '', citizenship: d.citizenship || '', trusted: d.trusted,
    rf_passport: d.rf_passport ? {
      series: p.series || '', number: p.number || '', issue_date: p.issue_date || '',
      issued_by: p.issued_by || '', vrf_stu: p.vrf_stu || '',
    } : null,
  };
}

const esiaJsonCache = new Map();   // `${task}|${url}` → {t, data}

async function esiaJson(url, task) {
  if (!url.startsWith(IDENTITY) && !url.startsWith(MANAGER)) throw fail('bad_url', `Неожиданный адрес файла ЕСИА: ${url}`);
  const key = `${task || ''}|${url}`;
  const hit = esiaJsonCache.get(key);
  if (hit && Date.now() - hit.t < 10 * 60 * 1000) return hit.data;
  let j = null, why = '';
  try {
    const r = await fetch(url, { credentials: 'include', cache: 'no-store' });
    if (r.ok) j = JSON.parse(await r.text());
    else why = `HTTP ${r.status}`;
  } catch (e) {
    why = e.message || String(e);
  }
  if (!j) j = await esiaJsonViaTab(url, why);   // напр. сайт требует вход — открываем файл как оператор по ссылке
  const data = esiaFields(j);
  if (esiaJsonCache.size > 500) esiaJsonCache.clear();
  esiaJsonCache.set(key, { t: Date.now(), data });
  return data;
}

async function esiaJsonViaTab(url, why) {
  const tab = await chrome.tabs.create({ url, active: false });
  try {
    await waitLoaded(tab.id, 30000, 60000);
    // JSON в Chrome показывается внутри <pre> (рядом может быть флажок «Автоформатирование» — его текст не берём)
    const [res] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => { const pre = document.querySelector('pre'); return pre ? pre.textContent : (document.body ? document.body.innerText : ''); },
    });
    const textBody = (res && res.result) || '';
    try {
      const a = textBody.indexOf('{'), b = textBody.lastIndexOf('}');
      return JSON.parse(a >= 0 && b > a ? textBody.slice(a, b + 1) : textBody);
    } catch {
      throw fail('esia_json', `Файл ЕСИА не открылся${why ? ` (${why})` : ''}: вместо JSON — «${textBody.slice(0, 80)}»`);
    }
  } finally {
    chrome.tabs.remove(tab.id).catch(() => {});
  }
}

// ------------------------------------------------------------------ аккаунт и базовая анкета

async function readAccount(reqId, { user_id, login }) {
  if (!user_id && !login) throw fail('bad_request', 'Не указан аккаунт');
  note(`Аккаунт: ${user_id || login}`);
  const out = await runWithLogin('account', [reqId, { user_id: user_id || '', login: login || '' }]);
  note(`Аккаунт ${user_id || login}: ${out.ok ? 'данные получены' : out.error.message}`);
  return out;
}

// Вкладка «Базовая анкета пользователя #N» (https://manager.reg.ru/user/N/runic_details): уже открытая
// или новая. Это вкладка оператора: она показывается, а не закрывается.
async function runicTab(userId) {
  const url = `${MANAGER}user/${userId}/runic_details`;
  const re = new RegExp(`^${MANAGER.replace(/[.]/g, '\\.')}user/${userId}/runic_details(?:[?#].*)?$`);
  const tabs = await chrome.tabs.query({ url: `${MANAGER}user/*` });
  let tab = tabs.find(t => re.test(t.url || ''));
  let opened = false;
  if (!tab) {
    tab = await chrome.tabs.create({ url, active: true });
    opened = true;
  }
  try {
    const t = await chrome.tabs.update(tab.id, { active: true });
    await chrome.windows.update(t.windowId, { focused: true });
  } catch { /* окно недоступно */ }
  const { authWaitSec } = await settings();
  await waitLoaded(tab.id, 30000, authWaitSec * 1000);
  await waitForLogin(tab.id);
  return { tabId: tab.id, opened };
}

// type (0.7.3) — тип нового владельца: pp / ip / org; страница переключается на него, если там другой.
// back_type — при restore: вернуть и прежний тип анкеты.
async function fillRunic(reqId, { user_id, fields, restore, type, back_type }) {
  if (!/^\d+$/.test(String(user_id || ''))) throw fail('bad_request', 'Не указан номер аккаунта');
  note(`Базовая анкета #${user_id}: ${restore ? 'возврат прежних значений' : 'заполнение'}`);
  progressTo(reqId, 'runic_tab', `открываю базовую анкету #${user_id}`);
  const { tabId, opened } = await runicTab(user_id);
  progressTo(reqId, 'fill', `${restore ? 'возвращаю' : 'заполняю'} поля во вкладке базовой анкеты #${user_id}`);
  const opts = { restore: !!restore };
  if (type) opts.type = type;
  if (back_type) opts.back_type = back_type;
  const out = await runInTab(tabId, 'fillRunic', [String(user_id), fields || {}, opts]);
  if (out.ok) out.data.tab_opened = opened;
  else out.error.tab_opened = opened;
  note(`Базовая анкета #${user_id}: ${out.ok ? `${out.data.type_switched ? `тип → ${out.data.type_page}, ` : ''}изменено полей ${out.data.changed.length}` : out.error.message}`);
  return out;
}

// ------------------------------------------------------------------ смена администратора домена (0.7.6)

async function focusTab(tabId) {
  try {
    const t = await chrome.tabs.update(tabId, { active: true });
    await chrome.windows.update(t.windowId, { focused: true });
  } catch { /* окно недоступно */ }
}

async function probe(tabId, func) {
  try {
    const [r] = await chrome.scripting.executeScript({ target: { tabId }, func });
    return !!(r && r.result);
  } catch {
    return false;
  }
}

// Вкладка оператора со страницей смены администратора: уже открытая на нужном шаге или новая (шаг 1).
async function domainMoveTab(step) {
  const tabs = await chrome.tabs.query({ url: `${DOMAIN_MOVE}*` });
  const want = step === 1 ? () => !!document.querySelector('textarea[name="domains"]')
                          : () => !!document.querySelector('form[name="contacts_form"]');
  for (const t of tabs) {
    if (await probe(t.id, want)) return { tabId: t.id, opened: false };
  }
  if (step !== 1) {
    throw fail('no_tab', 'Не найдена вкладка manager со вторым шагом смены администратора («данные нового администратора»). Нажмите «Далее» на первом шаге в manager и повторите.');
  }
  const tab = await chrome.tabs.create({ url: DOMAIN_MOVE, active: true });
  return { tabId: tab.id, opened: true };
}

async function domainMoveFill(reqId, params) {
  if (!params.domains || !params.domains.length) throw fail('bad_request', 'Нет доменов для смены администратора');
  note(`Смена администратора: ${params.domains.join(', ')}`);
  progressTo(reqId, 'domain_move', 'открываю «Перенос доменов между аккаунтами» в manager');
  const { tabId, opened } = await domainMoveTab(1);
  await focusTab(tabId);
  const { authWaitSec } = await settings();
  await waitLoaded(tabId, 30000, authWaitSec * 1000);
  await waitForLogin(tabId);
  progressTo(reqId, 'fill', `вписываю домены${(params.files || []).length ? ' и прикрепляю документы' : ''}`);
  const out = await runInTab(tabId, 'domainMoveFill', [params]);
  if (out.ok) out.data.tab_opened = opened;
  note(`Смена администратора: ${out.ok ? `заполнено (${(out.data.changed || []).length}), файлов ${(out.data.attached || []).length}` : out.error.message}`);
  return out;
}

async function domainMoveStep2(reqId, fn, args) {
  const { tabId } = await domainMoveTab(2);
  if (fn !== 'domainMoveRead') await focusTab(tabId);
  progressTo(reqId, 'domain_move', 'шаг 2: данные нового администратора');
  return runInTab(tabId, fn, args);
}

// ------------------------------------------------------------------ Госуслуги: проверка действительности паспорта (0.7.6)

// Выполняется во вкладке Госуслуг (изолированный мир расширения). Поля маскированные (Angular): значение вписывается
// по символу с событиями клавиатуры и ввода, затем проверяется, что поле его приняло. «Продолжить» не нажимается.
function fillGosuslugiForm(p) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  const typeInto = (el, value) => {
    el.focus();
    el.dispatchEvent(new FocusEvent('focus'));
    setter.call(el, '');
    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContentBackward' }));
    for (const ch of String(value)) {
      el.dispatchEvent(new KeyboardEvent('keydown', { key: ch, bubbles: true }));
      el.dispatchEvent(new KeyboardEvent('keypress', { key: ch, bubbles: true }));
      setter.call(el, el.value + ch);
      el.dispatchEvent(new InputEvent('input', { bubbles: true, data: ch, inputType: 'insertText' }));
      el.dispatchEvent(new KeyboardEvent('keyup', { key: ch, bubbles: true }));
    }
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.blur();
    el.dispatchEvent(new FocusEvent('blur'));
    el.style.outline = '2px solid #f0ad4e';
    return el.value;
  };
  const fields = [['series', '#series', 'Серия'], ['number', '#number', 'Номер'],
                  ['surname', '#component_lastname, input[name="component_lastname"]', 'Фамилия'],
                  ['name', '#component_firstname, input[name="component_firstname"]', 'Имя']];
  const filled = {}, problems = [];
  for (const [key, sel, label] of fields) {
    const el = document.querySelector(sel);
    if (!el) { problems.push(`нет поля «${label}»`); continue; }
    if (!p[key]) continue;
    const got = typeInto(el, p[key]);
    filled[key] = got;
    const norm = s => String(s || '').replace(/[\s_-]/g, '').toLowerCase();
    if (norm(got) !== norm(p[key])) problems.push(`«${label}»: поле приняло «${got}» вместо «${p[key]}» — поправьте вручную`);
  }
  const btn = [...document.querySelectorAll('button')].find(b => /Продолжить/i.test(b.textContent || ''));
  return { ok: true, data: { filled, problems, button_enabled: !!(btn && !btn.disabled && !btn.classList.contains('disabled')) } };
}

async function gosuslugiFill(reqId, p) {
  if (!p.series || !p.number) throw fail('bad_request', 'Нет серии и номера паспорта');
  note(`Госуслуги: проверка паспорта ${p.series} ${p.number}`);
  progressTo(reqId, 'gosuslugi', 'открываю «Проверка действительности паспорта» на Госуслугах');
  let tab = null;
  if (p.site && p.site.open_in !== 'tab') {
    // (0.8.0) там же, где сайты строки «Поиск»: рядом с рабочим местом; открытую форму не перезагружаем
    const r = await siteOpenTab(GOSUSLUGI_FORM, p.site, `${GOSUSLUGI}621102/`);
    tab = r.tabId !== null ? await getTab(r.tabId) : null;
  }
  if (!tab) {
    const tabs = await chrome.tabs.query({ url: `${GOSUSLUGI}621102/*` });
    tab = tabs[0];
    if (!tab) tab = await chrome.tabs.create({ url: GOSUSLUGI_FORM, active: true });
    await focusTab(tab.id);
  }
  await waitLoaded(tab.id, 60000, 60000);
  // страница — приложение Angular: форма появляется после загрузки скриптов; без входа Госуслуги покажут вход
  const has = () => !!document.querySelector('#series') && !!document.querySelector('#number');
  let ready = false;
  for (let i = 0; i < 90 && !ready; i++) {
    ready = await probe(tab.id, has);
    if (!ready) await sleep(1000);
  }
  if (!ready) {
    throw fail('gosuslugi_form', 'На странице Госуслуг нет формы «Паспорт РФ» (серия, номер). Если Госуслуги просят вход — войдите во вкладке и нажмите кнопку в программе ещё раз.');
  }
  progressTo(reqId, 'fill', 'вписываю серию, номер, фамилию и имя');
  const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: fillGosuslugiForm, args: [p] });
  const out = (res && res.result) || { ok: false, error: { code: 'error', message: 'Скрипт на странице Госуслуг не вернул результат' } };
  note(`Госуслуги: ${out.ok ? (out.data.problems.length ? out.data.problems.join('; ') : 'данные вписаны') : out.error.message}`);
  return out;
}

async function checkManager() {
  const tabId = await ensureManagerTab();
  const [res] = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => {
      const h = document.querySelector('#content h3');
      return { login: h ? h.textContent.replace(/\s*\[x\]\s*$/, '').replace(/[\[\]]/g, '').trim() : '' };
    },
  });
  return { ok: true, data: { manager: 'ok', login: (res && res.result && res.result.login) || '' } };
}

// ------------------------------------------------------------------ (0.8.0) сайт рядом с рабочим местом
//
// Строка «Поиск» рабочего места открывает сайт (Google, Яндекс, ЕГРЮЛ, быстрые ссылки, Госуслуги):
//   split  — во второй половине окна Chrome рядом со вкладкой программы (разделённый экран). Chrome 155+:
//            tabs.create({splitWithTabId}); в Chrome 145–154 — если пару вкладок один раз собрали вручную, сайт
//            открывается во второй вкладке пары. Не получилось — окном (window).
//   window — отдельное окно Chrome (тип popup) на правой части экрана, окно программы сдвигается влево.
//   tab    — обычная вкладка рядом с вкладкой программы.
//   auto   — split, иначе window.
// Следующий запрос открывается там же — одна вкладка (или одно окно) сайта. «Убрать» (site_hide): вкладка выходит из
// разделённого экрана и остаётся обычной вкладкой (Chrome 155+; в старых версиях — закрывается), окно сворачивается,
// окно программы возвращается на прежнее место. Кнопок на сайтах расширение не нажимает и формы не отправляет.
const SITE_DEFAULT = { tabId: null, windowId: null, mode: '', manual: false, progWindowId: null, prevBounds: null };
const DROP_HOSTS = ['e-trust.gosuslugi.ru', 'crypto.kontur.ru', 'nextcloud-ms.runity.ru'];   // content/drop.js
const FILLERS = { egrul: ['#query', 'input[name="query"]'] };   // формы поиска: где искать поле запроса
let site = null;

const chromeMajor = () => { const m = /Chrome\/(\d+)/.exec(navigator.userAgent || ''); return m ? Number(m[1]) : 0; };
const splitApi = () => !!(chrome.tabs && typeof chrome.tabs.createSplit === 'function' && typeof chrome.tabs.unsplit === 'function');
const inSplit = t => !!t && typeof t.splitViewId === 'number' && t.splitViewId >= 0;

async function siteLoad() {
  if (!site) {
    const s = await chrome.storage.session.get('site');
    site = { ...SITE_DEFAULT, ...(s.site || {}) };
  }
  return site;
}

async function siteSave(patch, reset = false) {
  site = { ...(reset ? SITE_DEFAULT : await siteLoad()), ...patch };
  await chrome.storage.session.set({ site });
}

async function getTab(id) {
  if (id === null || id === undefined) return null;
  try { return await chrome.tabs.get(id); } catch { return null; }
}

async function getWin(id) {
  if (id === null || id === undefined) return null;
  try { return await chrome.windows.get(id, { populate: true }); } catch { return null; }
}

// Вкладка программы (рабочее место): http://127.0.0.1:<порт>/ или http://localhost:<порт>/;
// активная в последнем окне — в первую очередь.
async function programTab() {
  const { port } = await settings();
  const tabs = (await chrome.tabs.query({ url: ['http://127.0.0.1/*', 'http://localhost/*'] }))
    .filter(t => { try { return new URL(t.url).port === String(port); } catch { return false; } });
  if (!tabs.length) return null;
  let win = null;
  try { win = await chrome.windows.getLastFocused(); } catch { /* нет окон */ }
  return tabs.find(t => t.active && win && t.windowId === win.id) || tabs.find(t => t.active) || tabs[0];
}

// Ждать окончания загрузки вкладки (status complete) — не дольше ms.
function waitComplete(tabId, ms) {
  return new Promise(resolve => {
    let timer = null;
    const done = v => { clearTimeout(timer); chrome.tabs.onUpdated.removeListener(onUpd); resolve(v); };
    const onUpd = (id, info) => { if (id === tabId && info.status === 'complete') done(true); };
    chrome.tabs.onUpdated.addListener(onUpd);
    timer = setTimeout(() => done(false), ms);
    chrome.tabs.get(tabId).then(t => { if (t.status === 'complete' && !t.pendingUrl) done(true); }).catch(() => done(false));
  });
}

// Где оператор видит экран: область, которую передала страница программы (screen.avail*), иначе окно программы.
async function screenArea(opts, prog) {
  const sc = opts && opts.screen;
  if (sc && sc.width > 300 && sc.height > 200) return sc;
  const w = prog ? await getWin(prog.windowId) : null;
  if (w && w.width) return { left: w.left || 0, top: w.top || 0, width: w.width, height: w.height };
  return { left: 0, top: 0, width: 1600, height: 900 };
}

async function shrinkProgram(prog, area, siteWidth) {
  if (!prog) return;
  const w = await getWin(prog.windowId);
  if (!w) return;
  const st = await siteLoad();
  if (st.progWindowId !== w.id || !st.prevBounds) {
    await siteSave({ progWindowId: w.id, prevBounds: { state: w.state, left: w.left, top: w.top, width: w.width, height: w.height } });
  }
  try {
    if (w.state !== 'normal') await chrome.windows.update(w.id, { state: 'normal' });
    await chrome.windows.update(w.id, { left: area.left, top: area.top, width: Math.max(400, area.width - siteWidth), height: area.height });
  } catch { /* окно нельзя двигать (полный экран и т. п.) */ }
}

async function restoreProgram() {
  const st = await siteLoad();
  if (!st.progWindowId || !st.prevBounds) return;
  const b = st.prevBounds;
  try {
    if (b.state === 'maximized' || b.state === 'fullscreen') await chrome.windows.update(st.progWindowId, { state: b.state });
    else await chrome.windows.update(st.progWindowId, { state: 'normal', left: b.left, top: b.top, width: b.width, height: b.height });
  } catch { /* окно закрыто */ }
  await siteSave({ prevBounds: null });
}

// Убрать сайт, открытый прежним способом (когда оператор выбрал другой).
async function siteDrop(st) {
  if (st.mode === 'window' && st.windowId !== null) {
    await chrome.windows.remove(st.windowId).catch(() => {});
    await restoreProgram();
  } else if (st.tabId !== null && !st.manual) {
    await chrome.tabs.remove(st.tabId).catch(() => {});
  }
  await siteSave({}, true);
}

async function goTo(tabId, url, keepPrefix) {
  const t = await getTab(tabId);
  if (!t) return null;
  if (url && !(keepPrefix && (t.url || '').startsWith(keepPrefix))) await chrome.tabs.update(tabId, { url });
  return t;
}

// Открыть адрес рядом с рабочим местом. opts: {open_in, screen, share, keep}. keep — не перезагружать сайт, если он
// уже открыт (показать снова после «убрать»); keepPrefix — не уходить со страницы, адрес которой так начинается.
async function siteOpenTab(url, opts = {}, keepPrefix = '') {
  const want = ['split', 'window', 'tab'].includes(opts.open_in) ? opts.open_in : 'auto';
  const prog = await programTab();
  let st = await siteLoad();
  const notes = [];
  const nav = opts.keep ? '' : url;
  // сайт закрыли руками — забываем его (окно программы — на прежнее место)
  const alive = st.mode === 'window' ? !!(await getWin(st.windowId)) : !!(st.mode && await getTab(st.tabId));
  if (st.mode && !alive) {
    if (st.mode === 'window') await restoreProgram();
    await siteSave({}, true);
    st = await siteLoad();
  }
  // auto — разделённый экран; если сайт уже открыт окном (разделённый экран не получился) — то же окно
  let mode = want === 'auto' ? (st.mode === 'window' ? 'window' : 'split') : want;
  if (st.mode && st.mode !== mode) { await siteDrop(st); st = await siteLoad(); }

  if (mode === 'split') {
    const t = await getTab(st.tabId);
    if (t && st.mode === 'split' && prog) {
      if (inSplit(t) && t.splitViewId === prog.splitViewId) {
        await goTo(t.id, nav || '', keepPrefix);
        await chrome.tabs.update(t.id, { active: true }).catch(() => {});
        return { tabId: t.id, mode: 'split', notes };
      }
      if (!inSplit(t) && !inSplit(prog) && splitApi()) {
        try {
          // вкладка сайта вышла из пары («убрать») — ставим рядом и собираем пару снова
          if (t.windowId !== prog.windowId || t.index !== prog.index + 1) {
            await chrome.tabs.move(t.id, { windowId: prog.windowId, index: prog.index + 1 });
          }
          await chrome.tabs.createSplit([prog.id, t.id]);
          await goTo(t.id, nav, keepPrefix);
          await chrome.tabs.update(t.id, { active: true }).catch(() => {});
          return { tabId: t.id, mode: 'split', notes };
        } catch (e) { notes.push(`пара вкладок не собралась (${e.message || e}) — открываю заново`); }
      }
      if (!st.manual) await chrome.tabs.remove(t.id).catch(() => {});
      await siteSave({}, true);
    }
    if (prog && inSplit(prog)) {
      // вкладка программы уже в разделённом экране (пару собрали вручную) — сайт открывается во второй вкладке пары
      const pair = (await chrome.tabs.query({ windowId: prog.windowId })).find(x => x.id !== prog.id && x.splitViewId === prog.splitViewId);
      if (pair) {
        await goTo(pair.id, nav, keepPrefix);
        await chrome.tabs.update(pair.id, { active: true }).catch(() => {});
        await siteSave({ tabId: pair.id, windowId: pair.windowId, mode: 'split', manual: true });
        return { tabId: pair.id, mode: 'split', notes };
      }
    }
    if (prog && splitApi()) {
      try {
        const t2 = await chrome.tabs.create({ splitWithTabId: prog.id, url });
        await siteSave({ tabId: t2.id, windowId: t2.windowId, mode: 'split', manual: false });
        return { tabId: t2.id, mode: 'split', notes };
      } catch (e) { notes.push(`разделённый экран не открылся: ${e.message || e}`); }
    } else {
      notes.push(prog ? `разделённый экран Chrome доступен расширению с версии 155 (у вас ${chromeMajor() || '?'}); `
        + 'в Chrome 145–154 соберите пару вкладок вручную: правый щелчок по вкладке программы → разделённый экран'
        : 'не найдена вкладка программы');
    }
  }

  if (mode === 'window' || mode === 'split') {
    const area = await screenArea(opts, prog);
    const share = Math.min(70, Math.max(20, Number(opts.share) || 40));
    const width = Math.round(area.width * share / 100);
    const w = st.mode === 'window' ? await getWin(st.windowId) : null;
    if (w) {
      const t = (w.tabs || [])[0];
      if (t) await goTo(t.id, nav, keepPrefix);
      await chrome.windows.update(w.id, { state: 'normal', focused: true }).catch(() => {});
      await shrinkProgram(prog, area, w.width || width);
      return { tabId: t ? t.id : null, mode: 'window', notes };
    }
    const win = await chrome.windows.create({ url, type: 'popup', focused: true, left: area.left + area.width - width, top: area.top, width, height: area.height });
    const t = (win.tabs || [])[0];
    await siteSave({ tabId: t ? t.id : null, windowId: win.id, mode: 'window', manual: false });
    await shrinkProgram(prog, area, width);
    return { tabId: t ? t.id : null, mode: 'window', notes };
  }

  // mode === 'tab'
  const t = st.mode === 'tab' ? await getTab(st.tabId) : null;
  if (t) {
    await goTo(t.id, nav, keepPrefix);
    await chrome.tabs.update(t.id, { active: true }).catch(() => {});
    return { tabId: t.id, mode: 'tab', notes };
  }
  const t3 = await chrome.tabs.create({ url, active: true, ...(prog ? { windowId: prog.windowId, index: prog.index + 1 } : {}) });
  await siteSave({ tabId: t3.id, windowId: t3.windowId, mode: 'tab', manual: false });
  return { tabId: t3.id, mode: 'tab', notes };
}

// Выполняется на странице сайта: вписать запрос в поле поиска (кнопку «Найти» нажимает оператор).
function fillSearchField(q, selectors) {
  const vis = el => !!el && !el.disabled && !el.readOnly && el.offsetParent !== null;
  let el = null;
  for (const s of selectors || []) { const x = document.querySelector(s); if (vis(x)) { el = x; break; } }
  if (!el) el = [...document.querySelectorAll('input[type="text"], input[type="search"], input:not([type])')].find(vis) || null;
  if (!el) return { ok: false, error: { code: 'no_field', message: 'На странице не найдено поле поиска — вставьте запрос вручную' } };
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  el.focus();
  setter.call(el, q);
  el.dispatchEvent(new InputEvent('input', { bubbles: true, data: q, inputType: 'insertText' }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  el.style.outline = '2px solid #f0ad4e';
  return { ok: true, data: { field: el.id || el.name || el.placeholder || 'поле поиска', value: el.value } };
}

async function injectDrop(tabId, url) {
  let host = '';
  try { host = new URL(url).hostname; } catch { return; }
  if (!DROP_HOSTS.includes(host)) return;
  await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ['content/drop.js'] }).catch(() => {});
}

async function siteOpen(reqId, p) {
  const url = String(p.url || '');
  if (!/^https?:\/\//.test(url)) throw fail('bad_request', 'Адрес сайта должен начинаться с https:// или http://');
  progressTo(reqId, 'site', `открываю ${url}`);
  const r = await siteOpenTab(url, p);
  const data = { mode: r.mode, notes: r.notes, chrome: chromeMajor(), split_api: splitApi() };
  note(`Сайт (${r.mode}): ${url}${r.notes.length ? ' — ' + r.notes.join('; ') : ''}`);
  if (r.tabId === null) return { ok: true, data };
  const fill = p.fill && FILLERS[p.fill.kind];
  if (!fill) {
    waitComplete(r.tabId, 30000).then(() => injectDrop(r.tabId, url));
    return { ok: true, data };
  }
  await waitComplete(r.tabId, 30000);
  let out = null;
  for (let i = 0; i < 15; i++) {
    try {
      const [res] = await chrome.scripting.executeScript({ target: { tabId: r.tabId }, func: fillSearchField, args: [String(p.fill.q || ''), fill] });
      out = res && res.result;
    } catch (e) { out = { ok: false, error: { code: 'error', message: e.message || String(e) } }; }
    if (out && out.ok) break;
    await sleep(1000);
  }
  data.fill = out || { ok: false, error: { code: 'error', message: 'страница не ответила' } };
  note(`Сайт: ${data.fill.ok ? `запрос вписан (${data.fill.data.field})` : data.fill.error.message}`);
  return { ok: true, data };
}

async function siteHide(p) {
  const st = await siteLoad();
  const prog = await programTab();
  const t = await getTab(st.tabId);
  if (!st.mode || (!t && st.mode !== 'window')) {
    await restoreProgram();
    await siteSave({}, true);
    return { ok: true, data: { mode: 'none' } };
  }
  if (p && p.close) {
    await siteDrop(st);
    return { ok: true, data: { mode: st.mode, closed: true } };
  }
  if (st.mode === 'split') {
    if (inSplit(t) && splitApi()) await chrome.tabs.unsplit(t.splitViewId).catch(() => {});
    else if (inSplit(t)) { await chrome.tabs.remove(t.id).catch(() => {}); await siteSave({}, true); }   // до Chrome 155 убрать половину можно только закрыв вкладку
  } else if (st.mode === 'window') {
    await chrome.windows.update(st.windowId, { state: 'minimized' }).catch(() => {});
    await restoreProgram();
  }
  if (prog) await focusTab(prog.id);
  return { ok: true, data: { mode: st.mode } };
}

// content/drop.js просит файл дела, перетащенный из рабочего места: берём его у программы на этом компьютере.
async function siteFile(msg, sender) {
  const { port } = await settings();
  let u;
  try { u = new URL(String(msg.url || '')); } catch { throw fail('bad_url', 'Неверная ссылка на файл'); }
  if (!['127.0.0.1', 'localhost'].includes(u.hostname) || u.port !== String(port) || u.protocol !== 'http:'
      || !/^\/api\/ws\/[0-9a-f]{6,32}\/file\/f\d+$/.test(u.pathname)) {
    throw fail('bad_url', 'Перетащен не файл дела PySimpleManager');
  }
  let host = '';
  try { host = new URL(sender.url || (sender.tab && sender.tab.url) || '').hostname; } catch { /* ignore */ }
  if (!DROP_HOSTS.includes(host)) throw fail('bad_host', `Сайт ${host || '?'} не из списка расширения`);
  const r = await fetch(u.href, { cache: 'no-store' });
  if (!r.ok) throw fail('http', `программа ответила HTTP ${r.status}`);
  const buf = new Uint8Array(await r.arrayBuffer());
  if (buf.length > 40 * 1024 * 1024) throw fail('too_big', 'Файл больше 40 МБ — выберите его на сайте вручную');
  let bin = '';
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  note(`Файл дела → ${host}: ${msg.name || u.pathname.split('/').pop()}`);
  return { ok: true, b64: btoa(bin), type: r.headers.get('content-type') || '' };
}

chrome.tabs.onRemoved.addListener(async id => {
  const st = await siteLoad();
  if (st.tabId === id && st.mode !== 'window') await siteSave({}, true);
});
chrome.windows.onRemoved.addListener(async id => {
  const st = await siteLoad();
  if (st.windowId === id && st.mode === 'window') { await restoreProgram(); await siteSave({}, true); }
});

// ------------------------------------------------------------------ вкладка manager

async function pageOk(tabId) {
  try {
    const [r] = await chrome.scripting.executeScript({
      target: { tabId },
      // обычная страница manager: «старая» (#content) или новая на Bootstrap (nav.navbar, напр. базовая анкета)
      func: () => !!document.querySelector('meta[name="_csrf"]')
        && !!(document.querySelector('#content') || document.querySelector('nav.navbar')),
    });
    return !!(r && r.result);
  } catch {
    return false;   // страница ошибки Chrome, 401 и т. п.
  }
}

function waitLoaded(tabId, ms, authMs) {
  return new Promise(resolve => {
    let timer;
    const done = v => {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(onUpd);
      chrome.tabs.onRemoved.removeListener(onRem);
      authHooks.delete(tabId);
      resolve(v);
    };
    const onUpd = (id, info) => { if (id === tabId && info.status === 'complete') done(true); };
    const onRem = id => { if (id === tabId) done(false); };
    chrome.tabs.onUpdated.addListener(onUpd);
    chrome.tabs.onRemoved.addListener(onRem);
    authHooks.set(tabId, () => { clearTimeout(timer); timer = setTimeout(() => done(false), authMs); });
    timer = setTimeout(() => done(false), ms);
    chrome.tabs.get(tabId).then(t => {
      if (t.status === 'complete' && t.url && (t.url.startsWith(MANAGER) || t.url.startsWith(IDENTITY) || t.url.startsWith(GOSUSLUGI))) done(true);
    }).catch(() => done(false));
  });
}

async function ensureManagerTab() {
  const cfg = await settings();
  if (managerTabId === null) {
    const saved = await chrome.storage.session.get(['managerTabId', 'createdTab']);
    managerTabId = saved.managerTabId ?? null;
    createdTab = !!saved.createdTab;
  }
  if (managerTabId !== null) {
    try {
      const t = await chrome.tabs.get(managerTabId);
      if (t.url && t.url.startsWith(MANAGER)) {
        if (t.status !== 'complete') await waitLoaded(t.id, 30000, cfg.authWaitSec * 1000);
        await waitForLogin(t.id);
        return t.id;
      }
    } catch { /* вкладку закрыли */ }
    managerTabId = null;
  }
  note('Открываю вкладку manager');
  const tab = await chrome.tabs.create({ url: MANAGER, active: false });
  managerTabId = tab.id;
  createdTab = true;
  await chrome.storage.session.set({ managerTabId, createdTab });
  await waitLoaded(tab.id, 30000, cfg.authWaitSec * 1000);
  await waitForLogin(tab.id);
  return tab.id;
}

// Страница manager не открылась (окно «Войти», 401, страница входа): показываем вкладку оператору
// и ждём, пока в ней появится обычная страница manager.
async function waitForLogin(tabId) {
  if (await pageOk(tabId)) return;
  const { authWaitSec } = await settings();
  const msg = 'manager просит вход: введите логин и пароль во вкладке manager в Chrome '
    + '(если окна «Войти» нет — обновите вкладку, F5)';
  note(msg);
  if (currentReq) send({ type: 'progress', id: currentReq, step: 'auth_required', message: msg });
  try {
    const t = await chrome.tabs.update(tabId, { active: true });
    await chrome.windows.update(t.windowId, { focused: true });
  } catch { /* вкладку закрыли */ }
  const until = Date.now() + authWaitSec * 1000;
  while (Date.now() < until) {
    await sleep(1000);
    try { await chrome.tabs.get(tabId); } catch { break; }
    if (await pageOk(tabId)) {
      note('Вход в manager выполнен');
      if (currentReq) send({ type: 'progress', id: currentReq, step: 'auth_ok', message: 'вход в manager выполнен, продолжаю' });
      return;
    }
  }
  throw fail('not_logged_in', `Вход в manager не выполнен за ${authWaitSec} с. Войдите в manager в Chrome и повторите.`);
}

async function scheduleTabClose() {
  clearTimeout(closeTimer);
  const { closeTabAfterSec } = await settings();
  if (!closeTabAfterSec || !createdTab || managerTabId === null) return;
  closeTimer = setTimeout(async () => {
    if (state.busy || managerTabId === null) return;
    try {
      const t = await chrome.tabs.get(managerTabId);
      if (!t.active) await chrome.tabs.remove(managerTabId);   // вкладку, которую смотрит оператор, не закрываем
    } catch { /* уже закрыта */ }
    managerTabId = null;
    createdTab = false;
    chrome.storage.session.set({ managerTabId: null, createdTab: false }).catch(() => {});
  }, closeTabAfterSec * 1000);
}

// Окно «Войти» (HTTP-авторизация manager): показываем вкладку и сообщаем doctool.
chrome.webRequest.onAuthRequired.addListener(details => {
  if (details.tabId < 0 || details.tabId !== managerTabId) return;
  note('manager показал окно «Войти»');
  chrome.tabs.update(details.tabId, { active: true })
    .then(t => chrome.windows.update(t.windowId, { focused: true }))
    .catch(() => {});
  const hook = authHooks.get(details.tabId);
  if (hook) hook();
}, { urls: ['https://manager.reg.ru/*'] });

// ------------------------------------------------------------------ сообщения от вкладки и окна расширения

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg && msg.type === 'progress') {
    send({ type: 'progress', id: msg.reqId, step: msg.step, message: msg.message });
    return;
  }
  if (msg && msg.type === 'state') {
    reply({ ...state, version: VERSION, managerTab: managerTabId !== null });
    return;
  }
  if (msg && msg.type === 'reconnect') {
    if (ws) { try { ws.close(); } catch { /* ignore */ } }
    ws = null;
    backoff = 2000;
    connect().then(() => reply({ ok: true }));
    return true;
  }
  if (msg && msg.type === 'site_file') {
    siteFile(msg, sender).then(reply).catch(e => reply({ ok: false, message: e.message || String(e) }));
    return true;
  }
  if (msg && msg.type === 'check_manager') {
    checkManager()
      .then(r => reply({ ok: true, login: r.data.login }))
      .catch(e => reply({ ok: false, message: e.message }));
    return true;
  }
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes.port) {
    if (ws) { try { ws.close(); } catch { /* ignore */ } }
    ws = null;
    backoff = 2000;
    connect();
  }
});

chrome.alarms.create('doctool-reconnect', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(a => { if (a.name === 'doctool-reconnect') connect(); });
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
badge();
connect();
