"""Audio-enhancer control panel: local web UI + API.  Run:  .venv/Scripts/python.exe panel/server.py  -> http://127.0.0.1:8765"""
import json, os, re, subprocess, sys, time
import numpy as np
from flask import Flask, jsonify, request, send_file, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine

ROOT = engine.ROOT
app = Flask(__name__, static_folder='static', static_url_path='/static')
S = dict(path=None, start=0.0, end=0.0, full=0.0, job=None, out_dir=None, final=None)   # single-user session state


def dialog(kind, **kw):
    """Native Windows file dialog, run in a separate process so Tk owns its own main thread."""
    code = ("import sys, json, tkinter as tk, tkinter.filedialog as fd\n"
            "a = json.loads(sys.argv[1]); r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
            f"p = fd.{'askopenfilename' if kind == 'open' else 'asksaveasfilename'}(parent=r, **a)\n"
            "print(p or '')")
    r = subprocess.run([sys.executable, '-c', code, json.dumps(kw)], capture_output=True, text=True, encoding='utf-8')
    return r.stdout.strip()


def probe_duration(path):
    err = subprocess.run([engine.FF, '-hide_banner', '-i', path], capture_output=True, text=True, encoding='utf-8', errors='replace').stderr
    h, m, s = re.search(r'Duration: (\d+):(\d+):([\d.]+)', err).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def probe_rate(path):
    err = subprocess.run([engine.FF, '-hide_banner', '-i', path], capture_output=True, text=True, encoding='utf-8', errors='replace').stderr
    m = re.search(r'Audio:.*?(\d+) Hz', err)
    return int(m.group(1)) if m else 44100


def peaks(path, start=0.0, dur=None, n=6000):
    """Min/max pairs for the waveform ribbon, decoded at 4 kHz mono (fast even for hour-long files)."""
    cmd = [engine.FF, '-hide_banner', '-loglevel', 'error', '-ss', str(start)] + (['-t', str(dur)] if dur else []) + \
          ['-i', path, '-ac', '1', '-ar', '4000', '-f', 'f32le', '-']
    x = np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.float32)
    if len(x) == 0: return []
    k = max(1, len(x) // n); x = x[:len(x) // k * k].reshape(-1, k)
    out = np.empty(2 * len(x), np.float32); out[0::2] = x.max(1); out[1::2] = x.min(1)
    return np.round(out, 4).tolist()


def preview_wav(src, start, dur, tag):
    """16-bit preview of a section for browser playback (float WAV and cut sections aren't directly streamable)."""
    d = os.path.join(engine.CACHE, 'preview'); os.makedirs(d, exist_ok=True)
    st = os.stat(src)
    key = engine.hashlib.sha1(f"{src}|{st.st_size}|{st.st_mtime}|{start}|{dur}".encode()).hexdigest()[:12]
    out = os.path.join(d, f"{tag}_{key}.wav")
    if os.path.exists(out):
        return out
    for f in os.listdir(d):                                   # keep only the latest preview per tag
        if f.startswith(tag + '_'): os.remove(os.path.join(d, f))
    subprocess.run([engine.FF, '-hide_banner', '-loglevel', 'error', '-y', '-ss', str(start), '-t', str(dur), '-i', src,
                    '-map', '0:a:0', '-c:a', 'pcm_s16le', out], check=True)
    return out


def spectrogram_png(src, start, dur, w=2400, h=300):
    """Spectrogram image (ffmpeg showspectrumpic, mono, linear frequency 0..Nyquist), cached per source + section."""
    d = os.path.join(engine.CACHE, 'spec'); os.makedirs(d, exist_ok=True)
    st = os.stat(src)
    flt = f"showspectrumpic=s={w}x{h}:mode=combined:color=magma:scale=log:fscale=lin:legend=0:drange=120:gain=2"
    out = os.path.join(d, engine.hashlib.sha1(f"{src}|{st.st_size}|{st.st_mtime}|{start}|{dur}|{flt}".encode()).hexdigest()[:16] + '.png')
    if not os.path.exists(out):
        cut = ['-ss', str(start)] + (['-t', str(dur)] if dur else [])
        subprocess.run([engine.FF, '-hide_banner', '-loglevel', 'error', '-y', *cut, '-i', src, '-filter_complex',
                        f"[0:a:0]aformat=channel_layouts=mono,{flt}[s]", '-map', '[s]', '-frames:v', '1', out], check=True)
    return out


def view(kind):
    if kind == 'output':
        if not S['final']: return None
        sr = engine.info(S['final'])[0]
        return dict(kind='output', name=os.path.basename(S['final']), duration=engine.info(S['final'])[2], nyquist=sr / 2,
                    peaks=peaks(S['final']), url=f"/api/audio/output?t={time.time()}", spec=f"/api/spectrogram/output?t={time.time()}")
    if not S['path']: return None
    dur = S['end'] - S['start']
    return dict(kind='input', name=os.path.basename(S['path']), duration=dur, full=S['full'], start=S['start'], nyquist=S['sr'] / 2,
                peaks=peaks(S['path'], S['start'], dur if S['end'] < S['full'] else None), url=f"/api/audio/input?t={time.time()}",
                spec=f"/api/spectrogram/input?t={time.time()}")


@app.get('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.get('/api/steps')
def steps():
    return jsonify(steps=engine.registry(), default=engine.DEFAULT_PIPELINE, overrides=engine.DEFAULT_OVERRIDES,
                   input=os.path.basename(S['path']) if S['path'] else None)


@app.post('/api/browse')
def browse():
    p = dialog('open', title='Choose input audio', initialdir=os.path.join(ROOT, 'raw'),
               filetypes=[['Audio', '*.wav *.flac *.mp3 *.m4a *.ogg *.opus *.aac *.mp4 *.mkv *.mov'], ['All files', '*.*']])
    if not p: return jsonify(ok=False)
    d = probe_duration(p)
    S.update(path=p, start=0.0, end=d, full=d, final=None, sr=probe_rate(p))
    return jsonify(ok=True, view=view('input'))


@app.post('/api/load')
def load():
    """Set the input by path (scripting/testing; the UI uses the native dialog)."""
    p = os.path.abspath(request.json['path'])
    if not os.path.isfile(p): return jsonify(ok=False, error='file not found')
    d = probe_duration(p)
    S.update(path=p, start=0.0, end=d, full=d, final=None, sr=probe_rate(p))
    return jsonify(ok=True, view=view('input'))


@app.get('/api/view/<kind>')
def get_view(kind):
    v = view(kind)
    return jsonify(ok=v is not None, view=v)


@app.get('/api/audio/<kind>')
def audio(kind):
    if kind == 'output':
        return send_file(preview_wav(S['final'], 0, engine.info(S['final'])[2], 'output'), conditional=True)
    if S['start'] == 0 and S['end'] >= S['full'] and os.path.splitext(S['path'])[1].lower() in ('.mp3', '.flac', '.wav', '.ogg', '.m4a'):
        return send_file(S['path'], conditional=True)
    return send_file(preview_wav(S['path'], S['start'], S['end'] - S['start'], 'input'), conditional=True)


@app.get('/api/spectrogram/<kind>')
def spectrogram(kind):
    if kind == 'output':
        return send_file(spectrogram_png(S['final'], 0, None), mimetype='image/png')
    whole = S['start'] == 0 and S['end'] >= S['full']
    return send_file(spectrogram_png(S['path'], S['start'], None if whole else S['end'] - S['start']), mimetype='image/png')


@app.post('/api/cut')
def cut():
    a, b = float(request.json['start']), float(request.json['end'])
    S.update(start=S['start'] + a, end=min(S['start'] + b, S['full']))
    return jsonify(ok=True, view=view('input'))


@app.post('/api/reset')
def reset():
    S.update(start=0.0, end=S['full'])
    return jsonify(ok=True, view=view('input'))


@app.post('/api/save')
def save():
    j = request.json
    if j['kind'] == 'output':
        src, base = S['final'], 0.0
    else:
        src, base = S['path'], S['start']
    a = j.get('start'); b = j.get('end')
    stem = os.path.splitext(os.path.basename(src))[0]
    p = dialog('save', title='Save audio as', initialdir=os.path.join(ROOT, 'out'), defaultextension='.wav',
               initialfile=f"{stem}_{'sel' if a is not None else j['kind']}.wav",
               filetypes=[['WAV', '*.wav'], ['FLAC', '*.flac'], ['MP3', '*.mp3']])
    if not p: return jsonify(ok=False)
    cutargs = ['-ss', str(base + (a or 0))] + (['-t', str(b - a)] if a is not None else
                                               (['-t', str(S['end'] - S['start'])] if j['kind'] == 'input' else []))
    codec = {'.mp3': ['-c:a', 'libmp3lame', '-b:a', '192k'], '.flac': ['-c:a', 'flac']}.get(os.path.splitext(p)[1].lower(), ['-c:a', 'pcm_s24le'])
    subprocess.run([engine.FF, '-hide_banner', '-loglevel', 'error', '-y', *cutargs, '-i', src, '-map', '0:a:0', *codec, p], check=True)
    return jsonify(ok=True, path=p)


@app.post('/api/run')
def run():
    if not S['path']: return jsonify(ok=False, error='choose an input file first')
    if S['job'] and S['job'].state['running']: return jsonify(ok=False, error='a run is already in progress')
    j = request.json
    stem = os.path.splitext(os.path.basename(S['path']))[0]
    sect = '' if S['start'] == 0 and S['end'] >= S['full'] else f"_{int(S['start'])}s-{int(S['end'])}s"
    S['out_dir'] = os.path.join(ROOT, 'out', 'panel', stem, time.strftime('%Y%m%d-%H%M%S') + sect)
    S['job'] = engine.Job(dict(path=S['path'], start=S['start'], end=S['end']), j['pipeline'], S['out_dir'], bool(j.get('save_all')))
    return jsonify(ok=True)


@app.get('/api/progress')
def progress():
    job = S['job']
    if not job: return jsonify(running=False, message='Idle', fraction=0, step=0, n=0, log=[])
    st = dict(job.state); st['elapsed'] = time.time() - st['started']
    if st.get('final'): S['final'] = st['final']
    return jsonify(st)


@app.post('/api/cancel')
def cancel():
    if S['job']: S['job'].cancel()
    return jsonify(ok=True)


@app.post('/api/open_folder')
def open_folder():
    if S['out_dir'] and os.path.isdir(S['out_dir']): os.startfile(S['out_dir'])
    return jsonify(ok=True)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8765))
    print(f"Control panel: http://127.0.0.1:{port}")
    app.run(host='127.0.0.1', port=port, threaded=True, debug=False)
