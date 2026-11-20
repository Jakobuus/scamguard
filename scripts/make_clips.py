"""Generate stage clips with ElevenLabs; default fake mode only prints the lines."""
import argparse
import json
import logging
import os
from pathlib import Path
from urllib.parse import quote

import httpx

CLIPS = Path(__file__).resolve().parent.parent / "clips"
API = "https://api.elevenlabs.io/v1"
STOCK_SCENES = {"delivery", "deliveryfee"}
log = logging.getLogger(__name__)


def main() -> int:
    scenes = json.loads((CLIPS / "lines.json").read_text())
    voices = scenes.pop("_voices", {})  # scene -> voice name
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--sample", type=Path, help="audio file for an instant voice clone")
    source.add_argument("--voice-id", help="reuse a voice instead of cloning")
    parser.add_argument("--voice-name", help="clone or reuse a named second voice (clips/voice_id.NAME.txt); generates only scenes bound to it in _voices")
    parser.add_argument("--stock-voice-id", help="stock voice for the delivery scene")
    parser.add_argument("--only", choices=scenes, help="generate just one scene")
    parser.add_argument("--dry-run", action="store_true", help="preview without network calls or writes")
    args = parser.parse_args()
    real = os.environ.get("FAKE", "1") == "0" and os.environ.get("REAL_ELEVENLABS", "0") == "1"
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    headers = {"xi-api-key": key}
    voice_id = args.voice_id
    named = {}
    if args.voice_name:
        id_file = CLIPS / f"voice_id.{args.voice_name}.txt"
        if args.sample:
            if args.dry_run:
                print(f"Would clone one audio sample and save {id_file.name}")
            elif real:
                try:
                    if not key:
                        raise ValueError("missing key")
                    with args.sample.open("rb") as sample:
                        response = httpx.post(
                            f"{API}/voices/add", headers=headers, timeout=60,
                            data={"name": args.voice_name},
                            files={"files": (args.sample.name, sample)},
                        )
                    response.raise_for_status()
                    named[args.voice_name] = response.json()["voice_id"]
                    id_file.write_text(named[args.voice_name] + "\n")
                except Exception as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    log.warning("ElevenLabs cloning failed (status %s)", status)
            else:
                print("Fake clone: no upload or voice id saved")
        elif voice_id:
            named[args.voice_name] = voice_id
        voice_id = None
        args.sample = None
    if args.sample:
        if args.dry_run:
            print("Would clone one audio sample and save clips/voice_id.txt")
        elif real:
            try:
                if not key:
                    raise ValueError("missing key")
                with args.sample.open("rb") as sample:
                    response = httpx.post(
                        f"{API}/voices/add", headers=headers, timeout=10,
                        data={"name": "stage-caller"},
                        files={"files": (args.sample.name, sample)},
                    )
                response.raise_for_status()
                voice_id = response.json()["voice_id"]
                if not isinstance(voice_id, str) or not voice_id.strip():
                    raise ValueError("missing voice id")
                (CLIPS / "voice_id.txt").write_text(voice_id + "\n")
            except Exception:
                log.warning("ElevenLabs cloning failed; using text-only fake")
                voice_id = None
        else:
            print("Fake clone: no upload or voice id saved")
    elif not voice_id and real and not args.dry_run and not args.voice_name and args.only not in STOCK_SCENES:
        try:
            voice_id = (CLIPS / "voice_id.txt").read_text().strip() or None
        except OSError:
            pass

    for scene, lines in scenes.items():
        if args.only and scene != args.only:
            continue
        bound = voices.get(scene)
        if args.voice_name and bound != args.voice_name:
            continue
        if bound and bound not in named and real and not args.dry_run:
            try:
                named[bound] = (CLIPS / f"voice_id.{bound}.txt").read_text().strip() or None
            except OSError:
                pass
        if scene in STOCK_SCENES and not args.stock_voice_id:
            print(f"Skip {scene}: provide --stock-voice-id")
            continue
        selected_voice = (args.stock_voice_id if scene in STOCK_SCENES
                          else named.get(bound) if bound else voice_id)
        for number, text in enumerate(lines, 1):
            target = CLIPS / f"{scene}-{number}.mp3"
            if args.dry_run:
                voice = "stock voice" if scene in STOCK_SCENES else "caller voice"
                print(f"Would synthesize {target.name} with {voice}: {text}")
                continue
            if real:
                try:
                    if not key or not selected_voice:
                        raise ValueError("missing key or voice")
                    response = httpx.post(
                        f"{API}/text-to-speech/{quote(selected_voice, safe='')}",
                        headers=headers, timeout=10,
                        json={"text": text, "model_id": "eleven_multilingual_v2"},
                    )
                    response.raise_for_status()
                    if not response.content:
                        raise ValueError("empty audio")
                    target.write_bytes(response.content)
                    print(f"Saved {target.name}")
                    continue
                except Exception:
                    log.warning("ElevenLabs speech failed; using text-only fake")
            print(f"Fake {target.name}: {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
