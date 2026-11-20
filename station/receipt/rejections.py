"""The raw facts retained when a delivery is refused."""

from dataclasses import dataclass
from enum import StrEnum

from station.capture.recording import SourceFrame
from station.receipt.frame import ReceivedFrame


class RejectionKind(StrEnum):
    BAD_CODE = 'bad_code'
    CONFLICT = 'conflict'
    OUT_OF_ORDER = 'out_of_order'


@dataclass(frozen=True, slots=True)
class Rejection:
    kind: RejectionKind
    incoming: SourceFrame
    first: SourceFrame | None
    after_first_seq: int


def make_rejection(
    kind: RejectionKind,
    incoming: SourceFrame,
    after_first_seq: int,
    first: ReceivedFrame | None = None,
) -> Rejection:
    if (kind is RejectionKind.CONFLICT) != (first is not None):
        raise ValueError('only conflicts must identify an existing first frame')
    return Rejection(kind, incoming, first.frame if first is not None else None, after_first_seq)
