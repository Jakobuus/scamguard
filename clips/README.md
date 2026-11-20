# Stage clips

`lines.json` holds the caller's lines in playback order. These scripts run separately
from the app and do not change the call flow or the seams in `docs/SYSTEM.md`.

Preview generation with no keys, uploads or files written:

```sh
uv run python scripts/make_clips.py --dry-run
uv run python scripts/make_clips.py --sample sample.wav --dry-run
```

To generate speech, export `ELEVENLABS_API_KEY`, set `FAKE=0` and
`REAL_ELEVENLABS=1`, then run:

```sh
uv run python scripts/make_clips.py --sample sample.wav
uv run python scripts/make_clips.py --voice-id VOICE_ID --only scam
uv run python scripts/make_clips.py --stock-voice-id STOCK_ID --only delivery
```

A successful clone saves `voice_id.txt`; later runs reuse it unless given a new
sample or voice id. Delivery needs `--stock-voice-id` and is skipped otherwise.
Real audio is saved as `<scene>-<n>.mp3`. Audio and the saved voice id are ignored
by git. Each network request has a ten-second timeout. Fake mode is the default;
it prints the lines without network calls or writing audio. On a service error,
the script logs one generic line and prints the affected fake result. Existing
clips stay in place, so check the output before using them on stage.

Play on macOS with speakers routed to the stage audio:

```sh
uv run python scripts/play_scene.py scam
uv run python scripts/play_scene.py codeword --auto 2
uv run python scripts/play_scene.py delivery --dry-run
```

Enter plays the next line, `r` repeats the last line, and `q` quits. After the last
line, `r` still repeats and Enter finishes. Auto mode waits the given number of
seconds after each clip finishes. Dry run prints every line without audio or
waiting. Playback uses `afplay`, preferring MP3, then M4A, then WAV. Missing clips
or playback errors fall back to showing the text.
