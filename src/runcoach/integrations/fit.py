"""A minimal FIT file writer: workout files for the watch (P4-T7) and test fixtures.

fitparse only reads FIT, so this writes the few message types needed, following the FIT
protocol: a 14-byte header, one definition message before each data message, and a CRC.
"""

from __future__ import annotations

import struct
from datetime import datetime, timezone

from runcoach.integrations.watch import RepeatGroup, WorkoutSpec, WorkoutStep

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)
_PROFILE_VERSION = 2100
# FIT base types: (id, struct format, size). Strings are padded to their declared size.
_BASE = {
    "enum": (0x00, "B", 1),
    "uint8": (0x02, "B", 1),
    "uint16": (0x84, "H", 2),
    "uint32": (0x86, "I", 4),
    "uint32z": (0x8C, "I", 4),
}
_CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)  # fmt: skip

# (field number, base type name or "string:<size>", value)
Field = tuple[int, str, int | str]
Message = tuple[int, list[Field]]  # (global message number, fields)


def crc(data: bytes, value: int = 0) -> int:
    """The FIT CRC-16 used for the header and the whole file."""
    for byte in data:
        for nibble in (byte & 0xF, byte >> 4):
            tmp = _CRC_TABLE[value & 0xF]
            value = ((value >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[nibble]
    return value


def fit_time(moment: datetime) -> int:
    """Seconds since the FIT epoch."""
    return int((moment - FIT_EPOCH).total_seconds())


def encode(messages: list[Message]) -> bytes:
    """Encode messages into a complete FIT file (every message redefines local type 0)."""
    body = bytearray()
    for global_number, fields in messages:
        definition = bytearray(struct.pack("<BBBHB", 0x40, 0, 0, global_number, len(fields)))
        data = bytearray(b"\x00")
        for number, kind, value in fields:
            if kind.startswith("string:"):
                size = int(kind.split(":")[1])
                raw = str(value).encode("utf-8")[: size - 1]
                definition += struct.pack("<BBB", number, size, 0x07)
                data += raw + b"\x00" * (size - len(raw))
            else:
                base_id, fmt, size = _BASE[kind]
                definition += struct.pack("<BBB", number, size, base_id)
                data += struct.pack("<" + fmt, int(value))
        body += definition + data
    header = struct.pack("<BBHI4s", 14, 0x20, _PROFILE_VERSION, len(body), b".FIT")
    header += struct.pack("<H", crc(header))
    content = header + bytes(body)
    return content + struct.pack("<H", crc(content))


# FIT profile values for workout_step.
_DURATION = {"time": 0, "distance": 1, "open": 5}
_REPEAT_UNTIL_STEPS = 6
_TARGET_SPEED, _TARGET_OPEN = 0, 2
_INTENSITY = {"warmup": 2, "cooldown": 3, "recovery": 1, "interval": 0, "run": 0}


def _step_fields(index: int, step: WorkoutStep) -> list[Field]:
    fields: list[Field] = [(254, "uint16", index), (1, "enum", _DURATION[step.end])]
    if step.end == "time" and step.value is not None:
        fields.append((2, "uint32", round(step.value * 1000)))  # milliseconds
    elif step.end == "distance" and step.value is not None:
        fields.append((2, "uint32", round(step.value * 100)))  # centimetres
    if step.pace_fast_s_per_km and step.pace_slow_s_per_km:
        # A custom speed range in mm/s; the slower pace is the low speed.
        fields += [
            (3, "enum", _TARGET_SPEED),
            (4, "uint32", 0),
            (5, "uint32", round(1_000_000 / step.pace_slow_s_per_km)),
            (6, "uint32", round(1_000_000 / step.pace_fast_s_per_km)),
        ]
    else:
        fields.append((3, "enum", _TARGET_OPEN))
    fields.append((7, "enum", _INTENSITY[step.kind]))
    return fields


def workout_file(spec: WorkoutSpec, created: datetime) -> bytes:
    """A running workout as a FIT file the watch can load from GARMIN/NewFiles."""
    steps: list[list[Field]] = []
    for item in spec.steps:
        if isinstance(item, RepeatGroup):
            first = len(steps)
            for child in item.steps:
                steps.append(_step_fields(len(steps), child))
            steps.append([
                (254, "uint16", len(steps)),
                (1, "enum", _REPEAT_UNTIL_STEPS),
                (2, "uint32", first),  # jump back to this step index
                (4, "uint32", item.iterations),
            ])  # fmt: skip
        else:
            steps.append(_step_fields(len(steps), item))
    messages: list[Message] = [
        (0, [(0, "enum", 5), (1, "uint16", 255), (2, "uint16", 0),
             (4, "uint32", fit_time(created))]),
        (26, [(4, "enum", 1), (6, "uint16", len(steps)), (8, "string:32", spec.name)]),
        *((27, fields) for fields in steps),
    ]  # fmt: skip
    return encode(messages)
