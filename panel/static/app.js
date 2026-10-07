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
let runMarks = { running: -1, done: 0 };   // survives re-renders

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
  input.className = 'tile input';
  input.innerHTML = `<div class="ico"><i data-lucide="file-audio"></i></div>
    <div><div class="title">Input</div><div class="sub">${inputName ? inputName : 'Click to choose an audio file'}</div></div>
    <div class="acts"><button class="iconbtn" title="Choose file"><i data-lucide="folder-open"></i></button></div>`;
  input.onclick = browse;
  root.appendChild(input);

  pipeline.forEach((t, i) => {
    root.appendChild(connector(i));
    const s = STEPS[t.type];
    const el = document.createElement('div');
    el.className = `tile ${s.group}` + (i === runMarks.running ? ' running' : '') + (i < runMarks.done ? ' done' : '');
    el.draggable = true; el.dataset.uid = t.uid;
    el.innerHTML = `<div class="ico"><i data-lucide="${s.icon}"></i></div>
      <div style="min-width:0"><div class="title"><span class="num">${i + 1}</span>${s.short}</div><div class="sub">${summary(t)}</div></div>
      <div class="acts">
        <button class="iconbtn edit" title="Settings"><i data-lucide="${t.open ? 'chevron-up' : 'settings-2'}"></i></button>
        <button class="iconbtn del" title="Remove from pipeline"><i data-lucide="trash-2"></i></button></div>`;
    el.querySelector('.del').onclick = (e) => { e.stopPropagation(); pipeline = pipeline.filter((x) => x.uid !== t.uid); runMarks = { running: -1, done: 0 }; persist(); render(); };
    const toggle = (e) => { e.stopPropagation(); t.open = !t.open; render(); };
    el.querySelector('.edit').onclick = toggle;
    el.addEventListener('click', (e) => { if (!e.target.closest('.params')) toggle(e); });
    el.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/plain', JSON.stringify({ src: 'pipe', uid: t.uid })); el.classList.add('dragging'); });
    el.addEventListener('dragend', () => el.classList.remove('dragging'));
    if (t.open) el.appendChild(paramsForm(t, i));
    root.appendChild(el);
  });
  root.appendChild(connector(pipeline.length, true));
  if (!pipeline.length) {
    const e = document.createElement('div'); e.className = 'empty-pipe';
    e.textContent = 'Drag steps here from the inventory, or click an inventory tile to append it.';
    root.appendChild(e);
  }
  icons();
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
      t.params[p.key] = v; persist();
      inp.closest('.tile').querySelector('.sub').textContent = summary(t);
    };
    lab.appendChild(inp); f.appendChild(lab);
  }
  const rst = document.createElement('button'); rst.className = 'link'; rst.textContent = 'Reset to defaults';
  rst.onclick = () => { t.params = {}; persist(); render(); };
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
  let idx = insertIndexAt(e.clientY);
  if (d.src === 'inv') pipeline.splice(idx, 0, makeTile(d.type));
  else {
    const from = pipeline.findIndex((t) => t.uid === d.uid); if (from < 0) return;
    const [t] = pipeline.splice(from, 1); if (from < idx) idx--; pipeline.splice(idx, 0, t);
  }
  persist(); render();
});
document.addEventListener('dragend', () => markDrop(null));

// ---------------------------------------------------------------- inventory
function buildInventory() {
  for (const s of Object.values(STEPS)) {
    const b = document.createElement('button'); b.className = `inv ${s.group}`; b.draggable = true;
    b.innerHTML = `<i data-lucide="${s.icon}"></i>`;
    b.addEventListener('dragstart', (e) => { hideTip(); e.dataTransfer.setData('text/plain', JSON.stringify({ src: 'inv', type: s.id })); });
    b.onclick = () => { pipeline.push(makeTile(s.id)); persist(); render(); toast(`Added “${s.short}” to the end of the pipeline`); };
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
    : `Pipeline output · ${v.name}`;
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
  if (!sel) return;
  const r = await api('/api/cut', { start: sel.start, end: sel.end });
  if (r.ok) { loadView(r.view); toast('Working input trimmed to the selection'); }
};
$('#reset').onclick = async () => { const r = await api('/api/reset', {}); if (r.ok) loadView(r.view); };
$('#save').onclick = async () => {
  if (!currentView) return;
  const r = await api('/api/save', { kind: currentView.kind, start: sel ? sel.start : null, end: sel ? sel.end : null });
  if (r.ok) toast(`Saved ${r.path}`);
};
$('#viewInput').onclick = async () => { const r = await api('/api/view/input'); if (r.ok) loadView(r.view); };
$('#viewOutput').onclick = async () => { const r = await api('/api/view/output'); if (r.ok) loadView(r.view); };

async function browse() {
  const r = await api('/api/browse', {});
  if (!r.ok) return;
  inputName = r.view.name; render(); loadView(r.view);
  $('#viewOutput').disabled = true;
}

// ---------------------------------------------------------------- run
$('#run').onclick = async () => {
  if (!inputName) { toast('Choose an input file first (click the Input tile)', true); return; }
  if (!pipeline.length) { toast('The pipeline is empty', true); return; }
  for (const [i, t] of pipeline.entries())
    for (const p of STEPS[t.type].params)
      if (p.kind === 'ref') {
        const v = paramVal(t, p);
        if (v !== 'input' && pipeline.findIndex((x) => x.uid === v) >= i) {
          toast(`Step ${i + 1} (${STEPS[t.type].short}) mixes with a step that is not before it`, true); return;
        }
      }
  const body = { save_all: $('#saveAll').checked, pipeline: pipeline.map(({ uid, type, params }) => ({ uid, type, params })) };
  const r = await api('/api/run', body);
  if (!r.ok) { toast(r.error, true); return; }
  runMarks = { running: -1, done: 0 }; document.querySelectorAll('.tile').forEach((el) => el.classList.remove('done', 'running'));
  $('#run').disabled = true; $('#cancel').disabled = false;
  poll();
};
$('#cancel').onclick = () => api('/api/cancel', {});
$('#openFolder').onclick = () => api('/api/open_folder', {});
$('#resetPipe').onclick = () => { loadDefault(); render(); };
$('#clearPipe').onclick = () => { pipeline = []; persist(); render(); };

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
    runMarks = { running: s.running ? s.step - 1 : -1, done: s.running || s.error ? s.step - 1 : tiles.length };
    tiles.forEach((el, i) => { el.classList.toggle('running', i === runMarks.running); el.classList.toggle('done', i < runMarks.done); });
    if (!s.running) {
      clearInterval(polling); $('#run').disabled = false; $('#cancel').disabled = true;
      if (s.error) { toast(s.error === 'cancelled' ? 'Run cancelled' : s.error.slice(0, 600), s.error !== 'cancelled'); return; }
      $('#viewOutput').disabled = false; $('#openFolder').disabled = false;
      toast('Done — showing the output in the ribbon');
      const v = await api('/api/view/output'); if (v.ok) loadView(v.view);
    }
  }, 700);
}

// ---------------------------------------------------------------- boot
(async () => {
  const r = await api('/api/steps');
  STEPS = Object.fromEntries(r.steps.map((s) => [s.id, s])); DEFAULT = r.default; OVERRIDES = r.overrides;
  buildInventory();
  loadDefault();                     // the page always opens with the full default pipeline
  inputName = r.input; render();
  if (r.input) { const v = await api('/api/view/input'); if (v.ok) loadView(v.view); }
  const p = await api('/api/progress');
  if (p.running) { $('#run').disabled = true; $('#cancel').disabled = false; poll(); }
  icons();
})();
