"""Terminal playback from the distributed scalar sequence and rational timing."""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from fractions import Fraction
from pathlib import Path


def play(path: Path, fps: str | None = None, speed: float = 1.0):
    if speed <= 0:
        raise ValueError("speed must be positive")
    text = path.read_text(encoding="utf-8")
    timing_path = path.with_name("timing.json")
    timing = json.loads(timing_path.read_text(encoding="utf-8")) if timing_path.exists() else {}
    rate = Fraction(fps or timing.get("fps", "30"))
    if rate <= 0:
        raise ValueError("fps must be positive")
    if "frame_count" in timing and len(text) != timing["frame_count"]:
        raise ValueError("characters.txt length disagrees with timing.json")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 4)
    sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[2J")
    start = time.perf_counter()
    try:
        last_index = -1
        while text:
            elapsed = (time.perf_counter()-start) * speed
            index = int(elapsed * float(rate))
            if index >= len(text):
                break
            if index != last_index:
                char = text[index]
                # Clear first: combining or zero-advance codepoints must not attach
                # to the previous frame. Terminal fallback/display is font dependent.
                sys.stdout.write(f"\x1b[H\x1b[2K{char}\n\x1b[2Kframe {index+1}/{len(text)}  U+{ord(char):04X}")
                sys.stdout.flush()
                last_index = index
            next_time = (index+1) / float(rate) / speed + start
            time.sleep(max(0, min(0.02, next_time-time.perf_counter())))
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Play one Unicode codepoint per frame")
    parser.add_argument("path", type=Path, nargs="?", default=Path(__file__).with_name("characters.txt"))
    parser.add_argument("--fps")
    parser.add_argument("--speed", type=float, default=1)
    arguments = parser.parse_args()
    play(arguments.path, arguments.fps, arguments.speed)
