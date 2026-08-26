"""Command-line entry point. This is the only module that prints."""

import dataclasses
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer

from automative import __version__, report, scaffold
from automative import bench as bench_io
from automative import ledger as ledger_io
from automative import protocol as protocol_io
from automative import strategies as strategy_io
from automative.errors import AutomativeError
from automative.paths import automative_home
from automative.runloop import Project, RunLoop
from automative.spec import parse_spec, render_pin
from automative.state import RunStatus, run_lock

app = typer.Typer(no_args_is_help=True, add_completion=False, help='Goal-directed iteration harness.')
run_app = typer.Typer(no_args_is_help=True, help='Run lifecycle.')
session_app = typer.Typer(no_args_is_help=True, help='Session recitation.')
protocol_app = typer.Typer(no_args_is_help=True, help='Protocol version store.')
app.add_typer(run_app, name='run')
app.add_typer(session_app, name='session')
app.add_typer(protocol_app, name='protocol')


def _fail(exc: AutomativeError) -> None:
    typer.echo(f'error: {exc}', err=True)
    raise typer.Exit(code=exc.exit_code)


def _loop() -> RunLoop:
    return RunLoop(Project.load())


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool, typer.Option('--version', help='Print the version and exit.', callback=_version_callback, is_eager=True)
    ] = False,
) -> None:
    """Goal-directed iteration harness."""


# ----- init / doctor -------------------------------------------------------------------------------------


@app.command()
def init(
    goal: Annotated[str, typer.Option(help='One-paragraph goal, recited every iteration.')] = 'Improve the metric.',
    metric: Annotated[str, typer.Option(help='Metric name.')] = 'score',
    direction: Annotated[str, typer.Option(help='lower | higher')] = 'lower',
    verify: Annotated[str, typer.Option(help='Command whose last stdout line is the metric.')] = 'echo 0',
    scope: Annotated[list[str] | None, typer.Option(help='Glob the agent may edit (repeatable).')] = None,
    protected: Annotated[list[str] | None, typer.Option(help='Glob to hash-lock (repeatable).')] = None,
    guard: Annotated[list[str] | None, typer.Option(help='Command that must exit 0 for a keep (repeatable).')] = None,
    tag: Annotated[list[str] | None, typer.Option(help='Domain tag for strategy retrieval (repeatable).')] = None,
    timeout_s: int = 600,
    repeats: int = 1,
    min_improvement: str = '0',
    iterations: int = 30,
    minutes: int = 120,
    protocol: Annotated[str | None, typer.Option(help='Protocol version to pin (default: latest bundled).')] = None,
    no_hooks: Annotated[
        bool, typer.Option('--no-hooks', help='Set enforcement.require_hooks: false (agents without hooks).')
    ] = False,
    force: bool = False,
) -> None:
    """Write AUTOMATIVE.md and the .automative/ bookkeeping in the current directory."""
    root = Path.cwd()
    try:
        pin = protocol or _latest_bundled()
        options = scaffold.InitOptions(
            goal=goal,
            metric_name=metric,
            direction=direction,
            verify=verify,
            scope=tuple(scope or ('src/**',)),
            protected=tuple(protected or ()),
            guard=tuple(guard or ()),
            tags=tuple(tag or ()),
            protocol=pin,
            timeout_s=timeout_s,
            repeats=repeats,
            min_improvement=min_improvement,
            iterations=iterations,
            minutes=minutes,
            require_hooks=not no_hooks,
        )
        text = scaffold.render_spec(options)
        parse_spec(text)
        target = scaffold.write_spec(root, text, force=force)
        added = scaffold.ensure_gitignore(root)
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo(f'Wrote {target}')
    if added:
        typer.echo('Added to .gitignore: ' + ', '.join(added))
    typer.echo(
        'Next: fill in the Goal and Context sections, commit, then run `automative doctor` and `automative run start`.'
    )


def _latest_bundled() -> str:
    versions = [v for v in protocol_io.list_versions() if v.version != protocol_io.NULL_VERSION]
    return versions[-1].version if versions else protocol_io.NULL_VERSION


@app.command()
def doctor() -> None:
    """Check git, the spec, the verify command, hooks, and the pinned protocol."""
    problems: list[str] = []
    try:
        project = Project.load()
    except AutomativeError as exc:
        _fail(exc)
        return
    spec = project.doc.spec
    typer.echo(f'Project: {project.paths.root}')
    typer.echo(f'Spec: ok (metric {spec.metric.name}, {spec.metric.direction.value}, protocol {spec.protocol})')
    if not project.git.is_repo():
        problems.append('not a git repository')
    elif not project.git.has_commits():
        problems.append('no commits yet')
    elif not project.git.is_clean():
        problems.append('working tree is dirty')
    try:
        proto = protocol_io.resolve_version(spec.protocol)
        drift = protocol_io.verify_integrity(proto)
        typer.echo(f'Protocol: {proto.skill_file}' + (' (MODIFIED!)' if drift else ''))
        if drift:
            problems.append('pinned protocol files modified: ' + ', '.join(drift))
    except AutomativeError as exc:
        problems.append(str(exc))
    loop = RunLoop(project)
    result = loop.verify_only()
    if result.ok:
        typer.echo(f'Verify: ok, {result.score:g} in {result.runtime_s:.1f}s')
    else:
        problems.append(f'verify failed ({result.outcome.value}): {result.tail[-300:]}')
    typer.echo('Claude CLI: ' + ('found' if shutil.which('claude') else 'not on PATH (needed for bench driver only)'))
    if problems:
        for problem in problems:
            typer.echo(f'PROBLEM: {problem}', err=True)
        raise typer.Exit(code=1)
    typer.echo('All checks passed.')


# ----- run lifecycle -------------------------------------------------------------------------------------


@run_app.command('start')
def run_start(
    name: Annotated[str | None, typer.Option(help='Slug for the run id and branch.')] = None,
    iterations: Annotated[int | None, typer.Option(help='Override the iteration budget for this run.')] = None,
    minutes: Annotated[int | None, typer.Option(help='Override the wall-clock budget for this run.')] = None,
    bench: Annotated[str | None, typer.Option(help='Benchmark task id (headless bench runs).')] = None,
    seed: Annotated[int | None, typer.Option()] = None,
    session_id: Annotated[str | None, typer.Option()] = None,
    model: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Measure the baseline, create the run branch, lock protected files, and record run_start."""
    try:
        loop = _loop()
        with run_lock(loop.paths.dotdir):
            state = loop.start(
                name,
                iterations=iterations,
                minutes=minutes,
                bench_task=bench,
                seed=seed,
                session_id=session_id,
                model=model,
            )
        typer.echo(f'Started {state.run_id} on branch {state.branch}; baseline {state.baseline.score:g}')  # type: ignore[union-attr]
        typer.echo(report.render_brief(loop.brief()))
    except AutomativeError as exc:
        _fail(exc)


@run_app.command('pause')
def run_pause() -> None:
    """Pause the run; the Stop hook stops blocking until `run resume`."""
    try:
        loop = _loop()
        with run_lock(loop.paths.dotdir):
            state = loop.pause()
        typer.echo(f'Paused {state.run_id}')
    except AutomativeError as exc:
        _fail(exc)


@run_app.command('resume')
def run_resume(reverify: bool = False) -> None:
    """Repair a crashed or paused run and make it active again."""
    try:
        loop = _loop()
        with run_lock(loop.paths.dotdir):
            state = loop.resume(reverify=reverify)
        typer.echo(f'Resumed {state.run_id}')
        typer.echo(report.render_brief(loop.brief()))
    except AutomativeError as exc:
        _fail(exc)


@run_app.command('end')
def run_end(reason: str = '') -> None:
    """Finish the run, print the summary, and commit the ledger."""
    try:
        loop = _loop()
        with run_lock(loop.paths.dotdir):
            summary = loop.end(reason)
        state = loop.require_state()
        typer.echo(report.render_summary(summary))
        if state.best:
            typer.echo(f'Best commit {state.best.commit} on {state.branch}; merge with: git merge {state.branch}')
    except AutomativeError as exc:
        _fail(exc)


# ----- brief / try / discard / verify --------------------------------------------------------------------


def _print_brief(as_json: bool) -> None:
    try:
        brief, text = _loop().brief_text(as_json=as_json)
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo(text)
    raise typer.Exit(code=brief.exit_code)


@session_app.command('brief')
def session_brief(json_: Annotated[bool, typer.Option('--json')] = False) -> None:
    """Print the recitation block. Exit 0 active, 3 done, 4 no run, 5 paused."""
    _print_brief(json_)


@app.command('status')
def status(json_: Annotated[bool, typer.Option('--json')] = False) -> None:
    """Alias for `session brief`."""
    _print_brief(json_)


@app.command('try')
def try_(
    message: Annotated[str, typer.Option('-m', '--message', help='What changed, one sentence.')],
    hypothesis: Annotated[str, typer.Option('--hypothesis', help='Why it should move the metric.')],
    predict: Annotated[str | None, typer.Option('--predict', help='Predicted delta (e.g. -30% or -0.01).')] = None,
    strategies: Annotated[
        str | None, typer.Option('--strategies', help='Comma-separated strategy ids applied.')
    ] = None,
    mode: Annotated[str, typer.Option('--mode', help='draft | improve | debug')] = 'improve',
) -> None:
    """Commit the in-scope diff, verify, decide, revert on non-keep, and log the result."""
    ids = tuple(s.strip() for s in (strategies or '').split(',') if s.strip())
    try:
        loop = _loop()
        with run_lock(loop.paths.dotdir):
            outcome = loop.try_change(message, hypothesis, predict=predict, strategy_ids=ids, mode=mode)
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo(outcome.text)


@app.command()
def checkout(iteration: Annotated[int, typer.Argument(help='Attempt number; 0 is the baseline.')]) -> None:
    """Restore attempt N's in-scope files so the next try builds on it instead of on the best."""
    try:
        loop = _loop()
        with run_lock(loop.paths.dotdir):
            rev, files = loop.checkout(iteration)
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo(f'Working tree now matches i{iteration} ({rev}); changed: ' + (', '.join(files) or '(nothing)'))
    typer.echo(f'The next `automative try` records i{iteration} as its parent. `automative discard` undoes this.')


@app.command()
def tree(run: Annotated[str | None, typer.Option('--run')] = None) -> None:
    """Show the attempt tree: every try under the attempt it was built from."""
    try:
        loop = _loop()
        state = loop.load_state()
        run_id = run or (state.run_id if state else None)
        rows = ledger_io.iterations(loop.paths.ledger_file, run_id)
        baseline = None
        for row in ledger_io.read(loop.paths.ledger_file):
            if isinstance(row, ledger_io.RunStartRow) and row.run_id == run_id:
                baseline = row.baseline.score
        text = report.render_tree(rows, baseline)
        loop.record_shown('tree', text, store_text=False, args={'run': run_id})
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo(text)


@app.command()
def audit(
    run: Annotated[str | None, typer.Option('--run')] = None,
    json_: Annotated[bool, typer.Option('--json')] = False,
) -> None:
    """Replay the ledger: was every try made against a view the agent was actually shown?"""
    from automative import audit as audit_io  # noqa: PLC0415 - keep base CLI import light

    try:
        loop = _loop()
        state = loop.load_state()
        run_id = run or (state.run_id if state else None)
        if run_id is None:
            raise AutomativeError('No run to audit')
        result = audit_io.audit(ledger_io.read(loop.paths.ledger_file), run_id)
    except AutomativeError as exc:
        _fail(exc)
        return
    if json_:
        typer.echo(json.dumps(dataclasses.asdict(result) | {'ok': result.ok}))
    else:
        typer.echo(report.render_audit(result))
    if not result.ok:
        raise typer.Exit(code=20)


@app.command()
def discard(reason: str = '') -> None:
    """Abandon the current attempt: restore in-scope files and revert a pending commit."""
    try:
        loop = _loop()
        with run_lock(loop.paths.dotdir):
            leftover = loop.discard(reason)
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo('Discarded in-scope changes.')
    if leftover:
        typer.echo('Still dirty outside scope (revert by hand): ' + ', '.join(leftover), err=True)


@app.command()
def verify(json_: Annotated[bool, typer.Option('--json')] = False) -> None:
    """Measure the current tree without committing or deciding."""
    try:
        loop = _loop()
        result = loop.verify_only()
    except AutomativeError as exc:
        _fail(exc)
        return
    if json_:
        text = json.dumps({'outcome': result.outcome.value, 'score': result.score, 'samples': result.samples})
    elif result.ok:
        text = f'{result.score:g}'
    else:
        text = f'{result.outcome.value}: {result.tail[-500:]}'
    loop.record_shown('verify', text, args={'json': json_})
    if result.ok or json_:
        typer.echo(text)
    else:
        typer.echo(text, err=True)
        raise typer.Exit(code=1)


# ----- ledger / report -----------------------------------------------------------------------------------


@app.command()
def ledger(
    last: int = 10,
    status: Annotated[
        str | None, typer.Option('--status', help='Filter by decision (keep, discard, crash, ...).')
    ] = None,
    run: Annotated[str | None, typer.Option('--run')] = None,
    json_: Annotated[bool, typer.Option('--json')] = False,
) -> None:
    """Show iteration rows."""
    try:
        loop = _loop()
        state = loop.load_state()
        run_id = run or (state.run_id if state else None)
        rows = ledger_io.iterations(loop.paths.ledger_file, run_id)
    except AutomativeError as exc:
        _fail(exc)
        return
    if status:
        rows = tuple(r for r in rows if r.decision.value == status)
    rows = rows[-last:] if last > 0 else rows
    text = '\n'.join(row.model_dump_json() for row in rows) if json_ else report.render_ledger(rows)
    loop.record_shown(
        'ledger', text, store_text=False, args={'last': last, 'status': status, 'run': run_id, 'json': json_}
    )
    typer.echo(text)


@app.command('report')
def report_cmd(
    run: Annotated[str | None, typer.Option('--run')] = None,
    heldout: Annotated[
        bool, typer.Option('--heldout', help='Also print the sealed held-out scores (for humans, after the run).')
    ] = False,
) -> None:
    """Summarize a run."""
    from automative import heldout as heldout_io  # noqa: PLC0415 - keep base CLI import light

    try:
        loop = _loop()
        state = loop.load_state()
        run_id = run or (state.run_id if state else None)
        if run_id is None:
            raise AutomativeError('No run to report on')
        summary = ledger_io.summarize(ledger_io.read(loop.paths.ledger_file), run_id)
    except AutomativeError as exc:
        _fail(exc)
        return
    text = report.render_summary(summary)
    loop.record_shown('report', text, store_text=False, args={'run': run_id})
    typer.echo(text)
    if heldout:
        records = heldout_io.read(loop.paths.heldout_file, run_id)
        kept = {r.iter for r in ledger_io.iterations(loop.paths.ledger_file, run_id) if r.decision.value == 'keep'}
        typer.echo(report.render_heldout(records, kept))


# ----- protocol store ------------------------------------------------------------------------------------


@protocol_app.command('list')
def protocol_list() -> None:
    """List installed protocol versions."""
    for version in protocol_io.list_versions():
        where = 'bundled' if version.bundled else 'user'
        typer.echo(f'{version.version:<12} {version.manifest.status:<10} {where:<8} {version.path}')


@protocol_app.command('show')
def protocol_show(version: Annotated[str | None, typer.Argument()] = None, path_only: bool = False) -> None:
    """Print the pinned (or given) protocol's SKILL.md path and text."""
    try:
        pinned = version or Project.load().doc.spec.protocol
        proto = protocol_io.resolve_version(pinned)
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo(str(proto.skill_file))
    if not path_only:
        typer.echo(proto.skill_file.read_text(encoding='utf-8'))


@protocol_app.command('pin')
def protocol_pin(
    version: Annotated[str | None, typer.Argument()] = None,
    previous: Annotated[bool, typer.Option('--previous', help='Pin the parent of the current version.')] = False,
) -> None:
    """Rewrite the `protocol:` line in AUTOMATIVE.md (refused during an active run)."""
    try:
        project = Project.load()
        loop = RunLoop(project)
        state = loop.load_state()
        if state is not None and state.status is not RunStatus.DONE:
            raise AutomativeError('Cannot re-pin while a run is active or paused; end it first')
        current = protocol_io.resolve_version(project.doc.spec.protocol)
        target = current.manifest.parent if previous else version
        if not target:
            raise AutomativeError('Give a version or --previous')
        protocol_io.resolve_version(target)
        project.paths.spec_file.write_text(render_pin(project.doc.raw, target), encoding='utf-8')
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo(f'Pinned protocol {target}')


@protocol_app.command('install')
def protocol_install() -> None:
    """Copy bundled versions into the user store (idempotent)."""
    target_root = protocol_io.user_versions_dir()
    target_root.mkdir(parents=True, exist_ok=True)
    for version in protocol_io.list_versions():
        if version.bundled and not (target_root / version.version).exists():
            shutil.copytree(version.path, target_root / version.version)
            typer.echo(f'Installed {version.version} to {target_root / version.version}')
    typer.echo(f'Store: {target_root}')


@protocol_app.command('seal', hidden=True)
def protocol_seal(
    directory: Path, version: str, parent: Annotated[str | None, typer.Option()] = None, created_by: str = 'human'
) -> None:
    """Write a manifest for a protocol directory (maintainer tool)."""
    try:
        protocol_io.write_manifest(directory, version=version, parent=parent, created_by=created_by)
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo(f'Sealed {directory}')


def main() -> None:
    """Console entry point."""
    try:
        app()
    except AutomativeError as exc:  # pragma: no cover - typer normally catches earlier
        typer.echo(f'error: {exc}', err=True)
        sys.exit(exc.exit_code)


if __name__ == '__main__':
    main()


# ----- hooks ---------------------------------------------------------------------------------------------


@app.command('hook', hidden=True)
def hook(event: str) -> None:
    """Hook entry point: reads the hook JSON from stdin, emits the response, exits accordingly."""
    from automative import hooks as hook_io  # noqa: PLC0415 - keep hook startup lean

    try:
        payload = json.loads(sys.stdin.read() or '{}')
    except json.JSONDecodeError:
        payload = {}
    cwd = Path(str(payload.get('cwd') or Path.cwd()))
    try:
        response = hook_io.handle(event, payload if isinstance(payload, dict) else {}, cwd)
    except Exception as exc:  # hooks must never crash the session
        typer.echo(f'automative hook error: {exc}', err=True)
        raise typer.Exit(code=0) from exc
    if response.stderr:
        typer.echo(response.stderr, err=True)
    if response.stdout_text is not None:
        typer.echo(response.stdout_text)
    elif response.payload is not None:
        typer.echo(json.dumps(response.payload))
    raise typer.Exit(code=response.exit_code)


# ----- strategy catalogue --------------------------------------------------------------------------------

strategy_app = typer.Typer(no_args_is_help=True, help='Strategy catalogue (L1).')
app.add_typer(strategy_app, name='strategy')


@strategy_app.command('add')
def strategy_add(
    action: Annotated[str, typer.Argument(help='The reusable action, one line.')],
    kind: Annotated[str, typer.Option('--kind', help='works | fails | insight | avoid')] = 'insight',
    when: Annotated[str, typer.Option('--when', help='Situation in which it applies.')] = '',
    effect: Annotated[str, typer.Option('--effect', help='Expected effect.')] = '',
    tag: Annotated[list[str] | None, typer.Option('--tag')] = None,
) -> None:
    """Append a strategy (refused if it duplicates a rejected one)."""
    try:
        loop = _loop()
        state = loop.load_state()
        entry = strategy_io.add(
            loop.paths.strategies_file,
            kind=strategy_io.Kind(kind),
            action=action,
            when=when,
            expected_effect=effect,
            tags=tuple(tag or loop.project.doc.spec.tags),
            run=state.run_id if state else None,
            protocol_version=loop.project.doc.spec.protocol,
        )
        if state:
            loop._event(state.run_id, 'learn', entry.line())
    except (AutomativeError, ValueError) as exc:
        typer.echo(f'error: {exc}', err=True)
        raise typer.Exit(code=18) from exc
    typer.echo(entry.line())


@strategy_app.command('show')
def strategy_show(
    status: Annotated[str | None, typer.Option('--status')] = None,
    json_: Annotated[bool, typer.Option('--json')] = False,
) -> None:
    """List catalogue entries."""
    try:
        loop = _loop()
        entries = strategy_io.load(loop.paths.strategies_file)
    except AutomativeError as exc:
        _fail(exc)
        return
    if status:
        entries = tuple(e for e in entries if e.status.value == status)
    for entry in entries:
        typer.echo(json.dumps(dataclasses.asdict(entry), default=str) if json_ else entry.line())
    if not entries:
        typer.echo('(no strategies yet)')


@strategy_app.command('suggest')
def strategy_suggest(k: Annotated[int, typer.Option('-k')] = 5) -> None:
    """Top-k strategies for ideation."""
    try:
        loop = _loop()
        lines = strategy_io.suggest_lines(loop.paths.strategies_file, loop.project.doc.spec.tags, k)
        loop.record_shown('suggest', '\n'.join(lines), args={'k': k})
    except AutomativeError as exc:
        _fail(exc)
        return
    for line in lines:
        typer.echo(line)


@strategy_app.command('set-status')
def strategy_set_status(strategy_id: str, status: str, reason: str = '') -> None:
    """Manually set a status (human curation)."""
    try:
        loop = _loop()
        entry = strategy_io.set_status(
            loop.paths.strategies_file, strategy_id, strategy_io.StrategyStatus(status), reason
        )
    except (AutomativeError, ValueError) as exc:
        typer.echo(f'error: {exc}', err=True)
        raise typer.Exit(code=18) from exc
    typer.echo(entry.line())


@strategy_app.command('merge')
def strategy_merge(keep: str, retire: str) -> None:
    """Retire one entry in favour of another."""
    try:
        loop = _loop()
        entry = strategy_io.merge(loop.paths.strategies_file, keep, retire)
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo(entry.line())


@strategy_app.command('promote-global')
def strategy_promote_global(strategy_id: str) -> None:
    """Copy a validated strategy into the global catalogue."""
    try:
        loop = _loop()
        entry = strategy_io.promote_global(loop.paths.strategies_file, strategy_id)
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo(f'Promoted as {entry.id} (global)')


@app.command('export', hidden=True)
def ledger_export(run: Annotated[str | None, typer.Option('--run')] = None) -> None:
    """Compact ledger export for the reflector."""
    try:
        loop = _loop()
        state = loop.load_state()
        run_id = run or (state.run_id if state else None)
        text = report.render_compact(ledger_io.iterations(loop.paths.ledger_file, run_id))
        loop.record_shown('export', text, store_text=False, args={'run': run_id})
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo(text)


# ----- protocol evolution --------------------------------------------------------------------------------

candidate_app = typer.Typer(no_args_is_help=True, help='Protocol candidates (L2).')
protocol_app.add_typer(candidate_app, name='candidate')


@candidate_app.command('create')
def candidate_create(
    version: str,
    from_: Annotated[str | None, typer.Option('--from', help='Parent version (default: pinned).')] = None,
    rationale: str = '',
) -> None:
    """Copy a parent version into the user store as an editable candidate."""
    from automative import evolution  # noqa: PLC0415 - keep base CLI import light

    try:
        parent = from_ or Project.load().doc.spec.protocol
        path = evolution.create_candidate(parent, version, created_by='agent', rationale=rationale)
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo(f'Candidate {version} created from {parent} at {path}')
    typer.echo('Edit SKILL.md / references/*.md / rules.toml in place, then `automative protocol candidate validate`.')


@candidate_app.command('validate')
def candidate_validate(version: str) -> None:
    """Derive bounded ops from the diff and enforce every limit."""
    from automative import evolution  # noqa: PLC0415

    try:
        result = evolution.validate_candidate(version)
    except AutomativeError as exc:
        _fail(exc)
        return
    for op in result.ops:
        typer.echo(f'{op.op:<16} {op.file}#{op.section}  +{op.lines_added}/-{op.lines_removed} {op.detail}')
    typer.echo(f'SKILL.md {result.skill_lines} lines, ~{result.tokens} tokens')
    if result.ok:
        typer.echo('VALID')
    else:
        for error in result.errors:
            typer.echo(f'INVALID: {error}', err=True)
        raise typer.Exit(code=17)


@protocol_app.command('promote')
def protocol_promote(version: str, confirm: Annotated[bool, typer.Option('--confirm')] = False) -> None:
    """Promote a gate-passed candidate (human only)."""
    from automative import evolution  # noqa: PLC0415

    try:
        manifest = evolution.promote(version, confirm=confirm)
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo(f'Promoted {manifest.version}; pin projects with `automative protocol pin {manifest.version}`')


@protocol_app.command('reject')
def protocol_reject(version: str, reason: Annotated[str, typer.Option('--reason')] = 'rejected') -> None:
    """Reject a candidate and remember it in the rejected buffer."""
    from automative import evolution  # noqa: PLC0415

    try:
        evolution.reject(version, reason)
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo(f'Rejected {version}')


@protocol_app.command('changelog')
def protocol_changelog() -> None:
    """Print the protocol changelog."""
    path = automative_home() / 'protocol' / 'CHANGELOG.md'
    typer.echo(path.read_text(encoding='utf-8') if path.is_file() else '(no changelog yet)')


# ----- bench + evolve ------------------------------------------------------------------------------------

bench_app = typer.Typer(no_args_is_help=True, help='Self-created benchmark suite (L2 gate).')
app.add_typer(bench_app, name='bench')


def _driver(name: str, model: str | None) -> bench_io.Driver:
    root = Path(__file__).resolve().parents[2]
    plugin_root = Path(os.environ.get('AUTOMATIVE_PLUGIN_ROOT') or root)
    if name == 'claude-p':
        return bench_io.ClaudePrintDriver(plugin_root=plugin_root, model=model)
    if name == 'dsh':
        return bench_io.DshHeadlessDriver(plugin_root=plugin_root, model=model)
    if name == 'manual':
        return bench_io.ManualDriver(announce=typer.echo)
    raise AutomativeError(f'Unknown driver {name!r}; use claude-p, dsh, or manual')


@bench_app.command('freeze')
def bench_freeze(
    run: Annotated[str | None, typer.Option('--run')] = None,
    requirement: Annotated[
        list[str] | None, typer.Option('--requirement', help='e.g. 8xH100 (marks expensive).')
    ] = None,
    split: Annotated[
        str | None, typer.Option('--split', help='train | heldout; overrides the hash-based assignment.')
    ] = None,
) -> None:
    """Freeze a finished run into a benchmark task."""
    from automative import bench as bench_io  # noqa: PLC0415

    try:
        loop = _loop()
        state = loop.load_state()
        run_id = run or (state.run_id if state else None)
        if run_id is None:
            raise AutomativeError('No run to freeze')
        task = bench_io.freeze(loop.project, run_id, requirements=tuple(requirement or ()), split=split)
    except AutomativeError as exc:
        _fail(exc)
        return
    typer.echo(
        f'Frozen {task.task_id} ({task.split}, {task.cost_class}): baseline {task.baseline_score:g} to '
        f'known {task.known_achievable:g}, {task.iterations} iterations'
    )


@bench_app.command('list')
def bench_list(all_: Annotated[bool, typer.Option('--all', help='Include expensive tasks.')] = False) -> None:
    """List benchmark tasks."""
    from automative import bench as bench_io  # noqa: PLC0415

    tasks = bench_io.list_tasks(include_expensive=all_)
    if not tasks:
        typer.echo('(no tasks)')
    for task in tasks:
        flag = '' if task.informative else f'  [uninformative: {task.informative_reason}]'
        typer.echo(
            f'{task.task_id:<40} {task.split:<8} {task.cost_class:<9} base {task.baseline_score:g} to '
            f'{task.known_achievable:g}{flag}'
        )


@bench_app.command('run')
def bench_run(
    candidate: Annotated[str, typer.Option('--candidate')],
    incumbent: Annotated[str | None, typer.Option('--incumbent', help='Default: candidate parent.')] = None,
    seeds: int = 2,
    driver: Annotated[str, typer.Option('--driver', help='claude-p | dsh | manual')] = 'claude-p',
    model: Annotated[str | None, typer.Option('--model')] = None,
    no_cache: bool = False,
) -> None:
    """Run the matched-budget cascade and print the gate result."""
    from automative import bench as bench_io  # noqa: PLC0415

    try:
        inc = incumbent or protocol_io.resolve_version(candidate).manifest.parent
        if inc is None:
            raise AutomativeError('Give --incumbent; the candidate has no parent')
        result = bench_io.run_bench(
            candidate,
            inc,
            _driver(driver, model),
            seeds=seeds,
            model=model,
            use_cache=not no_cache,
            announce=typer.echo,
        )
    except AutomativeError as exc:
        _fail(exc)
        return
    _print_gate(result)


def _print_gate(result: object) -> None:
    from automative import bench as bench_io  # noqa: PLC0415

    assert isinstance(result, bench_io.BenchRunResult)
    gate = result.gate
    typer.echo(f'Bench {result.bench_run_id}: {result.candidate} vs {result.incumbent} (stage {gate.stage_reached})')
    for task_id, versions in gate.per_task.items():
        typer.echo('  ' + task_id + ': ' + ', '.join(f'{v}={s:.2f}' for v, s in versions.items()))
    typer.echo(f'held-out means: {gate.heldout_mean}, train means: {gate.train_mean}, delta={gate.delta:.3f}')
    typer.echo(f'cost: {gate.cost_iterations} agent iterations, {gate.cost_wall_clock_s / 60:.1f} min')
    if gate.passed:
        typer.echo(f'GATE PASSED to a human may run `automative protocol promote {result.candidate} --confirm`')
    else:
        for reason in gate.reasons:
            typer.echo(f'GATE FAILED: {reason}', err=True)
        raise typer.Exit(code=19)


@bench_app.command('report')
def bench_report(bench_run_id: str) -> None:
    """Print a stored bench result."""
    from automative import bench as bench_io  # noqa: PLC0415

    try:
        typer.echo(json.dumps(bench_io.load_result(bench_run_id), indent=2))
    except AutomativeError as exc:
        _fail(exc)


@app.command('evolve')
def evolve_cmd(
    propose: Annotated[
        bool, typer.Option('--propose', help='Create a candidate and print the evidence to edit from.')
    ] = False,
    bench: Annotated[str | None, typer.Option('--bench', help='Validate + benchmark this candidate version.')] = None,
    from_: Annotated[str | None, typer.Option('--from')] = None,
    minor: bool = False,
    seeds: int = 2,
    driver: Annotated[str, typer.Option('--driver')] = 'claude-p',
    model: Annotated[str | None, typer.Option('--model')] = None,
    rationale: str = '',
) -> None:
    """L2: propose a bounded protocol edit from evidence, or benchmark a candidate through the gate."""
    from automative import evolve as evolve_io  # noqa: PLC0415

    try:
        if propose:
            proposal = evolve_io.propose(Project.load(), parent=from_, minor=minor, rationale=rationale)
            typer.echo(f'Candidate {proposal.version} (from {proposal.parent}) at {proposal.path}')
            typer.echo(f'Incumbent SKILL.md: {proposal.incumbent_skill}')
            typer.echo('Validated strategies:')
            for line in proposal.strategies or ('(none yet: evolve when strategies have been validated)',):
                typer.echo('  ' + line)
            typer.echo('Recent ledger:')
            typer.echo(proposal.ledger_digest or '  (empty)')
            typer.echo(
                'Edit the candidate files in place (<=3 section-level ops, <=40 lines each), then run '
                f'`automative protocol candidate validate {proposal.version}` and '
                f'`automative evolve --bench {proposal.version}`.'
            )
        elif bench:
            result = evolve_io.benchmark(
                bench,
                _driver(driver, model),
                seeds=seeds,
                model=model,
                announce=typer.echo,
            )
            _print_gate(result)
        else:
            raise AutomativeError('Use --propose or --bench VERSION')
    except AutomativeError as exc:
        _fail(exc)
