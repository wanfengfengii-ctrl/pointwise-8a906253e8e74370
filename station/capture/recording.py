"""Source recordings and independent, order-preserving playback cursors."""

from dataclasses import dataclass


class RecordingExhausted(LookupError):
    """There is no further recorded frame to advance."""


@dataclass(frozen=True, slots=True)
class SourceFrame:
    source_seq: int
    stream: str
    raw_code: int

    def __post_init__(self) -> None:
        if type(self.source_seq) is not int or type(self.raw_code) is not int:
            raise ValueError('source sequence and raw code must be integers')
        if not isinstance(self.stream, str) or not self.stream.strip():
            raise ValueError('source stream must be a nonempty name')


@dataclass(frozen=True, slots=True)
class Recording:
    raw_min: int
    raw_max: int
    frames: tuple[SourceFrame, ...]

    def __post_init__(self) -> None:
        if type(self.raw_min) is not int or type(self.raw_max) is not int or self.raw_min > self.raw_max:
            raise ValueError('recording requires an ordered integer raw-code interval')
        frames = tuple(self.frames)
        previous = None
        for frame in frames:
            if not isinstance(frame, SourceFrame):
                raise ValueError('recording items must be source frames')
            if previous is not None and frame.source_seq <= previous:
                raise ValueError('recording source sequences must strictly increase')
            previous = frame.source_seq
        object.__setattr__(self, 'frames', frames)

    @classmethod
    def from_document(cls, document: dict) -> 'Recording':
        frames = tuple(SourceFrame(**frame) for frame in document['frames'])
        return cls(document['raw_min'], document['raw_max'], frames)


class RecordingCatalog:
    def __init__(self) -> None:
        self._recordings: dict[str, Recording] = {}

    def register(self, recording_id: str, recording: Recording) -> None:
        if not isinstance(recording_id, str) or not recording_id.strip():
            raise ValueError('recording id must be nonempty')
        if not isinstance(recording, Recording):
            raise ValueError('recording must be validated before registration')
        if recording_id in self._recordings:
            raise ValueError(f'recording {recording_id} already exists')
        self._recordings[recording_id] = recording

    def resolve(self, recording_id: str) -> Recording:
        return self._recordings[recording_id]


class RecordingCursor:
    def __init__(self, recording: Recording) -> None:
        self._recording = recording
        self._position = 0

    @property
    def position(self) -> int:
        return self._position

    @property
    def next_source_seq(self) -> int | None:
        if self._position == len(self._recording.frames):
            return None
        return self._recording.frames[self._position].source_seq

    def next_frame(self) -> SourceFrame:
        if self._position == len(self._recording.frames):
            raise RecordingExhausted('recording has no further source frames')
        frame = self._recording.frames[self._position]
        self._position += 1
        return frame
