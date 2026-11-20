"""Freeze and serialize the independent ingredients of an offline audit."""

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from station.capture.filter_table import resolve_fixed_table
from station.capture.port import PhysicalObservation
from station.capture.recording import SourceFrame
from station.capture.switchover import SwitchAction, SwitchRecord
from station.procedure.admission import FixedAdmission
from station.procedure.calibration import CalibrationSpec, validate_calibration
from station.procedure.catalog import validate_catalog
from station.procedure.snapshot import FrozenRules
from station.quality.report import decode_integer, encode_integer
from station.receipt.frame import FrameVerdict, ReceivedFrame
from station.receipt.journal import JournalSnapshot
from station.receipt.rejections import Rejection, RejectionKind

if TYPE_CHECKING:
    from station.capture.session import StoppedRound


@dataclass(frozen=True, slots=True)
class RoundEvidence:
    round_id: str
    recording_id: str
    admission: FixedAdmission
    raw_min: int
    raw_max: int
    observations: tuple[PhysicalObservation, ...]
    journal: JournalSnapshot
    switch: SwitchRecord | None = None


def freeze_evidence(stopped: 'StoppedRound') -> RoundEvidence:
    return RoundEvidence(
        stopped.round_id, stopped.recording_id, stopped.admission,
        stopped.raw_min, stopped.raw_max, stopped.observations, stopped.journal, stopped.switch,
    )


def _encode_frame(frame: SourceFrame) -> dict:
    return {
        'source_seq': encode_integer(frame.source_seq),
        'stream': frame.stream,
        'raw_code': encode_integer(frame.raw_code),
    }


def _decode_frame(data: dict) -> SourceFrame:
    return SourceFrame(decode_integer(data['source_seq']), data['stream'], decode_integer(data['raw_code']))


def _encode_rules(rules: FrozenRules) -> dict:
    calibrations = {
        stream: {
            'scale': encode_integer(spec.scale),
            'offset': encode_integer(spec.offset),
            'upper_limit': encode_integer(spec.upper_limit),
        }
        for stream, spec in rules.calibrations.items()
    }
    return {
        'procedure_id': rules.procedure_id,
        'capacity': encode_integer(rules.capacity),
        'rates': {stream: encode_integer(rate) for stream, rate in rules.catalog.rates.items()},
        'masks': {name: sorted(members) for name, members in rules.catalog.masks.items()},
        'calibrations': calibrations,
    }


def _required_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('stored evidence requires nonempty names')
    return value


def _decode_rules(data: dict) -> FrozenRules:
    catalog = validate_catalog(
        {stream: decode_integer(rate) for stream, rate in data['rates'].items()}, data['masks'],
    )
    calibrations = {}
    for stream, item in data['calibrations'].items():
        parsed = CalibrationSpec(
            decode_integer(item['scale']), decode_integer(item['offset']), decode_integer(item['upper_limit']),
        )
        calibrations[stream] = validate_calibration(parsed)
    capacity = decode_integer(data['capacity'])
    if capacity <= 0 or calibrations.keys() != catalog.rates.keys():
        raise ValueError('stored procedure is incomplete')
    return FrozenRules(_required_name(data['procedure_id']), catalog, MappingProxyType(calibrations), capacity)


def _encode_observation(observation: PhysicalObservation) -> dict:
    return {
        'source_seq': encode_integer(observation.source_seq),
        'stream': observation.stream,
        'forwarded': observation.forwarded,
    }


def _decode_observation(data: dict) -> PhysicalObservation:
    forwarded = data['forwarded']
    if type(forwarded) is not bool:
        raise ValueError('physical forwarding flag must be boolean')
    return PhysicalObservation(decode_integer(data['source_seq']), _required_name(data['stream']), forwarded)


def _encode_rejection(rejection: Rejection) -> dict:
    return {
        'kind': rejection.kind.value,
        'incoming': _encode_frame(rejection.incoming),
        'first': None if rejection.first is None else _encode_frame(rejection.first),
        'after_first_seq': encode_integer(rejection.after_first_seq),
    }


def _decode_rejection(data: dict) -> Rejection:
    first = data['first']
    return Rejection(
        RejectionKind(data['kind']), _decode_frame(data['incoming']),
        None if first is None else _decode_frame(first), decode_integer(data['after_first_seq']),
    )


def _encode_action(action: SwitchAction) -> dict:
    return {
        'slot': None if action.slot is None else encode_integer(action.slot),
        'mask': action.mask,
        'cutover': action.cutover,
        'after_source_seq': None if action.after_source_seq is None else encode_integer(action.after_source_seq),
    }


def _decode_action(data: dict) -> SwitchAction:
    slot = data['slot']
    seq = data['after_source_seq']
    cutover = data['cutover']
    mask = data['mask']
    if type(cutover) is not bool:
        raise ValueError('stored switch action needs a boolean cutover flag')
    if mask is not None and (not isinstance(mask, str) or not mask.strip()):
        raise ValueError('stored switch action mask must be a name or null')
    parsed_slot = None if slot is None else decode_integer(slot)
    if parsed_slot not in (None, 0, 1):
        raise ValueError('stored switch action slot must be 0, 1, or null')
    return SwitchAction(
        parsed_slot, mask, cutover, None if seq is None else decode_integer(seq),
    )


def _encode_switch(switch: SwitchRecord) -> dict:
    return {
        'subscription': sorted(switch.subscription),
        'final_slots': list(switch.final_slots),
        'cut_after_source_seq': (
            None if switch.cut_after_source_seq is None else encode_integer(switch.cut_after_source_seq)
        ),
        'actions': [_encode_action(item) for item in switch.actions],
    }


def _decode_switch(data: dict) -> SwitchRecord:
    streams = [_required_name(name) for name in _items(data['subscription'])]
    slots = _items(data['final_slots'])
    if len(slots) != 2:
        raise ValueError('stored final slot table requires two positions')
    if any(name is not None and (not isinstance(name, str) or not name.strip()) for name in slots):
        raise ValueError('stored final slot names must be names or null')
    cut = data['cut_after_source_seq']
    return SwitchRecord(
        frozenset(streams), (slots[0], slots[1]),
        None if cut is None else decode_integer(cut),
        tuple(_decode_action(item) for item in _items(data['actions'])),
    )


def encode_evidence(evidence: RoundEvidence) -> str:
    admission = evidence.admission
    data = {
        'round_id': evidence.round_id,
        'recording_id': evidence.recording_id,
        'rules': _encode_rules(admission.rules),
        'subscription': sorted(admission.subscription),
        'slots': admission.table.slots,
        'reserved_rate': encode_integer(admission.reserved_rate),
        'raw_min': encode_integer(evidence.raw_min),
        'raw_max': encode_integer(evidence.raw_max),
        'observations': [_encode_observation(item) for item in evidence.observations],
        'first_frames': [
            {'frame': _encode_frame(item.frame), 'verdict': item.verdict.value}
            for item in evidence.journal.first_frames
        ],
        'rejections': [_encode_rejection(item) for item in evidence.journal.rejections],
        'switch': None if evidence.switch is None else _encode_switch(evidence.switch),
    }
    return json.dumps(data, sort_keys=True)


def _items(value: object) -> list:
    if not isinstance(value, list):
        raise ValueError('stored evidence sequence must be a list')
    return value


def decode_evidence(payload: str) -> RoundEvidence:
    data = json.loads(payload)
    rules = _decode_rules(data['rules'])
    streams = [_required_name(name) for name in _items(data['subscription'])]
    subscription = frozenset(streams)
    if len(streams) != len(subscription):
        raise ValueError('stored subscription contains duplicates')
    table = resolve_fixed_table(rules.catalog, _items(data['slots']))
    admission = FixedAdmission(rules, subscription, table, decode_integer(data['reserved_rate']))
    first_frames = tuple(
        ReceivedFrame(_decode_frame(item['frame']), FrameVerdict(item['verdict']))
        for item in _items(data['first_frames'])
    )
    journal = JournalSnapshot(first_frames, tuple(_decode_rejection(item) for item in _items(data['rejections'])))
    switch_data = data.get('switch')
    return RoundEvidence(
        _required_name(data['round_id']), _required_name(data['recording_id']), admission,
        decode_integer(data['raw_min']), decode_integer(data['raw_max']),
        tuple(_decode_observation(item) for item in _items(data['observations'])), journal,
        None if switch_data is None else _decode_switch(switch_data),
    )
