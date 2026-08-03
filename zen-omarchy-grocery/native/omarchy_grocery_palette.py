#!/usr/bin/env python3
"""Native-messaging bridge exposing the current Omarchy colors.toml."""

import json
import os
import re
import struct
import sys

COLORS_FILE = os.path.expanduser("~/.config/omarchy/current/theme/colors.toml")
COLOR_KEYS = {"accent", "foreground", "background", "color0", "color4", "color7", "color8"}


def read_message():
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) != 4:
        return None
    length = struct.unpack("<I", raw_length)[0]
    return json.loads(sys.stdin.buffer.read(length))


def palette():
    colors = {}
    with open(COLORS_FILE, encoding="utf-8") as source:
        for line in source:
            match = re.fullmatch(r"([A-Za-z0-9_]+)\s*=\s*\"(#[0-9A-Fa-f]{6})\"\s*", line.strip())
            if match and match.group(1) in COLOR_KEYS:
                colors[match.group(1)] = match.group(2)
    if COLOR_KEYS - colors.keys():
        raise ValueError("incomplete Omarchy palette")
    return {"ok": True, "colors": colors}


def send_message(value):
    payload = json.dumps(value, separators=(",", ":")).encode()
    sys.stdout.buffer.write(struct.pack("<I", len(payload)) + payload)
    sys.stdout.buffer.flush()


while True:
    message = read_message()
    if message is None:
        break
    try:
        send_message(palette())
    except Exception as error:  # native messaging must always return JSON
        send_message({"ok": False, "error": str(error)})
