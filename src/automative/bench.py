"""Self-created benchmark suite and the matched-budget promotion gate (L2).

Every finished run can be frozen into a task (repo bundle at the baseline commit + verify contract + budget +
known-achievable score). A bench run evaluates a candidate protocol against the incumbent and the null
protocol on the same tasks, budget, seeds, and model; the gate rule decides, and discrimination filtering
silences tasks that carry no signal.
"""

import hashlib
import json
import os
import shutil
import statistics
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from automative import ledger as ledger_io
from automative.errors import BenchError
from automative.gitops import Git
from automative.paths import automative_home
from automative.protocol import NULL_VERSION, resolve_version
from automative.runloop import Project, RunLoop
from automative.spec import Direction, render_pin
from automative.state import RunStatus

__all__ = [
    'DEFAULT_GATE',
    'BenchCell',
    'BenchRunResult',
    'CallableDriver',
    'ClaudePrintDriver',
    'Driver',
    'GateParams',
    'ManualDriver',
    'TaskSpec',
    'freeze',
    'gate',
    'list_tasks',
    'load_task',
    'normalized_score',
    'run_bench',
    'run_cell',
    'split_for',
]

EPSILON = 0.10
DELTA_FLOOR = 0.02
CACHE_MAX_AGE = timedelta(days=30)
BENCH_REF = 'refs/automative/bench'


class TaskSpec(BaseModel):
    """A frozen benchmark task."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    task_id: str
    schema_version: int = 1
    source_run: str
    source_project: str
    frozen_at: str
    baseline_commit: str
    bundle: str = 'repo.bundle'
    scope: tuple[str, ...]
    direction: Direction
    verify: str
    timeout_s: int
    repeats: int
    iterations: int
    minutes: int
    baseline_score: float
    known_achievable: float
    noise_floor: float
    cost_class: str
    requirements: tuple[str, ...] = ()
    split: str
    informative: bool = True
    informative_reason: str = ''
    history: tuple[dict[str, object], ...] = ()

    @property
    def signal_range(self) -> float:
        """Absolute distance from baseline to the known-achievable score (0 when the source run found nothing)."""
        return abs(self.known_achievable - self.baseline_score)


@dataclass(frozen=True, slots=True)
class BenchCell:
    """One (task, version, seed) evaluation."""

    task_id: str
    version: str
    seed: int
    run_id: str | None
    baseline: float | None
    best: float | None
    iterations: int
    keeps: int
    integrity_events: int
    normalized: float
    status: str
    wall_clock_s: float
    model: str | None = None
    cached: bool = False


@dataclass(frozen=True, slots=True)
class GateParams:
    """Tunable gate constants."""

    epsilon: float = EPSILON
    delta_floor: float = DELTA_FLOOR
    min_tasks: int = 6
    min_heldout: int = 3
    max_agent_iterations: int = 600
    max_wall_clock_s: int = 6 * 3600


DEFAULT_GATE = GateParams()


@dataclass(frozen=True, slots=True)
class GateResult:
    """Outcome of the promotion rule."""

    passed: bool
    reasons: tuple[str, ...]
    heldout_mean: dict[str, float]
    train_mean: dict[str, float]
    per_task: dict[str, dict[str, float]]
    delta: float
    cost_iterations: int
    cost_wall_clock_s: float
    stage_reached: int


@dataclass(frozen=True, slots=True)
class BenchRunResult:
    """Everything a bench run produced."""

    bench_run_id: str
    candidate: str
    incumbent: str
    cells: tuple[BenchCell, ...]
    gate: GateResult
    path: Path


# ----- tasks ---------------------------------------------------------------------------------------------


def tasks_dir() -> Path:
    return automative_home() / 'bench' / 'tasks'


def results_dir() -> Path:
    return automative_home() / 'bench' / 'results'


def split_for(task_id: str) -> str:
    """Deterministic held-out assignment: ~1/3 of tasks."""
    return 'heldout' if int(hashlib.sha256(task_id.encode()).hexdigest()[:8], 16) % 3 == 0 else 'train'


def _cost_class(runtime_s: float, requirements: tuple[str, ...]) -> str:
    if requirements:
        return 'expensive'
    if runtime_s < 60:
        return 'cheap'
    return 'medium' if runtime_s < 600 else 'expensive'


def freeze(project: Project, run_id: str, *, requirements: tuple[str, ...] = ()) -> TaskSpec:
    """Freeze a finished run into a benchmark task."""
    rows = ledger_io.read(project.paths.ledger_file)
    start = next((r for r in rows if isinstance(r, ledger_io.RunStartRow) and r.run_id == run_id), None)
    if start is None:
        raise BenchError(f'No run_start row for {run_id}')
    iters = [r for r in rows if isinstance(r, ledger_io.IterationRow) and r.run_id == run_id]
    summary = ledger_io.summarize(rows, run_id)
    spec = project.doc.spec
    task_id = f'{project.paths.root.name}-{start.baseline_commit[:7]}-{run_id.rsplit("-", 1)[-1]}'.lower()
    target = tasks_dir() / task_id
    if target.exists():
        raise BenchError(f'Task {task_id} already exists')
    target.mkdir(parents=True)
    ref = f'{BENCH_REF}/{task_id}'
    project.git.update_ref(ref, start.baseline_commit)
    project.git.run('bundle', 'create', str(target / 'repo.bundle'), ref)
    runtimes = [r.verify.runtime_s for r in iters] or [0.0]
    samples = start.baseline.samples
    noise = statistics.pstdev(samples) if len(samples) > 1 else 0.0
    if noise == 0.0:
        noise = spec.metric.threshold.amount(start.baseline.score)
    task = TaskSpec(
        task_id=task_id,
        source_run=run_id,
        source_project=project.paths.root.name,
        frozen_at=datetime.now(UTC).isoformat(timespec='seconds'),
        baseline_commit=start.baseline_commit,
        scope=spec.scope,
        direction=spec.metric.direction,
        verify=spec.metric.verify,
        timeout_s=spec.metric.timeout_s,
        repeats=spec.metric.repeats,
        iterations=int(start.budget.get('iterations') or spec.budget.iterations) or 30,
        minutes=int(start.budget.get('minutes') or spec.budget.minutes) or 120,
        baseline_score=start.baseline.score,
        known_achievable=summary.best if summary.best is not None else start.baseline.score,
        noise_floor=noise,
        cost_class=_cost_class(statistics.median(runtimes), requirements),
        requirements=requirements,
        split=split_for(task_id),
    )
    (target / 'task.json').write_text(task.model_dump_json(indent=2), encoding='utf-8')
    shutil.copy(project.paths.spec_file, target / 'AUTOMATIVE.md')
    return task


def load_task(task_id: str) -> TaskSpec:
    path = tasks_dir() / task_id / 'task.json'
    try:
        return TaskSpec.model_validate_json(path.read_text(encoding='utf-8'))
    except (OSError, ValidationError, ValueError) as exc:
        raise BenchError(f'Cannot load task {task_id}: {exc}') from exc


def save_task(task: TaskSpec) -> None:
    (tasks_dir() / task.task_id / 'task.json').write_text(task.model_dump_json(indent=2), encoding='utf-8')


def list_tasks(*, include_expensive: bool = False) -> tuple[TaskSpec, ...]:
    if not tasks_dir().is_dir():
        return ()
    tasks = [load_task(p.name) for p in sorted(tasks_dir().iterdir()) if (p / 'task.json').is_file()]
    return tuple(t for t in tasks if include_expensive or t.cost_class != 'expensive')


def normalized_score(task: TaskSpec, best: float | None) -> float:
    """``s = clip((best - baseline)/(known - baseline), -1, 1.5)``, direction-aware."""
    if best is None:
        return -1.0
    sign = -1.0 if task.direction is Direction.LOWER else 1.0
    improvement = (best - task.baseline_score) * sign
    denominator = task.signal_range
    if denominator == 0:
        return 1.0 if improvement > 0 else (0.0 if improvement == 0 else -1.0)
    return max(-1.0, min(1.5, improvement / denominator))


# ----- drivers -------------------------------------------------------------------------------------------


class Driver(Protocol):
    """Runs an agent against a prepared worktree until the run is done."""

    @property
    def name(self) -> str: ...

    def drive(self, worktree: Path, task: TaskSpec, version: str, seed: int) -> None: ...


@dataclass(frozen=True, slots=True)
class CallableDriver:
    """Test/FakeAgent driver: a Python callable iterates the run loop directly."""

    fn: Callable[[RunLoop, TaskSpec, str, int], None]
    name: str = 'callable'

    def drive(self, worktree: Path, task: TaskSpec, version: str, seed: int) -> None:
        self.fn(RunLoop(Project.load(worktree)), task, version, seed)


@dataclass(frozen=True, slots=True)
class ManualDriver:
    """Prints the command for a human/agent to run and waits for the run to finish."""

    poll_s: float = 5.0
    name: str = 'manual'
    announce: Callable[[str], None] = print

    def drive(self, worktree: Path, task: TaskSpec, version: str, seed: int) -> None:
        self.announce(
            f'[bench] task {task.task_id} protocol {version} seed {seed}: run your agent in {worktree} '
            f'(`cd {worktree} && /automative`), then wait; polling until the run is done.'
        )
        deadline = time.monotonic() + task.minutes * 60 + 600
        while time.monotonic() < deadline:
            state = RunLoop(Project.load(worktree)).load_state()
            if state is not None and state.status is RunStatus.DONE:
                return
            time.sleep(self.poll_s)


@dataclass(frozen=True, slots=True)
class ClaudePrintDriver:
    """Headless Claude Code (`claude -p`) with the plugin loaded from ``plugin_root``."""

    plugin_root: Path
    model: str | None = None
    permission_mode: str = 'bypassPermissions'
    max_turns: int = 400
    name: str = 'claude-p'
    extra_args: tuple[str, ...] = ()

    def drive(self, worktree: Path, task: TaskSpec, version: str, seed: int) -> None:
        prompt = (
            'You are running an automative benchmark cell. Run `automative session brief`, read the pinned '
            'protocol it names, and follow it until the harness reports the run is done, then run '
            '`automative run end`. Do not stop early and do not ask questions.'
        )
        cmd = [
            'claude',
            '-p',
            prompt,
            '--plugin-dir',
            str(self.plugin_root),
            '--permission-mode',
            self.permission_mode,
            '--output-format',
            'json',
            '--max-turns',
            str(self.max_turns),
            *self.extra_args,
        ]
        if self.model:
            cmd += ['--model', self.model]
        timeout = task.minutes * 60 + 900
        env = dict(os.environ)
        venv_bin = self.plugin_root / '.venv' / 'bin'
        if venv_bin.is_dir():
            env['PATH'] = f'{venv_bin}{os.pathsep}{env.get("PATH", "")}'
        env['AUTOMATIVE_PLUGIN_ROOT'] = str(self.plugin_root)
        try:
            subprocess.run(cmd, cwd=worktree, capture_output=True, text=True, timeout=timeout, check=False, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BenchError(f'claude -p failed for {task.task_id}: {exc}') from exc


# ----- cells ---------------------------------------------------------------------------------------------


def _materialize(task: TaskSpec, version: str, seed: int, root: Path) -> Path:
    """Restore the bundle into a fresh worktree, pin the protocol, disable hook requirement, commit."""
    worktree = root / f'{task.task_id}-{version}-s{seed}'
    worktree.mkdir(parents=True)
    git = Git(worktree)
    git.run('init', '-q', '-b', 'bench-init')
    git.run('config', 'user.email', 'bench@automative.local')
    git.run('config', 'user.name', 'automative-bench')
    git.run('config', 'commit.gpgsign', 'false')
    bundle = tasks_dir() / task.task_id / task.bundle
    git.run('fetch', '-q', str(bundle), f'{BENCH_REF}/{task.task_id}:refs/heads/main')
    git.run('checkout', '-q', 'main')
    spec_path = worktree / 'AUTOMATIVE.md'
    text = render_pin(spec_path.read_text(encoding='utf-8'), version)
    text = text.replace('require_hooks: true', 'require_hooks: false')
    spec_path.write_text(text, encoding='utf-8')
    git.run('add', 'AUTOMATIVE.md')
    git.run('commit', '-q', '--no-verify', '--allow-empty', '-m', f'automative(bench): pin {version} seed {seed}')
    return worktree


def _cache_path(version: str, task_id: str, seed: int) -> Path:
    return results_dir() / 'cache' / version / task_id / f'{seed}.json'


def _cached(version: str, task_id: str, seed: int, model: str | None) -> BenchCell | None:
    path = _cache_path(version, task_id, seed)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return None
    stamp = datetime.fromisoformat(data.get('ts', '1970-01-01T00:00:00+00:00'))
    if datetime.now(UTC) - stamp > CACHE_MAX_AGE or data.get('model') != model:
        return None
    cell = data['cell']
    return BenchCell(**{**cell, 'cached': True})


def _store(cell: BenchCell, model: str | None) -> None:
    path = _cache_path(cell.version, cell.task_id, cell.seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'ts': datetime.now(UTC).isoformat(), 'model': model, 'cell': _cell_dict(cell)}
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def _cell_dict(cell: BenchCell) -> dict[str, object]:
    return {
        'task_id': cell.task_id,
        'version': cell.version,
        'seed': cell.seed,
        'run_id': cell.run_id,
        'baseline': cell.baseline,
        'best': cell.best,
        'iterations': cell.iterations,
        'keeps': cell.keeps,
        'integrity_events': cell.integrity_events,
        'normalized': cell.normalized,
        'status': cell.status,
        'wall_clock_s': cell.wall_clock_s,
        'model': cell.model,
    }


def run_cell(
    task: TaskSpec,
    version: str,
    seed: int,
    driver: Driver,
    *,
    scratch: Path,
    model: str | None = None,
    use_cache: bool = True,
) -> BenchCell:
    """Evaluate one cell (materialize to start run to drive agent to read ledger)."""
    if use_cache:
        cached = _cached(version, task.task_id, seed, model)
        if cached is not None:
            return cached
    resolve_version(version)
    worktree = _materialize(task, version, seed, scratch)
    loop = RunLoop(Project.load(worktree))
    started = time.monotonic()
    state = loop.start(
        f'bench-{seed}',
        iterations=task.iterations,
        minutes=task.minutes,
        bench_task=task.task_id,
        seed=seed,
        model=model,
    )
    driver.drive(worktree, task, version, seed)
    loop = RunLoop(Project.load(worktree))
    final = loop.require_state()
    if final.status is not RunStatus.DONE:
        loop.end('bench driver returned')
        final = loop.require_state()
    rows = ledger_io.read(loop.paths.ledger_file)
    summary = ledger_io.summarize(rows, state.run_id)
    integrity = sum(
        1
        for r in rows
        if isinstance(r, ledger_io.EventRow)
        and r.run_id == state.run_id
        and r.event in ('integrity', 'tamper', 'denied', 'config_drift', 'hooks_dead')
    )
    best = final.best.score if final.best else None
    cell = BenchCell(
        task_id=task.task_id,
        version=version,
        seed=seed,
        run_id=state.run_id,
        baseline=final.baseline.score if final.baseline else None,
        best=best,
        iterations=summary.iterations,
        keeps=summary.keeps,
        integrity_events=integrity,
        normalized=normalized_score(task, best),
        status=final.stop_reason or 'done',
        wall_clock_s=time.monotonic() - started,
        model=model,
    )
    _store(cell, model)
    return cell


# ----- gate ----------------------------------------------------------------------------------------------


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _per_task_means(cells: Sequence[BenchCell]) -> dict[str, dict[str, float]]:
    """``{task_id: {version: mean normalized over seeds}}``."""
    table: dict[str, dict[str, list[float]]] = {}
    for cell in cells:
        table.setdefault(cell.task_id, {}).setdefault(cell.version, []).append(cell.normalized)
    return {task: {v: _mean(s) for v, s in versions.items()} for task, versions in table.items()}


def _pooled_noise(tasks: Sequence[TaskSpec]) -> float:
    ratios = [t.noise_floor / t.signal_range for t in tasks if t.signal_range > 0]
    return _mean(ratios) if ratios else 0.0


def _tokens(version: str) -> int:
    path = resolve_version(version).path
    return sum(int(len(p.read_text(encoding='utf-8').split()) * 1.3) for p in path.rglob('*.md'))


def gate(
    candidate: str,
    incumbent: str,
    tasks: Sequence[TaskSpec],
    cells: Sequence[BenchCell],
    params: GateParams = DEFAULT_GATE,
    *,
    stage_reached: int,
) -> GateResult:
    """Apply the promotion rule to the cells gathered so far."""
    reasons: list[str] = []
    informative = [t for t in tasks if t.informative]
    heldout = [t.task_id for t in informative if t.split == 'heldout']
    train = [t.task_id for t in informative if t.split == 'train']
    delta = max(params.delta_floor, _pooled_noise(informative))
    table = _per_task_means(cells)

    def mean_over(ids: Sequence[str], version: str) -> float:
        return _mean([table[t][version] for t in ids if t in table and version in table[t]])

    h_c, h_i, h_null = mean_over(heldout, candidate), mean_over(heldout, incumbent), mean_over(heldout, NULL_VERSION)
    t_c, t_i = mean_over(train, candidate), mean_over(train, incumbent)
    if len(informative) < params.min_tasks or len(heldout) < params.min_heldout:
        reasons.append(f'insufficient benchmark: {len(informative)} informative tasks / {len(heldout)} held-out')
    simpler = _tokens(candidate) <= 0.9 * _tokens(incumbent)
    if not (h_c >= h_i + delta or (h_c >= h_i - delta and simpler)):
        reasons.append(f'held-out mean {h_c:.3f} does not beat incumbent {h_i:.3f} by delta={delta:.3f}')
    for task_id in heldout + train:
        row = table.get(task_id, {})
        if candidate in row and incumbent in row and row[candidate] < row[incumbent] - params.epsilon:
            reasons.append(
                f'task {task_id} regressed {row[incumbent]:.2f} to {row[candidate]:.2f} (epsilon={params.epsilon})'
            )
    if t_c < t_i - delta:
        reasons.append(f'train mean {t_c:.3f} below incumbent {t_i:.3f} - delta')
    if t_c > t_i + 3 * delta and h_c <= h_i:
        reasons.append('overfit: train improved > 3delta while held-out did not')
    if any(c.integrity_events for c in cells if c.version == candidate):
        reasons.append('integrity events during candidate runs')
    cost_iterations = sum(c.iterations for c in cells if not c.cached)
    cost_wall = sum(c.wall_clock_s for c in cells if not c.cached)
    if cost_iterations > params.max_agent_iterations or cost_wall > params.max_wall_clock_s:
        reasons.append('cost caps exceeded')
    null_cells = [c for c in cells if c.version == NULL_VERSION and c.task_id in heldout]
    if null_cells and not h_c >= h_null + delta:
        reasons.append(f'candidate {h_c:.3f} does not beat the null protocol {h_null:.3f} by delta on held-out')
    return GateResult(
        passed=not reasons,
        reasons=tuple(reasons),
        heldout_mean={candidate: h_c, incumbent: h_i, NULL_VERSION: h_null},
        train_mean={candidate: t_c, incumbent: t_i},
        per_task=table,
        delta=delta,
        cost_iterations=cost_iterations,
        cost_wall_clock_s=cost_wall,
        stage_reached=stage_reached,
    )


def _update_discrimination(tasks: Sequence[TaskSpec], cells: Sequence[BenchCell]) -> None:
    """Silence tasks where every versionxseed lands in one noise band or everyone saturates."""
    by_task: dict[str, list[BenchCell]] = {}
    for cell in cells:
        by_task.setdefault(cell.task_id, []).append(cell)
    for task in tasks:
        group = by_task.get(task.task_id, [])
        versions = {c.version for c in group}
        if len(versions) < 2:
            continue
        values = [c.normalized for c in group]
        band = task.noise_floor / task.signal_range if task.signal_range else 0.0
        history = (*task.history, *({'version': c.version, 'seed': c.seed, 'normalized': c.normalized} for c in group))
        if all(c.status == 'consecutive_errors' for c in group):
            save_task(
                task.model_copy(update={'informative': False, 'informative_reason': 'broken', 'history': history})
            )
        elif max(values) - min(values) <= max(band, 1e-9) or min(values) >= 1.0:
            save_task(
                task.model_copy(
                    update={'informative': False, 'informative_reason': 'no discrimination', 'history': history}
                )
            )
        else:
            save_task(task.model_copy(update={'informative': True, 'informative_reason': '', 'history': history}))


def run_bench(
    candidate: str,
    incumbent: str,
    driver: Driver,
    *,
    seeds: int = 2,
    tasks: Sequence[TaskSpec] | None = None,
    params: GateParams = DEFAULT_GATE,
    model: str | None = None,
    use_cache: bool = True,
    announce: Callable[[str], None] = lambda _: None,
) -> BenchRunResult:
    """Run the cost cascade and apply the gate; writes results under ``bench/results/<id>/``."""
    tasks = tuple(tasks if tasks is not None else list_tasks())
    if not tasks:
        raise BenchError('No benchmark tasks; freeze some runs first (`automative bench freeze --run ...`)')
    train = [t for t in tasks if t.split == 'train' and t.informative]
    heldout = [t for t in tasks if t.split == 'heldout' and t.informative]
    bench_run_id = f'b-{datetime.now(UTC).strftime("%Y%m%d-%H%M%S")}-{candidate}'
    out = results_dir() / bench_run_id
    out.mkdir(parents=True, exist_ok=True)
    cells: list[BenchCell] = []
    stage = 0
    with tempfile.TemporaryDirectory(prefix='automative-bench-') as tmp:
        scratch = Path(tmp)

        def cell(task: TaskSpec, version: str, seed: int) -> None:
            announce(f'[bench] {task.task_id} x {version} x seed {seed}')
            cells.append(run_cell(task, version, seed, driver, scratch=scratch, model=model, use_cache=use_cache))

        stage = 1
        for task in train:
            cell(task, candidate, 1)
            cell(task, incumbent, 1)
        early = gate(candidate, incumbent, tasks, cells, params, stage_reached=stage)
        if train and early.train_mean[candidate] < early.train_mean[incumbent] - early.delta:
            result = early
        else:
            stage = 2
            for task in heldout:
                for seed in range(1, seeds + 1):
                    cell(task, candidate, seed)
                    cell(task, incumbent, seed)
                cell(task, NULL_VERSION, 1)
            stage = 3
            for task in train:
                for seed in range(2, seeds + 1):
                    cell(task, candidate, seed)
                    cell(task, incumbent, seed)
            result = gate(candidate, incumbent, tasks, cells, params, stage_reached=stage)
    _update_discrimination(tasks, cells)
    payload = {
        'bench_run_id': bench_run_id,
        'candidate': candidate,
        'incumbent': incumbent,
        'seeds': seeds,
        'model': model,
        'cells': [_cell_dict(c) | {'cached': c.cached} for c in cells],
        'gate': {
            'passed': result.passed,
            'reasons': list(result.reasons),
            'heldout_mean': result.heldout_mean,
            'train_mean': result.train_mean,
            'per_task': result.per_task,
            'delta': result.delta,
            'cost_iterations': result.cost_iterations,
            'cost_wall_clock_s': result.cost_wall_clock_s,
            'stage_reached': result.stage_reached,
        },
    }
    (out / 'result.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return BenchRunResult(bench_run_id, candidate, incumbent, tuple(cells), result, out)


@dataclass(frozen=True, slots=True)
class BenchSummary:
    """Loaded result for reporting."""

    payload: dict[str, object] = field(default_factory=dict)


def load_result(bench_run_id: str) -> dict[str, object]:
    path = results_dir() / bench_run_id / 'result.json'
    try:
        return dict(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchError(f'Cannot load bench result {bench_run_id}: {exc}') from exc
