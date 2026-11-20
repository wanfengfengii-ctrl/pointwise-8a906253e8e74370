"""Run a shift and verify an independently hand-calculated archive result."""

import json
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from station.archive.lookup import ArchiveReader
from station.archive.publication import ArchivePublisher
from station.archive.storage import ArchiveStore
from station.capture.recording import Recording, RecordingCatalog, RecordingExhausted
from station.capture.session import CaptureStation
from station.procedure.calibration import CalibrationSpec
from station.procedure.registration import ProcedureRegistry
from switch_fixture import open_case, statuses, supply_group


def _capture_and_seal(document, database):
    procedures = ProcedureRegistry()
    calibrations = {stream: CalibrationSpec(**data) for stream, data in document['calibrations'].items()}
    procedure = procedures.register(document['rates'], document['masks'], calibrations, document['capacity'])
    recordings = RecordingCatalog()
    recordings.register('day', Recording.from_document(document['recording']))
    station = CaptureStation(procedures, recordings)
    round_id = station.open_round(procedure, document['subscription'], document['slots'], 'day')
    statuses = []
    while True:
        try:
            outcome = station.step(round_id)
        except RecordingExhausted:
            break
        statuses.append(outcome.status.value)
    actual = {'statuses': statuses, 'live': {name: asdict(tally) for name, tally in station.live(round_id).items()}}
    station.stop(round_id)
    with closing(ArchiveStore(database)) as store:
        publication = ArchivePublisher(station, store).seal(round_id)
    return actual, publication


def _switch_case():
    """Run the marking fixture: pre-cut A/C, cut at 13, post-cut A/D, then stop and reopen."""
    station, round_id, new_subscription, final_slots = open_case('mark')
    pre = statuses(supply_group(station, round_id, 10))
    observed = []

    def after_action(action):
        observed.append((action.slot, action.mask, action.cutover, action.after_source_seq))
        supply_group(station, round_id, 20)

    record = station.switch_subscription(
        round_id, new_subscription, final_slots, after_action=after_action,
    )
    stopped = station.stop(round_id)
    return station, round_id, pre, observed, record, stopped


def _tally_map(tallies):
    if tallies is None:
        return None
    return {
        name: [tally.valid_count, tally.total_micro, tally.minimum, tally.minimum_seq,
               tally.maximum, tally.maximum_seq, tally.over_limit, tally.bad_count]
        for name, tally in tallies.items()
    }


def _switch_marker():
    station, round_id, pre, observed, record, stopped = _switch_case()
    with TemporaryDirectory(prefix='lanqiao-switch-') as folder:
        database = Path(folder) / 'switch.sqlite3'
        with closing(ArchiveStore(database)) as store:
            publication = ArchivePublisher(station, store).seal(round_id)
        station.procedures.revise(
            stopped.admission.rules.procedure_id,
            rates=dict(stopped.admission.rules.catalog.rates),
            masks={name: list(members) for name, members in stopped.admission.rules.catalog.masks.items()},
            calibrations={name: CalibrationSpec(9, 9, 9) for name in stopped.admission.rules.catalog.rates},
            capacity=1,
        )
        with closing(ArchiveStore(database)) as reopened:
            received = ArchiveReader(reopened).read(publication.result_id)

    first_frames = [
        [item.frame.source_seq, item.frame.stream, item.frame.raw_code, item.verdict.value]
        for item in received.evidence.journal.first_frames
    ]
    expected_firsts = [
        [10, 'A', 5, 'accepted'], [11, 'B', 5, 'filtered'], [12, 'C', 5, 'accepted'],
        [13, 'D', 5, 'filtered'], [20, 'A', 5, 'accepted'], [21, 'B', 5, 'filtered'],
        [22, 'C', 5, 'filtered'], [23, 'D', 5, 'accepted'],
    ]
    expected_before = {
        'A': [1, 11, 11, 10, 11, 10, 0, 0], 'C': [1, 5, 5, 12, 5, 12, 0, 0],
    }
    expected_after = {
        'A': [1, 11, 11, 20, 11, 20, 0, 0], 'D': [1, 5, 5, 23, 5, 23, 0, 0],
    }
    switch = received.evidence.switch
    actual = {
        'statuses': pre,
        'actions': observed,
        'cut_after_source_seq': switch.cut_after_source_seq,
        'final_slots': list(switch.final_slots),
        'subscription': sorted(switch.subscription),
        'before_tallies': _tally_map(received.report.before_tallies),
        'tallies': _tally_map(received.report.tallies),
        'recalculated_before': _tally_map(received.audit.before_tallies),
        'recalculated_after': _tally_map(received.audit.tallies),
        'first_frames': first_frames,
        'same_receipt': received.receipt_id == publication.receipt_id,
        'calibration_A_after_reopen': asdict(received.evidence.admission.rules.calibrations['A']),
    }
    expected = {
        'statuses': ['accepted', 'filtered', 'accepted', 'filtered'],
        'actions': [(None, None, True, 13)],
        'cut_after_source_seq': 13,
        'final_slots': ['AB', 'CD'],
        'subscription': ['A', 'D'],
        'before_tallies': expected_before,
        'tallies': expected_after,
        'recalculated_before': expected_before,
        'recalculated_after': expected_after,
        'first_frames': expected_firsts,
        'same_receipt': True,
        'calibration_A_after_reopen': {'scale': 2, 'offset': 1, 'upper_limit': 11},
    }
    if actual != expected:
        raise RuntimeError(f'switch sample disagrees with the hand calculation: {actual!r}')
    return {
        'case': 'mark', 'pre_statuses': pre, 'actions': [list(item) for item in observed],
        'cut_after_source_seq': switch.cut_after_source_seq,
        'final_slots': list(switch.final_slots),
        'before_tallies': _tally_map(received.report.before_tallies),
        'tallies': _tally_map(received.report.tallies),
        'first_frames': first_frames,
        'recalculated_before': _tally_map(received.audit.before_tallies),
        'recalculated_after': _tally_map(received.audit.tallies),
        'same_receipt': received.receipt_id == publication.receipt_id,
        'oracle': 'AB|CD 两槽均在旧段与切点后才放行；旧订阅 A、C，锚点 13 前 A(10)、C(12) 各 11 与 5；'
                  '新订阅 A、D，锚点后 A(20)、D(23) 各 11 与 5。A 两段各 11 等上限不计超限。',
    }


def main():
    sample = Path(__file__).resolve().parent / 'samples' / 'day.json'
    document = json.loads(sample.read_text(encoding='utf-8'))
    expected_tallies = {
        'A': {'valid_count': 2, 'total_micro': 22, 'minimum': 11, 'minimum_seq': 10,
              'maximum': 11, 'maximum_seq': 10, 'over_limit': 0, 'bad_count': 1},
        'B': {'valid_count': 0, 'total_micro': 0, 'minimum': None, 'minimum_seq': None,
              'maximum': None, 'maximum_seq': None, 'over_limit': 0, 'bad_count': 0},
    }
    expected = {'statuses': ['accepted', 'filtered', 'accepted', 'rejected'], 'live': expected_tallies}
    downstream_expected = {
        'first_frames': [[10, 'A', 5, 'accepted'], [14, 'A', 5, 'accepted'], [15, 'A', 11, 'bad_code']],
        'forwarded': [[10, True], [11, False], [14, True], [15, True]],
        'calibration_A': {'scale': 2, 'offset': 1, 'upper_limit': 11},
        'report': expected_tallies,
        'recalculated': expected_tallies,
        'same_receipt': True,
    }
    with TemporaryDirectory(prefix='lanqiao-') as folder:
        database = Path(folder) / 'shift.sqlite3'
        actual, publication = _capture_and_seal(document, database)
        with closing(ArchiveStore(database)) as reopened:
            received = ArchiveReader(reopened).read(publication.result_id)

    downstream_actual = {
        'first_frames': [
            [item.frame.source_seq, item.frame.stream, item.frame.raw_code, item.verdict.value]
            for item in received.evidence.journal.first_frames
        ],
        'forwarded': [[item.source_seq, item.forwarded] for item in received.evidence.observations],
        'calibration_A': asdict(received.evidence.admission.rules.calibrations['A']),
        'report': {name: asdict(tally) for name, tally in received.report.tallies.items()},
        'recalculated': {name: asdict(tally) for name, tally in received.audit.tallies.items()},
        'same_receipt': received.receipt_id == publication.receipt_id,
    }
    if actual != expected or downstream_actual != downstream_expected:
        raise RuntimeError(f'shift sample disagrees with the hand calculation: {actual!r}, {downstream_actual!r}')
    switch_marker = _switch_marker()
    marker = {
        'case_id': 'v8', 'input': document, 'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
        'switchover': switch_marker,
        'code_paths': [
            'station/capture/port.py', 'station/receipt/ingest.py', 'station/quality/tally.py',
            'station/archive/publication.py', 'station/archive/lookup.py', 'station/archive/audit.py',
            'station/capture/switchover.py', 'station/capture/session.py',
        ],
        'oracle': '两条 5 按 2x+1 各得 11，总和 22，等上限未超限，较早源序 10 保留极值出处。11 越过设备原码上界 10。C 命不中槽位，B 无源帧。',
    }
    print('POINTWISE_SCENARIO ' + json.dumps(marker, ensure_ascii=False, sort_keys=True))
    print('SWITCHOVER_SCENARIO ' + json.dumps(switch_marker, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
