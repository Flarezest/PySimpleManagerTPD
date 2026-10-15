/* doctool — чтение данных из manager.reg.ru и заполнение базовой анкеты.
 *
 * Скрипт внедряется расширением во вкладку https://manager.reg.ru/ (изолированный мир расширения).
 * Страницы manager загружаются отсюда обычными GET-запросами того же сайта — с входом оператора,
 * как при открытии ссылок вручную. Скрипт ничего не нажимает и ничего не отправляет в manager.
 *
 * Порядок для одного домена (lookup):
 *   1) /bill/bills?searchstring=<домен>&from_head=1&_csrf=<токен>  — строка домена, service_id;
 *   2) /tech/srv_details?service_id=<id>                           — Sd: данные администратора;
 *   3) /tech/service_details?service_id=<id>                       — S: provider, user_id, dname;
 *   4) (0.6.0 — физлица, с 0.7.2 — и юрлица) ссылка «Идентификация через Госуслуги» со страницы Sd — state
 *      и ссылка на JSON ЕСИА. Сам JSON лежит на другом сайте (identity.reg.ru) — его загружает background.js.
 *
 * Аккаунт (account, 0.6.0): /manager/user_details?user_id=N (базовая анкета — скрытый блок страницы,
 * логин, обслуживающая организация) и /user/N/runic_details (тип анкеты, значения формы).
 * По логину/e-mail номер аккаунта находится через /user/<логин>/runic_details.
 *
 * Заполнение (fillRunic, 0.6.0): только во вкладке «Базовая анкета пользователя #N», которую видит оператор:
 * вписывает значения в поля формы и вызывает события страницы (input/change/blur), чтобы она
 * подсветила изменённые поля и пересчитала English name. Кнопку «Сохранить» не нажимает.
 * С 0.7.3 — формы ИП и юрлица: если новый владелец другого типа, переключатель «Физик / ИП / Юрик» на странице
 * переводится на нужный тип (страница сама показывает нужную форму), затем заполняется эта форма.
 *
 * С 0.7.6 — смена администратора домена (рабочее место программы), только во вкладке оператора:
 *   domainMoveFill        — шаг 1 «Перенос доменов между аккаунтами»: домены, «смена администратора»,
 *                           «перенос на другой аккаунт» и принимающий аккаунт, документы в «Загрузить документы»;
 *   domainMoveRead        — шаг 2 «данные нового администратора»: прочитать данные на странице;
 *   domainMoveMark        — подсветить их по итогам сверки с заявлением (зелёный / красный);
 *   domainMoveFillContacts — шаг 2, «заполнить вручную»: ФИО, телефон, e-mail из заявления.
 * Кнопки «Далее», «Использовать» и остальные флажки страницы скрипт не трогает.
 *
 * С 0.8.2 — безбумажные операции: loaFill — «Добавить новый документ от пользователя» (/userdocs/loa_add,
 * доверенность): номер паспорта / код страны компании, ФИО / ИНН компании, e-mail, телефон. «Добавить документ»,
 * «Показать домены…», вид документа и срок действия — оператор.
 */
(() => {
  const VERSION = '0.8.2';
  if (window.__doctoolManager && window.__doctoolManager.version === VERSION) return;

  // Поля Sd, которые передаются в doctool. authinfo, адреса и служебные поля не передаются.
  // E-mail администратора передаётся (с 0.5.0): e_mail — зоны .RU/.РФ/.SU, o_email — остальные зоны.
  // Телефон администратора (phone / o_phone) — с 0.8.2, для доверенности (безбумажные операции).
  // Юридический адрес юрлица (улица, дом, корпус, строение, индекс) — с 0.7.2, для сверки с ЕСИА.
  const SD_FIELDS = [
    // физлицо (.RU/.РФ/.SU)
    'person_r_surname', 'person_r_name', 'person_r_patronimic', 'person_surname', 'person_name', 'person_patronimic',
    'birth_date', 'passport_series', 'passport_number_short', 'passport_date', 'passport_place', 'passport_place_id',
    'passport_expiration_date', 'is_entrepreneur',
    // юрлицо (.RU/.РФ/.SU); юридический адрес — для сверки с ЕСИА (0.7.2)
    'org_r', 'org', 'kpp', 'address_r_street', 'address_r_house', 'address_r_frame', 'address_r_building', 'address_r_zip',
    // администратор (владелец) в остальных зонах — группа o_* (o_deti_org, o_gtld…)
    'o_company', 'o_company_ru', 'o_first_name', 'o_first_name_ru', 'o_last_name', 'o_last_name_ru',
    'o_patronimic', 'o_patronimic_ru', 'o_country_code', 'o_birth_date',
    // контактный e-mail; (0.8.2) телефон
    'e_mail', 'o_email', 'phone', 'o_phone',
    // общее
    'code', 'country', 'last_admin_change', 'date_update_details', 'matches_user_contacts',
  ];
  // Группа контактов администратора: ru_pp/ru_org; иначе o_* (владелец); иначе самая заполненная.
  const pickGroup = groups => {
    const names = Object.keys(groups);
    return names.find(g => g === 'ru_pp' || g === 'ru_org') || names.find(g => /^o_/.test(g))
      || (Object.entries(groups).sort((a, b) => b[1] - a[1])[0] || [''])[0];
  };
  // S: с 0.7.6 — комментарий администратора (admin_comment, в программе — буква «К») и поля для проверок домена
  // на рабочем месте (запреты, заморозка, блокировки). Комментарий пользователя (comment) не передаётся.
  const S_FIELDS = ['provider', 'dname', 'contype', 'state', 'substate', 'user_id', 'expiration_date', 'creation_date',
    'trans_in_from', 'admin_comment', 'changeProhibited', 'clientHold', 'serverHold', 'clientUpdateProhibited',
    'serverUpdateProhibited', 'clientTransferProhibited', 'serverTransferProhibited', 'clientDeleteProhibited',
    'serverDeleteProhibited', 'freeze_date', 'freeze_reason', 'operation_blocks', 'lock_transfer_till', 'trans_out_to'];

  class LookupError extends Error {
    constructor(code, message, extra) { super(message); this.code = code; this.extra = extra || {}; }
  }

  let lastToken = '';
  const tokenOf = doc => (doc.querySelector('meta[name="_csrf"]') || {}).content || '';
  // Обычная страница manager: «старая» (#content) или новая на Bootstrap (nav.navbar, напр. базовая анкета).
  const isManagerPage = doc => !!doc.querySelector('meta[name="_csrf"]')
    && !!(doc.querySelector('#content') || doc.querySelector('nav.navbar'));
  const norm = s => (s || '').toString().trim().toLowerCase().replace(/\.$/, '');
  const text = el => ((el && el.textContent) || '').replace(/\s+/g, ' ').trim();

  async function getDoc(path) {
    let r;
    try {
      r = await fetch(path, { credentials: 'include', cache: 'no-store' });
    } catch (e) {
      throw new LookupError('network', 'manager не отвечает: ' + (e && e.message || e));
    }
    if (r.status === 401 || r.status === 403) {
      throw new LookupError('not_logged_in', `manager требует вход (HTTP ${r.status}). Войдите в manager в Chrome и повторите.`);
    }
    if (!r.ok) throw new LookupError('http', `manager вернул HTTP ${r.status} на ${path}`);
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    if (!isManagerPage(doc)) {
      throw new LookupError('not_logged_in', 'Вместо страницы manager открылась другая страница — вероятно, нужен вход.');
    }
    lastToken = tokenOf(doc) || lastToken;
    return doc;
  }

  // ------------------------------------------------------------------ страница счетов

  function parseBills(doc) {
    const m = /Всего позиций:\s*(\d+)/.exec(doc.body ? doc.body.textContent : '');
    const total = m ? +m[1] : null;
    const form = doc.querySelector('form[name="billlist"]');
    const table = form && form.querySelector('table');
    if (!table) return { total: total || 0, rows: [] };
    const header = [...table.rows].find(tr => tr.querySelector('th') && /Название/.test(tr.textContent));
    const heads = header ? [...header.cells].map(c => text(c)) : [];
    const col = re => heads.findIndex(h => re.test(h));
    const iName = col(/^Название/), iSt = col(/^St$/), iOwner = col(/^Владелец/);
    const rows = [];
    for (const tr of table.rows) {
      const sid = tr.querySelector('input[name^="service_id_"]');
      if (!sid) continue;
      const n = sid.name.slice('service_id_'.length);
      const val = k => (tr.querySelector(`input[name="${k}_${n}"]`) || {}).value || '';
      const titled = tr.querySelector('[data-service-title]');
      const cells = tr.cells;
      const owner = iOwner >= 0 && cells[iOwner] ? cells[iOwner].querySelector('a[href*="user_details"]') : null;
      rows.push({
        service_id: sid.value, user_id: val('user_id'), bill_id: val('bill_id'), pos_id: val('pos_id'),
        title: titled ? titled.getAttribute('data-service-title') : '',
        name_cell: iName >= 0 ? text(cells[iName]) : '',
        state: iSt >= 0 ? text(cells[iSt]) : '',
        bill_owner: text(owner),
      });
    }
    return { total: total === null ? rows.length : total, rows };
  }

  function matchTitle(title, names) {
    const t = norm(title);
    if (!t) return false;
    if (names.includes(t)) return true;
    const m = /^(?:регистрация|перенос|продление|трансфер)\s+домена\s+(\S+)$/i.exec(title.trim());
    return !!(m && names.includes(norm(m[1])));
  }

  function pickService(rows, names) {
    const cand = rows.filter(r => matchTitle(r.title, names) || (!r.title && names.includes(norm(r.name_cell))));
    const byId = new Map();
    for (const r of cand) {
      const prev = byId.get(r.service_id);
      if (!prev) byId.set(r.service_id, { ...r });
      else if (r.state === 'A') prev.state = 'A';
    }
    const services = [...byId.values()];
    if (!services.length) return {};
    const active = services.filter(s => s.state === 'A');
    if (active.length === 1) return { service: active[0] };
    if (!active.length && services.length === 1) return { service: services[0] };
    return {
      error: 'ambiguous',
      message: `По домену найдено несколько услуг: ${services.map(s => `${s.service_id} (${s.state || '?'})`).join(', ')}`,
      candidates: services,
    };
  }

  // ------------------------------------------------------------------ Sd и S

  function parseSd(doc) {
    const form = doc.querySelector('form#srv_details');
    if (!form) throw new LookupError('bad_page', 'На странице Sd нет таблицы полей (form#srv_details).');
    const fields = {}, groups = {};
    for (const tr of form.querySelectorAll('tr')) {
      const c = tr.cells;
      if (c.length < 3) continue;
      const el = c[2].querySelector('input[name^="value__"], textarea[name^="value__"]');
      if (!el) continue;
      let grp = text(c[0]), fld = text(c[1]);
      const m = /^value__(.*?)____(.+)$/.exec(el.name);
      if (m) { grp = grp || m[1]; fld = fld || m[2]; }
      if (grp === 'authinfo' || fld === 'authinfo') continue;
      const val = (el.value || '').trim();
      if (grp && val) groups[grp] = (groups[grp] || 0) + 1;
      if (!SD_FIELDS.includes(fld)) continue;
      if (fld in fields && fields[fld] !== '') continue;
      fields[fld] = val;
    }
    const body = text(doc.querySelector('#content'));
    const trustee = /Trustee:\s*(Да|Нет)/i.exec(body);
    const status = /Статус услуги:\s*(.+?\([A-Z]+\))/.exec(body);
    // ссылки со страницы Sd: «Данные персоны услуги» и «Идентификация через Госуслуги» (у физлица — ?user_id=,
    // у персоны/юрлица — ?person_id=; идентификация относится к администратору домена, а не к аккаунту)
    const href = sel => { const a = doc.querySelector(sel); return a ? absUrl(a.getAttribute('href')) : ''; };
    const links = {
      person: href('a[href*="/manager/user_details?"], a[href*="/manager/person?"]'),
      esia: href('a[href*="esia_identifications"]'),
    };
    return { group: pickGroup(groups), fields, trustee: trustee ? trustee[1] : '', status: status ? status[1] : '', links };
  }

  const absUrl = h => { try { return h ? new URL(h, location.origin).href : ''; } catch (e) { return ''; } };

  // ------------------------------------------------------------------ идентификация через Госуслуги (ЕСИА)

  // Таблица /manager/esia_identifications?…: колонки identity_id, object, object_id, state, file, comment,
  // creation_date, processed_date, login_processed, doc_id, esia_state_id, reason, action, params.
  function parseEsiaList(doc) {
    const table = [...doc.querySelectorAll('table')].find(t => [...t.querySelectorAll('th')].some(th => text(th) === 'state'));
    if (!table) {
      if (!isManagerPage(doc)) throw new LookupError('bad_page', 'Страница идентификации через Госуслуги не открылась.');
      return { rows: [], latest: null };
    }
    const heads = [...table.querySelectorAll('th')].map(th => text(th));
    const rows = [];
    for (const tr of table.querySelectorAll('tr')) {
      const cells = [...tr.querySelectorAll('td')];
      if (cells.length < heads.length / 2) continue;
      const r = {};
      heads.forEach((h, i) => { if (cells[i]) r[h] = text(cells[i]); });
      const fileCell = cells[heads.indexOf('file')];
      const a = fileCell && fileCell.querySelector('a[href]');
      r.file_url = a ? absUrl(a.getAttribute('href')) : '';
      delete r.file;
      if (r.state !== undefined) rows.push(r);
    }
    // последняя попытка — по дате создания (формат «ГГГГ-ММ-ДД чч:мм:сс» сортируется как строка)
    const latest = rows.length ? [...rows].sort((a, b) => (b.creation_date || '').localeCompare(a.creation_date || ''))[0] : null;
    return { rows, latest };
  }

  // ------------------------------------------------------------------ аккаунт: данные пользователя и базовая анкета

  // /manager/user_details?user_id=N. Базовая анкета уже есть в HTML (блок .user_contacts_block, скрыт стилем),
  // кнопка «Базовая анкета пользователя» только показывает его.
  function parseUserDetails(doc) {
    const h = text(doc.querySelector('#content h2'));
    const m = /#\s*(\d+)/.exec(h) || /user_id=(\d+)/.exec((doc.querySelector('a[href*="user_details?user_id="]') || {}).href || '');
    const ba = {};
    const block = doc.querySelector('.user_contacts_block');
    if (block) {
      for (const tr of block.querySelectorAll('tr')) {
        const c = tr.querySelectorAll('td');
        if (c.length < 2) continue;
        const k = text(c[0]);
        if (/^[a-z_]+\.[a-z0-9_]+$/i.test(k)) ba[k] = (c[1].textContent || '').replace(/[ \t]+/g, ' ').trim();
      }
    }
    const det = doc.querySelector('#user_details');
    const row = title => {
      if (!det) return null;
      return [...det.querySelectorAll('tr')].find(tr => { const th = tr.querySelector('th'); return th && text(th).startsWith(title); }) || null;
    };
    const first = det && det.querySelector('tr td');
    const login = first ? text(first.querySelector('b')) : '';
    // (0.8.1) e-mail аккаунта — поле «E-mail» (input name=email); с логином может не совпадать
    const em = det && det.querySelector('input[name="email"]');
    const emRow = row('E-mail');
    const email = em ? (em.value || '').trim() : emRow ? text(emRow.querySelector('td')).split(/\s/)[0] : '';
    const statuses = first ? [...first.querySelectorAll('span')].map(s => text(s)).filter(Boolean) : [];
    const org = row('Обслуживающая организация');
    const orgText = org ? text(org.querySelector('td')) : '';
    const orgCur = /текущая:\s*(.+)$/i.exec(orgText);
    const companyId = org ? (org.querySelector('input[name="company_id"]') || {}).value || '' : '';
    const contypes = row('contypes');
    const ent = doc.querySelector('#user_details input[name="is_entrepreneur"]');
    const links = {};
    for (const [k, sel] of [['runic', 'a[href*="/runic_details"]'], ['esia', 'a[href*="esia_identifications"]'],
                            ['persons', 'a[href*="/manager/persons?"]'], ['history', 'a[href*="history_changes_of_base_form"]']]) {
      const a = doc.querySelector(sel);
      if (a) links[k] = absUrl(a.getAttribute('href'));
    }
    return {
      user_id: m ? m[1] : '', login, email, statuses, ba,
      servicing_org: orgCur ? orgCur[1].trim() : orgText, servicing_org_id: companyId,
      contypes: contypes ? text(contypes.querySelector('td')) : '',
      is_entrepreneur: !!(ent && ent.checked), links,
    };
  }

  // Поля форм на странице базовой анкеты (/user/N/runic_details), которые читает программа.
  // Форма «Физик»/«ИП» — form#ru_pp_contacts (у ИП видны ещё ИНН и адрес регистрации), «Юрик» — form#ru_org_contacts.
  const RUNIC_FIELDS = ['person_r_surname', 'person_r_name', 'person_r_patronimic', 'person', 'passport_number',
    'passport_date', 'passport_place', 'code', 'birth_date', 'p_addr_r_zip', 'p_addr_r_area', 'p_addr_r_city',
    'p_addr_r_addr', 'country', 'p_addr_country', 'p_addr_zip', 'p_addr_area', 'p_addr_city', 'p_addr_addr',
    'p_addr_recipient', 'phone', 'sms_security_number', 'fax', 'e_mail'];
  const RUNIC_ORG_FIELDS = ['country', 'org_r', 'org', 'parent_org_r', 'code', 'kpp', 'address_r_zip', 'address_r_area',
    'address_r_city', 'address_r_addr', 'p_addr_country', 'p_addr_zip', 'p_addr_area', 'p_addr_city', 'p_addr_addr',
    'p_addr_recipient', 'phone', 'sms_security_number', 'fax', 'e_mail', 'actt_e_mail'];
  // Что заполняет программа — по типу нового владельца, в порядке полей на странице. Гражданство / страна
  // регистрации, страна почтового адреса, область, SMS-безопасность, факс, English name, родительская организация
  // и флажок «Внесены данные нового владельца» не заполняются.
  const RUNIC_FILL = {
    pp: ['person_r_surname', 'person_r_name', 'person_r_patronimic', 'passport_number', 'passport_date',
      'passport_place', 'birth_date', 'p_addr_zip', 'p_addr_city', 'p_addr_addr', 'p_addr_recipient', 'phone', 'e_mail'],
    ip: ['person_r_surname', 'person_r_name', 'person_r_patronimic', 'passport_number', 'passport_date',
      'passport_place', 'code', 'birth_date', 'p_addr_r_zip', 'p_addr_r_city', 'p_addr_r_addr', 'p_addr_zip',
      'p_addr_city', 'p_addr_addr', 'p_addr_recipient', 'phone', 'e_mail'],
    org: ['org_r', 'code', 'kpp', 'address_r_zip', 'address_r_city', 'address_r_addr', 'p_addr_zip', 'p_addr_city',
      'p_addr_addr', 'p_addr_recipient', 'phone', 'e_mail', 'actt_e_mail'],
  };
  const TYPE_RU = { pp: 'физлицо', ip: 'ИП', org: 'юрлицо' };
  const TYPE_PAGE = { pp: 'Физик', ip: 'ИП', org: 'Юрик' };
  const formFor = (doc, type) => doc.querySelector(type === 'org' ? 'form#ru_org_contacts' : 'form#ru_pp_contacts');
  const readForm = (form, names) => {
    const out = {};
    if (form) {
      for (const k of names) {
        const el = form.querySelector(`[name="${k}"]`);
        if (el) out[k] = (el.value || '').trim();
      }
    }
    return out;
  };
  const shown = el => !!el && !el.classList.contains('hidden') && !(el.closest('tr') && el.closest('tr').classList.contains('hidden'));

  function parseRunic(doc) {
    const h = text(doc.querySelector('h3'));
    const m = /#\s*(\d+)/.exec(h);
    const checked = doc.querySelector('input[name="type"]:checked') || doc.querySelector('input[name="type"][checked]');
    const type = checked ? checked.value : '';
    const pp = formFor(doc, 'pp'), org = formFor(doc, 'org');
    const valuesPp = readForm(pp, RUNIC_FIELDS), valuesOrg = readForm(org, RUNIC_ORG_FIELDS);
    if (type !== 'ip') ['code', 'p_addr_r_zip', 'p_addr_r_area', 'p_addr_r_city', 'p_addr_r_addr'].forEach(k => {
      if (!valuesPp[k]) delete valuesPp[k];          // строки ИП у физлица скрыты и пусты
    });
    return {
      user_id: m ? m[1] : '', type, has_pp_form: !!pp, has_org_form: !!org,
      values: type === 'org' ? valuesOrg : valuesPp, values_pp: valuesPp, values_org: valuesOrg,
    };
  }

  // Переключатель «Физик / ИП / Юрик» на странице базовой анкеты. Обработчик страницы на change показывает нужную
  // форму (у ИП — строки ИНН и адреса регистрации, is_entrepreneur = 1). Ничего не сохраняется.
  function setRunicType(doc, type) {
    const radios = [...doc.querySelectorAll('input[name="type"]')];
    const r = radios.find(x => x.value === type);
    if (!r) return false;
    radios.forEach(x => {
      x.checked = x === r;
      const l = x.closest('label');
      if (l) l.classList.toggle('active', x === r);
    });
    r.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  function typeReady(doc, type) {
    const form = formFor(doc, type);
    if (!shown(form)) return false;
    if (type === 'ip') {
      const code = form.querySelector('[name="code"]');
      return shown(code) && (form.querySelector('[name="is_entrepreneur"]') || {}).value === '1';
    }
    return true;
  }

  // Заполнение формы на открытой странице базовой анкеты (вкладка оператора). fields — {имя поля: значение};
  // пустые значения пропускаются (кроме restore — возврата прежних значений). «Сохранить» не нажимается.
  // opts.type — тип нового владельца (pp / ip / org, с 0.7.3): если на странице другой, переключатель типа
  // переводится на нужный. Без opts.type — как в 0.6.0: только физлицо, другой тип — ошибка not_person.
  // opts.back_type (с restore) — после возврата значений вернуть и прежний тип анкеты.
  function fillRunic(expectedId, fields, opts) {
    opts = opts || {};
    const info = parseRunic(document);
    if (!info.user_id) return { ok: false, error: { code: 'bad_page', message: 'Во вкладке не страница базовой анкеты.' } };
    if (String(info.user_id) !== String(expectedId)) {
      return { ok: false, error: { code: 'wrong_account', message: `Во вкладке базовая анкета аккаунта #${info.user_id}, а нужен #${expectedId}.` } };
    }
    const target = opts.type || 'pp';
    if (!RUNIC_FILL[target]) return { ok: false, error: { code: 'bad_request', message: `Неизвестный тип анкеты: ${target}` } };
    let switched = false;
    if (info.type !== target) {
      if (!opts.type) {
        const who = TYPE_RU[info.type] || (info.type || 'неизвестный тип');
        return { ok: false, error: { code: 'not_person', type: info.type,
          message: `Аккаунт #${expectedId} оформлен на ${who}: автозаполнение без типа нового владельца — только для физлиц. Вкладка базовой анкеты открыта.` } };
      }
      if (!setRunicType(document, target)) {
        return { ok: false, error: { code: 'bad_page', message: `На странице нет переключателя типа «${TYPE_PAGE[target]}».` } };
      }
      switched = true;
    }
    if (!typeReady(document, target)) {
      return { ok: false, error: { code: 'type_switch', type: info.type,
        message: `Не удалось переключить базовую анкету на «${TYPE_PAGE[target]}»: страница не показала нужную форму. Переключите тип вручную и нажмите «Заполнить автоматически» ещё раз.` } };
    }
    const form = formFor(document, target);
    const changed = [], same = [], missing = [];
    const fire = (el, type) => el.dispatchEvent(new Event(type, { bubbles: type !== 'blur' }));
    fields = fields || {};
    for (const name of RUNIC_FILL[target]) {     // в порядке полей на странице
      if (!(name in fields)) continue;
      const value = (fields[name] ?? '').toString();
      if (!value && !opts.restore) continue;
      const el = form.querySelector(`[name="${name}"]`);
      if (!el) { missing.push(name); continue; }
      const old = el.value;
      const label = text((el.previousElementSibling && el.previousElementSibling.matches('.input-group-addon'))
        ? el.previousElementSibling : form.querySelector(`.input-group-addon.${name}`)) || name;
      if (old === value) { same.push({ name, label, value }); continue; }
      try { el.focus({ preventScroll: true }); } catch (e) { /* ничего */ }
      el.value = value;
      fire(el, 'input');
      fire(el, 'change');      // страница помечает поле как изменённое (класс unsaved)
      try { el.blur(); } catch (e) { /* ничего */ }
      fire(el, 'blur');        // у ФИО и названия организации — пересчёт English name (translit_pers_org)
      el.style.outline = opts.restore ? '' : '2px solid #f0ad4e';
      changed.push({ name, label, old, new: value });
    }
    // «Вернуть как было» после переключения типа: значения формы возвращены — возвращаем и прежний тип
    let typeBack = false;
    if (opts.restore && opts.back_type && opts.back_type !== target && RUNIC_FILL[opts.back_type]) {
      typeBack = setRunicType(document, opts.back_type);
    }
    const after = parseRunic(document);
    return { ok: true, data: { user_id: info.user_id, type: target, type_old: info.type, type_switched: switched,
                               type_back: typeBack ? opts.back_type : '',
                               type_page: TYPE_PAGE[target], changed, same, missing, values: after.values,
                               values_pp: after.values_pp, values_org: after.values_org } };
  }

  async function account(reqId, query) {
    const progress = (step, message) => {
      try { chrome.runtime.sendMessage({ type: 'progress', reqId, step, message }); } catch (e) { /* вне расширения */ }
    };
    try {
      let uid = String((query && query.user_id) || '').trim();
      const login = String((query && query.login) || '').trim();
      if (!uid && login) {
        progress('account', `${login}: поиск аккаунта по логину`);
        const r = parseRunic(await getDoc(`/user/${encodeURIComponent(login)}/runic_details`));
        if (!r.user_id) throw new LookupError('not_found', `Аккаунт с логином «${login}» не найден в manager.`);
        uid = r.user_id;
      }
      if (!/^\d+$/.test(uid)) throw new LookupError('bad_request', 'Не указан номер аккаунта.');
      progress('user_details', `аккаунт #${uid}: данные пользователя`);
      const det = parseUserDetails(await getDoc(`/manager/user_details?user_id=${uid}`));
      if (!det.user_id && !det.login) throw new LookupError('not_found', `Аккаунт #${uid} не найден в manager.`);
      progress('runic', `аккаунт #${uid}: базовая анкета`);
      const runic = parseRunic(await getDoc(`/user/${uid}/runic_details`));
      return {
        ok: true,
        data: {
          ...det, user_id: det.user_id || uid, runic,
          urls: {
            user_details: `${location.origin}/manager/user_details?user_id=${uid}`,
            runic: `${location.origin}/user/${uid}/runic_details`,
            esia: det.links.esia || `${location.origin}/manager/esia_identifications?user_id=${uid}`,
          },
        },
      };
    } catch (e) {
      if (e instanceof LookupError) return { ok: false, error: { code: e.code, message: e.message, ...e.extra } };
      return { ok: false, error: { code: 'error', message: String((e && e.message) || e) } };
    }
  }

  // (0.8.1) e-mail аккаунта владельца услуги — поле «E-mail» страницы «Данные пользователя»; аккаунт у доменов
  // обычно общий, страница читается один раз за 10 минут.
  const emailCache = new Map();   // user_id → {t, value}
  async function accountEmailFor(uid) {
    const hit = emailCache.get(uid);
    if (hit && Date.now() - hit.t < 10 * 60 * 1000) return hit.value;
    let value = '';
    try {
      value = parseUserDetails(await getDoc(`/manager/user_details?user_id=${encodeURIComponent(uid)}`)).email || '';
    } catch (e) {
      return '';                    // страница не открылась — домен всё равно показываем, без e-mail аккаунта
    }
    if (emailCache.size > 500) emailCache.clear();
    emailCache.set(uid, { t: Date.now(), value });
    return value;
  }

  // Страница идентификации общая у доменов одной персоны — в пределах одной загрузки читается один раз.
  const esiaCache = new Map();   // url → {t, value}
  async function esiaFor(url, task) {
    const key = `${task || ''}|${url}`;
    const hit = esiaCache.get(key);
    if (hit && Date.now() - hit.t < 10 * 60 * 1000) return hit.value;
    let value;
    try {
      const { rows, latest } = parseEsiaList(await getDoc(url.replace(location.origin, '')));
      value = latest
        ? { url, count: rows.length, state: latest.state || '', file_url: latest.file_url, latest,
            history: rows.map(r => ({ state: r.state, creation_date: r.creation_date, processed_date: r.processed_date,
                                      action: r.action, reason: r.reason, comment: r.comment })) }
        : { url, count: 0, state: '' };
    } catch (e) {
      if (e instanceof LookupError && e.code === 'not_logged_in') throw e;
      value = { url, error: String((e && e.message) || e) };
    }
    if (esiaCache.size > 500) esiaCache.clear();
    esiaCache.set(key, { t: Date.now(), value });
    return value;
  }

  function parseS(doc) {
    const form = doc.querySelector('form#service_details');
    if (!form) throw new LookupError('bad_page', 'На странице S нет таблицы свойств (form#service_details).');
    const out = {};
    for (const k of S_FIELDS) {
      const el = form.querySelector(`[name="${k}"]`);
      if (el) out[k] = (el.value || '').trim();
    }
    // (0.7.6) логин аккаунта: кнопка «Клиент обращается через Телефон» над формой (data-user-login, data-user-id)
    const who = doc.querySelector('[data-user-login]');
    if (who && (!out.user_id || who.getAttribute('data-user-id') === out.user_id)) {
      out.login = (who.getAttribute('data-user-login') || '').trim();
    }
    return out;
  }

  // ------------------------------------------------------------------ один домен целиком

  async function lookup(reqId, domain, ascii, opts) {
    opts = opts || {};
    const names = [...new Set([norm(domain), norm(ascii)].filter(Boolean))];
    const progress = (step, message) => {
      try { chrome.runtime.sendMessage({ type: 'progress', reqId, step, message }); } catch (e) { /* вне расширения */ }
    };
    try {
      if (!lastToken) lastToken = tokenOf(document);
      let found = null;
      const searched = [];
      for (const q of names) {
        progress('bills', `${q}: поиск в счетах`);
        const url = `/bill/bills?searchstring=${encodeURIComponent(q)}&from_head=1&_csrf=${encodeURIComponent(lastToken)}`;
        const bills = parseBills(await getDoc(url));
        searched.push({ query: q, total: bills.total, rows: bills.rows.length });
        const pick = pickService(bills.rows, names);
        if (pick.error) throw new LookupError(pick.error, pick.message, { candidates: pick.candidates });
        if (pick.service) { found = { ...pick.service, total: bills.total, query: q }; break; }
      }
      if (!found) {
        const s = searched.map(x => `«${x.query}» — позиций ${x.total}`).join('; ');
        const more = searched.some(x => x.total > x.rows) ? ' (просмотрена только первая страница выдачи)' : '';
        return { ok: false, error: { code: 'not_found', message: `Домен не найден в счетах manager: ${s}${more}`, searched } };
      }
      progress('sd', `${domain}: Sd (service_id ${found.service_id})`);
      const sd = parseSd(await getDoc(`/tech/srv_details?service_id=${encodeURIComponent(found.service_id)}`));
      progress('s', `${domain}: S (service_id ${found.service_id})`);
      const s = parseS(await getDoc(`/tech/service_details?service_id=${encodeURIComponent(found.service_id)}`));
      if (s.dname && !names.includes(norm(s.dname))) {
        throw new LookupError('mismatch', `Страница S относится к другому домену: ${s.dname}`);
      }
      // ЕСИА — у физлиц (ru_pp) и юрлиц (ru_org, с 0.7.2): по ссылке со страницы Sd этого домена
      const accId = s.user_id || found.user_id;
      let accountEmail = '';
      if (accId && opts.account_email !== false) {
        progress('user_details', `${domain}: e-mail аккаунта #${accId}`);
        accountEmail = await accountEmailFor(accId);
      }
      let esia = null;
      if (opts.esia !== false && (sd.group === 'ru_pp' || sd.group === 'ru_org') && sd.links && sd.links.esia) {
        progress('esia', `${domain}: идентификация через Госуслуги`);
        esia = await esiaFor(sd.links.esia, opts.task);
      }
      return {
        ok: true,
        data: {
          domain, service_id: found.service_id, account: accId, bill_owner: found.bill_owner,
          login: s.login || '', account_email: accountEmail,
          bill_state: found.state, sd, s, esia,
          urls: {
            bills: `${location.origin}/bill/bills?searchstring=${encodeURIComponent(found.query)}`,
            sd: `${location.origin}/tech/srv_details?service_id=${found.service_id}`,
            s: `${location.origin}/tech/service_details?service_id=${found.service_id}`,
          },
        },
      };
    } catch (e) {
      if (e instanceof LookupError) return { ok: false, error: { code: e.code, message: e.message, ...e.extra } };
      return { ok: false, error: { code: 'error', message: String((e && e.message) || e) } };
    }
  }

  // ------------------------------------------------------------------ смена администратора домена (0.7.6)

  const fireEv = (el, type) => el.dispatchEvent(new Event(type, { bubbles: type !== 'blur' && type !== 'focus' }));
  const mark = (el, color) => { if (el) { el.style.outline = color ? `2px solid ${color}` : ''; el.style.outlineOffset = color ? '1px' : ''; } };

  // Шаг 1: /manager/domain_move — form с textarea[name=domains]. p: {domains: [], change_admin, change_account,
  // login_to, files: [{name, type, b64}]}. Формы не отправляются.
  function domainMoveFill(p) {
    p = p || {};
    const ta = document.querySelector('textarea[name="domains"]');
    const form = ta && ta.closest('form');
    if (!form) {
      return { ok: false, error: { code: 'bad_page', message: 'Во вкладке не первый шаг «Перенос доменов между аккаунтами» (нет поля «Домены»).' } };
    }
    const changed = [], notes = [];
    const setVal = (el, v, label) => {
      if (!el) { notes.push(`нет поля «${label}»`); return; }
      if (el.value !== v) {
        el.value = v;
        fireEv(el, 'input');
        fireEv(el, 'change');
        changed.push(label);
      }
      mark(el, '#f0ad4e');
    };
    const check = (name, on, label) => {
      const el = form.querySelector(`input[type="checkbox"][name="${name}"]`);
      if (!el) { notes.push(`нет флажка «${label}»`); return; }
      if (el.checked !== !!on) {
        el.checked = !!on;
        fireEv(el, 'change');          // страница показывает/скрывает поля этой операции (jQuery .change)
        changed.push(`${label}: ${on ? 'да' : 'нет'}`);
      }
      mark(el.closest('label') || el, '#f0ad4e');
    };
    // (0.8.2) список пуст — поле «Домены» не трогается (оператор впишет сам)
    if ((p.domains || []).length) setVal(ta, p.domains.join('\n'), 'Домены');
    else notes.push('домены не вписаны — список в программе пуст');
    check('do_change_admin', p.change_admin !== false, 'смена администратора');
    check('do_change_account', !!p.change_account, 'перенос на другой аккаунт');
    if (p.change_account) setVal(form.querySelector('input[name="login_to"]'), String(p.login_to || ''), 'Принимающий аккаунт');
    let attached = [];
    if (p.files && p.files.length) {
      const fi = form.querySelector('input[type="file"][name="attachment"]');
      if (!fi) notes.push('нет поля «Загрузить документы»');
      else {
        const dt = new DataTransfer();
        for (const f of p.files) {
          const bin = atob(f.b64 || '');
          const bytes = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
          dt.items.add(new File([bytes], f.name, { type: f.type || 'application/octet-stream' }));
        }
        fi.files = dt.files;
        fireEv(fi, 'change');
        attached = [...fi.files].map(x => x.name);
        mark(fi, '#f0ad4e');
      }
    }
    try { ta.scrollIntoView({ block: 'center' }); } catch (e) { /* ничего */ }
    return { ok: true, data: { step: 1, changed, attached, notes, domains: p.domains || [],
      untouched: ['не проверять наличие контактов и документов', 'allow_rereg_only_bill', 'сохранить ЧУ', 'расторгнуть сделку в Магазине доменов'] } };
  }

  // Шаг 2: form[name=contacts_form] (POST /manager/domain_move_contacts). Блок #base-contacts — «изменить на данные
  // нового аккаунта» (только чтение, значения в <b>), #fill-contacts — «заполнить вручную» (поля формы).
  const BASE_LABELS = [
    [/^Гражданство/, 'citizenship'], [/^Ф\.?И\.?О/, 'fio'], [/^Паспорт/, 'passport'], [/^ИНН/, 'inn'],
    [/^Дата рождения/, 'birth_date'], [/^Почтовый адрес/, 'address'], [/^Телефон/, 'phone'],
    [/^Номер sms/i, 'sms'], [/^Факс/, 'fax'], [/^Электронная почта/, 'email'],
  ];
  function baseContacts(box) {
    const out = {}, cells = {};
    if (!box) return { out, cells };
    for (const td of box.querySelectorAll('td.col1')) {
      const label = text(td.querySelector('span:last-child') || td);
      const hit = BASE_LABELS.find(([re]) => re.test(label));
      const val = td.nextElementSibling;
      if (!hit || !val) continue;
      const bs = [...val.querySelectorAll('b')];
      let v = bs.length ? text(bs[0]) : text(val);
      if (hit[1] === 'fio' && bs[1]) out.fio_en = text(bs[1]);
      v = v.replace(/\s*,\s*/g, ', ').replace(/(,\s*)+$/, '').trim();
      out[hit[1]] = v;
      cells[hit[1]] = bs[0] || val;
    }
    // «4000, 123456, ГУ МВД …, 01.02.2020» → части
    const m = /^(\d{2}\s?\d{2})\s*,\s*(\d{6})\s*,\s*(.*?)\s*,\s*(\d{2}\.\d{2}\.\d{4})(?:\s*,\s*(.*))?$/.exec(out.passport || '');
    if (m) Object.assign(out, { passport_series: m[1].replace(/\s/g, ''), passport_number: m[2], passport_place: m[3], passport_date: m[4], passport_place_id: m[5] || '' });
    return { out, cells };
  }
  const FILL_NAMES = ['person_r_surname', 'person_r_name', 'person_r_patronimic', 'passport_series', 'passport_number_short',
    'passport_place', 'passport_date', 'passport_place_id', 'code', 'birth_date', 'p_addr_zip', 'p_addr_area',
    'p_addr_district', 'p_addr_city', 'p_addr_street', 'p_addr_house', 'p_addr_building', 'p_addr_frame', 'p_addr_flat',
    'p_addr_additional', 'phone', 'e_mail'];
  function contactsForm() {
    const form = document.querySelector('form[name="contacts_form"]');
    if (!form) return null;
    return form;
  }
  function notStep2() {
    return { ok: false, error: { code: 'bad_page', message: 'Во вкладке manager не второй шаг смены администратора («данные нового администратора»). Нажмите «Далее» на первом шаге и повторите.' } };
  }
  function domainMoveRead() {
    const form = contactsForm();
    if (!form) return notStep2();
    const hidden = n => ((form.querySelector(`input[type="hidden"][name="${n}"]`) || {}).value || '').trim();
    const mode = (form.querySelector('input[name="change_contacts"]:checked') || {}).value || '';
    const { out } = baseContacts(form.querySelector('#base-contacts'));
    const fill = {};
    const fc = form.querySelector('#fill-contacts');
    if (fc) {
      for (const n of FILL_NAMES) {
        const el = fc.querySelector(`[name="${n}"]`);
        if (el && !el.classList.contains('blank')) fill[n] = (el.value || '').trim();
      }
    }
    return { ok: true, data: { step: 2, mode, base: out, fill, domains: hidden('domains'), to_user_id: hidden('to_user_id'),
      current_user_id: hidden('current_user_id'), change_account: hidden('do_change_account') === '1',
      change_admin: hidden('do_change_admin') === '1' } };
  }
  const MARK_COLOR = { ok: '#2e9d57', fail: '#d0453b', warn: '#e0a100' };
  const MARK_FILL = { fio: ['person_r_surname', 'person_r_name', 'person_r_patronimic'], email: ['e_mail'], phone: ['phone'] };
  function domainMoveMark(marks) {
    const form = contactsForm();
    if (!form) return notStep2();
    const { cells } = baseContacts(form.querySelector('#base-contacts'));
    let n = 0;
    for (const [k, st] of Object.entries(marks || {})) {
      const color = MARK_COLOR[st] || '';
      if (cells[k]) { mark(cells[k], color); cells[k].style.background = color ? `${color}22` : ''; n++; }
      for (const name of MARK_FILL[k] || []) {
        const el = form.querySelector(`#fill-contacts [name="${name}"]`);
        if (el) { mark(el, color); n++; }
      }
    }
    return { ok: true, data: { marked: n } };
  }
  function domainMoveFillContacts(fields) {
    const form = contactsForm();
    if (!form) return notStep2();
    const radio = form.querySelector('input[name="change_contacts"][value="fill"]');
    if (radio && !radio.checked) {
      radio.checked = true;
      fireEv(radio, 'change');        // страница показывает блок «заполнить вручную»
    }
    const box = form.querySelector('#fill-contacts');
    if (!box) return { ok: false, error: { code: 'bad_page', message: 'На странице нет блока «заполнить вручную».' } };
    if (box.style.display === 'none') box.style.display = '';
    const changed = [], missing = [];
    for (const name of FILL_NAMES) {
      if (!(name in (fields || {}))) continue;
      const value = String(fields[name] ?? '').trim();
      if (!value) continue;
      const el = box.querySelector(`[name="${name}"]`);
      if (!el) { missing.push(name); continue; }
      const old = el.classList.contains('blank') ? '' : el.value;
      try { el.focus({ preventScroll: true }); } catch (e) { /* ничего */ }
      fireEv(el, 'focus');              // страница убирает текст-подсказку из пустого поля
      el.classList.remove('blank');
      el.value = value;
      fireEv(el, 'input');
      fireEv(el, 'change');
      try { el.blur(); } catch (e) { /* ничего */ }
      fireEv(el, 'blur');               // у ФИО — пересчёт имени по-английски (translit_pers_org)
      mark(el, '#f0ad4e');
      changed.push({ name, old, new: value });
    }
    return { ok: true, data: { step: 2, mode: 'fill', changed, missing } };
  }

  // ------------------------------------------------------------------ безбумажные операции: доверенность (0.8.2)

  // «Документы → Доверенности → Добавить» (/userdocs/loa_add, «Добавить новый документ от пользователя»).
  // Поля, которые вписываются (только переданные непустые): passport_inn — «Номер паспорта (физ.лицо) / Код cтраны
  // регистрации компании», fullname — «ФИО Администратора / ИНН компании», email, customer_phone. Из них страница
  // собирает идентификатор администратора (паспорт:ФИО без пробелов) для «Показать домены…». Вид документа, срок
  // действия, список доменов, внутренний номер — оператор; «Добавить документ» не нажимается.
  const LOA_FIELDS = [['passport_inn', 'Номер паспорта (физ.лицо) / Код cтраны регистрации компании'],
    ['fullname', 'ФИО Администратора / ИНН компании'], ['email', 'email'], ['customer_phone', 'Телефон']];
  function loaFill(fields) {
    const pi = document.querySelector('form input[name="passport_inn"]');
    const form = pi && pi.closest('form');
    if (!form || !form.querySelector('[name="fullname"]')) {
      return { ok: false, error: { code: 'bad_page', message: 'Во вкладке не страница «Добавить новый документ от пользователя» (доверенность): нет поля «Номер паспорта / Код страны».' } };
    }
    const changed = [], missing = [];
    for (const [name, label] of LOA_FIELDS) {
      const value = String((fields || {})[name] ?? '').trim();
      if (!value) continue;
      const el = form.querySelector(`[name="${name}"]`);
      if (!el) { missing.push(label); continue; }
      const old = el.value;
      if (old !== value) {
        el.value = value;
        fireEv(el, 'input');
        fireEv(el, 'change');
        changed.push({ name, label, old, new: value });
      }
      mark(el, '#f0ad4e');
    }
    try { pi.scrollIntoView({ block: 'center' }); } catch (e) { /* ничего */ }
    const sel = form.querySelector('[name="doc_type_and_subtype"]');
    const opt = sel && sel.selectedOptions[0];
    return { ok: true, data: { changed, missing, doc_type: sel && sel.value ? (opt ? opt.textContent.trim() : sel.value) : '',
      untouched: ['Вид документа', 'Документ действителен С / ПО', 'список доменов', 'внутренний номер документа'] } };
  }

  window.__doctoolManager = {
    version: VERSION, lookup, account, fillRunic, parseBills, pickService, parseSd, parseS, parseEsiaList,
    parseUserDetails, parseRunic, setRunicType, isManagerPage,
    domainMoveFill, domainMoveRead, domainMoveMark, domainMoveFillContacts, loaFill,
  };
})();
