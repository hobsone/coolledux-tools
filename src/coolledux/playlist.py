"""Turn animations into a playlist the panel accepts.

A CoolLEDUX panel holds up to MAX_SLOTS programs, plays them in order, and loops.
Each program holds at most FRAMES_PER_SLOT frames, and each frame shows for 20 to
65535 ms. plan() takes any sequence of frames, from one GIF or many, splits frames
that are too long to show in one go, and packs the result into slots. On the panel
the slots play back to back with no visible gap, so a playlist acts as one long
animation.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from PIL import Image, ImageSequence

from . import limits


@dataclass
class Frame:
    image: Image.Image          # already panel-sized (see fit_to_panel)
    ms: int                     # how long it shows


class PlaylistTooLong(ValueError):
    """The animation needs more slots than the panel has."""


# ---- Loading ----------------------------------------------------------------------------

def fit_to_panel(img: Image.Image, width: int, height: int, fit: str = "stretch") -> Image.Image:
    """Scale an image to the panel. stretch: fill exactly (nearest neighbor, best for
    pixel art drawn at panel size); contain: letterbox on black; cover: fill and crop."""
    img = img.convert("RGBA")
    if fit == "stretch" or img.size == (width, height):
        return img.resize((width, height), Image.Resampling.NEAREST)
    sx, sy = width / img.width, height / img.height
    s = min(sx, sy) if fit == "contain" else max(sx, sy)
    resized = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.Resampling.NEAREST)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    canvas.paste(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
    return canvas


def load_gif(path: str, width: int, height: int, *, rotate: int = 0, fit: str = "stretch",
             speed: float = 1.0) -> list[Frame]:
    """Read every frame of a GIF (or any animated image Pillow opens) with its duration.
    rotate (0/90/180/270, clockwise) is applied before fitting; use 180 for a panel
    mounted upside down. speed > 1 plays faster."""
    if rotate not in (0, 90, 180, 270):
        raise ValueError("rotate must be 0, 90, 180 or 270")
    if speed <= 0:
        raise ValueError("speed must be greater than 0")
    turn = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}
    frames = []
    with Image.open(path) as src:
        for f in ImageSequence.Iterator(src):
            ms = f.info.get("duration", 100) or 100
            img = f.convert("RGBA")
            if rotate:
                img = img.transpose(turn[rotate])
            frames.append(Frame(fit_to_panel(img, width, height, fit), max(1, round(ms / speed))))
    if not frames:
        raise ValueError(f"no frames in {path}")
    return frames


def load_many(paths: Iterable[str], width: int, height: int, **kw) -> list[Frame]:
    """Load several files as one continuous sequence, in order."""
    out: list[Frame] = []
    for p in paths:
        out += load_gif(p, width, height, **kw)
    return out


# ---- Planning ---------------------------------------------------------------------------

def expand_long_frames(frames: Sequence[Frame]) -> list[Frame]:
    """Clamp short frames up to the minimum and split frames longer than the 16-bit
    delay limit into equal identical frames, keeping the total time."""
    out = []
    for f in frames:
        ms = max(limits.MIN_DELAY_MS, f.ms)
        if ms <= limits.MAX_DELAY_MS:
            out.append(Frame(f.image, ms))
            continue
        n = math.ceil(ms / limits.LONG_FRAME_SPLIT_MS)
        base, extra = divmod(ms, n)
        out += [Frame(f.image, base + (1 if i < extra else 0)) for i in range(n)]
    return out


@dataclass
class Playlist:
    slots: list[list[Frame]] = field(default_factory=list)
    width: int = 0
    height: int = 0

    @property
    def frames(self) -> int:
        return sum(len(s) for s in self.slots)

    @property
    def total_ms(self) -> int:
        return sum(f.ms for s in self.slots for f in s)

    def summary(self) -> str:
        lines = [f"{self.frames} frames in {len(self.slots)} slot(s), {self.width}x{self.height}, "
                 f"loop {format_ms(self.total_ms)}"]
        t = 0
        for i, s in enumerate(self.slots):
            ms = sum(f.ms for f in s)
            lines.append(f"  slot {i + 1}: {len(s):3d} frames, {format_ms(t)} - {format_ms(t + ms)}")
            t += ms
        return "\n".join(lines)


def plan(frames: Sequence[Frame], width: int, height: int, *,
         frames_per_slot: int = limits.FRAMES_PER_SLOT, max_slots: int = limits.MAX_SLOTS) -> Playlist:
    """Pack frames into slots, splitting long frames first. Raises PlaylistTooLong
    with the numbers if it doesn't fit."""
    if frames_per_slot < 1 or max_slots < 1:
        raise ValueError("frames_per_slot and max_slots must be at least 1")
    expanded = expand_long_frames(frames)
    if not expanded:
        raise ValueError("no frames")
    slots = [expanded[i:i + frames_per_slot] for i in range(0, len(expanded), frames_per_slot)]
    if len(slots) > max_slots:
        raise PlaylistTooLong(
            f"{len(expanded)} frames need {len(slots)} slots of {frames_per_slot}, but the panel has "
            f"{max_slots} ({max_slots * frames_per_slot} frames). Use fewer distinct frames: frames "
            f"longer than {limits.MAX_DELAY_MS} ms each cost one extra frame per "
            f"{limits.LONG_FRAME_SPLIT_MS / 1000:.0f} s.")
    return Playlist(slots, width, height)


def programs(playlist: Playlist) -> list[bytes]:
    """The native program for each slot, ready to upload."""
    from .protocol import build_program
    return [build_program([f.image for f in s], [f.ms for f in s], playlist.width, playlist.height)
            for s in playlist.slots]


# ---- Export -----------------------------------------------------------------------------

def export_gifs(playlist: Playlist, out_dir: str, prefix: str = "slot") -> list[str]:
    """Write each slot as a GIF (for previewing, or for other upload tools).

    Pillow merges identical consecutive GIF frames and adds up their durations, which
    would push long holds past the 65535 ms limit again. To keep every frame, odd
    frames get the lowest bit of one pixel's blue value flipped. The panel keeps only
    the top 4 bits of each channel, so the change never shows."""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for n, slot in enumerate(playlist.slots, 1):
        imgs = []
        for i, f in enumerate(slot):
            img = f.image.convert("RGB")
            if i % 2:
                img = img.copy()
                r, g, b = img.getpixel((0, 0))
                img.putpixel((0, 0), (r, g, b ^ 1))
            imgs.append(img)
        path = os.path.join(out_dir, f"{prefix}{n}.gif")
        imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=[f.ms for f in slot],
                     loop=0, optimize=False)
        paths.append(path)
    return paths


def format_ms(ms: int) -> str:
    s = ms / 1000
    if s < 60:
        return f"{s:.1f}s"
    m, s = divmod(round(s), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
