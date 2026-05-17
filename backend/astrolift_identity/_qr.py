"""Pure-Python QR-code renderer for install enrollment (#494).

This is a compact, dependency-free QR Code (ISO/IEC 18004) encoder. We
ship it vendored so the install-enrollment mutation can render the QR
SVG without dragging ``Pillow`` / ``qrcode`` / ``segno`` into the
backend's Pipfile (lockfile churn on a parallel-agent build farm is
expensive, and the spec only needs single-version byte-mode encoding).

Scope:

* Byte mode (encodes the URL string as UTF-8 octets).
* Error-correction levels L/M/Q/H — the caller picks the level.
* Versions 1-40 (auto-select smallest that fits the payload at the
  chosen ECC level).
* Mask selection per Annex E (try every mask, pick the lowest penalty).
* SVG output (no PNG dependency).

Algorithm references:

* Reed-Solomon encoding follows ISO/IEC 18004 §6.7.
* Mask penalty follows §6.8.3.
* Format-info BCH(15,5) and version-info BCH(18,6) bit patterns are
  embedded as constants per Annex C / Annex D.

The public surface is two functions, :func:`encode_text` (returns the
finished module matrix) and :func:`to_svg` (renders to an SVG string).
"""

from __future__ import annotations

import dataclasses

# ---- alignment-pattern positions per version (Annex E table) ---------
# fmt: off
_ALIGN_POSITIONS: list[list[int]] = [
    [],  # v0 unused
    [],  # v1 has no alignment patterns
    [6, 18], [6, 22], [6, 26], [6, 30], [6, 34],
    [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50],
    [6, 30, 54], [6, 32, 58], [6, 34, 62],
    [6, 26, 46, 66], [6, 26, 48, 70], [6, 26, 50, 74],
    [6, 30, 54, 78], [6, 30, 56, 82], [6, 30, 58, 86],
    [6, 34, 62, 90],
    [6, 28, 50, 72, 94], [6, 26, 50, 74, 98], [6, 30, 54, 78, 102],
    [6, 28, 54, 80, 106], [6, 32, 58, 84, 110], [6, 30, 58, 86, 114],
    [6, 34, 62, 90, 118],
    [6, 26, 50, 74, 98, 122], [6, 30, 54, 78, 102, 126],
    [6, 26, 52, 78, 104, 130], [6, 30, 56, 82, 108, 134],
    [6, 34, 60, 86, 112, 138], [6, 30, 58, 86, 114, 142],
    [6, 34, 62, 90, 118, 146],
    [6, 30, 54, 78, 102, 126, 150], [6, 24, 50, 76, 102, 128, 154],
    [6, 28, 54, 80, 106, 132, 158], [6, 32, 58, 84, 110, 136, 162],
    [6, 26, 54, 82, 110, 138, 166], [6, 30, 58, 86, 114, 142, 170],
]

# Number of error-correction codewords per block, indexed [ecc][version].
# Source: ISO/IEC 18004:2015 Table 9 (subset replicated for L/M/Q/H).
_NUM_EC_CODEWORDS: list[list[int]] = [
    # ECC L
    [-1,  7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    # ECC M
    [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
    # ECC Q
    [-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    # ECC H
    [-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
]
_NUM_EC_BLOCKS: list[list[int]] = [
    [-1,  1,  1,  1,  1,  1,  2,  2,  2,  2,  4,  4,  4,  4,  4,  6,  6,  6,  6,  7,  8,  8,  9,  9, 10, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25, 27, 30],  # L  # noqa: E501
    [-1,  1,  1,  1,  2,  2,  4,  4,  4,  5,  5,  5,  8,  9,  9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],  # M  # noqa: E501
    [-1,  1,  1,  2,  2,  4,  4,  6,  6,  8,  8,  8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68],  # Q  # noqa: E501
    [-1,  1,  1,  2,  4,  4,  4,  5,  6,  8,  8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81],  # H  # noqa: E501
]
# fmt: on


# ---- ECC levels ------------------------------------------------------


class Ecc:
    L = 0
    M = 1
    Q = 2
    H = 3


# Format-info bit string (5 bits → 15-bit BCH including masking).
# Lookup per (ecc level, mask). Source: ISO/IEC 18004:2015 Annex C.
_FORMAT_BITS: dict[tuple[int, int], int] = {
    (Ecc.L, 0): 0x77C4,
    (Ecc.L, 1): 0x72F3,
    (Ecc.L, 2): 0x7DAA,
    (Ecc.L, 3): 0x789D,  # noqa: E241
    (Ecc.L, 4): 0x662F,
    (Ecc.L, 5): 0x6318,
    (Ecc.L, 6): 0x6C41,
    (Ecc.L, 7): 0x6976,  # noqa: E241
    (Ecc.M, 0): 0x5412,
    (Ecc.M, 1): 0x5125,
    (Ecc.M, 2): 0x5E7C,
    (Ecc.M, 3): 0x5B4B,  # noqa: E241
    (Ecc.M, 4): 0x45F9,
    (Ecc.M, 5): 0x40CE,
    (Ecc.M, 6): 0x4F97,
    (Ecc.M, 7): 0x4AA0,  # noqa: E241
    (Ecc.Q, 0): 0x355F,
    (Ecc.Q, 1): 0x3068,
    (Ecc.Q, 2): 0x3F31,
    (Ecc.Q, 3): 0x3A06,  # noqa: E241
    (Ecc.Q, 4): 0x24B4,
    (Ecc.Q, 5): 0x2183,
    (Ecc.Q, 6): 0x2EDA,
    (Ecc.Q, 7): 0x2BED,  # noqa: E241
    (Ecc.H, 0): 0x1689,
    (Ecc.H, 1): 0x13BE,
    (Ecc.H, 2): 0x1CE7,
    (Ecc.H, 3): 0x19D0,  # noqa: E241
    (Ecc.H, 4): 0x0762,
    (Ecc.H, 5): 0x0255,
    (Ecc.H, 6): 0x0D0C,
    (Ecc.H, 7): 0x083B,  # noqa: E241
}


# Version-info bit string for v7-v40 (6 data bits + 12 BCH parity).
# Source: ISO/IEC 18004:2015 Annex D.
_VERSION_BITS: dict[int, int] = {
    7: 0x07C94,
    8: 0x085BC,
    9: 0x09A99,
    10: 0x0A4D3,
    11: 0x0BBF6,
    12: 0x0C762,
    13: 0x0D847,
    14: 0x0E60D,
    15: 0x0F928,
    16: 0x10B78,
    17: 0x1145D,
    18: 0x12A17,
    19: 0x13532,
    20: 0x149A6,
    21: 0x15683,
    22: 0x168C9,
    23: 0x177EC,
    24: 0x18EC4,
    25: 0x191E1,
    26: 0x1AFAB,
    27: 0x1B08E,
    28: 0x1CC1A,
    29: 0x1D33F,
    30: 0x1ED75,
    31: 0x1F250,
    32: 0x209D5,
    33: 0x216F0,
    34: 0x228BA,
    35: 0x2379F,
    36: 0x24B0B,
    37: 0x2542E,
    38: 0x26A64,
    39: 0x27541,
    40: 0x28C69,
}


# ---- Galois-field arithmetic (GF(2^8) with primitive polynomial 0x11D)


_GF_EXP: list[int] = [0] * 512
_GF_LOG: list[int] = [0] * 256


def _build_gf_tables() -> None:
    x = 1
    for i in range(255):
        _GF_EXP[i] = x
        _GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _GF_EXP[i] = _GF_EXP[i - 255]


_build_gf_tables()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator(degree: int) -> list[int]:
    result = [1]
    for i in range(degree):
        new = [0] * (len(result) + 1)
        for j, coef in enumerate(result):
            new[j] ^= coef
            new[j + 1] ^= _gf_mul(coef, _GF_EXP[i])
        result = new
    return result


def _rs_compute(data: bytes, num_ec: int) -> list[int]:
    gen = _rs_generator(num_ec)
    res = [0] * num_ec
    for b in data:
        factor = b ^ res[0]
        res = res[1:] + [0]
        for i, g in enumerate(gen[1:]):
            res[i] ^= _gf_mul(g, factor)
    return res


# ---- Capacity ---------------------------------------------------------


def _num_raw_data_modules(version: int) -> int:
    """Total data modules available (after subtracting function patterns)."""
    size = version * 4 + 17
    result = size * size
    # finder + format
    result -= 3 * (8 * 8) + 2 * 15 + 1
    # timing
    result -= (size - 16) * 2
    # alignment
    if version >= 2:
        align = len(_ALIGN_POSITIONS[version])
        result -= (align * align - 3) * 25
        result -= (align - 2) * 2 * 20
        if version >= 7:
            result -= 36  # two 6×3 version-info blocks
    return result


def _num_data_codewords(version: int, ecc: int) -> int:
    total = _num_raw_data_modules(version) // 8
    return total - _NUM_EC_CODEWORDS[ecc][version] * _NUM_EC_BLOCKS[ecc][version]


# ---- Encoder ---------------------------------------------------------


@dataclasses.dataclass(slots=True)
class QrCode:
    version: int
    size: int
    ecc: int
    mask: int
    modules: list[list[bool]]


def _byte_mode_length_bits(version: int) -> int:
    if version < 10:
        return 8
    return 16


def _make_data_bitstring(text: str, version: int, ecc: int) -> list[int]:
    """Build the data bit stream (mode + length + payload + terminator
    + padding to the codeword boundary)."""
    payload = text.encode("utf-8")
    capacity_bits = _num_data_codewords(version, ecc) * 8
    bits: list[int] = []
    # Mode indicator: 0100 (byte mode)
    for b in (0, 1, 0, 0):
        bits.append(b)
    length_bits = _byte_mode_length_bits(version)
    n = len(payload)
    for i in range(length_bits - 1, -1, -1):
        bits.append((n >> i) & 1)
    for byte in payload:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    # Terminator + pad to byte boundary
    for _ in range(min(4, capacity_bits - len(bits))):
        bits.append(0)
    while len(bits) % 8 != 0:
        bits.append(0)
    # Pad with the standard 0xEC / 0x11 pattern
    pad = [0xEC, 0x11]
    pi = 0
    while len(bits) < capacity_bits:
        b = pad[pi % 2]
        for i in range(7, -1, -1):
            bits.append((b >> i) & 1)
        pi += 1
    return bits[:capacity_bits]


def _bits_to_codewords(bits: list[int]) -> bytes:
    out = bytearray(len(bits) // 8)
    for i, bit in enumerate(bits):
        if bit:
            out[i // 8] |= 0x80 >> (i % 8)
    return bytes(out)


def _interleave(data: bytes, version: int, ecc: int) -> bytes:
    """Split into blocks, append EC bytes per block, then interleave."""
    num_blocks = _NUM_EC_BLOCKS[ecc][version]
    block_ec_len = _NUM_EC_CODEWORDS[ecc][version]
    raw = _num_raw_data_modules(version) // 8
    num_short_blocks = num_blocks - raw % num_blocks
    short_data_len = raw // num_blocks - block_ec_len

    blocks: list[tuple[bytes, list[int]]] = []
    k = 0
    for i in range(num_blocks):
        data_len = short_data_len + (0 if i < num_short_blocks else 1)
        chunk = data[k : k + data_len]
        k += data_len
        ec = _rs_compute(chunk, block_ec_len)
        blocks.append((chunk, ec))

    result = bytearray()
    # Data codewords interleaved column-major
    max_data_len = short_data_len + 1
    for i in range(max_data_len):
        for j, (chunk, _) in enumerate(blocks):
            if i < short_data_len or j >= num_short_blocks:
                result.append(chunk[i])
    # EC codewords interleaved column-major
    for i in range(block_ec_len):
        for _, ec in blocks:
            result.append(ec[i])
    return bytes(result)


def _pick_version(text: str, ecc: int) -> int:
    payload = text.encode("utf-8")
    for v in range(1, 41):
        cap_bits = _num_data_codewords(v, ecc) * 8
        # mode indicator (4) + length bits + payload bits
        needed = 4 + _byte_mode_length_bits(v) + len(payload) * 8
        if needed <= cap_bits:
            return v
    raise ValueError(f"payload of {len(payload)} bytes does not fit at ECC {ecc}")


# ---- Matrix construction ---------------------------------------------


def _init_matrix(size: int) -> tuple[list[list[bool]], list[list[bool]]]:
    modules = [[False] * size for _ in range(size)]
    is_function = [[False] * size for _ in range(size)]
    return modules, is_function


def _draw_finder(modules, is_function, x: int, y: int) -> None:
    for dy in range(-1, 8):
        for dx in range(-1, 8):
            xx, yy = x + dx, y + dy
            if 0 <= xx < len(modules) and 0 <= yy < len(modules):
                is_function[yy][xx] = True
                dist = max(abs(dx - 3), abs(dy - 3))
                modules[yy][xx] = dist != 2 and dist != 4


def _draw_alignment(modules, is_function, x: int, y: int) -> None:
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            is_function[y + dy][x + dx] = True
            modules[y + dy][x + dx] = max(abs(dx), abs(dy)) != 1


def _draw_function_patterns(version: int, modules, is_function) -> None:
    size = len(modules)
    # Timing patterns
    for i in range(size):
        is_function[6][i] = True
        is_function[i][6] = True
        modules[6][i] = i % 2 == 0
        modules[i][6] = i % 2 == 0
    # Finder patterns (top-left, top-right, bottom-left)
    _draw_finder(modules, is_function, 3, 3)
    _draw_finder(modules, is_function, size - 4, 3)
    _draw_finder(modules, is_function, 3, size - 4)
    # Alignment patterns (skip ones overlapping finders)
    pos = _ALIGN_POSITIONS[version]
    n = len(pos)
    for i in range(n):
        for j in range(n):
            if (i, j) in ((0, 0), (0, n - 1), (n - 1, 0)):
                continue
            _draw_alignment(modules, is_function, pos[i], pos[j])
    # Reserve format-info modules
    for i in range(9):
        is_function[8][i] = True
        is_function[i][8] = True
    for i in range(8):
        is_function[8][size - 1 - i] = True
        is_function[size - 1 - i][8] = True
    # Dark module
    modules[size - 8][8] = True
    # Reserve version-info area (v7+)
    if version >= 7:
        for i in range(6):
            for j in range(3):
                is_function[size - 11 + j][i] = True
                is_function[i][size - 11 + j] = True


def _draw_data(modules, is_function, codewords: bytes) -> None:
    size = len(modules)
    i = 0  # bit index
    # Right-to-left zig-zag columns, skipping the timing-pattern column
    for right in range(size - 1, 0, -2):
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = size - 1 - vert if upward else vert
                if is_function[y][x] or i >= len(codewords) * 8:
                    continue
                bit = (codewords[i >> 3] >> (7 - (i & 7))) & 1
                modules[y][x] = bool(bit)
                i += 1


def _apply_mask(modules, is_function, mask: int) -> None:
    for y, row in enumerate(modules):
        for x in range(len(row)):
            if is_function[y][x]:
                continue
            if mask == 0:
                inv = (x + y) % 2 == 0
            elif mask == 1:
                inv = y % 2 == 0
            elif mask == 2:
                inv = x % 3 == 0
            elif mask == 3:
                inv = (x + y) % 3 == 0
            elif mask == 4:
                inv = (x // 3 + y // 2) % 2 == 0
            elif mask == 5:
                inv = (x * y) % 2 + (x * y) % 3 == 0
            elif mask == 6:
                inv = ((x * y) % 2 + (x * y) % 3) % 2 == 0
            else:
                inv = ((x + y) % 2 + (x * y) % 3) % 2 == 0
            if inv:
                row[x] = not row[x]


def _draw_format_bits(modules, ecc: int, mask: int) -> None:
    bits = _FORMAT_BITS[(ecc, mask)]
    size = len(modules)
    # First copy: around top-left finder
    for i in range(6):
        modules[8][i] = ((bits >> i) & 1) == 1
    modules[8][7] = ((bits >> 6) & 1) == 1
    modules[8][8] = ((bits >> 7) & 1) == 1
    modules[7][8] = ((bits >> 8) & 1) == 1
    for i in range(9, 15):
        modules[14 - i][8] = ((bits >> i) & 1) == 1
    # Second copy: top-right + bottom-left strips
    for i in range(8):
        modules[size - 1 - i][8] = ((bits >> i) & 1) == 1
    for i in range(8, 15):
        modules[8][size - 15 + i] = ((bits >> i) & 1) == 1
    modules[size - 8][8] = True  # dark module is fixed


def _draw_version_bits(modules, version: int) -> None:
    if version < 7:
        return
    bits = _VERSION_BITS[version]
    size = len(modules)
    for i in range(18):
        bit = ((bits >> i) & 1) == 1
        a = size - 11 + (i % 3)
        b = i // 3
        modules[a][b] = bit
        modules[b][a] = bit


def _mask_penalty(modules) -> int:
    """ISO/IEC 18004 §6.8.3 penalty score."""
    size = len(modules)
    score = 0
    # Rule 1: runs of same colour ≥5 in rows/columns
    for y in range(size):
        run_color = modules[y][0]
        run_len = 1
        for x in range(1, size):
            if modules[y][x] == run_color:
                run_len += 1
            else:
                if run_len >= 5:
                    score += 3 + (run_len - 5)
                run_color = modules[y][x]
                run_len = 1
        if run_len >= 5:
            score += 3 + (run_len - 5)
    for x in range(size):
        run_color = modules[0][x]
        run_len = 1
        for y in range(1, size):
            if modules[y][x] == run_color:
                run_len += 1
            else:
                if run_len >= 5:
                    score += 3 + (run_len - 5)
                run_color = modules[y][x]
                run_len = 1
        if run_len >= 5:
            score += 3 + (run_len - 5)
    # Rule 2: 2×2 same-colour blocks
    for y in range(size - 1):
        for x in range(size - 1):
            c = modules[y][x]
            if c == modules[y][x + 1] == modules[y + 1][x] == modules[y + 1][x + 1]:
                score += 3
    # Rule 3: finder-like patterns (1011101 plus four-light run)
    for y in range(size):
        for x in range(size - 10):
            row = modules[y]
            if (
                row[x]
                and not row[x + 1]
                and row[x + 2]
                and row[x + 3]
                and row[x + 4]
                and not row[x + 5]
                and row[x + 6]
            ) and ((x >= 4 and not any(row[x - 4 : x])) or (x + 11 <= size and not any(row[x + 7 : x + 11]))):
                score += 40
    for x in range(size):
        for y in range(size - 10):
            col = [modules[y + k][x] for k in range(11)]
            if (col[0] and not col[1] and col[2] and col[3] and col[4] and not col[5] and col[6]) and (
                (y >= 4 and not any(modules[y - 4 + k][x] for k in range(4)))
                or (y + 11 <= size and not any(col[7:11]))
            ):
                score += 40
    # Rule 4: balance of dark/light
    dark = sum(sum(1 for v in row if v) for row in modules)
    total = size * size
    ratio = abs(dark * 20 - total * 10) // total
    score += ratio * 10
    return score


def encode_text(text: str, ecc: int = Ecc.M) -> QrCode:
    """Encode ``text`` and return a finished :class:`QrCode`."""
    version = _pick_version(text, ecc)
    size = version * 4 + 17
    bits = _make_data_bitstring(text, version, ecc)
    data_codewords = _bits_to_codewords(bits)
    interleaved = _interleave(data_codewords, version, ecc)

    best: tuple[int, list[list[bool]], int] | None = None
    for mask in range(8):
        modules, is_function = _init_matrix(size)
        _draw_function_patterns(version, modules, is_function)
        _draw_data(modules, is_function, interleaved)
        _apply_mask(modules, is_function, mask)
        _draw_format_bits(modules, ecc, mask)
        _draw_version_bits(modules, version)
        penalty = _mask_penalty(modules)
        if best is None or penalty < best[0]:
            best = (penalty, modules, mask)
    assert best is not None
    return QrCode(version=version, size=size, ecc=ecc, mask=best[2], modules=best[1])


def to_svg(qr: QrCode, *, quiet_zone: int = 2, scale: int = 8) -> str:
    """Render the QR matrix as an SVG string suitable for inline render."""
    if quiet_zone < 0:
        raise ValueError("quiet_zone must be ≥0")
    if scale < 1:
        raise ValueError("scale must be ≥1")
    side = qr.size + 2 * quiet_zone
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {side} {side}"'
        f' shape-rendering="crispEdges" width="{side * scale}" height="{side * scale}">',
        f'<rect width="{side}" height="{side}" fill="#ffffff"/>',
        '<path fill="#000000" d="',
    ]
    # Build a single path of all dark module rectangles for compact SVG.
    for y in range(qr.size):
        for x in range(qr.size):
            if qr.modules[y][x]:
                parts.append(f"M{x + quiet_zone},{y + quiet_zone}h1v1h-1z")
    parts.append('"/></svg>')
    return "".join(parts)


__all__ = ["Ecc", "QrCode", "encode_text", "to_svg"]
