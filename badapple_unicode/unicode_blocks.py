"""Unicode 17 block names and inclusive ranges from the official UCD."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

MAX_CODEPOINT = 0x10FFFF


@dataclass(frozen=True)
class UnicodeBlock:
    start: int
    end: int
    name: str


@lru_cache(maxsize=1)
def blocks() -> tuple[UnicodeBlock, ...]:
    data = files("badapple_unicode").joinpath("data/Blocks-17.0.0.txt").read_text(encoding="utf-8")
    if "# Blocks-17.0.0.txt" not in data[:100]:
        raise ValueError("Expected official Unicode 17.0 block data")
    result = []
    for line in data.splitlines():
        line = line.split("#",1)[0].strip()
        if not line:
            continue
        interval, name = line.split(";",1)
        start, end = (int(part,16) for part in interval.strip().split(".."))
        if not 0 <= start <= end <= MAX_CODEPOINT or (result and start <= result[-1].end):
            raise ValueError("Invalid or overlapping Unicode block intervals")
        result.append(UnicodeBlock(start,end,name.strip()))
    if not result:
        raise ValueError("Unicode block data is empty")
    return tuple(result)


@lru_cache(maxsize=4096)
def unicode_block(codepoint: int) -> UnicodeBlock | None:
    if not 0 <= codepoint <= MAX_CODEPOINT:
        raise ValueError("Codepoint must be in U+0000..U+10FFFF")
    items = blocks()
    index = bisect_right(tuple(block.start for block in items),codepoint)-1
    if index >= 0 and codepoint <= items[index].end:
        return items[index]
    return None


def number_line_position(codepoint: int, start: int, end: int, left: float, right: float) -> float:
    """Linear mapping with inclusive endpoints; a singleton sits at the center."""
    if not start <= codepoint <= end or left > right:
        raise ValueError("Invalid number-line interval")
    if start == end:
        return (left+right)/2
    return left+(right-left)*(codepoint-start)/(end-start)
