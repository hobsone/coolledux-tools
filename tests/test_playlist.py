import pytest
from PIL import Image, ImageSequence

from coolledux import limits
from coolledux.ble import parse_size
from coolledux.playlist import (Frame, PlaylistTooLong, expand_long_frames, export_gifs, load_gif, plan,
                                programs)

W, H = 48, 24


def img(color=(255, 0, 0)):
    return Image.new("RGBA", (W, H), color + (255,))


def test_long_frames_split_and_keep_total():
    out = expand_long_frames([Frame(img(), 15 * 60_000)])
    assert len(out) == 14
    assert sum(f.ms for f in out) == 900_000
    assert all(f.ms <= limits.MAX_DELAY_MS for f in out)


def test_short_frames_clamped():
    assert expand_long_frames([Frame(img(), 5)])[0].ms == limits.MIN_DELAY_MS


def test_packs_into_slots():
    pl = plan([Frame(img(), 100)] * 85, W, H)
    assert [len(s) for s in pl.slots] == [40, 40, 5]
    assert pl.total_ms == 8500


def test_too_long_raises_with_numbers():
    with pytest.raises(PlaylistTooLong, match="361 frames need 10 slots"):
        plan([Frame(img(), 100)] * 361, W, H)


def test_hours_long_hold_fits():
    # A 4-hour animation of 16 holds: 16 x 15 min -> 16 x 14 frames = 224 frames, 6 slots.
    pl = plan([Frame(img(), 15 * 60_000)] * 16, W, H)
    assert len(pl.slots) == 6 and pl.total_ms == 4 * 3600_000


def test_programs_built_per_slot():
    pl = plan([Frame(img(), 100)] * 41, W, H)
    progs = programs(pl)
    assert len(progs) == 2
    assert len(progs[0]) > len(progs[1])


def test_export_keeps_identical_frames(tmp_path):
    pl = plan([Frame(img(), 60_000)] * 5, W, H)
    (path,) = export_gifs(pl, str(tmp_path))
    with Image.open(path) as g:
        durs = [f.info["duration"] for f in ImageSequence.Iterator(g)]
    assert durs == [60_000] * 5          # Pillow would otherwise merge them into one 300 s frame


def test_load_gif_rotate_and_fit(tmp_path):
    src = Image.new("RGB", (W, H))
    src.putpixel((0, 0), (255, 255, 255))
    path = tmp_path / "a.gif"
    second = src.copy()
    second.putpixel((1, 0), (255, 0, 0))     # must differ, or Pillow merges the two frames
    src.save(path, save_all=True, append_images=[second], duration=[100, 250], loop=0)
    frames = load_gif(str(path), W, H, rotate=180)
    assert [f.ms for f in frames] == [100, 250]
    assert frames[0].image.getpixel((W - 1, H - 1))[:3] == (255, 255, 255)


def test_parse_size_from_advertisement():
    assert parse_size(bytes.fromhex("aabbccddeeff180030031e")) == (48, 24)
    assert parse_size(b"\x00\x01") == (None, None)
