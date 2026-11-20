import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from station.archive.lookup import ArchiveReader
from station.archive.publication import ArchivePublisher
from station.archive.storage import ArchiveStore, CommitError
from station.capture.session import RoundStateError
from tests.test_open import make_station


class PublicationTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.path = Path(folder) / 'archive.sqlite3'
        self.store = self.enterContext(closing(ArchiveStore(self.path)))
        self.station, procedure = make_station(((10, 'A', 5), (14, 'A', 5)))
        self.round_id = self.station.open_round(procedure, ('A', 'B'), ('AB', None), 'shift')
        self.publisher = ArchivePublisher(self.station, self.store)
        self.station.step(self.round_id)
        self.station.step(self.round_id)

    def record_counts(self):
        with closing(sqlite3.connect(self.path)) as observer:
            return observer.execute('''SELECT (SELECT count(*) FROM reports),
                                              (SELECT count(*) FROM evidence),
                                              (SELECT count(*) FROM receipts)''').fetchone()

    def test_running_seal_rejected_then_stop_report_matches_live(self):
        with self.assertRaises(RoundStateError):
            self.publisher.seal(self.round_id)
        self.assertEqual(self.record_counts(), (0, 0, 0))
        live = self.station.live(self.round_id)
        stopped = self.station.stop(self.round_id)
        self.assertEqual(self.station.stop(self.round_id), stopped)
        published = self.publisher.seal(self.round_id)
        self.assertEqual(published.report.tallies, live)
        self.assertEqual(published.report.tallies['A'].total_micro, 22)
        self.assertEqual(self.record_counts(), (1, 1, 1))

    def test_failed_commit_rolls_back_and_success_replays_original_bundle(self):
        stopped = self.station.stop(self.round_id)
        with closing(sqlite3.connect(self.path)) as control:
            control.executescript('''
                CREATE TABLE allowed_commit (id INTEGER PRIMARY KEY);
                CREATE TABLE commit_guard (
                    id INTEGER REFERENCES allowed_commit(id) DEFERRABLE INITIALLY DEFERRED
                );
                CREATE TRIGGER refuse_commit AFTER INSERT ON receipts
                BEGIN
                    INSERT INTO commit_guard (id) VALUES (NEW.result_id);
                END;
            ''')
        with self.assertRaises(CommitError) as error:
            self.publisher.seal(self.round_id)
        self.assertIsInstance(error.exception.__cause__, sqlite3.IntegrityError)
        self.assertEqual(self.record_counts(), (0, 0, 0))
        self.assertIsNone(self.store.result_for_round(self.round_id))
        self.assertEqual(self.station.stopped_round(self.round_id), stopped)

        with closing(sqlite3.connect(self.path, isolation_level=None)) as control:
            control.execute('DROP TRIGGER refuse_commit')
        published = self.publisher.seal(self.round_id)
        original = self.store.load_result(published.result_id)
        received = ArchiveReader(self.store).read(published.result_id)
        replay = self.publisher.seal(self.round_id)
        self.assertEqual(replay, published)
        self.assertEqual(received.receipt_id, published.receipt_id)
        self.assertEqual(received.evidence.journal, stopped.journal)
        self.assertEqual(self.store.load_result(published.result_id), original)
        self.assertEqual(self.record_counts(), (1, 1, 1))
