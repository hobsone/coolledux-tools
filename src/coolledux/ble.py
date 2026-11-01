"""Talking to a CoolLEDUX panel over Bluetooth LE (via Bleak).

    async with Panel("AA:BB:...") as panel:      # macOS: a UUID instead of a MAC address
        await panel.set_brightness(120)
        await panel.upload(programs)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from bleak import BleakClient, BleakScanner

from . import protocol as p

NAME_TAG = "CoolLEDUX"


class PanelError(RuntimeError):
    pass


@dataclass
class PanelInfo:
    address: str
    name: str
    rssi: int
    width: Optional[int] = None
    height: Optional[int] = None


def parse_size(manufacturer_value: bytes) -> tuple[Optional[int], Optional[int]]:
    """The advertisement carries the matrix size: byte 6 is the height and bytes 7-8 the
    width (big-endian), after a 6-byte identifier. A 48x24 panel sends ... 18 00 30 ..."""
    if len(manufacturer_value) >= 9:
        h = manufacturer_value[6]
        w = int.from_bytes(manufacturer_value[7:9], "big")
        if 0 < h <= 128 and 0 < w <= 1024:
            return w, h
    return None, None


async def scan(timeout: float = 8.0, all_devices: bool = False) -> list[PanelInfo]:
    """CoolLEDUX panels in range (everything named, with all_devices), strongest first."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    out = []
    for dev, adv in found.values():
        name = adv.local_name or dev.name or ""
        if not name or (not all_devices and NAME_TAG not in name):
            continue
        w = h = None
        if NAME_TAG in name and adv.manufacturer_data:
            w, h = parse_size(next(iter(adv.manufacturer_data.values())))
        out.append(PanelInfo(dev.address, name, adv.rssi, w, h))
    return sorted(out, key=lambda i: -i.rssi)


async def find_panel(address: Optional[str] = None, timeout: float = 8.0) -> PanelInfo:
    """The panel with this address, or the strongest CoolLEDUX in range."""
    panels = await scan(timeout)
    if address:
        for info in panels:
            if info.address.lower() == address.lower():
                return info
        raise PanelError(f"no CoolLEDUX panel with address {address} found")
    if not panels:
        raise PanelError("no CoolLEDUX panel found. Is it on, and is its phone app closed? "
                         "The panel accepts only one Bluetooth connection.")
    return panels[0]


Progress = Callable[[int, int, int, int], None]   # slot, slots, chunk, chunks


class Panel:
    """A logged-in connection to one panel."""

    def __init__(self, address: str, *, timeout: float = 20.0, ack_timeout: float = 8.0,
                 log: Callable[[str], None] = lambda s: None):
        self.address = address
        self.timeout = timeout
        self.ack_timeout = ack_timeout
        self.log = log
        self._client: Optional[BleakClient] = None
        self._acks: Optional[asyncio.Queue] = None

    async def __aenter__(self) -> "Panel":
        self._acks = asyncio.Queue()
        self._client = BleakClient(self.address, timeout=self.timeout)
        await self._client.connect()
        await self._client.start_notify(p.CHAR_UUID, self._on_notify)
        await asyncio.sleep(0.2)
        await self._send(p.login_packet())
        await self._ack(0x0D, "login")
        self.log("connected and logged in")
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None

    def _on_notify(self, _sender, data: bytearray) -> None:
        self._acks.put_nowait(p.unwrap(bytes(data)))

    async def _send(self, packet: bytes) -> None:
        # The panel's characteristic only allows write-without-response.
        await self._client.write_gatt_char(p.CHAR_UUID, packet, response=False)

    async def _ack(self, kind: int, what: str) -> bytes:
        while True:
            try:
                payload = await asyncio.wait_for(self._acks.get(), timeout=self.ack_timeout)
            except asyncio.TimeoutError:
                raise PanelError(f"no {what} acknowledgement from the panel") from None
            if payload and payload[0] == kind:
                return payload

    async def set_brightness(self, level: int) -> None:
        await self._send(p.brightness_packet(level))
        await self._ack(0x04, "brightness")

    async def set_flip(self, mode: str) -> None:
        """Persistent orientation: none, x, y or xy (xy = rotated 180 degrees)."""
        await self._send(p.flip_packet(mode))
        await self._ack(0x0C, "orientation")

    async def sync_time(self) -> None:
        await self._send(p.sync_time_packet())
        await self._ack(0x09, "time sync")

    async def upload(self, programs: Sequence[bytes], progress: Optional[Progress] = None) -> list[str]:
        """Upload a playlist: program i goes to slot i, and the panel is told to cycle
        through all of them. Returns per-slot results: "sent" or "cached" (the panel
        already held an identical program and skipped the transfer)."""
        count = len(programs)
        if not 1 <= count <= 255:
            raise ValueError("need 1 to 255 programs")
        results = []
        for index, program in enumerate(programs):
            compressed = p.lzss_compress(program)
            parts = list(p.chunks(compressed))
            await self._send(p.start_packet(program, index=index, count=count, show_count=1))
            ack = await self._ack(0x02, f"slot {index + 1} start")
            status = ack[1] if len(ack) >= 2 else 0
            if status == 1:
                results.append("cached")
                self.log(f"slot {index + 1}/{count}: already on the panel")
                await asyncio.sleep(0.2)
                continue
            if status == 3:
                # Seen when a program is too big for the panel; the transfer that
                # follows usually stalls. Carry on, but say so.
                self.log(f"slot {index + 1}/{count}: panel asked to restart from chunk 0 "
                         f"(often means the slot has too many frames)")
            elif status != 0:
                raise PanelError(f"panel rejected slot {index + 1} (status {status:02x})")
            for ci, chunk in enumerate(parts):
                await self._send(p.chunk_packet(compressed, ci, chunk))
                ack = await self._ack(0x03, f"slot {index + 1} chunk {ci + 1}")
                if len(ack) >= 2 and ack[1] != 0:
                    raise PanelError(f"slot {index + 1} chunk {ci + 1} rejected: {ack.hex(' ')}")
                if progress:
                    progress(index, count, ci + 1, len(parts))
                await asyncio.sleep(0.03)
            results.append("sent")
            self.log(f"slot {index + 1}/{count}: sent ({len(parts)} chunks)")
            await asyncio.sleep(0.2)
        await asyncio.sleep(3.0)   # let the panel switch over before disconnecting (as upstream does)
        return results
