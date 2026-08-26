"""Held-out scores are sealed: pass/fail is all the agent sees, the numbers live in the sidecar."""

import os
from pathlib import Path

from typer.testing import CliRunner

from automative import heldout as heldout_io
from automative import hooks
from automative import ledger as ledger_io
from automative.cli import app
from automative.decide import FailureCategory, Outcome
from automative.runloop import Project, RunLoop
from tests.conftest import git, make_repo


def _repo_with_heldout(root: Path) -> RunLoop:
    """Metric: bytes of data.txt (lower). Held out: words in data.txt (lower)."""
    make_repo(root)
    (root / 'heldout.sh').write_text('#!/bin/sh\nwc -w < src/data.txt | tr -d " "\n')
    os.chmod(root / 'heldout.sh', 0o755)
    spec = root / 'AUTOMATIVE.md'
    spec.write_text(spec.read_text().replace('  timeout_s: 5\n', '  heldout: ./heldout.sh\n  timeout_s: 5\n'))
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'heldout')
    return RunLoop(Project.load(root))


def test_pass_and_fail_are_all_the_row_says(tmp_path: Path, home: Path) -> None:
    root = tmp_path / 'repo'
    loop = _repo_with_heldout(root)
    loop.start('held')
    run_id = loop.require_state().run_id
    data = root / 'src' / 'data.txt'

    data.write_text('hello world hello world\n')  # 24 bytes (better), 4 words (better)
    kept = loop.try_change('shorter', 'fewer bytes')
    assert kept.decision.outcome is Outcome.KEEP
    assert kept.row.heldout == 'pass' and kept.row.heldout_score is None
    assert 'Held-out check: pass' in kept.text and '4' not in kept.text.split('Held-out')[1].split('\n')[0]

    data.write_text('a b c d e hello\n')  # 16 bytes (better), 6 words (worse than 4)
    lost = loop.try_change('more words', 'fewer bytes but more words')
    assert lost.decision.outcome is Outcome.DISCARD
    assert lost.decision.failure_category is FailureCategory.HELDOUT
    assert lost.row.heldout == 'fail' and lost.row.heldout_score is None
    assert data.read_text() == 'hello world hello world\n'

    records = heldout_io.read(loop.paths.heldout_file, run_id)
    assert [(r.iter, r.score, r.not_worse) for r in records] == [(1, 4.0, True), (2, 6.0, False)]
    assert heldout_io.best_kept(loop.paths.heldout_file, run_id, frozenset({1})) == 4.0

    # the numbers are nowhere the agent looks: rows say pass/fail, the verify log never ran heldout.sh
    ledger_text = loop.paths.ledger_file.read_text()
    assert '"heldout":"pass"' in ledger_text and '"heldout":"fail"' in ledger_text
    assert '"heldout_score":4' not in ledger_text and '"heldout_score":6' not in ledger_text
    assert (loop.paths.iter_dir(run_id, 1) / 'verify.log').read_text().count('verify sample') == 1
    held_log = loop.paths.heldout_log(run_id, 1)
    assert held_log.is_file() and '\n4\n' in held_log.read_text()
    assert loop.paths.heldout_file.is_relative_to(loop.paths.heldout_dir)


def test_hooks_seal_the_sidecar(tmp_path: Path, home: Path) -> None:
    root = tmp_path / 'repo'
    loop = _repo_with_heldout(root)
    loop.start('held')
    run_id = loop.require_state().run_id

    def pre(tool: str, **tool_input: object) -> bool:
        payload = {'tool_name': tool, 'tool_input': tool_input, 'session_id': 's1'}
        response = hooks.handle('pre-tool-use', payload, root)
        return bool(response.payload) and response.payload['hookSpecificOutput']['permissionDecision'] == 'deny'  # type: ignore[index]

    assert pre('Read', file_path='.automative/heldout/scores.jsonl')
    assert pre('Read', file_path=str(root / '.automative' / 'heldout' / f'{run_id}-iter-001.log'))
    assert pre('Bash', command='cat .automative/heldout/scores.jsonl')
    assert pre('Bash', command='automative report --heldout')
    assert not pre('Read', file_path=f'.automative/runs/{run_id}/notes.md')
    assert not pre('Read', file_path='src/data.txt')
    assert not pre('Bash', command='automative report')
    assert loop.require_state().denied_tool_calls == 4


def test_report_heldout_for_humans(tmp_path: Path, home: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / 'repo'
    loop = _repo_with_heldout(root)
    monkeypatch.chdir(root)
    runner = CliRunner()
    assert runner.invoke(app, ['run', 'start', '--name', 'held']).exit_code == 0
    (root / 'src' / 'data.txt').write_text('hello world hello world\n')
    out = runner.invoke(app, ['try', '-m', 'shorter', '--hypothesis', 'x'])
    assert 'Held-out check: pass' in out.stdout
    rows = runner.invoke(app, ['ledger', '--json']).stdout
    assert '"heldout":"pass"' in rows and '"heldout_score":null' in rows
    assert runner.invoke(app, ['run', 'end']).exit_code == 0
    plain = runner.invoke(app, ['report']).stdout
    assert 'held-out' not in plain
    sealed = runner.invoke(app, ['report', '--heldout']).stdout
    assert 'iter  kept  held-out' in sealed and '1     yes   4          pass' in sealed
    assert ledger_io.iterations(loop.paths.ledger_file)[0].heldout == 'pass'
