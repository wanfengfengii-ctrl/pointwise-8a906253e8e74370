import unittest

from station.quality.tally import StreamTally, UnknownRound
from tests.test_open import make_station


class QualityTests(unittest.TestCase):
    def test_equal_boundary_values_keep_earliest_extrema(self):
        station, procedure = make_station(((10, 'A', 5), (14, 'A', 5)))
        round_id = station.open_round(procedure, ('A', 'B'), ('AB', None), 'shift')
        station.step(round_id)
        early_view = station.live(round_id)
        station.step(round_id)
        expected = StreamTally(
            valid_count=2, total_micro=22, minimum=11, minimum_seq=10,
            maximum=11, maximum_seq=10, over_limit=0, bad_count=0,
        )
        self.assertEqual(station.live(round_id), {'A': expected, 'B': StreamTally()})
        self.assertEqual(early_view['A'].valid_count, 1)
        early_view.clear()
        self.assertEqual(station.live(round_id)['A'], expected)

    def test_unknown_live_round(self):
        station, _ = make_station()
        with self.assertRaises(UnknownRound):
            station.live('missing')

    def test_signed_conversion_and_closed_raw_interval(self):
        station, procedure = make_station(((10, 'A', -10), (13, 'A', 10)), a=(-2, -3, 0))
        round_id = station.open_round(procedure, ('A',), ('A', None), 'shift')
        station.step(round_id)
        station.step(round_id)
        self.assertEqual(station.live(round_id)['A'], StreamTally(
            valid_count=2, total_micro=-6, minimum=-23, minimum_seq=13,
            maximum=17, maximum_seq=10, over_limit=1, bad_count=0,
        ))
