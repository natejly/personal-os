# Voice input

The mic button in the chat composer (and the dictation chord, set in Settings → Behavior) records a short
clip, sends it to `POST /stt/transcribe`, and puts the text in the message box. Nothing is sent as a message
and nothing is kept: the clip goes to a temp file that is removed whatever happens. Clips are limited to two
minutes.

## Settings

Settings → Advanced → Voice and shortcuts, stored under the settings key `voice` and served by `GET`/`PUT /voice/config`
(a partial patch; the stored value is validated and the full config is returned).

| Field | Default | Meaning |
| --- | --- | --- |
| `sttBackend` | `auto` | `auto`, `speech`, `whistle`, `proxy`, `local` or `off` |
| `sttModel` | `whisper-1` | Model name the `proxy` backend asks `/v1/audio/transcriptions` for |
| `whisperModelPath` | empty | ggml weights for `local`; empty means the first `*.bin` in `<data_dir>/models` |
| `whisperVadModelPath` | empty | Optional Silero ggml for whisper.cpp `--vad` |
| `hallucinationFilter` | on | Drop the phrases a recogniser invents on silence, and runs of a repeated word |
| `dictationCleanup` | off | One short model pass over each clip to fix punctuation, case and filler words |

A database written by an older build has no `voice` row; the first read seeds these fields from the matching
fields of its old `meetings` settings row, so a configured backend carries over.

## Backends

`auto` prefers Apple's on-device Speech framework when it is authorized, then Whistle if it is installed, then
whisper.cpp when both the binary and a model exist, then the proxy. Audio stays on the machine for every backend
except `proxy`.

```bash
cd backend && uv pip install -e '.[mac]'        # Speech and the permission probes
cd backend && uv pip install -e '.[whistle]'    # Whistle: 17 MB, seven languages, CPU only
brew install whisper-cpp                        # whisper.cpp

# The ggml model goes in <data_dir>/models/. On a fresh install data_dir is
# ~/Library/Application Support/Grain/data; installs that predate the rename keep .../personal-os/data.
DATA="$HOME/Library/Application Support/Grain/data"
mkdir -p "$DATA/models"
curl -L -o "$DATA/models/ggml-base.en.bin" \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
```

The `proxy` backend POSTs to `/v1/audio/transcriptions` on the configured base URL. A default `litellm.yaml` has no
model behind that path; see the commented blocks in it.

`POST /stt/transcribe` answers 409 with the exact fix when the chosen backend cannot run.

## Permissions

Microphone permission is required: Settings → Privacy & Security → Microphone, enabled for the app. macOS attributes
the grant to the bundle, so it is Grain in a packaged build and Electron in development, and a grant made after
launch does not take until the app restarts. The Speech backend also needs Speech Recognition in the same pane.
`POST /system/permissions/request` asks macOS, and `POST /system/permissions/open` opens the right pane; neither is
called unless the user pressed the matching button.
