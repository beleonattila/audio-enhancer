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
        params=dict(nr="How much the noise is reduced. 6–12 dB sounds natural; above ~20 dB artifacts appear.",
                    nf="Estimated level of the hiss in pauses. Around -50 dBFS for phone recordings.")),
    'wpe': dict(
        desc="Weighted Prediction Error dereverberation: predicts the late reverb from the past signal and subtracts it. "
             "Purely linear, it never invents sound and keeps overlapping voices intact. Works better on real stereo.",
        where="First step, on the untouched recording.",
        params=dict(taps="How far back the reverb is predicted, in 16 ms frames. More taps remove longer tails but cost "
                         "time. 8–15.",
                    delay="Frames skipped before prediction; protects the direct sound and early reflections. 2–4.")),
    'eq': dict(
        desc="Tone shaping: the high-pass removes rumble; the shelves and bells set body, boxiness, intelligibility, "
             "presence and air.",
        where="Mastering, first: before de-esser and dynamics.",
        params=dict(hp="Everything below this is removed. 70–100 Hz for voices.",
                    warmth="Low shelf at 180 Hz: chest and body. +1 to +3 dB for thin voices.",
                    mud="Bell at 250 Hz: negative values remove the boxy room sound.",
                    mid="Bell at 1.2 kHz: forwardness and intelligibility.",
                    presence="Bell at 4.2 kHz: clarity. Too much sounds harsh.",
                    air="High shelf at 10 kHz: sparkle. Negative values give a rounder, less 'digital' top.")),
    'deesser': dict(
        desc="Reduces sharp 's' and 'sh' sounds, which generative restoration and presence boosts tend to exaggerate.",
        where="Mastering, after the EQ.",
        params=dict(i="0 = off. 0.2–0.4 is usually enough.")),
    'expander': dict(
        desc="Downward expander: turns the pauses (room tone, breaths, noise) down without touching speech. "
             "Too much makes the pauses dead silent, which sounds sterile.",
        where="Mastering, before the compressor.",
        params=dict(range="Maximum reduction in pauses. 3–8 dB stays natural; 0 = off.")),
    'compressor': dict(
        desc="Evens out the loudness between the louder and the quieter host, and within phrases. The input is "
             "normalised first, so the threshold behaves the same on any recording.",
        where="Mastering, after EQ / de-esser / expander.",
        params=dict(ratio="1.5–2.5 is gentle levelling; 3 and above sounds like broadcast radio.",
                    thresh="Compression starts above this level (input at -21.5 LUFS). -24 to -18 dBFS.")),
    'room': dict(
        desc="Adds a quiet synthetic stereo room: space and width for very dry results (e.g. after Sidon). "
             "Not a fix for recordings that are already roomy.",
        where="Mastering, just before Loudness.",
        params=dict(level="How loud the room is under the voice. -30 to -20 dB stays subtle.",
                    rt60="Decay time: 0.2 s small treated room, 0.4 s living room.")),
    'mix': dict(
        desc="Blends the current audio with the input or an earlier step, both matched to the same speech level. "
             "Brings back natural texture and room tone, and hides the gating and warble of generative steps.",
        where="After the neural steps and Dropout repair, before mastering.",
        params=dict(percent="Share of the other source: 10–30 % is subtle, 50 % is equal parts.",
                    **{'with': "The input (original), or any earlier step — e.g. a filter-only step like ClearerVoice SE."})),
    'repair': dict(
        desc="Compares the audio with a lightly denoised input every 10 ms. Where a neural step erased speech (quiet "
             "syllables, consonants under overlapping speech), it crossfades to the EQ-matched original for that moment.",
        where="Right after the generative step (and its DeepFilterNet3).",
        params=dict(deficit="How much quieter than the original (dB) a spot must be to count as lost. Lower = more "
                            "sensitive, more patches. 10–14.")),
    'loudness': dict(
        desc="Two-pass EBU R128 loudness normalisation with a true-peak limit.",
        where="Always the final step.",
        params=dict(lufs="-16 for podcasts, -14 for YouTube / streaming, -19 for mono podcasts.",
                    tp="Maximum true peak. -1 to -1.5 dBTP keeps MP3 / AAC encoding from clipping.")),
    'dfn': dict(
        desc="DeepFilterNet3: neural noise suppression at 48 kHz. Filter-type, so it keeps the original voice; removes "
             "steady and changing noise, but does not remove reverb or rebuild high frequencies.",
        where="As the cleaner early on, or lightly right after a generative step.",
        params=dict(atten="Limits how much noise is removed. 8–15 dB keeps a natural floor; more is cleaner but "
                          "can sound processed.")),
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
        params=dict(model="aggressive: strongest, can thin the voice · less aggressive: safer · mono: for mono files "
                          "(chosen automatically when the input is mono).")),
    'uvr_deecho': dict(
        desc="UVR DeEcho-DeReverb (VR architecture): an older echo and reverb remover from Ultimate Vocal Remover; "
             "milder than RoFormer.",
        where="First step, on the untouched recording.", params={}),
    'resemble': dict(
        desc="resemble-enhance: a denoiser plus a generative enhancer. Rebuilds bandwidth like Sidon with a different "
             "character; slower.",
        where="The one generative step, after cleaning.",
        params=dict(mode="enhance = denoise + generative restoration · denoise = only the denoiser (filter-type).",
                    lambd="Denoising strength inside the enhancer, 0–1. 0.9 is strong.")),
    'voicefixer': dict(
        desc="VoiceFixer: general restoration (noise, reverb, clipping, bandwidth) with a neural vocoder at 44.1 kHz. "
             "Strong, but it can noticeably change the voice character.",
        where="The one generative step, on badly damaged audio.",
        params=dict(mode="0 = default · 1 = with extra pre-processing · 2 = variant for very degraded audio.")),
}
