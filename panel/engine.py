"""Pipeline engine for the control panel: step registry, cached step execution with progress reporting.

Every step takes one audio file and produces one audio file. Results are cached in cache/panel/ under a key made
from the input (file + cut range) and the chain of steps/parameters up to that point, so re-running a pipeline
after changing a later step only recomputes from that step on.
"""
import hashlib, json, os, re, shutil, subprocess, sys, threading, time
import numpy as np, soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import enhance as E   # DSP helpers: read48, add_room, repair_dropouts, speech_rms, loudness, ffmpeg path

CACHE = os.path.join(ROOT, 'cache', 'panel')
os.makedirs(CACHE, exist_ok=True)
FF = E.FF


class Cancelled(Exception):
    pass


# ---- step registry --------------------------------------------------------------------------------------------
def P(key, label, default, kind='number', lo=None, hi=None, step=None, options=None):
    return dict(key=key, label=label, default=default, kind=kind, min=lo, max=hi, step=step, options=options)


STEPS = {}


def step(id, name, group, icon, params=(), short=None):
    def deco(fn):
        STEPS[id] = dict(id=id, name=name, short=short or name, group=group, icon=icon, params=list(params), fn=fn)
        return fn
    return deco


def info(path):
    i = sf.info(path)
    return i.samplerate, i.channels, i.frames / i.samplerate


# ---- classic ----------------------------------------------------------------------------------------------------
def ffchain(ctx, inp, out, chain, prenorm=None):
    if prenorm is not None:                             # make level-dependent thresholds behave the same on any input
        chain = f"volume={prenorm - E.loudness(inp):.2f}dB," + chain
    ctx.ff('-i', inp, '-af', chain, '-c:a', 'pcm_f32le', out)


@step('dsp_denoise', 'FFT noise reduction (ffmpeg afftdn)', 'classic', 'filter',
      [P('nr', 'Noise reduction (dB)', 12, lo=1, hi=40, step=1), P('nf', 'Noise floor (dBFS)', -50, lo=-80, hi=-20, step=1)],
      short='FFT denoise')
def _dsp_denoise(ctx, inp, out, p):
    ffchain(ctx, inp, out, f"afftdn=nr={p['nr']}:nf={p['nf']}")


@step('wpe', 'WPE dereverberation (classical, best on real stereo)', 'classic', 'waves',
      [P('taps', 'Prediction taps', 10, lo=3, hi=30, step=1), P('delay', 'Delay (frames)', 3, lo=1, hi=8, step=1)],
      short='WPE de-reverb')
def _wpe(ctx, inp, out, p):
    ctx.proc([E.pyenv('.venv'), os.path.join(ROOT, 'scripts', 'wpe_run.py'), inp, out,
              '--taps', str(int(p['taps'])), '--delay', str(int(p['delay']))])


@step('eq', 'Tone EQ (high-pass, warmth, mud, mid, presence, air)', 'classic', 'sliders-horizontal',
      [P('hp', 'High-pass (Hz)', 80, lo=20, hi=200, step=5), P('warmth', 'Warmth 180 Hz (dB)', 1.0, lo=-6, hi=6, step=0.5),
       P('mud', 'Mud 250 Hz (dB)', -1.0, lo=-6, hi=6, step=0.5), P('mid', 'Mid 1.2 kHz (dB)', 1.0, lo=-6, hi=6, step=0.5),
       P('presence', 'Presence 4.2 kHz (dB)', 1.5, lo=-6, hi=6, step=0.5), P('air', 'Air 10 kHz (dB)', 0.5, lo=-6, hi=6, step=0.5)],
      short='Tone EQ')
def _eq(ctx, inp, out, p):
    ffchain(ctx, inp, out, f"highpass=f={p['hp']}:poles=2,lowshelf=f=180:g={p['warmth']},equalizer=f=250:t=o:w=1.2:g={p['mud']},"
                           f"equalizer=f=1200:t=o:w=1.5:g={p['mid']},equalizer=f=4200:t=o:w=1.3:g={p['presence']},"
                           f"highshelf=f=10000:g={p['air']}")


@step('deesser', 'De-esser (tames harsh s sounds)', 'classic', 'audio-lines', [P('i', 'Intensity (0-1)', 0.25, lo=0, hi=1, step=0.05)],
      short='De-esser')
def _deesser(ctx, inp, out, p):
    ffchain(ctx, inp, out, f"volume=-18dB,deesser=i={p['i']}:m=0.5:f=0.5,volume=18dB")   # deesser is unstable above 0 dBFS


@step('expander', 'Expander (pushes pauses down)', 'classic', 'chevrons-down', [P('range', 'Max reduction (dB)', 6, lo=0, hi=30, step=1)],
      short='Expander')
def _expander(ctx, inp, out, p):
    ffchain(ctx, inp, out, f"agate=threshold=0.008:ratio=1.6:range={10 ** (-p['range'] / 20):.4f}:attack=8:release=250:knee=4", prenorm=-21.5)


@step('compressor', 'Compressor / leveler (evens out the speakers)', 'classic', 'gauge',
      [P('ratio', 'Ratio', 1.6, lo=1, hi=8, step=0.1), P('thresh', 'Threshold (dBFS)', -20, lo=-40, hi=0, step=1)],
      short='Compressor')
def _compressor(ctx, inp, out, p):
    ffchain(ctx, inp, out, f"acompressor=threshold={p['thresh']}dB:ratio={p['ratio']}:attack=10:release=200:knee=8:makeup=1.5", prenorm=-21.5)


@step('room', 'Synthetic stereo room (space and width)', 'classic', 'box',
      [P('level', 'Room level (dB)', -24, lo=-40, hi=-10, step=1), P('rt60', 'Decay RT60 (s)', 0.35, lo=0.15, hi=1.0, step=0.05)],
      short='Room')
def _room(ctx, inp, out, p):
    sf.write(out, E.add_room(E.read48(inp), p['level'], p['rt60']), E.SR, subtype='FLOAT')


@step('mix', 'Mix with the input or an earlier step (percent)', 'classic', 'git-merge',
      [P('percent', 'Percent of the other source', 30, lo=0, hi=100, step=5), P('with', 'Mix with', 'input', kind='ref')],
      short='Mix')
def _mix(ctx, inp, out, p):
    x = E.read48(inp)
    o = E.read48(ctx.ref(p['with']))[:len(x)]
    x = x[:len(o)]
    m = p['percent'] / 100
    sf.write(out, (1 - m) * x + m * o * (E.speech_rms(x) / max(E.speech_rms(o), 1e-9)), E.SR, subtype='FLOAT')


@step('repair', 'Dropout repair (restore syllables a neural step erased)', 'classic', 'bandage',
      [P('deficit', 'Trigger deficit (dB)', 12, lo=6, hi=24, step=1)], short='Dropout repair')
def _repair(ctx, inp, out, p):
    ref = ctx.ref_input_denoised()
    y, spans = E.repair_dropouts(E.read48(inp), E.read48(ref), p['deficit'])
    ctx.log(f"repaired {len(spans)} dropouts ({sum(e - s for s, e in spans):.1f} s)")
    sf.write(out, y, E.SR, subtype='FLOAT')


@step('loudness', 'Loudness normalisation (LUFS, true peak)', 'classic', 'volume-2',
      [P('lufs', 'Target (LUFS)', -16, lo=-30, hi=-9, step=1), P('tp', 'True peak (dBTP)', -1.5, lo=-6, hi=0, step=0.5)],
      short='Loudness')
def _loudness(ctx, inp, out, p):
    ln = f"loudnorm=I={p['lufs']}:TP={p['tp']}:LRA=20"
    err = ctx.ff('-i', inp, '-af', ln + ':print_format=json', '-f', 'null', '-', capture=True)
    j = json.loads(re.search(r'\{[^{}]*\}', err).group())
    ln += (f":measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}"
           f":measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    ctx.ff('-i', inp, '-af', ln + ',aresample=48000:filter_size=64:cutoff=0.97', '-c:a', 'pcm_f32le', out)


# ---- neural networks ----------------------------------------------------------------------------------------------
@step('dfn', 'DeepFilterNet3 noise suppression', 'neural', 'shield', [P('atten', 'Max reduction (dB)', 12, lo=3, hi=40, step=1)],
      short='DeepFilterNet3')
def _dfn(ctx, inp, out, p):
    ctx.proc([E.pyenv('envs/dfn'), os.path.join(ROOT, 'scripts', 'dfn_enhance.py'), inp, out, '--atten', str(p['atten'])])


@step('cvse', 'ClearerVoice MossFormer2 SE 48k (enhancement, filter only)', 'neural', 'sparkles', short='ClearerVoice SE')
def _cvse(ctx, inp, out, p):
    ctx.proc([E.pyenv('envs/clearvoice'), os.path.join(ROOT, 'experiments', 'clearvoice_run.py'), inp, out, 'se48'])


@step('cvsr', 'ClearerVoice MossFormer2 SR 48k (bandwidth super-resolution)', 'neural', 'maximize-2', short='ClearerVoice SR')
def _cvsr(ctx, inp, out, p):
    ctx.proc([E.pyenv('envs/clearvoice'), os.path.join(ROOT, 'experiments', 'clearvoice_run.py'), inp, out, 'sr48'])


@step('sidon', 'Sidon generative speech restoration (full band, dry)', 'neural', 'wand-sparkles', short='Sidon')
def _sidon(ctx, inp, out, p):
    ctx.proc([E.pyenv('envs/sidon'), os.path.join(ROOT, 'scripts', 'sidon_enhance.py'), inp, out, '--seg', '30'])


@step('sep_sidon', 'Speaker separation (MossFormer2 SS) + Sidon on each voice', 'neural', 'users', short='Separate + Sidon')
def _sep_sidon(ctx, inp, out, p):
    pre = out[:-4] + '_sep'
    ctx.proc([E.pyenv('envs/clearvoice'), os.path.join(ROOT, 'scripts', 'separate.py'), inp, pre], span=(0, 0.3))
    total = None
    for i, span in ((1, (0.3, 0.65)), (2, (0.65, 1.0))):
        r = f"{pre}_{i}_sidon.wav"
        ctx.proc([E.pyenv('envs/sidon'), os.path.join(ROOT, 'scripts', 'sidon_enhance.py'), f"{pre}_{i}.wav", r, '--seg', '30'], span=span)
        xi, yi = E.read48(f"{pre}_{i}.wav"), E.read48(r)
        yi *= np.sqrt(np.mean(xi ** 2)) / max(np.sqrt(np.mean(yi ** 2)), 1e-9)
        total = yi if total is None else total[:len(yi)] + yi[:len(total)]
    sf.write(out, total, E.SR, subtype='FLOAT')
    for f in (f"{pre}_1.wav", f"{pre}_2.wav", f"{pre}_1_sidon.wav", f"{pre}_2_sidon.wav"):
        os.remove(f)


ROFORMER = {'aggressive': 'dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt',
            'less aggressive': 'dereverb_mel_band_roformer_less_aggressive_anvuew_sdr_18.8050.ckpt',
            'mono': 'dereverb_mel_band_roformer_mono_anvuew.ckpt'}


def separator(ctx, inp, out, model, stem):
    d = out[:-4] + '_sepdir'; os.makedirs(d, exist_ok=True)
    ctx.proc([E.pyenv('envs/sep').replace('python.exe', 'audio-separator.exe'), inp, '--model_filename', model,
              '--model_file_dir', os.path.join(ROOT, 'models', 'uvr'), '--output_dir', d, '--output_format', 'WAV',
              '--single_stem', stem, '--custom_output_names', json.dumps({stem: 'result'})])
    shutil.move(os.path.join(d, 'result.wav'), out); shutil.rmtree(d, ignore_errors=True)


@step('roformer', 'MelBand RoFormer de-reverb (anvuew; stereo models need real stereo)', 'neural', 'wind',
      [P('model', 'Model', 'less aggressive', kind='select', options=list(ROFORMER))], short='RoFormer de-reverb')
def _roformer(ctx, inp, out, p):
    model = p['model']
    if model != 'mono':
        _, ch, _ = info(inp)
        if ch == 1:
            ctx.log("input is mono: stereo de-reverb models pass mono through unchanged, using the mono model")
            model = 'mono'
    src = inp
    if info(inp)[1] == 1:                     # the separator wants stereo files
        src = out[:-4] + '_st.wav'; ctx.ff('-i', inp, '-ac', '2', '-c:a', 'pcm_f32le', src)
    separator(ctx, src, out, ROFORMER[model], 'noreverb')
    if src != inp: os.remove(src)


@step('uvr_deecho', 'UVR DeEcho-DeReverb (VR architecture)', 'neural', 'radio', short='UVR DeEcho')
def _uvr(ctx, inp, out, p):
    separator(ctx, inp, out, 'UVR-DeEcho-DeReverb.pth', 'No Reverb')


@step('resemble', 'resemble-enhance (denoise / generative enhance)', 'neural', 'bot',
      [P('mode', 'Mode', 'enhance', kind='select', options=['enhance', 'denoise']), P('lambd', 'Denoise strength λ', 0.9, lo=0, hi=1, step=0.1)],
      short='resemble-enhance')
def _resemble(ctx, inp, out, p):
    ctx.proc([E.pyenv('envs/resemble'), os.path.join(ROOT, 'experiments', 'resemble_run.py'), inp, out, '--mode', p['mode'],
              '--lambd', str(p['lambd']), '--chunk', '10'])


@step('voicefixer', 'VoiceFixer general restoration', 'neural', 'wrench', [P('mode', 'Mode', 0, kind='select', options=[0, 1, 2])],
      short='VoiceFixer')
def _voicefixer(ctx, inp, out, p):
    ctx.proc([E.pyenv('envs/vf'), os.path.join(ROOT, 'experiments', 'voicefixer_run.py'), inp, out, '--mode', str(p['mode'])])


DEFAULT_PIPELINE = ['cvse', 'sep_sidon', 'dfn', 'repair', 'mix', 'eq', 'deesser', 'compressor', 'room', 'loudness']
DEFAULT_OVERRIDES = {'dfn': {'atten': 8}, 'mix': {'percent': 50, 'with': '#1'}}   # '#1' = first step of the pipeline


def make_key(prev, kind, params, extra=''):
    return hashlib.sha1(json.dumps([prev, kind, params, extra], sort_keys=True).encode()).hexdigest()[:16]


def resolve_params(t):
    spec = STEPS[t['type']]
    return {pp['key']: (t.get('params') or {}).get(pp['key'], pp['default']) for pp in spec['params']}


def input_key(source):
    st = os.stat(source['path'])
    return make_key(f"{source['path']}|{st.st_size}|{st.st_mtime}", 'input', [source['start'], source['end']])


def chain_keys(source, pipeline):
    """Cache key of every step's result for this input section and pipeline; {'input': key, uid: key, ...}."""
    ikey = input_key(source)
    keys, prev = {'input': ikey}, ikey
    for t in pipeline:
        params = resolve_params(t)
        extra = ''
        if t['type'] == 'mix':
            w = params['with']
            if w and w.startswith('#'): w = pipeline[int(w[1:]) - 1]['uid']
            extra = keys.get(w, ikey) if w not in ('input', '') else ikey
        prev = keys[t['uid']] = make_key(prev, t['type'], params, extra)
    return keys


def cache_path(key):
    return os.path.join(CACHE, key + '.wav')


def registry():
    return [{k: v for k, v in s.items() if k != 'fn'} for s in STEPS.values()]


# ---- job ------------------------------------------------------------------------------------------------------------
class Job:
    """Runs a pipeline in a background thread. `state` is polled by the UI."""

    def __init__(self, source, pipeline, out_dir, save_all, preview=False):
        """preview=True: run the pipeline (a prefix of it) only into the cache; the last step's cached file is the temp
        result shown in the ribbon. Nothing is exported."""
        self.source, self.pipeline, self.out_dir, self.save_all, self.preview = source, pipeline, out_dir, save_all, preview
        self.state = dict(running=True, step=0, n=len(pipeline), name='', fraction=0.0, message='starting',
                          log=[], outputs=[], final=None, error=None, started=time.time(), preview=preview,
                          target=pipeline[-1]['uid'] if pipeline else None, key=None)
        self.cancelled, self.popen, self.results = False, None, {}
        self.duration = source['end'] - source['start']
        threading.Thread(target=self._run, daemon=True).start()

    # helpers used by steps
    def log(self, msg):
        self.state['log'] = (self.state['log'] + [msg])[-200:]

    def ff(self, *args, capture=False):
        r = subprocess.run([FF, '-hide_banner', '-nostats', '-y', *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
        if r.returncode != 0:
            raise RuntimeError('ffmpeg failed: ' + r.stderr[-1500:])
        return r.stderr if capture else None

    def proc(self, cmd, span=(0.0, 1.0)):
        """Run a step script, streaming its output into the log and the progress bar."""
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
                   PATH=os.path.join(ROOT, 'envs', 'sep', 'Scripts') + os.pathsep + os.environ.get('PATH', ''))
        self.popen = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                      errors='replace', env=env, cwd=ROOT)
        tail = []
        for line in self.popen.stdout:
            line = line.strip()
            if not line: continue
            tail = (tail + [line])[-40:]
            frac = None
            m = re.search(r'seg \d+ \d+-(\d+)s', line) or re.search(r'seg ([\d.]+)/', line)
            if m: frac = float(m.group(1)) / max(self.duration, 1e-3)
            m = re.search(r'([\d.]+)/([\d.]+)s\s+elapsed', line)
            if m: frac = float(m.group(1)) / float(m.group(2))
            m = re.search(r'(\d+)%\|', line)
            if m: frac = int(m.group(1)) / 100
            if frac is not None:
                self.state['fraction'] = span[0] + (span[1] - span[0]) * min(frac, 1.0)
            if line.startswith(('done', 'wrote', 'out:', 'repaired')) or 'Error' in line:
                self.log(line[:300])
            if self.cancelled: self.popen.kill()
        rc = self.popen.wait(); self.popen = None
        if self.cancelled: raise Cancelled()
        if rc != 0:
            raise RuntimeError(f"{os.path.basename(cmd[1])} failed (exit {rc}):\n" + "\n".join(tail[-15:]))

    def ref(self, which):
        if which in ('input', None, ''): return self.results['input']
        if which.startswith('#'):                                   # '#k' = k-th pipeline step
            which = self.pipeline[int(which[1:]) - 1]['uid']
        if which not in self.results:
            raise RuntimeError('mix source must be an earlier step')
        return self.results[which]

    def ref_input_denoised(self):
        key = self._key(self.keys['input'], 'dfn', {'atten': 12})
        return self._cached(key, lambda o: _dfn(self, self.results['input'], o, {'atten': 12}))

    # internals
    def _key(self, prev, kind, params, extra=''):
        return make_key(prev, kind, params, extra)

    def _cached(self, key, make):
        path = os.path.join(CACHE, key + '.wav')
        if not os.path.exists(path):
            tmp = path[:-4] + '.part.wav'
            make(tmp); os.replace(tmp, path)
        return path

    def _run(self):
        try:
            s = self.source
            all_keys = chain_keys(s, self.pipeline)
            ikey = all_keys['input']
            self.keys = {'input': ikey}
            self.results['input'] = self._cached(ikey, lambda o: self.ff(
                '-ss', str(s['start']), '-t', str(self.duration), '-i', s['path'], '-map', '0:a:0', '-c:a', 'pcm_f32le', o))
            prev = self.results['input']
            if not self.preview: os.makedirs(self.out_dir, exist_ok=True)
            for i, t in enumerate(self.pipeline, 1):
                spec = STEPS[t['type']]
                params, key = resolve_params(t), all_keys[t['uid']]
                self.state.update(step=i, name=spec['name'], fraction=0.0, message=f"Step {i}/{len(self.pipeline)}: {spec['name']}")
                t0 = time.time()
                hit = os.path.exists(cache_path(key))
                prev = self._cached(key, lambda o, prev=prev: spec['fn'](self, prev, o, params))
                self.log(f"{i}. {spec['short']}: {'cached' if hit else f'{time.time() - t0:.0f}s'}")
                self.results[t['uid']], self.keys[t['uid']] = prev, key
                if self.save_all and not self.preview:
                    self._export(prev, os.path.join(self.out_dir, f"{i:02d}_{t['type']}.wav"))
                    self.state['outputs'].append(f"{i:02d}_{t['type']}.wav")
            took = f"{time.time() - self.state['started']:.0f}s"
            if self.preview:
                last = STEPS[self.pipeline[-1]['type']]['short']
                self.state.update(key=self.keys[self.pipeline[-1]['uid']], fraction=1.0,
                                  message=f"Preview ready in {took}: result after step {len(self.pipeline)} ({last})")
            else:
                final = os.path.join(self.out_dir, 'final.wav')
                self._export(prev, final, mp3=True)
                self.state.update(final=final, fraction=1.0, message=f"Done in {took} -> {self.out_dir}")
        except Cancelled:
            self.state.update(message='Cancelled', error='cancelled')
        except Exception as e:
            self.state.update(message='Failed: ' + str(e).splitlines()[0][:200], error=str(e))
        finally:
            self.state['running'] = False

    def _export(self, src, dst, mp3=False):
        x, sr = sf.read(src, dtype='float32', always_2d=True)
        pk = float(np.abs(x).max()) if len(x) else 0
        if pk > 0.999: x = x * (0.999 / pk)                     # never clip on export
        sf.write(dst, x, sr, subtype='PCM_24')
        if mp3:
            self.ff('-i', dst, '-c:a', 'libmp3lame', '-b:a', '192k', dst[:-4] + '.mp3')

    def cancel(self):
        self.cancelled = True
        if self.popen: self.popen.kill()
