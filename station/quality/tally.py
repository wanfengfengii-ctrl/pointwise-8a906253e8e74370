"""Independent running quality ledgers and immutable stream readings."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from station.procedure.calibration import CalibrationSpec
from station.quality.conversion import to_micro_units
from station.quality.limits import QualityLevel, classify_value


class UnknownRound(KeyError):
    """No such round exists in this station."""


@dataclass(frozen=True, slots=True)
class StreamTally:
    valid_count: int = 0
    total_micro: int = 0
    minimum: int | None = None
    minimum_seq: int | None = None
    maximum: int | None = None
    maximum_seq: int | None = None
    over_limit: int = 0
    bad_count: int = 0


class RoundTally:
    def __init__(self, streams: Iterable[str], calibrations: Mapping[str, CalibrationSpec]) -> None:
        self._calibrations = {stream: calibrations[stream] for stream in sorted(set(streams))}
        self._tallies = {stream: StreamTally() for stream in self._calibrations}

    def record_valid(self, stream: str, source_seq: int, raw_code: int) -> None:
        tally = self._tallies[stream]
        calibration = self._calibrations[stream]
        value = to_micro_units(raw_code, calibration)
        over_limit = classify_value(value, calibration.upper_limit) is QualityLevel.OVER_LIMIT

        minimum, minimum_seq = tally.minimum, tally.minimum_seq
        if minimum is None or value < minimum or (value == minimum and source_seq < minimum_seq):
            minimum, minimum_seq = value, source_seq
        maximum, maximum_seq = tally.maximum, tally.maximum_seq
        if maximum is None or value > maximum or (value == maximum and source_seq < maximum_seq):
            maximum, maximum_seq = value, source_seq

        self._tallies[stream] = StreamTally(
            valid_count=tally.valid_count + 1,
            total_micro=tally.total_micro + value,
            minimum=minimum,
            minimum_seq=minimum_seq,
            maximum=maximum,
            maximum_seq=maximum_seq,
            over_limit=tally.over_limit + int(over_limit),
            bad_count=tally.bad_count,
        )

    def record_bad(self, stream: str) -> None:
        tally = self._tallies[stream]
        self._tallies[stream] = replace(tally, bad_count=tally.bad_count + 1)

    def snapshot(self) -> dict[str, StreamTally]:
        return dict(self._tallies)


class LiveLedger:
    def __init__(self) -> None:
        self._rounds: dict[str, RoundTally] = {}

    def open(
        self, round_id: str, streams: Iterable[str], calibrations: Mapping[str, CalibrationSpec]
    ) -> RoundTally:
        if round_id in self._rounds:
            raise ValueError(f'round {round_id} already has a quality ledger')
        tally = RoundTally(streams, calibrations)
        self._rounds[round_id] = tally
        return tally

    def live(self, round_id: str) -> dict[str, StreamTally]:
        try:
            tally = self._rounds[round_id]
        except KeyError as exc:
            raise UnknownRound(round_id) from exc
        return tally.snapshot()
