"""Write evidence clips by remuxing buffered packets (no decode, no encode)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import av

from .ringbuffer import BufferedPacket


def write_clip(packets: list[BufferedPacket], template_stream, path: str | Path) -> str:
    """Remux packets into an MP4 at path and return the file's SHA-256.

    The file is written to a temporary name and renamed, so a crash never
    leaves a half-written clip under the final name."""
    if not packets:
        raise ValueError("no packets to write")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        with av.open(str(tmp), mode="w", format="mp4") as out:
            stream = out.add_stream_from_template(template_stream)
            first = packets[0].packet
            offset = first.dts if first.dts is not None else (first.pts or 0)
            for bp in packets:
                pkt = bp.packet
                if pkt.dts is None and pkt.pts is None:
                    continue
                pkt.stream = stream
                if pkt.pts is not None:
                    pkt.pts -= offset
                if pkt.dts is not None:
                    pkt.dts -= offset
                out.mux(pkt)
                # Restore so the same packet can go into another clip.
                if pkt.pts is not None:
                    pkt.pts += offset
                if pkt.dts is not None:
                    pkt.dts += offset
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
