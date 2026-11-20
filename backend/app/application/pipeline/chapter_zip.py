"""Stream a ZIP of ready chapter files without loading the book into memory."""

from __future__ import annotations

import asyncio
import io
import queue
import shutil
import threading
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

_CHUNK = 1024 * 1024


class _QueueWriter(io.RawIOBase):
    def __init__(self, messages: queue.Queue[bytes | BaseException | None]) -> None:
        self._messages = messages

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def write(self, b: bytes) -> int:  # type: ignore[override]
        payload = bytes(b)
        if payload:
            self._messages.put(payload)
        return len(payload)


async def iter_stored_zip(entries: list[tuple[str, Path]]) -> AsyncIterator[bytes]:
    """ZIP_STORED entries with ZIP64 data descriptors. MP3 is already compressed.

    The writer thread reads each chapter from disk in 1 MiB pieces. The event
    loop only waits on the bounded queue, so a multi-gigabyte book is not
    buffered in RAM.
    """
    messages: queue.Queue[bytes | BaseException | None] = queue.Queue(maxsize=8)

    def _produce() -> None:
        try:
            with zipfile.ZipFile(
                _QueueWriter(messages),
                "w",
                compression=zipfile.ZIP_STORED,
                allowZip64=True,
            ) as archive:
                for name, path in entries:
                    info = zipfile.ZipInfo(filename=name)
                    info.compress_type = zipfile.ZIP_STORED
                    with (
                        archive.open(info, "w", force_zip64=True) as dest,
                        path.open("rb") as source,
                    ):
                        shutil.copyfileobj(source, dest, length=_CHUNK)
        except BaseException as exc:  # noqa: BLE001 - the consumer re-raises it
            messages.put(exc)
        finally:
            messages.put(None)

    threading.Thread(target=_produce, name="chapter-zip", daemon=True).start()
    while True:
        item = await asyncio.to_thread(messages.get)
        if item is None:
            return
        if isinstance(item, BaseException):
            raise item
        yield item
