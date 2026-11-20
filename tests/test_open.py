import unittest

from station.capture.recording import Recording, RecordingCatalog, SourceFrame
from station.capture.session import CaptureStation
from station.procedure.admission import AdmissionError
from station.procedure.calibration import CalibrationSpec
from station.procedure.registration import ProcedureRegistry


def rule_inputs(a=(2, 1, 11), capacity=3):
    return {
        'rates': dict.fromkeys('ABCD', 1),
        'masks': {name: list(name) for name in ('A', 'B', 'C', 'D', 'AB', 'AC', 'CD')},
        'calibrations': {
            'A': CalibrationSpec(*a),
            **{name: CalibrationSpec(1, 0, 10) for name in 'BCD'},
        },
        'capacity': capacity,
    }


def make_station(rows=(), capacity=3, raw_bounds=(-10, 10), a=(2, 1, 11)):
    procedures = ProcedureRegistry()
    procedure_id = procedures.register(**rule_inputs(a=a, capacity=capacity))
    recordings = RecordingCatalog()
    recordings.register('shift', Recording(*raw_bounds, tuple(SourceFrame(*row) for row in rows)))
    return CaptureStation(procedures, recordings), procedure_id


class OpenTests(unittest.TestCase):
    def test_freezes_rules_and_opens_new_round_with_revision(self):
        station, procedure = make_station(((10, 'A', 5), (14, 'A', 5)), capacity=2)
        original = station.open_round(procedure, ('A', 'B'), ('AB', 'A'), 'shift')
        self.assertEqual(station.step(original).observation.source_seq, 10)
        draft_view = station.procedures.get_draft(procedure)
        new = rule_inputs(a=(3, 7, 20), capacity=3)
        new['rates']['A'] = 2
        new['masks']['AB'].remove('B')
        station.procedures.revise(procedure, **new)
        self.assertEqual(draft_view.calibrations['A'], CalibrationSpec(3, 7, 20))
        self.assertEqual(station.step(original).observation.source_seq, 14)
        stopped = station.stop(original)
        self.assertEqual(stopped.tallies['A'].total_micro, 22)
        self.assertEqual(stopped.admission.rules.calibrations['A'], CalibrationSpec(2, 1, 11))
        self.assertEqual(stopped.admission.rules.catalog.masks['AB'], frozenset('AB'))
        self.assertEqual(stopped.admission.rules.catalog.rates['A'], 1)
        self.assertEqual(stopped.admission.rules.capacity, 2)

        following = station.open_round(procedure, ('A',), ('AB', None), 'shift')
        self.assertEqual(station.step(following).observation.source_seq, 10)
        self.assertEqual(station.live(following)['A'].total_micro, 22)
        self.assertEqual(station.live(following)['A'].over_limit, 1)

    def test_invalid_registration_and_revision_leave_valid_draft(self):
        station, procedure = make_station(((10, 'A', 5),))
        missing = rule_inputs()
        del missing['calibrations']['C']
        noninteger = rule_inputs()
        noninteger['calibrations']['A'] = CalibrationSpec(2, 1.5, 11)
        unknown_member = rule_inputs()
        unknown_member['masks']['AB'] = ['A', 'missing']
        for invalid in (missing, noninteger, unknown_member):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    station.procedures.register(**invalid)
                with self.assertRaises(ValueError):
                    station.procedures.revise(procedure, **invalid)
        self.assertEqual(station.procedures.get_draft(procedure).calibrations['A'], CalibrationSpec(2, 1, 11))
        round_id = station.open_round(procedure, ('A',), ('A', None), 'shift')
        station.step(round_id)
        self.assertEqual(station.live(round_id)['A'].total_micro, 11)
        self.assertEqual(station.procedures.register(**rule_inputs()), 'P-2')

    def test_invalid_open_leaves_rounds_and_recording_untouched(self):
        station, procedure = make_station(((10, 'A', 5),))
        cases = (
            (KeyError, 'missing', ('A',), ('A', None), 'shift'),
            (KeyError, procedure, ('A',), ('A', None), 'missing'),
            (AdmissionError, procedure, ('A', 'B'), ('A', None), 'shift'),
            (AdmissionError, procedure, ('A',), ('not-a-mask', None), 'shift'),
            (AdmissionError, procedure, ('unknown',), ('A', None), 'shift'),
        )
        for expected, *args in cases:
            with self.subTest(args=args):
                with self.assertRaises(expected):
                    station.open_round(*args)
                self.assertEqual(station.round_ids, ())
        round_id = station.open_round(procedure, ('A',), ('A', None), 'shift')
        self.assertEqual(station.recording_position(round_id), 0)
        self.assertEqual(station.observations(round_id), ())
        self.assertEqual(station.step(round_id).observation.source_seq, 10)

    def test_over_capacity_does_not_start_port(self):
        station, procedure = make_station(((10, 'A', 5),), capacity=3)
        with self.assertRaises(AdmissionError):
            station.open_round(procedure, ('A', 'B'), ('AB', 'CD'), 'shift')
        self.assertEqual(station.round_ids, ())
        round_id = station.open_round(procedure, ('A', 'B'), ('AB', None), 'shift')
        self.assertEqual(station.recording_position(round_id), 0)
        self.assertEqual(station.step(round_id).observation.source_seq, 10)

    def test_overlap_is_charged_once_at_exact_capacity(self):
        station, procedure = make_station(capacity=3)
        round_id = station.open_round(procedure, ('A', 'B'), ('AB', 'AC'), 'shift')
        admission = station.stop(round_id).admission
        self.assertEqual(admission.reserved_rate, 3)
        self.assertEqual(admission.table.allowed_streams, frozenset('ABC'))
        self.assertEqual(admission.rules.capacity, 3)
