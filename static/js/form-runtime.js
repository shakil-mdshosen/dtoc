/* Renders a DTOC form schema for respondents (and for the builder's live preview).
 * Mirrors the server rules in form_schema.py for logic and validation. */
(function () {
  'use strict';

  const { icon, escapeHtml: esc, store } = window.DTOC;
  const INPUT_TYPES = new Set(['text', 'textarea', 'email', 'number', 'phone', 'url', 'date', 'time',
    'radio', 'checkbox', 'dropdown', 'yesno', 'scale', 'rating', 'nps', 'matrix', 'ranking']);
  const ALLOWED_DESC_TAGS = new Set(['b', 'strong', 'i', 'em', 'u', 's', 'strike', 'br', 'p', 'div', 'ul', 'ol', 'li', 'blockquote', 'a']);
  const PATTERNS = {
    email: /^[^@\s]+@[^@\s]+\.[^@\s]+$/,
    phone: /^[0-9+()\-.\s]{5,32}$/,
    url: /^https?:\/\/\S+$/i,
  };

  function sanitizeUrl(url, allowMailTo, allowDataImage) {
    if (!url) return '';
    const trimmed = String(url).trim();
    if (allowDataImage && /^data:(image\/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=]+)$/i.test(trimmed)) return trimmed;
    try {
      const parsed = new URL(trimmed);
      const protocols = allowMailTo ? ['http:', 'https:', 'mailto:'] : ['http:', 'https:'];
      return protocols.includes(parsed.protocol) ? trimmed : '';
    } catch (_) {
      return '';
    }
  }

  function sanitizeHtml(html) {
    const template = document.createElement('template');
    template.innerHTML = html || '';
    const clean = (node) => {
      if (node.nodeType === Node.TEXT_NODE) return;
      if (node.nodeType !== Node.ELEMENT_NODE) { node.remove(); return; }
      const tag = node.tagName.toLowerCase();
      [...node.childNodes].forEach(clean);
      if (!ALLOWED_DESC_TAGS.has(tag)) {
        const frag = document.createDocumentFragment();
        while (node.firstChild) frag.appendChild(node.firstChild);
        node.replaceWith(frag);
        return;
      }
      const href = tag === 'a' ? node.getAttribute('href') : '';
      [...node.attributes].forEach(a => node.removeAttribute(a.name));
      if (tag === 'a') {
        const safe = sanitizeUrl(href, true);
        if (safe) {
          node.setAttribute('href', safe);
          node.setAttribute('target', '_blank');
          node.setAttribute('rel', 'noopener noreferrer nofollow');
        }
      }
    };
    [...template.content.childNodes].forEach(clean);
    return template.innerHTML.trim();
  }

  function isEmpty(v) {
    if (v == null) return true;
    if (typeof v === 'string') return v.trim() === '';
    if (Array.isArray(v)) return v.length === 0;
    if (typeof v === 'object') return Object.keys(v).length === 0;
    return false;
  }

  function asTextList(v) {
    if (Array.isArray(v)) return v.map(String);
    if (v && typeof v === 'object') return Object.values(v).map(String);
    if (v == null) return [];
    return [String(v)];
  }

  function logicMatches(logic, answers) {
    if (!logic) return true;
    const value = answers[logic.field];
    const target = String(logic.value || '').trim().toLowerCase();
    switch (logic.op) {
      case 'answered': return !isEmpty(value);
      case 'not_answered': return isEmpty(value);
      case 'equals': return asTextList(value).map(s => s.trim().toLowerCase()).includes(target);
      case 'not_equals': return !asTextList(value).map(s => s.trim().toLowerCase()).includes(target);
      case 'contains': return asTextList(value).some(s => s.toLowerCase().includes(target));
      case 'gt': case 'lt': {
        const left = parseFloat(asTextList(value)[0]);
        const right = parseFloat(target);
        if (Number.isNaN(left) || Number.isNaN(right)) return false;
        return logic.op === 'gt' ? left > right : left < right;
      }
      default: return true;
    }
  }

  function shuffled(list) {
    const copy = list.slice();
    for (let i = copy.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [copy[i], copy[j]] = [copy[j], copy[i]];
    }
    return copy;
  }

  /** Validate one answer. Returns an error message or ''. */
  function validate(field, value) {
    if (isEmpty(value)) return field.required ? 'This question is required.' : '';
    const t = field.type;
    if (t === 'text' || t === 'textarea') {
      const len = String(value).trim().length;
      if (field.min_length && len < field.min_length) return `Must be at least ${field.min_length} characters.`;
      if (field.max_length && len > field.max_length) return `Must be at most ${field.max_length} characters.`;
    }
    if (PATTERNS[t] && !PATTERNS[t].test(String(value).trim())) {
      return { email: 'Please enter a valid email address.', phone: 'Please enter a valid phone number.', url: 'Please enter a full URL starting with https://' }[t];
    }
    if (t === 'number') {
      const n = Number(value);
      if (Number.isNaN(n)) return 'Please enter a number.';
      if (field.min != null && n < field.min) return `Must be at least ${field.min}.`;
      if (field.max != null && n > field.max) return `Must be at most ${field.max}.`;
    }
    if (t === 'checkbox') {
      if (field.min_select && value.length < field.min_select) return `Select at least ${field.min_select}.`;
      if (field.max_select && value.length > field.max_select) return `Select at most ${field.max_select}.`;
    }
    if (t === 'matrix' && field.required && Object.keys(value).length < (field.rows || []).length) return 'Please answer every row.';
    return '';
  }

  // ---------------------------------------------------------------------------

  function create(root, schema, opts) {
    opts = opts || {};
    const fields = (schema.fields || []).filter(f => f && f.type);
    const settings = schema.settings || {};
    const draftKey = opts.draftKey || null;
    let answers = {};
    const touchedRank = new Set();

    if (draftKey) {
      try {
        const saved = JSON.parse(store.get(draftKey) || 'null');
        if (saved && typeof saved === 'object') answers = saved.answers || {};
        (saved && saved.ranked || []).forEach(id => touchedRank.add(id));
      } catch (_) { answers = {}; }
    }
    const restored = draftKey && Object.keys(answers).length > 0;

    // Split into pages at each section.
    const pages = [{ section: null, fields: [] }];
    fields.forEach(f => {
      if (f.type === 'section') {
        if (pages[pages.length - 1].fields.length || pages[pages.length - 1].section) pages.push({ section: f, fields: [] });
        else pages[pages.length - 1].section = f;
      } else {
        pages[pages.length - 1].fields.push(f);
      }
    });
    let current = 0;

    root.innerHTML = '';
    root.classList.add('fv-runtime');

    const progress = document.createElement('div');
    progress.className = 'progress';
    progress.innerHTML = `<div class="progress-label"><span class="pl-left"></span><span class="pl-right"></span></div>
      <div class="progress-track"><div class="progress-bar"></div></div>`;
    if (settings.show_progress === false) progress.classList.add('hidden');
    root.appendChild(progress);

    if (restored) {
      const note = document.createElement('div');
      note.className = 'alert alert-info';
      note.style.marginTop = '14px';
      note.innerHTML = `${icon('info')}<div>We restored the answers you saved earlier on this device.</div>
        <button type="button" class="btn btn-sm btn-ghost" style="margin-left:auto" data-clear-draft>Start over</button>`;
      note.querySelector('[data-clear-draft]').addEventListener('click', () => {
        store.remove(draftKey);
        answers = {};
        touchedRank.clear();
        create(root, schema, opts);
      });
      root.appendChild(note);
    }

    const pageEls = pages.map((page, index) => {
      const el = document.createElement('div');
      el.className = 'fv-page';
      el.dataset.page = index;
      if (page.section) el.appendChild(renderSection(page.section));
      page.fields.forEach(f => el.appendChild(f.type === 'statement' ? renderStatement(f) : renderQuestion(f)));
      root.appendChild(el);
      return el;
    });

    const nav = document.createElement('div');
    nav.className = 'fv-nav';
    nav.innerHTML = `
      <button type="button" class="btn btn-lg" data-prev>${icon('left')}Back</button>
      <button type="button" class="btn btn-primary btn-lg" data-next>Next${icon('right')}</button>
      <button type="button" class="btn btn-primary btn-lg" data-submit>${icon('check')}Submit</button>
      <span class="spacer"></span>
      ${draftKey ? `<span class="fv-autosave">${icon('check', 'icon-sm')}<span>Answers saved on this device</span></span>` : ''}
      <button type="button" class="btn btn-ghost" data-clear>Clear form</button>`;
    root.appendChild(nav);
    nav.querySelector('[data-prev]').addEventListener('click', () => go(-1));
    nav.querySelector('[data-next]').addEventListener('click', () => { if (validatePage(current)) go(1); });
    nav.querySelector('[data-submit]').addEventListener('click', submit);
    nav.querySelector('[data-clear]').addEventListener('click', () => {
      if (!window.confirm('Clear all your answers?')) return;
      if (draftKey) store.remove(draftKey);
      answers = {};
      touchedRank.clear();
      create(root, schema, opts);
    });

    refresh();
    show(0, false);

    // ----- rendering -----

    function renderSection(f) {
      const el = document.createElement('div');
      el.className = 'card q-section';
      el.dataset.fieldId = f.id;
      el.innerHTML = `<h2>${esc(f.label || 'Untitled section')}</h2>${f.description ? `<p class="q-desc">${esc(f.description)}</p>` : ''}`;
      return el;
    }

    function renderStatement(f) {
      const el = document.createElement('div');
      el.className = 'card q q-statement';
      el.dataset.fieldId = f.id;
      el.innerHTML = `<div class="q-label">${icon('info')}<span>${esc(f.label)}</span></div>${f.description ? `<p class="q-desc">${esc(f.description)}</p>` : ''}`;
      return el;
    }

    function renderQuestion(f) {
      const el = document.createElement('div');
      el.className = 'card q';
      el.dataset.fieldId = f.id;
      const labelId = `lbl_${f.id}`;
      el.innerHTML = `
        <label class="q-label" id="${labelId}" for="in_${esc(f.id)}"><span class="q-num"></span>${esc(f.label || 'Untitled question')}${f.required ? ' <span class="req" aria-label="required">*</span>' : ''}</label>
        ${f.description ? `<p class="q-desc">${esc(f.description)}</p>` : ''}
        <div class="q-control"></div>
        <div class="q-error" role="alert">${icon('alert', 'icon-sm')}<span></span></div>`;
      const control = el.querySelector('.q-control');
      buildControl(f, control, labelId);
      return el;
    }

    function setAnswer(id, value) {
      if (isEmpty(value)) delete answers[id];
      else answers[id] = value;
      const q = root.querySelector(`[data-field-id="${CSS.escape(id)}"]`);
      if (q && q.classList.contains('has-error')) {
        const f = fields.find(x => x.id === id);
        const msg = validate(f, answers[id]);
        if (!msg) q.classList.remove('has-error');
      }
      refresh();
      saveDraft();
    }

    function buildControl(f, control, labelId) {
      const value = answers[f.id];
      const name = `q_${f.id}`;
      const ph = f.placeholder ? esc(f.placeholder) : 'Your answer';
      const t = f.type;

      if (['text', 'email', 'number', 'phone', 'url', 'date', 'time'].includes(t)) {
        const type = { text: 'text', email: 'email', number: 'number', phone: 'tel', url: 'url', date: 'date', time: 'time' }[t];
        const extra = t === 'number' ? `${f.min != null ? `min="${f.min}"` : ''} ${f.max != null ? `max="${f.max}"` : ''} step="any"` : '';
        const ac = { email: 'email', phone: 'tel', url: 'url' }[t] || 'off';
        control.innerHTML = `<input id="in_${esc(f.id)}" class="input" type="${type}" ${extra} autocomplete="${ac}"
          placeholder="${t === 'url' ? 'https://' : ph}" ${f.max_length ? `maxlength="${f.max_length}"` : ''} style="max-width:${t === 'date' || t === 'time' || t === 'number' ? '240px' : '100%'}">`;
        const input = control.querySelector('input');
        input.value = value == null ? '' : value;
        input.addEventListener('input', () => setAnswer(f.id, input.value));
        return;
      }
      if (t === 'textarea') {
        control.innerHTML = `<textarea id="in_${esc(f.id)}" class="textarea" rows="4" placeholder="${ph}" ${f.max_length ? `maxlength="${f.max_length}"` : ''}></textarea>
          ${f.max_length ? '<div class="q-counter"></div>' : ''}`;
        const area = control.querySelector('textarea');
        const counter = control.querySelector('.q-counter');
        area.value = value || '';
        const count = () => { if (counter) counter.textContent = `${area.value.length} / ${f.max_length}`; };
        count();
        area.addEventListener('input', () => { count(); setAnswer(f.id, area.value); });
        return;
      }
      if (t === 'dropdown') {
        const options = f.shuffle ? shuffled(f.options || []) : (f.options || []);
        control.innerHTML = `<select id="in_${esc(f.id)}" class="select" style="max-width:420px"><option value="">Choose…</option>
          ${options.map(o => `<option value="${esc(o)}">${esc(o)}</option>`).join('')}</select>`;
        const select = control.querySelector('select');
        select.value = value || '';
        select.addEventListener('change', () => setAnswer(f.id, select.value));
        return;
      }
      if (t === 'radio' || t === 'checkbox' || t === 'yesno') {
        const multi = t === 'checkbox';
        const options = t === 'yesno' ? ['Yes', 'No'] : (f.shuffle ? shuffled(f.options || []) : (f.options || []));
        const chosen = multi ? (Array.isArray(value) ? value : []) : (value == null ? [] : [value]);
        const otherValue = chosen.find(c => !options.includes(c)) || '';
        const type = multi ? 'checkbox' : 'radio';
        control.innerHTML = `<div class="${t === 'yesno' ? 'yesno' : 'choice-list'}" role="${multi ? 'group' : 'radiogroup'}" aria-labelledby="${labelId}">
          ${options.map((o, i) => `<label class="choice"><input type="${type}" name="${esc(name)}" value="${esc(o)}" ${i === 0 ? `id="in_${esc(f.id)}"` : ''} ${chosen.includes(o) ? 'checked' : ''}><span>${esc(o)}</span></label>`).join('')}
          ${f.allow_other ? `<label class="choice choice-other"><input type="${type}" name="${esc(name)}" value="__other__" ${otherValue ? 'checked' : ''}><span>Other:</span>
            <input type="text" class="input" data-other placeholder="Please specify" value="${esc(otherValue)}"></label>` : ''}
        </div>`;
        const collect = () => {
          const other = control.querySelector('[data-other]');
          const values = [...control.querySelectorAll(`input[type="${type}"]:checked`)].map(inp =>
            inp.value === '__other__' ? (other ? other.value.trim() : '') : inp.value).filter(Boolean);
          setAnswer(f.id, multi ? values : (values[0] || ''));
        };
        control.addEventListener('change', collect);
        const other = control.querySelector('[data-other]');
        if (other) other.addEventListener('input', () => {
          const box = control.querySelector('input[value="__other__"]');
          if (other.value && !box.checked) box.checked = true;
          collect();
        });
        return;
      }
      if (t === 'scale' || t === 'nps') {
        const lo = t === 'nps' ? 0 : (f.min == null ? 1 : f.min);
        const hi = t === 'nps' ? 10 : (f.max || 5);
        let buttons = '';
        for (let i = lo; i <= hi; i++) buttons += `<button type="button" class="scale-btn" data-v="${i}" aria-pressed="${value === i}">${i}</button>`;
        control.innerHTML = `<div class="scale ${t === 'nps' ? 'scale-nps' : ''}" role="group" aria-labelledby="${labelId}" id="in_${esc(f.id)}">${buttons}</div>
          ${(f.min_label || f.max_label) ? `<div class="scale-labels"><span>${esc(f.min_label || '')}</span><span>${esc(f.max_label || '')}</span></div>` : ''}`;
        control.addEventListener('click', (e) => {
          const btn = e.target.closest('.scale-btn');
          if (!btn) return;
          const v = Number(btn.dataset.v);
          const next = answers[f.id] === v ? null : v;
          control.querySelectorAll('.scale-btn').forEach(b => b.setAttribute('aria-pressed', String(Number(b.dataset.v) === next)));
          setAnswer(f.id, next);
        });
        return;
      }
      if (t === 'rating') {
        const max = f.max || 5;
        let stars = '';
        for (let i = 1; i <= max; i++) stars += `<button type="button" class="star-btn" data-v="${i}" aria-label="${i} of ${max}">${icon('star')}</button>`;
        control.innerHTML = `<div class="stars" role="group" aria-labelledby="${labelId}" id="in_${esc(f.id)}">${stars}</div>`;
        const paint = (n) => control.querySelectorAll('.star-btn').forEach(b => {
          b.classList.toggle('on', Number(b.dataset.v) <= n);
          b.setAttribute('aria-pressed', String(Number(b.dataset.v) === n));
        });
        paint(value || 0);
        control.addEventListener('click', (e) => {
          const btn = e.target.closest('.star-btn');
          if (!btn) return;
          const v = Number(btn.dataset.v);
          const next = answers[f.id] === v ? null : v;
          paint(next || 0);
          setAnswer(f.id, next);
        });
        control.addEventListener('mouseover', (e) => { const b = e.target.closest('.star-btn'); if (b) paint(Number(b.dataset.v)); });
        control.addEventListener('mouseleave', () => paint(answers[f.id] || 0));
        return;
      }
      if (t === 'matrix') {
        const rows = f.rows || [];
        const cols = f.columns || [];
        const current = value && typeof value === 'object' ? value : {};
        control.innerHTML = `<div class="table-wrap"><table class="matrix" aria-labelledby="${labelId}">
          <thead><tr><th></th>${cols.map(c => `<th scope="col">${esc(c)}</th>`).join('')}</tr></thead>
          <tbody>${rows.map((r, ri) => `<tr><td>${esc(r)}</td>${cols.map((c, ci) => `<td><input type="radio" name="${esc(name)}_${ri}" value="${esc(c)}" aria-label="${esc(r)}: ${esc(c)}" ${ri === 0 && ci === 0 ? `id="in_${esc(f.id)}"` : ''} ${current[r] === c ? 'checked' : ''}></td>`).join('')}</tr>`).join('')}</tbody>
        </table></div>`;
        control.addEventListener('change', () => {
          const out = {};
          rows.forEach((r, ri) => {
            const hit = control.querySelector(`input[name="${CSS.escape(name + '_' + ri)}"]:checked`);
            if (hit) out[r] = hit.value;
          });
          setAnswer(f.id, out);
        });
        return;
      }
      if (t === 'ranking') {
        let order = Array.isArray(value) && value.length ? value.slice() : (f.shuffle ? shuffled(f.options || []) : (f.options || []).slice());
        const draw = () => {
          control.innerHTML = `<p class="hint" style="margin:0 0 10px">Drag items or use the arrows to put them in order (1 = top choice).</p>
            <ol class="rank-list" id="in_${esc(f.id)}" aria-labelledby="${labelId}">${order.map((o, i) => `
              <li class="rank-item" draggable="true" data-i="${i}"><span class="rank-pos">${i + 1}</span>${icon('drag', 'icon-sm')}
                <span class="rank-label">${esc(o)}</span>
                <button type="button" class="btn btn-ghost btn-icon" data-move="-1" aria-label="Move up" ${i === 0 ? 'disabled' : ''}>${icon('up', 'icon-sm')}</button>
                <button type="button" class="btn btn-ghost btn-icon" data-move="1" aria-label="Move down" ${i === order.length - 1 ? 'disabled' : ''}>${icon('down', 'icon-sm')}</button>
              </li>`).join('')}</ol>
            ${touchedRank.has(f.id) ? '' : '<button type="button" class="btn btn-sm btn-soft" data-keep style="margin-top:10px">Keep this order</button>'}`;
        };
        const commit = () => { touchedRank.add(f.id); draw(); setAnswer(f.id, order.slice()); };
        draw();
        let dragFrom = null;
        control.addEventListener('click', (e) => {
          if (e.target.closest('[data-keep]')) { commit(); return; }
          const btn = e.target.closest('[data-move]');
          if (!btn) return;
          const i = Number(btn.closest('.rank-item').dataset.i);
          const j = i + Number(btn.dataset.move);
          [order[i], order[j]] = [order[j], order[i]];
          commit();
        });
        control.addEventListener('dragstart', (e) => {
          const item = e.target.closest('.rank-item');
          if (!item) return;
          dragFrom = Number(item.dataset.i);
          item.classList.add('dragging');
          e.dataTransfer.effectAllowed = 'move';
        });
        control.addEventListener('dragover', (e) => { if (dragFrom != null) e.preventDefault(); });
        control.addEventListener('drop', (e) => {
          const item = e.target.closest('.rank-item');
          if (!item || dragFrom == null) return;
          e.preventDefault();
          const to = Number(item.dataset.i);
          const [moved] = order.splice(dragFrom, 1);
          order.splice(to, 0, moved);
          dragFrom = null;
          commit();
        });
        control.addEventListener('dragend', () => { dragFrom = null; control.querySelectorAll('.dragging').forEach(x => x.classList.remove('dragging')); });
        if (touchedRank.has(f.id) && isEmpty(answers[f.id])) answers[f.id] = order.slice();
      }
    }

    // ----- state -----

    function visibleIds() {
      const visible = new Set();
      let sectionVisible = true;
      fields.forEach(f => {
        if (f.type === 'section') {
          sectionVisible = logicMatches(f.logic, answers);
          if (sectionVisible) visible.add(f.id);
          return;
        }
        if (sectionVisible && logicMatches(f.logic, answers)) visible.add(f.id);
      });
      return visible;
    }

    function pageVisible(index, visible) {
      const page = pages[index];
      if (page.section && !visible.has(page.section.id)) return false;
      return !page.section ? page.fields.length > 0 || pages.length === 1 : true;
    }

    function visiblePages() {
      const visible = visibleIds();
      return pages.map((_, i) => i).filter(i => pageVisible(i, visible));
    }

    function refresh() {
      const visible = visibleIds();
      let number = 0;
      fields.forEach(f => {
        const el = root.querySelector(`[data-field-id="${CSS.escape(f.id)}"]`);
        if (!el) return;
        el.classList.toggle('hidden', !visible.has(f.id));
        if (INPUT_TYPES.has(f.type)) {
          const num = el.querySelector('.q-num');
          if (visible.has(f.id)) number++;
          if (num) num.textContent = settings.show_question_numbers ? `${number}.` : '';
        }
      });
      updateProgress();
    }

    function updateProgress() {
      const vp = visiblePages();
      const pos = Math.max(0, vp.indexOf(current));
      const visible = visibleIds();
      const qs = fields.filter(f => INPUT_TYPES.has(f.type) && visible.has(f.id));
      const answered = qs.filter(f => !isEmpty(answers[f.id])).length;
      const pct = vp.length > 1 ? Math.round(((pos + (answered === qs.length ? 1 : 0.5)) / vp.length) * 100)
        : (qs.length ? Math.round(answered * 100 / qs.length) : 0);
      progress.querySelector('.progress-bar').style.width = `${Math.min(100, pct)}%`;
      progress.querySelector('.pl-left').textContent = vp.length > 1 ? `Page ${pos + 1} of ${vp.length}` : `${answered} of ${qs.length} answered`;
      progress.querySelector('.pl-right').textContent = `${Math.min(100, pct)}%`;
    }

    function show(index, scroll) {
      current = index;
      pageEls.forEach((el, i) => el.classList.toggle('hidden', i !== index));
      const vp = visiblePages();
      const pos = vp.indexOf(index);
      nav.querySelector('[data-prev]').classList.toggle('hidden', pos <= 0);
      const last = pos === vp.length - 1;
      nav.querySelector('[data-next]').classList.toggle('hidden', last);
      nav.querySelector('[data-submit]').classList.toggle('hidden', !last);
      updateProgress();
      if (scroll !== false) root.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function go(delta) {
      const vp = visiblePages();
      const pos = vp.indexOf(current);
      const next = vp[pos + delta];
      if (next != null) show(next);
    }

    function showError(id, message) {
      const q = root.querySelector(`[data-field-id="${CSS.escape(id)}"]`);
      if (!q) return;
      q.classList.add('has-error');
      q.querySelector('.q-error span').textContent = message;
    }

    function validatePage(index) {
      const visible = visibleIds();
      let first = null;
      pages[index].fields.forEach(f => {
        if (!INPUT_TYPES.has(f.type) || !visible.has(f.id)) return;
        const msg = validate(f, answers[f.id]);
        const q = root.querySelector(`[data-field-id="${CSS.escape(f.id)}"]`);
        if (msg) { showError(f.id, msg); if (!first) first = q; } else if (q) q.classList.remove('has-error');
      });
      if (first) {
        first.scrollIntoView({ behavior: 'smooth', block: 'center' });
        const focusable = first.querySelector('input, textarea, select, button');
        if (focusable) setTimeout(() => focusable.focus({ preventScroll: true }), 300);
      }
      return !first;
    }

    function saveDraft() {
      if (!draftKey) return;
      store.set(draftKey, JSON.stringify({ answers, ranked: [...touchedRank], saved: Date.now() }));
    }

    function payload() {
      const visible = visibleIds();
      const out = {};
      fields.forEach(f => {
        if (INPUT_TYPES.has(f.type) && visible.has(f.id) && !isEmpty(answers[f.id])) out[f.id] = answers[f.id];
      });
      return out;
    }

    async function submit() {
      const vp = visiblePages();
      for (const i of vp) {
        if (!validatePage(i)) { if (i !== current) { show(i, false); validatePage(i); } return; }
      }
      if (opts.preview) { done(); return; }

      const btn = nav.querySelector('[data-submit]');
      btn.disabled = true;
      btn.innerHTML = `${icon('clock')}Submitting…`;
      try {
        const res = await fetch(opts.submitUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ answers: payload() }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          if (draftKey) store.remove(draftKey);
          done();
          return;
        }
        if (res.status === 401 && data.login_url) {
          // Session expired: answers are kept in the draft, so log in and come back.
          saveDraft();
          window.DTOC.toast(data.message || 'Please log in again.', 'alert');
          setTimeout(() => { window.location.href = data.login_url; }, 1200);
          return;
        }
        if (data.errors) {
          Object.entries(data.errors).forEach(([id, msg]) => showError(id, msg));
          const firstId = Object.keys(data.errors)[0];
          const pageIndex = pages.findIndex(p => p.fields.some(f => f.id === firstId));
          if (pageIndex >= 0) show(pageIndex, false);
          const el = root.querySelector(`[data-field-id="${CSS.escape(firstId)}"]`);
          if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        window.DTOC.toast(data.message || 'Could not submit the form.', 'alert');
      } catch (_) {
        window.DTOC.toast('Network error. Your answers are saved, please try again.', 'alert');
      }
      btn.disabled = false;
      btn.innerHTML = `${icon('check')}Submit`;
    }

    function done() {
      const message = settings.confirmation_message || 'Your response has been recorded. Thank you!';
      const target = opts.doneTarget || root;
      target.innerHTML = `<div class="card fv-done" style="margin-top:14px">
        <div class="fv-done-icon">${icon('check')}</div>
        <h2 style="font-size:24px">Thank you!</h2>
        <p class="muted" style="margin:10px auto 24px;max-width:480px;white-space:pre-line">${esc(message)}</p>
        ${opts.preview ? '<button type="button" class="btn" data-restart>Restart preview</button>'
          : (settings.one_response_per_user ? '' : '<button type="button" class="btn" data-restart>Submit another response</button>')}
      </div>`;
      const restart = target.querySelector('[data-restart]');
      if (restart) restart.addEventListener('click', () => (opts.preview ? opts.onRestart && opts.onRestart() : window.location.reload()));
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    return { answers: () => answers };
  }

  window.DTOCForm = { create, sanitizeHtml, sanitizeUrl, validate, logicMatches, INPUT_TYPES };
})();
