#!/bin/bash
# Creates the Python environments with uv (https://docs.astral.sh/uv/).
#   ./setup.sh          main pipeline + control panel: .venv (ffmpeg, evaluation, Flask, WPE), envs/sidon, envs/dfn
#   ./setup.sh --all    also envs/clearvoice (ClearerVoice SE/SR, speaker separation), envs/sep (RoFormer / UVR de-reverb),
#                       envs/resemble and envs/vf
# Each tool gets its own venv because their dependency pins conflict. Python 3.11, PyTorch 2.5.1 + CUDA 12.4.
set -e
cd "$(dirname "$0")"
pyenv() { if [ -x "$1/Scripts/python.exe" ]; then echo "$1/Scripts/python.exe"; else echo "$1/bin/python"; fi; }
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$PWD/.uvpython}"
TORCH=(torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu124)

mkenv() {  # mkenv <dir> <packages...>
  local dir="$1"; shift
  [ -d "$dir" ] || uv venv --python 3.11 "$dir"
  uv pip install --python "$(pyenv "$dir")" "${TORCH[@]}"
  [ $# -eq 0 ] || uv pip install --python "$(pyenv "$dir")" "$@"
}

mkenv .venv imageio-ffmpeg numpy scipy soundfile librosa pyloudnorm matplotlib torchmetrics onnxruntime requests flask nara_wpe
mkenv envs/sidon "transformers==5.18.0" huggingface_hub soundfile numpy
mkenv envs/dfn "deepfilternet==0.5.6" soundfile "numpy<2"

if [ "$1" = "--all" ]; then
  # resemble-enhance pins deepspeed (training only, no Windows build) -> install without deps
  mkenv envs/resemble "numpy<2" "librosa==0.10.1" soundfile scipy omegaconf resampy tqdm rich matplotlib pandas celluloid tabulate ptflops huggingface_hub
  uv pip install --python "$(pyenv envs/resemble)" --no-deps resemble-enhance==0.0.1

  mkenv envs/clearvoice "clearvoice==0.1.2" soxr imageio-ffmpeg
  uv pip install --python "$(pyenv envs/clearvoice)" --reinstall-package torch --reinstall-package torchaudio "${TORCH[@]}"
  # clearvoice 0.1.2 bug: MossFormer2_SR_48K crashes for batch size 1 (squeeze() drops the batch dim)
  DB="$(find envs/clearvoice -path '*clearvoice/utils/decode_batch.py' | head -1)"
  sed -i 's/outputs = generator_output\.squeeze()/outputs = generator_output.reshape(b, -1)/' "$DB"

  mkenv envs/vf voicefixer soundfile

  # audio-separator (RoFormer / UVR de-reverb) shells out to `ffmpeg`: put a copy next to its executables
  mkenv envs/sep "audio-separator[gpu]"
  FFBIN="$("$(pyenv .venv)" -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
  if [ -d envs/sep/Scripts ]; then cp "$FFBIN" envs/sep/Scripts/ffmpeg.exe; else cp "$FFBIN" envs/sep/bin/ffmpeg; fi
fi
echo "setup complete"
