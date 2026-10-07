"""Explanations shown in the panel's "Active step" help box: what each step does, where it belongs, and its parameters."""

INPUT = dict(
    desc="The recording the pipeline runs on: the whole file, or the section kept with Cut. Click the tile to choose a file; "
         "the ↶ button shows the original input in the ribbon again.",
    where="Always first.")

STEPS = {
    'dsp_denoise': dict(
        desc="Classic spectral noise reduction (ffmpeg afftdn): estimates a steady noise floor and subtracts it. "
             "Fast and predictable, but strong settings leave 'musical noise', and it does nothing against reverb.",
        where="Early, as the cleaner; or not at all if a neural cleaner is used.",
        params=dict(
            nr="How far the estimated noise is pushed down. Higher values remove more hiss but start to 'chew' the voice "
               "and leave bubbly, warbling 'musical noise' in the pauses; lower values keep the voice natural but leave "
               "some hiss. 6–12 dB sounds natural, 12–20 dB suits noisy recordings, above ~20 dB the artifacts are "
               "usually audible.",
            nf="The level the filter assumes for the noise floor, i.e. the hiss you hear in the quietest pauses. Set too "
               "high (e.g. -30 dBFS), quiet speech is mistaken for noise and gets thinned; set too low (e.g. -75 dBFS), "
               "the filter barely acts. Phone recordings usually sit around -50 to -60 dBFS.")),
    'wpe': dict(
        desc="Weighted Prediction Error dereverberation: predicts the late reverb from the past signal and subtracts it. "
             "Purely linear, it never invents sound and keeps overlapping voices intact. Works better on real stereo.",
        where="First step, on the untouched recording.",
        params=dict(
            taps="Length of the prediction window, in analysis frames (one frame is about 5–6 ms here). The filter can "
                 "only cancel reverb it can predict from this window: more taps remove longer reverb tails, but run "
                 "slower and can start to colour the voice. 8–15 for a living room, 20–30 for large, echoey rooms.",
            delay="Frames skipped between the current sound and the prediction window. That first stretch — the direct "
                  "sound and the early reflections — is left untouched, which keeps the voice natural; only the later "
                  "reverb is removed. Lower values (1–2) remove more, including early reflections, and can thin the "
                  "voice; higher values (4–6) are gentler. 2–4 is typical.")),
    'eq': dict(
        desc="Tone shaping: the high-pass removes rumble; the shelves and bells set body, boxiness, intelligibility, "
             "presence and air.",
        where="Mastering, first: before de-esser and dynamics.",
        params=dict(
            hp="Everything below this frequency is removed: table thumps, handling noise, traffic rumble, mains hum and "
               "boominess. Voices carry almost nothing useful below ~80 Hz (female voices below ~120 Hz). Higher values "
               "sound thinner and cleaner. 70–100 Hz is safe; 120–150 Hz for very boomy rooms.",
            warmth="A low shelf at 180 Hz raises or lowers everything below it: the body and chest resonance of the "
                   "voice. Positive values make thin phone recordings fuller and warmer; too much sounds muddy or boomy. "
                   "Negative values tighten a bassy recording. Typical 0 to +3 dB.",
            mud="A bell around 250 Hz, where small rooms add a boxy, 'cardboard' resonance and phones pile up low-mid "
                "energy. Cutting here (-1 to -4 dB) makes the voice clearer and less roomy without losing body; boosting "
                "makes it thicker. Usually negative.",
            mid="A broad bell around 1.2 kHz: the range that makes a voice sound forward, 'in front of the speakers', and "
                "keeps it intelligible at low volume. +1 to +2 dB brings the hosts forward; too much sounds nasal or "
                "honky. Negative values push the voice back.",
            presence="A bell around 4.2 kHz: consonant definition and clarity. A small boost (+1 to +3 dB) helps muffled "
                     "recordings; too much makes the voice harsh and tiring and exaggerates 's' sounds (raise the "
                     "De-esser if you boost here). After Sidon the top is already bright, so keep it small.",
            air="A high shelf from 10 kHz up: breath, sparkle, the open sound of a good microphone. Positive values add "
                "openness but also lift hiss and the slightly digital sheen of generative steps; negative values (-1 to "
                "-3 dB) give a rounder, more analogue sound. Typical -1 to +2 dB.")),
    'deesser': dict(
        desc="Reduces sharp 's' and 'sh' sounds, which generative restoration and presence boosts tend to exaggerate.",
        where="Mastering, after the EQ.",
        params=dict(
            i="How strongly sibilance — the hissy energy of s, sh, z and ts sounds around 5–9 kHz — is turned down when "
              "it jumps out. 0 is off; 0.2–0.4 tames harsh esses transparently; above ~0.5 the voice can start to sound "
              "lispy or dull. Raise it if you boosted Presence or Air, or if Sidon made the esses sharp.")),
    'expander': dict(
        desc="Downward expander: turns the pauses (room tone, breaths, noise) down without touching speech. "
             "Too much makes the pauses dead silent, which sounds sterile.",
        where="Mastering, before the compressor.",
        params=dict(
            range="The most the expander turns the audio down while nobody is speaking (room tone, breathing, chair "
                  "noise, leftover hiss). 0 disables it. 3–8 dB makes pauses calmer while keeping a natural sense of the "
                  "room; 10 dB and more makes pauses near-silent, which sounds clean but sterile and can clip quiet word "
                  "endings. Keep it low if you mix in the original or add a Room.")),
    'compressor': dict(
        desc="Evens out the loudness between the louder and the quieter host, and within phrases. The input is "
             "normalised first, so the threshold behaves the same on any recording.",
        where="Mastering, after EQ / de-esser / expander.",
        params=dict(
            ratio="How strongly the loud parts are turned down: at 2:1, every 2 dB the voice rises above the threshold "
                  "comes out as 1 dB. Higher ratios make the two hosts and the loud and quiet moments more equal (easier "
                  "listening in a car or with earbuds) but reduce liveliness and lift the background in pauses. "
                  "1.5–2.5 is gentle podcast levelling, 3–4 sounds like radio.",
            thresh="The level where compression starts. The input is first normalised to -21.5 LUFS, so this value means "
                   "the same on every recording. Lower values (e.g. -26 dBFS) compress most of the speech, including "
                   "normal talking; higher values (e.g. -16 dBFS) only catch the loudest moments such as laughs. "
                   "-24 to -18 dBFS is typical.")),
    'room': dict(
        desc="Adds a quiet synthetic stereo room: space and width for very dry results (e.g. after Sidon). "
             "Not a fix for recordings that are already roomy.",
        where="Mastering, just before Loudness.",
        params=dict(
            level="How loud the synthetic room is relative to the voice. It adds a sense of space and stereo width to "
                  "very dry, close-sounding results. -30 dB is barely noticeable, -24 dB subtle, -18 dB clearly audible; "
                  "more sounds like a bathroom. Never use it to fix a recording that is already echoey.",
            rt60="The room's decay time: how long the reverb tail takes to fade away (by 60 dB). 0.2–0.3 s sounds like a "
                 "small furnished room or a studio, 0.4–0.5 s like a living room; longer values sound like a hall and "
                 "start to smear the speech.")),
    'mix': dict(
        desc="Blends the current audio with the input or an earlier step, both matched to the same speech level. "
             "Brings back natural texture and room tone, and hides the gating and warble of generative steps.",
        where="After the neural steps and Dropout repair, before mastering.",
        params=dict(
            percent="How much of the other source goes into the mix, after both are matched to the same speech level. "
                    "10–20 % adds a hint of natural texture and keeps the room tone continuous; 30–50 % clearly brings "
                    "back the original character (and some of its noise and reverb) and hides the warble and gating of "
                    "generative steps; 50 % is equal parts. Above 50 % the other source dominates.",
            **{'with': "Which audio is mixed in. 'Input' is the untouched recording: the most natural, but it also brings "
                       "back its noise and room. An earlier step such as ClearerVoice SE is cleaned but not re-synthesised: "
                       "natural texture without the noise. It must be a step above this one."})),
    'repair': dict(
        desc="Compares the audio with a lightly denoised input every 10 ms. Where a neural step erased speech (quiet "
             "syllables, consonants under overlapping speech), it crossfades to the EQ-matched original for that moment.",
        where="Right after the generative step (and its DeepFilterNet3).",
        params=dict(
            deficit="Every 10 ms the speech level of the current audio is compared with the original. If it is at least "
                    "this many dB quieter while the original clearly contains speech, the spot counts as erased and is "
                    "patched with the original. Lower values (8–10 dB) also catch partial losses but patch more often "
                    "(patches can sound slightly duller); higher values (14–18 dB) only fix obvious dropouts. 12 dB is a "
                    "good start. Very short spots must be 8 dB deeper and start with a rising onset, so room echo is "
                    "not patched.")),
    'loudness': dict(
        desc="Two-pass EBU R128 loudness normalisation with a true-peak limit.",
        where="Always the final step.",
        params=dict(
            lufs="The target average loudness over the whole file, in LUFS (the broadcast loudness unit). -16 is the "
                 "podcast standard for stereo, -19 for mono podcasts, -14 matches what YouTube and Spotify play at. "
                 "Closer to 0 is louder. The whole file is turned up or down evenly, so its dynamics stay the same.",
            tp="The highest allowed true peak, including the peaks that appear between samples when the file is played "
               "back or encoded. -1 to -1.5 dBTP leaves enough headroom for MP3 / AAC encoding and playback not to "
               "clip. If reaching the loudness target would exceed it, the peaks are gently limited.")),
    'dfn': dict(
        desc="DeepFilterNet3: neural noise suppression at 48 kHz. Filter-type, so it keeps the original voice; removes "
             "steady and changing noise, but does not remove reverb or rebuild high frequencies.",
        where="As the cleaner early on, or lightly right after a generative step.",
        params=dict(
            atten="The most DeepFilterNet may turn the noise down. It is a limit, not a strength: low values (6–10 dB) "
                  "leave a soft, natural noise floor and avoid the 'underwater' or gated sound; 12–20 dB removes noise "
                  "much more completely; without a limit (40 dB) pauses become silent and the voice can sound "
                  "processed. After Sidon, 8–15 dB just cleans up the leftovers.")),
    'cvse': dict(
        desc="ClearerVoice MossFormer2 SE 48k: neural speech enhancement that filters noise while keeping the original "
             "voice. Handles overlapping speakers well — a good first cleaner.",
        where="Early, after any de-reverb step.", params={}),
    'cvsr': dict(
        desc="ClearerVoice MossFormer2 SR 48k: neural bandwidth extension that predicts missing high frequencies. "
             "On very muffled sources it can sound artificial.",
        where="After cleaning; not together with Sidon.", params={}),
    'sidon': dict(
        desc="Sidon: multilingual generative speech restoration. Re-synthesises the voice at 48 kHz — removes noise, "
             "reverb and codec damage, rebuilds the high frequencies. Very clean and full, but can sound sterile, gate "
             "between phrases, and drop or warble quiet or overlapping speech.",
        where="The one generative step, after cleaning; follow with Dropout repair and a Mix.", params={}),
    'sep_sidon': dict(
        desc="Splits the two speakers with MossFormer2 separation, restores each voice alone with Sidon, then adds them "
             "back together. Sidon never hears two voices at once, so overlapping speech survives better. About twice "
             "as slow as Sidon.",
        where="Instead of Sidon when the hosts talk over each other.", params={}),
    'roformer': dict(
        desc="MelBand RoFormer de-reverb (anvuew's vocal models): estimates the reverb and removes it without "
             "re-synthesising the voice. The stereo models rely on differences between the channels — they do nothing "
             "on mono or dual-mono files.",
        where="First step, on the untouched recording.",
        params=dict(
            model="Which de-reverb model to use. 'aggressive' removes the most reverb but can thin the voice and swallow "
                  "soft word endings; 'less aggressive' keeps more body and is the safe choice; 'mono' is trained for "
                  "single-channel audio — use it for mono or dual-mono files. It is chosen automatically when the input "
                  "is mono, because the stereo models leave mono audio unchanged.")),
    'uvr_deecho': dict(
        desc="UVR DeEcho-DeReverb (VR architecture): an older echo and reverb remover from Ultimate Vocal Remover; "
             "milder than RoFormer.",
        where="First step, on the untouched recording.", params={}),
    'resemble': dict(
        desc="resemble-enhance: a denoiser plus a generative enhancer. Rebuilds bandwidth like Sidon with a different "
             "character; slower.",
        where="The one generative step, after cleaning.",
        params=dict(
            mode="'enhance' runs the denoiser and then the generative enhancer, which rebuilds the high frequencies and "
                 "removes reverb (the same role as Sidon, with a different character, and slower). 'denoise' runs only "
                 "the first, filter-type denoiser: it keeps the original voice and just removes noise.",
            lambd="Denoising strength used inside the enhancer, 0–1. Higher values give the enhancer a cleaner input, so "
                  "the result is cleaner but more synthetic; lower values keep more of the original signal and its "
                  "noise. 0.5 is balanced, 0.9 strong. Only matters in 'enhance' mode.")),
    'voicefixer': dict(
        desc="VoiceFixer: general restoration (noise, reverb, clipping, bandwidth) with a neural vocoder at 44.1 kHz. "
             "Strong, but it can noticeably change the voice character.",
        where="The one generative step, on badly damaged audio.",
        params=dict(
            mode="0 is the standard model and the best starting point. 1 adds a pre-processing step that removes some "
                 "high-frequency noise first — try it if mode 0 leaves hiss. 2 is a variant meant for severely degraded "
                 "audio and can be unstable on normal recordings.")),
}
