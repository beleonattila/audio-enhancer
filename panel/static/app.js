import WaveSurfer from 'https://unpkg.com/wavesurfer.js@7.8.11/dist/wavesurfer.esm.js';
import RegionsPlugin from 'https://unpkg.com/wavesurfer.js@7.8.11/dist/plugins/regions.esm.js';

const $ = (s) => document.querySelector(s);
const api = async (path, body) => {
  const r = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return r.json();
};
const icons = () => window.lucide && window.lucide.createIcons();
const fmt = (t) => { t = Math.max(0, t || 0); const m = Math.floor(t / 60); return `${m}:${(t - m * 60).toFixed(1).padStart(4, '0')}`; };
const uid = () => Math.random().toString(36).slice(2, 9);

let STEPS = {}, DEFAULT = [], OVERRIDES = {};
let pipeline = [];                 // [{uid, type, params, open}]
let inputName = null;
let ws = null, regions = null, sel = null, currentView = null;
let polling = null;
let runMarks = { running: -1 };     // tile index currently running (survives re-renders)
let cachedSteps = {};               // uid -> {key, ready}: step results available for the current input + settings
let jobRunning = false;
let activeUid = null;                    // tile explained in the "Active step" box ('input' for the input tile)
let INPUT_HELP = {};
let shown = { uid: null, index: -1 };   // pipeline tile whose temp result is in the ribbon (kind 'step')
let outputKey = null;                    // cache key of the last step of the pipeline that produced the Output view

function toast(msg, err = false) {
  const t = $('#toast'); t.textContent = msg; t.className = 'toast show' + (err ? ' err' : '');
  clearTimeout(toast.h); toast.h = setTimeout(() => (t.className = 'toast'), err ? 7000 : 4000);
}

// ---------------------------------------------------------------- pipeline model
function makeTile(type, params = {}) { return { uid: uid(), type, params: { ...params }, open: false }; }

function loadDefault() {
  pipeline = DEFAULT.map((t) => makeTile(t, OVERRIDES[t] || {}));
  for (const t of pipeline)              // '#k' references -> uid of the k-th step
    for (const [k, v] of Object.entries(t.params))
      if (typeof v === 'string' && v.startsWith('#')) t.params[k] = pipeline[+v.slice(1) - 1]?.uid || 'input';
  persist();
}
// Any change to the pipeline or the input invalidates what we know: drop all ready marks immediately (so a stale ✓ can
// never show on a tile that moved), reset the progress bar, re-check results with the server, and if the ribbon was
// showing a result that is no longer valid, stop playback and fall back to the last still-valid step (or the input).
function changed() {
  persist();
  cachedSteps = {};
  if (!jobRunning) resetProgress('Idle · pipeline changed');
  render();
  refreshCached(true);
}
function locked() {
  if (jobRunning) toast('The pipeline is locked while a run is in progress (Cancel to edit)', true);
  return jobRunning;
}
function resetProgress(text = 'Idle') {
  const f = $('#barFill'); f.style.width = '0'; f.classList.remove('indet');
  $('#progText').textContent = text;
}
let refreshTimer = null, refreshSeq = 0;
function refreshCached(validate = false) {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => {
    const seq = ++refreshSeq;
    if (!inputName) { cachedSteps = {}; render(); return; }
    const r = await api('/api/cached', { pipeline: pipeline.map(({ uid, type, params }) => ({ uid, type, params })) });
    if (seq !== refreshSeq) return;                         // a newer check is on its way
    cachedSteps = r.steps || {};
    render();
    if (validate) await validateView();
  }, 120);
}
async function validateView() {
  if (!currentView) return;
  if (currentView.kind === 'step') {
    const i = pipeline.findIndex((t) => t.uid === shown.uid);
    const c = i >= 0 ? cachedSteps[shown.uid] : null;
    if (c && c.ready && c.key === currentView.key) {
      if (i !== shown.index) await showStep(pipeline[i], i, true);    // still valid, only its number changed
      return;
    }
    await fallback(i >= 0 ? i : shown.index, 'The result shown in the ribbon no longer matches the pipeline');
  } else if (currentView.kind === 'output') {
    const last = pipeline[pipeline.length - 1], c = last && cachedSteps[last.uid];
    if (!(c && c.key === outputKey)) {
      $('#viewOutput').disabled = true;
      await fallback(pipeline.length, 'The output no longer matches the pipeline');
    }
  }
}
async function fallback(upto, why) {
  if (ws) ws.pause();
  for (let j = Math.min(upto, pipeline.length) - 1; j >= 0; j--) {
    const c = cachedSteps[pipeline[j].uid];
    if (c && c.ready) { await showStep(pipeline[j], j); toast(`${why} — showing the result after step ${j + 1}`); return; }
  }
  await showInput(); toast(`${why} — showing the input`);
}
function persist() { try { localStorage.setItem('pipeline', JSON.stringify(pipeline.map(({ open, ...t }) => t))); } catch {} }
function restore() {
  try {
    const p = JSON.parse(localStorage.getItem('pipeline') || 'null');
    if (Array.isArray(p) && p.every((t) => STEPS[t.type])) { pipeline = p.map((t) => ({ ...t, open: false })); return true; }
  } catch {}
  return false;
}
const paramVal = (t, p) => (t.params[p.key] ?? p.default);

function summary(t) {
  const s = STEPS[t.type];
  return s.params.map((p) => {
    const v = paramVal(t, p);
    if (p.kind === 'ref') return `with ${refLabel(v)}`;
    return `${p.label.replace(/\s*\(.*\)/, '')}: ${v}`;
  }).join(' · ') || s.name;
}
function refLabel(v) {
  if (!v || v === 'input') return 'input';
  const i = pipeline.findIndex((t) => t.uid === v);
  return i < 0 ? '⚠ removed step' : `${i + 1} · ${STEPS[pipeline[i].type].short}`;
}

// ---------------------------------------------------------------- render pipeline
function render() {
  const root = $('#pipeline'); root.innerHTML = '';
  const input = document.createElement('div');
  const showingInput = currentView?.kind === 'input';
  input.className = 'tile input' + (showingInput ? ' showing' : '') + (activeUid === 'input' ? ' active' : '');
  input.innerHTML = `<div class="ico"><i data-lucide="file-audio"></i></div>
    <div><div class="title">Input${showingInput ? '<span class="tag">in ribbon</span>' : ''}</div>
      <div class="sub">${inputName ? inputName : 'Click to choose an audio file'}</div></div>
    <div class="acts">
      <button class="iconbtn restore" title="Show the original input in the ribbon" ${inputName && !showingInput ? '' : 'disabled'}><i data-lucide="undo-2"></i></button>
      <button class="iconbtn" title="Choose file"><i data-lucide="folder-open"></i></button></div>`;
  input.onclick = () => { setActive('input'); browse(); };
  input.querySelector('.restore').onclick = (e) => { e.stopPropagation(); setActive('input'); showInput(); };
  root.appendChild(input);

  pipeline.forEach((t, i) => {
    root.appendChild(connector(i));
    const s = STEPS[t.type];
    const el = document.createElement('div');
    const c = cachedSteps[t.uid], ready = !!(c && c.ready);
    const showing = ready && currentView?.kind === 'step' && currentView.key === c.key;
    el.className = `tile ${s.group}` + (i === runMarks.running ? ' running' : '') + (ready ? ' done' : '') + (showing ? ' showing' : '')
      + (activeUid === t.uid ? ' active' : '');
    el.draggable = !jobRunning; el.dataset.uid = t.uid;
    if (ready) el.title = 'Click to show the result after this step in the ribbon';
    el.innerHTML = `<div class="ico"><i data-lucide="${s.icon}"></i></div>
      <div style="min-width:0"><div class="title"><span class="num">${i + 1}</span>${s.short}${showing ? '<span class="tag">in ribbon</span>' : ''}</div>
        <div class="sub">${summary(t)}</div></div>
      <div class="acts">
        <button class="iconbtn runto" ${ready || jobRunning || !inputName ? 'disabled' : ''}
          title="${ready ? 'Result ready: click the tile to show it' : 'Run the pipeline up to this step and show the result'}"><i data-lucide="play"></i></button>
        <button class="iconbtn edit" title="Settings"><i data-lucide="${t.open ? 'chevron-up' : 'settings-2'}"></i></button>
        <button class="iconbtn del" title="Remove from pipeline"><i data-lucide="trash-2"></i></button></div>`;
    el.querySelector('.del').onclick = (e) => { e.stopPropagation(); if (locked()) return; pipeline = pipeline.filter((x) => x.uid !== t.uid); changed(); };
    const toggle = (e) => { e.stopPropagation(); activeUid = t.uid; t.open = !t.open; render(); };
    el.querySelector('.edit').onclick = toggle;
    el.querySelector('.runto').onclick = (e) => { e.stopPropagation(); setActive(t.uid); runTo(i); };
    el.addEventListener('click', (e) => {
      if (e.target.closest('.params')) return;
      if (ready) { e.stopPropagation(); setActive(t.uid); showStep(t, i); } else toggle(e);
    });
    el.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/plain', JSON.stringify({ src: 'pipe', uid: t.uid })); el.classList.add('dragging'); });
    el.addEventListener('dragend', () => el.classList.remove('dragging'));
    if (t.open) el.appendChild(paramsForm(t, i));
    root.appendChild(el);
  });
  root.appendChild(connector(pipeline.length, true));
  renderStepHelp();
  if (!pipeline.length) {
    const e = document.createElement('div'); e.className = 'empty-pipe';
    e.textContent = 'Drag steps here from the inventory, or click an inventory tile to append it.';
    root.appendChild(e);
  }
  icons();
}

function setActive(uid) { if (activeUid !== uid) { activeUid = uid; render(); } }
const esc = (x) => String(x).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
function renderStepHelp() {
  const box = $('#stepHelp');
  if (activeUid === 'input') {
    box.className = 'help-card step-help i';
    box.innerHTML = `<h4><span class="ico"><i data-lucide="file-audio"></i></span>Input</h4><div>${esc(INPUT_HELP.desc || '')}</div>
      <p class="where"><b>Position:</b> ${esc(INPUT_HELP.where || '')}</p>`;
    return;
  }
  const i = pipeline.findIndex((t) => t.uid === activeUid);
  if (i < 0) {
    activeUid = null; box.className = 'help-card step-help';
    box.innerHTML = '<p class="lead">Click a tile in the pipeline to see what it does and what its settings mean.</p>';
    return;
  }
  const t = pipeline[i], s = STEPS[t.type];
  const params = s.params.map((p) => {
    const v = paramVal(t, p), shown = p.kind === 'ref' ? refLabel(v) : v;
    const dflt = p.kind === 'ref' ? '' : ` · default ${p.default}`;
    return `<dt>${esc(p.label)}<span class="val">now <b>${esc(shown)}</b>${dflt}</span></dt><dd>${esc(p.help || '')}</dd>`;
  }).join('');
  box.className = `help-card step-help ${s.group === 'neural' ? 'm' : 'c'}`;
  box.innerHTML = `<h4><span class="ico"><i data-lucide="${s.icon}"></i></span>${i + 1} · ${esc(s.short)}</h4>
    <div>${esc(s.desc || s.name)}</div>
    <p class="where"><b>Position:</b> ${esc(s.where || '')}</p>
    ${params ? `<dl>${params}</dl>` : '<p class="where">No settings.</p>'}`;
}

function connector(index, last = false) {
  const c = document.createElement('div'); c.className = 'connector'; c.dataset.index = index;
  if (last && !pipeline.length) c.style.height = '10px';
  return c;
}

function paramsForm(t, idx) {
  const s = STEPS[t.type];
  const f = document.createElement('div'); f.className = 'params';
  f.onclick = (e) => e.stopPropagation(); f.draggable = false;
  f.addEventListener('dragstart', (e) => { e.preventDefault(); e.stopPropagation(); });
  if (!s.params.length) { f.innerHTML = `<div class="none">${s.name} — no settings.</div>`; return f; }
  for (const p of s.params) {
    const lab = document.createElement('label'); lab.textContent = p.label;
    let inp;
    if (p.kind === 'select' || p.kind === 'ref') {
      inp = document.createElement('select');
      const opts = p.kind === 'ref'
        ? [['input', 'Input (original)'], ...pipeline.slice(0, idx).map((x, j) => [x.uid, `${j + 1} · ${STEPS[x.type].short}`])]
        : p.options.map((o) => [o, String(o)]);
      for (const [v, l] of opts) { const o = document.createElement('option'); o.value = v; o.textContent = l; inp.appendChild(o); }
      inp.value = String(paramVal(t, p));
    } else {
      inp = document.createElement('input'); inp.type = 'number';
      Object.assign(inp, { min: p.min ?? '', max: p.max ?? '', step: p.step ?? 'any', value: paramVal(t, p) });
    }
    inp.onchange = () => {
      let v = inp.value;
      if (p.kind === 'number') v = parseFloat(v);
      else if (p.kind === 'select' && typeof p.default === 'number') v = parseFloat(v);
      t.params[p.key] = v;
      changed();
    };
    inp.disabled = jobRunning;
    lab.appendChild(inp); f.appendChild(lab);
  }
  const rst = document.createElement('button'); rst.className = 'link'; rst.textContent = 'Reset to defaults';
  rst.onclick = () => { if (locked()) return; t.params = {}; changed(); };
  f.appendChild(rst);
  return f;
}

// drag & drop into the pipeline
function insertIndexAt(y) {
  const tiles = [...$('#pipeline').querySelectorAll('.tile:not(.input)')];
  let i = 0;
  for (const el of tiles) { const r = el.getBoundingClientRect(); if (y > r.top + r.height / 2) i++; }
  return i;
}
function markDrop(index) {
  document.querySelectorAll('.connector').forEach((c) => c.classList.toggle('drop', index !== null && +c.dataset.index === index));
}
const pipeEl = () => $('#pipeline');
document.addEventListener('dragover', (e) => {
  if (!e.target.closest('.pipeline-wrap')) { markDrop(null); return; }
  e.preventDefault(); markDrop(insertIndexAt(e.clientY));
});
document.addEventListener('drop', (e) => {
  if (!e.target.closest('.pipeline-wrap')) return;
  e.preventDefault(); markDrop(null);
  let d; try { d = JSON.parse(e.dataTransfer.getData('text/plain')); } catch { return; }
  if (locked()) return;
  let idx = insertIndexAt(e.clientY);
  if (d.src === 'inv') { const nt = makeTile(d.type); activeUid = nt.uid; pipeline.splice(idx, 0, nt); }
  else {
    const from = pipeline.findIndex((t) => t.uid === d.uid); if (from < 0) return;
    const [t] = pipeline.splice(from, 1); if (from < idx) idx--; pipeline.splice(idx, 0, t);
  }
  changed();
});
document.addEventListener('dragend', () => markDrop(null));

// ---------------------------------------------------------------- inventory
function buildInventory() {
  for (const s of Object.values(STEPS)) {
    const b = document.createElement('button'); b.className = `inv ${s.group}`; b.draggable = true;
    b.innerHTML = `<i data-lucide="${s.icon}"></i>`;
    b.addEventListener('dragstart', (e) => { hideTip(); e.dataTransfer.setData('text/plain', JSON.stringify({ src: 'inv', type: s.id })); });
    b.onclick = () => { if (locked()) return; const nt = makeTile(s.id); activeUid = nt.uid; pipeline.push(nt); changed(); toast(`Added “${s.short}” to the end of the pipeline`); };
    b.addEventListener('mouseenter', () => showTip(b, s));
    b.addEventListener('mouseleave', hideTip);
    (s.group === 'neural' ? $('#invNeural') : $('#invClassic')).appendChild(b);
  }
}
function showTip(el, s) {
  const t = $('#tooltip'); t.innerHTML = `<b>${s.short}</b>${s.name}`;
  const r = el.getBoundingClientRect();
  t.classList.add('show');
  const w = t.offsetWidth;
  t.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2))}px`;
  t.style.top = `${r.bottom + 8}px`;
}
function hideTip() { $('#tooltip').classList.remove('show'); }
window.addEventListener('scroll', hideTip, true);

// ---------------------------------------------------------------- waveform ribbon
function loadView(v) {
  currentView = v;
  if (ws) { ws.destroy(); ws = null; }
  setPlayIcon(false);
  $('#wave').innerHTML = '';
  sel = null; updateSel();
  loadSpec(v);
  if (!v) { $('#wave').innerHTML = '<div class="empty">Choose an input file to see its waveform</div>'; return; }
  ws = WaveSurfer.create({
    container: '#wave', height: 110, url: v.url, peaks: [Float32Array.from(v.peaks)], duration: v.duration,
    waveColor: '#1fb8d6', progressColor: '#ff2bd6', cursorColor: '#39ff88', cursorWidth: 2,
    barWidth: 2, barGap: 1, barRadius: 2, normalize: true, dragToSeek: false,
  });
  regions = ws.registerPlugin(RegionsPlugin.create());
  regions.enableDragSelection({ color: 'rgba(57,255,136,0.16)' });
  regions.on('region-created', (r) => { regions.getRegions().forEach((x) => x !== r && x.remove()); sel = r; updateSel(); });
  regions.on('region-updated', (r) => { sel = r; updateSel(); });
  regions.on('region-update', (r) => { sel = r; updateSel(); });
  regions.on('region-removed', (r) => { if (sel === r) { sel = null; updateSel(); } });
  ws.on('timeupdate', (t) => { $('#time').textContent = `${fmt(t)} / ${fmt(v.duration)}`; syncCursor(t); });
  ws.on('seeking', syncCursor);
  ws.on('ready', () => ($('#time').textContent = `${fmt(0)} / ${fmt(v.duration)}`));
  ws.on('play', () => setPlayIcon(true)); ws.on('pause', () => setPlayIcon(false));
  $('#viewInput').classList.toggle('active', v.kind === 'input');
  $('#viewOutput').classList.toggle('active', v.kind === 'output');
  $('#save').disabled = false;
  $('#reset').disabled = !(v.kind === 'input' && (v.start > 0 || v.duration < v.full - 0.05));
  $('#cutInfo').textContent = v.kind === 'input'
    ? (v.duration < v.full - 0.05 ? `Working section ${fmt(v.start)} – ${fmt(v.start + v.duration)} of ${fmt(v.full)} · the pipeline runs on this section` : `Whole file · ${fmt(v.full)}`)
    : v.kind === 'step' ? `Temp result · ${v.name}` : `Pipeline output · ${v.name}`;
  render();
}
// ---------------------------------------------------------------- spectrogram ribbon (server-rendered image)
function loadSpec(v) {
  const img = $('#specImg'), msg = $('#specMsg');
  img.style.display = 'none'; $('#specCursor').style.display = 'none'; $('#specAxis').innerHTML = '';
  if (!v) { msg.textContent = 'Spectrogram'; msg.style.display = ''; return; }
  msg.textContent = 'Rendering spectrogram…'; msg.style.display = '';
  img.onload = () => { img.style.display = 'block'; msg.style.display = 'none'; syncCursor(0); };
  img.onerror = () => { msg.textContent = 'Spectrogram unavailable'; };
  img.src = v.spec;
  const ny = v.nyquist || 22050, step = ny > 16000 ? 5000 : ny > 8000 ? 2000 : 1000;
  for (let f = 0; f <= ny; f += step) {
    if (f > 0 && f / ny > 0.97) continue;
    const sp = document.createElement('span'); sp.style.bottom = `${(f / ny) * 100}%`;
    sp.textContent = f === 0 ? '0' : `${f / 1000}k`;
    $('#specAxis').appendChild(sp);
  }
}
function syncCursor(t) {
  if (!currentView) return;
  const c = $('#specCursor'); c.style.display = 'block';
  const now = typeof t === 'number' ? t : (ws ? ws.getCurrentTime() : 0);
  c.style.left = `${(Math.min(now, currentView.duration) / currentView.duration) * 100}%`;
}
function syncSpecSel() {
  const o = $('#specSel');
  if (!sel || !currentView) { o.style.display = 'none'; return; }
  const d = currentView.duration;
  o.style.display = 'block'; o.style.left = `${(sel.start / d) * 100}%`; o.style.width = `${((sel.end - sel.start) / d) * 100}%`;
}
const timeAt = (el, e) => {
  const r = el.getBoundingClientRect();
  return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * (currentView ? currentView.duration : 0);
};
function clearSelection() { if (regions) regions.clearRegions(); sel = null; updateSel(); }

// a single click (no drag) outside the selected region cancels the selection, on both ribbons
function clickOutsideClears(el) {
  let down = null;
  el.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY, t: performance.now() }; }, true);
  el.addEventListener('pointerup', (e) => {
    if (!down) return;
    const click = Math.hypot(e.clientX - down.x, e.clientY - down.y) < 4 && performance.now() - down.t < 500;
    down = null;
    if (click && sel) { const t = timeAt(el, e); if (t < sel.start || t > sel.end) clearSelection(); }
  }, true);
}
clickOutsideClears($('#wave'));
clickOutsideClears($('#spec'));

// drag on the spectrogram selects like on the waveform; a click seeks
(() => {
  const el = $('#spec'); let start = null, region = null, moved = false, x0 = 0;
  el.addEventListener('pointerdown', (e) => {
    if (!ws || !currentView) return;
    start = timeAt(el, e); x0 = e.clientX; moved = false; region = null; el.setPointerCapture(e.pointerId);
  });
  el.addEventListener('pointermove', (e) => {
    if (start === null) return;
    if (!moved && Math.abs(e.clientX - x0) < 4) return;
    moved = true;
    const t = timeAt(el, e), a = Math.min(start, t), b = Math.max(start, t);
    if (!region) region = regions.addRegion({ start: a, end: b, color: 'rgba(57,255,136,0.16)', drag: true, resize: true });
    else region.setOptions({ start: a, end: b });
    sel = region; updateSel();
  });
  el.addEventListener('pointerup', (e) => {
    if (start !== null && !moved && ws) ws.setTime(timeAt(el, e));
    start = null;
  });
})();

function setPlayIcon(playing) { $('#play').innerHTML = `<i data-lucide="${playing ? 'pause' : 'play'}"></i>`; icons(); }
function updateSel() {
  const has = !!sel;
  $('#selInfo').textContent = has ? `Selection ${fmt(sel.start)} – ${fmt(sel.end)} (${(sel.end - sel.start).toFixed(1)} s)` : 'Drag on the waveform to select';
  $('#cut').disabled = !(has && currentView?.kind === 'input');
  $('#clearSel').disabled = !has;
  syncSpecSel();
}

$('#play').onclick = () => ws && ws.playPause();
document.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && !e.target.closest('input,select,textarea,button')) { e.preventDefault(); ws && ws.playPause(); }
});
$('#clearSel').onclick = clearSelection;
$('#cut').onclick = async () => {
  if (!sel || locked()) return;
  const r = await api('/api/cut', { start: sel.start, end: sel.end });
  if (r.ok) { outputKey = null; $('#viewOutput').disabled = true; loadView(r.view); changed(); toast('Working input trimmed to the selection'); }
};
$('#reset').onclick = async () => {
  if (locked()) return;
  const r = await api('/api/reset', {});
  if (r.ok) { outputKey = null; $('#viewOutput').disabled = true; loadView(r.view); changed(); }
};
$('#save').onclick = async () => {
  if (!currentView) return;
  const r = await api('/api/save', { kind: currentView.kind, key: currentView.key, start: sel ? sel.start : null, end: sel ? sel.end : null });
  if (r.ok) toast(`Saved ${r.path}`);
};
$('#viewInput').onclick = async () => { const r = await api('/api/view/input'); if (r.ok) loadView(r.view); };
$('#viewOutput').onclick = async () => { const r = await api('/api/view/output'); if (r.ok) loadView(r.view); };

async function browse() {
  const r = await api('/api/browse', {});
  if (!r.ok) return;
  inputName = r.view.name; outputKey = null; $('#viewOutput').disabled = true;
  loadView(r.view); changed();
}

async function showInput() {
  const r = await api('/api/view/input'); if (r.ok) loadView(r.view);
}
async function showStep(t, i, relabelOnly = false) {
  const c = cachedSteps[t.uid]; if (!c || !c.ready) return;
  const label = `after step ${i + 1} · ${STEPS[t.type].short}`;
  shown = { uid: t.uid, index: i };
  if (relabelOnly && currentView?.kind === 'step') {        // same audio, the tile only moved: keep playback going
    currentView.name = label; $('#cutInfo').textContent = `Temp result · ${label}`; render(); return;
  }
  const r = await api(`/api/view/step?key=${c.key}&label=${encodeURIComponent(label)}`);
  if (r.ok) loadView(r.view); else { toast('That result is no longer available, run the step again', true); refreshCached(); }
}

function checkRefs(upto) {
  for (const [i, t] of pipeline.slice(0, upto + 1).entries())
    for (const p of STEPS[t.type].params)
      if (p.kind === 'ref') {
        const v = paramVal(t, p);
        if (v !== 'input' && pipeline.findIndex((x) => x.uid === v) >= i) {
          toast(`Step ${i + 1} (${STEPS[t.type].short}) mixes with a step that is not before it`, true); return false;
        }
      }
  return true;
}
function startJob() {
  jobRunning = true; runMarks = { running: -1 };
  $('#run').disabled = true; $('#cancel').disabled = false; render(); poll();
}
async function runTo(i) {
  if (!inputName) { toast('Choose an input file first (click the Input tile)', true); return; }
  if (!checkRefs(i)) return;
  const r = await api('/api/run', { preview: true, pipeline: pipeline.slice(0, i + 1).map(({ uid, type, params }) => ({ uid, type, params })) });
  if (!r.ok) { toast(r.error, true); return; }
  startJob();
}

// ---------------------------------------------------------------- run
$('#run').onclick = async () => {
  if (!inputName) { toast('Choose an input file first (click the Input tile)', true); return; }
  if (!pipeline.length) { toast('The pipeline is empty', true); return; }
  if (!checkRefs(pipeline.length - 1)) return;
  const body = { save_all: $('#saveAll').checked, pipeline: pipeline.map(({ uid, type, params }) => ({ uid, type, params })) };
  const r = await api('/api/run', body);
  if (!r.ok) { toast(r.error, true); return; }
  startJob();
};
$('#cancel').onclick = () => api('/api/cancel', {});
$('#openFolder').onclick = () => api('/api/open_folder', {});
$('#resetPipe').onclick = () => { if (locked()) return; loadDefault(); changed(); };
$('#clearPipe').onclick = () => { if (locked()) return; pipeline = []; changed(); };

function poll() {
  clearInterval(polling);
  polling = setInterval(async () => {
    const s = await api('/api/progress');
    const n = s.n || 1, frac = s.running ? ((s.step - 1) + (s.fraction || 0)) / n : (s.error ? ((s.step - 1) + (s.fraction || 0)) / n : 1);
    const fill = $('#barFill'); fill.style.width = `${Math.max(2, frac * 100)}%`;
    fill.classList.toggle('indet', s.running && !s.fraction);
    const last = (s.log || []).slice(-1)[0];
    $('#progText').textContent = s.running
      ? `${s.message} · ${Math.round((s.fraction || 0) * 100)}% · ${fmt(s.elapsed)} elapsed${last ? '  —  ' + last : ''}`
      : s.message;
    const tiles = [...document.querySelectorAll('.tile:not(.input)')];
    const runningIdx = s.running ? s.step - 1 : -1;
    if (runningIdx !== runMarks.running) {
      runMarks = { running: runningIdx };
      tiles.forEach((el, i) => el.classList.toggle('running', i === runningIdx));
      if (s.running && s.step > 1) refreshCached();          // earlier steps just finished
    }
    if (!s.running) {
      clearInterval(polling); jobRunning = false; runMarks = { running: -1 };
      $('#run').disabled = false; $('#cancel').disabled = true;
      if (s.error) { render(); refreshCached(true); toast(s.error === 'cancelled' ? 'Run cancelled' : s.error.slice(0, 600), s.error !== 'cancelled'); return; }
      if (s.preview) {
        const i = pipeline.findIndex((t) => t.uid === s.target);
        cachedSteps[s.target] = { key: s.key, ready: true };
        if (i >= 0) await showStep(pipeline[i], i);
        refreshCached();
        toast(s.message);
        return;
      }
      outputKey = s.key;
      $('#viewOutput').disabled = false; $('#openFolder').disabled = false;
      toast('Done — showing the output in the ribbon');
      const v = await api('/api/view/output'); if (v.ok) loadView(v.view);
      refreshCached();
    }
  }, 700);
}

// ---------------------------------------------------------------- boot
(async () => {
  const r = await api('/api/steps');
  STEPS = Object.fromEntries(r.steps.map((s) => [s.id, s])); DEFAULT = r.default; OVERRIDES = r.overrides;
  INPUT_HELP = r.input_help || {};
  buildInventory();
  loadDefault();                     // the page always opens with the full default pipeline
  inputName = r.input; render();
  if (r.input) { const v = await api('/api/view/input'); if (v.ok) loadView(v.view); refreshCached(); }
  const p = await api('/api/progress');
  if (p.running) startJob();
  icons();
})();
