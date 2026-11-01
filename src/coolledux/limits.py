"""Known limits of CoolLEDUX panels, measured on real hardware.

See the README's "Known limits" section for how each was found. The defaults are the
safe values; override them per call if your panel turns out to allow more.
"""

FRAMES_PER_SLOT = 40
"""Safe frames per slot. The official app uses 40. A 64x16 panel has accepted about
55; a 48x24 panel rejected a 55-frame program (it asked for a retransmit from chunk 0,
then stopped responding). The real limit is probably the program's size in bytes, so
bigger panels allow fewer frames."""

MAX_SLOTS = 9
"""Program slots on the panel. A playlist of 9 slots x 40 frames uploads and plays."""

MIN_DELAY_MS = 20
MAX_DELAY_MS = 65535
"""Per-frame display time. The delay is a 16-bit millisecond field."""

LONG_FRAME_SPLIT_MS = 65000
"""Frames longer than MAX_DELAY_MS are split into equal identical frames of at most
this long, so a 15-minute hold becomes 14 frames of about 64.3 s."""

BRIGHTNESS_RANGE = (5, 255)
