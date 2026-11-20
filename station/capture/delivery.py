"""Transfer each device delivery to receipt and apply only new quality facts."""

from dataclasses import dataclass
from enum import StrEnum

from station.capture.port import PhysicalObservation, PortStep
from station.capture.recording import SourceFrame
from station.quality.tally import RoundTally, SegmentLedger
from station.receipt.frame import FrameVerdict
from station.receipt.ingest import ReceiptOutcome, ingest
from station.receipt.journal import ReceiptJournal


class DeliveryStatus(StrEnum):
    ACCEPTED = 'accepted'
    FILTERED = 'filtered'
    REJECTED = 'rejected'


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    observation: PhysicalObservation
    receipts: tuple[ReceiptOutcome, ...]

    @property
    def status(self) -> DeliveryStatus:
        if not self.receipts or self.receipts[0].verdict is FrameVerdict.FILTERED:
            return DeliveryStatus.FILTERED
        if self.receipts[0].verdict is FrameVerdict.ACCEPTED:
            return DeliveryStatus.ACCEPTED
        return DeliveryStatus.REJECTED


def deliver_received(
    frame: SourceFrame,
    journal: ReceiptJournal,
    tally: RoundTally | SegmentLedger,
    subscription: frozenset[str],
    raw_min: int,
    raw_max: int,
) -> ReceiptOutcome:
    receipt = ingest(journal, frame, subscription, raw_min, raw_max)
    if receipt.is_new:
        if receipt.verdict is FrameVerdict.ACCEPTED:
            tally.record_valid(frame.stream, frame.source_seq, frame.raw_code)
        elif receipt.verdict is FrameVerdict.BAD_CODE:
            tally.record_bad(frame.stream)
    return receipt


def deliver_frame(
    step: PortStep,
    journal: ReceiptJournal,
    tally: RoundTally | SegmentLedger,
    subscription: frozenset[str],
    raw_min: int,
    raw_max: int,
) -> DeliveryOutcome:
    receipts = tuple(
        deliver_received(frame, journal, tally, subscription, raw_min, raw_max)
        for frame in step.deliveries
    )
    return DeliveryOutcome(step.observation, receipts)
