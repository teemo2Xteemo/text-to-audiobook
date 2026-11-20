"""Stream a ZIP of ready chapter files without loading the book into memory."""

from __future__ import annotations

import asyncio
import contextlib
import io
import queue
import shutil
import threading
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

_CHUNK = 1024 * 1024
_PUT_TIMEOUT_SECONDS = 0.2


class _QueueWriter(io.RawIOBase):
    def __init__(
        self,
        messages: queue.Queue[bytes | BaseException | None],
        cancelled: threading.Event,
    ) -> None:
        self._messages = messages
        self._cancelled = cancelled

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def write(self, b: bytes) -> int:  # type: ignore[override]
        if self._cancelled.is_set():
            raise BrokenPipeError("zip cancelled")
        payload = bytes(b)
        if not payload:
            return 0
        while not self._cancelled.is_set():
            try:
                self._messages.put(payload, timeout=_PUT_TIMEOUT_SECONDS)
                return len(payload)
            except queue.Full:
                continue
        raise BrokenPipeError("zip cancelled")


def _wake_blocked_get(messages: queue.Queue[bytes | BaseException | None]) -> None:
    while True:
        try:
            messages.get_nowait()
        except queue.Empty:
            break
    try:
        messages.put_nowait(None)
    except queue.Full:
        return


def _offer(
    messages: queue.Queue[bytes | BaseException | None],
    cancelled: threading.Event,
    item: bytes | BaseException | None,
) -> None:
    while not cancelled.is_set():
        try:
            messages.put(item, timeout=_PUT_TIMEOUT_SECONDS)
            return
        except queue.Full:
            continue


async def iter_stored_zip(entries: list[tuple[str, Path]]) -> AsyncIterator[bytes]:
    """ZIP_STORED entries with ZIP64 data descriptors. MP3 is already compressed.

    The writer thread reads each chapter from disk in 1 MiB pieces. The event
    loop only waits on the bounded queue, so a multi-gigabyte book is not
    buffered in RAM. If the client disconnects, the generator's ``finally``
    stops the thread and closes the chapter file it was reading.
    """
    cancelled = threading.Event()
    messages: queue.Queue[bytes | BaseException | None] = queue.Queue(maxsize=8)

    def _produce() -> None:
        try:
            with zipfile.ZipFile(
                _QueueWriter(messages, cancelled),
                "w",
                compression=zipfile.ZIP_STORED,
                allowZip64=True,
            ) as archive:
                for name, path in entries:
                    if cancelled.is_set():
                        return
                    info = zipfile.ZipInfo(filename=name)
                    info.compress_type = zipfile.ZIP_STORED
                    with (
                        archive.open(info, "w", force_zip64=True) as dest,
                        path.open("rb") as source,
                    ):
                        shutil.copyfileobj(source, dest, length=_CHUNK)
        except BaseException as exc:  # noqa: BLE001 - the consumer re-raises it
            if not cancelled.is_set():
                _offer(messages, cancelled, exc)
        finally:
            _offer(messages, cancelled, None)

    thread = threading.Thread(target=_produce, name="chapter-zip", daemon=True)
    thread.start()
    try:
        while True:
            item = await asyncio.to_thread(messages.get)
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        cancelled.set()
        # A cancelled `to_thread(messages.get)` does not stop the executor thread.
        # Wake it, or that worker stays blocked and later ZIP downloads hang.
        _wake_blocked_get(messages)
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(asyncio.to_thread(thread.join, 2.0))
