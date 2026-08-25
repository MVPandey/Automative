"""CLI smoke tests through typer's runner."""

from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner

from automative.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ['--version'])
    assert result.exit_code == 0 and result.stdout.strip()


def test_init_writes_spec(tmp_path: Path, home: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ['init', '--goal', 'Shrink it.', '--verify', 'echo 1', '--scope', 'src/**', '--no-hooks']
    )
    assert result.exit_code == 0, result.output
    text = (tmp_path / 'AUTOMATIVE.md').read_text()
    assert 'protocol: 1.0.0' in text and 'require_hooks: false' in text
    assert (tmp_path / '.gitignore').read_text().count('.automative/') >= 5
    again = runner.invoke(app, ['init', '--verify', 'echo 1'])
    assert again.exit_code == 10


def test_brief_exit_codes(in_repo: Path) -> None:
    assert runner.invoke(app, ['status']).exit_code == 4
    assert runner.invoke(app, ['run', 'start', '--name', 'cli']).exit_code == 0
    assert runner.invoke(app, ['status']).exit_code == 0
    assert runner.invoke(app, ['run', 'pause']).exit_code == 0
    assert runner.invoke(app, ['status']).exit_code == 5
    assert runner.invoke(app, ['run', 'resume']).exit_code == 0


def test_try_and_end(in_repo: Path, write_data: Callable[[str], None]) -> None:
    runner.invoke(app, ['run', 'start', '--name', 'cli', '--iterations', '2'])
    write_data('hello world\n')
    result = runner.invoke(app, ['try', '-m', 'shorter', '--hypothesis', 'fewer bytes', '--predict', '-50%'])
    assert result.exit_code == 0 and 'KEEP' in result.stdout
    empty = runner.invoke(app, ['try', '-m', 'nothing', '--hypothesis', 'x'])
    assert empty.exit_code == 15
    ledger = runner.invoke(app, ['ledger'])
    assert 'keep' in ledger.stdout
    end = runner.invoke(app, ['run', 'end'])
    assert end.exit_code == 0 and 'Baseline 36' in end.stdout
    report = runner.invoke(app, ['report'])
    assert '1 kept' in report.stdout


def test_doctor(in_repo: Path) -> None:
    result = runner.invoke(app, ['doctor'])
    assert result.exit_code == 0, result.output
    assert 'All checks passed' in result.stdout


def test_protocol_commands(in_repo: Path, home: Path) -> None:
    assert '1.0.0' in runner.invoke(app, ['protocol', 'list']).stdout
    show = runner.invoke(app, ['protocol', 'show', '--path-only'])
    assert show.exit_code == 0 and show.stdout.strip().endswith('SKILL.md')
    assert runner.invoke(app, ['protocol', 'pin', '0.0.0-null']).exit_code == 0
    assert 'protocol: 0.0.0-null' in (in_repo / 'AUTOMATIVE.md').read_text()
    assert runner.invoke(app, ['protocol', 'install']).exit_code == 0
    assert (home / 'protocol' / 'versions' / '1.0.0' / 'SKILL.md').is_file()


def test_strategy_commands(in_repo: Path) -> None:
    add = runner.invoke(
        app, ['strategy', 'add', 'use a set for membership', '--kind', 'works', '--when', 'O(n^2) scans']
    )
    assert add.exit_code == 0 and add.stdout.startswith('S-0001')
    assert 'S-0001' in runner.invoke(app, ['strategy', 'show']).stdout
    assert 'S-0001' in runner.invoke(app, ['strategy', 'suggest']).stdout
    bad = runner.invoke(app, ['strategy', 'add', 'x', '--kind', 'bogus'])
    assert bad.exit_code == 18


def test_hook_noop_outside_run(in_repo: Path) -> None:
    result = runner.invoke(app, ['hook', 'stop'], input='{}')
    assert result.exit_code == 0 and result.stdout.strip() == ''


def test_hook_denies_protected_edit(in_repo: Path) -> None:
    runner.invoke(app, ['run', 'start', '--name', 'hook'])
    payload = f'{{"tool_name":"Edit","tool_input":{{"file_path":"score.sh"}},"session_id":"s","cwd":"{in_repo}"}}'
    result = runner.invoke(app, ['hook', 'pre-tool-use'], input=payload)
    assert result.exit_code == 0 and '"deny"' in result.stdout


def test_bench_list_empty(in_repo: Path, home: Path) -> None:
    assert '(no tasks)' in runner.invoke(app, ['bench', 'list']).stdout


def test_candidate_lifecycle(in_repo: Path, home: Path) -> None:
    created = runner.invoke(app, ['protocol', 'candidate', 'create', '1.0.1', '--from', '1.0.0'])
    assert created.exit_code == 0, created.output
    invalid = runner.invoke(app, ['protocol', 'candidate', 'validate', '1.0.1'])
    assert invalid.exit_code == 17  # identical to parent
    skill = home / 'protocol' / 'versions' / '1.0.1' / 'SKILL.md'
    skill.write_text(skill.read_text() + '\n## Tip\nRead the profile first.\n')
    valid = runner.invoke(app, ['protocol', 'candidate', 'validate', '1.0.1'])
    assert valid.exit_code == 0 and 'VALID' in valid.stdout
    assert runner.invoke(app, ['protocol', 'promote', '1.0.1', '--confirm']).exit_code == 17
    assert runner.invoke(app, ['protocol', 'reject', '1.0.1', '--reason', 'no']).exit_code == 0
    assert '1.0.1 rejected' in runner.invoke(app, ['protocol', 'changelog']).stdout
