import unittest

from station.capture.delivery import DeliveryStatus
from station.capture.recording import SourceFrame
from station.quality.tally import StreamTally
from station.receipt.frame import FrameVerdict
from station.receipt.rejections import RejectionKind
from tests.test_open import make_station


class ReceiptTests(unittest.TestCase):
    def test_first_frame_survives_identical_and_conflicting_redeliveries(self):
        station, procedure = make_station(((10, 'A', 5),))
        round_id = station.open_round(procedure, ('A',), ('A', None), 'shift')
        self.assertTrue(station.step(round_id).receipts[0].is_new)
        replay = station.redeliver(round_id, SourceFrame(10, 'A', 5))
        conflict = station.redeliver(round_id, SourceFrame(10, 'A', 6))
        self.assertEqual(replay.verdict, FrameVerdict.ACCEPTED)
        self.assertFalse(replay.is_new)
        self.assertEqual(conflict.rejection, RejectionKind.CONFLICT)
        self.assertFalse(conflict.is_new)
        journal = station.receipts(round_id)
        self.assertEqual([item.frame.raw_code for item in journal.first_frames], [5])
        self.assertEqual(len(journal.rejections), 1)
        self.assertEqual(journal.rejections[0].first, SourceFrame(10, 'A', 5))
        self.assertEqual(journal.rejections[0].incoming, SourceFrame(10, 'A', 6))
        self.assertEqual(station.live(round_id)['A'].valid_count, 1)
        self.assertEqual(station.live(round_id)['A'].total_micro, 11)

    def test_old_replay_and_unseen_out_of_order_frame(self):
        station, procedure = make_station(((10, 'A', 5), (14, 'A', 6)))
        round_id = station.open_round(procedure, ('A',), ('A', None), 'shift')
        station.step(round_id)
        station.step(round_id)
        replay = station.redeliver(round_id, SourceFrame(10, 'A', 5))
        late = station.redeliver(round_id, SourceFrame(12, 'A', 6))
        self.assertEqual(replay.verdict, FrameVerdict.ACCEPTED)
        self.assertFalse(replay.is_new)
        self.assertEqual(late.rejection, RejectionKind.OUT_OF_ORDER)
        journal = station.receipts(round_id)
        self.assertEqual([item.frame.source_seq for item in journal.first_frames], [10, 14])
        self.assertEqual(journal.rejections[0].incoming.source_seq, 12)
        self.assertEqual(journal.rejections[0].after_first_seq, 14)
        self.assertEqual(station.live(round_id)['A'].total_micro, 24)

    def test_earliest_peak_refers_to_kept_first_frame(self):
        station, procedure = make_station(((10, 'A', 5), (14, 'A', 5)))
        round_id = station.open_round(procedure, ('A',), ('A', None), 'shift')
        station.step(round_id)
        station.step(round_id)
        rejection = station.redeliver(round_id, SourceFrame(10, 'A', 6))
        self.assertEqual(rejection.rejection, RejectionKind.CONFLICT)
        stopped = station.stop(round_id)
        tally = stopped.tallies['A']
        self.assertEqual((tally.maximum, tally.maximum_seq, tally.total_micro), (11, 10, 22))
        first = {item.frame.source_seq: item.frame for item in stopped.journal.first_frames}
        self.assertEqual(first[tally.maximum_seq], SourceFrame(10, 'A', 5))

    def test_only_bad_frame_keeps_effective_tally_empty(self):
        station, procedure = make_station(((10, 'A', 11),))
        round_id = station.open_round(procedure, ('A',), ('A', None), 'shift')
        outcome = station.step(round_id)
        self.assertEqual(outcome.status, DeliveryStatus.REJECTED)
        self.assertTrue(outcome.receipts[0].is_new)
        again = station.redeliver(round_id, SourceFrame(10, 'A', 11))
        self.assertEqual(again.rejection, RejectionKind.BAD_CODE)
        self.assertFalse(again.is_new)
        self.assertEqual(station.live(round_id)['A'], StreamTally(bad_count=1))
        journal = station.receipts(round_id)
        self.assertEqual(journal.first_frames[0].frame.raw_code, 11)
        self.assertEqual(journal.first_frames[0].verdict, FrameVerdict.BAD_CODE)
        self.assertEqual(len(journal.rejections), 1)

    def test_valid_at_limit_valid_over_limit_and_bad_are_distinct(self):
        station, procedure = make_station(((10, 'A', 5), (14, 'A', 6), (15, 'A', 11)))
        round_id = station.open_round(procedure, ('A',), ('A', None), 'shift')
        self.assertEqual([station.step(round_id).status for _ in range(3)], ['accepted', 'accepted', 'rejected'])
        self.assertEqual(station.live(round_id)['A'], StreamTally(
            valid_count=2, total_micro=24, minimum=11, minimum_seq=10,
            maximum=13, maximum_seq=14, over_limit=1, bad_count=1,
        ))
