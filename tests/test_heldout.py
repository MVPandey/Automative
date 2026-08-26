"""The held-out check: paired, unpersisted, sealed data, traced, and auditable."""

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from automative import audit as audit_io
from automative import hooks
from automative import ledger as ledger_io
from automative import trace as trace_io
from automative.cli import app
from automative.decide import FailureCategory, Outcome
from automative.errors import VerifyError
from automative.runloop import Project, RunLoop
from automative.verify import CommandResult, SudoRunner
from tests.conftest import git, make_repo

HELDOUT_SH = '#!/bin/sh\nwc -w < src/data.txt | tr -d " "\n'


def _repo_with_heldout(root: Path, *, heldout_sh: str = HELDOUT_SH) -> RunLoop:
    """Metric: bytes of data.txt (lower). Held out: words in data.txt (lower). data/heldout/ is sealed."""
    make_repo(root)
    (root / 'heldout.sh').write_text(heldout_sh)
    os.chmod(root / 'heldout.sh', 0o755)
    (root / 'data' / 'heldout').mkdir(parents=True)
    (root / 'data' / 'heldout' / 'secret.csv').write_text('1,2,3\n')
    spec = root / 'AUTOMATIVE.md'
    text = spec.read_text().replace('  timeout_s: 5\n', '  heldout: ./heldout.sh\n  timeout_s: 5\n')
    text = text.replace(
        'protected: [score.sh, guard.sh]\n', 'protected: [score.sh, guard.sh]\nsealed: [data/heldout/**]\n'
    )
    text = text.replace('max_denied_tool_calls: 5', 'max_denied_tool_calls: 20')
    spec.write_text(text)
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'heldout')
    return RunLoop(Project.load(root))


def _pre(root: Path, tool: str, **tool_input: object) -> bool:
    payload = {'tool_name': tool, 'tool_input': tool_input, 'session_id': 's1'}
    response = hooks.handle('pre-tool-use', payload, root)
    return bool(response.payload) and response.payload['hookSpecificOutput']['permissionDecision'] == 'deny'  # type: ignore[index]


def test_paired_check_writes_no_number_anywhere(tmp_path: Path, home: Path) -> None:
    root = tmp_path / 'repo'
    loop = _repo_with_heldout(root)
    loop.start('held')
    run_id = loop.require_state().run_id
    data = root / 'src' / 'data.txt'

    data.write_text('hello world hello world\n')  # 24 bytes (better), 4 words (better than 6)
    kept = loop.try_change('shorter', 'fewer bytes')
    assert kept.decision.outcome is Outcome.KEEP
    assert kept.row.heldout == 'pass' and kept.row.heldout_score is None
    assert 'Held-out check: pass' in kept.text

    data.write_text('a b c d e hello\n')  # 16 bytes (better), 6 words (worse than 4)
    lost = loop.try_change('more words', 'fewer bytes but more words')
    assert lost.decision.outcome is Outcome.DISCARD
    assert lost.decision.failure_category is FailureCategory.HELDOUT
    assert lost.row.heldout == 'fail'
    assert data.read_text() == 'hello world hello world\n'
    assert git(root, 'status', '--porcelain', 'src') == ''

    assert not (root / '.automative' / 'heldout').exists()
    ledger_text = loop.paths.ledger_file.read_text()
    assert '"heldout":"pass"' in ledger_text and '"heldout":"fail"' in ledger_text
    for row in ledger_text.splitlines():
        parsed = json.loads(row)
        if parsed['type'] == 'event' and parsed['event'] == 'heldout':
            assert set(parsed['data']) == {'iter', 'passed'}
    iter_dir = loop.paths.iter_dir(run_id, 1)
    assert (iter_dir / 'verify.log').read_text().count('verify sample') == 1
    assert [p.name for p in iter_dir.iterdir() if p.suffix == '.log'] == ['verify.log']


def test_tree_is_restored_when_the_incumbent_measurement_crashes(tmp_path: Path, home: Path) -> None:
    counter = tmp_path / 'calls'
    # 1st call: baseline check at start; 2nd: candidate; 3rd: incumbent, which crashes
    script = (
        '#!/bin/sh\n'
        f'n=$(cat "{counter}" 2>/dev/null || echo 0); echo $((n+1)) > "{counter}"\n'
        '[ "$n" -eq 2 ] && exit 3\n'
        'wc -w < src/data.txt | tr -d " "\n'
    )
    root = tmp_path / 'repo'
    loop = _repo_with_heldout(root, heldout_sh=script)
    loop.start('held')
    data = root / 'src' / 'data.txt'
    data.write_text('hello world\n')
    lost = loop.try_change('shorter', 'fewer bytes')
    assert lost.decision.outcome is Outcome.DISCARD and lost.row.heldout == 'fail'
    events = [r for r in ledger_io.read(loop.paths.ledger_file) if r.type == 'event' and r.event == 'heldout']
    assert 'incumbent' in events[-1].detail
    assert data.read_text() == 'hello world hello world hello world\n'  # back at the baseline, cleanly
    assert git(root, 'status', '--porcelain', 'src') == ''


def test_start_refuses_a_broken_heldout_command(tmp_path: Path, home: Path) -> None:
    root = tmp_path / 'repo'
    loop = _repo_with_heldout(root, heldout_sh='#!/bin/sh\nexit 9\n')
    with pytest.raises(VerifyError):
        loop.start('held')


def test_hooks_seal_declared_paths_and_out_of_band_runs(tmp_path: Path, home: Path) -> None:
    root = tmp_path / 'repo'
    loop = _repo_with_heldout(root)
    loop.start('held')
    run_id = loop.require_state().run_id

    assert _pre(root, 'Read', file_path='data/heldout/secret.csv')
    assert _pre(root, 'Read', file_path=str(root / 'data' / 'heldout' / 'secret.csv'))
    assert _pre(root, 'Bash', command='cat data/heldout/secret.csv')
    assert _pre(root, 'Bash', command='ls data/heldout')
    assert _pre(root, 'Bash', command='python3 -c "open(\'data/heldout/secret.csv\')"')
    assert _pre(root, 'Bash', command='./heldout.sh')
    assert _pre(root, 'Bash', command='sudo -u verifier ./anything')
    assert not _pre(root, 'Read', file_path='src/data.txt')
    assert not _pre(root, 'Read', file_path=f'.automative/runs/{run_id}/notes.md')
    assert not _pre(root, 'Bash', command='ls data')
    assert not _pre(root, 'Bash', command='automative report')
    assert loop.require_state().denied_tool_calls == 7
    denied = [r for r in trace_io.read(loop.paths.trace_file, run_id) if r.denied]
    assert len(denied) == 7 and all(r.reason for r in denied)


def test_trace_records_every_call_and_audit_reads_it(tmp_path: Path, home: Path) -> None:
    root = tmp_path / 'repo'
    loop = _repo_with_heldout(root)
    loop.start('held')
    run_id = loop.require_state().run_id

    def post(tool: str, tool_input: dict[str, object], response: str = 'ok') -> None:
        payload = {'tool_name': tool, 'tool_input': tool_input, 'tool_response': response, 'session_id': 's1'}
        hooks.handle('post-tool-use', payload, root)

    post('Read', {'file_path': 'src/data.txt'}, 'hello ' * 500)
    post('Bash', {'command': 'ls'}, 'AUTOMATIVE.md\nsrc')
    post('Grep', {'pattern': 'hello', 'path': 'src'})
    rows = trace_io.read(loop.paths.trace_file, run_id)
    assert [r.tool for r in rows] == ['Read', 'Bash', 'Grep']
    assert rows[0].response is not None and 'more chars' in rows[0].response and len(rows[0].response) < 1700

    kwargs = {'sealed': ('data/heldout/**',), 'heldout_cmd': './heldout.sh', 'is_sealed': hooks._is_sealed}
    clean = audit_io.audit(ledger_io.read(loop.paths.ledger_file), run_id, trace=rows, **kwargs)  # type: ignore[arg-type]
    assert clean.ok and clean.tool_calls == 3 and clean.denials == 0

    # calls that slipped past the hooks (a script, a python one-liner) still show up in the audit
    post('Bash', {'command': 'python3 -c \'print(open("data/heldout/secret.csv").read())\''})
    post('Bash', {'command': './heldout.sh'})
    rows = trace_io.read(loop.paths.trace_file, run_id)
    dirty = audit_io.audit(ledger_io.read(loop.paths.ledger_file), run_id, trace=rows, **kwargs)  # type: ignore[arg-type]
    assert not dirty.ok
    assert any('sealed path' in f for f in dirty.trace_findings)
    assert any('out of band' in f for f in dirty.trace_findings)


def test_report_heldout_re_measures_after_the_run(tmp_path: Path, home: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / 'repo'
    _repo_with_heldout(root)
    monkeypatch.chdir(root)
    runner = CliRunner()
    assert runner.invoke(app, ['run', 'start', '--name', 'held']).exit_code == 0
    (root / 'src' / 'data.txt').write_text('hello world hello world\n')
    out = runner.invoke(app, ['try', '-m', 'shorter', '--hypothesis', 'x'])
    assert 'Held-out check: pass' in out.stdout
    during = runner.invoke(app, ['report', '--heldout'])
    assert during.exit_code != 0 and 'after `automative run end`' in during.output
    assert runner.invoke(app, ['run', 'end']).exit_code == 0
    after = runner.invoke(app, ['report', '--heldout'])
    assert after.exit_code == 0, after.output
    lines = after.stdout.splitlines()
    assert any(line.startswith('baseline') and line.endswith(' 6') for line in lines)
    assert any(line.startswith('i1') and line.endswith(' 4') for line in lines)
    assert (root / 'src' / 'data.txt').read_text() == 'hello world hello world\n'
    trace = runner.invoke(app, ['trace'])
    assert trace.exit_code == 0
    audit = runner.invoke(app, ['audit'])
    assert audit.exit_code == 0 and 'trace is clean' in audit.stdout


def test_sudo_runner_builds_exact_argv_and_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    log = tmp_path / 'sudo.log'
    script = fake_bin / 'sudo'
    script.write_text(f'#!/bin/sh\necho "$@" > "{log}"\nshift 4\nexec "$@"\n')
    os.chmod(script, 0o755)
    monkeypatch.setenv('PATH', f'{fake_bin}{os.pathsep}{os.environ["PATH"]}')
    runner = SudoRunner('verifier')
    assert runner.argv('python3 backtest.py heldout') == [
        'sudo',
        '-n',
        '-u',
        'verifier',
        '--',
        'python3',
        'backtest.py',
        'heldout',
    ]
    result = runner.run("sh -c 'echo 42'", tmp_path, 5)
    assert isinstance(result, CommandResult) and result.exit_code == 0 and result.stdout.strip() == '42'
    assert log.read_text().startswith('-n -u verifier -- sh -c')
    assert runner.run("sh -c 'unterminated", tmp_path, 5).exit_code == 127


def test_doctor_reports_sealed_files(tmp_path: Path, home: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / 'repo'
    _repo_with_heldout(root)
    monkeypatch.chdir(root)
    result = CliRunner().invoke(app, ['doctor'])
    assert result.exit_code == 0, result.output
    assert 'Sealed: 1 files listed' in result.stdout
