"""Unicode 17 algorithmic names omitted by some unicodedata implementations.

Ranges transcribed from the Unicode 17.0 DerivedName data (2025-07-30):
https://www.unicode.org/Public/17.0.0/ucd/extracted/DerivedName.txt
"""
import unicodedata2 as ucd

PATTERNS = [
    (0x3400, 0x4DBF, "CJK UNIFIED IDEOGRAPH"),
    (0x4E00, 0x9FFF, "CJK UNIFIED IDEOGRAPH"),
    (0xF900, 0xFA6D, "CJK COMPATIBILITY IDEOGRAPH"),
    (0xFA70, 0xFAD9, "CJK COMPATIBILITY IDEOGRAPH"),
    (0x13460, 0x143FA, "EGYPTIAN HIEROGLYPH"),
    (0x17000, 0x187FF, "TANGUT IDEOGRAPH"),
    (0x18B00, 0x18CD5, "KHITAN SMALL SCRIPT CHARACTER"),
    (0x18CFF, 0x18CFF, "KHITAN SMALL SCRIPT CHARACTER"),
    (0x18D00, 0x18D1E, "TANGUT IDEOGRAPH"),
    (0x1B170, 0x1B2FB, "NUSHU CHARACTER"),
    (0x20000, 0x2A6DF, "CJK UNIFIED IDEOGRAPH"),
    (0x2A700, 0x2B81D, "CJK UNIFIED IDEOGRAPH"),
    (0x2B820, 0x2CEAD, "CJK UNIFIED IDEOGRAPH"),
    (0x2CEB0, 0x2EBE0, "CJK UNIFIED IDEOGRAPH"),
    (0x2EBF0, 0x2EE5D, "CJK UNIFIED IDEOGRAPH"),
    (0x2F800, 0x2FA1D, "CJK COMPATIBILITY IDEOGRAPH"),
    (0x30000, 0x3134A, "CJK UNIFIED IDEOGRAPH"),
    (0x31350, 0x33479, "CJK UNIFIED IDEOGRAPH"),
]


def unicode_name(cp: int) -> str:
    name = ucd.name(chr(cp), None)
    if name is not None:
        return name
    for low, high, prefix in PATTERNS:
        if low <= cp <= high:
            return f"{prefix}-{cp:04X}"
    return "UNNAMED"
