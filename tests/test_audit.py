import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from station.archive.audit import EvidenceMismatch
from station.archive.lookup import ArchiveReader, UnknownResult
from station.archive.publication import ArchivePublisher
from station.archive.storage import ArchiveStore
from station.capture.recording import RecordingCatalog, SourceFrame
from station.capture.session import CaptureStation
from station.procedure.calibration import CalibrationSpec
from station.procedure.registration import ProcedureRegistry
from station.quality.tally import StreamTally
from tests.test_open import make_station, rule_inputs


class AuditTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.path = Path(folder) / 'archive.sqlite3'

    def publish_sample(self):
        rows = ((10, 'A', 5), (11, 'C', 9), (14, 'A', 5), (15, 'A', 11))
        station, procedure = make_station(rows, capacity=2)
        round_id = station.open_round(procedure, ('A', 'B'), ('AB', 'A'), 'shift')
        for _ in rows:
            station.step(round_id)
        station.redeliver(round_id, SourceFrame(10, 'A', 6))
        station.redeliver(round_id, SourceFrame(12, 'A', 5))
        station.stop(round_id)
        with closing(ArchiveStore(self.path)) as store:
            result = ArchivePublisher(station, store).seal(round_id)
            station.procedures.revise(procedure, **rule_inputs(a=(3, 7, 20)))
            return result

    def test_read_uses_original_codes_and_frozen_nonzero_offset(self):
        published = self.publish_sample()
        with closing(ArchiveStore(self.path)) as store:
            received = ArchiveReader(store).read(published.result_id)
        self.assertEqual(received.report, published.report)
        self.assertEqual(received.receipt_id, published.receipt_id)
        admission = received.evidence.admission
        self.assertEqual(admission.rules.calibrations['A'], CalibrationSpec(2, 1, 11))
        self.assertEqual(admission.subscription, frozenset('AB'))
        self.assertEqual((received.evidence.raw_min, received.evidence.raw_max), (-10, 10))
        self.assertEqual(
            [(item.frame.source_seq, item.frame.stream, item.frame.raw_code, item.verdict.value)
             for item in received.evidence.journal.first_frames],
            [(10, 'A', 5, 'accepted'), (14, 'A', 5, 'accepted'), (15, 'A', 11, 'bad_code')],
        )
        self.assertEqual([item.kind.value for item in received.evidence.journal.rejections],
                         ['bad_code', 'conflict', 'out_of_order'])
        expected = StreamTally(
            valid_count=2, total_micro=22, minimum=11, minimum_seq=10,
            maximum=11, maximum_seq=10, over_limit=0, bad_count=1,
        )
        self.assertEqual(received.report.tallies, {'A': expected, 'B': StreamTally()})
        self.assertEqual(received.audit.tallies, {'A': expected, 'B': StreamTally()})

    def test_repeated_initialization_keeps_result_evidence_and_receipt(self):
        published = self.publish_sample()
        with closing(ArchiveStore(self.path)) as store:
            original = store.load_result(published.result_id)
            store.initialize()
            store.initialize()
            self.assertEqual(store.load_result(published.result_id), original)
        with closing(ArchiveStore(self.path)) as store:
            empty_station = CaptureStation(ProcedureRegistry(), RecordingCatalog())
            replay = ArchivePublisher(empty_station, store).seal(published.report.round_id)
            received = ArchiveReader(store).read(published.result_id)
            self.assertEqual(store.load_result(published.result_id), original)
        self.assertEqual(replay, published)
        self.assertEqual(received.receipt_id, published.receipt_id)
        self.assertEqual(received.audit.tallies['A'].total_micro, 22)

    def test_modified_raw_is_preserved_by_initialization_and_detected_by_audit(self):
        published = self.publish_sample()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            payload, = db.execute('SELECT payload FROM evidence WHERE result_id = ?', (published.result_id,)).fetchone()
            document = json.loads(payload)
            document['first_frames'][1]['frame']['raw_code'] = '0x6'
            modified = json.dumps(document)
            db.execute('UPDATE evidence SET payload = ? WHERE result_id = ?', (modified, published.result_id))
        with closing(ArchiveStore(self.path)) as store:
            store.initialize()
            self.assertEqual(store.load_result(published.result_id).evidence_payload, modified)
            reader = ArchiveReader(store)
            with self.assertRaisesRegex(EvidenceMismatch, 'quality totals disagree'):
                reader.read(published.result_id)
            with self.assertRaises(UnknownResult):
                reader.read(published.result_id + 10_000)

    def test_missing_receipt_is_distinguished_from_unknown_result(self):
        published = self.publish_sample()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute('DELETE FROM receipts WHERE result_id = ?', (published.result_id,))
        with closing(ArchiveStore(self.path)) as store:
            reader = ArchiveReader(store)
            with self.assertRaisesRegex(EvidenceMismatch, 'incomplete publication'):
                reader.read(published.result_id)
            with self.assertRaises(UnknownResult):
                reader.read(published.result_id + 1)

    def test_archive_preserves_large_signed_integers(self):
        huge = 10 ** 4400
        station, procedure = make_station(((10, 'A', -huge),), raw_bounds=(-huge, huge), a=(2, 1, 0))
        round_id = station.open_round(procedure, ('A',), ('A', None), 'shift')
        station.step(round_id)
        station.stop(round_id)
        with closing(ArchiveStore(self.path)) as store:
            published = ArchivePublisher(station, store).seal(round_id)
        with closing(ArchiveStore(self.path)) as store:
            received = ArchiveReader(store).read(published.result_id)
        self.assertTrue(received.evidence.journal.first_frames[0].frame.raw_code == -huge)
        self.assertTrue(received.audit.tallies['A'].total_micro == 1 - 2 * huge)
        self.assertEqual(received.audit.tallies['A'].minimum_seq, 10)
