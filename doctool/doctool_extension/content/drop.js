/* PySimpleManager (0.8.0): файлы дела, перетащенные из рабочего места программы на страницу сайта —
 * проверка ЭП на Госуслугах (e-trust.gosuslugi.ru), Контур.Крипто, Nextcloud.
 *
 * Сами файлы Chrome между страницами не передаёт: со страницы программы приходит только ссылка на файл дела
 * (тип данных application/x-pysm-file или текст «pysm-file:{…}»). Скрипт просит фоновую часть расширения взять
 * файл у программы на этом компьютере и отдаёт его странице так, как если бы его перетащили из папки: событием drop
 * на то же место, а если страница его не приняла — в ближайшее поле выбора файла. Ничего не отправляет и кнопок
 * не нажимает. Чужие перетаскивания (файлы из папки, текст, ссылки сайта) не трогает.
 */
(() => {
  if (window.__pysmDrop) return;
  window.__pysmDrop = true;
  const MARK = 'application/x-pysm-file';

  // во время перетаскивания данные не читаются — видны только типы
  const looksOurs = dt => {
    const t = [...((dt && dt.types) || [])];
    return t.includes(MARK) || (t.includes('text/plain') && t.includes('text/uri-list') && !t.includes('Files'));
  };
  function fileInfo(dt) {
    try { const raw = dt.getData(MARK); if (raw) return JSON.parse(raw); } catch { /* нет такого типа */ }
    const t = (dt.getData('text/plain') || '').trim();
    if (t.startsWith('pysm-file:')) { try { return JSON.parse(t.slice(10)); } catch { /* не наше */ } }
    return null;
  }

  let note = null, noteT = 0;
  function say(text, bad, ms = 5000) {
    if (!note) {
      note = document.createElement('div');
      note.style.cssText = 'position:fixed;left:50%;top:12px;transform:translateX(-50%);z-index:2147483647;padding:8px 14px;'
        + 'border-radius:8px;font:13px/1.4 "Segoe UI",Arial,sans-serif;box-shadow:0 4px 18px rgba(0,0,0,.25);pointer-events:none;max-width:80vw';
      (document.body || document.documentElement).appendChild(note);
    }
    note.textContent = 'PySimpleManager: ' + text;
    note.style.background = bad ? '#fde4e2' : '#1d1f23';
    note.style.color = bad ? '#9b1c1c' : '#fff';
    note.style.display = 'block';
    clearTimeout(noteT);
    if (ms) noteT = setTimeout(() => { note.style.display = 'none'; }, ms);
  }

  function nearestFileInput(el, name) {
    for (let n = el; n && n !== document; n = n.parentElement) {
      const x = n.querySelector && n.querySelector('input[type="file"]');
      if (x) return x;
    }
    const all = [...document.querySelectorAll('input[type="file"]')];
    if (all.length <= 1) return all[0] || null;
    const ext = (name.split('.').pop() || '').toLowerCase();
    return all.find(x => (x.accept || '').toLowerCase().split(',').some(a => a.trim() === '.' + ext)) || all[0];
  }

  function deliver(file, target, x, y) {
    const dt = new DataTransfer();
    dt.items.add(file);
    const opts = { bubbles: true, cancelable: true, composed: true, clientX: x, clientY: y, dataTransfer: dt };
    const el = (target && target.isConnected) ? target : (document.elementFromPoint(x, y) || document.body);
    el.dispatchEvent(new DragEvent('dragenter', opts));
    el.dispatchEvent(new DragEvent('dragover', opts));
    const taken = !el.dispatchEvent(new DragEvent('drop', opts));   // страница вызвала preventDefault — файл принят
    if (taken) return 'передан странице';
    const input = nearestFileInput(el, file.name);
    if (!input) return '';
    const d2 = new DataTransfer();
    if (input.multiple) for (const f of input.files || []) d2.items.add(f);
    d2.items.add(file);
    input.files = d2.files;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return 'положен в поле выбора файла';
  }

  addEventListener('dragover', e => {
    if (!e.isTrusted || !looksOurs(e.dataTransfer)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    say('отпустите — файл дела будет положен на страницу', false, 1500);
  }, true);

  addEventListener('drop', async e => {
    if (!e.isTrusted) return;
    const f = fileInfo(e.dataTransfer);
    if (!f || !f.url) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    const target = e.target, x = e.clientX, y = e.clientY;
    say(`беру файл «${f.name || ''}» у программы…`, false, 0);
    let r;
    try { r = await chrome.runtime.sendMessage({ type: 'site_file', url: f.url, name: f.name || '' }); }
    catch (err) { r = { ok: false, message: err.message || String(err) }; }
    if (!r || !r.ok) { say('файл не получен: ' + ((r && r.message) || 'расширение не ответило'), true, 8000); return; }
    const bin = atob(r.b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const file = new File([bytes], f.name || 'file', { type: f.mime || r.type || 'application/octet-stream' });
    const how = deliver(file, target, x, y);
    if (how) say(`«${file.name}» ${how}`, false, 4000);
    else say(`страница не приняла «${file.name}» — выберите файл на странице вручную`, true, 8000);
  }, true);
})();
