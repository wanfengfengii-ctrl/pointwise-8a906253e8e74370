"""Run this project's full unittest suite from the repository root."""
import sys
import unittest

from demo import _switch_marker


def _switch_gate() -> str | None:
    """Re-run the joint switch scenario as an explicit end-to-end gate."""
    try:
        marker = _switch_marker()
    except Exception as exc:  # noqa: BLE001 - report any failure as a gate failure
        return f'{type(exc).__name__}: {exc}'
    if marker['cut_after_source_seq'] != 13 or marker['final_slots'] != ['AB', 'CD']:
        return f'unexpected switch outcome: {marker!r}'
    if marker['before_tallies'] != marker['recalculated_before']:
        return 'old-segment report and audit disagree'
    if marker['tallies'] != marker['recalculated_after']:
        return 'new-segment report and audit disagree'
    return None


def main():
    suite = unittest.defaultTestLoader.discover('tests', top_level_dir='.')
    if suite.countTestCases():
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    else:
        print('No tests discovered in tests/', file=sys.stderr)
        result = unittest.TestResult()
    for status, failed_cases in (
        ('FAIL', (test for test, _ in result.failures)),
        ('ERROR', (test for test, _ in result.errors)),
        ('UNEXPECTED_SUCCESS', result.unexpectedSuccesses),
    ):
        for test in failed_cases:
            print(f'{status} {test.id()}')
    switch_failure = _switch_gate()
    if switch_failure is not None:
        print(f'FAIL switchover joint gate: {switch_failure}', file=sys.stderr)
    successful = result.testsRun > 0 and result.wasSuccessful() and switch_failure is None
    print(f'TEST SUMMARY total={result.testsRun} failures={len(result.failures)} '
          f'errors={len(result.errors)} skipped={len(result.skipped)} '
          f'expected_failures={len(result.expectedFailures)} '
          f'unexpected_successes={len(result.unexpectedSuccesses)} '
          f'switchover={"PASS" if switch_failure is None else "FAIL"} '
          f'result={"PASS" if successful else "FAIL"}')
    return 0 if successful else 1


if __name__ == '__main__':
    raise SystemExit(main())
