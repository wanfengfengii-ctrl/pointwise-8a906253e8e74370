"""Decide business eligibility for a physically delivered frame."""

from dataclasses import dataclass

from station.capture.recording import SourceFrame
from station.receipt.frame import FrameVerdict, ReceivedFrame, raw_code_in_range
from station.receipt.journal import ReceiptJournal
from station.receipt.order import ArrivalOrder, classify_arrival
from station.receipt.rejections import RejectionKind, make_rejection


@dataclass(frozen=True, slots=True)
class ReceiptOutcome:
    verdict: FrameVerdict | None
    rejection: RejectionKind | None
    is_new: bool


def ingest(
    journal: ReceiptJournal,
    frame: SourceFrame,
    subscription: frozenset[str],
    raw_min: int,
    raw_max: int,
) -> ReceiptOutcome:
    first = journal.first_at(frame.source_seq)
    last_seq = journal.last_first_seq
    order = classify_arrival(frame, first, last_seq)

    if first is not None:
        if order is ArrivalOrder.REPLAY:
            rejection = RejectionKind.BAD_CODE if first.verdict is FrameVerdict.BAD_CODE else None
            return ReceiptOutcome(first.verdict, rejection, False)
        kind = RejectionKind.CONFLICT
        journal.record_rejection(make_rejection(kind, frame, last_seq, first))
        return ReceiptOutcome(None, kind, False)

    if order is ArrivalOrder.OUT_OF_ORDER:
        kind = RejectionKind.OUT_OF_ORDER
        journal.record_rejection(make_rejection(kind, frame, last_seq))
        return ReceiptOutcome(None, kind, False)

    if frame.stream not in subscription:
        verdict = FrameVerdict.FILTERED
    elif raw_code_in_range(frame, raw_min, raw_max):
        verdict = FrameVerdict.ACCEPTED
    else:
        verdict = FrameVerdict.BAD_CODE
    journal.record_first(ReceivedFrame(frame, verdict))

    if verdict is FrameVerdict.BAD_CODE:
        kind = RejectionKind.BAD_CODE
        journal.record_rejection(make_rejection(kind, frame, frame.source_seq))
        return ReceiptOutcome(verdict, kind, True)
    return ReceiptOutcome(verdict, None, True)
