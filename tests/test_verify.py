"""Score parsing and subprocess measurement."""

from pathlib import Path

from automative.verify import GuardStatus, LocalRunner, VerifyOutcome, measure, parse_score, run_guards


def test_parse_score_last_line() -> None:
    assert parse_score('noise\n 12.5 \n') == 12.5
    assert parse_score('1e-3\n') == 0.001
    assert parse_score('done\n') is None
    assert parse_score('') is None
    assert parse_score('3\n\n') == 3.0


def test_measure_median_and_log(tmp_path: Path) -> None:
    log = tmp_path / 'v.log'
    result = measure(LocalRunner(), 'echo 4', tmp_path, 5, repeats=3, log_path=log)
    assert result.outcome is VerifyOutcome.OK
    assert result.score == 4.0
    assert result.samples == (4.0, 4.0, 4.0)
    assert log.read_text().count('verify sample') == 3


def test_measure_crash(tmp_path: Path) -> None:
    result = measure(LocalRunner(), 'echo boom >&2; exit 3', tmp_path, 5)
    assert result.outcome is VerifyOutcome.CRASH
    assert 'boom' in result.tail


def test_measure_metric_error(tmp_path: Path) -> None:
    assert measure(LocalRunner(), 'echo not-a-number', tmp_path, 5).outcome is VerifyOutcome.METRIC_ERROR


def test_measure_timeout(tmp_path: Path) -> None:
    assert measure(LocalRunner(), 'sleep 3; echo 1', tmp_path, 1).outcome is VerifyOutcome.TIMEOUT


def test_guards(tmp_path: Path) -> None:
    assert run_guards(LocalRunner(), (), tmp_path, 5).status is GuardStatus.NONE
    assert run_guards(LocalRunner(), ('true',), tmp_path, 5).status is GuardStatus.PASS
    failed = run_guards(LocalRunner(), ('true', 'false'), tmp_path, 5)
    assert failed.status is GuardStatus.FAIL
    assert failed.failed_cmd == 'false'
