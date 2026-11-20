"""Fixed quality reports with lossless persistence of business integers."""

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from types import MappingProxyType

from station.quality.tally import StreamTally


@dataclass(frozen=True, slots=True)
class RoundReport:
    round_id: str
    tallies: Mapping[str, StreamTally]
    before_tallies: Mapping[str, StreamTally] | None = None


def build_report(
    round_id: str, tallies: Mapping[str, StreamTally],
    before_tallies: Mapping[str, StreamTally] | None = None,
) -> RoundReport:
    frozen_before = None if before_tallies is None else MappingProxyType(dict(before_tallies))
    return RoundReport(round_id, MappingProxyType(dict(tallies)), frozen_before)


def encode_integer(value: int) -> str:
    return hex(value)


def decode_integer(encoded: object) -> int:
    if type(encoded) is not str:
        raise ValueError('stored integer must be hexadecimal text')
    value = int(encoded, 16)
    if hex(value) != encoded:
        raise ValueError('stored integer is not in canonical hexadecimal form')
    return value


def _encode_tallies(tallies: Mapping[str, StreamTally]) -> dict:
    return {
        stream: {
            metric: None if value is None else encode_integer(value)
            for metric, value in asdict(tally).items()
        }
        for stream, tally in tallies.items()
    }


def encode_report(report: RoundReport) -> str:
    data: dict = {'round_id': report.round_id, 'tallies': _encode_tallies(report.tallies)}
    if report.before_tallies is not None:
        data['before_tallies'] = _encode_tallies(report.before_tallies)
    return json.dumps(data, sort_keys=True)


def _decode_tally(data: dict) -> StreamTally:
    optional = {'minimum', 'minimum_seq', 'maximum', 'maximum_seq'}
    expected = {field.name for field in fields(StreamTally)}
    if not isinstance(data, dict) or data.keys() != expected:
        raise ValueError('stored tally has missing or unexpected metrics')
    return StreamTally(**{
        metric: None if value is None and metric in optional else decode_integer(value)
        for metric, value in data.items()
    })


def decode_report(payload: str) -> RoundReport:
    data = json.loads(payload)
    round_id = data['round_id']
    if not isinstance(round_id, str) or not round_id:
        raise ValueError('stored report requires a round id')
    tallies = {stream: _decode_tally(tally) for stream, tally in data['tallies'].items()}
    before = data.get('before_tallies')
    if before is None:
        return build_report(round_id, tallies)
    if not isinstance(before, dict):
        raise ValueError('stored before-segment tallies must be an object')
    return build_report(
        round_id, tallies, {stream: _decode_tally(tally) for stream, tally in before.items()},
    )
