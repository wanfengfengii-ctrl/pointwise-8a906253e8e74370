"""First-receipt index and per-round rejection log."""

from dataclasses import dataclass

from station.receipt.frame import ReceivedFrame
from station.receipt.rejections import Rejection


@dataclass(frozen=True, slots=True)
class JournalSnapshot:
    first_frames: tuple[ReceivedFrame, ...]
    rejections: tuple[Rejection, ...]


class ReceiptJournal:
    def __init__(self) -> None:
        self._first: dict[int, ReceivedFrame] = {}
        self._last_first_seq: int | None = None
        self._rejections: list[Rejection] = []

    def first_at(self, source_seq: int) -> ReceivedFrame | None:
        return self._first.get(source_seq)

    @property
    def last_first_seq(self) -> int | None:
        return self._last_first_seq

    def record_first(self, received: ReceivedFrame) -> None:
        sequence = received.frame.source_seq
        if self._last_first_seq is not None and sequence <= self._last_first_seq:
            raise ValueError('first receipts must strictly increase by source sequence')
        self._first[sequence] = received
        self._last_first_seq = sequence

    def record_rejection(self, rejection: Rejection) -> None:
        if rejection.after_first_seq != self._last_first_seq:
            raise ValueError('rejection does not reference the current receipt position')
        self._rejections.append(rejection)

    def snapshot(self) -> JournalSnapshot:
        return JournalSnapshot(tuple(self._first.values()), tuple(self._rejections))
