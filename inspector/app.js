const $ = id => document.getElementById(id);
const token = location.hash.slice(1) || sessionStorage.getItem('savior_access');
if (location.hash) sessionStorage.setItem('savior_access', token);
history.replaceState(null, '', '/');
let view = 'live', selected = null, rows = [], before = Number.MAX_SAFE_INTEGER, after_id = '';
let session_id = null, sequence = 0, paused = false, state = null, latest_snapshot = null;
let detail_key = '';
const live_rows = new Map();

async function api(path, body) {
  const response = await fetch('/api/' + path, {
    method: body ? 'POST' : 'GET', headers: {Authorization: 'Bearer ' + token, ...(body ? {'Content-Type': 'application/json'} : {})},
    ...(body ? {body: JSON.stringify(body)} : {}), cache: 'no-store',
  });
  const value = await response.json();
  if (!response.ok) throw Error(value.error);
  return value;
}
function element(tag, text, class_name) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (class_name) node.className = class_name;
  return node;
}
async function run(action) {
  try { await action(); } catch (error) { $('status').textContent = 'Inspection unavailable: ' + error.message; }
}
function original_diff(row) {
  const pre = element('pre', undefined, 'original');
  if (row.original == null) { pre.textContent = 'Text not recorded'; return pre; }
  const bytes = new TextEncoder().encode(row.original), decode = b => new TextDecoder('utf-8', {fatal: true}).decode(b);
  let cursor = 0;
  for (const span of row.decision?.applied ?? []) {
    pre.append(document.createTextNode(decode(bytes.slice(cursor, span.start))), element('mark', decode(bytes.slice(span.start, span.end))));
    cursor = span.end;
  }
  pre.append(document.createTextNode(decode(bytes.slice(cursor))));
  return pre;
}
function output_diff(row) {
  const pre = element('pre', undefined, 'output');
  if (row.output == null) { pre.textContent = row.outcome == null ? 'Waiting for processing…' : ['failed', 'abstained', 'cancelled'].includes(row.outcome) ? 'No successful submission output' : 'Text not recorded'; return pre; }
  if (!row.original || !row.decision?.applied?.length) { pre.textContent = row.output; return pre; }
  const bytes = new TextEncoder().encode(row.original), decode = b => new TextDecoder('utf-8', {fatal: true}).decode(b);
  let cursor = 0;
  for (const span of row.decision.applied) {
    pre.append(document.createTextNode(decode(bytes.slice(cursor, span.start))), element('mark', row.decision.marker ?? ''));
    cursor = span.end;
  }
  pre.append(document.createTextNode(decode(bytes.slice(cursor))));
  return pre;
}
function annotations(row, card) {
  const section = element('details', undefined, 'annotations'); section.append(element('summary', 'Human judgment'));
  const annotation = row.annotation ?? {profanity: 'unresolved', targeted_abuse: 'unresolved', preservation: 'unresolved', note: ''}, controls = {};
  for (const key of ['profanity', 'targeted_abuse', 'preservation']) {
    const label = element('label', key.replaceAll('_', ' ') + ' '), select = element('select');
    for (const value of ['unresolved', 'pass', 'fail', 'not_applicable']) { const option = element('option', value); option.value = value; select.append(option); }
    select.value = annotation[key]; controls[key] = select; label.append(select); section.append(label);
  }
  const note = element('textarea'); note.value = annotation.note; note.maxLength = 4096; note.setAttribute('aria-label', 'Reviewer note'); section.append(note);
  const save = element('button', 'Save human judgment');
  save.onclick = () => run(async () => {
    const annotation = {...Object.fromEntries(Object.entries(controls).map(([key, select]) => [key, select.value])), note: note.value};
    await api('annotate', {id: row.id, annotation}); row.annotation = annotation; save.textContent = 'Saved';
  });
  section.append(save); card.append(section);
}
function render_detail(row) {
  const key = JSON.stringify([view, row?.id, row?.sequence, state?.generation, row?.annotation]);
  if (key === detail_key) return;
  detail_key = key;
  const expanded = [...$('detail').querySelectorAll('details[open] summary')].map(node => node.textContent);
  const card = element('article');
  if (!row) { card.className = 'empty'; card.append(element('h3', view === 'history' ? 'No saved observations' : 'Ready when you are'), element('p', view === 'history' ? 'History appears here as Savior processes submissions.' : 'Submit a prompt through Savior in the same profile. Its processing and exact edits will appear here.')); $('detail').replaceChildren(card); return; }
  card.append(element('h2', row.outcome ?? 'Processing'), element('p', new Date(row.timestamp_ms).toLocaleString() + ' · ' + (row.disposition ?? 'pending') + (row.elapsed_ms != null && row.code !== 'adapter_failed' ? ' · ' + row.elapsed_ms.toFixed(1) + ' ms' : ''), 'details-meta'));
  const stages = element('ol', undefined, 'stages');
  for (const stage of row.stages ?? []) stages.append(element('li', stage.name === 'delivery' ? row.disposition : stage.name));
  card.append(stages);
  if (row.recording_warning) card.append(element('p', 'Recording gap: this observation was not saved. Live processing is unchanged.', 'recording-warning'));
  const diff = element('div', undefined, 'diff');
  for (const [title, node] of [['Original', original_diff(row)], ['Replacement', output_diff(row)]]) { const side = element('div'); side.append(element('h3', title), node); diff.append(side); }
  card.append(diff);
  const details = element('details'); details.append(element('summary', 'Detector decisions and evidence'), element('pre', JSON.stringify({...row, original: undefined, output: undefined, annotation: undefined, stages: undefined}, null, 2))); card.append(details);
  if (view === 'history' && state?.full_text) annotations(row, card);
  for (const details of card.querySelectorAll('details')) details.open = expanded.includes(details.querySelector('summary')?.textContent);
  $('detail').replaceChildren(card);
}
function render() {
  const needle = $('search').value.toLowerCase(), outcome = $('outcome').value;
  const visible = rows.filter(row => (!outcome || row.outcome === outcome) && (!needle || JSON.stringify(row).toLowerCase().includes(needle)));
  if (!visible.some(row => row.id === selected)) selected = visible[0]?.id ?? null;
  const list = document.createDocumentFragment();
  for (const row of visible) {
    const button = element('button', undefined, 'activity'); button.setAttribute('aria-pressed', String(row.id === selected));
    button.dataset.observation = row.id;
    button.append(element('span', row.outcome ?? row.stages?.at(-1)?.name ?? 'processing', 'badge'), element('span', row.original ?? 'Text not recorded', 'preview'));
    const meta = element('span', undefined, 'meta'); meta.append(element('span', new Date(row.timestamp_ms).toLocaleTimeString()), element('span', row.disposition ?? 'pending')); button.append(meta);
    button.onclick = () => { selected = row.id; $('follow').checked = false; render(); }; list.append(button);
  }
  const focused = document.activeElement?.closest('.activity')?.dataset.observation;
  const scroll_top = $('events').scrollTop;
  $('events').replaceChildren(list);
  if (focused) [...$('events').children].find(button => button.dataset.observation === focused)?.focus({preventScroll: true});
  $('events').scrollTop = scroll_top;
  render_detail(visible.find(row => row.id === selected));
}
function display_snapshot() {
  if (!latest_snapshot || paused || view !== 'live') return;
  rows = [...live_rows.values()].sort((a, b) => b.timestamp_ms - a.timestamp_ms || b.id.localeCompare(a.id));
  if ($('follow').checked) selected = rows[0]?.id ?? null;
  const stats = latest_snapshot.stats, counts = stats.outcomes;
  const cards = [['Rewritten', counts.rewritten ?? 0], ['Unchanged', counts.unchanged ?? 0], ['Abstained', counts.abstained ?? 0], ['Failed', counts.failed ?? 0], ['Cancelled', counts.cancelled ?? 0], ['Replacements', stats.replacements], ['Mean processing', stats.mean_elapsed_ms == null ? '—' : stats.mean_elapsed_ms.toFixed(1) + ' ms']];
  $('stats').replaceChildren(...cards.map(([label, value]) => { const node = element('div', undefined, 'stat'); node.append(element('strong', String(value)), element('span', label)); return node; }));
  $('coverage').textContent = latest_snapshot.dropped ? `${latest_snapshot.dropped} events dropped or observations evicted. Coverage is incomplete; counts include completed observations already evicted.` : 'Live text stays in this inspector session unless you opt in to full history.';
  render();
}
function accept_snapshot(snapshot) {
  if (session_id !== snapshot.session_id) { live_rows.clear(); session_id = snapshot.session_id; selected = null; }
  const retained = new Set(snapshot.retained_ids);
  for (const id of live_rows.keys()) if (!retained.has(id)) live_rows.delete(id);
  for (const row of snapshot.rows) live_rows.set(row.id, row);
  sequence = snapshot.sequence; latest_snapshot = snapshot; display_snapshot();
}
async function stream() {
  while (true) {
    try {
      const response = await fetch('/api/live', {headers: {Authorization: 'Bearer ' + token, 'X-Savior-Cursor': String(sequence)}, cache: 'no-store'});
      if (!response.ok) throw Error(response.status === 403 ? 'Reopen the inspector launch URL.' : 'Live stream unavailable.');
      $('connection').textContent = 'Live · same profile'; $('connection').className = 'connected';
      const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = '';
      try {
        while (true) {
          const {value, done} = await reader.read(); if (done) throw Error('Connection closed.');
          buffer += decoder.decode(value, {stream: true}); let boundary;
          while ((boundary = buffer.indexOf('\n\n')) >= 0) { const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2); if (frame.startsWith('data: ')) accept_snapshot(JSON.parse(frame.slice(6))); }
        }
      } finally { await reader.cancel().catch(() => {}); }
    } catch (error) {
      $('connection').className = ''; $('connection').textContent = 'Disconnected · reconnecting'; $('coverage').textContent = 'Live coverage may be incomplete. ' + error.message;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }
}
async function refresh_state() {
  state = await api('status'); $('status').textContent = `${state.events} saved events · full-text recording ${state.full_text ? 'on' : 'off'} · no remote telemetry`;
  $('recording-state').textContent = state.full_text ? 'Full-text recording is on.' : 'Full-text recording is off. Live viewing does not save prompt text.';
  $('enable').disabled = state.full_text; $('disable').disabled = !state.full_text;
}
async function refresh_history() { await refresh_state(); rows = await api('events', {before, after_id, outcome: $('outcome').value}); render(); }
async function change_view(next) {
  view = next; selected = null; $('outcome').value = ''; $('search').value = '';
  for (const button of document.querySelectorAll('[data-view]')) { if (button.dataset.view === view) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current'); }
  $('live-panel').hidden = view !== 'live'; $('history-panel').hidden = view !== 'history'; $('settings-panel').hidden = view !== 'settings'; $('activity-panel').hidden = view === 'settings';
  if (view === 'history') { before = Number.MAX_SAFE_INTEGER; after_id = ''; await refresh_history(); }
  else if (view === 'live') { rows = []; display_snapshot(); render(); }
  else await refresh_state();
}
for (const button of document.querySelectorAll('[data-view]')) button.onclick = () => run(() => change_view(button.dataset.view));
$('pause').onclick = () => { paused = !paused; $('pause').textContent = paused ? 'Resume display' : 'Pause display'; if (!paused) display_snapshot(); };
$('follow').onchange = display_snapshot; $('search').oninput = render;
$('outcome').onchange = () => run(() => view === 'history' ? refresh_history() : render());
$('refresh').onclick = () => run(async () => { before = Number.MAX_SAFE_INTEGER; after_id = ''; await refresh_history(); });
$('older').onclick = () => run(async () => { if (rows.length) { before = rows.at(-1).timestamp_ms; after_id = rows.at(-1).id; } await refresh_history(); });
$('enable').onclick = () => run(async () => { if (confirm('Save original prompts and replacements locally? They may contain secrets.')) { await api('consent', {enabled: true}); await refresh_state(); } });
$('disable').onclick = () => run(async () => { await api('consent', {enabled: false}); await refresh_state(); });
$('delete').onclick = () => run(async () => { if (confirm('Delete retained records and annotations, and disable recording? Exported copies are unaffected.')) { await api('delete', {confirm: true}); await refresh_state(); } });
$('export').onclick = () => run(async () => {
  if (!confirm('Export potentially sensitive prompts? This copy will not expire or be deleted by Savior.')) return;
  const response = await fetch('/api/export', {headers: {Authorization: 'Bearer ' + token}, cache: 'no-store'}); if (!response.ok) throw Error('export_failed');
  const url = URL.createObjectURL(await response.blob()), link = element('a'); link.href = url; link.download = 'savior-replacements.parquet'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
async function save_preference(enabled) { await api('consent', {enabled}); $('onboarding').close(); await refresh_state(); }
$('history-preference').onsubmit = event => { event.preventDefault(); run(() => save_preference(new FormData(event.currentTarget).get('history') === 'yes')); };
$('dismiss-onboarding').onclick = () => run(() => save_preference(false));
$('onboarding').addEventListener('cancel', event => { event.preventDefault(); run(() => save_preference(false)); });
render(); run(async () => { await refresh_state(); if (!state.onboarding_complete) $('onboarding').showModal(); }); stream();
