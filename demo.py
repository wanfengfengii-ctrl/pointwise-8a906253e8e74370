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
    marker = {
        'case_id': 'v8', 'input': document, 'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
        'code_paths': [
            'station/capture/port.py', 'station/receipt/ingest.py', 'station/quality/tally.py',
            'station/archive/publication.py', 'station/archive/lookup.py', 'station/archive/audit.py',
        ],
        'oracle': '两条 5 按 2x+1 各得 11，总和 22，等上限未超限，较早源序 10 保留极值出处。11 越过设备原码上界 10。C 命不中槽位，B 无源帧。',
    }
    print('POINTWISE_SCENARIO ' + json.dumps(marker, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
