"""Serial device playback and the physical observation boundary."""

from dataclasses import dataclass

from station.capture.filter_table import FixedFilterTable
from station.capture.recording import Recording, RecordingCursor, SourceFrame


@dataclass(frozen=True, slots=True)
class PhysicalObservation:
    source_seq: int
    stream: str
    forwarded: bool


@dataclass(frozen=True, slots=True)
class PortStep:
    observation: PhysicalObservation
    deliveries: tuple[SourceFrame, ...]


class ReplayPort:
    def __init__(self) -> None:
        self._cursor: RecordingCursor | None = None
        self._table: FixedFilterTable | None = None
        self._running = False
        self._last_source_seq: int | None = None
        self._observations: list[PhysicalObservation] = []

    def start(self, recording: Recording, table: FixedFilterTable) -> None:
        if self._cursor is not None:
            raise RuntimeError('port has already been started')
        self._cursor = RecordingCursor(recording)
        self._table = table
        self._running = True

    def _running_state(self) -> tuple[RecordingCursor, FixedFilterTable]:
        if not self._running or self._cursor is None or self._table is None:
            raise RuntimeError('port is not running')
        return self._cursor, self._table

    def _emit(self, frame: SourceFrame, table: FixedFilterTable) -> PortStep:
        deliveries = (frame,) if table.allows(frame.stream) else ()
        observation = PhysicalObservation(frame.source_seq, frame.stream, bool(deliveries))
        self._observations.append(observation)
        self._last_source_seq = frame.source_seq
        return PortStep(observation, deliveries)

    def advance(self) -> PortStep:
        cursor, table = self._running_state()
        return self._emit(cursor.next_frame(), table)

    @property
    def table(self) -> FixedFilterTable:
        _, table = self._running_state()
        return table

    def replace_table(self, table: FixedFilterTable) -> None:
        """Change the physical slot table as one device action, keeping the cursor."""
        self._running_state()
        self._table = table

    @property
    def last_source_seq(self) -> int | None:
        return self._last_source_seq

    def supply(self, frame: SourceFrame) -> PortStep:
        """Insert a real source frame in the gap before the next recording item."""
        cursor, table = self._running_state()
        if not isinstance(frame, SourceFrame):
            raise ValueError('supply requires a source frame')
        if self._last_source_seq is not None and frame.source_seq <= self._last_source_seq:
            raise ValueError('supplied frame must follow the last source frame')
        next_seq = cursor.next_source_seq
        if next_seq is not None and frame.source_seq >= next_seq:
            raise ValueError('supplied frame must precede the next recorded frame')
        return self._emit(frame, table)

    def stop(self) -> None:
        if self._cursor is None:
            raise RuntimeError('port has never been started')
        self._running = False

    def observations(self) -> tuple[PhysicalObservation, ...]:
        return tuple(self._observations)

    @property
    def recording_position(self) -> int:
        return self._cursor.position if self._cursor is not None else 0
