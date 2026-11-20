import unittest

from station.capture.delivery import DeliveryStatus
from station.capture.port import PhysicalObservation, ReplayPort
from station.capture.recording import Recording, RecordingExhausted, SourceFrame
from station.capture.session import RoundStateError, UnknownRound
from station.receipt.frame import FrameVerdict
from tests.test_open import make_station


class CaptureTests(unittest.TestCase):
    def test_overlap_and_source_gap(self):
        rows = ((10, 'A', 5), (11, 'C', 9), (14, 'A', 5))
        station, procedure = make_station(rows)
        round_id = station.open_round(procedure, ('A',), ('AB', 'A'), 'shift')
        outcomes = [station.step(round_id) for _ in rows]
        self.assertEqual([item.status for item in outcomes], ['accepted', 'filtered', 'accepted'])
        self.assertEqual([len(item.receipts) for item in outcomes], [1, 0, 1])
        self.assertEqual(station.observations(round_id), (
            PhysicalObservation(10, 'A', True),
            PhysicalObservation(11, 'C', False),
            PhysicalObservation(14, 'A', True),
        ))
        self.assertEqual([item.frame.source_seq for item in station.receipts(round_id).first_frames], [10, 14])
        self.assertEqual(station.recording_position(round_id), 3)
        self.assertEqual(station.live(round_id)['A'].valid_count, 2)

    def test_overlap_at_capacity_delivers_once(self):
        station, procedure = make_station(((10, 'A', 5),), capacity=3)
        round_id = station.open_round(procedure, ('A', 'B'), ('AB', 'AC'), 'shift')
        outcome = station.step(round_id)
        self.assertEqual(outcome.status, DeliveryStatus.ACCEPTED)
        self.assertEqual(outcome.observation, PhysicalObservation(10, 'A', True))
        self.assertEqual(len(outcome.receipts), 1)
        self.assertTrue(outcome.receipts[0].is_new)
        self.assertEqual(station.live(round_id)['A'].valid_count, 1)
        self.assertEqual(len(station.receipts(round_id).first_frames), 1)
        stopped = station.stop(round_id)
        port = ReplayPort()
        frame = SourceFrame(10, 'A', 5)
        port.start(Recording(-10, 10, (frame,)), stopped.admission.table)
        self.assertEqual(port.advance().deliveries, (frame,))

    def test_physically_forwarded_extra_stream_is_filtered_at_receipt(self):
        station, procedure = make_station(((10, 'A', 5), (11, 'C', 99)))
        round_id = station.open_round(procedure, ('A',), ('AC', None), 'shift')
        station.step(round_id)
        before = station.live(round_id)
        extra = station.step(round_id)
        self.assertEqual(extra.status, DeliveryStatus.FILTERED)
        self.assertEqual(extra.observation, PhysicalObservation(11, 'C', True))
        self.assertEqual(len(extra.receipts), 1)
        self.assertEqual(extra.receipts[0].verdict, FrameVerdict.FILTERED)
        journal = station.receipts(round_id)
        self.assertEqual(journal.first_frames[-1].frame, SourceFrame(11, 'C', 99))
        self.assertEqual(journal.rejections, ())
        self.assertEqual(station.live(round_id), before)

    def test_step_guards_preserve_position_and_ledger(self):
        station, procedure = make_station(((10, 'A', 5),))
        with self.assertRaises(UnknownRound):
            station.step('missing')
        round_id = station.open_round(procedure, ('A',), ('A', None), 'shift')
        station.step(round_id)
        ledger = station.live(round_id)
        observations = station.observations(round_id)
        receipts = station.receipts(round_id)
        with self.assertRaises(RecordingExhausted):
            station.step(round_id)
        station.stop(round_id)
        with self.assertRaises(RoundStateError):
            station.step(round_id)
        with self.assertRaises(RoundStateError):
            station.inject_source(round_id, SourceFrame(12, 'A', 5))
        with self.assertRaises(RoundStateError):
            station.redeliver(round_id, SourceFrame(10, 'A', 5))
        self.assertEqual(station.recording_position(round_id), 1)
        self.assertEqual(station.observations(round_id), observations)
        self.assertEqual(station.receipts(round_id), receipts)
        self.assertEqual(station.live(round_id), ledger)

    def test_serial_source_injection_preserves_recorded_gaps(self):
        station, procedure = make_station(((10, 'A', 5), (14, 'A', 5)))
        round_id = station.open_round(procedure, ('A',), ('A', None), 'shift')
        self.assertEqual(station.step(round_id).status, DeliveryStatus.ACCEPTED)
        with self.assertRaises(ValueError):
            station.inject_source(round_id, SourceFrame(14, 'A', 5))
        with self.assertRaises(ValueError):
            station.inject_source(round_id, SourceFrame(10, 'A', 5))
        self.assertEqual(station.inject_source(round_id, SourceFrame(11, 'A', 6)).status, DeliveryStatus.ACCEPTED)
        self.assertEqual(station.inject_source(round_id, SourceFrame(12, 'C', 6)).status, DeliveryStatus.FILTERED)
        self.assertEqual(station.recording_position(round_id), 1)
        station.step(round_id)
        self.assertEqual([row.source_seq for row in station.observations(round_id)], [10, 11, 12, 14])
        self.assertEqual([row.frame.source_seq for row in station.receipts(round_id).first_frames], [10, 11, 14])
        self.assertEqual(station.live(round_id)['A'].total_micro, 35)

    def test_recording_requires_strict_source_order(self):
        with self.assertRaises(ValueError):
            Recording(-10, 10, (SourceFrame(10, 'A', 5), SourceFrame(10, 'B', 6)))
        with self.assertRaises(ValueError):
            Recording(-10, 10, (SourceFrame(11, 'A', 5), SourceFrame(10, 'B', 6)))
