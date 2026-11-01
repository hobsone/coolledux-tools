# coolledux

Push GIFs to **CoolLEDUX** Bluetooth LED panels (the full-color matrix panels driven by the **CoolLED1248** phone app) from a computer, without the app.

- One or many GIFs are uploaded as a single **playlist**. It is split across the panel's program slots automatically, plays back to back with no visible gap, and loops.
- The panel's **known limits** are built in: frames per slot, number of slots, and frame duration. Frames longer than a frame can last are split for you, so animations with minutes-long holds work. A plan that won't fit is refused before anything is sent.
- The panel's pixel size is read from its Bluetooth advertisement, so you don't need to look it up.
- Brightness, orientation, and clock sync are also supported, as a command-line tool (`coolledux`) and a small Python API.

Works on macOS and Linux (and anywhere [Bleak](https://github.com/hbldh/bleak) runs). The protocol code is ported from the hardware-tested [coolledux-linux-uploader](https://github.com/zackmcmurrin-dev/coolledux-linux-uploader); see [Credits](#credits).

## Is my panel a CoolLEDUX?

Run `coolledux scan`. Panels show up named `CoolLEDUX`. Older CoolLED1248 panels advertise as `CoolLEDX` or `CoolLEDM` and speak a different protocol; for those, use [coolledx-driver](https://github.com/UpDryTwist/coolledx-driver). A CoolLEDUX panel accepts a connection from coolledx-driver but ignores its commands.

## Install

```bash
git clone https://github.com/hobsone/coolledux-tools.git
cd coolledux-tools
python3 -m venv .venv
.venv/bin/pip install -e .
```

That puts the `coolledux` command in `.venv/bin/`. You can also run it as `python -m coolledux`. Python 3.10 or newer is required.

## Quick start

Close the CoolLED1248 app on your phone first, because the panel accepts only one Bluetooth connection.

```bash
coolledux scan                                  # find panels: address, name, size
coolledux push animation.gif                    # upload and play (scans for the panel)
coolledux push intro.gif loop.gif outro.gif     # several GIFs, one looping playlist
coolledux push show.gif --rotate 180 --brightness 120
coolledux push show.gif --width 48 --height 24 --dry-run --export out/   # plan only, no Bluetooth
coolledux brightness 80
coolledux flip xy                               # persistent 180-degree orientation
coolledux limits                                # print the known limits
```

To skip scanning, set your panel's address once in your shell profile, not in scripts you share:

```bash
export COOLLEDUX_ADDRESS=<address from coolledux scan>
```

Useful `push` options:

| Option | Effect |
|---|---|
| `-a, --address` | Panel address. It's a UUID on macOS, a MAC address elsewhere. The default is `$COOLLEDUX_ADDRESS`, else the strongest CoolLEDUX in range. |
| `--width`, `--height` | Panel size. The default reads it from the panel. |
| `--rotate 0/90/180/270` | Rotates frames clockwise before upload, for example 180 for a panel mounted upside down. |
| `--fit stretch/contain/cover` | How to scale images that aren't panel-sized. The default, stretch, uses nearest neighbor. |
| `--speed` | Playback speed multiplier. |
| `--brightness 5-255` | Sets brightness in the same connection as the upload. |
| `--dry-run` | Prints the slot plan without connecting. |
| `--export DIR` | Also writes each slot as a GIF. |
| `--frames-per-slot`, `--max-slots` | Overrides the safe defaults if your panel allows more. |

## Python API

```python
import asyncio
from coolledux import Frame, Panel, find_panel, load_gif, plan, programs

async def main():
    info = await find_panel()                         # address + size from the advertisement
    frames = load_gif("show.gif", info.width, info.height, rotate=180)
    frames.append(Frame(frames[-1].image, 10 * 60_000))   # hold the last frame for 10 minutes
    playlist = plan(frames, info.width, info.height)  # raises PlaylistTooLong if it can't fit
    print(playlist.summary())
    async with Panel(info.address, log=print) as panel:
        await panel.set_brightness(120)
        await panel.upload(programs(playlist))

asyncio.run(main())
```

Frames are plain Pillow images, so you can generate animations in code (counters, clocks, schedules) and hand them to `plan()` with any durations.

## How playlists work

The panel stores up to 9 programs (slots) and is told how many to play. It plays slot 1, then slot 2, and so on, then loops back to slot 1. The handoff between slots is seamless, so `plan()` treats your whole animation as one sequence and cuts it into slots of up to 40 frames. Each upload replaces the playlist: a single-slot upload plays only that slot, so leftover programs from earlier uploads or the factory "CoolLED" demos don't play.

If the panel already holds an identical program, it says so and the transfer for that slot is skipped (reported as "cached").

## Known limits

Measured on real hardware. The defaults in `coolledux/limits.py` are the safe values.

| Limit | Value | How it was found |
|---|---|---|
| Frames per slot | **40** (safe) | The official app uses 40. About 55 has been reported to work on a 64x16 panel. On a 48x24 panel, a 55-frame program was rejected: the panel asked for a retransmit from chunk 0, then stopped responding. The real cap is probably bytes per program, so bigger panels allow fewer frames. |
| Slots | **9** | A 9 x 40 = 360-frame playlist uploaded and played on a 48x24 panel. |
| Frame duration | **20-65535 ms** | It's a 16-bit field (the upstream tool clamps to this range). Longer frames are split into repeated frames of at most 65 s, so a 15-minute hold costs 14 frames. |
| Longest playlist | About 6.5 hours of pure holds | 360 frames x 65 s. A 4-hour, 16-step schedule needs 224 frames (6 slots). |
| Colors | 4 bits per channel | The panel stores RGB444. |
| Brightness | 5-255 | |
| Power cycle | Restarts the playlist at slot 1, frame 1 | You can start a timed sequence just by switching the panel on. |

## Gotchas

- **Close the phone app.** While CoolLED1248 is connected, the panel won't accept another connection.
- **macOS Bluetooth permission.** The app you run `coolledux` from, such as Terminal or iTerm, needs Bluetooth access (System Settings > Privacy & Security > Bluetooth). Without it, macOS kills the process with no error message (exit code 134). Some sandboxed or embedded terminals never get access, so use a regular terminal app.
- **Addresses on macOS** are per-computer UUIDs, not MAC addresses. Use what `coolledux scan` prints.
- **Write without response.** The panel's characteristic (`fff1`) rejects acknowledged writes with "Write Not Permitted". The library always writes without response.
- **Brightness in the same connection.** `push --brightness` sets brightness in the same connection as the upload. Some tools treat a brightness flag as a separate command and skip the upload.
- **Exporting GIFs.** Pillow merges identical consecutive GIF frames and adds up their durations, which breaks long holds and can push a frame past 65 s. `export_gifs()` flips the lowest blue bit of one pixel on alternate frames to prevent the merge. The panel's 4-bit color can't show the difference.
- **Upside-down mounting.** You can either rotate the content (`--rotate 180`, which affects only this upload) or set the panel's persistent orientation (`coolledux flip xy`, which affects everything until you change it back).

## Development

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

The protocol tests compare this port byte for byte against output captured from the upstream uploader.

## Credits

The wire protocol (framing, CRC, LZSS compression, program layout, packet formats) and the upload sequence are ported from [coolledux-linux-uploader](https://github.com/zackmcmurrin-dev/coolledux-linux-uploader), MIT licensed. See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md). This project adds the playlist planner, limit handling, size detection, the CLI, and the Python API.

## License

MIT. See [LICENSE](LICENSE).
