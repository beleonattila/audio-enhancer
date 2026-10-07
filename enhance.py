"""Podcast speech restoration with tunable character and a fast snippet loop.

    python enhance.py INPUT [--start 10:00 --dur 3:00] [--preset natural] [--set key=value ...] [--score]
    python enhance.py INPUT --start 10:00 --dur 3:00 --preset clean,natural,warm      # A/B several presets
    python enhance.py INPUT --preset natural --set room_db=-20 --name roomier          # full file, with an override

Run with the .venv python. Without --start/--dur the whole file is processed.
Outputs go to out/<input name>/<section>/<name>.wav (+ .mp3). Expensive model stages are cached in
cache/, so re-rendering a section with different mastering parameters takes seconds.

Signal flow:
  source -> [ClearerVoice SE, pre=cvse] -> [Sidon restore] -> [DeepFilterNet, dfn_atten dB] -> dropout repair (repair_db) -> mix with the original (orig_mix %, orig_source)
         -> high-pass, EQ (warmth / mud / mid / presence / air) -> de-esser -> expander -> compressor
         -> + stereo synthetic room (room_db, room_rt60) -> loudness normalisation (lufs, true peak -1.5 dBTP)
"""
import argparse, hashlib, json, os, re, subprocess, sys, time
import numpy as np, soundfile as sf, scipy.signal as ss
from scipy.ndimage import find_objects, label, maximum_filter1d, minimum_filter1d

ROOT = os.path.dirname(os.path.abspath(__file__))
SR = 48000

# ---- parameters -------------------------------------------------------------------------------------------
PRESETS = {
    # sharp, dry, silent pauses: the original v1 chain
    'clean':   dict(repair_db=12, pre='none', restore='sidon', dfn_atten=15, orig_mix=0, orig_source='clean', warmth_db=0.0, mud_db=-1.5, mid_db=2.0, presence_db=2.5,
                    air_db=1.5, deess=0.35, expander_db=10, comp_ratio=2.0, comp_thresh_db=-20, room_db=None, room_rt60=0.35, lufs=-16),
    # keeps some room tone and texture, a touch of real-sounding space, softer top end
    'natural': dict(repair_db=12, pre='none', restore='sidon', dfn_atten=8, orig_mix=10, orig_source='clean', warmth_db=1.0, mud_db=-1.0, mid_db=1.0, presence_db=1.5,
                    air_db=0.5, deess=0.25, expander_db=0, comp_ratio=1.6, comp_thresh_db=-20, room_db=-24, room_rt60=0.35, lufs=-16),
    # radio-like: fuller low end, rounder highs, a bit more levelling
    'warm':    dict(repair_db=12, pre='none', restore='sidon', dfn_atten=10, orig_mix=0, orig_source='clean', warmth_db=2.5, mud_db=-0.5, mid_db=0.5, presence_db=1.0,
                    air_db=-1.0, deess=0.3, expander_db=4, comp_ratio=2.5, comp_thresh_db=-22, room_db=-28, room_rt60=0.3, lufs=-16),
    # no generative model at all: denoised original with mastering (most "authentic", least improved)
    'denoise': dict(repair_db=None, pre='none', restore='none', dfn_atten=18, orig_mix=0, orig_source='clean', warmth_db=1.0, mud_db=-1.5, mid_db=2.0, presence_db=3.0,
                    air_db=2.0, deess=0.2, expander_db=4, comp_ratio=2.0, comp_thresh_db=-20, room_db=None, room_rt60=0.35, lufs=-16),
}
DOC = {
    'pre':            "before Sidon: none | cvse = ClearerVoice SE 48k | sep = split the 2 speakers, Sidon each, sum | "
                      "cvse_sep = cvse then sep (cvse/sep need setup.sh --all)",
    'restore':        "sidon = generative restoration (full band, dry, very clean) | none = keep original voice, denoise only",
    'repair_db':      "dropout repair: where Sidon is this many dB quieter than the original speech, fall back to the original (none = off)",
    'dfn_atten':      "DeepFilterNet max noise reduction in dB after restoration; 0 = stage off. Lower = more natural room tone",
    'orig_mix':       "percent of the ORIGINAL in the final mix (0-100, loudness-matched; 30 = 30% original + 70% enhanced). "
                      "Keeps room tone continuous, hides Sidon's on/off gating and warble, adds natural texture",
    'orig_source':    "what to mix in: raw = untouched recording, clean = lightly denoised (DeepFilterNet 12 dB), "
                      "cvse = ClearerVoice SE 48k (filtered only, no re-synthesis)",
    'warmth_db':      "low shelf at 180 Hz: body / chest",
    'mud_db':         "bell at 250 Hz: negative removes boxy room sound",
    'mid_db':         "bell at 1.2 kHz: forwardness / intelligibility",
    'presence_db':    "bell at 4.2 kHz: clarity, can get harsh",
    'air_db':         "high shelf at 10 kHz: sparkle; negative = rounder, less 'digital'",
    'deess':          "de-esser intensity 0..1",
    'expander_db':    "how far pauses are pushed down (0 = off). High values = dead-silent, sterile pauses",
    'comp_ratio':     "compressor ratio (1 = off): evens out the two speakers",
    'comp_thresh_db': "compressor threshold in dBFS (input pre-normalised to -21.5 LUFS)",
    'room_db':        "level of a synthetic stereo small-room reverb (e.g. -30..-18); none = off. Adds space and width",
    'room_rt60':      "room decay time in seconds (0.2 = small treated room, 0.5 = living room)",
    'lufs':           "final integrated loudness (-16 podcast standard, -14 streaming-loud)",
}


def parse_time(t):
    if t is None: return None
    parts = [float(p) for p in str(t).split(':')]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def fmt_time(s):
    return f"{int(s // 60):02d}m{int(s % 60):02d}s"


def pyenv(d):
    p = os.path.join(ROOT, d, 'Scripts', 'python.exe')
    return p if os.path.exists(p) else os.path.join(ROOT, d, 'bin', 'python')


def ffmpeg_exe():
    import shutil
    exe = os.environ.get('FFMPEG') or shutil.which('ffmpeg')
    if not exe:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    return exe


FF = ffmpeg_exe()


def ff(*args, capture=False):
    r = subprocess.run([FF, '-hide_banner', '-nostats', '-y', *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        sys.exit(f"ffmpeg failed:\n{r.stderr[-2000:]}")
    return r.stderr if capture else None


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        sys.exit(f"{os.path.basename(cmd[1])} failed:\n{r.stderr[-3000:]}")
    return [l for l in r.stdout.splitlines() if l.startswith('done')][-1:]


def loudness(path):
    err = ff('-i', path, '-af', 'ebur128', '-f', 'null', '-', capture=True)
    return float(re.findall(r'^\s+I:\s+(-?[\d.]+) LUFS', err, re.M)[-1])


# ---- cached model stages -------------------------------------------------------------------------------------
class Cache:
    def __init__(self, src, start, dur):
        st = os.stat(src)
        key = hashlib.sha1(f"{os.path.abspath(src)}|{st.st_size}|{st.st_mtime}".encode()).hexdigest()[:8]
        name = re.sub(r'[^\w\-]+', '_', os.path.splitext(os.path.basename(src))[0]).strip('_')
        self.section = 'full' if start is None else f"{fmt_time(start)}_{fmt_time(dur)}"
        self.dir = os.path.join(ROOT, 'cache', f"{name}_{key}", self.section)
        self.out_dir = os.path.join(ROOT, 'out', name, self.section)
        os.makedirs(self.dir, exist_ok=True); os.makedirs(self.out_dir, exist_ok=True)
        self.src, self.start, self.dur = src, start, dur

    FIXES = os.path.join(ROOT, 'fixes.json')

    def _all_fixes(self):
        try:
            with open(self.FIXES, encoding='utf-8') as f: return json.load(f)
        except FileNotFoundError:
            return {}

    def add_fix(self, a, b, mix):
        """Store a fix range given in section time, as absolute file time, so full renders apply it too."""
        off = self.start or 0
        fx = self._all_fixes(); key = os.path.basename(self.src)
        fx.setdefault(key, []).append([round(off + a, 2), round(off + b, 2), mix])
        with open(self.FIXES, 'w', encoding='utf-8') as f: json.dump(fx, f, indent=1, ensure_ascii=False)

    def clear_fixes(self):
        fx = self._all_fixes(); fx.pop(os.path.basename(self.src), None)
        with open(self.FIXES, 'w', encoding='utf-8') as f: json.dump(fx, f, indent=1, ensure_ascii=False)

    def fixes(self):
        """Stored fix ranges overlapping this section, in section time."""
        off = self.start or 0; end = off + self.dur if self.dur else float('inf')
        return [(max(a, off) - off, min(b, end) - off, m) for a, b, m in self._all_fixes().get(os.path.basename(self.src), [])
                if b > off and a < end]

    def path(self, stage):
        return os.path.join(self.dir, stage + '.wav')

    def get(self, stage, make):
        p = self.path(stage)
        if not os.path.exists(p):
            t = time.time(); tmp = p + '.part.wav'
            make(tmp); os.replace(tmp, p)
            print(f"  [{stage}] computed in {time.time() - t:.0f}s")
        return p

    def source(self):
        def make(o):
            cut = [] if self.start is None else ['-ss', str(self.start), '-t', str(self.dur)]
            ff(*cut, '-i', self.src, '-map', '0:a:0', '-ac', '1', '-c:a', 'pcm_f32le', o)
        return self.get('source', make)

    def cvse(self):
        """ClearerVoice-Studio MossFormer2_SE_48K speech enhancement of the source (envs/clearvoice, setup.sh --all)."""
        return self.get('cvse', lambda o: run([pyenv('envs/clearvoice'), os.path.join(ROOT, 'experiments', 'clearvoice_run.py'), self.source(), o, 'se48']))

    def _sidon_run(self, inp, out):
        run([pyenv('envs/sidon'), os.path.join(ROOT, 'scripts', 'sidon_enhance.py'), inp, out, '--seg', '30'])

    def sep_sidon(self, on):
        """Separate the two speakers (ClearerVoice MossFormer2_SS_16K), restore each voice alone with Sidon,
        restore their relative levels and sum. Sidon then never sees two overlapping voices."""
        src = self.source() if on == 'source' else self.cvse()

        def make(o):
            pre = os.path.join(self.dir, f"{on}_sep")
            if not os.path.exists(pre + '_2.wav'):
                run([pyenv('envs/clearvoice'), os.path.join(ROOT, 'scripts', 'separate.py'), src, pre])
            total = None
            for i in (1, 2):
                restored = f"{pre}_{i}_sidon.wav"
                if not os.path.exists(restored):
                    self._sidon_run(f"{pre}_{i}.wav", restored)
                xi, yi = read48(f"{pre}_{i}.wav"), read48(restored)
                yi *= np.sqrt(np.mean(xi ** 2)) / max(np.sqrt(np.mean(yi ** 2)), 1e-9)   # Sidon normalises level per file
                total = yi if total is None else total[:len(yi)] + yi[:len(total)]
            sf.write(o, total, SR, subtype='FLOAT')
        return self.get(('' if on == 'source' else 'cvse_') + 'sep_sidon', make)

    def sidon(self, pre='none'):
        if pre in ('sep', 'cvse_sep'):
            return self.sep_sidon('source' if pre == 'sep' else 'cvse')
        stage, inp = ('sidon', self.source) if pre == 'none' else ('cvse_sidon', self.cvse)
        return self.get(stage, lambda o: self._sidon_run(inp(), o))

    def dfn(self, inp_stage, atten):
        inp = {'sidon': self.sidon, 'cvse_sidon': lambda: self.sidon('cvse'), 'sep_sidon': lambda: self.sidon('sep'),
               'cvse_sep_sidon': lambda: self.sidon('cvse_sep')}.get(inp_stage, self.source)()
        return self.get(f"{inp_stage}_dfn{atten:g}", lambda o: run([pyenv('envs/dfn'), os.path.join(ROOT, 'scripts', 'dfn_enhance.py'), inp, o, '--atten', str(atten)]))


# ---- DSP --------------------------------------------------------------------------------------------------------
def read48(path):
    x, sr = sf.read(path, dtype='float32', always_2d=True)
    x = x.mean(1)
    return ss.resample_poly(x, SR // 100, sr // 100).astype(np.float32) if sr != SR else x


def room_ir(rt60, seed=7):
    """Stereo small-room impulse response: sparse early reflections + decorrelated exponentially decaying,
    progressively darker noise tail. Normalised to unit energy per channel."""
    rng = np.random.default_rng(seed)
    n = int(SR * rt60 * 1.2)
    t = np.arange(n) / SR
    ir = np.zeros((n, 2), np.float32)
    for ch in range(2):
        tail = rng.standard_normal(n) * np.exp(-6.9 * t / rt60)
        tail[:int(0.012 * SR)] *= np.linspace(0, 1, int(0.012 * SR))          # tail builds up after ~12 ms
        b, a = ss.butter(1, 5000, fs=SR); dark = ss.lfilter(b, a, tail)          # air absorption: tail darkens
        tail = tail * np.exp(-t / (rt60 / 3)) + dark * (1 - np.exp(-t / (rt60 / 3)))
        for d, g in zip(rng.uniform(0.004, 0.025, 8), rng.uniform(0.2, 0.6, 8)):  # early reflections
            tail[int(d * SR)] += g * rng.choice([-1, 1])
        b, a = ss.butter(2, 150, 'highpass', fs=SR)
        ir[:, ch] = ss.lfilter(b, a, tail)
    return ir / np.sqrt((ir ** 2).sum(0, keepdims=True))


def add_room(x, wet_db, rt60, block=SR * 30):
    """x mono -> stereo dry + wet room, block-wise overlap-add so hour-long files stay in bounded memory."""
    ir = room_ir(rt60); g = 10 ** (wet_db / 20); L = len(ir)
    y = np.repeat(x[:, None], 2, 1).astype(np.float32)
    carry = np.zeros((L - 1, 2), np.float32)
    for i in range(0, len(x), block):
        seg = x[i:i + block]
        wet = np.stack([ss.oaconvolve(seg, ir[:, c]) for c in range(2)], 1).astype(np.float32)
        wet[:L - 1] += carry
        n = len(seg)
        y[i:i + n] += g * wet[:n]
        carry = wet[n:].copy()  # oaconvolve 'full' output is always n + L - 1 long
    return y


def fir_filter(x, h, block=SR * 30):
    """Linear-phase FIR, block-wise overlap-add, delay-compensated (same length as x)."""
    L, d = len(h), len(h) // 2
    y = np.zeros(len(x) + L - 1, np.float32)
    for i in range(0, len(x), block):
        seg = x[i:i + block]
        y[i:i + len(seg) + L - 1] += ss.oaconvolve(seg, h).astype(np.float32)
    return y[d:d + len(x)]


def speech_env(x, hop):
    """Speech-band (150-4000 Hz) level per frame in dB, 0 dB ≈ loud speech (90th percentile)."""
    y = ss.sosfilt(ss.butter(4, [150, 4000], 'bandpass', fs=SR, output='sos'), x).astype(np.float32)
    n = int(hop * SR); k = len(y) // n
    e = 10 * np.log10((y[:k * n].reshape(k, n).astype(np.float64) ** 2).mean(1) + 1e-12)
    e = np.convolve(e, np.ones(3) / 3, 'same')
    return e - np.percentile(e, 90)


def speech_rms(x, hop=0.02):
    """RMS over the louder half of 20 ms frames: a level measure that ignores pauses and noise floor."""
    n = int(hop * SR); k = len(x) // n
    e = (x[:k * n].reshape(k, n).astype(np.float64) ** 2).mean(1)
    return float(np.sqrt(e[e >= np.median(e)].mean()))


def match_eq(src, target, taps=2047):
    """FIR that gives `src` the long-term spectrum (and level) of `target`, smoothed to 1/3 octave, ±18 dB max."""
    f, Ps = ss.welch(src[:SR * 600], SR, nperseg=4096)
    _, Pt = ss.welch(target[:SR * 600], SR, nperseg=4096)
    g = 10 * np.log10(Pt + 1e-14) - 10 * np.log10(Ps + 1e-14)
    gs = np.array([g[(f >= fc / 2 ** (1 / 6)) & (f <= fc * 2 ** (1 / 6))].mean() if fc > 30 else g[1]
                   for fc in np.maximum(f, 1)])
    gs = np.clip(gs, np.median(gs[(f > 200) & (f < 4000)]) - 18, np.median(gs[(f > 200) & (f < 4000)]) + 18)
    return ss.firwin2(taps, f / (SR / 2), 10 ** (gs / 20)).astype(np.float32)


def repair_dropouts(x, ref, deficit_db, manual=(), min_ms=100, short_ms=30, merge_ms=250, hop=0.01):
    """Where the restored voice `x` is >= deficit_db quieter than the reference (lightly denoised original) while the
    reference has speech, crossfade (equal-power, ~40 ms ramps) to the EQ-matched reference. `manual` adds
    user-chosen ranges [(start_s, end_s, mix 0..1)] (e.g. where Sidon warbles on overlapping voices).
    deficit_db=None disables the automatic part. Returns x, automatic spans."""
    n = min(len(x), len(ref)); x, ref = x[:n], ref[:n]
    auto = deficit_db is not None
    deficit_db = deficit_db if auto else 999
    ex, er = speech_env(x, hop), speech_env(ref, hop)
    d = er - ex

    def sustained(mask, ms):                                                  # morphological opening: keep runs >= ms
        k = max(1, int(round(ms / 1000 / hop)))
        return maximum_filter1d(minimum_filter1d(mask.astype(np.float32), k), k)
    # long rule: moderate deficit sustained >= min_ms (shorter moderate deficits are mostly room echo Sidon rightly removed)
    # short rule: deep deficit (+8 dB) in clearly audible speech for >= short_ms, e.g. a plosive + vowel onset lost
    # while the other speaker talks over it
    short = sustained((er > -22) & (d > deficit_db + 8), short_ms)
    # ...but only at onsets: the reference must get >= 6 dB louder entering the gap (a decaying echo tail never does)
    lab, nlab = label(short > 0)
    for i, sl in enumerate(find_objects(lab), 1):
        a, b = sl[0].start, sl[0].stop
        if er[a:b].max() - er[max(0, a - 6):a + 1].min() < 6:
            short[lab == i] = 0
    w = np.maximum(sustained((er > -28) & (d > deficit_db), min_ms), short)
    k = int(round(merge_ms / 1000 / hop))                                     # merge patches < merge_ms apart, so the
    w = minimum_filter1d(maximum_filter1d(w, k), k)                           # texture doesn't flip back and forth
    w = maximum_filter1d(w, 5)                                                # extend ±20 ms around the gap
    spans, on = [], np.flatnonzero(w > 0.5)
    if len(on):
        brk = np.flatnonzero(np.diff(on) > 1)
        spans = [(on[s] * hop, (on[e] + 1) * hop) for s, e in zip(np.r_[0, brk + 1], np.r_[brk, len(on) - 1])]
    t = np.arange(len(w)) * hop
    for a, b, mix in manual:
        w = np.maximum(w, np.where((t >= a) & (t < b), mix, 0))
    w = np.convolve(w, np.hanning(7) / np.hanning(7).sum(), 'same')           # ~40 ms ramps
    if not w.any():
        return x, spans
    fill = fir_filter(ref, match_eq(ref, x))
    ws = np.interp(np.arange(n), (np.arange(len(w)) + 0.5) * hop * SR, w).astype(np.float32)
    return np.sqrt(1 - ws) * x + np.sqrt(ws) * fill, spans


def render(cache, p, name):
    t0 = time.time()
    # 1) restoration + residual denoise
    if p['restore'] == 'sidon':
        base = {'none': 'sidon', 'cvse': 'cvse_sidon', 'sep': 'sep_sidon', 'cvse_sep': 'cvse_sep_sidon'}[p['pre']]
        voice = cache.dfn(base, p['dfn_atten']) if p['dfn_atten'] else cache.sidon(p['pre'])
    else:
        voice = cache.dfn('source', p['dfn_atten'] or 12)
    x = read48(voice)
    manual = cache.fixes()
    if p['restore'] == 'sidon' and (p['repair_db'] is not None or manual):
        o = read48(cache.dfn('source', 12))
    # 2) dropout repair: Sidon occasionally erases quiet/fast syllables -> fall back to the original there;
    #    plus manual fix ranges from fixes.json (e.g. warbles on overlapping voices)
    if p['restore'] == 'sidon' and (p['repair_db'] is not None or manual):
        if manual:
            print("  manual fixes: " + ", ".join(f"{int(a // 60)}:{a % 60:04.1f}-{int(b // 60)}:{b % 60:04.1f} ({m:.0%})" for a, b, m in manual))
        x, spans = repair_dropouts(x, o, p['repair_db'], manual)
        print(f"  repaired {len(spans)} dropouts ({sum(e - s for s, e in spans):.1f} s)" +
              (": " + ", ".join(f"{int(s // 60)}:{s % 60:04.1f}" for s, _ in spans[:12]) + (" ..." if len(spans) > 12 else "") if spans else ""))
    # 3) mix with the original: orig_mix % original + (100 - orig_mix) % enhanced, both matched to the same speech level
    if p['orig_mix'] and p['restore'] == 'sidon':
        m = p['orig_mix'] / 100
        orig = read48({'raw': cache.source, 'cvse': cache.cvse}.get(p['orig_source'], lambda: cache.dfn('source', 12))())[:len(x)]
        x = x[:len(orig)]
        x = (1 - m) * x + m * orig * (speech_rms(x) / max(speech_rms(orig), 1e-9))
    pre = os.path.join(cache.dir, f"_mix_{os.getpid()}.wav"); sf.write(pre, x, SR, subtype='FLOAT')
    # 4) tone + dynamics (ffmpeg), input pre-normalised to -21.5 LUFS so thresholds are level-independent
    gain = -21.5 - loudness(pre)
    chain = [f"volume={gain:.2f}dB", "highpass=f=80:poles=2",
             f"lowshelf=f=180:g={p['warmth_db']}", f"equalizer=f=250:t=o:w=1.2:g={p['mud_db']}",
             f"equalizer=f=1200:t=o:w=1.5:g={p['mid_db']}", f"equalizer=f=4200:t=o:w=1.3:g={p['presence_db']}",
             f"highshelf=f=10000:g={p['air_db']}"]
    if p['deess'] > 0:  # ffmpeg's deesser goes unstable on samples above full scale: run it 18 dB down
        chain += ["volume=-18dB", f"deesser=i={p['deess']}:m=0.5:f=0.5", "volume=18dB"]
    if p['expander_db'] > 0:
        chain.append(f"agate=threshold=0.008:ratio=1.6:range={10 ** (-p['expander_db'] / 20):.4f}:attack=8:release=250:knee=4")
    if p['comp_ratio'] > 1:
        chain.append(f"acompressor=threshold={p['comp_thresh_db']}dB:ratio={p['comp_ratio']}:attack=10:release=200:knee=8:makeup=1.5")
    dyn = os.path.join(cache.dir, f"_dyn_{os.getpid()}.wav")
    ff('-i', pre, '-af', ','.join(chain), '-c:a', 'pcm_f32le', dyn)
    # 5) synthetic stereo room
    y = read48(dyn)
    y = add_room(y, p['room_db'], p['room_rt60']) if p['room_db'] is not None else np.repeat(y[:, None], 2, 1)
    sf.write(dyn, y, SR, subtype='FLOAT')
    # 6) two-pass linear loudness normalisation
    ln = f"loudnorm=I={p['lufs']}:TP=-1.5:LRA=20"  # wide LRA keeps loudnorm in linear (non-pumping) mode
    j = json.loads(re.search(r'\{[^{}]*\}', ff('-i', dyn, '-af', ln + ':print_format=json', '-f', 'null', '-', capture=True)).group())
    ln += (f":measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}"
           f":measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    out = os.path.join(cache.out_dir, name)
    ff('-i', dyn, '-af', ln + ',aresample=48000:filter_size=64:cutoff=0.97', '-c:a', 'pcm_s24le', out + '.wav')
    for f in (pre, dyn): os.remove(f)
    # mp3 with the source's tags (+ cover art for full renders)
    has_pic = cache.start is None and 'attached pic' in ff('-i', cache.src, '-f', 'null', '-t', '0', '-', capture=True)
    maps = ['-map', '0:a', '-map', '1:v', '-c:v', 'copy', '-disposition:v', 'attached_pic'] if has_pic else ['-map', '0:a']
    ff('-i', out + '.wav', '-i', cache.src, *maps, '-map_metadata', '1', '-c:a', 'libmp3lame', '-b:a', '192k', '-id3v2_version', '3', out + '.mp3')
    with open(out + '.json', 'w') as f: json.dump(p, f, indent=1)
    print(f"  -> {os.path.relpath(out, ROOT)}.wav/.mp3  ({time.time() - t0:.0f}s)")
    return out + '.wav'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="parameters (--set key=value):\n" + "\n".join(f"  {k:15s} {v}" for k, v in DOC.items())
                                        + "\n\npresets: " + ", ".join(PRESETS))
    ap.add_argument('input')
    ap.add_argument('--start', help='section start, seconds or mm:ss (default: whole file)')
    ap.add_argument('--dur', default='3:00', help='section length when --start is given (default 3:00)')
    ap.add_argument('--preset', default='natural', help='preset name, or comma-separated list to render several')
    ap.add_argument('--set', nargs='*', default=[], metavar='KEY=VALUE', help='override preset parameters')
    ap.add_argument('--mix', type=float, help='shortcut for --set orig_mix=N (percent of original in the mix)')
    ap.add_argument('--name', help='output name (default: preset name, plus overrides)')
    ap.add_argument('--fix', nargs='*', default=[], metavar='START-END[@MIX]',
                    help='mark a bad range (section time, e.g. 0:40-0:42 or 0:40-0:42@0.6) to fall back to the original; '
                         'saved in fixes.json and applied to all later renders of this file, including the full one')
    ap.add_argument('--clear-fixes', action='store_true', help='remove all stored fix ranges of this file (before applying --fix)')
    ap.add_argument('--include-original', action='store_true', help='also export the untouched section, loudness-matched')
    ap.add_argument('--score', action='store_true', help='print DNSMOS/UTMOS scores of the outputs')
    a = ap.parse_args()

    start = parse_time(a.start)
    cache = Cache(a.input, start, parse_time(a.dur) if start is not None else None)
    print(f"{a.input} [{cache.section}]")
    if a.clear_fixes:
        cache.clear_fixes()
    for fx in a.fix:
        rng, _, mix = fx.partition('@'); s0, s1 = rng.split('-')
        cache.add_fix(parse_time(s0), parse_time(s1), float(mix or 1.0))
    outs = []
    if a.include_original:
        o = os.path.join(cache.out_dir, '0_original')
        ff('-i', cache.source(), '-af', 'loudnorm=I=-16:TP=-1.5,aresample=48000', '-ac', '2', '-c:a', 'pcm_s24le', o + '.wav')
        ff('-i', o + '.wav', '-c:a', 'libmp3lame', '-b:a', '192k', o + '.mp3'); outs.append(o + '.wav')
    for preset in a.preset.split(','):
        p = dict(PRESETS[preset])
        if a.mix is not None: p['orig_mix'] = a.mix
        for kv in a.set:
            k, v = kv.split('=', 1)
            if k not in p: sys.exit(f"unknown parameter '{k}'. Known: {', '.join(p)}")
            p[k] = None if v.lower() in ('none', 'off') else (v if k in ('pre', 'restore', 'orig_source') else float(v))
        name = a.name or (preset + ''.join(f"_{kv.replace('=', '')}" for kv in a.set))
        print(f"* {name}")
        outs.append(render(cache, p, name))
    if a.score:
        subprocess.run([sys.executable, os.path.join(ROOT, 'eval', 'score.py'), *outs])


if __name__ == '__main__':
    main()
