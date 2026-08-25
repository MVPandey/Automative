"""L2 orchestration: propose a candidate from evidence, benchmark it, and record the gate outcome."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from automative import bench as bench_io
from automative import evolution
from automative import ledger as ledger_io
from automative import strategies as strategy_io
from automative.errors import ProtocolError
from automative.protocol import NULL_VERSION, list_versions, resolve_version
from automative.report import render_compact
from automative.runloop import Project

__all__ = ['Proposal', 'benchmark', 'next_version', 'propose']

SEMVER_RE = re.compile(r'^(\d+)\.(\d+)\.(\d+)')


def next_version(parent: str, *, minor: bool = False) -> str:
    """Bump the patch (or minor) component, skipping versions that already exist."""
    match = SEMVER_RE.match(parent)
    if not match:
        raise ProtocolError(f'{parent!r} is not semver')
    major, mnr, patch = (int(g) for g in match.groups())
    existing = {v.version for v in list_versions()}
    while True:
        mnr, patch = (mnr + 1, 0) if minor else (mnr, patch + 1)
        candidate = f'{major}.{mnr}.{patch}'
        if candidate not in existing:
            return candidate


@dataclass(frozen=True, slots=True)
class Proposal:
    """What the agent gets to write the candidate from."""

    version: str
    parent: str
    path: Path
    strategies: tuple[str, ...]
    ledger_digest: str
    incumbent_skill: Path


def propose(project: Project, *, parent: str | None = None, minor: bool = False, rationale: str = '') -> Proposal:
    """Create an editable candidate and gather the evidence the agent should ground its edits in."""
    base = parent or project.doc.spec.protocol
    if base == NULL_VERSION:
        raise ProtocolError('Evolve from a real protocol, not the null baseline')
    version = next_version(base, minor=minor)
    path = evolution.create_candidate(
        base, version, created_by=f'evolve@{project.paths.root.name}', rationale=rationale
    )
    validated = tuple(
        s.line()
        for s in strategy_io.load(project.paths.strategies_file)
        if s.status is strategy_io.StrategyStatus.VALIDATED
    )
    rows = ledger_io.iterations(project.paths.ledger_file)
    digest = render_compact(rows[-40:])
    return Proposal(
        version=version,
        parent=base,
        path=path,
        strategies=validated,
        ledger_digest=digest,
        incumbent_skill=resolve_version(base).skill_file,
    )


def benchmark(
    version: str,
    driver: bench_io.Driver,
    *,
    seeds: int = 2,
    model: str | None = None,
    params: bench_io.GateParams = bench_io.DEFAULT_GATE,
    announce: Callable[[str], None] | None = None,
) -> bench_io.BenchRunResult:
    """Validate, run the gate, and stamp the manifest ``gate-passed`` or ``rejected``."""
    report = evolution.validate_candidate(version)
    if not report.ok:
        raise ProtocolError('Candidate is invalid: ' + '; '.join(report.errors))
    incumbent = resolve_version(version).manifest.parent
    assert incumbent is not None
    result = bench_io.run_bench(
        version, incumbent, driver, seeds=seeds, model=model, params=params, announce=announce or (lambda _: None)
    )
    evidence = {
        'bench_run_id': result.bench_run_id,
        'heldout_mean': result.gate.heldout_mean,
        'train_mean': result.gate.train_mean,
        'per_task': result.gate.per_task,
        'seeds': seeds,
        'model': model,
        'delta': result.gate.delta,
        'stage_reached': result.gate.stage_reached,
        'cost_iterations': result.gate.cost_iterations,
    }
    if result.gate.passed:
        evolution.set_manifest_status(version, 'gate-passed', evidence=evidence)
    else:
        evolution.set_manifest_status(version, 'candidate', evidence=evidence)
        evolution.reject(version, '; '.join(result.gate.reasons))
    return result
