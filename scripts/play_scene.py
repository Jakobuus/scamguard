"""Play a stage scene on macOS: Enter advances, r repeats, q quits."""
import argparse
import json
import logging
import math
from pathlib import Path
import subprocess
import time

CLIPS = Path(__file__).resolve().parent.parent / "clips"
log = logging.getLogger(__name__)


def play(scene: str, number: int, text: str, dry_run: bool) -> None:
    print(f"{scene}-{number}: {text}", flush=True)
    if dry_run:
        return
    try:
        clip = next((CLIPS / f"{scene}-{number}{suffix}" for suffix in (".mp3", ".m4a", ".wav")
                     if (CLIPS / f"{scene}-{number}{suffix}").is_file()), None)
        if clip is None:
            raise FileNotFoundError
        subprocess.run(["afplay", str(clip)], check=True, timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        log.warning("Clip playback unavailable; using text-only fake")


def main() -> int:
    scenes = json.loads((CLIPS / "lines.json").read_text())
    scenes.pop("_voices", None)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", choices=scenes)
    parser.add_argument("--auto", type=float, metavar="SECONDS", help="gap after each clip")
    parser.add_argument("--dry-run", action="store_true", help="print all lines without audio or waits")
    args = parser.parse_args()
    if args.auto is not None and (not math.isfinite(args.auto) or args.auto < 0):
        parser.error("--auto must be a finite, nonnegative number")
    lines = scenes[args.scene]
    if args.dry_run or args.auto is not None:
        for index, text in enumerate(lines):
            if index and not args.dry_run:
                time.sleep(args.auto)
            play(args.scene, index + 1, text, args.dry_run)
        return 0

    index = -1
    while True:
        try:
            command = input("Enter: next | r: repeat | q: quit > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if command == "q":
            return 0
        if command == "":
            index += 1
            if index == len(lines):
                return 0
        elif command != "r" or index < 0:
            continue
        play(args.scene, index + 1, lines[index], False)


if __name__ == "__main__":
    raise SystemExit(main())
