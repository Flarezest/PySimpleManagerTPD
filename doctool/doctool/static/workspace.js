/* PySimpleManager — рабочее место оператора (0.7.6; 0.8.0 — строка «Поиск», меню «Окна», удаление дел; 0.8.1 — дело
 * без файлов и добавление файлов в открытое дело, выделение текста PDF, без окна «Сверка»; 0.8.2 — кнопки «Смена
 * админа через manager» и «Безбумажные операции» (доверенность в manager) при любой операции; 0.8.3 — верхняя панель
 * убирается наверх, стол с окнами растёт).
 *
 * Дело из архива тикетницы (или пустое): страницы по документам (лента), плавающие окна (заявление, паспорт, домены,
 * строка таблицы, Sd, manager), смена администратора в manager через расширение, строка для таблицы xlsx, строка
 * «Поиск» (Ctrl+K) — сайт открывается рядом с рабочим местом через расширение, файлы дела перетаскиваются на сайт.
 * Сервер — /api/ws/* (doctool/workspace.py, web.py). Общие функции страницы ($, esc, api, copyText, showKPop,
 * showEsia, FLAG_RU, FLAG_CLS, ST) — из index.html.
 */
(() => {
'use strict';
const q = (s, r = document) => r.querySelector(s);
const qq = (s, r = document) => [...r.querySelectorAll(s)];
const store = {
  get(k, d){ try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
};
let toastT = 0;
function toast(t, ms = 4200){ const el = q('#wsToast'); el.innerHTML = t; el.classList.add('on'); clearTimeout(toastT); toastT = setTimeout(() => el.classList.remove('on'), ms); }
const ICON = {
  min: '<svg viewBox="0 0 16 16"><path d="M3 12h10"/></svg>',
  max: '<svg viewBox="0 0 16 16"><rect x="3" y="3" width="10" height="10" rx="1"/></svg>',
  close: '<svg viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8"/></svg>',
  detach: '<svg viewBox="0 0 16 16"><rect x="2" y="5" width="9" height="9" rx="1"/><path d="M7 2h7v7M14 2L8 8"/></svg>',
};
async function getJSON(url){
  const r = await fetch(url, {cache: 'no-store'});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
  return j;
}
const today = () => new Date().toLocaleDateString('ru-RU');

const W = {id: null, ws: null, res: null, meta: null, logLen: 0, timer: null, sel: null, domain: '', built: false,
           operator: store.get('pysm.operator', {login: '', surname: ''}), docx: {}, recheck: false, lookupTask: null,
           lastJob: '', lastStatus: ''};
window.WS = {shown, domainByName: n => ((W.res && W.res.result.domains) || []).find(d => d.domain === n)};

const CLS = {application: 'Заявление', passport: 'Паспорт', other: 'Прочее', unknown: 'Не определено', hidden: 'Скрытые (пустые, повторы)'};
const pages = () => (W.ws && W.ws.pages) || [];
const pageById = id => pages().find(p => p.id === id);
const st = () => (W.ws && W.ws.state) || {};
const op = () => ((W.meta && W.meta.operations) || []).find(o => o.title === (st().operation || q('#wsOp').value)) || {title: q('#wsOp').value, column4: 'domain'};

// ================================================================ сохранение состояния дела
let saveT = 0, pending = {}, saveGen = 0, inflight = 0;
// ответ опроса, запрошенный до сохранения, не должен затирать свежие локальные правки (отметки, строка таблицы)
const stateBusy = gen => gen !== saveGen || inflight > 0 || Object.keys(pending).length > 0;
function saveState(part, now){
  saveGen++;
  Object.entries(part).forEach(([k, v]) => {
    if (v && typeof v === 'object' && !Array.isArray(v)) pending[k] = {...(pending[k] || {}), ...v};
    else pending[k] = v;
  });
  if (W.ws) Object.entries(part).forEach(([k, v]) => {
    W.ws.state[k] = (v && typeof v === 'object' && !Array.isArray(v)) ? {...(W.ws.state[k] || {}), ...v} : v;
  });
  clearTimeout(saveT);
  const go = async () => {
    const body = pending; pending = {};
    if (!W.id || !Object.keys(body).length) return;
    inflight++;
    try { await api(`/api/ws/${W.id}/state`, body); } catch (e) { toast('Не сохранилось: ' + esc(e.message)); }
    finally { inflight--; }
  };
  if (now) return go();
  saveT = setTimeout(go, 400);
}

// ================================================================ приём дела
function fillSelect(sel, items, value){
  sel.innerHTML = items.map(t => `<option ${t === value ? 'selected' : ''}>${esc(t)}</option>`).join('');
}
async function init(){
  try { W.meta = await getJSON('/api/ws/meta'); } catch (e) { return; }
  const ops = W.meta.operations.map(o => o.title), vias = W.meta.receipt_methods;
  fillSelect(q('#wsOpNew'), ops, store.get('pysm.ws.op', ops[0]));
  fillSelect(q('#wsViaNew'), vias, store.get('pysm.ws.via', vias[0]));
  fillSelect(q('#wsOp'), ops, ops[0]);
  fillSelect(q('#wsVia'), vias, vias[0]);
  const drop = q('#wsDrop');
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); if (e.dataTransfer.files.length) createCase([...e.dataTransfer.files]); });
  drop.addEventListener('click', e => { if (e.target.id !== 'wsFiles') q('#wsFiles').click(); });
  q('#wsFiles').addEventListener('change', e => { if (e.target.files.length) createCase([...e.target.files]); e.target.value = ''; });
  // (0.8.1) дело без файлов — сразу к окнам; файлы можно добавить потом (перетащить или «Добавить файлы»)
  q('#wsEmpty').addEventListener('click', () => createCase([]));
  q('#wsAddInput').addEventListener('change', e => { if (e.target.files.length) addFiles([...e.target.files]); e.target.value = ''; });
  document.addEventListener('click', async e => {
    const b = e.target.closest && e.target.closest('[data-wspaste]');
    if (!b) return;
    const inp = q('#' + b.dataset.wspaste);
    try {
      const t = (await navigator.clipboard.readText()).trim();
      if (t){ inp.value = /^\d{6,}$/.test(t) ? '#' + t : t; inp.dispatchEvent(new Event('input', {bubbles: true})); return; }
    } catch (x) {}
    inp.focus(); toast('Вставьте номер тикета: Ctrl+V');
  });
  q('#wsTicket').addEventListener('input', () => { saveState({ticket: q('#wsTicket').value.trim()}); renderRow(); renderMini(); });
  q('#wsOp').addEventListener('change', () => {
    saveState({operation: q('#wsOp').value}, true); store.set('pysm.ws.op', q('#wsOp').value);
    renderRow(); renderSteps(); renderGate();
    if (W.ws && W.ws.status === 'ready' && !W.ws.job_id) setRecheck(true);
  });
  q('#wsVia').addEventListener('change', () => { saveState({via: q('#wsVia').value}); store.set('pysm.ws.via', q('#wsVia').value); renderRow(); renderMini(); });
  q('#wsRecheck').addEventListener('click', recheck);
  q('#wsCopyRow2').addEventListener('click', copyRow);
  q('#wsOpenMgr').addEventListener('click', gateClick);
  q('#wsOpenLoa').addEventListener('click', openLoa);
  q('#wsStripT').addEventListener('click', () => { W.stripManual = true; stripMode(!q('#wsStrip').classList.contains('compact')); });
  // на невысоком экране лента — одной строкой, пока оператор сам не переключил
  addEventListener('resize', () => { if (!W.stripManual) stripMode(innerHeight < 820); });
  qq('[data-wspreset]').forEach(b => b.addEventListener('click', () => applyPreset(b.dataset.wspreset)));
  q('#wsSaveLayout').addEventListener('click', saveLayout);
  q('#wsJoin').addEventListener('click', joinPages);
  // (0.8.0) строка «Поиск» и меню «Окна»
  q('#wsSearchBtn').addEventListener('click', toggleSearch);
  q('#wsWinBtn').addEventListener('click', e => openWinMenu(e.currentTarget));
  const sb = q('#wsSbar');
  sb.addEventListener('click', sbarClick);
  sb.addEventListener('mousedown', e => { if (e.target.closest('#wsSdrop')) e.preventDefault(); });   // щелчок по списку не уводит фокус
  sb.addEventListener('input', e => { if (e.target.id === 'wsQ') SR.q = e.target.value; });
  sb.addEventListener('change', e => { if (e.target.id === 'wsOpenIn'){ store.set('pysm.ws.openin', e.target.value); toast('Сайт будет открываться: ' + OPEN_IN_RU[e.target.value]); } });
  sb.addEventListener('focusin', e => { if (e.target.id === 'wsQ') renderDrop(); });
  sb.addEventListener('focusout', e => { if (e.target.id === 'wsQ') setTimeout(() => { if (document.activeElement !== q('#wsQ')) hideDrop(); }, 150); });
  sb.addEventListener('keydown', e => {
    if (e.target.id !== 'wsQ') return;
    if (e.key === 'Enter'){ e.preventDefault(); runSvc(0, e.shiftKey); }
    if (e.key === 'Escape'){ e.preventDefault(); const d = q('#wsSdrop'); if (d && !d.classList.contains('hidden')) hideDrop(); else hideSearch(); }
  });
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.code === 'KeyK' && W.id && !q('#tabWork').classList.contains('hidden')){ e.preventDefault(); toggleSearch(); }
    // (0.8.3) Ctrl+Shift+↑ — убрать верхнюю панель, Ctrl+Shift+↓ — вернуть (в полях ввода — обычное выделение текста)
    if (e.ctrlKey && e.shiftKey && !e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown') && W.id && !q('#tabWork').classList.contains('hidden')
        && !(e.target.closest && e.target.closest('input,textarea,select,[contenteditable]'))){ e.preventDefault(); setTop(e.key === 'ArrowUp'); }
  });
  // (0.8.3) верхняя панель: убрать наверх / вернуть; тонкая полоса вместо неё («Перепроверить» — и в полосе)
  q('#wsTopHide').addEventListener('click', () => setTop(true));
  q('#wsTopShow').addEventListener('click', () => setTop(false));
  q('#wsMiniSearch').addEventListener('click', () => { toggleSearch(); renderMini(); });
  q('#wsMiniRecheck').addEventListener('click', recheck);
  q('#wsMiniWin').addEventListener('click', e => openWinMenu(e.currentTarget));
  q('#wsMini').addEventListener('dblclick', e => { if (!e.target.closest('button')) setTop(false); });
  const ro = new ResizeObserver(measureTop);
  ['#appTop', '#wsTop'].forEach(s => { const el = q(s); if (el) ro.observe(el); });
  stripMode(innerHeight < 820);
  const m = /ws=([0-9a-f]{6,32})/.exec(location.hash);
  if (m) openCase(m[1]); else showIntake();
  if (!q('#tabWork').classList.contains('hidden')) shown();
}
function shown(){ if (W.id) WM.all().forEach(w => { WM.apply(w); w.onResize && w.onResize(); }); }
function stripMode(compact){ q('#wsStrip').classList.toggle('compact', compact); q('#wsStripT').textContent = compact ? 'Лента ▾' : 'Лента ▴'; }

// ================================================================ (0.8.3) верхняя панель: убрать наверх / вернуть
// Шапка программы с вкладками (#appTop) и шапка дела с лентой и панелью раскладки (#wsTop) уезжают наверх на свою
// высоту (--h), стол с окнами растёт — окна держат доли стола и растут вместе с ним. Остаётся полоса #wsMini: «▾ Панель»,
// тикет · операция · способ получения, шаги дела, «Поиск», «Окна», свёрнутые окна (#wsDock переносится в полосу).
// Выбор запоминается (localStorage pysm.ws.topoff); на экране приёма и на других вкладках панель на месте.
const TOP = {off: !!store.get('pysm.ws.topoff', false)};
function measureTop(){
  ['#appTop', '#wsTop'].forEach(s => { const el = q(s); if (el && el.offsetHeight) el.style.setProperty('--h', el.offsetHeight + 'px'); });
}
function applyTop(){
  const on = !!(TOP.off && W.id);
  measureTop();
  document.body.classList.toggle('ws-topoff', on);
  const dk = q('#wsDock'), hide = q('#wsTopHide');
  if (on && dk.parentNode !== q('#wsMiniDock')) q('#wsMiniDock').appendChild(dk);
  if (!on && dk.parentNode !== hide.parentNode) hide.parentNode.insertBefore(dk, hide);
  renderMini();
}
function setTop(off){
  if (TOP.off === !!off) return;
  TOP.off = !!off; store.set('pysm.ws.topoff', TOP.off);
  closeWinPop(); closeGatePop();
  applyTop();
  if (off) toast('Панель убрана наверх — вернуть: «▾ Панель» или Ctrl+Shift+↓', 3500);
}
function renderMini(){
  const box = q('#wsMiniInfo'); if (!box) return;
  const t = ticketNo(), o = q('#wsOp').value, v = q('#wsVia').value;
  box.innerHTML = [t ? `<b>${esc(t)}</b>` : 'тикет не указан', esc(o), esc(v)].filter(Boolean).join(' · ');
  box.title = box.textContent;
  q('#wsMiniSteps').innerHTML = q('#wsSteps').innerHTML.replace(/ data-step="[^"]*"/g, '');   // копия шагов шапки, без data-step
  q('#wsMiniSearch').classList.toggle('on', !!SR.on);
}

async function showIntake(){
  W.id = null; W.ws = null; W.res = null;
  applyTop();                              // (0.8.3) на экране приёма верхняя панель всегда на месте
  q('#wsMain').classList.add('hidden'); q('#wsIntake').classList.remove('hidden');
  q('#wsSteps').innerHTML = '';
  try {
    const r = await getJSON('/api/ws/recent');
    const total = r.total || r.items.length;
    q('#wsRecent').innerHTML = r.items.length ? `<div class="ws-rhead"><h3 style="font-size:14px;margin:0">Недавние дела${total > r.items.length ? ` <span class="hint">(последние ${r.items.length} из ${total})</span>` : ''}</h3>
        <button class="sm" id="wsDelAll" title="Удалить все дела рабочего места: папки дел с файлами и результаты проверок">Удалить все (${total})</button></div>
      <div id="wsDelBar"></div>
      <table><tr><th>Создано</th><th>Тикет</th><th>Операция</th><th>Файлы</th><th></th></tr>
      ${r.items.map(i => `<tr><td>${esc((i.created || '').replace('T', ' '))}</td><td>${esc(i.ticket || '—')}</td><td>${esc(i.operation || '')}</td>
        <td><small>${(i.files || []).length ? esc(i.files.join(', ')) : '<span class="hint">без файлов</span>'}</small></td><td class="ws-ract"><button class="sm" data-wsopen="${esc(i.id)}">Открыть</button>
        <button class="sm" data-wsdel="${esc(i.id)}" title="Удалить дело: папка дела с файлами и результаты проверки стираются с диска">Удалить</button></td></tr>`).join('')}</table>` : '';
    bindRecent(total);
  } catch (e) { q('#wsRecent').innerHTML = ''; }
}
document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('[data-wsopen]'); if (b) openCase(b.dataset.wsopen); });

async function createCase(files){
  const msgEl = q('#wsIntakeMsg');
  msgEl.className = 'msg'; msgEl.textContent = files.length ? `Загружаю файлы: ${files.map(f => f.name).join(', ')}…` : 'Открываю рабочее место без файлов…';
  const fd = new FormData();
  files.forEach(f => fd.append('files', f, f.name));
  fd.append('state', JSON.stringify({ticket: q('#wsTicketNew').value.trim(), operation: q('#wsOpNew').value, via: q('#wsViaNew').value}));
  store.set('pysm.ws.op', q('#wsOpNew').value); store.set('pysm.ws.via', q('#wsViaNew').value);
  try {
    const r = await fetch('/api/ws', {method: 'POST', body: fd});
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || 'ошибка');
    msgEl.className = 'msg hidden';
    q('#wsTicketNew').value = '';
    openCase(j.id);
  } catch (e) { msgEl.className = 'msg fail'; msgEl.textContent = 'Не получилось: ' + e.message; }
}

// (0.8.1) файлы в открытое дело: разбираются только новые страницы, затем проверка запускается заново
async function addFiles(files){
  if (!W.id || !files.length) return;
  if (W.ws && !['ready', 'error'].includes(W.ws.status)){ toast('Дело ещё разбирается — добавьте файлы, когда разбор закончится'); return; }
  const fd = new FormData();
  files.forEach(f => fd.append('files', f, f.name));
  toast(`Добавляю в дело: ${esc(files.map(f => f.name).join(', '))}…`);
  try {
    const r = await fetch(`/api/ws/${W.id}/files`, {method: 'POST', body: fd});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
    setRecheck(false);
    poll(true);
  } catch (e) { toast('Не добавилось: ' + esc(e.message), 6000); }
}
// файлы с компьютера (не миниатюры и не файлы дела) над рабочим местом — добавить в дело
const osFiles = e => { const t = [...((e.dataTransfer && e.dataTransfer.types) || [])]; return t.includes('Files') && !t.includes('text/plain') && !t.includes(FILE_MARK); };
let addOverT = 0;
function addOver(on){
  const d = q('#wsDesk'); if (!d) return;
  clearTimeout(addOverT);
  if (on){ d.classList.add('addover'); addOverT = setTimeout(() => d.classList.remove('addover'), 200); }
  else d.classList.remove('addover');
}

function openCase(id){
  if (W.id !== id){ WM.closeAll(); W.res = null; W.ws = null; W.logLen = 0; W.docx = {}; W.text = {}; W.domain = ''; W.lastJob = ''; setRecheck(false); SR.last = null; SR.q = ''; }
  W.id = id;
  if (location.hash !== '#ws=' + id) history.replaceState(null, '', '#ws=' + id);
  q('#wsIntake').classList.add('hidden'); q('#wsMain').classList.remove('hidden');
  applyTop();                              // (0.8.3) панель убрана, если оператор её убрал
  buildWindows();
  poll(true);
}
function newCase(){ history.replaceState(null, '', location.pathname); WM.closeAll(); showIntake(); }

// ================================================================ опрос сервера
async function poll(force){
  clearTimeout(W.timer);
  if (!W.id) return;
  let d;
  const gen = saveGen;
  try { d = await getJSON(`/api/ws/${W.id}?since=${W.logLen}`); }
  catch (e) {
    q('#wsStatus').textContent = 'Дело не найдено: ' + e.message;
    if (/Нет такого/.test(e.message)) { toast('Дело не найдено — начните новое'); newCase(); }
    return;
  }
  const first = !W.ws;
  const changed = force || first || d.version !== W.ws.version;
  W.logLen = d.log_len;
  if (d.log.length) W.lastLog = d.log[d.log.length - 1];
  if (W.ws && stateBusy(gen)) d.state = W.ws.state;
  W.ws = d;
  if (first){
    q('#wsTicket').value = d.state.ticket || '';
    if (d.state.operation) q('#wsOp').value = d.state.operation;
    if (d.state.via) q('#wsVia').value = d.state.via;
  }
  if (first){ renderRow(); renderDomains(); }
  const st0 = W.lastWsStatus + '|' + (d.job ? d.job.status : '');
  W.lastWsStatus = d.status;
  if (changed){ renderArch(); renderStrip(); refreshDocs(); if (SR.on){ if (first) renderSbar(); else renderSfiles(); } }
  if (!W.res && (changed || st0 !== d.status + '|' + (d.job ? d.job.status : ''))) renderDomains();
  const job = d.job;
  const jst = job ? job.status : '';
  const sl = {unpacking: 'Распаковываю…', pages: 'Готовлю страницы…', classifying: 'Разбираю страницы…', ready: '', error: 'Ошибка: ' + (d.error || '')}[d.status] || '';
  const jl = job && (jst === 'running' || jst === 'queued') ? 'Проверка: ' + ((job.log || []).slice(-1)[0] || 'в очереди').replace(/^\s*[\d.]+ с\s+/, '') : '';
  q('#wsStatus').textContent = sl ? `${sl} ${W.lastLog ? '— ' + W.lastLog.replace(/^\d\d:\d\d:\d\d\s+/, '') : ''}` : jl || (jst === 'error' ? 'Проверка: ошибка — ' + (job.error || '') : '');
  if (job && jst === 'done' && (W.lastJob !== job.id || W.lastStatus !== 'done')) { W.lastJob = job.id; W.lastStatus = 'done'; await loadResult(); }
  else if (job) W.lastStatus = jst;
  if (!W.applied && d.status !== 'unpacking') { W.applied = true; }
  renderSteps(); renderGate();
  const busy = d.status !== 'ready' && d.status !== 'error' || jst === 'running' || jst === 'queued';
  W.timer = setTimeout(() => poll(false), busy ? 800 : 5000);
}
async function loadResult(){
  if (!W.id) return;
  try {
    const r = await getJSON(`/api/ws/${W.id}/result${W.domain ? '?domain=' + encodeURIComponent(W.domain) : ''}`);
    if (!r.ready) return;
    W.res = r;
    renderDomains(); renderRow(); renderGate(); renderSteps(); renderMgr(); renderLoa(); renderSst();
  } catch (e) { toast('Результат не получен: ' + esc(e.message)); }
}

// ================================================================ шапка дела и лента страниц
function renderArch(){
  const fs = W.ws.files, ps = pages();
  const sig = fs.filter(f => f.kind === 'sig').length;
  q('#wsArch').innerHTML = `Файлов: <b>${fs.filter(f => f.kind !== 'archive').length}</b> · страниц: <b>${ps.filter(p => p.kind !== 'crop').length}</b>${sig ? ` · подписей: ${sig}` : ''}
    ${(W.ws.notes || []).length ? `<span class="badge s-warn" title="${esc(W.ws.notes.join('\n'))}">замечаний: ${W.ws.notes.length}</span>` : ''}
    <button class="sm" id="wsAdd" type="button" title="Добавить в дело файлы или архив (можно просто перетащить их на рабочее место)">Добавить файлы</button>
    <button class="sm" id="wsNew" type="button">Новое дело</button>`;
  q('#wsNew').onclick = newCase;
  q('#wsAdd').onclick = () => q('#wsAddInput').click();
}
function thumbHTML(p, cur){
  const cf = p.manual ? '<span class="cf man">вручную</span>' : `<span class="cf${p.conf < .8 ? ' low' : ''}">${Math.round(p.conf * 100)}%</span>`;
  const img = p.kind === 'docx' ? '<span style="font:600 11px sans-serif;color:#2b6cb0">DOCX</span>'
    : `<img src="${p.thumb}" alt="" style="transform:rotate(${p.rot || 0}deg)">`;
  const short = p.file_name.split('/').pop();
  return `<div class="ws-th${cur ? ' cur' : ''}${p.cls === 'hidden' ? ' hid' : ''}" draggable="true" data-pid="${p.id}"
    title="${esc(p.label)} — ${esc(p.sub)}\n${esc(p.why)}${p.auto_rot ? '\nповёрнута автоматически' : ''}\nПеретащите в другую группу или в окно документа">
    ${cf}<div class="tb${p.rot % 180 ? ' r' + p.rot : ''}">${img}</div>${esc(short.length > 10 ? short.slice(0, 8) + '…' : short)}${p.file_pages > 1 ? ' · ' + (p.index + 1) : ''}</div>`;
}
function renderStrip(){
  if (!W.ws) return;
  if (!W.ws.files.length){
    q('#wsStrip').innerHTML = `<div class="ws-nofiles">Файлов в деле нет — перетащите архив или файлы на рабочее место или нажмите «Добавить файлы». Окна, «Поиск» и домены работают и без них.</div>`;
    return;
  }
  const cur = new Set(WM.all().filter(w => w.kind === 'doc' || w.kind === 'page').map(w => (docPages(w)[w.idx] || {}).id));
  const grp = cls => {
    const ps = pages().filter(p => p.cls === cls);
    const empty = {application: 'Нет страниц', passport: 'Нет страниц — перетащите сюда или выделите рамкой на странице', other: 'Конверт, опись, нотариус…',
      unknown: 'Всё определено', hidden: 'Нет'}[cls];
    return `<div class="ws-grp" data-cls="${cls}"><h4>${CLS[cls]} · ${ps.length}</h4><div class="items">${ps.map(p => thumbHTML(p, cur.has(p.id))).join('') || `<span class="empty">${empty}</span>`}</div></div>`;
  };
  const extra = W.ws.files.filter(f => f.kind === 'sig' || f.kind === 'other' && !/TIFF/.test(f.note));
  const fileBox = extra.length ? `<div class="ws-grp"><h4>Подпись и другие файлы · ${extra.length}</h4><div class="items">${extra.map(f =>
      `<div class="ws-file" draggable="true" data-dragfile="${esc(f.id)}" title="Перетащите на страницу сайта (строка «Поиск») — файл положит туда расширение"><a href="/api/ws/${W.id}/file/${f.id}" target="_blank" draggable="false"><b>${esc(f.name.split('/').pop())}</b></a><br><span class="hint">${esc(f.note)}</span></div>`).join('')}</div></div>` : '';
  q('#wsStrip').innerHTML = ['application', 'passport', 'other', 'unknown', 'hidden'].map(grp).join('') + fileBox;
}
async function pageAction(body, msgText){
  try {
    const gen = saveGen;
    const r = await api(`/api/ws/${W.id}/pages`, body);
    const local = W.ws.state;
    W.ws = {...W.ws, ...r.ws};
    if (stateBusy(gen) || !r.ws.state) W.ws.state = local;
    renderStrip(); refreshDocs(); setRecheck(true);
    if (msgText) toast(msgText);
    return r;
  } catch (e) { toast('Не получилось: ' + esc(e.message)); return null; }
}
function movePage(pid, cls){
  const p = pageById(pid);
  if (!p || p.cls === cls) return Promise.resolve(null);
  return pageAction({action: 'move', pid, cls}, `${esc(p.label)} → «${CLS[cls]}». Нажмите «Перепроверить», когда разложите все страницы`);
}
function setRecheck(on){ W.recheck = on; q('#wsRecheck').classList.toggle('hidden', !on); if (on) q('#wsRecheck').classList.add('primary'); q('#wsMiniRecheck').classList.toggle('hidden', !on); }
async function recheck(){
  try {
    const r = await api(`/api/ws/${W.id}/check`, {});
    setRecheck(false); W.res = null; W.lastJob = '';
    toast(r.job_id ? 'Проверка запущена заново' : 'Для этого типа операции проверка не настроена — сверка вручную');
    poll(true);
  } catch (e) { toast('Не получилось: ' + esc(e.message)); }
}
document.addEventListener('dragstart', e => {
  const t = e.target.closest && e.target.closest('.ws-th');
  if (t){
    e.dataTransfer.setData('text/plain', 'wspid:' + t.dataset.pid);
    // (0.8.0) миниатюру можно перетащить и на страницу сайта — туда уйдёт файл этой страницы
    const p = pageById(t.dataset.pid);
    if (p && p.kind !== 'crop' && W.ws){ setFileDrag(e.dataTransfer, W.ws.files.find(f => f.id === p.file)); e.dataTransfer.effectAllowed = 'copyMove'; }
    else e.dataTransfer.effectAllowed = 'move';
    return;
  }
  const fd = e.target.closest && e.target.closest('[data-dragfile]');
  if (fd && W.ws){
    const info = setFileDrag(e.dataTransfer, W.ws.files.find(f => f.id === fd.dataset.dragfile));
    if (info) e.dataTransfer.setData('text/plain', 'pysm-file:' + JSON.stringify(info));
    e.dataTransfer.effectAllowed = 'copy';
  }
});
function dropTarget(e){
  const g = e.target.closest && e.target.closest('.ws-grp[data-cls]');
  if (g) return {el: g, cls: g.dataset.cls};
  const w = e.target.closest && e.target.closest('.ws-win');
  if (w){ const ww = WM.get(w.dataset.id); if (ww && ww.kind === 'doc') return {el: w, cls: ww.cls, win: ww}; }
  return null;
}
document.addEventListener('dragover', e => {
  if (!W.id || q('#tabWork').classList.contains('hidden')) return;
  if (osFiles(e)){ if (e.target.closest && e.target.closest('#wsMain')){ e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; addOver(true); } return; }
  const t = dropTarget(e);
  qq('.ws-grp.over,.ws-win.dropok').forEach(x => x.classList.remove('over', 'dropok'));
  if (t && (e.dataTransfer.types || []).includes('text/plain')){ e.preventDefault(); t.el.classList.add(t.win ? 'dropok' : 'over'); }
});
document.addEventListener('drop', async e => {
  if (W.id && !q('#tabWork').classList.contains('hidden') && osFiles(e) && e.target.closest && e.target.closest('#wsMain')){
    e.preventDefault(); addOver(false);
    if (e.dataTransfer.files.length) addFiles([...e.dataTransfer.files]);
    return;
  }
  const t = dropTarget(e);
  qq('.ws-grp.over,.ws-win.dropok').forEach(x => x.classList.remove('over', 'dropok'));
  const data = (e.dataTransfer && e.dataTransfer.getData('text/plain')) || '';
  if (!t || !data.startsWith('wspid:')) return;
  e.preventDefault();
  const pid = data.slice(6);
  await movePage(pid, t.cls);
  if (t.win){ const i = docPages(t.win).findIndex(p => p.id === pid); if (i >= 0){ t.win.idx = i; renderDoc(t.win); } }
});
document.addEventListener('click', e => {
  const t = e.target.closest && e.target.closest('.ws-th');
  if (t) showPage(pageById(t.dataset.pid));
});
function showPage(p){
  if (!p) return;
  let w = WM.all().find(w => w.kind === 'doc' && w.cls === p.cls);
  if (!w){
    w = WM.get('oth') || openDoc('oth', p.cls, null);
    w.cls = p.cls;
  }
  const i = docPages(w).findIndex(x => x.id === p.id);
  if (i >= 0) w.idx = i;
  WM.restore(w.id); WM.focus(w.id); renderDoc(w); renderStrip();
}

// ================================================================ оконный менеджер
const WM = (() => {
  const wins = new Map();
  let z = 10, active = null;
  const desk = () => q('#wsDesk');
  const D = () => { const r = desk().getBoundingClientRect(); return {w: r.width, h: r.height, left: r.left, top: r.top}; };
  const snapOn = () => q('#wsSnapOn').checked;
  function open(id, title, opts = {}){
    if (wins.has(id)){ const w = wins.get(id); restore(id); focus(id); return w; }
    const el = document.createElement('div');
    el.className = 'ws-win'; el.dataset.id = id;
    el.innerHTML = `<div class="bar"><span class="t"></span>
      ${opts.detach ? `<button class="wb" data-a="detach" title="Открепить в отдельное окно (второй монитор)">${ICON.detach}</button>` : ''}
      <button class="wb" data-a="min" title="Свернуть">${ICON.min}</button>
      <button class="wb" data-a="max" title="Развернуть / вернуть (или двойной щелчок по заголовку)">${ICON.max}</button>
      ${opts.closable ? `<button class="wb" data-a="close" title="Закрыть">${ICON.close}</button>` : ''}</div>
      <div class="body"></div>${['n', 's', 'w', 'e', 'nw', 'ne', 'sw', 'se'].map(d => `<div class="ws-rz ${d}" data-rz="${d}"></div>`).join('')}`;
    desk().appendChild(el);
    const w = {id, el, title, kind: opts.kind || 'panel', fr: opts.fr || {x: .2, y: .1, w: .4, h: .6}, min: false, max: false, prev: null, ...(opts.extra || {})};
    wins.set(id, w);
    setTitle(w, title); apply(w); focus(id); dock();
    return w;
  }
  function setTitle(w, t){ w.title = t; q('.bar .t', w.el).innerHTML = t; dock(); }
  function apply(w){
    const d = D();
    if (!d.w || !d.h) return;
    Object.assign(w.el.style, {left: Math.round(w.fr.x * d.w) + 'px', top: Math.round(w.fr.y * d.h) + 'px',
      width: Math.round(w.fr.w * d.w) + 'px', height: Math.round(w.fr.h * d.h) + 'px'});
    w.el.style.display = w.min ? 'none' : '';
  }
  function rectPx(w){ const d = D(); return {x: w.fr.x * d.w, y: w.fr.y * d.h, w: w.fr.w * d.w, h: w.fr.h * d.h}; }
  function setPx(w, r){ const d = D(); if (!d.w || !d.h) return; w.fr = {x: r.x / d.w, y: r.y / d.h, w: r.w / d.w, h: r.h / d.h}; apply(w); }
  function setFr(w, fr, gap = 3){ const d = D(); if (!d.w) { w.fr = {...fr}; return; } setPx(w, {x: fr.x * d.w + gap, y: fr.y * d.h + gap, w: fr.w * d.w - 2 * gap, h: fr.h * d.h - 2 * gap}); }
  function focus(id){
    const w = wins.get(id); if (!w) return;
    w.el.style.zIndex = ++z;
    if (active && active !== w) active.el.classList.remove('active');
    active = w; w.el.classList.add('active');
  }
  function minimize(id){ const w = wins.get(id); if (!w) return; w.min = true; apply(w); dock(); }
  function restore(id){ const w = wins.get(id); if (!w || !w.min) return; w.min = false; apply(w); dock(); focus(id); resized(w); }
  function toggleMax(id){
    const w = wins.get(id); if (!w) return;
    if (w.max){ w.max = false; w.fr = w.prev || w.fr; apply(w); }
    else { w.prev = {...w.fr}; w.max = true; setFr(w, {x: 0, y: 0, w: 1, h: 1}, 0); }
    resized(w);
  }
  function close(id){
    const w = wins.get(id); if (!w) return;
    if (w.pop && !w.pop.closed) w.pop.close();
    w.el.remove(); wins.delete(id); dock();
    if (w.onClose) w.onClose();
  }
  function closeAll(){ [...wins.keys()].forEach(close); }
  function dock(){
    const mins = [...wins.values()].filter(w => w.min);
    q('#wsDock').innerHTML = mins.length ? '<span>Свёрнуты:</span>' + mins.map(w => `<button data-restore="${esc(w.id)}">${w.title.replace(/<[^>]+>/g, '')}${w.pop && !w.pop.closed ? ' (откреплено)' : ''}</button>`).join('') : '';
    q('#wsJoin').classList.toggle('hidden', ![...wins.values()].some(w => w.kind === 'page'));
  }
  function resized(w){ if (w.onResize) requestAnimationFrame(() => w.onResize()); }
  function snapZone(px, py){
    const d = D(), E = 18;
    px = Math.max(0, Math.min(px, d.w)); py = Math.max(0, Math.min(py, d.h));
    if (py <= E && px > d.w * .15 && px < d.w * .85) return {x: 0, y: 0, w: 1, h: 1, full: true};
    if (px <= E) return py < d.h * .25 ? {x: 0, y: 0, w: .5, h: .5} : py > d.h * .75 ? {x: 0, y: .5, w: .5, h: .5} : {x: 0, y: 0, w: .5, h: 1};
    if (px >= d.w - E) return py < d.h * .25 ? {x: .5, y: 0, w: .5, h: .5} : py > d.h * .75 ? {x: .5, y: .5, w: .5, h: .5} : {x: .5, y: 0, w: .5, h: 1};
    return null;
  }
  function showPv(zn){
    const pv = q('#wsSnap'), d = D();
    if (!zn){ pv.style.display = 'none'; return; }
    Object.assign(pv.style, {display: 'block', left: zn.x * d.w + 3 + 'px', top: zn.y * d.h + 3 + 'px', width: zn.w * d.w - 6 + 'px', height: zn.h * d.h - 6 + 'px'});
  }
  function magnet(w, x, y, ww, hh){
    const d = D(), M = 10, gap = 6, vx = [0, d.w], vy = [0, d.h];
    wins.forEach(o => {
      if (o === w || o.min) return;
      const r = rectPx(o);
      vx.push(r.x - gap, r.x + r.w + gap, r.x, r.x + r.w); vy.push(r.y - gap, r.y + r.h + gap, r.y, r.y + r.h);
    });
    let bx = null, bd = M;
    vx.forEach(c => { if (Math.abs(x - c) < bd){ bd = Math.abs(x - c); bx = c; } if (Math.abs(x + ww - c) < bd){ bd = Math.abs(x + ww - c); bx = c - ww; } });
    let by = null; bd = M;
    vy.forEach(c => { if (Math.abs(y - c) < bd){ bd = Math.abs(y - c); by = c; } if (Math.abs(y + hh - c) < bd){ bd = Math.abs(y + hh - c); by = c - hh; } });
    return [bx ?? x, by ?? y];
  }
  function startDrag(e, w){
    if (e.button !== 0) return;
    const sx = e.clientX, sy = e.clientY;
    let r0 = rectPx(w), moved = false, zone = null;
    const bar = e.target.closest('.bar');
    bar.setPointerCapture(e.pointerId);
    document.body.classList.add('ws-dragging');
    const move = ev => {
      const dx = ev.clientX - sx, dy = ev.clientY - sy;
      if (!moved && Math.abs(dx) + Math.abs(dy) < 4) return;
      if (!moved){
        moved = true;
        if (w.max){
          const d = D(), pf = w.prev || {x: .2, y: .1, w: .5, h: .6};
          w.max = false;
          const nw = pf.w * d.w, nh = pf.h * d.h, rel = (sx - d.left) / d.w;
          r0 = {x: sx - d.left - rel * nw, y: 0, w: nw, h: nh};
        }
      }
      const d = D();
      let x = r0.x + dx, y = r0.y + dy;
      if (snapOn()) [x, y] = magnet(w, x, y, r0.w, r0.h);
      y = Math.max(0, Math.min(y, d.h - 31)); x = Math.max(-r0.w + 80, Math.min(x, d.w - 80));
      setPx(w, {x, y, w: r0.w, h: r0.h});
      zone = snapOn() ? snapZone(ev.clientX - d.left, ev.clientY - d.top) : null;
      showPv(zone);
    };
    const up = () => {
      bar.removeEventListener('pointermove', move); bar.removeEventListener('pointerup', up); bar.removeEventListener('pointercancel', up);
      document.body.classList.remove('ws-dragging'); showPv(null);
      if (zone){ if (zone.full){ w.prev = {...w.fr}; w.max = true; } setFr(w, zone, zone.full ? 0 : 3); }
      if (moved){ resized(w); presetOff(); }
    };
    bar.addEventListener('pointermove', move); bar.addEventListener('pointerup', up); bar.addEventListener('pointercancel', up);
  }
  function startResize(e, w, dir){
    if (e.button !== 0) return;
    e.preventDefault();
    const h = e.target; h.setPointerCapture(e.pointerId);
    const sx = e.clientX, sy = e.clientY, r0 = rectPx(w), MINW = 240, MINH = 140;
    w.max = false;
    document.body.classList.add('ws-dragging');
    const move = ev => {
      const d = D(), dx = ev.clientX - sx, dy = ev.clientY - sy;
      let {x, y, w: ww, h: hh} = r0;
      if (dir.includes('e')) ww = Math.min(Math.max(MINW, r0.w + dx), d.w - r0.x);
      if (dir.includes('s')) hh = Math.min(Math.max(MINH, r0.h + dy), d.h - r0.y);
      if (dir.includes('w')){ const nx = Math.max(0, Math.min(r0.x + dx, r0.x + r0.w - MINW)); ww = r0.w + (r0.x - nx); x = nx; }
      if (dir.includes('n')){ const ny = Math.max(0, Math.min(r0.y + dy, r0.y + r0.h - MINH)); hh = r0.h + (r0.y - ny); y = ny; }
      if (snapOn()){
        const [mx, my] = magnet(w, dir.includes('w') ? x : x + ww, dir.includes('n') ? y : y + hh, 0, 0);
        if (dir.includes('e') && mx - x >= MINW) ww = mx - x;
        if (dir.includes('s') && my - y >= MINH) hh = my - y;
        if (dir.includes('w') && r0.x + r0.w - mx >= MINW){ ww = r0.x + r0.w - mx; x = mx; }
        if (dir.includes('n') && r0.y + r0.h - my >= MINH){ hh = r0.y + r0.h - my; y = my; }
      }
      setPx(w, {x, y, w: ww, h: hh});
      resized(w);
    };
    const up = () => {
      h.removeEventListener('pointermove', move); h.removeEventListener('pointerup', up); h.removeEventListener('pointercancel', up);
      document.body.classList.remove('ws-dragging'); presetOff();
    };
    h.addEventListener('pointermove', move); h.addEventListener('pointerup', up); h.addEventListener('pointercancel', up);
  }
  function bind(){
    desk().addEventListener('pointerdown', e => {
      const wel = e.target.closest('.ws-win'); if (!wel) return;
      const w = wins.get(wel.dataset.id); if (!w) return;
      focus(w.id);
      if (e.target.closest('.wb')) return;
      const rz = e.target.closest('.ws-rz');
      if (rz) return startResize(e, w, rz.dataset.rz);
      if (e.target.closest('.bar') && !e.target.closest('button,input,select')) return startDrag(e, w);
    });
    desk().addEventListener('dblclick', e => {
      const bar = e.target.closest('.bar'); if (!bar || e.target.closest('button')) return;
      toggleMax(bar.parentNode.dataset.id);
    });
    desk().addEventListener('click', e => {
      const b = e.target.closest('.wb'); if (!b) return;
      const id = b.closest('.ws-win').dataset.id, a = b.dataset.a;
      if (a === 'min') minimize(id); else if (a === 'max') toggleMax(id); else if (a === 'close') close(id);
      else if (a === 'detach') detach(wins.get(id));
    });
    new ResizeObserver(() => wins.forEach(w => { apply(w); resized(w); })).observe(desk());
    q('#wsDock').addEventListener('click', e => {
      const b = e.target.closest('[data-restore]'); if (!b) return;
      const w = wins.get(b.dataset.restore);
      if (w && w.pop && !w.pop.closed){ w.pop.focus(); return; }
      restore(b.dataset.restore);
    });
  }
  return {open, close, closeAll, focus, minimize, restore, toggleMax, setFr, apply, setTitle, dock, bind, rectPx,
    get: id => wins.get(id), all: () => [...wins.values()]};
})();

// ---------------------------------------------------------------- раскладки
const CORE = ['app', 'pas', 'dom', 'row'];
const PRESETS = {
  cols3: {app: [0, 0, .4, 1], pas: [.4, 0, .32, 1], dom: [.72, 0, .28, .62], row: [.72, .62, .28, .38]},
  docs: {app: [0, 0, .5, 1], pas: [.5, 0, .5, 1], dom: null, row: null},
  grid: {app: [0, 0, .5, .5], pas: [.5, 0, .5, .5], dom: [0, .5, .5, .5], row: [.5, .5, .5, .5]},
};
const layoutKey = () => 'pysm.ws.layout.' + (q('#wsOp').value || '');
function presetOff(){ qq('[data-wspreset]').forEach(b => b.classList.remove('on')); }
function applyPreset(name){
  qq('[data-wspreset]').forEach(b => b.classList.toggle('on', b.dataset.wspreset === name));
  if (name === 'cascade'){
    CORE.forEach((id, i) => { const w = WM.get(id); if (!w) return; w.min = false; w.max = false; WM.setFr(w, {x: .03 + i * .045, y: .03 + i * .06, w: .5, h: .66}, 0); WM.focus(id); });
  } else if (name === 'mine'){
    const L = store.get(layoutKey(), null) || store.get('pysm.ws.layout.', null);
    if (!L){ toast('Своей раскладки ещё нет: расставьте окна и нажмите «Запомнить мою»'); return applyPreset('cols3'); }
    CORE.forEach(id => { const w = WM.get(id), s = L[id]; if (!w || !s) return; w.max = false; w.min = !!s.min; w.fr = s.fr; WM.apply(w); });
  } else {
    const P = PRESETS[name];
    CORE.forEach(id => {
      const w = WM.get(id), f = P[id];
      if (!w) return;
      w.max = false;
      if (!f){ w.min = true; WM.apply(w); return; }
      w.min = false; WM.setFr(w, {x: f[0], y: f[1], w: f[2], h: f[3]});
    });
  }
  store.set('pysm.ws.preset', name);
  WM.dock();
  WM.all().forEach(w => w.onResize && requestAnimationFrame(() => w.onResize()));
}
function saveLayout(){
  const L = {}; CORE.forEach(id => { const w = WM.get(id); if (w) L[id] = {fr: w.fr, min: w.min}; });
  store.set(layoutKey(), L);
  toast(`Раскладка запомнена для «${esc(q('#wsOp').value)}» — следующее дело откроется так же`);
  store.set('pysm.ws.preset', 'mine');
}
function buildWindows(){
  if (WM.get('app')) return;
  openDoc('app', 'application', {x: 0, y: 0, w: .4, h: 1});
  openDoc('pas', 'passport', {x: .4, y: 0, w: .32, h: 1});
  openDom({x: .72, y: 0, w: .28, h: .62});
  openRow({x: .72, y: .62, w: .28, h: .38});
  const pre = store.get('pysm.ws.preset', 'cols3');
  applyPreset(store.get(layoutKey(), null) && pre === 'mine' ? 'mine' : (PRESETS[pre] || pre === 'cascade' ? pre : 'cols3'));
  WM.focus('app');
}

// ================================================================ окна документов
function docPages(w){
  if (w.kind === 'page') return pages().filter(p => p.id === w.pid);
  return pages().filter(p => p.cls === w.cls);
}
function openDoc(id, cls, fr, extra = {}){
  const w = WM.open(id, CLS[cls] || 'Документ', {kind: extra.pid ? 'page' : 'doc', detach: true, closable: !CORE.includes(id),
    fr: fr || {x: .3, y: .08, w: .34, h: .7}, extra: {cls, idx: 0, fit: true, zoom: 1, selmode: false, ...extra}});
  w.onResize = () => { if (w.fit) renderDoc(w, true); };
  w.onClose = () => renderStrip();
  renderDoc(w);
  return w;
}
function refreshDocs(){ WM.all().filter(w => w.kind === 'doc' || w.kind === 'page').forEach(w => renderDoc(w, true)); }
function renderDoc(w, keepScroll){
  if (!W.ws) return;
  const ps = docPages(w);
  if (w.idx >= ps.length) w.idx = Math.max(0, ps.length - 1);
  const p = ps[w.idx];
  const body = q('.body', w.el);
  const vpOld = q('.ws-vp', body), st0 = vpOld ? [vpOld.scrollLeft, vpOld.scrollTop] : [0, 0];
  const name = w.kind === 'page' ? 'Страница' : CLS[w.cls] || 'Документ';
  WM.setTitle(w, `${name}${p ? ` <small>· ${esc(p.sub || '')}</small>` : ''}`);
  if (!p){
    const hint = !['ready', 'error'].includes(W.ws.status) ? 'Страницы разбираются…'
      : !W.ws.files.length ? 'Файлов в деле нет.<br>Перетащите сюда архив или файлы с компьютера — они добавятся в дело.'
      : w.cls === 'passport' ? 'Страниц паспорта нет.<br>Перетащите сюда миниатюру из ленты или выделите паспорт рамкой на странице заявления (⬚).'
      : 'Нет страниц.<br>Перетащите сюда миниатюру из ленты сверху.';
    body.innerHTML = `<div class="ws-doc"><div class="ws-vp" style="cursor:default"><div class="ws-empty">${hint}</div></div></div>`;
    return;
  }
  const rot = ((p.rot || 0) % 360 + 360) % 360, W0 = p.pv[0] || 800, H0 = p.pv[1] || 1100;
  const rw = rot % 180 ? H0 : W0, rh = rot % 180 ? W0 : H0;
  body.innerHTML = `<div class="ws-doc">
    <div class="ws-dtools">
      ${ps.length > 1 ? `<button data-d="prev" title="Предыдущая страница">‹</button><span>стр. ${w.idx + 1} из ${ps.length}</span><button data-d="next" title="Следующая страница">›</button><span class="sep"></span>` : ''}
      ${p.kind === 'docx' ? '<span>документ Word — текст</span>' : `<button data-d="zout" title="Уменьшить (Ctrl + колесо)">−</button><span class="zt"></span><button data-d="zin" title="Увеличить (Ctrl + колесо)">+</button>
      <button data-d="fit" title="По ширине окна">по ширине</button>${p.kind !== 'crop' ? '<button data-d="rot" title="Повернуть на 90° по часовой стрелке">↻</button>' : ''}`}
      ${w.kind === 'doc' && ps.length > 1 ? '<span class="sep"></span><button data-d="split" title="Каждую страницу — в своё окно">Разложить</button>' : ''}
      ${p.kind !== 'docx' && p.kind !== 'crop' ? `<span class="sep"></span><button data-d="crop" title="Обвести рамкой паспорт (или другую часть страницы) — вырезка уйдёт в окно «Паспорт»">${w.selmode ? 'Отмена рамки' : '⬚ Рамкой → паспорт'}</button>` : ''}
    </div>
    <div class="ws-vp${w.selmode ? ' selmode' : ''}">${p.kind === 'docx' ? '<div class="ws-docx">загрузка…</div>' : '<div class="ws-stage"></div>'}</div>
    <div class="ws-pinfo">${p.kind === 'pdf' && p.text ? '<span title="Текст страницы можно выделить мышью и скопировать (Ctrl+C), как в браузере">текст выделяется</span> · ' : ''}${esc(p.label)} · ${esc(p.sub || '')} · ${p.manual ? 'группа выбрана вручную' : 'уверенность ' + Math.round(p.conf * 100) + ' %'}${p.auto_rot ? ' · повёрнута автоматически' : ''}${p.why ? ' · ' + esc(p.why) : ''}</div></div>`;
  bindTools(w, p);
  const vp = q('.ws-vp', body);
  if (p.kind === 'docx'){
    loadDocx(p).then(html => { const box = q('.ws-docx', body); if (box) box.innerHTML = html; });
    return;
  }
  const z = w.fit ? Math.max(.05, (vp.clientWidth - 18) / rw) : w.zoom;
  w.zoom = z;
  q('.zt', body).textContent = Math.round(z * 100) + '%';
  const stage = q('.ws-stage', body);
  stage.style.width = rw * z + 'px'; stage.style.height = rh * z + 'px';
  const tr = {0: [0, 0], 90: [H0 * z, 0], 180: [W0 * z, H0 * z], 270: [0, W0 * z]}[rot];
  const tf = `width:${W0}px;height:${H0}px;transform:translate(${tr[0]}px,${tr[1]}px) rotate(${rot}deg) scale(${z})`;
  stage.innerHTML = `<img class="pg" src="${p.url}" alt="" draggable="false" style="${tf}">`
    + (p.kind === 'pdf' && p.text ? `<div class="ws-tl" data-pid="${p.id}" style="${tf}"></div>` : '');
  if (p.kind === 'pdf' && p.text) textLayer(p).then(h => { const tl = q('.ws-tl', stage); if (tl && tl.dataset.pid === p.id) tl.innerHTML = h; });
  if (keepScroll){ vp.scrollLeft = st0[0]; vp.scrollTop = st0[1]; }
  bindViewer(w, vp, stage, p, z);
  if (w.pop && !w.pop.closed) renderPopup(w);
}
// (0.8.1) текстовый слой PDF: прозрачные слова поверх превью — выделение мышью и Ctrl+C, как в просмотрщике PDF
// браузера. Слова и их места (px превью, без поворота) — /api/ws/<id>/text/<pid>; у сканов текстового слоя нет.
let measureCtx = null;
function textWidth(t){
  if (!measureCtx) measureCtx = document.createElement('canvas').getContext('2d');
  measureCtx.font = '100px sans-serif';
  return measureCtx.measureText(t).width / 100;      // ширина при размере шрифта 1px
}
function textLayerHTML(data){
  const out = [], rot = data.rot ? `rotate(${data.rot}deg) ` : '';     // страница PDF с /Rotate — строки под углом
  (data.lines || []).forEach(words => {
    words.forEach(([x, y, w, h0, t], i) => {
      const h = Math.max(4, h0), w1 = textWidth(t) * h;
      const k = w1 > 0 ? w / w1 : 1;
      out.push(`<span style="left:${x}px;top:${y}px;font-size:${h.toFixed(1)}px;transform:${rot}scaleX(${k.toFixed(3)})">${esc(t)}${i < words.length - 1 ? ' ' : ''}</span>`);
    });
    out.push('<br>');
  });
  return out.join('');
}
async function textLayer(p){
  W.text = W.text || {};
  if (W.text[p.id] === undefined){
    try { W.text[p.id] = textLayerHTML(await getJSON(`/api/ws/${W.id}/text/${p.id}`)); }
    catch (e) { W.text[p.id] = ''; }
  }
  return W.text[p.id];
}
async function loadDocx(p){
  if (!W.docx[p.id]){
    try { W.docx[p.id] = await (await fetch(`/api/ws/${W.id}/docx/${p.id}`)).text(); } catch (e) { W.docx[p.id] = '<p>не открылся</p>'; }
  }
  return W.docx[p.id];
}
function bindTools(w, p){
  q('.ws-dtools', w.el).onclick = async e => {
    const b = e.target.closest('button'); if (!b) return;
    const ps = docPages(w), a = b.dataset.d;
    if (a === 'prev'){ w.idx = (w.idx - 1 + ps.length) % ps.length; renderDoc(w); renderStrip(); }
    if (a === 'next'){ w.idx = (w.idx + 1) % ps.length; renderDoc(w); renderStrip(); }
    if (a === 'zin' || a === 'zout'){ w.fit = false; w.zoom = Math.min(5, Math.max(.05, w.zoom * (a === 'zin' ? 1.2 : 1 / 1.2))); renderDoc(w, true); }
    if (a === 'fit'){ w.fit = true; renderDoc(w); }
    if (a === 'rot'){ await pageAction({action: 'rotate', pid: p.id, rot: ((p.rot || 0) + 90) % 360}); }
    if (a === 'split') splitPages(w);
    if (a === 'crop'){
      w.selmode = !w.selmode; renderDoc(w, true);
      if (w.selmode) toast('Обведите паспорт рамкой: нажмите и тяните мышью по странице');
    }
  };
}
function bindViewer(w, vp, stage, p, z){
  vp.onwheel = e => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    const rect = vp.getBoundingClientRect(), ox = e.clientX - rect.left, oy = e.clientY - rect.top;
    const old = w.zoom, nz = Math.min(5, Math.max(.05, old * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
    const sl = (vp.scrollLeft + ox) * nz / old - ox, stp = (vp.scrollTop + oy) * nz / old - oy;
    w.fit = false; w.zoom = nz; renderDoc(w, true);
    const v2 = q('.ws-vp', w.el); v2.scrollLeft = sl; v2.scrollTop = stp;
  };
  vp.onpointerdown = e => {
    if (e.button !== 0 || e.target.closest('button')) return;
    if (w.selmode){
      e.preventDefault();
      const sr = stage.getBoundingClientRect();
      const x0 = e.clientX - sr.left, y0 = e.clientY - sr.top;
      const box = document.createElement('div'); box.className = 'ws-sel'; stage.appendChild(box);
      vp.setPointerCapture(e.pointerId);
      const mv = ev => {
        const x1 = ev.clientX - sr.left, y1 = ev.clientY - sr.top;
        Object.assign(box.style, {left: Math.min(x0, x1) + 'px', top: Math.min(y0, y1) + 'px', width: Math.abs(x1 - x0) + 'px', height: Math.abs(y1 - y0) + 'px'});
      };
      const upf = async ev => {
        vp.removeEventListener('pointermove', mv); vp.removeEventListener('pointerup', upf);
        const x1 = ev.clientX - sr.left, y1 = ev.clientY - sr.top;
        const r = [Math.min(x0, x1) / z, Math.min(y0, y1) / z, Math.abs(x1 - x0) / z, Math.abs(y1 - y0) / z];
        w.selmode = false;
        if (r[2] < 30 || r[3] < 30){ renderDoc(w, true); toast('Рамка слишком маленькая — попробуйте ещё раз'); return; }
        const res = await pageAction({action: 'crop', pid: p.id, rect: r, cls: 'passport'}, 'Вырезка → окно «Паспорт». Нажмите «Перепроверить», чтобы прочитать паспорт');
        if (res && res.page){ const pw = WM.get('pas'); WM.restore('pas'); WM.focus('pas'); pw.idx = Math.max(0, docPages(pw).findIndex(x => x.id === res.page)); renderDoc(pw); }
        else renderDoc(w, true);
      };
      vp.addEventListener('pointermove', mv); vp.addEventListener('pointerup', upf);
      return;
    }
    if (e.target.closest('.ws-tl span')) return;      // по тексту — выделение (страница не двигается)
    const sx = e.clientX, sy = e.clientY, sl = vp.scrollLeft, stp = vp.scrollTop;
    vp.setPointerCapture(e.pointerId); vp.classList.add('panning');
    const mv = ev => { vp.scrollLeft = sl - (ev.clientX - sx); vp.scrollTop = stp - (ev.clientY - sy); };
    const upf = () => { vp.classList.remove('panning'); vp.removeEventListener('pointermove', mv); vp.removeEventListener('pointerup', upf); };
    vp.addEventListener('pointermove', mv); vp.addEventListener('pointerup', upf);
  };
}
function splitPages(w){
  const ps = docPages(w), r = WM.rectPx(w), d = q('#wsDesk').getBoundingClientRect();
  const n = ps.length, ww = r.w / n;
  ps.forEach((p, i) => {
    const fr = {x: (r.x + i * ww) / d.width, y: r.y / d.height, w: ww / d.width, h: r.h / d.height};
    const pw = openDoc('pg_' + p.id, p.cls, fr, {pid: p.id});
    WM.setFr(pw, fr, 2);
  });
  WM.minimize(w.id); renderStrip();
  toast('Каждая страница — в своём окне. «Собрать страницы» на панели раскладки — обратно');
}
function joinPages(){ WM.all().filter(w => w.kind === 'page').forEach(w => WM.close(w.id)); WM.restore('app'); renderStrip(); }

// ---------------------------------------------------------------- открепление в отдельное окно браузера
function detach(w){
  if (w.kind !== 'doc' && w.kind !== 'page') return;
  const pop = window.open('', 'pysm_' + w.id + '_' + W.id, 'width=820,height=980');
  if (!pop){ toast('Браузер не дал открыть окно — разрешите всплывающие окна для этой страницы'); return; }
  w.pop = pop;
  pop.document.open();
  pop.document.write(`<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>PySimpleManager — ${w.title.replace(/<[^>]+>/g, '')}</title>
    <style>body{margin:0;background:#8a9099;font:13px "Segoe UI",system-ui,sans-serif}.bar{position:sticky;top:0;z-index:5;display:flex;gap:8px;align-items:center;padding:6px 10px;background:#fff;border-bottom:1px solid #ddd}
    .pst{position:relative;margin:10px auto;box-shadow:0 2px 12px rgba(0,0,0,.35);background:#fff;overflow:hidden}.pst img{position:absolute;left:0;top:0;transform-origin:0 0}
    .tl{position:absolute;left:0;top:0;transform-origin:0 0;line-height:1}.tl span{position:absolute;white-space:pre;color:transparent;transform-origin:0 0;cursor:text;font-family:sans-serif}
    .tl ::selection{background:rgba(0,90,255,.3)}.hint{color:#667085;font-size:12px}
    .docx{background:#fff;margin:10px auto;padding:30px 40px;max-width:820px;font:15px/1.5 "Times New Roman",serif}</style></head>
    <body><div class="bar"><b id="pt"></b><span class="hint">Окно открыто отдельно — его можно унести на второй монитор. Текст PDF выделяется и копируется.</span>
    <button id="back" style="margin-left:auto">Вернуть в рабочее место</button></div><div id="pp"></div></body></html>`);
  pop.document.close();
  pop.document.getElementById('back').onclick = () => pop.close();
  WM.minimize(w.id); WM.dock();
  renderPopup(w);
  Promise.all(docPages(w).filter(p => p.kind === 'pdf' && p.text).map(textLayer)).then(() => { if (w.pop && !w.pop.closed) renderPopup(w); });
  const t = setInterval(() => { if (pop.closed){ clearInterval(t); w.pop = null; WM.restore(w.id); renderDoc(w); } }, 700);
}
function renderPopup(w){
  const pop = w.pop, doc = pop.document, box = doc.getElementById('pp'); if (!box) return;
  doc.getElementById('pt').innerHTML = w.title;
  const Wd = Math.max(300, pop.innerWidth - 40), T = W.text || {};
  box.innerHTML = docPages(w).map(p => {
    if (p.kind === 'docx') return `<div class="docx">${W.docx[p.id] || ''}</div>`;
    const rot = p.rot || 0, W0 = p.pv[0], H0 = p.pv[1], rw = rot % 180 ? H0 : W0, rh = rot % 180 ? W0 : H0, z = Wd / rw;
    const tr = {0: [0, 0], 90: [H0 * z, 0], 180: [W0 * z, H0 * z], 270: [0, W0 * z]}[rot];
    const tf = `width:${W0}px;height:${H0}px;transform:translate(${tr[0]}px,${tr[1]}px) rotate(${rot}deg) scale(${z})`;
    return `<div class="pst" style="width:${rw * z}px;height:${rh * z}px"><img src="${location.origin}${p.url}" style="${tf}">
      ${T[p.id] ? `<div class="tl" style="${tf}">${T[p.id]}</div>` : ''}</div>`;
  }).join('');
}

// ================================================================ домены
const WST = {stop: '<span class="badge s-fail">стоп</span>', warn: '<span class="badge s-warn">внимание</span>', info: '<span class="badge s-info">к сведению</span>', '': '<span class="badge s-ok">в порядке</span>'};
const excluded = () => new Set(((st().mgr || {}).excluded) || []);
const resDomains = () => (W.res && W.res.result.domains) || [];
function openDom(fr){
  const w = WM.open('dom', 'Домены <small>· Sd, S, ЕСИА из manager</small>', {kind: 'panel', fr});
  q('.body', w.el).innerHTML = `<div class="ws-dadd"><textarea id="wsDomList" placeholder="Домены (через пробел, запятую или с новой строки)"></textarea>
    <button class="sm" id="wsDomLoad" title="Загрузить данные доменов из manager (расширение Chrome)">Загрузить из manager</button></div>
    <div class="hint" id="wsDomHint" style="padding:2px 8px"></div><div class="ws-dlist" id="wsDlist"></div>`;
  q('#wsDomLoad').onclick = domLookup;
  q('.body', w.el).addEventListener('click', e => {
    const c = e.target.closest('input[data-inc]');
    if (c){ const ex = excluded(); if (c.checked) ex.delete(c.dataset.inc); else ex.add(c.dataset.inc); saveState({mgr: {excluded: [...ex]}}); renderDomains(); renderGate(); renderRow(); renderLoa(); return; }
    const sd = e.target.closest('[data-sd]'); if (sd) openSd(sd.dataset.sd);
    const wq = e.target.closest('[data-wsq]');
    if (wq){ SR.q = wq.dataset.wsq; if (!SR.on) showSearch(); else { const i = q('#wsQ'); if (i){ i.value = SR.q; i.focus(); i.select(); } } return; }
    const es = e.target.closest('[data-wsesia]');
    if (es){ const d = resDomains().find(x => x.domain === es.dataset.wsesia); if (d && typeof showEsia === 'function') showEsia(d); }
  });
  renderDomains();
  return w;
}
function renderDomains(){
  const box = q('#wsDlist'); if (!box) return;
  const items = resDomains(), ex = excluded();
  const ta = q('#wsDomList');
  if (ta && !ta.dataset.touched){
    const list = items.length ? items.map(d => d.domain) : ((W.res && W.res.result.domains_from_app) || []);
    ta.value = list.join('\n');
  }
  if (ta) ta.oninput = () => { ta.dataset.touched = '1'; };
  if (!items.length){
    const noCheck = W.ws && W.ws.status === 'ready' && !W.ws.job_id;
    box.innerHTML = `<p class="hint" style="margin:4px">${W.res ? (W.res.result.domains_from_app || []).length ? 'Данных из manager ещё нет — нажмите «Загрузить из manager» (нужен Chrome с расширением).' : 'Домены в заявлении не найдены — впишите их выше.'
      : noCheck ? 'Впишите домены выше и нажмите «Загрузить из manager» — покажутся Sd, S, аккаунт и проверки.' : 'Домены появятся после проверки заявления.'}</p>`;
    return;
  }
  const link = (u, t) => u ? `<a class="ws-lnk" href="${esc(u)}" target="_blank">${t}</a>` : '';
  box.innerHTML = items.map(d => {
    const off = ex.has(d.domain);
    if (d.status !== 'found'){
      const wh = d.whois || {};
      return `<div class="ws-dc off"><div class="h"><b>${esc(d.domain)}</b> <span class="badge s-${d.status === 'not_found' ? 'fail' : 'review'}">${d.status === 'not_found' ? 'не найден' : 'ошибка'}</span></div>
        <div class="hint">${esc(d.error || '')}${wh.status === 'free' ? '<br>WHOIS: домен свободен' : wh.status === 'registered' ? '<br>WHOIS: зарегистрирован' + (wh.registrar ? ', ' + esc(wh.registrar) : '') : ''}</div></div>`;
    }
    const sd = d.sd || {};
    const who = d.kind === 'person' ? `${esc(d.fio)} · ${esc(sd.birth_date || '—')}` : esc(d.holder || '—');
    const v = d.verdict ? `<span class="badge s-${d.verdict}">${esc((typeof ST !== 'undefined' && ST[d.verdict]) || d.verdict)}</span>` : '';
    const kb = d.admin_comment ? `<button class="kbtn" data-k="${esc(d.domain)}" title="Комментарий администратора в S — показать">К</button>` : '';
    return `<div class="ws-dc${off ? ' off' : ''}"><div class="h"><label title="Включить домен в смену администратора и в строку таблицы"><input type="checkbox" data-inc="${esc(d.domain)}" ${off ? '' : 'checked'}></label>
        <b>${esc(d.domain)}</b>${kb} <button class="ws-lnk" data-sd="${esc(d.domain)}">Sd</button> ${link((d.urls || {}).s, 'S')} <button class="ws-lnk" data-wsesia="${esc(d.domain)}">ESIA</button>
        <span style="margin-left:auto">${WST[d.flags_level || ''] || ''}</span></div>
      <div class="g"><span>Администратор</span><span>${who} ${v}</span>
        ${d.kind === 'person' ? `<span>Паспорт</span><span>${d.passport ? `${esc(d.passport)} <button class="ws-lnk" data-wsq="${esc(d.passport)}" title="Вписать в строку «Поиск»">в поиск</button>` : '—'}${sd.passport_date || sd.passport_place ? `<small class="ws-sub">выдан ${esc(sd.passport_date || '—')}${sd.passport_place ? ' · ' + esc(sd.passport_place) : ''}</small>` : ''}</span>`
          : d.kind === 'org' ? `<span>ИНН</span><span>${sd.code ? `${esc(sd.code)} <button class="ws-lnk" data-wsq="${esc(sd.code)}" title="Вписать в строку «Поиск»">в поиск</button>` : '—'}</span>` : ''}
        <span>ЕСИА</span><span>${esc(d.esia_ru || '—')}</span>
        <span>Смена админа</span><span>${esc(d.last_admin_change || '—')}</span>
        <span>Provider</span><span>${esc(d.provider || '—')}</span>
        <span>Аккаунт</span><span class="ws-acc">${esc(d.account || '—')}${d.account ? ` <button class="ws-lnk" data-acc="${esc(d.account)}" title="Вкладка «Смена владельца ЛК» с этим аккаунтом">→ ЛК</button>` : ''}${d.login ? `<small>логин: ${esc(d.login)}</small>` : ''}${d.account_email ? `<small>e-mail: ${esc(d.account_email)}</small>` : ''}</span></div>
      ${(d.flags || []).length ? '<ul>' + d.flags.map(f => `<li><span class="badge s-${FLAG_CLS[f.level] || 'info'}">${FLAG_RU[f.level] || f.level}</span> ${esc(f.text)}</li>`).join('') + '</ul>' : ''}</div>`;
  }).join('');
}
async function domLookup(){
  const doms = q('#wsDomList').value;
  if (!doms.trim()){ toast('Впишите домены'); return; }
  const body = {domains: doms};
  if (W.res) body.job_id = W.res.result.job_id;
  try {
    const j = await api('/api/manager/lookup', body);
    q('#wsDomHint').textContent = `Загружаю из manager: ${j.domains.join(', ')}…`;
    q('#wsDomLoad').disabled = true;
    pollLookup(j.task_id, 0);
  } catch (e) { q('#wsDomHint').textContent = e.message; }
}
async function pollLookup(id, since){
  try {
    const t = await getJSON(`/api/manager/tasks/${id}?since=${since}`);
    if (t.log.length) q('#wsDomHint').textContent = t.log[t.log.length - 1];
    if (t.status === 'running'){ setTimeout(() => pollLookup(id, t.log_len), 800); return; }
    q('#wsDomLoad').disabled = false;
    q('#wsDomHint').textContent = t.status === 'error' ? 'Ошибка: ' + t.error : '';
    if (t.result) await loadResult();
    else if (t.items){ W.res = W.res || null; if (!W.res){ W.looseDomains = t.items; renderLoose(t.items); } }
  } catch (e) { q('#wsDomLoad').disabled = false; q('#wsDomHint').textContent = e.message; }
}
function renderLoose(items){
  // дело без проверки (операция без типа заявления): домены — без сверки с документами
  W.res = {result: {domains: items, domains_from_app: [], decision: {code: 'review', title: 'Без проверки документов', reasons: []}, job_id: ''}, matrix: {rows: [], domains: []}, boxes: {}, gos: {}, new_admin: {}, login_to: ''};
  renderDomains(); renderRow(); renderGate(); renderLoa();
}
function openSd(dn){
  const d = resDomains().find(x => x.domain === dn); if (!d) return;
  const id = 'sd_' + dn.replace(/[^\w.-]/g, '_');
  const dr = WM.get('dom') ? WM.rectPx(WM.get('dom')) : {x: 600, y: 40}, D = q('#wsDesk').getBoundingClientRect();
  const w = WM.open(id, `Sd · ${esc(dn)}`, {kind: 'panel', closable: true, fr: {x: Math.max(0, dr.x - 440) / D.width, y: (dr.y + 30) / D.height, w: 430 / D.width, h: Math.min(.85, 600 / D.height)}});
  const sd = d.sd || {}, s = d.s || {};
  const cmp = {}; (d.compare || []).forEach(r => { cmp[r.field] = r; });
  const C = f => cmp[f] ? (cmp[f].status === 'ok' ? 'c-ok' : cmp[f].status === 'fail' ? 'c-fail' : cmp[f].status === 'warn' ? 'c-warn' : '') : '';
  const row = (k, v, cls = '', f = '') => v ? `<tr><td>${k}${f ? `<br><span class="mono">${f}</span>` : ''}</td><td class="${cls}">${v}</td></tr>` : '';
  q('.body', w.el).innerHTML = `<div class="ws-pad">
    <h5>Администратор (${esc(d.group || '?')})</h5><table>
      ${d.kind === 'person' ? row('ФИО', esc(d.fio), C('fio'), 'person_r_*') + row('Дата рождения', esc(sd.birth_date), C('birth_date'), 'birth_date')
        + row('Серия и номер', esc(d.passport), C('passport'), 'passport_series, _number_short') + row('Дата выдачи', esc(sd.passport_date), C('passport_date'), 'passport_date')
        + row('Кем выдан', esc(sd.passport_place), C('passport_place'), 'passport_place') + row('Код подразделения', esc(sd.passport_place_id), '', 'passport_place_id')
        : row('Организация', esc(d.holder), '', 'org_r') + row('ИНН', esc(sd.code), C('code'), 'code')}
      ${row('E-mail', esc((d.emails || []).join(', ')), '', 'e_mail')}${row('Смена администратора', esc(d.last_admin_change), '', 'last_admin_change')}</table>
    <h5>Услуга (S)</h5><table>
      ${row('Аккаунт', `${esc(d.account)}${d.login ? `<br><small>логин: ${esc(d.login)}</small>` : ''}`, '', 'user_id')}${row('E-mail аккаунта', esc(d.account_email), '', 'user_details: email')}${row('Provider', esc(d.provider), '', 'provider')}
      ${row('Статус', esc(s.state || d.service_status), '', 'state')}${row('Оплачен до', esc(s.expiration_date), '', 'expiration_date')}
      <tr><td>Комментарий администратора<br><span class="mono">admin_comment</span></td><td class="${d.admin_comment ? 'c-warn' : ''}">${d.admin_comment ? esc(d.admin_comment) : '<span class="hint">пусто</span>'}</td></tr></table>
    ${(d.compare || []).length ? `<h5>Сверка с документами</h5><table>${d.compare.map(r => `<tr><td>${esc(r.title)}</td><td class="${r.status === 'ok' ? 'c-ok' : r.status === 'fail' ? 'c-fail' : r.status === 'warn' ? 'c-warn' : ''}">${esc(r.sd || '')}${r.doc ? `<br><small class="hint">документы: ${esc(r.doc)}</small>` : ''}${r.detail ? `<br><small>${esc(r.detail)}</small>` : ''}</td></tr>`).join('')}</table>` : ''}
    <p class="hint">${link2((d.urls || {}).sd, 'Открыть Sd в manager')} ${link2((d.urls || {}).s, 'Открыть S')}</p></div>`;
}
const link2 = (u, t) => u ? `<a href="${esc(u)}" target="_blank">${t}</a>` : '';

// ================================================================ строка таблицы
// номер тикета в таблице — с «#» (как в тикетнице): 20261012373031821 → #20261012373031821
function ticketNo(){ const t = q('#wsTicket').value.trim(); return /^\d{6,}$/.test(t) ? '#' + t : t; }
function incDomains(){
  // найденные в manager и не снятые галкой домены — в порядке заявления (в окне «Домены» они по проблемности)
  const fromApp = (W.res && W.res.result.domains_from_app) || [];
  const pos = d => { const i = fromApp.indexOf(d); return i < 0 ? 1e6 : i; };
  return resDomains().filter(d => d.status === 'found' && !excluded().has(d.domain)).map(d => d.domain).sort((a, b) => pos(a) - pos(b));
}
function rowDefaults(){
  const o = op(), inc = incDomains();
  const doms = inc.length ? inc : ((W.res && W.res.result.domains_from_app) || []);
  const acc = (resDomains().find(d => d.account) || {}).account || (W.res && W.res.new_admin && '') || '';
  const surname = W.operator.surname || '';
  const status = (W.meta && W.meta.status_default) || 'готово';
  return {date: today(), got: surname, done: surname, d: o.column4 === 'contract' ? acc : doms.join(', '), doms,
          op: o.title, via: q('#wsVia').value, ticket_status: [ticketNo(), status].filter(Boolean).join(' ')};
}
const ROW_KEYS = [['date', 'A · дата обработки'], ['got', 'B · принял'], ['done', 'C · обработал'], ['d', 'D · домен / договор'],
                  ['op', 'E · тип операции'], ['via', 'F · способ получения'], ['ticket_status', 'G · тикет и статус']];
function rowValues(){
  const def = rowDefaults(), ov = (st().row || {}).values || {};
  const v = {};
  ROW_KEYS.forEach(([k]) => { v[k] = ov[k] !== undefined && ov[k] !== null ? ov[k] : def[k]; });
  return {v, def, ov};
}
function rowLines(){
  const {v, def, ov} = rowValues();
  const per = !!(st().row || {}).per_domain && op().column4 === 'domain' && ov.d === undefined && def.doms.length > 1;
  const line = d => [v.date, v.got, v.done, d, v.op, v.via, v.ticket_status];
  return per ? def.doms.map(d => line(d)) : [line(v.d)];
}
function openRow(fr){
  const w = WM.open('row', 'Строка таблицы <small>· xlsx</small>', {kind: 'panel', fr});
  q('.body', w.el).addEventListener('input', e => {
    const i = e.target.closest('[data-rk]'); if (!i) return;
    const k = i.dataset.rk;
    const values = {...((st().row || {}).values || {})};
    if (k === 'got'){
      // «принял» — сам оператор: фамилия запоминается для следующих дел, «обработал» по умолчанию — тот же человек
      W.operator.surname = i.value.trim(); store.set('pysm.operator', W.operator);
      delete values.got;
    }
    const {def} = rowValues();
    if (k !== 'got'){ if (i.value === def[k]) delete values[k]; else values[k] = i.value; }
    saveState({row: {values}});
    const body = q('.body', w.el);
    qq('[data-rk]', body).forEach(x => {
      const kk = x.dataset.rk, over = values[kk] !== undefined;
      if (!over && x !== i) x.value = def[kk];          // поля по умолчанию следуют за правкой (C за B, D за доменами)
      x.classList.toggle('over', over);
      const rb = q(`[data-rreset="${kk}"]`, body);
      if (rb){ rb.classList.toggle('hidden', !over); rb.title = 'Вернуть значение по умолчанию: ' + def[kk]; }
    });
    renderRowPreview();
  });
  q('.body', w.el).addEventListener('click', e => {
    const r = e.target.closest('[data-rreset]');
    if (r){ const values = {...((st().row || {}).values || {})}; delete values[r.dataset.rreset]; saveState({row: {values}}, true); renderRow(); return; }
    if (e.target.closest('#wsCopyRow')) copyRow();
  });
  q('.body', w.el).addEventListener('change', e => {
    if (e.target.id === 'wsPer'){ saveState({row: {per_domain: e.target.checked}}); renderRowPreview(); }
  });
  renderRow();
  return w;
}
function renderRow(){
  const w = WM.get('row'); if (!w || !W.ws) return;
  const body = q('.body', w.el);
  const focusK = document.activeElement && body.contains(document.activeElement) ? document.activeElement.dataset.rk : null;
  const {v, ov, def} = rowValues(), o = op();
  body.innerHTML = `<div class="ws-pad"><div class="ws-rowf">
    ${ROW_KEYS.map(([k, t]) => `<div class="rf${k === 'd' || k === 'ticket_status' ? ' wide' : ''}"><span class="rt"><label for="wsRk_${k}">${t}</label>
      <button type="button" class="ws-lnk rr${ov[k] !== undefined ? '' : ' hidden'}" data-rreset="${k}" title="Вернуть значение по умолчанию: ${esc(def[k])}">↺</button></span>
      <input type="text" id="wsRk_${k}" data-rk="${k}" class="${ov[k] !== undefined ? 'over' : ''}" value="${esc(v[k])}"
        ${k === 'got' ? 'title="Ваша фамилия — запоминается для следующих дел"' : k === 'done' ? 'title="По умолчанию — тот же, кто принял"' : ''}></div>`).join('')}</div>
    ${o.column4 === 'domain' && def.doms.length > 1 ? `<label class="chk" style="margin-top:6px"><input type="checkbox" id="wsPer" ${(st().row || {}).per_domain ? 'checked' : ''}> строка на каждый домен (иначе домены через запятую)</label>` : ''}
    <div id="wsRowPrev"></div>
    <div style="display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap"><button class="primary" id="wsCopyRow">Копировать строку</button>
      <span class="hint">Все поля можно поправить, ↺ — вернуть как было. D — ${o.column4 === 'contract' ? 'номер договора (аккаунта)' : 'домен'} для «${esc(o.title)}». B — ваша фамилия, запоминается${W.operator.login ? ` (логин manager: ${esc(W.operator.login)})` : ''}; C по умолчанию — тот же человек.</span></div></div>`;
  renderRowPreview();
  if (focusK){ const i = q(`[data-rk="${focusK}"]`, body); if (i){ i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }
}
function renderRowPreview(){
  const box = q('#wsRowPrev'); if (!box) return;
  const lines = rowLines();
  box.innerHTML = `<div style="overflow-x:auto"><table class="ws-xl"><tr class="hd">${['A', 'B', 'C', 'D', 'E', 'F', 'G'].map(x => `<td>${x}</td>`).join('')}</tr>
    ${lines.map(l => `<tr>${l.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</table></div>`;
  const b = q('#wsCopyRow'); if (b) b.textContent = lines.length > 1 ? `Копировать строки (${lines.length})` : 'Копировать строку';
}
function copyRow(){
  const tsv = rowLines().map(l => l.join('\t')).join('\n');
  copyText(tsv).then(ok => {
    toast(ok ? 'Строка скопирована — вставьте в таблицу (Ctrl+V)' : 'Не удалось скопировать — выделите строку в окне «Строка таблицы»');
    if (ok){ saveState({row: {copied: true, copied_at: new Date().toISOString(), last: tsv}}); renderSteps(); }
  });
}
async function loadOperator(){
  try {
    const r = await getJSON('/api/manager/operator');
    if (r.login){
      W.operator.login = r.login;
      if (r.surname) W.operator.surname = r.surname;
      store.set('pysm.operator', W.operator);
      renderRow();
    }
  } catch (e) {}
}

// ================================================================ смена администратора в manager
function gateReasons(){
  const R = [];
  const inc = resDomains().filter(d => d.status === 'found' && !excluded().has(d.domain));
  const stops = inc.filter(d => d.flags_level === 'stop');
  if (stops.length) R.push(`стоп по домену ${stops.map(d => d.domain).join(', ')} — снимите галку у домена или откройте с причиной`);
  // (0.8.2) пустой список доменов — не причина: окно открывается сразу, домены можно вписать на странице manager
  const d = W.res && W.res.result.decision;
  if (d && d.code === 'reject') R.push('вердикт программы: ' + d.title + ((d.reasons || []).length ? ' — ' + d.reasons.slice(0, 2).join('; ') : ''));
  return R;
}
function renderGate(){
  const g = q('#wsGate'); if (!g) return;
  const R = gateReasons();
  const inc = resDomains().filter(d => d.status === 'found' && !excluded().has(d.domain));
  const stops = inc.filter(d => d.flags_level === 'stop').length, warns = inc.filter(d => d.flags_level === 'warn').length;
  g.innerHTML = `<span class="badge ${stops ? 's-fail' : warns ? 's-warn' : inc.length ? 's-ok' : 's-info'}">Домены: ${inc.length} в смене${stops ? ` · стоп ${stops}` : ''}${warns ? ` · внимание ${warns}` : ''}</span>
    ${R.length || !hasMgr() ? '' : '<span class="badge s-ok">можно открывать смену</span>'}`;
  // (0.8.2) обе кнопки видны всегда; выделена кнопка страницы manager этой операции (config/workspace.yaml: manager_page)
  q('#wsOpenMgr').classList.toggle('primary', hasMgr() && !R.length);
  q('#wsOpenLoa').classList.toggle('primary', op().manager_page === 'loa_add');
}
// шаг «Смена в manager» в шапке дела — у операций со страницей domain_move, «Доверенность в manager» — loa_add
const hasMgr = () => op().manager_page === 'domain_move';
function renderSteps(){
  const box = q('#wsSteps'); if (!box || !W.ws) { if (box) box.innerHTML = ''; return; }
  const mg = st().mgr || {}, lo = st().loa || {}, rw = st().row || {};
  const S = [['Приём', W.ws.status === 'ready' ? 'done' : 'cur', 'intake'],
    ...(hasMgr() ? [['Смена в manager', mg.done ? 'done' : mg.step ? 'cur' : '', 'mgr']] : []),
    ...(op().manager_page === 'loa_add' ? [['Доверенность в manager', lo.done ? 'done' : lo.step ? 'cur' : '', 'loa']] : []),
    ['Строка таблицы', rw.copied ? 'done' : '', 'row']];
  box.innerHTML = S.map(([t, c, k], i) => `<span class="ws-step ${c}" data-step="${k}">${i + 1}. ${esc(t)}</span>`).join('');
  renderMini();
}
let gatePop = null;
function closeGatePop(){ if (gatePop){ gatePop.remove(); gatePop = null; } }
document.addEventListener('click', e => { if (gatePop && !gatePop.contains(e.target) && !e.target.closest('#wsOpenMgr')) closeGatePop(); });
function gateClick(e){
  const R = gateReasons();
  if (!R.length){ openMgr(''); return; }
  closeGatePop();
  gatePop = document.createElement('div'); gatePop.className = 'ws-pop';
  gatePop.innerHTML = `<h5>Перед сменой осталось</h5><ul>${R.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
    <label class="f">Открыть всё равно — причина (сохранится в деле) <input type="text" id="wsWhy" placeholder="например: стоп снят по согласованию с …"></label>
    <div style="display:flex;gap:6px;margin-top:8px"><button class="sm primary" id="wsForce">Открыть всё равно</button><button class="sm" id="wsGateClose">Закрыть</button></div>`;
  document.body.appendChild(gatePop);
  const r = e.target.getBoundingClientRect();
  gatePop.style.left = Math.max(8, Math.min(r.left, innerWidth - gatePop.offsetWidth - 8)) + 'px';
  gatePop.style.top = Math.max(8, r.top - gatePop.offsetHeight - 8) + 'px';
  q('#wsGateClose', gatePop).onclick = closeGatePop;
  q('#wsForce', gatePop).onclick = () => {
    const why = q('#wsWhy', gatePop).value.trim();
    if (!why){ q('#wsWhy', gatePop).focus(); toast('Укажите причину'); return; }
    closeGatePop(); openMgr(why);
  };
}
function openMgr(why){
  if (why) saveState({mgr: {why, why_at: new Date().toISOString(), why_reasons: gateReasons()}}, true);
  saveState({mgr: {opened: true}});
  const D = q('#wsDesk').getBoundingClientRect();
  WM.open('mgr', 'manager · смена администратора <small>· что впишет расширение</small>', {kind: 'panel', closable: true,
    fr: {x: .2, y: .03, w: Math.min(.6, 820 / Math.max(1, D.width)), h: .92}});
  renderMgr(); renderSteps();
}
function renderMgr(){
  const w = WM.get('mgr'); if (!w || !W.ws) return;
  const mg = st().mgr || {};
  const inc = incDomains();
  const adm = (W.res && W.res.new_admin) || {};
  const loginTo = mg.login_to !== undefined ? mg.login_to : (W.res && W.res.login_to) || '';
  const acc = mg.change_account !== undefined ? mg.change_account : !!loginTo;
  const files = W.ws.files.filter(f => ['pdf', 'image', 'docx', 'sig'].includes(f.kind));
  // подпись .sig проверяется вручную — по умолчанию не загружается (галку можно поставить)
  const sel = new Set(mg.files_sel || files.filter(f => f.kind !== 'sig').map(f => f.id));
  const rows = mg.rows || [];
  const fio = (adm.fio || '').split(/\s+/);
  q('.body', w.el).innerHTML = `<div class="ws-pad">
    ${mg.why ? `<div class="msg warn">Открыто с причиной: ${esc(mg.why)}</div>` : ''}
    <h4>Шаг 1 · «Перенос доменов между аккаунтами»</h4>
    <table><tr><th style="width:34%">Поле страницы</th><th>Значение</th></tr>
      <tr><td>Домены <span class="mono">domains</span></td><td>${inc.map(esc).join('<br>') || '<span class="hint">нет — поле на странице не трогается: впишите домены в manager или загрузите их в окне «Домены»</span>'}</td></tr>
      <tr><td>Операция</td><td><label class="chk"><input type="checkbox" checked disabled> смена администратора <span class="mono">do_change_admin</span></label>
        <label class="chk"><input type="checkbox" id="mAcc" ${acc ? 'checked' : ''}> перенос на другой аккаунт <span class="mono">do_change_account</span></label></td></tr>
      ${acc ? `<tr><td>Принимающий аккаунт <span class="mono">login_to</span></td><td><input type="text" id="mLogin" value="${esc(loginTo)}" style="width:100%"><span class="hint">из «Номер договора» заявления: ${esc(adm.contract || '—')}</span></td></tr>` : ''}
      <tr><td>Загрузить документы <span class="mono">attachment</span></td><td>${files.map(f => `<label class="chk"><input type="checkbox" data-mf="${f.id}" ${sel.has(f.id) ? 'checked' : ''}> ${esc(f.name.split('/').pop())} <span class="hint">${(f.size / 1024 / 1024).toFixed(2)} МБ</span></label>`).join('') || '<span class="hint">нет файлов</span>'}</td></tr>
      <tr><td>Остальные флажки</td><td class="c-info">не проверять контакты…, allow_rereg_only_bill, сохранить ЧУ, расторгнуть сделку — расширение не трогает</td></tr></table>
    <div style="display:flex;gap:8px;align-items:center;margin:8px 0;flex-wrap:wrap"><button class="primary" id="mFill">Вписать на странице manager</button>
      <span class="hint">Расширение откроет «Перенос доменов между аккаунтами» и заполнит её${inc.length ? '' : ' (без доменов — только флажки и документы)'}. <b>«Далее» нажимаете вы.</b></span></div>
    <div id="mFillMsg">${mg.step >= 1 ? `<div class="msg ok">Шаг 1 заполнен${(mg.files || []).length ? `, документов: ${mg.files.length}` : ''}.</div>` : ''}</div>
    <h4>Шаг 2 · «Данные нового администратора»</h4>
    <p class="hint" style="margin:0 0 6px">После «Далее» в manager: «Сверить» прочитает данные на странице и подсветит их там (зелёный — совпадает с заявлением, красный — нет).</p>
    <div style="display:flex;gap:8px;flex-wrap:wrap"><button id="mRead">Сверить «данные нового аккаунта»</button>
      <button id="mFillC" title="Переключить на «заполнить вручную» и вписать ФИО, телефон и e-mail из заявления">Заполнить вручную: ФИО и контакты</button></div>
    <div class="msg" style="margin-top:8px">Новый администратор в заявлении: <b>${esc(adm.fio || adm.org || '—')}</b>${adm.email ? ' · ' + esc(adm.email) : ''}${adm.phone ? ' · ' + esc(adm.phone) : ''}${adm.contract ? ' · договор ' + esc(adm.contract) : ''}</div>
    <div id="mReadOut">${rows.length ? mgrRowsHTML(rows) : ''}</div>
    <div style="display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap"><button class="primary" id="mDone">Смена выполнена → строка таблицы</button>
      <span class="hint">Нажмите после сообщения об успехе в manager.</span></div></div>`;
  const b = q('.body', w.el);
  q('#mAcc', b).onchange = e => { saveState({mgr: {change_account: e.target.checked}}); renderMgr(); };
  const ml = q('#mLogin', b); if (ml) ml.oninput = () => saveState({mgr: {login_to: ml.value}});
  qq('[data-mf]', b).forEach(c => c.onchange = () => saveState({mgr: {files_sel: qq('[data-mf]', b).filter(x => x.checked).map(x => x.dataset.mf)}}));
  q('#mFill', b).onclick = async () => {
    const btn = q('#mFill', b); btn.disabled = true;
    q('#mFillMsg', b).innerHTML = '<div class="msg">Открываю manager…</div>';
    try {
      const r = await api(`/api/ws/${W.id}/manager/fill`, {domains: inc, change_admin: true, change_account: q('#mAcc', b).checked,
        login_to: q('#mLogin', b) ? q('#mLogin', b).value.trim() : '', files: qq('[data-mf]', b).filter(x => x.checked).map(x => x.dataset.mf)});
      if (!r.ok) q('#mFillMsg', b).innerHTML = `<div class="msg fail">${esc((r.error || {}).message || 'ошибка')}</div>`;
      else {
        const d = r.data || {};
        q('#mFillMsg', b).innerHTML = `<div class="msg ok"><b>Страница заполнена</b>${d.tab_opened ? ' (вкладка открыта)' : ''}: ${esc((d.changed || []).join(', ') || 'всё уже было так')}.${inc.length ? '' : ' <b>Домены впишите на странице.</b>'}
          ${(d.attached || []).length ? `Документы: ${esc(d.attached.join(', '))}.` : ''}${(d.notes || []).length ? ' <br>Замечания: ' + esc(d.notes.join('; ')) : ''}<br><b>Проверьте страницу и нажмите «Далее» в manager.</b></div>`;
        saveState({mgr: {step: 1}});
        renderSteps();
      }
    } catch (e) { q('#mFillMsg', b).innerHTML = `<div class="msg fail">${esc(e.message)}</div>`; }
    finally { btn.disabled = false; }
  };
  q('#mRead', b).onclick = async () => {
    q('#mReadOut', b).innerHTML = '<div class="msg">Читаю страницу manager…</div>';
    try {
      const r = await api(`/api/ws/${W.id}/manager/read`, {});
      if (!r.ok){ q('#mReadOut', b).innerHTML = `<div class="msg fail">${esc((r.error || {}).message || 'ошибка')}</div>`; return; }
      saveState({mgr: {step: 2, rows: r.rows}});
      q('#mReadOut', b).innerHTML = mgrRowsHTML(r.rows) + (r.page.mode === 'fill' ? '<p class="hint">На странице выбрано «заполнить вручную».</p>' : '');
      renderSteps();
    } catch (e) { q('#mReadOut', b).innerHTML = `<div class="msg fail">${esc(e.message)}</div>`; }
  };
  q('#mFillC', b).onclick = async () => {
    const fields = {person_r_surname: fio[0] || '', person_r_name: fio[1] || '', person_r_patronimic: fio.slice(2).join(' '), phone: adm.phone || '', e_mail: adm.email || ''};
    if (!adm.fio){ toast('В заявлении новый администратор — не физлицо или не прочитан: впишите данные в manager вручную'); return; }
    try {
      const r = await api(`/api/ws/${W.id}/manager/fill_contacts`, {fields});
      toast(r.ok ? `Вписано полей: ${(r.data.changed || []).length}. Паспорт, дату рождения и адрес нового администратора впишите сами` : esc((r.error || {}).message || 'ошибка'), 7000);
    } catch (e) { toast(esc(e.message)); }
  };
  q('#mDone', b).onclick = () => { saveState({mgr: {done: true, done_at: new Date().toISOString()}}, true); renderSteps(); WM.close('mgr'); WM.restore('row'); WM.focus('row'); toast('Смена выполнена. Скопируйте строку таблицы'); };
}
function mgrRowsHTML(rows){
  const C = {ok: 'c-ok', fail: 'c-fail', warn: 'c-warn', info: 'c-info'};
  const L = {ok: '<span class="badge s-ok">совпадает</span>', fail: '<span class="badge s-fail">расходится</span>', warn: '<span class="badge s-warn">похоже</span>', info: '<span class="badge s-info">к сведению</span>'};
  return `<table style="margin-top:8px"><tr><th>Поле</th><th>Заявление</th><th>manager: данные нового аккаунта</th><th></th></tr>
    ${rows.map(r => `<tr><td>${esc(r.title)}</td><td class="${C[r.status] || ''}">${esc(r.app) || '<span class="hint">в заявлении нет</span>'}</td><td class="${C[r.status] || ''}">${esc(r.mgr) || '<span class="hint">—</span>'}</td><td>${L[r.status] || ''}</td></tr>`).join('')}</table>`;
}

// ================================================================ (0.8.2) безбумажные операции: доверенность в manager
// Кнопка «Безбумажные операции» (есть всегда): окно с полями страницы manager «Добавить новый документ от
// пользователя» (Документы → Доверенности → Добавить, /userdocs/loa_add) под теми же названиями. Значения — из Sd
// найденного домена (физлицо: серия и номер паспорта, ФИО; юрлицо: код страны и ИНН; e-mail и телефон
// администратора), иначе паспорт и ФИО из документов дела; всё можно поправить. Расширение вписывает их на странице
// (/api/ws/{id}/manager/loa_fill); вид документа, срок действия, «Показать домены…» и «Добавить документ» — оператор.
const LOA_KEYS = ['passport_inn', 'fullname', 'email', 'customer_phone'];
const LOA_LABEL = {
  person: {passport_inn: 'Номер паспорта (физ.лицо)', fullname: 'ФИО Администратора'},
  org: {passport_inn: 'Код страны регистрации компании (RU, ...)', fullname: 'ИНН компании'},
  '': {passport_inn: 'Номер паспорта (физ.лицо) / Код страны регистрации компании (RU, ...)', fullname: 'ФИО Администратора / ИНН компании'},
};
const LOA_LABEL_ALL = {email: 'Высылать письма об окончании срока доверенности на указанный email', customer_phone: 'Телефон (опционально)'};
const firstPhone = s => String(s || '').split(/[\n,;]+/).map(x => x.trim()).filter(Boolean)[0] || '';
function loaSources(){
  // администраторы найденных доменов (домены с одинаковым паспортом / ИНН — одна строка) в порядке строки таблицы
  // (включённые галкой, по заявлению), затем остальные; последним — документы дела (паспорт, иначе заявление)
  const inc = incDomains(), out = [];
  const pos = d => { const i = inc.indexOf(d.domain); return i < 0 ? 1e6 : i; };
  const found = resDomains().filter(d => d.status === 'found' && (d.kind === 'person' || d.kind === 'org'))
    .sort((a, b) => pos(a) - pos(b));
  for (const d of found){
    const sd = d.sd || {};
    const v = d.kind === 'person' ? {passport_inn: d.passport || '', fullname: d.fio || ''}
      : {passport_inn: String(sd.country || 'RU').trim().toUpperCase(), fullname: String(sd.code || '').replace(/\D/g, '')};
    const key = (v.passport_inn + ':' + v.fullname).replace(/\s/g, '').toLowerCase();
    const same = out.find(x => x.key === key);
    if (same){ same.domains.push(d.domain); continue; }
    out.push({id: d.domain, key, kind: d.kind, domains: [d.domain], ...v, email: (d.emails || [])[0] || '',
      emails: [...new Set([...(d.emails || []), d.account_email].filter(Boolean))], customer_phone: firstPhone(sd.phone || sd.o_phone)});
  }
  const L = (W.res && W.res.loa_doc) || {};
  if (L.passport || L.fio) out.push({id: '@docs', key: '@docs', kind: 'person', domains: [], passport_inn: L.passport || '', fullname: L.fio || '', email: '', emails: [], customer_phone: ''});
  return out;
}
function openLoa(){
  saveState({loa: {opened: true}});
  const D = q('#wsDesk').getBoundingClientRect();
  WM.open('loa', 'manager · безбумажные операции <small>· доверенность: что впишет расширение</small>', {kind: 'panel', closable: true,
    fr: {x: .22, y: .04, w: Math.min(.58, 780 / Math.max(1, D.width)), h: .9}});
  renderLoa(); renderSteps();
}
function renderLoa(){
  const w = WM.get('loa'); if (!w || !W.ws) return;
  const b = q('.body', w.el);
  if (b.contains(document.activeElement) && document.activeElement.matches('input[data-lf]')) return;   // не мешать вводу
  const S = loaSources(), ls = st().loa || {};
  const src = S.find(x => x.id === ls.src) || S[0] || null;
  const sid = src ? src.id : '';
  // правки оператора относятся к своему источнику: другой источник (или загруженные домены) — значения из него
  const ed = ls.edit_src === sid ? (ls.edit || {}) : {};
  const kind = src ? src.kind : '';
  const lab = {...LOA_LABEL[kind], ...LOA_LABEL_ALL};
  const val = k => k in ed ? ed[k] : src ? (src[k] || '') : '';
  const srcText = x => x.id === '@docs' ? 'документы дела (паспорт / заявление)'
    : `Sd: ${x.domains.join(', ')} · ${x.kind === 'org' ? 'юрлицо' : 'физлицо'}`;
  const nAdm = S.filter(x => x.id !== '@docs').length;
  const docs = (W.res && W.res.loa_doc) || {};
  const pDiff = src && src.id !== '@docs' && kind === 'person' && docs.passport && src.passport_inn
    && docs.passport.replace(/\s/g, '') !== src.passport_inn.replace(/\s/g, '');
  b.innerHTML = `<div class="ws-pad">
    <p class="hint" style="margin:0 0 8px">Страница manager «Добавить новый документ от пользователя» (Документы → Доверенности → Добавить). Поля — как на странице.</p>
    <label class="f">Данные администратора из <select id="lSrc" ${S.length ? '' : 'disabled'}>${S.length ? S.map(x => `<option value="${esc(x.id)}" ${x.id === sid ? 'selected' : ''}>${esc(srcText(x))}</option>`).join('') : '<option value="">нет данных — впишите вручную</option>'}</select></label>
    ${S.length ? '' : '<p class="hint" style="margin:6px 0 0">Загрузите домены в окне «Домены» (данные возьмутся из Sd) или добавьте паспорт в дело — или впишите поля ниже.</p>'}
    ${nAdm > 1 ? `<p class="hint" style="margin:6px 0 0">У доменов разные администраторы (${nAdm}): доверенность на каждого добавляется отдельно — выберите, чьи данные вписать.</p>` : ''}
    ${pDiff ? `<div class="msg warn" style="margin-top:6px">В документах дела другой паспорт: ${esc(docs.passport)}. По паспорту и ФИО manager ищет домены администратора — сверьте.</div>` : ''}
    <table style="margin-top:8px"><tr><th style="width:42%">Поле страницы</th><th>Значение</th></tr>
      ${LOA_KEYS.map(k => `<tr><td>${esc(lab[k])} <span class="mono">${k}</span></td><td><input type="text" data-lf="${k}" value="${esc(val(k))}" style="width:100%"${k === 'email' ? ' list="lMails"' : ''}>${k in ed ? '<small class="hint">изменено вручную</small>' : ''}</td></tr>`).join('')}
      <tr><td>Вид документа, «Документ действителен С / ПО», список доменов, внутренний номер документа</td><td class="c-info">расширение не трогает — выбираете и вписываете вы</td></tr></table>
    <datalist id="lMails">${(src ? src.emails : []).map(e => `<option value="${esc(e)}">`).join('')}</datalist>
    <div style="display:flex;gap:8px;align-items:center;margin:8px 0;flex-wrap:wrap"><button class="primary" id="lFill">Вписать на странице manager</button>
      <span class="hint">Расширение откроет страницу доверенности и впишет поля. Проверить домены — «Показать домены, на которые будет выдана доверенность»; <b>«Добавить документ» нажимаете вы.</b></span></div>
    <div id="lMsg">${ls.step >= 1 ? '<div class="msg ok">Поля вписаны на странице manager.</div>' : ''}</div>
    <div style="display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap"><button id="lDone">Документ добавлен → строка таблицы</button>
      <span class="hint">Нажмите после «Добавить документ» в manager.</span></div></div>`;
  q('#lSrc', b).onchange = e => { saveState({loa: {src: e.target.value, edit: {}, edit_src: e.target.value}}); renderLoa(); };
  qq('input[data-lf]', b).forEach(i => {
    i.oninput = () => {
      const cur = st().loa || {};
      const e = cur.edit_src === sid ? {...(cur.edit || {})} : {};
      e[i.dataset.lf] = i.value;
      saveState({loa: {edit: e, edit_src: sid}});
    };
  });
  q('#lFill', b).onclick = async () => {
    const btn = q('#lFill', b); btn.disabled = true;
    const fields = {};
    qq('input[data-lf]', b).forEach(i => { fields[i.dataset.lf] = i.value.trim(); });
    q('#lMsg', b).innerHTML = '<div class="msg">Открываю manager…</div>';
    try {
      const r = await api(`/api/ws/${W.id}/manager/loa_fill`, {fields});
      if (!r.ok){
        const er = r.error || {};
        q('#lMsg', b).innerHTML = `<div class="msg fail">${er.code === 'unknown_cmd' ? 'Расширение старой версии — обновите его: chrome://extensions → PySimpleManager → «Обновить» (⟳), затем повторите.' : esc(er.message || 'ошибка')}</div>`;
      } else {
        const d = r.data || {};
        saveState({loa: {step: 1}}); renderSteps();
        q('#lMsg', b).innerHTML = `<div class="msg ok"><b>Страница заполнена</b>${d.tab_opened ? ' (вкладка открыта)' : ''}: ${esc((d.changed || []).map(x => x.label).join(', ') || 'всё уже было так')}.
          ${(d.missing || []).length ? '<br>Нет на странице: ' + esc(d.missing.join(', ')) + '.' : ''}<br><b>Выберите «Вид документа» и срок действия, проверьте домены и нажмите «Добавить документ» в manager.</b></div>`;
      }
    } catch (e) { q('#lMsg', b).innerHTML = `<div class="msg fail">${esc(e.message)}</div>`; }
    finally { btn.disabled = false; }
  };
  q('#lDone', b).onclick = () => { saveState({loa: {done: true, done_at: new Date().toISOString()}}, true); renderSteps(); WM.close('loa'); WM.restore('row'); WM.focus('row'); toast('Доверенность добавлена. Скопируйте строку таблицы'); };
}

// ================================================================ (0.8.0) строка «Поиск» и сайт рядом
// Кнопка «Поиск» или Ctrl+K: строка запроса над окнами. Сайт (Google, ЕГРЮЛ, Госуслуги, быстрые ссылки из
// config/workspace.yaml) открывает расширение — во второй половине окна Chrome (разделённый экран), окном справа или
// вкладкой (/api/site/open). Без расширения — обычным окном браузера. Файлы дела перетаскиваются на сайт: со страницы
// уходит ссылка на файл (application/x-pysm-file), файл на страницу кладёт расширение (content/drop.js).
const FILE_MARK = 'application/x-pysm-file';
const SR = {on: false, drop: false, last: null};
const OPEN_IN_RU = {auto: 'рядом (авто)', split: 'вторая половина Chrome', window: 'окном справа', tab: 'вкладкой'};
const MODE_RU = {split: 'во второй половине окна Chrome', window: 'в окне справа', tab: 'в соседней вкладке', popup: 'в окне браузера'};
const SEARCH_ICON = '<svg class="ws-si" viewBox="0 0 16 16"><circle cx="7" cy="7" r="4.5"/><path d="M10.4 10.4L14 14"/></svg>';
const searchCfg = () => (W.meta && W.meta.search) || {services: [], links: [], open_in: 'auto', window_share: 40};
const openIn = () => { const v = store.get('pysm.ws.openin', ''); return OPEN_IN_RU[v] ? v : (searchCfg().open_in || 'auto'); };
const screenArea = () => ({left: screen.availLeft || 0, top: screen.availTop || 0, width: screen.availWidth || screen.width, height: screen.availHeight || screen.height});
const siteOpts = extra => ({open_in: openIn(), screen: screenArea(), ...(extra || {})});
const nowHM = () => new Date().toLocaleTimeString('ru-RU', {hour: '2-digit', minute: '2-digit'});
const MIME = {pdf: 'application/pdf', jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', tif: 'image/tiff', tiff: 'image/tiff',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', sig: 'application/pkcs7-signature', p7s: 'application/pkcs7-signature'};
const baseName = n => String(n || '').split('/').pop();
const dragFiles = () => ((W.ws && W.ws.files) || []).filter(f => f.kind !== 'archive');

function toggleSearch(){ if (SR.on) hideSearch(); else showSearch(); }
function showSearch(){
  if (!W.id) return;
  SR.on = true;
  q('#wsSbar').classList.remove('hidden'); q('#wsSearchBtn').classList.add('on'); q('#wsMiniSearch').classList.add('on');
  renderSbar();
  const last = (st().search || {}).last;
  // сайт этого дела был открыт и его убрали — вернуть на место без перезагрузки страницы
  if (last && last.url && SR.hidden) api('/api/site/open', {url: last.url, ...siteOpts({keep: true})}).catch(() => {});
  SR.hidden = false;
  setTimeout(() => { const i = q('#wsQ'); if (i){ i.focus(); i.select(); } }, 0);
}
function hideSearch(){
  SR.on = false;
  q('#wsSbar').classList.add('hidden'); q('#wsSearchBtn').classList.remove('on'); q('#wsMiniSearch').classList.remove('on');
  SR.drop = false;
  if ((st().search || {}).last){ SR.hidden = true; api('/api/site/hide', {}).catch(() => {}); }
}
function svcTip(s){
  return s.fill === 'egrul' ? `Откроет ${s.url.replace(/^https?:\/\//, '').split('/')[0]} и впишет запрос в поле поиска; «Найти» нажимаете вы`
    : `Поиск: ${s.title}` + ((searchCfg().services[0] || {}).id === s.id ? ' (Enter)' : '');
}
function renderSbar(){
  const box = q('#wsSbar'); if (!box || !W.ws) return;
  const cfg = searchCfg();
  box.innerHTML = `<div class="ws-srow"><div class="ws-qbox">${SEARCH_ICON}<input type="text" id="wsQ" autocomplete="off" placeholder="Запрос: ФИО, серия и номер, ИНН, ОГРН, домен…" value="${esc(SR.q || '')}">
      <button type="button" class="wb" data-sa="clear" title="Очистить">${ICON.close}</button></div>
    <div class="ws-svc">${cfg.services.map((s, i) => `<button type="button" data-svc="${i}" title="${esc(svcTip(s))}">${esc(s.title)}</button>`).join('')}
      <span class="sep"></span><button type="button" data-sa="gos" title="Откроет «Проверка действительности паспорта» и впишет серию, номер, фамилию и имя: из строки запроса («4012 654321 Иванова Анна»), иначе из паспорта дела, иначе из Sd домена; «Продолжить» нажимаете вы">Госуслуги · паспорт</button>
      ${cfg.links.length ? '<span class="sep"></span>' + cfg.links.map((l, i) => `<button type="button" class="lnk" data-link="${i}" title="${esc(l.url)}">${esc(l.title)}</button>`).join('') : ''}</div>
    <span class="ws-sright"><select id="wsOpenIn" title="Где открывать сайт (запоминается в этом браузере)">${Object.entries(OPEN_IN_RU).map(([k, v]) => `<option value="${k}" ${k === openIn() ? 'selected' : ''}>${v}</option>`).join('')}</select>
      <button type="button" class="sm" data-sa="help" title="Как это устроено">?</button>
      <button type="button" class="wb" data-sa="hide" title="Убрать строку поиска и сайт рядом (Ctrl+K)">${ICON.close}</button></span></div>
    <div class="ws-srow ws-srow2"><div class="ws-sfiles" id="wsSfiles" title="Перетащите файл на страницу сайта (проверка ЭП, Контур.Крипто, Nextcloud) — его положит туда расширение"></div>
      <div class="ws-sst" id="wsSst"></div></div>
    <div class="ws-sdrop hidden" id="wsSdrop"></div>`;
  renderSfiles(); renderSst();
}
function renderSfiles(){
  const box = q('#wsSfiles'); if (!box) return;
  const files = dragFiles();
  box.innerHTML = files.length ? '<span class="hint">Файлы:</span>' + files.map(f =>
    `<span class="ws-fchip" draggable="true" data-dragfile="${esc(f.id)}" title="${esc(baseName(f.name))} — перетащите на страницу сайта">${esc(baseName(f.name))}</span>`).join('') : '';
  box.classList.toggle('hidden', !files.length);
}
function renderSst(){
  const box = q('#wsSst'); if (!box) return;
  const s = SR.last;
  let h = '';
  if (!s) h = `<span class="hint">Enter — ${esc((searchCfg().services[0] || {}).title || 'поиск')}; значения дела и прошлые запросы — под строкой</span>`;
  else if (s.error) h = `<span class="badge s-fail">${esc(s.title)}</span> ${esc(s.error)}`;
  else if (s.pending) h = `<span class="badge s-info">${esc(s.title)}</span> <span class="hint">открываю…</span>`;
  else if (s.kind === 'gos') h = `<span class="badge s-review">Госуслуги</span> вписано — <b>«Продолжить» нажимаете вы</b>${s.where ? ' ' + esc(s.where) : ''}`;
  else if (s.fill){
    h = s.fillOk ? `<span class="badge s-review">${esc(s.title)}</span> вписано «${esc(s.q)}» — <b>«Найти» нажимаете вы</b>${s.where ? ' ' + esc(s.where) : ''}`
      : `<span class="badge s-warn">${esc(s.title)}</span> ${esc(s.fillErr || 'запрос не вписан')} <button type="button" class="sm" data-sa="copyq">Копировать запрос</button>`;
  } else h = `<span class="hint">Открыто ${esc(s.where || '')}: ${esc(s.title)}${s.q ? ' — «' + esc(s.q) + '»' : ''}</span>`;
  if (s && s.notes && s.notes.length) h += ` <span class="hint" title="${esc(s.notes.join('\n'))}">· ${esc(s.notes[0].slice(0, 90))}${s.notes[0].length > 90 ? '…' : ''}</span>`;
  box.innerHTML = h;
}
// значения дела для запроса: Sd, паспорт, заявление (без повторов)
function caseChips(){
  const out = [], seen = new Set();
  const add = (v, label) => { v = String(v || '').trim(); if (!v || seen.has(v.toLowerCase())) return; seen.add(v.toLowerCase()); out.push([v, label]); };
  const rows = (W.res && W.res.matrix && W.res.matrix.rows) || [];
  const cell = (id, k) => (((rows.find(r => r.id === id) || {}).cells || {})[k] || {}).value || '';
  ['pas', 'sd', 'app'].forEach(k => add(cell('fio', k), 'ФИО · ' + {pas: 'паспорт', sd: 'Sd', app: 'заявление'}[k]));
  const g = (W.res && W.res.gos) || {};
  if (g.series && g.number) add(`${g.series} ${g.number}`, 'паспорт');
  ['pas', 'sd'].forEach(k => add(cell('passport', k), 'паспорт · ' + (k === 'pas' ? 'документ' : 'Sd')));
  add(cell('birth_date', 'sd') || cell('birth_date', 'pas'), 'д. р.');
  add(cell('inn', 'sd') || cell('inn', 'app'), 'ИНН');
  resDomains().filter(d => d.status === 'found').slice(0, 6).forEach(d => {
    add(d.domain, 'домен');
    if (d.kind === 'org'){ add(d.holder, 'организация · Sd'); add((d.sd || {}).code, 'ИНН · Sd'); }
    else { add(d.fio, 'ФИО · Sd'); add(d.passport, 'паспорт · Sd'); }
  });
  if (!resDomains().length) ((W.res && W.res.result.domains_from_app) || []).slice(0, 6).forEach(d => add(d, 'домен'));
  const a = (W.res && W.res.new_admin) || {};
  add(a.fio || a.org, 'новый администратор');
  add(a.contract, 'договор');
  return out;
}
function renderDrop(){
  const d = q('#wsSdrop'); if (!d) return;
  const chips = caseChips(), hist = (st().search || {}).hist || [];
  if (!chips.length && !hist.length){ d.classList.add('hidden'); return; }
  d.innerHTML = (chips.length ? `<div class="hint">Из дела — щелчок: вставить, Shift+щелчок: добавить к запросу</div>
    <div class="ws-chips">${chips.map(([v, l]) => `<button type="button" class="ws-chip" data-chip="${esc(v)}">${esc(v)} <small>${esc(l)}</small></button>`).join('')}</div>` : '')
    + (hist.length ? `<div class="hint" style="margin-top:6px">Запросы по делу — щелчок: открыть снова</div>`
      + hist.slice(0, 8).map((h, i) => `<button type="button" class="ws-hrow" data-hist="${i}">${esc(h.t)} · ${esc(h.title)}${h.q ? ' · ' + esc(h.q) : ''}</button>`).join('') : '');
  d.classList.remove('hidden');
}
function hideDrop(){ const d = q('#wsSdrop'); if (d) d.classList.add('hidden'); }
function remember(entry){
  const hist = [{t: nowHM(), ...entry}, ...(((st().search || {}).hist) || [])].slice(0, 20);
  saveState({search: {hist, last: {url: entry.url, title: entry.title, kind: entry.kind}}});
}
async function openSite(entry, extra){
  // entry: {kind: svc|link, title, url, q, fill}
  SR.last = {...entry, where: '', notes: [], pending: true};
  remember({kind: entry.kind, title: entry.title, url: entry.url, q: entry.q || ''});
  renderSst(); hideDrop();
  try {
    const r = await api('/api/site/open', {url: entry.url, fill: entry.fill || null, q: entry.q || '', ...siteOpts(extra)});
    if (!r.ok) throw new Error((r.error || {}).message || 'расширение не открыло сайт');
    const d = r.data || {};
    SR.last.where = MODE_RU[d.mode] || '';
    SR.last.notes = d.notes || [];
    if (entry.fill){ SR.last.fillOk = !!(d.fill && d.fill.ok); SR.last.fillErr = d.fill && d.fill.error ? d.fill.error.message : ''; }
  } catch (e) {
    const oldExt = /Неизвестная команда/.test(e.message);
    if (/не подключено|409/.test(e.message) || oldExt){
      // без расширения (или со старым, до 0.8.0) — обычное окно браузера справа; запрос для формы — в буфер обмена
      const a = screenArea(), w = Math.round(a.width * (searchCfg().window_share || 40) / 100);
      const pop = window.open(entry.url, 'pysm_site', `popup,left=${a.left + a.width - w},top=${a.top},width=${w},height=${a.height}`);
      SR.last.where = MODE_RU.popup;
      SR.last.notes = [oldExt ? 'расширение старой версии — обновите его (chrome://extensions → «Обновить»); пока сайт открыт обычным окном'
        : 'расширение не подключено: сайт открыт обычным окном, файлы на него не перетаскиваются'];
      if (entry.fill){ SR.last.fillOk = false; SR.last.fillErr = 'расширение не подключено — запрос скопирован, вставьте его (Ctrl+V)'; copyText(entry.q); }
      if (!pop) SR.last.error = 'браузер не дал открыть окно — разрешите всплывающие окна для этой страницы';
    } else SR.last.error = e.message;
  }
  SR.last.pending = false;
  renderSst();
}
function runSvc(i, shift){
  const svcs = searchCfg().services;
  const s = svcs[shift ? (svcs.length > 1 ? 1 : 0) : i];
  const inp = q('#wsQ');
  const query = ((inp && inp.value) || '').trim();
  SR.q = query;
  if (!s) return;
  if (!query){ toast('Впишите запрос или выберите значение из дела под строкой'); if (inp){ inp.focus(); renderDrop(); } return; }
  openSite({kind: 'svc', title: s.title, url: s.url.includes('{q}') ? s.url.split('{q}').join(encodeURIComponent(query)) : s.url, q: query, fill: s.fill || null});
}
function openLink(i){
  const l = searchCfg().links[i]; if (!l) return;
  openSite({kind: 'link', title: l.title, url: l.url});
}
// серия и номер (и фамилия, имя) из строки запроса: «4012 654321 Иванова Анна», «4012654321»
function gosFromQuery(s){
  s = String(s || '');
  const m = /(?:^|\D)(\d{2}\s?\d{2})\s?№?\s?(\d{6})(?!\d)/.exec(s);
  if (!m) return null;
  const words = s.replace(m[0], ' ').match(/[А-ЯЁа-яё][А-ЯЁа-яё-]+/g) || [];
  const cap = w => w ? w[0].toUpperCase() + w.slice(1).toLowerCase() : '';
  return {series: m[1].replace(/\s/g, ''), number: m[2], surname: cap(words[0]), name: cap(words[1])};
}
// без прочитанного паспорта (дело без файлов, скан не прочитан) — паспорт администратора из Sd найденного домена
function gosFromSd(){
  const d = resDomains().find(x => x.status === 'found' && x.kind === 'person' && /^\d{4}\s?\d{6}$/.test(String(x.passport || '').trim()));
  if (!d) return null;
  const pp = d.passport.replace(/\s/g, ''), fio = String(d.fio || '').split(/\s+/);
  return {series: pp.slice(0, 4), number: pp.slice(4), surname: fio[0] || '', name: fio[1] || ''};
}
async function gosFill(btn){
  // «Госуслуги · паспорт» (строка поиска): расширение вписывает серию, номер, фамилию и имя; «Продолжить» — оператор.
  // Откуда: серия и номер в строке запроса → паспорт (заявление) дела → Sd найденного домена.
  const fromQ = gosFromQuery(q('#wsQ') ? q('#wsQ').value : SR.q);
  const g = (W.res && W.res.gos) || {};
  const extra = fromQ || (g.series && g.number ? {} : gosFromSd());
  if (!extra){
    toast('Серия и номер паспорта не прочитаны — впишите их в строку «Поиск» (например, «4012 654321 Иванова Анна») и нажмите «Госуслуги · паспорт» ещё раз', 9000);
    if (!SR.on) showSearch(); else { const i = q('#wsQ'); if (i) i.focus(); }
    return;
  }
  if (btn) btn.disabled = true;
  SR.last = {kind: 'gos', title: 'Госуслуги', where: '', notes: [], pending: true};
  renderSst();
  try {
    const body = {site: siteOpts()};
    Object.entries(extra).forEach(([k, v]) => { if (v) body[k] = v; });
    const r = await api(`/api/ws/${W.id}/gosuslugi`, body);
    if (!r.ok){ SR.last.error = (r.error || {}).message || 'ошибка'; toast('Госуслуги: ' + esc(SR.last.error), 7000); }
    else {
      const pr = (r.data || {}).problems || [];
      toast(pr.length ? 'Вписано, но проверьте поля: ' + esc(pr.join('; ')) : 'Вписано на Госуслугах: ' + esc(`${r.sent.series} ${r.sent.number} · ${r.sent.surname} · ${r.sent.name}`) + '. «Продолжить» нажимаете вы', 7000);
      remember({kind: 'gos', title: 'Госуслуги · паспорт', url: 'https://www.gosuslugi.ru/621102/1/form', q: `${r.sent.series} ${r.sent.number}`});
    }
  } catch (x) { SR.last.error = x.message; toast(esc(x.message), 7000); }
  finally { if (btn) btn.disabled = false; SR.last.pending = false; renderSst(); }
}
function searchHelp(anchor){
  closeWinPop();
  fetch('/api/manager/status').then(r => r.json()).catch(() => ({})).then(s => {
    const ch = s.chrome ? `Chrome ${s.chrome}` : 'версия Chrome неизвестна';
    const pop = winPopAt(anchor, `<h5>Где открывается сайт</h5>
      <p style="margin:0 0 6px"><b>Рядом (авто)</b> — во второй половине окна Chrome рядом с программой (разделённый экран). Расширение делает это само
        в Chrome 155+; в Chrome 145–154 соберите пару один раз вручную (правый щелчок по вкладке программы → разделённый экран) —
        дальше сайты открываются во второй вкладке пары. Иначе — окном справа.</p>
      <p style="margin:0 0 6px"><b>Окном справа</b> — отдельное окно Chrome, окно программы сдвигается влево; «убрать» возвращает его на место.</p>
      <p style="margin:0 0 6px">Следующий запрос открывается там же. На страницы проверки ЭП, Контур.Крипто и Nextcloud можно перетащить файлы дела
        («Файлы:» в строке, лента страниц, список файлов) — их положит туда расширение. Кнопки на сайтах («Найти», «Продолжить»,
        «Проверить») нажимаете вы.</p>
      <p class="hint" style="margin:0">Сейчас: ${s.connected ? `расширение подключено, ${esc(ch)}, разделённый экран для расширения — ${s.split_api ? 'есть' : 'нет (нужен Chrome 155+)'}` : 'расширение не подключено — сайты открываются обычным окном браузера'}.
        Сервисы и ссылки — config/workspace.yaml, раздел search.</p>`);
    pop.style.maxWidth = '460px';
  });
}
function sbarClick(e){
  const chip = e.target.closest('[data-chip]');
  if (chip){
    const i = q('#wsQ'), v = chip.dataset.chip;
    i.value = e.shiftKey && i.value.trim() ? i.value.trim() + ' ' + v : v;
    SR.q = i.value; i.focus(); return;
  }
  const sv = e.target.closest('[data-svc]'); if (sv){ runSvc(+sv.dataset.svc, false); return; }
  const lk = e.target.closest('[data-link]'); if (lk){ openLink(+lk.dataset.link); return; }
  const h = e.target.closest('[data-hist]');
  if (h){
    const x = ((st().search || {}).hist || [])[+h.dataset.hist]; if (!x) return;
    if (x.kind === 'gos'){ if (x.q){ q('#wsQ').value = x.q; SR.q = x.q; } gosFill(null); return; }
    if (x.q){ q('#wsQ').value = x.q; SR.q = x.q; }
    const svc = searchCfg().services.find(s => s.title === x.title);
    openSite({kind: x.kind, title: x.title, url: x.url, q: x.q, fill: svc ? svc.fill : null});
    return;
  }
  const a = e.target.closest('[data-sa]'); if (!a) return;
  const k = a.dataset.sa;
  if (k === 'clear'){ q('#wsQ').value = ''; SR.q = ''; q('#wsQ').focus(); renderDrop(); }
  else if (k === 'hide') hideSearch();
  else if (k === 'help'){ e.stopPropagation(); searchHelp(a); }
  else if (k === 'gos') gosFill(a);
  else if (k === 'copyq') copyText((SR.last && SR.last.q) || '').then(ok => toast(ok ? 'Запрос скопирован — вставьте его на сайте (Ctrl+V)' : 'Не удалось скопировать'));
}
// перетаскивание файлов дела на сайт: ссылка на файл + имя; для папки Windows — DownloadURL (Chrome)
function setFileDrag(dt, f){
  if (!f || !W.id) return;
  const name = baseName(f.name), ext = (name.split('.').pop() || '').toLowerCase();
  const info = {url: `${location.origin}/api/ws/${W.id}/file/${f.id}`, name, mime: MIME[ext] || 'application/octet-stream', size: f.size || 0};
  try {
    dt.setData(FILE_MARK, JSON.stringify(info));
    dt.setData('text/uri-list', info.url);
    dt.setData('DownloadURL', `${info.mime}:${name}:${info.url}`);
  } catch (x) {}
  return info;
}

// ================================================================ (0.8.0) меню «Окна»
let winPop = null;
function closeWinPop(){ if (winPop){ winPop.remove(); winPop = null; } }
document.addEventListener('click', e => { if (winPop && !winPop.contains(e.target) && !e.target.closest('#wsWinBtn,#wsMiniWin,[data-sa=help]')) closeWinPop(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeWinPop(); });
function winPopAt(anchor, html){
  closeWinPop();
  winPop = document.createElement('div'); winPop.className = 'ws-pop ws-winpop';
  winPop.innerHTML = html;
  document.body.appendChild(winPop);
  const r = anchor.getBoundingClientRect();
  winPop.style.left = Math.max(8, Math.min(r.left, innerWidth - winPop.offsetWidth - 8)) + 'px';
  winPop.style.top = Math.min(r.bottom + 6, innerHeight - winPop.offsetHeight - 8) + 'px';
  return winPop;
}
function winList(){
  const L = [];
  const stOf = w => !w ? 'закрыто' : w.pop && !w.pop.closed ? 'откреплено' : w.min ? 'свёрнуто' : 'открыто';
  [['app', 'Заявление'], ['pas', 'Паспорт'], ['dom', 'Домены'], ['row', 'Строка таблицы']].forEach(([id, t]) => L.push({id, t, s: stOf(WM.get(id))}));
  L.push({id: 'mgr', t: 'manager · смена администратора', s: stOf(WM.get('mgr'))});
  L.push({id: 'loa', t: 'manager · безбумажные операции', s: stOf(WM.get('loa'))});
  WM.all().filter(w => w.kind === 'page' || w.id === 'oth').forEach(w => L.push({id: w.id, t: w.title.replace(/<[^>]+>/g, ''), s: stOf(w)}));
  resDomains().filter(d => d.status === 'found').forEach(d => {
    const id = 'sd_' + d.domain.replace(/[^\w.-]/g, '_');
    L.push({id, t: 'Sd · ' + d.domain, s: stOf(WM.get(id)), sd: d.domain});
  });
  return L;
}
function openWinMenu(anchor){
  if (winPop){ closeWinPop(); return; }
  const L = winList(), nsd = L.filter(x => x.sd).length;
  const p = winPopAt(anchor, `<h5>Окна рабочего места</h5>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px"><button class="sm primary" data-wa="all" title="Вернуть все свёрнутые окна на их места (расположение окон не меняется)">Показать все</button>
      ${nsd ? `<button class="sm" data-wa="sdall" title="Открыть Sd каждого найденного домена">Sd всех доменов (${nsd})</button>` : ''}
      <button class="sm" data-wa="search">${SR.on ? 'Убрать' : 'Показать'} строку «Поиск»</button></div>
    <div class="ws-wlist">${L.map(x => `<button type="button" data-wopen="${esc(x.id)}" ${x.sd ? `data-wsd="${esc(x.sd)}"` : ''}><span>${esc(x.t)}</span><small class="ws-ws ${x.s === 'открыто' ? 'on' : ''}">${x.s}</small></button>`).join('')}</div>`);
  p.onclick = e => {
    const b = e.target.closest('[data-wa],[data-wopen]'); if (!b) return;
    if (b.dataset.wa === 'all') showAllWins();
    else if (b.dataset.wa === 'sdall') resDomains().filter(d => d.status === 'found').forEach((d, i) => { openSd(d.domain); const w = WM.get('sd_' + d.domain.replace(/[^\w.-]/g, '_')); if (w && i){ const r = WM.rectPx(w); const D = q('#wsDesk').getBoundingClientRect(); w.fr = {...w.fr, x: Math.max(0, (r.x - i * 24) / D.width), y: Math.min(.6, (r.y + i * 24) / D.height)}; WM.apply(w); } });
    else if (b.dataset.wa === 'search') toggleSearch();
    else showWin(b.dataset.wopen, b.dataset.wsd);
    closeWinPop();
  };
}
function showWin(id, sd){
  const w = WM.get(id);
  if (w && w.pop && !w.pop.closed){ w.pop.focus(); return; }
  if (w){ WM.restore(id); WM.focus(id); return; }
  if (sd){ openSd(sd); return; }
  if (id === 'mgr'){ gateClick({target: q('#wsOpenMgr')}); return; }
  if (id === 'loa'){ openLoa(); return; }
}
function showAllWins(){
  // свёрнутые окна возвращаются на свои места — раскладка не применяется, окно заявления не сдвигается
  WM.all().forEach(w => { if (w.min && !(w.pop && !w.pop.closed)) WM.restore(w.id); });
  WM.dock();
  toast('Все окна показаны на своих местах');
}

// ================================================================ (0.8.0) удаление недавних дел
async function deleteCase(id){
  const r = await fetch(`/api/ws/${id}`, {method: 'DELETE'});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
  return j;
}
function bindRecent(total){
  const box = q('#wsRecent'); if (!box) return;
  box.onclick = async e => {
    const del = e.target.closest('[data-wsdel]'), yes = e.target.closest('[data-wsdelyes]'), no = e.target.closest('[data-wsdelno]');
    const all = e.target.closest('#wsDelAll'), allYes = e.target.closest('#wsDelAllYes'), allNo = e.target.closest('#wsDelAllNo');
    if (del){
      const td = del.closest('td');
      td.dataset.html = td.innerHTML;
      td.innerHTML = `<span class="ws-delq">Удалить дело и его файлы с диска?</span> <button class="sm danger" data-wsdelyes="${esc(del.dataset.wsdel)}">Удалить</button> <button class="sm" data-wsdelno="1">Нет</button>`;
    } else if (no){ const td = no.closest('td'); td.innerHTML = td.dataset.html || ''; }
    else if (yes){
      yes.disabled = true;
      try { await deleteCase(yes.dataset.wsdelyes); toast('Дело удалено'); } catch (x) { toast('Не удалилось: ' + esc(x.message), 6000); }
      showIntake();
    } else if (all){
      q('#wsDelBar').innerHTML = `<div class="msg warn">Удалить все дела рабочего места (${total})? Папки дел с файлами и результаты их проверок будут стёрты с диска.
        <button class="sm danger" id="wsDelAllYes">Удалить все</button> <button class="sm" id="wsDelAllNo">Отмена</button></div>`;
    } else if (allNo) q('#wsDelBar').innerHTML = '';
    else if (allYes){
      allYes.disabled = true;
      try {
        const j = await api('/api/ws/recent/clear', {});
        toast(`Удалено дел: ${j.deleted.length}${j.skipped.length ? `, оставлено (ещё разбираются): ${j.skipped.length}` : ''}`, 6000);
      } catch (x) { toast('Не удалилось: ' + esc(x.message), 6000); }
      showIntake();
    }
  };
}

// ================================================================ запуск
WM.bind();
init().then(loadOperator);
})();
