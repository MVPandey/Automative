"""Benchmark freeze, normalization, cascade, and gate with a scripted FakeAgent."""

import json
from pathlib import Path

import pytest

from automative import bench, evolution
from automative.bench import CallableDriver, GateParams, TaskSpec, normalized_score, split_for
from automative.protocol import NULL_VERSION, resolve_version
from automative.runloop import Project, RunLoop
from automative.spec import Direction
from tests.conftest import make_repo


def _task(
    task_id: str, baseline: float = 36.0, known: float = 12.0, noise: float = 0.0, split: str = 'train'
) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        source_run='r',
        source_project='p',
        frozen_at='t',
        baseline_commit='abc',
        scope=('src/**',),
        direction=Direction.LOWER,
        verify='./score.sh',
        timeout_s=5,
        repeats=1,
        iterations=4,
        minutes=5,
        baseline_score=baseline,
        known_achievable=known,
        noise_floor=noise,
        cost_class='cheap',
        split=split,
    )


def test_split_deterministic() -> None:
    assert split_for('abc') == split_for('abc')
    kinds = {split_for(f'task-{i}') for i in range(50)}
    assert kinds == {'train', 'heldout'}


def test_normalized_score() -> None:
    task = _task('t')
    assert normalized_score(task, 36.0) == 0.0
    assert normalized_score(task, 12.0) == 1.0
    assert normalized_score(task, 24.0) == 0.5
    assert normalized_score(task, 48.0) == -0.5
    assert normalized_score(task, 0.0) == 1.5
    assert normalized_score(task, None) == -1.0
    flat = _task('f', known=36.0)
    assert normalized_score(flat, 30.0) == 1.0 and normalized_score(flat, 36.0) == 0.0


def _agent_factory(strength: dict[str, int]):  # type: ignore[no-untyped-def]
    """FakeAgent: removes `strength[version]` words per try from data.txt."""

    def agent(loop: RunLoop, task: TaskSpec, version: str, seed: int) -> None:
        words_per_try = strength.get(version, 1)
        path = loop.paths.root / 'src' / 'data.txt'
        for _ in range(task.iterations):
            words = path.read_text().split()
            if len(words) <= 1:
                break
            keep = max(1, len(words) - words_per_try)
            path.write_text(' '.join(words[:keep]) + '\n')
            outcome = loop.try_change(f'drop to {keep} words', 'fewer bytes')
            if outcome.stopped:
                break
        if loop.require_state().status.value != 'done':
            loop.end('fake agent finished')

    return agent


def _freeze_many(tmp_path: Path, count: int) -> list[TaskSpec]:
    tasks: list[TaskSpec] = []
    for i in range(count):
        repo = make_repo(tmp_path / f'repo{i}', iterations=4)
        loop = RunLoop(Project.load(repo))
        loop.start(f'src{i}')
        (repo / 'src' / 'data.txt').write_text('hello world hello\n')
        loop.try_change('shorter', 'fewer bytes')
        loop.end('freeze me')
        tasks.append(bench.freeze(loop.project, loop.require_state().run_id))
    return tasks


def test_freeze_creates_task(tmp_path: Path, home: Path) -> None:
    (task,) = _freeze_many(tmp_path, 1)
    assert (home / 'bench' / 'tasks' / task.task_id / 'repo.bundle').is_file()
    assert task.baseline_score == 36.0 and task.known_achievable == 18.0 and task.cost_class == 'cheap'
    assert bench.load_task(task.task_id) == task


def test_gate_passes_for_stronger_candidate(tmp_path: Path, home: Path) -> None:
    tasks = _freeze_many(tmp_path, 6)
    # force a 4/2 split so the gate has enough held-out tasks regardless of hashing
    tasks = [t.model_copy(update={'split': 'heldout' if i < 3 else 'train'}) for i, t in enumerate(tasks)]
    for t in tasks:
        bench.save_task(t)
    evolution.create_candidate('1.0.0', '1.0.1', created_by='test')
    driver = CallableDriver(_agent_factory({'1.0.1': 2, '1.0.0': 1, NULL_VERSION: 1}))
    result = bench.run_bench(
        '1.0.1', '1.0.0', driver, seeds=2, tasks=tasks, params=GateParams(min_tasks=6, min_heldout=3)
    )
    assert result.gate.passed, result.gate.reasons
    assert result.gate.stage_reached == 3
    assert result.gate.heldout_mean['1.0.1'] > result.gate.heldout_mean['1.0.0']
    assert (result.path / 'result.json').is_file()
    assert all(c.integrity_events == 0 for c in result.cells)


def test_gate_fails_for_identical_candidate_and_marks_uninformative(tmp_path: Path, home: Path) -> None:
    tasks = _freeze_many(tmp_path, 6)
    tasks = [t.model_copy(update={'split': 'heldout' if i < 3 else 'train'}) for i, t in enumerate(tasks)]
    for t in tasks:
        bench.save_task(t)
    evolution.create_candidate('1.0.0', '1.0.1', created_by='test')
    driver = CallableDriver(_agent_factory({}))
    result = bench.run_bench(
        '1.0.1', '1.0.0', driver, seeds=1, tasks=tasks, params=GateParams(min_tasks=6, min_heldout=3)
    )
    assert not result.gate.passed
    assert any('does not beat' in r for r in result.gate.reasons)
    assert all(not bench.load_task(t.task_id).informative for t in tasks)


def test_early_reject_on_train(tmp_path: Path, home: Path) -> None:
    tasks = _freeze_many(tmp_path, 3)
    tasks = [t.model_copy(update={'split': 'train'}) for t in tasks]
    for t in tasks:
        bench.save_task(t)
    evolution.create_candidate('1.0.0', '1.0.1', created_by='test')
    driver = CallableDriver(_agent_factory({'1.0.1': 1, '1.0.0': 3}))
    result = bench.run_bench(
        '1.0.1', '1.0.0', driver, seeds=2, tasks=tasks, params=GateParams(min_tasks=1, min_heldout=0)
    )
    assert result.gate.stage_reached == 1 and not result.gate.passed


def test_cache_reuse(tmp_path: Path, home: Path) -> None:
    (task,) = _freeze_many(tmp_path, 1)
    driver = CallableDriver(_agent_factory({}))
    with __import__('tempfile').TemporaryDirectory() as tmp:
        first = bench.run_cell(task, '1.0.0', 1, driver, scratch=Path(tmp))
        second = bench.run_cell(task, '1.0.0', 1, driver, scratch=Path(tmp))
    assert not first.cached and second.cached and second.best == first.best


def test_evolve_benchmark_marks_manifest(tmp_path: Path, home: Path) -> None:
    from automative import evolve as evolve_io

    tasks = _freeze_many(tmp_path, 6)
    tasks = [t.model_copy(update={'split': 'heldout' if i < 3 else 'train'}) for i, t in enumerate(tasks)]
    for t in tasks:
        bench.save_task(t)
    project = Project.load(tmp_path / 'repo0')
    proposal = evolve_io.propose(project, parent='1.0.0')
    skill = proposal.path / 'SKILL.md'
    skill.write_text(skill.read_text() + '\n## Extra\nPrefer removing several words per try.\n')
    driver = CallableDriver(_agent_factory({proposal.version: 3}))
    result = evolve_io.benchmark(proposal.version, driver, seeds=1, params=GateParams(min_tasks=6, min_heldout=3))
    assert result.gate.passed
    assert resolve_version(proposal.version).manifest.status == 'gate-passed'
    evolution.promote(proposal.version, confirm=True)
    assert resolve_version(proposal.version).manifest.status == 'promoted'


def test_evolve_rejects_failed_candidate(tmp_path: Path, home: Path) -> None:
    from automative import evolve as evolve_io

    tasks = _freeze_many(tmp_path, 6)
    tasks = [t.model_copy(update={'split': 'heldout' if i < 3 else 'train'}) for i, t in enumerate(tasks)]
    for t in tasks:
        bench.save_task(t)
    proposal = evolve_io.propose(Project.load(tmp_path / 'repo0'), parent='1.0.0')
    skill = proposal.path / 'SKILL.md'
    skill.write_text(skill.read_text() + '\n## Extra\nNothing that changes behaviour.\n')
    driver = CallableDriver(_agent_factory({}))
    result = evolve_io.benchmark(proposal.version, driver, seeds=1, params=GateParams(min_tasks=6, min_heldout=3))
    assert not result.gate.passed
    assert resolve_version(proposal.version).manifest.status == 'rejected'


def test_dsh_driver_invokes_headless_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from automative.bench import DshHeadlessDriver

    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    log = tmp_path / 'argv.json'
    script = fake_bin / 'dsh'
    script.write_text(
        "#!/bin/sh\npython3 -c \"import json,sys,os; json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd(), "
        f"'root': os.environ.get('AUTOMATIVE_PLUGIN_ROOT')}}, open('{log}', 'w'))\" \"$@\"\n"
    )
    os.chmod(script, 0o755)
    monkeypatch.setenv('PATH', f'{fake_bin}{os.pathsep}{os.environ["PATH"]}')
    worktree = tmp_path / 'wt'
    worktree.mkdir()
    DshHeadlessDriver(plugin_root=tmp_path / 'plugin').drive(worktree, _task('t1'), '1.0.0', 1)
    seen = json.loads(log.read_text())
    assert seen['argv'][:4] == [
        '--profile',
        'headless',
        '--patch',
        str(tmp_path / 'plugin/integrations/dsh/cordis.patch.yml'),
    ]
    assert 'automative session brief' in seen['argv'][-1]
    assert seen['cwd'] == str(worktree.resolve()) and seen['root'] == str(tmp_path / 'plugin')
