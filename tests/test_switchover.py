import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from station.archive.audit import EvidenceMismatch
from station.archive.lookup import ArchiveReader
from station.archive.publication import ArchivePublisher
from station.archive.storage import ArchiveStore, CommitError
from station.capture.recording import SourceFrame
from station.capture.session import RoundStateError
from station.procedure.admission import AdmissionError
from station.quality.tally import StreamTally
from switch_fixture import open_case, statuses, supply_group


def fmt(tallies):
    return {name: (t.valid_count, t.total_micro, t.minimum, t.minimum_seq,
                   t.maximum, t.maximum_seq, t.over_limit, t.bad_count)
            for name, tally in tallies.items() for t in (tally,)}


class SwitchCaseTests(unittest.TestCase):
    def test_bridge_switches_through_a_single_slot_and_marks_cutover(self):
        station, round_id, new_subscription, final_slots = open_case('bridge')
        self.assertEqual(statuses(supply_group(station, round_id, 10)),
                         ['accepted', 'accepted', 'filtered', 'filtered'])
        actions = []

        def after_action(action):
            actions.append((action.slot, action.mask, action.cutover, action.after_source_seq))
            supply_group(station, round_id, 20 + 10 * len(actions))

        record = station.switch_subscription(round_id, new_subscription, final_slots,
                                             after_action=after_action)
        self.assertIsNotNone(record)
        self.assertEqual(record.final_slots, final_slots)
        self.assertEqual(record.subscription, frozenset(new_subscription))
        self.assertEqual(sum(1 for action in record.actions if action.cutover), 1)
        merged = [a for a in record.actions if a.cutover and a.slot is not None]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].mask, 'D')
        for action in record.actions:
            if action.slot is not None:
                self.assertIn(action.slot, (0, 1))
        self.assertEqual(actions, [(a.slot, a.mask, a.cutover, a.after_source_seq) for a in record.actions])
        self.assertEqual(record.cut_after_source_seq, actions[1][3])

        stopped = station.stop(round_id)
        cut = record.cut_after_source_seq
        firsts = {item.frame.source_seq: item for item in stopped.journal.first_frames}
        # A transitional extra stream is physically forwarded but kept filtered and uncounted.
        self.assertEqual(firsts[32].frame.stream, 'C')
        self.assertEqual(firsts[32].verdict.value, 'filtered')
        self.assertLessEqual(firsts[32].frame.source_seq, cut)
        self.assertEqual(set(stopped.tallies), set('CD'))
        self.assertEqual(set(stopped.before_tallies), set('AB'))
        for frame in firsts.values():
            if frame.frame.stream in stopped.tallies and frame.frame.source_seq > cut:
                self.assertEqual(frame.verdict.value, 'accepted')

    def test_blocked_has_no_legal_path_and_old_capture_continues(self):
        station, round_id, new_subscription, final_slots = open_case('blocked')
        self.assertEqual(statuses(supply_group(station, round_id, 10)),
                         ['accepted', 'accepted', 'filtered', 'filtered'])
        called = []
        self.assertIsNone(station.switch_subscription(
            round_id, new_subscription, final_slots, after_action=called.append))
        self.assertEqual(called, [])
        self.assertEqual(statuses(supply_group(station, round_id, 20)),
                         ['accepted', 'accepted', 'filtered', 'filtered'])
        live = station.live(round_id)
        self.assertEqual(set(live), set('AB'))
        self.assertEqual(live['A'], StreamTally(valid_count=2, total_micro=22, minimum=11,
                                                minimum_seq=10, maximum=11, maximum_seq=10))
        stopped = station.stop(round_id)
        self.assertIsNone(stopped.before_tallies)
        self.assertIsNone(stopped.switch)

    def test_mark_marks_only_the_cutover_and_counts_each_segment(self):
        station, round_id, new_subscription, final_slots = open_case('mark')
        self.assertEqual(statuses(supply_group(station, round_id, 10)),
                         ['accepted', 'filtered', 'accepted', 'filtered'])
        seen = []

        def after_action(action):
            seen.append(action)
            supply_group(station, round_id, 20)

        record = station.switch_subscription(round_id, new_subscription, final_slots,
                                             after_action=after_action)
        self.assertEqual([(a.slot, a.mask, a.cutover) for a in record.actions],
                         [(None, None, True)])
        self.assertEqual(record.cut_after_source_seq, 13)
        self.assertEqual(record.final_slots, ('AB', 'CD'))
        self.assertEqual(len(seen), 1)
        stopped = station.stop(round_id)
        self.assertEqual(fmt(stopped.before_tallies), {
            'A': (1, 11, 11, 10, 11, 10, 0, 0),
            'C': (1, 5, 5, 12, 5, 12, 0, 0),
        })
        self.assertEqual(fmt(stopped.tallies), {
            'A': (1, 11, 11, 20, 11, 20, 0, 0),
            'D': (1, 5, 5, 23, 5, 23, 0, 0),
        })
        self.assertEqual(
            [(item.frame.source_seq, item.frame.stream, item.frame.raw_code, item.verdict.value)
             for item in stopped.journal.first_frames],
            [(10, 'A', 5, 'accepted'), (11, 'B', 5, 'filtered'), (12, 'C', 5, 'accepted'),
             (13, 'D', 5, 'filtered'), (20, 'A', 5, 'accepted'), (21, 'B', 5, 'filtered'),
             (22, 'C', 5, 'filtered'), (23, 'D', 5, 'accepted')],
        )

    def test_either_accepts_whatever_legal_path_is_taken(self):
        station, round_id, new_subscription, final_slots = open_case('either')
        supply_group(station, round_id, 10)
        actions = []

        def after_action(action):
            actions.append(action)
            supply_group(station, round_id, 20 + 10 * len(actions))

        record = station.switch_subscription(round_id, new_subscription, final_slots,
                                             after_action=after_action)
        self.assertIsNotNone(record)
        stopped = station.stop(round_id)
        cut = record.cut_after_source_seq
        self.assertEqual(cut, record.actions[-1].after_source_seq)
        firsts = stopped.journal.first_frames
        for item in firsts:
            eligibility = new_subscription if item.frame.source_seq > cut else frozenset('AB')
            if item.frame.stream not in eligibility:
                self.assertEqual(item.verdict.value, 'filtered')
        self.assertEqual(set(stopped.before_tallies), set('AB'))
        self.assertEqual(set(stopped.tallies), set('CD'))


class SwitchLifecycleTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.path = Path(folder) / 'archive.sqlite3'

    def _seal(self, station, round_id):
        with closing(ArchiveStore(self.path)) as store:
            return ArchivePublisher(station, store).seal(round_id)

    def test_terminal_validation_and_single_success(self):
        station, round_id, new_subscription, _ = open_case('mark')
        with self.assertRaises(AdmissionError):
            station.switch_subscription(round_id, ('A', 'missing'), ('AB', 'CD'))
        with self.assertRaises(AdmissionError):
            station.switch_subscription(round_id, ('A', 'D'), ('AB',))
        with self.assertRaises(AdmissionError):
            station.switch_subscription(round_id, ('A', 'D'), ('AB', 'AB'))
        supply_group(station, round_id, 10)
        record = station.switch_subscription(round_id, new_subscription, ('AB', 'CD'))
        self.assertIsNotNone(record)
        before = station.observations(round_id)
        with self.assertRaises(RoundStateError):
            station.switch_subscription(round_id, new_subscription, ('AB', 'CD'))
        self.assertEqual(station.observations(round_id), before)

    def test_round_trip_seals_both_segments_and_evidence(self):
        station, round_id, new_subscription, final_slots = open_case('mark')
        supply_group(station, round_id, 10)
        record = station.switch_subscription(
            round_id, new_subscription, final_slots,
            after_action=lambda action: supply_group(station, round_id, 20),
        )
        stopped = station.stop(round_id)
        published = self._seal(station, round_id)
        with closing(ArchiveStore(self.path)) as store:
            received = ArchiveReader(store).read(published.result_id)
            replayed = ArchivePublisher(station, store).seal(round_id)
        self.assertEqual(replayed, published)
        self.assertEqual(received.receipt_id, published.receipt_id)
        self.assertEqual(received.report.tallies, stopped.tallies)
        self.assertEqual(received.report.before_tallies, stopped.before_tallies)
        self.assertEqual(received.audit.tallies, stopped.tallies)
        self.assertEqual(received.audit.before_tallies, stopped.before_tallies)
        switch = received.evidence.switch
        self.assertEqual(switch.subscription, frozenset(new_subscription))
        self.assertEqual(switch.final_slots, final_slots)
        self.assertEqual(switch.cut_after_source_seq, record.cut_after_source_seq)
        self.assertEqual(
            [(a.slot, a.mask, a.cutover, a.after_source_seq) for a in switch.actions],
            [(a.slot, a.mask, a.cutover, a.after_source_seq) for a in record.actions],
        )
        self.assertEqual(received.evidence.admission.subscription, frozenset('AC'))

    def test_failed_commit_leaves_no_result_and_resumes_from_stopped_round(self):
        station, round_id, new_subscription, final_slots = open_case('bridge')
        supply_group(station, round_id, 10)
        actions = []

        def after_action(action):
            actions.append(action)
            supply_group(station, round_id, 20 + 10 * len(actions))

        station.switch_subscription(round_id, new_subscription, final_slots, after_action=after_action)
        stopped = station.stop(round_id)
        ArchiveStore(self.path).close()
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
        publisher = ArchivePublisher(station, store=ArchiveStore(self.path))
        self.addCleanup(publisher._store.close)
        with self.assertRaises(CommitError):
            publisher.seal(round_id)
        with closing(sqlite3.connect(self.path)) as observer:
            self.assertEqual(observer.execute(
                'SELECT (SELECT count(*) FROM reports), (SELECT count(*) FROM evidence), '
                '(SELECT count(*) FROM receipts)').fetchone(), (0, 0, 0))
        self.assertEqual(station.stopped_round(round_id), stopped)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as control:
            control.execute('DROP TRIGGER refuse_commit')
        published = publisher.seal(round_id)
        self.assertEqual(publisher.seal(round_id), published)
        with closing(ArchiveStore(self.path)) as reopened:
            received = ArchiveReader(reopened).read(published.result_id)
        self.assertEqual(received.evidence.switch.cut_after_source_seq,
                         stopped.switch.cut_after_source_seq)
        self.assertEqual(received.report.before_tallies, stopped.before_tallies)
        self.assertEqual(received.report.tallies, stopped.tallies)

    def test_tampered_switch_evidence_is_detected(self):
        station, round_id, new_subscription, final_slots = open_case('mark')
        supply_group(station, round_id, 10)
        station.switch_subscription(
            round_id, new_subscription, final_slots,
            after_action=lambda action: supply_group(station, round_id, 20),
        )
        station.stop(round_id)
        published = self._seal(station, round_id)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            payload, = db.execute('SELECT payload FROM evidence WHERE result_id = ?',
                                  (published.result_id,)).fetchone()
            document = json.loads(payload)
            document['switch']['cut_after_source_seq'] = '0x14'
            db.execute('UPDATE evidence SET payload = ? WHERE result_id = ?',
                       (json.dumps(document), published.result_id))
        with closing(ArchiveStore(self.path)) as store:
            with self.assertRaisesRegex(EvidenceMismatch, 'cut anchor'):
                ArchiveReader(store).read(published.result_id)

    def _tamper(self, result_id, table, mutate):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            payload, = db.execute(f'SELECT payload FROM {table} WHERE result_id = ?',
                                  (result_id,)).fetchone()
            document = json.loads(payload)
            mutate(document)
            db.execute(f'UPDATE {table} SET payload = ? WHERE result_id = ?',
                       (json.dumps(document), result_id))

    def test_switch_audit_enforces_one_slot_and_one_cutover(self):
        station, round_id, new_subscription, final_slots = open_case('bridge')
        supply_group(station, round_id, 10)
        actions = []

        def after_action(action):
            actions.append(action)
            supply_group(station, round_id, 20 + 10 * len(actions))

        station.switch_subscription(round_id, new_subscription, final_slots,
                                    after_action=after_action)
        station.stop(round_id)
        published = self._seal(station, round_id)
        merged = next(i for i, a in enumerate(actions) if a.cutover and a.slot is not None)
        original = Path(self.path).read_bytes()

        def two_cuts(document):
            document['switch']['actions'].append({
                'slot': None, 'mask': None, 'cutover': True,
                'after_source_seq': document['switch']['actions'][-1]['after_source_seq'],
            })

        def merged_anchor(document):
            document['switch']['actions'][merged]['after_source_seq'] = '0x0'

        for mutate, message in (
            (two_cuts, 'more than one cutover'),
            (merged_anchor, 'physically observed frame'),
        ):
            with self.subTest(message=message):
                with TemporaryDirectory() as folder:
                    self.path = Path(folder) / 'archive.sqlite3'
                    self.path.write_bytes(original)
                    self._tamper(published.result_id, 'evidence', mutate)
                    with closing(ArchiveStore(self.path)) as store:
                        with self.assertRaisesRegex(EvidenceMismatch, message):
                            ArchiveReader(store).read(published.result_id)

    def test_missing_old_tallies_in_switch_report_is_detected(self):
        station, round_id, new_subscription, final_slots = open_case('mark')
        supply_group(station, round_id, 10)
        station.switch_subscription(
            round_id, new_subscription, final_slots,
            after_action=lambda action: supply_group(station, round_id, 20),
        )
        station.stop(round_id)
        published = self._seal(station, round_id)
        self._tamper(published.result_id, 'reports', lambda document: document.pop('before_tallies'))
        with closing(ArchiveStore(self.path)) as store:
            with self.assertRaisesRegex(EvidenceMismatch, 'missing the old-segment'):
                ArchiveReader(store).read(published.result_id)


if __name__ == '__main__':
    unittest.main()
