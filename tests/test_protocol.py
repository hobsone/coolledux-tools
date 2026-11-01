"""The port must produce exactly what the hardware-tested upstream uploader produced.
These expected values were captured from coolledux-linux-uploader 0.3.0."""
import hashlib

from PIL import Image

from coolledux import protocol as p


def test_crc():
    assert p.crc32_coolledux(b"123456789") == 0x0376E6E7


def test_wrap_escapes_control_bytes():
    assert p.wrap(bytes([0x02, 0x01, 0x03, 0x00, 0x05])).hex() == "010005020602050207000503"


def test_unwrap_roundtrip():
    payload = bytes(range(256))
    assert p.unwrap(p.wrap(payload)) == payload


def test_lzss_matches_upstream():
    data = bytes((i * 7 + i // 13) % 256 for i in range(3000)) + b"\x00" * 2000
    c = p.lzss_compress(data)
    assert len(c) == 1427
    assert hashlib.sha256(c).hexdigest() == "ffa7a234ec75b6d84ba5cd0e9ecf1a0fe1bbc2df2f07f8c1c8640f04db50901e"


def _tiny_image():
    im = Image.new("RGB", (4, 2), (0, 0, 0))
    im.putpixel((1, 0), (255, 0, 0))
    im.putpixel((3, 1), (17, 34, 51))
    return im


def test_program_and_start_packet_match_upstream():
    prog = p.build_program([_tiny_image()], [65535], 4, 2)
    assert prog.hex() == ("000000000000000001000000002a0301000000000000000000000000040002000001ffff"
                          "000000000f0000000000000000000123")
    assert p.start_packet(prog, 1, 3, 1).hex() == "01000c0206ff020530290000003402050207020503"
