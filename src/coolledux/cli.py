"""coolledux: push GIFs to CoolLEDUX LED panels (the ones the CoolLED1248 app drives).

  coolledux scan
  coolledux push intro.gif loop.gif --rotate 180 --brightness 120
  coolledux push long_show.gif --dry-run --export out/
  coolledux brightness 80
  coolledux limits
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from . import __version__, limits
from .ble import Panel, PanelError, find_panel, scan
from .playlist import PlaylistTooLong, export_gifs, load_many, plan, programs


def _bar(slot, slots, chunk, chunks):
    done = int(30 * chunk / chunks)
    sys.stdout.write(f"\r  slot {slot + 1}/{slots} [{'#' * done}{'.' * (30 - done)}] {chunk}/{chunks}")
    sys.stdout.flush()
    if chunk == chunks:
        sys.stdout.write("\n")


async def _resolve(args):
    """Address and size: from the flags, else from a scan."""
    if args.address and args.width and args.height:
        return args.address, args.width, args.height
    print("scanning for the panel...")
    info = await find_panel(args.address, timeout=args.scan_timeout)
    w, h = args.width or info.width, args.height or info.height
    if not (w and h):
        raise PanelError("couldn't read the panel's size from its advertisement; pass --width and --height")
    print(f"found {info.name} at {info.address}, {w}x{h}")
    return info.address, w, h


async def cmd_scan(args):
    panels = await scan(args.timeout, all_devices=args.all)
    if not panels:
        print("nothing found" + ("" if args.all else " (try --all to list every named device)"))
    for i in panels:
        size = f"{i.width}x{i.height}" if i.width else ""
        print(f"{i.address}  {i.name:<12} {size:>7}  RSSI {i.rssi}")


async def cmd_push(args):
    if args.dry_run and not (args.width and args.height):
        raise SystemExit("--dry-run needs --width and --height (no Bluetooth is used)")
    address, w, h = (None, args.width, args.height) if args.dry_run else await _resolve(args)
    frames = load_many(args.files, w, h, rotate=args.rotate, fit=args.fit, speed=args.speed)
    pl = plan(frames, w, h, frames_per_slot=args.frames_per_slot, max_slots=args.max_slots)
    print(pl.summary())
    if args.frames_per_slot > limits.FRAMES_PER_SLOT:
        print(f"warning: more than {limits.FRAMES_PER_SLOT} frames per slot may be rejected by the panel")
    if args.export:
        for path in export_gifs(pl, args.export):
            print("wrote", path)
    if args.dry_run:
        return
    progs = programs(pl)
    async with Panel(address, log=(lambda s: None) if args.quiet else print) as panel:
        if args.brightness is not None:
            await panel.set_brightness(args.brightness)
            print(f"brightness {args.brightness}")
        results = await panel.upload(progs, progress=None if args.quiet else _bar)
    sent = results.count("sent")
    print(f"done: {len(results)} slot(s), {sent} sent, {len(results) - sent} already on the panel. "
          f"It now loops this playlist, and restarts it from slot 1 after a power cycle.")


async def cmd_brightness(args):
    address, _, _ = await _resolve_address_only(args)
    async with Panel(address) as panel:
        await panel.set_brightness(args.level)
    print(f"brightness {args.level}")


async def cmd_flip(args):
    address, _, _ = await _resolve_address_only(args)
    async with Panel(address) as panel:
        await panel.set_flip(args.mode)
    print(f"orientation {args.mode} (persists on the panel)")


async def cmd_sync_time(args):
    address, _, _ = await _resolve_address_only(args)
    async with Panel(address) as panel:
        await panel.sync_time()
    print("panel clock set from this computer")


async def _resolve_address_only(args):
    if args.address:
        return args.address, None, None
    info = await find_panel(timeout=args.scan_timeout)
    print(f"found {info.name} at {info.address}")
    return info.address, None, None


def cmd_limits(_args):
    print(f"""Known CoolLEDUX limits (see README for details):
  frames per slot   {limits.FRAMES_PER_SLOT} safe (about 55 has worked on 64x16; 55 failed on 48x24)
  slots             {limits.MAX_SLOTS}  -> up to {limits.FRAMES_PER_SLOT * limits.MAX_SLOTS} frames per playlist
  frame time        {limits.MIN_DELAY_MS}-{limits.MAX_DELAY_MS} ms (longer frames are split automatically)
  brightness        {limits.BRIGHTNESS_RANGE[0]}-{limits.BRIGHTNESS_RANGE[1]}
  playback          slots play in order with no gap, then loop; a power cycle restarts at slot 1""")


def _brightness(v):
    v = int(v)
    lo, hi = limits.BRIGHTNESS_RANGE
    if not lo <= v <= hi:
        raise argparse.ArgumentTypeError(f"brightness must be {lo}-{hi}")
    return v


def build_parser():
    ap = argparse.ArgumentParser(prog="coolledux", description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", action="version", version=f"coolledux {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def conn(sp):
        sp.add_argument("-a", "--address", default=os.environ.get("COOLLEDUX_ADDRESS") or None,
                        help="panel address (a UUID on macOS); default: $COOLLEDUX_ADDRESS, else scan for one")
        sp.add_argument("--scan-timeout", type=float, default=8.0)

    s = sub.add_parser("scan", help="list CoolLEDUX panels in range, with their size")
    s.add_argument("--timeout", type=float, default=8.0)
    s.add_argument("--all", action="store_true", help="list every named BLE device")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("push", help="upload one or more GIFs as one looping playlist")
    s.add_argument("files", nargs="+", help="GIFs (or any animated image), played in order")
    conn(s)
    s.add_argument("--width", type=int, help="panel width in pixels (default: read from the panel)")
    s.add_argument("--height", type=int, help="panel height in pixels (default: read from the panel)")
    s.add_argument("--rotate", type=int, default=0, choices=(0, 90, 180, 270),
                   help="rotate clockwise first, e.g. 180 for a panel mounted upside down")
    s.add_argument("--fit", choices=("stretch", "contain", "cover"), default="stretch")
    s.add_argument("--speed", type=float, default=1.0, help="playback speed multiplier")
    s.add_argument("--brightness", type=_brightness, help=f"also set brightness "
                   f"({limits.BRIGHTNESS_RANGE[0]}-{limits.BRIGHTNESS_RANGE[1]}) in the same connection")
    s.add_argument("--frames-per-slot", type=int, default=limits.FRAMES_PER_SLOT)
    s.add_argument("--max-slots", type=int, default=limits.MAX_SLOTS)
    s.add_argument("--dry-run", action="store_true", help="plan only, no Bluetooth (needs --width/--height)")
    s.add_argument("--export", metavar="DIR", help="also write each slot as a GIF")
    s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(fn=cmd_push)

    s = sub.add_parser("brightness", help="set brightness")
    s.add_argument("level", type=_brightness)
    conn(s)
    s.set_defaults(fn=cmd_brightness)

    s = sub.add_parser("flip", help="set the panel's persistent orientation")
    s.add_argument("mode", choices=("none", "x", "y", "xy"))
    conn(s)
    s.set_defaults(fn=cmd_flip)

    s = sub.add_parser("sync-time", help="set the panel's clock from this computer")
    conn(s)
    s.set_defaults(fn=cmd_sync_time)

    s = sub.add_parser("limits", help="print the known panel limits")
    s.set_defaults(fn=cmd_limits)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.fn(args)
        if asyncio.iscoroutine(result):
            asyncio.run(result)
    except (PanelError, PlaylistTooLong, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
