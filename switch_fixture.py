"""Deterministic source fixtures and fixed-round controls for online switch acceptance."""

from station.capture.recording import Recording, RecordingCatalog, SourceFrame
from station.capture.session import CaptureStation
from station.procedure.calibration import CalibrationSpec
from station.procedure.registration import ProcedureRegistry


SINGLES = ("A", "B", "C", "D")
# Capacity, mask names, old subscription, start slots, new subscription, end slots.
CASES = {
    "bridge": (3, (*SINGLES, "AC"), ("A", "B"), ("A", "B"), ("C", "D"), ("C", "D")),
    "blocked": (3, SINGLES, ("A", "B"), ("A", "B"), ("C", "D"), ("C", "D")),
    "either": (4, (*SINGLES, "AB", "AC", "CD"), ("A", "B"), ("A", "B"), ("C", "D"), ("C", "D")),
    "mark": (4, ("AB", "CD"), ("A", "C"), ("AB", "CD"), ("A", "D"), ("AB", "CD")),
}


def make_station(name):
    capacity, names, old, start, new, end = CASES[name]
    rates = dict.fromkeys(SINGLES, 1)
    masks = {mask: tuple(mask) for mask in names}
    calibrations = dict.fromkeys(SINGLES, CalibrationSpec(1, 0, 10))
    calibrations["A"] = CalibrationSpec(2, 1, 11)
    procedures = ProcedureRegistry()
    procedure_id = procedures.register(rates, masks, calibrations, capacity)
    recordings = RecordingCatalog()
    recordings.register("switch", Recording(-10, 10, ()))
    return CaptureStation(procedures, recordings), procedure_id, old, start, new, end


def open_case(name):
    station, procedure_id, old, start, new, end = make_station(name)
    return station, station.open_round(procedure_id, old, start, "switch"), new, end


def supply_group(station, round_id, first_seq):
    """Feed equal codes for all four streams through the real serial port."""
    outcomes = []
    for offset, stream in enumerate(SINGLES):
        frame = SourceFrame(first_seq + offset, stream, 5)
        outcomes.append(station.inject_source(round_id, frame))
    return tuple(outcomes)


def statuses(outcomes):
    return [item.status.value for item in outcomes]


def main():
    station, procedure_id, old, start, new, end = make_station("blocked")
    old_round = station.open_round(procedure_id, old, start, "switch")
    new_round = station.open_round(procedure_id, new, end, "switch")
    got_old = statuses(supply_group(station, old_round, 10))
    got_new = statuses(supply_group(station, new_round, 10))
    if got_old != ["accepted", "accepted", "filtered", "filtered"] or got_new != ["filtered", "filtered", "accepted", "accepted"]:
        raise AssertionError(f"fixed endpoints disagree: {got_old}, {got_new}")

    station, procedure_id, old, _, _, _ = make_station("bridge")
    round_id = station.open_round(procedure_id, old, ("AC", "B"), "switch")
    old_gap = supply_group(station, round_id, 20)
    forwarded = [item.observation.stream for item in old_gap if item.observation.forwarded]
    accepted = [item.observation.stream for item in old_gap if item.status.value == "accepted"]
    if forwarded != ["A", "B", "C"] or accepted != ["A", "B"]:
        raise AssertionError(f"fixed AC|B control disagrees: {forwarded}, {accepted}")
    print("fixed endpoints", got_old, got_new)
    print("fixed table AC|B forwarded", forwarded, "and accepted", accepted)


if __name__ == "__main__":
    main()
