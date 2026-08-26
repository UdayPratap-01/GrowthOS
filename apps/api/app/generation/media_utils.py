"""Helpers for validating media bytes and building demo PNG files."""

from __future__ import annotations

import struct
import zlib
from typing import Tuple


def detect_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return "image/webp"
    if data.startswith(b"GIF8"):
        return "image/gif"
    return None


def detect_video_mime(data: bytes) -> str | None:
    if len(data) < 12:
        return None
    # ISO BMFF (mp4/mov): ....ftyp
    if data[4:8] == b"ftyp":
        return "video/mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    return None


def is_valid_image(data: bytes) -> bool:
    return detect_image_mime(data) is not None and len(data) > 32


def is_valid_video(data: bytes) -> bool:
    return detect_video_mime(data) is not None and len(data) > 128


def parse_aspect_ratio(aspect: str, *, default: Tuple[int, int] = (1024, 1024)) -> Tuple[int, int]:
    mapping = {
        "1:1": (1024, 1024),
        "16:9": (1792, 1024),
        "9:16": (1024, 1792),
        "4:5": (1024, 1280),
        "5:4": (1280, 1024),
    }
    return mapping.get((aspect or "1:1").strip(), default)


def is_gpt_image_model(model: str) -> bool:
    """
    True for the `gpt-image-*` family (including `chatgpt-image-latest`).

    The two OpenAI image families share one endpoint but not one request shape,
    so the caller has to know which it is talking to.
    """
    return "gpt-image" in (model or "").strip().lower()


# Lightricks LTX on Replicate accepts only these two ratios (vendor schema).
# Other Replicate video models are not constrained by this allowlist.
LTX_SUPPORTED_ASPECT_RATIOS = frozenset({"16:9", "9:16"})


def is_ltx_video_model(model: str) -> bool:
    """
    True for Lightricks LTX video models addressed by owner/name (optional :version).

    Version-hash-only ids cannot be classified here; those stay unrestricted so
    we never invent a capability gate from an opaque hash.
    """
    slug = (model or "").strip().lower().split(":", 1)[0]
    return "ltx-" in slug or slug.endswith("/ltx")


def replicate_video_aspect_ratio_error(aspect: str, model: str) -> str | None:
    """
    Return a clear error string when this Replicate model rejects `aspect`, else None.

    Non-LTX models (and unclassifiable version hashes) are not restricted — 1:1
    remains valid for providers that support it.
    """
    if not is_ltx_video_model(model):
        return None
    ratio = (aspect or "").strip()
    if ratio in LTX_SUPPORTED_ASPECT_RATIOS:
        return None
    allowed = ", ".join(sorted(LTX_SUPPORTED_ASPECT_RATIOS))
    return (
        f"aspect_ratio {ratio!r} is not supported by this video model; "
        f"supported values are {allowed}"
    )


def openai_image_size(aspect: str, model: str = "") -> str:
    a = (aspect or "1:1").strip()
    if is_gpt_image_model(model):
        # This family accepts only these three; a DALL·E size is rejected.
        if a == "16:9":
            return "1536x1024"
        if a == "9:16":
            return "1024x1536"
        return "1024x1024"
    if a == "16:9":
        return "1792x1024"
    if a == "9:16":
        return "1024x1792"
    return "1024x1024"


def make_demo_png(width: int = 512, height: int = 512, label: str = "DEMO") -> bytes:
    """Create a real PNG file (not a placeholder URL). Visibly labeled DEMO via solid bands."""
    width = max(64, min(width, 1024))
    height = max(64, min(height, 1024))
    rows = []
    for y in range(height):
        row = bytearray([0])  # filter none
        for x in range(width):
            # navy background with a horizontal DEMO band
            in_band = height // 3 <= y <= (2 * height) // 3
            if in_band:
                # amber band
                row.extend((230, 160, 40))
            else:
                row.extend((18, 28, 48))
            # vertical ticks to make it obviously generated
            if x % 64 == 0:
                row[-3:] = bytes((255, 255, 255))
        rows.append(bytes(row))
    raw = b"".join(rows)
    compressed = zlib.compress(raw, 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    # label unused in pixels but kept for callers; band encodes DEMO visually
    _ = label
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")
