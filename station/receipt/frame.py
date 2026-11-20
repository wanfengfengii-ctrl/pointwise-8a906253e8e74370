"""Raw identity and the durable verdict for a first receipt."""

from dataclasses import dataclass
from enum import StrEnum

from station.capture.recording import SourceFrame


@dataclass(frozen=True, slots=True)
class FrameIdentity:
    stream: str
    raw_code: int


class FrameVerdict(StrEnum):
    ACCEPTED = 'accepted'
    FILTERED = 'filtered'
    BAD_CODE = 'bad_code'


@dataclass(frozen=True, slots=True)
class ReceivedFrame:
    frame: SourceFrame
    verdict: FrameVerdict


def frame_identity(frame: SourceFrame) -> FrameIdentity:
    return FrameIdentity(frame.stream, frame.raw_code)


def raw_code_in_range(frame: SourceFrame, raw_min: int, raw_max: int) -> bool:
    return raw_min <= frame.raw_code <= raw_max
