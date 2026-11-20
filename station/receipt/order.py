"""Classify a delivery relative to the first-receipt index."""

from enum import StrEnum

from station.capture.recording import SourceFrame
from station.receipt.frame import ReceivedFrame, frame_identity


class ArrivalOrder(StrEnum):
    NEW = 'new'
    REPLAY = 'replay'
    CONFLICT = 'conflict'
    OUT_OF_ORDER = 'out_of_order'


def classify_arrival(
    frame: SourceFrame, first: ReceivedFrame | None, last_first_seq: int | None
) -> ArrivalOrder:
    if first is not None:
        if frame_identity(frame) == frame_identity(first.frame):
            return ArrivalOrder.REPLAY
        return ArrivalOrder.CONFLICT
    if last_first_seq is not None and frame.source_seq <= last_first_seq:
        return ArrivalOrder.OUT_OF_ORDER
    return ArrivalOrder.NEW
