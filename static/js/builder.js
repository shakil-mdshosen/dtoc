/* DTOC form builder: question palette, drag-and-drop editing, logic, settings and live preview. */
(function () {
  'use strict';

  const { icon, escapeHtml: esc, toast } = window.DTOC;
  const { sanitizeHtml, sanitizeUrl, INPUT_TYPES } = window.DTOCForm;

  const TYPES = {
    text:      { label: 'Short answer', icon: 'text', group: 'Text' },
    textarea:  { label: 'Paragraph', icon: 'paragraph', group: 'Text' },
    email:     { label: 'Email', icon: 'mail', group: 'Text' },
    number:    { label: 'Number', icon: 'hash', group: 'Text' },
    phone:     { label: 'Phone', icon: 'phone', group: 'Text' },
    url:       { label: 'Website / URL', icon: 'globe', group: 'Text' },
    radio:     { label: 'Multiple choice', icon: 'radio', group: 'Choice' },
    checkbox:  { label: 'Checkboxes', icon: 'checkbox', group: 'Choice' },
    dropdown:  { label: 'Dropdown', icon: 'dropdown', group: 'Choice' },
    yesno:     { label: 'Yes / No', icon: 'toggle', group: 'Choice' },
    scale:     { label: 'Linear scale', icon: 'scale', group: 'Scales & grids' },
    rating:    { label: 'Star rating', icon: 'star', group: 'Scales & grids' },
    nps:       { label: 'Net Promoter Score', icon: 'gauge', group: 'Scales & grids' },
    matrix:    { label: 'Matrix / Likert', icon: 'table', group: 'Scales & grids' },
    ranking:   { label: 'Ranking', icon: 'rank', group: 'Scales & grids' },
    date:      { label: 'Date', icon: 'calendar', group: 'Date & time' },
    time:      { label: 'Time', icon: 'clock', group: 'Date & time' },
    section:   { label: 'Section / page break', icon: 'section', group: 'Layout' },
    statement: { label: 'Text block', icon: 'info', group: 'Layout' },
  };
  const OPTION_TYPES = new Set(['radio', 'checkbox', 'dropdown', 'ranking']);
  const THEMES = { indigo: '#4f46e5', violet: '#7c3aed', blue: '#2563eb', teal: '#0d9488', green: '#16a34a', amber: '#d97706', rose: '#e11d48', slate: '#475569' };
  const OPS = [
    ['equals', 'is'], ['not_equals', 'is not'], ['contains', 'contains'],
    ['gt', 'is greater than'], ['lt', 'is less than'], ['answered', 'is answered'], ['not_answered', 'is not answered'],
  ];
  const HEADER_MAX_BYTES = 2 * 1024 * 1024;
  const HEADER_TYPES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);

  const uid = () => 'f_' + Math.random().toString(36).slice(2, 10);

  function newField(type) {
    const f = { id: uid(), type, label: '', description: '', required: false };
    const defaults = {
      text: { label: 'Short answer question' },
      textarea: { label: 'Tell us more' },
      email: { label: 'Email address' },
      number: { label: 'Number' },
      phone: { label: 'Phone number' },
      url: { label: 'Website' },
      date: { label: 'Date' },
      time: { label: 'Time' },
      radio: { label: 'Multiple choice question', options: ['Option 1', 'Option 2'] },
      checkbox: { label: 'Select all that apply', options: ['Option 1', 'Option 2'] },
      dropdown: { label: 'Choose one', options: ['Option 1', 'Option 2', 'Option 3'] },
      yesno: { label: 'Yes or no?' },
      scale: { label: 'How would you rate this?', min: 1, max: 5, min_label: 'Poor', max_label: 'Excellent' },
      rating: { label: 'Rate your experience', max: 5 },
      nps: { label: 'How likely are you to recommend this to a friend or colleague?', min_label: 'Not at all likely', max_label: 'Extremely likely' },
      matrix: { label: 'Please rate the following', rows: ['Statement 1', 'Statement 2'], columns: ['Disagree', 'Neutral', 'Agree'] },
      ranking: { label: 'Rank these in order of priority', options: ['Item 1', 'Item 2', 'Item 3'] },
      section: { label: 'New section' },
      statement: { label: 'Add some information for respondents here.' },
    };
    Object.assign(f, defaults[type] || {});
    if (!INPUT_TYPES.has(type)) delete f.required;
    return f;
  }

  // ---------------------------------------------------------------------------
  // Templates

  const T = (type, extra) => Object.assign(newField(type), extra || {});
  const TEMPLATES = [
    { key: 'blank', name: 'Blank form', icon: 'plus', desc: 'Start from scratch.', build: () => ({ title: '', fields: [T('text', { label: 'Untitled question' })] }) },
    {
      key: 'event', name: 'Event registration', icon: 'calendar', color: 'blue', desc: 'Sign-ups for meetups, edit-a-thons and conferences.',
      build: () => {
        const mode = T('radio', { label: 'How will you attend?', required: true, options: ['In person', 'Online'] });
        return {
          title: 'Event registration', description_html: '<p>Please register so we can plan venue, food and materials.</p>',
          settings: { theme_color: 'blue', one_response_per_user: true },
          fields: [
            T('text', { label: 'Full name', required: true }),
            T('email', { label: 'Email address', required: true, description: 'We will only use this to send event updates.' }),
            T('dropdown', { label: 'Affiliation', options: ['Wikimedia chapter', 'User group', 'Independent volunteer', 'Other'] }),
            mode,
            T('checkbox', { label: 'Dietary requirements', options: ['Vegetarian', 'Vegan', 'Halal', 'Gluten free'], allow_other: true, logic: { field: mode.id, op: 'equals', value: 'In person' } }),
            T('dropdown', { label: 'T-shirt size', options: ['S', 'M', 'L', 'XL', 'XXL'], logic: { field: mode.id, op: 'equals', value: 'In person' } }),
            T('yesno', { label: 'May we take photos of you during the event?', required: true }),
          ],
        };
      },
    },
    {
      key: 'feedback', name: 'Feedback survey', icon: 'star', color: 'amber', desc: 'Ratings, NPS and a Likert grid with follow-up logic.',
      build: () => {
        const overall = T('rating', { label: 'Overall, how would you rate the event?', required: true });
        return {
          title: 'Event feedback', description_html: '<p>Thanks for joining us! Your feedback helps us improve future events.</p>',
          settings: { theme_color: 'amber', show_question_numbers: true },
          fields: [
            overall,
            T('textarea', { label: 'Sorry to hear that. What went wrong?', logic: { field: overall.id, op: 'lt', value: '3' } }),
            T('matrix', { label: 'How satisfied were you with…', rows: ['Content', 'Speakers', 'Organisation', 'Venue'], columns: ['Very dissatisfied', 'Dissatisfied', 'Neutral', 'Satisfied', 'Very satisfied'] }),
            T('nps', { label: 'How likely are you to recommend our events to other Wikimedians?', required: true }),
            T('textarea', { label: 'What should we do differently next time?' }),
          ],
        };
      },
    },
    {
      key: 'application', name: 'Scholarship application', icon: 'file', color: 'green', desc: 'Multi-page application with sections and validation.',
      build: () => {
        const prior = T('yesno', { label: 'Have you received a scholarship from us before?', required: true });
        return {
          title: 'Scholarship application', description_html: '<p>Applications are reviewed by the scholarship committee. All fields marked * are required.</p>',
          settings: { theme_color: 'green', one_response_per_user: true, show_progress: true },
          fields: [
            T('section', { label: 'About you', description: 'Basic information about the applicant.' }),
            T('text', { label: 'Full name', required: true }),
            T('email', { label: 'Email address', required: true }),
            T('text', { label: 'Country of residence', required: true }),
            T('section', { label: 'Your contributions' }),
            T('number', { label: 'How many years have you been editing Wikimedia projects?', min: 0, max: 30, required: true }),
            T('checkbox', { label: 'Which projects do you contribute to?', options: ['Wikipedia', 'Wikidata', 'Wikimedia Commons', 'Wikisource', 'Wiktionary'], allow_other: true, required: true }),
            T('url', { label: 'Link to a contribution you are proud of' }),
            prior,
            T('textarea', { label: 'What did you do with your previous scholarship?', required: true, logic: { field: prior.id, op: 'equals', value: 'Yes' } }),
            T('section', { label: 'Motivation' }),
            T('textarea', { label: 'Why do you want to attend?', required: true, min_length: 100, max_length: 2000, description: 'At least 100 characters.' }),
            T('statement', { label: 'By submitting, you agree that the committee may contact you about your application.' }),
          ],
        };
      },
    },
    {
      key: 'consultation', name: 'Community consultation', icon: 'users', color: 'violet', desc: 'Gather priorities with ranking and open comments.',
      build: () => ({
        title: 'Community priorities consultation', settings: { theme_color: 'violet', one_response_per_user: true },
        fields: [
          T('ranking', { label: 'Rank these focus areas for next year', required: true, options: ['Content gaps', 'New editor support', 'Technical tools', 'Offline events', 'Partnerships'] }),
          T('scale', { label: 'How involved do you feel in community decisions?', min: 1, max: 5, min_label: 'Not at all', max_label: 'Very involved' }),
          T('radio', { label: 'How often do you edit?', options: ['Daily', 'Weekly', 'Monthly', 'Rarely'] }),
          T('textarea', { label: 'Anything else you would like to share?' }),
        ],
      }),
    },
    {
      key: 'contact', name: 'Contact / volunteer sign-up', icon: 'mail', color: 'teal', desc: 'Collect contact details and availability.',
      build: () => ({
        title: 'Volunteer sign-up', settings: { theme_color: 'teal' },
        fields: [
          T('text', { label: 'Name', required: true }),
          T('email', { label: 'Email', required: true }),
          T('phone', { label: 'Phone (optional)' }),
          T('checkbox', { label: 'I can help with', options: ['Organising events', 'Training newcomers', 'Photography', 'Translation', 'Social media'], allow_other: true, min_select: 1 }),
          T('date', { label: 'Available from' }),
          T('time', { label: 'Preferred time to be contacted' }),
        ],
      }),
    },
  ];

  // ---------------------------------------------------------------------------
  // State

  let state = { fields: [], settings: {}, description_html: '', header_image_url: '' };
  let activeId = null;
  let advancedOpen = new Set();
  let dirty = false;
  const $ = (id) => document.getElementById(id);

  function markDirty() {
    dirty = true;
    $('saveState').textContent = 'Unsaved changes';
  }

  function field(id) { return state.fields.find(f => f.id === id); }
  function indexOf(id) { return state.fields.findIndex(f => f.id === id); }

  function load(schema, title) {
    state = {
      fields: Array.isArray(schema.fields) ? schema.fields.map(f => Object.assign({}, f)) : [],
      settings: Object.assign({ theme_color: 'indigo', show_progress: true }, schema.settings || {}),
      description_html: schema.description_html || '',
      header_image_url: schema.header_image_url || '',
    };
    $('formTitle').value = title || '';
    $('formDescEditor').innerHTML = sanitizeHtml(state.description_html);
    $('headerUrl').value = state.header_image_url.startsWith('data:') ? '' : state.header_image_url;
    activeId = state.fields[0] ? state.fields[0].id : null;
    syncTitle();
    syncHeader();
    syncSettings();
    render();
  }

  function buildSchema() {
    state.description_html = sanitizeHtml($('formDescEditor').innerHTML);
    return {
      description_html: state.description_html,
      description: $('formDescEditor').innerText.trim(),
      header_image_url: state.header_image_url,
      settings: state.settings,
      fields: state.fields,
    };
  }

  // ---------------------------------------------------------------------------
  // Palette

  function paletteHtml() {
    const groups = {};
    Object.entries(TYPES).forEach(([type, meta]) => { (groups[meta.group] = groups[meta.group] || []).push([type, meta]); });
    return Object.entries(groups).map(([group, items]) => `<h4>${esc(group)}</h4>` + items.map(([type, meta]) =>
      `<button type="button" class="palette-item" data-add="${type}"><span class="pi-icon">${icon(meta.icon)}</span>${esc(meta.label)}</button>`).join('')).join('');
  }

  function addField(type) {
    const f = newField(type);
    const at = activeId ? indexOf(activeId) + 1 : state.fields.length;
    state.fields.splice(at, 0, f);
    activeId = f.id;
    markDirty();
    render();
    focusActive(true);
  }

  // ---------------------------------------------------------------------------
  // Rendering

  function earlierQuestions(id) {
    const out = [];
    for (const f of state.fields) {
      if (f.id === id) break;
      if (INPUT_TYPES.has(f.type)) out.push(f);
    }
    return out;
  }

  function sourceValues(src) {
    if (!src) return null;
    if (src.type === 'yesno') return ['Yes', 'No'];
    if (['radio', 'checkbox', 'dropdown'].includes(src.type)) return src.options || [];
    return null;
  }

  function optionListHtml(f, key, marker) {
    const list = f[key] || [];
    return `<div class="b-options">${list.map((opt, i) => `
      <div class="b-option">
        <span class="marker">${marker(i)}</span>
        <input type="text" class="input" value="${esc(opt)}" data-opt="${key}" data-i="${i}" aria-label="Option ${i + 1}">
        <button type="button" class="btn btn-ghost btn-icon btn-sm" data-remove-opt="${key}" data-i="${i}" aria-label="Remove option" ${list.length <= 1 ? 'disabled' : ''}>${icon('x', 'icon-sm')}</button>
      </div>`).join('')}
      <div class="b-option"><span class="marker">${marker(list.length)}</span>
        <button type="button" class="btn btn-ghost btn-sm" data-add-opt="${key}">${icon('plus', 'icon-sm')}Add ${key === 'rows' ? 'row' : key === 'columns' ? 'column' : 'option'}</button>
        ${key === 'options' && (f.type === 'radio' || f.type === 'checkbox') && !f.allow_other ? `<span class="muted small">or</span><button type="button" class="btn btn-ghost btn-sm" data-toggle="allow_other">add "Other"</button>` : ''}
      </div>
      ${key === 'options' && f.allow_other ? `<div class="b-option"><span class="marker">${marker(list.length)}</span><input class="input" value="Other…" disabled><button type="button" class="btn btn-ghost btn-icon btn-sm" data-toggle="allow_other" aria-label="Remove other option">${icon('x', 'icon-sm')}</button></div>` : ''}
    </div>`;
  }

  function markerFor(type) {
    if (type === 'checkbox') return () => icon('checkbox', 'icon-sm');
    if (type === 'radio') return () => icon('radio', 'icon-sm');
    return (i) => `${i + 1}.`;
  }

  function numberSelect(name, value, from, to) {
    let opts = '';
    for (let i = from; i <= to; i++) opts += `<option value="${i}" ${Number(value) === i ? 'selected' : ''}>${i}</option>`;
    return `<select class="select select-sm" data-prop="${name}" data-num="int" style="width:auto">${opts}</select>`;
  }

  function editorBody(f) {
    const t = f.type;
    let body = '';
    if (OPTION_TYPES.has(t)) body += optionListHtml(f, 'options', markerFor(t));
    if (t === 'matrix') {
      body += `<div class="field-row" style="margin-top:12px"><div><span class="label">Rows (statements)</span>${optionListHtml(f, 'rows', i => `${i + 1}.`)}</div>
        <div><span class="label">Columns (answer scale)</span>${optionListHtml(f, 'columns', () => icon('radio', 'icon-sm'))}</div></div>`;
    }
    if (t === 'scale') {
      body += `<div class="row row-wrap" style="margin-top:14px">${numberSelect('min', f.min == null ? 1 : f.min, 0, 1)}<span class="muted">to</span>${numberSelect('max', f.max || 5, 2, 10)}</div>
        <div class="field-row" style="margin-top:10px">
          <input class="input input-sm" data-prop="min_label" value="${esc(f.min_label || '')}" placeholder="Label for lowest (optional)">
          <input class="input input-sm" data-prop="max_label" value="${esc(f.max_label || '')}" placeholder="Label for highest (optional)"></div>`;
    }
    if (t === 'nps') {
      body += `<div class="field-row" style="margin-top:12px">
        <input class="input input-sm" data-prop="min_label" value="${esc(f.min_label || '')}" placeholder="Label for 0">
        <input class="input input-sm" data-prop="max_label" value="${esc(f.max_label || '')}" placeholder="Label for 10"></div>`;
    }
    if (t === 'rating') body += `<div class="row" style="margin-top:14px"><span class="muted small">Number of stars</span>${numberSelect('max', f.max || 5, 3, 10)}</div>`;
    const previews = {
      text: ['text', 'Short answer text'], textarea: ['paragraph', 'Long answer text'], email: ['mail', 'name@example.org'],
      number: ['hash', 'Number'], phone: ['phone', '+880 1…'], url: ['globe', 'https://…'], date: ['calendar', 'Day, month, year'],
      time: ['clock', 'Time'], yesno: ['toggle', 'Yes  /  No'],
    };
    if (previews[t]) body += `<div class="b-preview">${icon(previews[t][0], 'icon-sm')}${esc(f.placeholder || previews[t][1])}</div>`;
    return body;
  }

  function advancedHtml(f) {
    const t = f.type;
    let html = '';
    if (['text', 'textarea', 'email', 'number', 'phone', 'url'].includes(t)) {
      html += `<div><label class="label">Placeholder</label><input class="input input-sm" data-prop="placeholder" value="${esc(f.placeholder || '')}" placeholder="Your answer"></div>`;
    }
    if (t === 'text' || t === 'textarea') {
      html += `<div><h5>${icon('check', 'icon-sm')}Validation</h5><div class="field-row" style="margin-top:8px">
        <input type="number" min="0" class="input input-sm" data-prop="min_length" data-num="int" value="${f.min_length ?? ''}" placeholder="Min characters">
        <input type="number" min="1" class="input input-sm" data-prop="max_length" data-num="int" value="${f.max_length ?? ''}" placeholder="Max characters"></div></div>`;
    }
    if (t === 'number') {
      html += `<div><h5>${icon('check', 'icon-sm')}Allowed range</h5><div class="field-row" style="margin-top:8px">
        <input type="number" step="any" class="input input-sm" data-prop="min" data-num="float" value="${f.min ?? ''}" placeholder="Minimum">
        <input type="number" step="any" class="input input-sm" data-prop="max" data-num="float" value="${f.max ?? ''}" placeholder="Maximum"></div></div>`;
    }
    if (t === 'checkbox') {
      html += `<div><h5>${icon('check', 'icon-sm')}Number of selections</h5><div class="field-row" style="margin-top:8px">
        <input type="number" min="0" class="input input-sm" data-prop="min_select" data-num="int" value="${f.min_select ?? ''}" placeholder="At least">
        <input type="number" min="1" class="input input-sm" data-prop="max_select" data-num="int" value="${f.max_select ?? ''}" placeholder="At most"></div></div>`;
    }
    if (OPTION_TYPES.has(t)) {
      html += `<label class="switch"><input type="checkbox" data-bool="shuffle" ${f.shuffle ? 'checked' : ''}><span class="track"></span>Shuffle option order for each respondent</label>`;
    }

    const earlier = earlierQuestions(f.id);
    const logic = f.logic || null;
    const src = logic ? field(logic.field) : null;
    const values = sourceValues(src);
    const needsValue = logic && !['answered', 'not_answered'].includes(logic.op);
    html += `<div><h5>${icon('branch', 'icon-sm')}Display logic</h5>
      ${earlier.length === 0 ? '<p class="hint">Add questions above this one to show it conditionally.</p>' : `
      <p class="hint" style="margin:4px 0 8px">Only show this ${t === 'section' ? 'section (and everything in it)' : 'question'} when…</p>
      <div class="b-logic">
        <select class="select select-sm" data-logic="field"><option value="">Always show</option>
          ${earlier.map(q => `<option value="${esc(q.id)}" ${logic && logic.field === q.id ? 'selected' : ''}>${esc(q.label || 'Untitled question')}</option>`).join('')}</select>
        <select class="select select-sm" data-logic="op" ${logic ? '' : 'disabled'}>${OPS.map(([v, l]) => `<option value="${v}" ${logic && logic.op === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
        ${!logic || !needsValue ? '<span></span>' : values
          ? `<select class="select select-sm" data-logic="value"><option value="">Choose…</option>${values.map(v => `<option value="${esc(v)}" ${logic.value === v ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select>`
          : `<input class="input input-sm" data-logic="value" value="${esc(logic.value || '')}" placeholder="Value">`}
      </div>`}</div>`;
    return `<div class="b-advanced">${html}</div>`;
  }

  function summaryHtml(f) {
    const t = f.type;
    if (OPTION_TYPES.has(t)) return `<div class="muted small" style="margin-top:6px">${(f.options || []).slice(0, 4).map(esc).join(' · ')}${(f.options || []).length > 4 ? ` · +${f.options.length - 4} more` : ''}${f.allow_other ? ' · Other' : ''}</div>`;
    if (t === 'matrix') return `<div class="muted small" style="margin-top:6px">${(f.rows || []).length} rows × ${(f.columns || []).length} columns</div>`;
    if (t === 'scale') return `<div class="muted small" style="margin-top:6px">${f.min == null ? 1 : f.min} – ${f.max || 5}</div>`;
    if (t === 'rating') return `<div class="muted small" style="margin-top:6px">${'★'.repeat(f.max || 5)}</div>`;
    return f.description ? `<div class="muted small" style="margin-top:6px">${esc(f.description)}</div>` : '';
  }

  function cardHtml(f, number) {
    const meta = TYPES[f.type] || TYPES.text;
    const active = f.id === activeId;
    const isLayout = !INPUT_TYPES.has(f.type);
    const logicSrc = f.logic ? field(f.logic.field) : null;
    const logicBadge = f.logic ? `<span class="b-logic-badge" title="Shown only when “${esc(logicSrc ? logicSrc.label : '')}” ${esc((OPS.find(o => o[0] === f.logic.op) || [])[1] || '')} ${esc(f.logic.value || '')}">${icon('branch', 'icon-sm')}Conditional</span>` : '';

    const head = `<div class="b-field-head">
        <span class="b-handle" draggable="true" data-drag title="Drag to reorder">${icon('drag')}</span>
        <span class="b-type-pill">${icon(meta.icon)}${esc(meta.label)}</span>
        ${logicBadge}
        ${f.required ? '<span class="badge badge-danger plain">Required</span>' : ''}
        <span class="spacer"></span>
        ${!active ? `<span class="muted xsmall">${number ? `Q${number}` : ''}</span>` : ''}
      </div>`;

    if (!active) {
      return `<div class="card b-field ${f.type === 'section' ? 'b-section-card' : ''}" data-id="${esc(f.id)}" tabindex="0">
        ${head}
        <div class="b-field-body" style="cursor:pointer">
          <div style="font-weight:600;font-size:${f.type === 'section' ? '18px' : '15.5px'}">${esc(f.label || (isLayout ? 'Untitled' : 'Untitled question'))}${f.required ? ' <span class="req">*</span>' : ''}</div>
          ${summaryHtml(f)}
        </div>
      </div>`;
    }

    const typeOptions = Object.entries(TYPES).map(([k, m]) => `<option value="${k}" ${k === f.type ? 'selected' : ''}>${esc(m.label)}</option>`).join('');
    return `<div class="card b-field active ${f.type === 'section' ? 'b-section-card' : ''}" data-id="${esc(f.id)}">
      ${head}
      <div class="b-field-body">
        <input type="text" class="b-label" data-prop="label" value="${esc(f.label)}" placeholder="${f.type === 'section' ? 'Section title' : f.type === 'statement' ? 'Text to display' : 'Question'}" aria-label="Question text">
        <textarea class="b-desc" data-prop="description" rows="1" placeholder="${f.type === 'statement' ? 'More details (optional)' : 'Description or help text (optional)'}">${esc(f.description || '')}</textarea>
        ${editorBody(f)}
        ${advancedOpen.has(f.id) ? advancedHtml(f) : ''}
      </div>
      <div class="b-field-foot">
        <select class="select select-sm" data-type style="width:auto;max-width:200px" aria-label="Question type">${typeOptions}</select>
        <span class="spacer"></span>
        <button type="button" class="btn btn-ghost btn-icon btn-sm" data-act="up" title="Move up" aria-label="Move up">${icon('up', 'icon-sm')}</button>
        <button type="button" class="btn btn-ghost btn-icon btn-sm" data-act="down" title="Move down" aria-label="Move down">${icon('down', 'icon-sm')}</button>
        <button type="button" class="btn btn-ghost btn-icon btn-sm" data-act="duplicate" title="Duplicate" aria-label="Duplicate">${icon('copy', 'icon-sm')}</button>
        <button type="button" class="btn btn-danger-ghost btn-icon btn-sm" data-act="delete" title="Delete" aria-label="Delete">${icon('trash', 'icon-sm')}</button>
        <span class="divider"></span>
        <button type="button" class="btn btn-ghost btn-sm" data-act="advanced" aria-pressed="${advancedOpen.has(f.id)}">${icon('settings', 'icon-sm')}${advancedOpen.has(f.id) ? 'Hide options' : 'More options'}</button>
        ${isLayout ? '' : `<span class="divider"></span><label class="switch"><input type="checkbox" data-bool="required" ${f.required ? 'checked' : ''}><span class="track"></span>Required</label>`}
      </div>
    </div>`;
  }

  function render() {
    const host = $('fields');
    if (!state.fields.length) {
      host.innerHTML = `<div class="b-empty">${icon('plus', 'icon-lg')}<p style="margin-top:8px">No questions yet. Pick a question type from the left to get started.</p></div>`;
      return;
    }
    let n = 0;
    host.innerHTML = state.fields.map(f => cardHtml(f, INPUT_TYPES.has(f.type) ? ++n : 0)).join('');
    host.querySelectorAll('.b-desc').forEach(autoGrow);
  }

  function autoGrow(el) {
    el.style.height = 'auto';
    el.style.height = el.scrollHeight + 'px';
  }

  function focusActive(select) {
    const card = document.querySelector(`.b-field.active`);
    if (!card) return;
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    const label = card.querySelector('.b-label');
    if (label) { label.focus({ preventScroll: true }); if (select) label.select(); }
  }

  function activate(id) {
    if (activeId === id) return;
    activeId = id;
    render();
  }

  // ---------------------------------------------------------------------------
  // Field events (delegated)

  function onFieldsInput(e) {
    const card = e.target.closest('.b-field');
    if (!card) return;
    const f = field(card.dataset.id);
    const el = e.target;
    if (el.dataset.prop) {
      let v = el.value;
      if (el.dataset.num) {
        v = v === '' ? null : (el.dataset.num === 'int' ? parseInt(v, 10) : parseFloat(v));
        if (Number.isNaN(v)) v = null;
      }
      f[el.dataset.prop] = v;
      if (el.classList.contains('b-desc')) autoGrow(el);
      markDirty();
      return;
    }
    if (el.dataset.opt) {
      f[el.dataset.opt][Number(el.dataset.i)] = el.value;
      markDirty();
      return;
    }
    if (el.dataset.logic === 'value' && el.tagName === 'INPUT') {
      f.logic.value = el.value;
      markDirty();
    }
  }

  function onFieldsChange(e) {
    const card = e.target.closest('.b-field');
    if (!card) return;
    const f = field(card.dataset.id);
    const el = e.target;
    if (el.dataset.bool) {
      f[el.dataset.bool] = el.checked;
      markDirty();
      render();
      return;
    }
    if (el.hasAttribute('data-type')) {
      changeType(f, el.value);
      return;
    }
    if (el.dataset.prop && el.tagName === 'SELECT') { markDirty(); render(); return; }
    if (el.dataset.logic && el.tagName === 'SELECT') {
      const key = el.dataset.logic;
      if (key === 'field') {
        f.logic = el.value ? { field: el.value, op: (f.logic && f.logic.op) || 'equals', value: '' } : undefined;
        if (!f.logic) delete f.logic;
      } else if (f.logic) {
        f.logic[key] = el.value;
      }
      markDirty();
      render();
    }
  }

  function changeType(f, type) {
    const fresh = newField(type);
    const keep = { id: f.id, label: f.label, description: f.description, logic: f.logic };
    if (INPUT_TYPES.has(type)) keep.required = !!f.required;
    if (OPTION_TYPES.has(type) && Array.isArray(f.options)) keep.options = f.options;
    Object.keys(f).forEach(k => delete f[k]);
    Object.assign(f, fresh, keep);
    if (!f.logic) delete f.logic;
    // Other questions' logic may depend on this one's answers.
    if (!INPUT_TYPES.has(type)) state.fields.forEach(o => { if (o.logic && o.logic.field === f.id) delete o.logic; });
    markDirty();
    render();
  }

  function onFieldsClick(e) {
    const card = e.target.closest('.b-field');
    if (!card) return;
    const id = card.dataset.id;
    const f = field(id);

    if (!card.classList.contains('active')) {
      activate(id);
      focusActive(false);
      return;
    }
    const act = e.target.closest('[data-act]');
    if (act) {
      const i = indexOf(id);
      const a = act.dataset.act;
      if (a === 'delete') {
        state.fields.splice(i, 1);
        state.fields.forEach(o => { if (o.logic && o.logic.field === id) delete o.logic; });
        activeId = state.fields[Math.min(i, state.fields.length - 1)]?.id || null;
      } else if (a === 'duplicate') {
        const copy = JSON.parse(JSON.stringify(f));
        copy.id = uid();
        state.fields.splice(i + 1, 0, copy);
        activeId = copy.id;
      } else if (a === 'up' && i > 0) {
        move(i, i - 1);
      } else if (a === 'down' && i < state.fields.length - 1) {
        move(i, i + 1);
      } else if (a === 'advanced') {
        advancedOpen.has(id) ? advancedOpen.delete(id) : advancedOpen.add(id);
        render();
        return;
      }
      markDirty();
      render();
      return;
    }
    const addOpt = e.target.closest('[data-add-opt]');
    if (addOpt) {
      const key = addOpt.dataset.addOpt;
      const noun = key === 'rows' ? 'Row' : key === 'columns' ? 'Column' : 'Option';
      f[key] = f[key] || [];
      f[key].push(`${noun} ${f[key].length + 1}`);
      markDirty();
      render();
      const inputs = document.querySelectorAll(`.b-field.active [data-opt="${key}"]`);
      const last = inputs[inputs.length - 1];
      if (last) { last.focus(); last.select(); }
      return;
    }
    const removeOpt = e.target.closest('[data-remove-opt]');
    if (removeOpt) {
      const key = removeOpt.dataset.removeOpt;
      if (f[key].length > 1) f[key].splice(Number(removeOpt.dataset.i), 1);
      markDirty();
      render();
      return;
    }
    const toggle = e.target.closest('[data-toggle]');
    if (toggle) {
      f[toggle.dataset.toggle] = !f[toggle.dataset.toggle];
      markDirty();
      render();
    }
  }

  function onFieldsKeydown(e) {
    const el = e.target;
    if (el.dataset && el.dataset.opt && e.key === 'Enter') {
      e.preventDefault();
      const card = el.closest('.b-field');
      const f = field(card.dataset.id);
      const key = el.dataset.opt;
      const i = Number(el.dataset.i);
      const noun = key === 'rows' ? 'Row' : key === 'columns' ? 'Column' : 'Option';
      f[key].splice(i + 1, 0, `${noun} ${f[key].length + 1}`);
      markDirty();
      render();
      const next = document.querySelector(`.b-field.active [data-opt="${key}"][data-i="${i + 1}"]`);
      if (next) { next.focus(); next.select(); }
    }
    if (e.key === 'Enter' && el.classList && el.classList.contains('b-field') && !el.classList.contains('active')) {
      activate(el.dataset.id);
      focusActive(false);
    }
  }

  function onFieldsPaste(e) {
    const el = e.target;
    if (!el.dataset || !el.dataset.opt) return;
    const text = (e.clipboardData || window.clipboardData).getData('text');
    const lines = text.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    if (lines.length < 2) return;
    e.preventDefault();
    const f = field(el.closest('.b-field').dataset.id);
    const key = el.dataset.opt;
    const i = Number(el.dataset.i);
    f[key].splice(i, 1, ...lines);
    markDirty();
    render();
    toast(`Added ${lines.length} options`);
  }

  function move(from, to) {
    const [item] = state.fields.splice(from, 1);
    state.fields.splice(to, 0, item);
    // Logic may only reference earlier questions.
    state.fields.forEach((f, i) => {
      if (f.logic && state.fields.findIndex(o => o.id === f.logic.field) >= i) delete f.logic;
    });
  }

  // Drag and drop reordering via the handle.
  let dragId = null;
  function setupDnd(host) {
    host.addEventListener('dragstart', (e) => {
      const handle = e.target.closest('[data-drag]');
      if (!handle) return;
      const card = handle.closest('.b-field');
      dragId = card.dataset.id;
      card.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', dragId);
      try { e.dataTransfer.setDragImage(card, 20, 20); } catch (_) { /* optional */ }
    });
    host.addEventListener('dragover', (e) => {
      if (!dragId) return;
      const card = e.target.closest('.b-field');
      if (!card) return;
      e.preventDefault();
      host.querySelectorAll('.drag-over-top, .drag-over-bottom').forEach(c => c.classList.remove('drag-over-top', 'drag-over-bottom'));
      const rect = card.getBoundingClientRect();
      card.classList.add(e.clientY < rect.top + rect.height / 2 ? 'drag-over-top' : 'drag-over-bottom');
    });
    host.addEventListener('drop', (e) => {
      if (!dragId) return;
      const card = e.target.closest('.b-field');
      e.preventDefault();
      if (card && card.dataset.id !== dragId) {
        const after = card.classList.contains('drag-over-bottom');
        const from = indexOf(dragId);
        let to = indexOf(card.dataset.id) + (after ? 1 : 0);
        if (from < to) to--;
        move(from, to);
        markDirty();
      }
      dragId = null;
      render();
    });
    host.addEventListener('dragend', () => {
      dragId = null;
      host.querySelectorAll('.dragging, .drag-over-top, .drag-over-bottom').forEach(c => c.classList.remove('dragging', 'drag-over-top', 'drag-over-bottom'));
    });
  }

  // ---------------------------------------------------------------------------
  // Title, description, header image

  function syncTitle() {
    const title = $('formTitle').value.trim();
    $('barTitle').textContent = title || 'Untitled form';
  }

  function syncHeader() {
    const url = state.header_image_url;
    $('headerPreview').classList.toggle('hidden', !url);
    if (url) $('headerPreviewImg').src = url;
  }

  function onHeaderFile(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    if (!HEADER_TYPES.has(file.type)) { toast('Please upload a PNG, JPEG, GIF or WEBP image.', 'alert'); e.target.value = ''; return; }
    if (file.size > HEADER_MAX_BYTES * 5) { toast('That image is too large (max 10 MB before compression).', 'alert'); e.target.value = ''; return; }
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        // Downscale and compress so the payload stays well under proxy limits.
        const MAX_WIDTH = 1200;
        let { width, height } = img;
        if (width > MAX_WIDTH) { height = Math.round(height * MAX_WIDTH / width); width = MAX_WIDTH; }
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        canvas.getContext('2d').drawImage(img, 0, 0, width, height);
        state.header_image_url = canvas.toDataURL('image/jpeg', 0.6);
        $('headerUrl').value = '';
        syncHeader();
        markDirty();
      };
      img.src = typeof reader.result === 'string' ? reader.result : '';
    };
    reader.onerror = () => toast('Failed to read the selected image.', 'alert');
    reader.readAsDataURL(file);
  }

  function onDescCommand(e) {
    const btn = e.target.closest('[data-cmd]');
    if (!btn) return;
    const editor = $('formDescEditor');
    editor.focus();
    const cmd = btn.dataset.cmd;
    if (cmd === 'link') {
      const safe = sanitizeUrl(window.prompt('Enter URL (https://…)'), true);
      if (safe) document.execCommand('createLink', false, safe);
    } else {
      document.execCommand(cmd, false, null);
    }
    markDirty();
  }

  // ---------------------------------------------------------------------------
  // Settings

  function syncSettings() {
    const s = state.settings;
    $('swatches').innerHTML = Object.entries(THEMES).map(([k, c]) =>
      `<button type="button" class="swatch" data-theme-color="${k}" style="background:${c}" aria-label="${k}" title="${k}" aria-pressed="${s.theme_color === k}"></button>`).join('');
    $('setProgress').checked = s.show_progress !== false;
    $('setNumbers').checked = !!s.show_question_numbers;
    $('setOnce').checked = !!s.one_response_per_user;
    $('setEmail').checked = !!s.collect_email;
    $('setLimit').value = s.response_limit || '';
    $('setCloseAt').value = s.close_at || '';
    $('setConfirm').value = s.confirmation_message || '';
    document.querySelectorAll('#canvas, #previewWrap').forEach(el => el.setAttribute('data-accent', s.theme_color || 'indigo'));
  }

  function bindSettings() {
    $('swatches').addEventListener('click', (e) => {
      const sw = e.target.closest('[data-theme-color]');
      if (!sw) return;
      state.settings.theme_color = sw.dataset.themeColor;
      syncSettings();
      markDirty();
    });
    const bind = (id, key, read) => $(id).addEventListener('input', () => { state.settings[key] = read($(id)); markDirty(); });
    bind('setProgress', 'show_progress', el => el.checked);
    bind('setNumbers', 'show_question_numbers', el => el.checked);
    bind('setOnce', 'one_response_per_user', el => el.checked);
    bind('setEmail', 'collect_email', el => el.checked);
    bind('setLimit', 'response_limit', el => (el.value ? parseInt(el.value, 10) : null));
    bind('setCloseAt', 'close_at', el => el.value);
    bind('setConfirm', 'confirmation_message', el => el.value);
  }

  // ---------------------------------------------------------------------------
  // Views

  function showView(view) {
    document.querySelectorAll('[data-view]').forEach(t => t.setAttribute('aria-selected', String(t.dataset.view === view)));
    document.querySelectorAll('[data-panel]').forEach(p => p.classList.toggle('hidden', p.dataset.panel !== view));
    if (view === 'preview') renderPreview();
    window.scrollTo({ top: 0 });
  }

  function renderPreview() {
    const schema = JSON.parse(JSON.stringify(buildSchema()));
    $('pvTitle').textContent = $('formTitle').value.trim() || 'Untitled form';
    const desc = sanitizeHtml(schema.description_html);
    $('pvDesc').innerHTML = desc;
    $('pvDesc').classList.toggle('hidden', !desc);
    const img = $('pvImage');
    img.classList.toggle('hidden', !schema.header_image_url);
    if (schema.header_image_url) img.src = schema.header_image_url;
    window.DTOCForm.create($('previewRoot'), schema, { preview: true, onRestart: renderPreview });
  }

  // ---------------------------------------------------------------------------
  // Save

  function validateBeforeSave() {
    if (!$('formTitle').value.trim()) {
      showView('questions');
      $('formTitle').focus();
      toast('Please give your form a title.', 'alert');
      return false;
    }
    if (!state.fields.some(f => INPUT_TYPES.has(f.type))) {
      toast('Add at least one question before saving.', 'alert');
      return false;
    }
    const seen = new Map();
    for (const f of state.fields) {
      if (!INPUT_TYPES.has(f.type)) continue;
      const key = (f.label || '').trim().toLowerCase();
      if (key && seen.has(key)) {
        toast(`Two questions are both called “${f.label.trim()}”. They will be saved as separate columns.`, 'info');
        break;
      }
      seen.set(key, true);
    }
    return true;
  }

  function bindSave() {
    $('builderForm').addEventListener('submit', (e) => {
      if (!validateBeforeSave()) { e.preventDefault(); return; }
      $('titleInput').value = $('formTitle').value.trim();
      $('schemaInput').value = JSON.stringify(buildSchema());
      dirty = false;
      $('saveState').textContent = 'Saving…';
    });
    window.addEventListener('beforeunload', (e) => {
      if (!dirty) return;
      e.preventDefault();
      e.returnValue = '';
    });
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        $('builderForm').requestSubmit();
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Init

  function applyTemplate(tpl) {
    if (dirty && !window.confirm('Replace your current form with this template?')) return;
    const built = tpl.build();
    load({ fields: built.fields, settings: built.settings || {}, description_html: built.description_html || '' }, built.title);
    dirty = built.title !== '';
    $('saveState').textContent = dirty ? 'Unsaved changes' : 'New form';
    $('templatesDialog').close();
    showView('questions');
  }

  document.addEventListener('DOMContentLoaded', () => {
    const palette = paletteHtml();
    $('palette').innerHTML = palette;
    $('paletteMobile').innerHTML = palette;
    document.addEventListener('click', (e) => {
      const add = e.target.closest('[data-add]');
      if (!add) return;
      addField(add.dataset.add);
      const dlg = add.closest('dialog');
      if (dlg) dlg.close();
    });
    $('addQuestion').addEventListener('click', () => addField('radio'));

    const host = $('fields');
    host.addEventListener('input', onFieldsInput);
    host.addEventListener('change', onFieldsChange);
    host.addEventListener('click', onFieldsClick);
    host.addEventListener('keydown', onFieldsKeydown);
    host.addEventListener('paste', onFieldsPaste);
    setupDnd(host);

    $('formTitle').addEventListener('input', () => { syncTitle(); markDirty(); });
    $('formDescEditor').addEventListener('input', markDirty);
    document.querySelector('.rich-toolbar').addEventListener('click', onDescCommand);
    $('headerFile').addEventListener('change', onHeaderFile);
    $('headerUrl').addEventListener('change', () => {
      const safe = sanitizeUrl($('headerUrl').value);
      if ($('headerUrl').value && !safe) toast('Please use an https:// image link.', 'alert');
      state.header_image_url = safe;
      syncHeader();
      markDirty();
    });
    $('removeHeader').addEventListener('click', () => {
      state.header_image_url = '';
      $('headerUrl').value = '';
      $('headerFile').value = '';
      syncHeader();
      markDirty();
    });

    document.querySelectorAll('[data-view]').forEach(t => t.addEventListener('click', () => showView(t.dataset.view)));
    bindSettings();
    bindSave();

    $('templateGrid').innerHTML = TEMPLATES.map(t => `<button type="button" class="template-card" data-template="${t.key}">
      <div class="stat-icon ${t.color || ''}">${icon(t.icon)}</div><h4>${esc(t.name)}</h4><p>${esc(t.desc)}</p></button>`).join('');
    $('templateGrid').addEventListener('click', (e) => {
      const card = e.target.closest('[data-template]');
      if (card) applyTemplate(TEMPLATES.find(t => t.key === card.dataset.template));
    });

    let initial = {};
    try {
      const raw = JSON.parse($('initialSchema').textContent || '""');
      initial = raw ? (typeof raw === 'string' ? JSON.parse(raw) : raw) : {};
    } catch (_) { initial = {}; }
    const title = JSON.parse($('initialTitle').textContent || '""');
    if (initial && Array.isArray(initial.fields)) {
      load(initial, title);
    } else {
      load({ fields: [newField('text')] }, title);
      if (window.BUILDER_IS_NEW) $('templatesDialog').showModal();
    }
  });
})();
