# Hero-run demo video

A silent screen recording of the console: the cinematic hero run at 1x (seed 1), the offline
verification, and a tour of the command centre, analyst console, evidence page, citizen shield
(English, then Tamil) and insights page. Everything on screen is simulated data.

| File | What | Size |
|---|---|---|
| `hero-run.webm` | main recording, VP9, 1440x900, 25 fps, 2:49 | about 4.7 MB |
| `hero-run.mp4` | same, H.264, for players that lack webm | about 4.5 MB |
| `hero-run.gif` | 45 s preview (call flagged to evidence sealed), 560 px wide | about 1.4 MB |
| `hero-run.vtt` | WebVTT caption track (sidecar; not burned in) | small |
| `hero-run.marks.json` | segment start times in seconds, from the recording script | small |

## What it shows (approximate clock)

| Clock | Segment |
|---|---|
| 0:00 | title card (3 s): product name, tagline, "Simulated data" |
| 0:03 | splash, Officer role, idle live page |
| 0:14 | Play at 1x: call flagged, transfer held, antibody shared, bank B blocks, cash-out, evidence sealed (run takes about 72 s) |
| 1:27 | finale card, **Verify offline**, result `PASS_PINNED` |
| 1:40 | `/command` (about 12 s), `/analyst` (about 11 s), `/evidence` build, export and verify (about 13 s) |
| 2:17 | `/citizen`: sample digital-arrest message, verdict in English, then Tamil |
| 2:35 | `/insights` scroll, then outro card: `make demo` and a repository placeholder (see Honest notes) |

Exact segment times are in `hero-run.marks.json`; `hero-run.vtt` is built from them and the seed-1
step offsets measured in [../demo-script.md](../demo-script.md). The console already shows step
captions on the player; the caption track adds the talk-track gist.

## Honest notes

* **Known cosmetic gap:** the outro card in the recording still shows a repository placeholder
  instead of the real URL (<https://github.com/aman-18-choudhary/ai_powered_immune>, branch
  `build/implementation`). It was not re-recorded; the deck and README carry the real URL.
* **No audio narration was recorded.** The voice-over script is in
  [../demo-script.md](../demo-script.md) for a human to read over this video (or a re-take).
* The recording is of the static console build (`web-console/dist`) served by the demo server,
  the same process `make demo` runs. The multi-container Docker stack is not shown and was not run.
* The citizen verdict card translates the headline, summary and advice into Tamil; the list of
  reasons stays in English in the current console build.
* A green dot follows the mouse so the pointer is visible in a headless recording. The
  "sample" text is filled into the box rather than pasted from the clipboard.
* Timings are real wall time from one recording; a different machine will differ by a second or two.
* The video was checked by extracting frames at 13 points (title, idle, each run stage, finale,
  verify result, each page, outro): none blank or black.

## Re-record

```bash
cd web-console && npm run build && cd ..
source .venv/bin/activate
PORT=18095 DEMO_STATIC_DIR=web-console/dist DEMO_CORS_ORIGINS=http://127.0.0.1:18095 python -m demo_server &
# use a FRESH server: a previous run is replayed on the live page otherwise
cd web-console && CONSOLE_URL=http://127.0.0.1:18095 OUT_DIR=../docs/demo node e2e/record-hero.mjs
kill %1     # stop the demo server
```

The script writes a large raw webm (about 40 MB). Re-encode and rebuild the extras:

```bash
cd docs/demo
mv hero-run.webm /tmp/raw.webm
ffmpeg -i /tmp/raw.webm -c:v libvpx-vp9 -crf 36 -b:v 0 -row-mt 1 -an hero-run.webm
ffmpeg -i /tmp/raw.webm -c:v libx264 -crf 26 -preset slow -pix_fmt yuv420p -movflags +faststart -an hero-run.mp4
ffmpeg -ss 28 -t 45 -i /tmp/raw.webm -vf "fps=4,scale=560:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64[p];[b][p]paletteuse=dither=bayer:bayer_scale=5" hero-run.gif
python3 make_vtt.py
```

Source: `web-console/e2e/record-hero.mjs` (Playwright chromium video, smooth mouse moves).
