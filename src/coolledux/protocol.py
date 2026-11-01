"""CoolLEDUX BLE wire protocol: framing, checksums, compression and packet builders.

Ported from coolledux-linux-uploader by Zachary McMurrin
(https://github.com/zackmcmurrin-dev/coolledux-linux-uploader), MIT License; see
THIRD_PARTY_LICENSES.md. The byte layouts are kept identical to that hardware-tested
implementation, and tests/test_protocol.py checks this port against outputs captured
from it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterator, Sequence

from PIL import Image

CHAR_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"   # write-without-response + notify
LOGIN_PACKET_PAYLOAD = bytes.fromhex("0d 55 55 55 55 55 55 55 00")
CHUNK_SIZE = 240                                      # bytes of compressed data per packet


# ---- Framing ----------------------------------------------------------------------

def u16(n: int) -> bytes:
    return int(n).to_bytes(2, "big")


def u32(n: int) -> bytes:
    return int(n).to_bytes(4, "big", signed=False)


def wrap(payload: bytes) -> bytes:
    """Frame a payload: 0x01, escaped (length + payload), 0x03. Bytes 0x01-0x03 are
    escaped as 0x02, byte ^ 0x04."""
    data = u16(len(payload)) + payload
    out = bytearray([0x01])
    for b in data:
        if 0x00 < b < 0x04:
            out += bytes([0x02, b ^ 0x04])
        else:
            out.append(b)
    out.append(0x03)
    return bytes(out)


def unwrap(packet: bytes) -> bytes:
    """Undo wrap(): strip the frame, unescape, drop the length prefix."""
    if packet and packet[0] == 0x01 and packet[-1] == 0x03:
        packet = packet[1:-1]
    out = bytearray()
    i = 0
    while i < len(packet):
        if packet[i] == 0x02 and i + 1 < len(packet):
            out.append(packet[i + 1] ^ 0x04)
            i += 2
        else:
            out.append(packet[i])
            i += 1
    return bytes(out[2:]) if len(out) >= 2 else bytes(out)


def crc32_coolledux(data: bytes) -> int:
    """The panel's CRC-32 variant (poly 0x04C11DB7, MSB-first, no reflection)."""
    poly = 0x04C11DB7
    crc = 0xFFFFFFFF
    for b in data:
        for _ in range(8):
            crc_high = crc & 0x80000000
            data_high = b & 0x80
            crc = (crc << 1) & 0xFFFFFFFF
            if crc_high:
                crc ^= poly
            if data_high:
                crc ^= poly
            b = (b << 1) & 0xFF
    return crc & 0xFFFFFFFF


def xor_checksum(data: bytes) -> int:
    x = 0
    for b in data:
        x ^= b
    return x & 0xFF


# ---- LZSS compression (N=512, F=18, threshold 2) -------------------------------------

_N, _F, _THRESHOLD = 512, 18, 2
_NIL = _N


def lzss_compress(src: bytes) -> bytes:
    """LZSS as the panel expects it (binary-tree matcher, 0x20-filled window)."""
    N, F, THRESHOLD, NIL = _N, _F, _THRESHOLD, _NIL
    text_buf = bytearray(N + F - 1)
    lson = [NIL] * (N + 1)
    rson = [NIL] * (N + 257)
    dad = [NIL] * (N + 1)
    match_position = 0
    match_length = 0

    def insert_node(r):
        nonlocal match_position, match_length
        p = text_buf[r] + N + 1
        lson[r] = NIL
        rson[r] = NIL
        match_length = 0
        cmp_val = 1
        while True:
            if cmp_val >= 0:
                if rson[p] != NIL:
                    p = rson[p]
                else:
                    rson[p] = r
                    dad[r] = p
                    return
            else:
                if lson[p] != NIL:
                    p = lson[p]
                else:
                    lson[p] = r
                    dad[r] = p
                    return
            i = 1
            while i < F:
                cmp_val = text_buf[r + i] - text_buf[p + i]
                if cmp_val != 0:
                    break
                i += 1
            if i > match_length:
                match_position = p
                match_length = i
                if i >= F:
                    dad[r] = dad[p]
                    lson[r] = lson[p]
                    rson[r] = rson[p]
                    dad[lson[p]] = r
                    dad[rson[p]] = r
                    if rson[dad[p]] == p:
                        rson[dad[p]] = r
                    else:
                        lson[dad[p]] = r
                    dad[p] = NIL
                    return

    def delete_node(p):
        if dad[p] == NIL:
            return
        if rson[p] == NIL:
            q = lson[p]
        elif lson[p] == NIL:
            q = rson[p]
        else:
            q = lson[p]
            if rson[q] != NIL:
                while rson[q] != NIL:
                    q = rson[q]
                rson[dad[q]] = lson[q]
                dad[lson[q]] = dad[q]
                lson[q] = lson[p]
                dad[lson[p]] = q
            rson[q] = rson[p]
            dad[rson[p]] = q
        dad[q] = dad[p]
        if rson[dad[p]] == p:
            rson[dad[p]] = q
        else:
            lson[dad[p]] = q
        dad[p] = NIL

    out = bytearray()
    code_buf = bytearray(17)
    code_buf[0] = 0
    code_buf_ptr = 1
    mask = 1
    s = 0
    r = N - F
    for i in range(s, r):
        text_buf[i] = 0x20
    src_i = 0
    length = 0
    while length < F and src_i < len(src):
        text_buf[r + length] = src[src_i]
        src_i += 1
        length += 1
    if length == 0:
        return bytes(out)
    for i in range(1, F + 1):
        insert_node(r - i)
    insert_node(r)
    while True:
        if match_length > length:
            match_length = length
        if match_length <= THRESHOLD:
            match_length = 1
            code_buf[0] |= mask
            code_buf[code_buf_ptr] = text_buf[r]
            code_buf_ptr += 1
        else:
            code_buf[code_buf_ptr] = match_position & 0xFF
            code_buf[code_buf_ptr + 1] = ((match_position >> 4) & 0xF0) | (match_length - (THRESHOLD + 1))
            code_buf_ptr += 2
        mask = (mask << 1) & 0xFF
        if mask == 0:
            out += code_buf[:code_buf_ptr]
            code_buf[0] = 0
            code_buf_ptr = 1
            mask = 1
        last_match_length = match_length
        i = 0
        while i < last_match_length and src_i < len(src):
            delete_node(s)
            text_buf[s] = src[src_i]
            if s < F - 1:
                text_buf[s + N] = src[src_i]
            s = (s + 1) & (N - 1)
            r = (r + 1) & (N - 1)
            src_i += 1
            insert_node(r)
            i += 1
        while i < last_match_length:
            delete_node(s)
            s = (s + 1) & (N - 1)
            r = (r + 1) & (N - 1)
            length -= 1
            if length:
                insert_node(r)
            i += 1
        if length <= 0:
            break
    if code_buf_ptr > 1:
        out += code_buf[:code_buf_ptr]
    return bytes(out)


# ---- Native animation program ---------------------------------------------------------

def rgb444(r: int, g: int, b: int) -> bytes:
    """The panel stores 4 bits per channel: two bytes, 0R and GB."""
    return bytes([r // 16, ((g // 16) << 4) | (b // 16)])


def frame_to_native_bytes(img: Image.Image, width: int, height: int) -> bytes:
    """One frame in the panel's pixel order (column by column), RGB444."""
    img = img.convert("RGBA").resize((width, height), Image.Resampling.NEAREST)
    data = bytearray()
    for x in range(width):
        for y in range(height):
            r, g, b, a = img.getpixel((x, y))
            if a < 128:
                r = g = b = 0
            data += rgb444(r, g, b)
    return bytes(data)


def build_program(frames: Sequence[Image.Image], delays_ms: Sequence[int], width: int, height: int) -> bytes:
    """Build one slot's native animation program. Delays must already be in
    20..65535 ms (see coolledux.limits); one frame per delay."""
    if not frames or len(frames) != len(delays_ms):
        raise ValueError("need one delay per frame, and at least one frame")
    if any(not 0 <= d <= 0xFFFF for d in delays_ms):
        raise ValueError("delays must fit in 16 bits (0..65535 ms)")
    inner = bytearray()
    inner += b"\x03\x01"
    inner += b"\x00" * 6
    inner += b"\x00"
    inner += u16(0)
    inner += u16(0)
    inner += u16(width)
    inner += u16(height)
    inner += b"\x00"
    inner += u16(len(frames))
    for d in delays_ms:
        inner += u16(d)
    for f in frames:
        inner += frame_to_native_bytes(f, width, height)
    content_block = u32(len(inner) + 4) + inner
    program = bytearray()
    program += b"\x00" * 8
    program += b"\x01"
    program += b"\x00"
    program += content_block
    return bytes(program)


# ---- Packets ---------------------------------------------------------------------------

def login_packet() -> bytes:
    return wrap(LOGIN_PACKET_PAYLOAD)


def start_packet(program: bytes, index: int = 0, count: int = 1, show_count: int = 1) -> bytes:
    """Announce program `index` of a `count`-slot playlist (CRC and size)."""
    payload = b"\x02" + u32(crc32_coolledux(program)) + u32(len(program)) + bytes([index, count, show_count])
    return wrap(payload)


def chunk_packet(compressed: bytes, chunk_index: int, chunk: bytes) -> bytes:
    body = b"\x00" + u32(len(compressed)) + u16(chunk_index) + u16(len(chunk)) + chunk
    body += bytes([xor_checksum(body)])
    return wrap(b"\x03" + body)


def brightness_packet(level: int) -> bytes:
    return wrap(bytes([0x04, level & 0xFF]))


def flip_packet(mode: str) -> bytes:
    """Persistent orientation, as captured from the official app: none, x, y or xy."""
    packets = {
        "none": bytes.fromhex("01 00 02 06 0c 00 03"),
        "x": bytes.fromhex("01 00 02 06 0c 02 06 03"),
        "y": bytes.fromhex("01 00 02 06 0c 02 07 03"),
        "xy": bytes.fromhex("01 00 02 06 0c 02 05 03"),
    }
    return packets[mode]


def sync_time_packet(now: datetime | None = None) -> bytes:
    now = now or datetime.now()
    return wrap(bytes([0x09, now.year - 2000, now.month, now.day, now.weekday() + 1,
                       now.hour, now.minute, now.second]))


def chunks(data: bytes, size: int = CHUNK_SIZE) -> Iterator[bytes]:
    for i in range(0, len(data), size):
        yield data[i:i + size]
