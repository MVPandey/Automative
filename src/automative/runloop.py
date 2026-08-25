"""The harness proper: starting runs, judging tries, reverting, and recording.

Everything the agent is not allowed to do itself lives here. The agent edits files and calls
``automative try``; this module commits, verifies, decides, reverts, logs, and enforces the budget.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from automative import budget as budget_rules
from automative import evolution
from automative import ledger as ledger_io
from automative import lock as lock_io
from automative import strategies as strategy_io
from automative.decide import GreedyPolicy, Outcome, Policy, is_improvement
from automative.disclosure import disclosure_card
from automative.errors import ContextError, GitError, IntegrityError, ScopeError, StateError, VerifyError
from automative.gitops import Git, ref_for_try
from automative.heartbeat import age_seconds, read_heartbeat
from automative.paths import DOTDIR, ProjectPaths, find_root
from automative.protocol import ProtocolVersion, manifest_sha, resolve_version, verify_integrity
from automative.scope import Classification, classify
from automative.spec import BudgetSpec, Direction, SpecDocument, compute_spec_sha, load_spec
from automative.state import (
    Best,
    Mode,
    Pending,
    RunState,
    RunStatus,
    Score,
    load_state,
    new_run_id,
    now,
    save_state,
)
from automative.verify import GuardResult, GuardStatus, LocalRunner, Runner, VerifyResult, measure, run_guards
from automative.views import Brief, TryOutcome, render_brief, render_try

__all__ = ['Brief', 'Project', 'RunLoop', 'TryOutcome', 'parse_prediction']

SUGGESTIONS_IN_BRIEF = 3

COMMIT_PREFIX = 'automative'
BRANCH_PREFIX = 'automative'
PERCENT_RE = re.compile(r'^([-+]?\d+(?:\.\d+)?)%$')


@dataclass(frozen=True, slots=True)
class Project:
    """A target project: its paths, parsed spec, and git handle."""

    paths: ProjectPaths
    doc: SpecDocument
    git: Git

    @classmethod
    def load(cls, start: Path | None = None) -> 'Project':
        paths = find_root(start)
        return cls(paths=paths, doc=load_spec(paths.spec_file), git=Git(paths.root))

    def reload(self) -> 'Project':
        return replace(self, doc=load_spec(self.paths.spec_file))


def parse_prediction(text: str | None, best: float) -> float | None:
    """Turn ``--predict`` (``-30%`` or an absolute delta) into a signed delta in metric units."""
    if text is None or not text.strip():
        return None
    raw = text.strip()
    match = PERCENT_RE.match(raw)
    try:
        if match:
            return float(match.group(1)) / 100.0 * abs(best)
        return float(raw)
    except ValueError as exc:
        raise ScopeError(f'--predict must be a number or a percent like -30%, got {text!r}') from exc


class RunLoop:
    """Owns every state transition of a run."""

    def __init__(
        self,
        project: Project,
        runner: Runner | None = None,
        policy: Policy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.project = project
        self.runner: Runner = runner or LocalRunner()
        self.policy: Policy = policy or GreedyPolicy()
        self.clock = clock or now

    # ----- helpers -------------------------------------------------------------------------------------

    @property
    def paths(self) -> ProjectPaths:
        return self.project.paths

    @property
    def git(self) -> Git:
        return self.project.git

    def load_state(self) -> RunState | None:
        return load_state(self.paths.state_file)

    def require_state(self) -> RunState:
        state = self.load_state()
        if state is None:
            raise StateError('No run in this project; run `automative run start`')
        return state

    def _save(self, state: RunState) -> None:
        save_state(self.paths.state_file, state)

    def _elapsed_s(self, state: RunState) -> float:
        elapsed = (self.clock() - state.started_at).total_seconds() - state.paused_total_s
        if state.paused_at is not None:
            elapsed -= (self.clock() - state.paused_at).total_seconds()
        return max(0.0, elapsed)

    def _event(self, run_id: str, event: str, detail: str = '', **data: object) -> None:
        ledger_io.append(
            self.paths.ledger_file,
            ledger_io.EventRow(run_id=run_id, ts=self.clock(), event=event, detail=detail, data=dict(data)),
        )

    def _protocol(self) -> ProtocolVersion:
        return resolve_version(self.project.doc.spec.protocol)

    # ----- what the agent sees --------------------------------------------------------------------------

    def record_shown(
        self, surface: str, text: str, *, store_text: bool = True, args: dict[str, object] | None = None
    ) -> ledger_io.ShownRow | None:
        """Log a model-visible text against the current view of the run. No run, no row."""
        state = self.load_state()
        if state is None:
            return None
        row = ledger_io.ShownRow(
            run_id=state.run_id,
            ts=self.clock(),
            surface=surface,
            context_sha=state.view_sha(),
            sha256=ledger_io.text_sha(text),
            chars=len(text),
            text=text if store_text else None,
            args=dict(args or {}),
        )
        ledger_io.append(self.paths.ledger_file, row)
        return row

    def _check_context(self, state: RunState) -> None:
        """Refuse a try made against a view the harness never showed, or showed before the run changed."""
        if not self.project.doc.spec.enforcement.logged_context:
            return
        last = ledger_io.last_shown(self.paths.ledger_file, state.run_id)
        current = state.view_sha()
        if last is not None and last.context_sha == current:
            return
        detail = (
            'no view of the run has been shown since it last changed'
            if last is None
            else f'last shown view {last.context_sha} ({last.surface}) is not the current view {current}'
        )
        self._event(state.run_id, 'stale_context', detail)
        raise ContextError(f'Refusing to try: {detail}. Run `automative session brief`, read it, then try again.')

    def brief_text(self, *, as_json: bool = False) -> tuple[Brief, str]:
        """Render the brief with strategy suggestions and record it as shown."""
        suggestions = strategy_io.suggest_lines(
            self.paths.strategies_file, self.project.doc.spec.tags, SUGGESTIONS_IN_BRIEF
        )
        brief = self.brief(suggestions)
        if as_json:
            payload = {
                k: (v if not hasattr(v, 'model_dump') else v.model_dump(mode='json')) for k, v in brief.__dict__.items()
            }
            payload['recent'] = [r.model_dump(mode='json') for r in brief.recent]
            text = json.dumps(payload, default=str)
        else:
            text = render_brief(brief)
        self.record_shown('brief', text, args={'json': as_json})
        return brief, text

    def _classify_dirty(self) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """Split dirty paths into (in_scope, protected, other); ``.automative/`` bookkeeping is ignored."""
        spec = self.project.doc.spec
        in_scope: list[str] = []
        protected: list[str] = []
        other: list[str] = []
        for path in self.git.dirty_files():
            if path.startswith(f'{DOTDIR}/'):
                continue
            kind = classify(path, spec.scope, spec.protected)
            if kind is Classification.IN_SCOPE:
                in_scope.append(path)
            elif kind is Classification.PROTECTED:
                protected.append(path)
            else:
                other.append(path)
        return tuple(in_scope), tuple(protected), tuple(other)

    def _check_integrity(self, state: RunState) -> None:
        """Raise and halt the run if the spec, protected files, or pinned protocol changed."""
        doc = load_spec(self.paths.spec_file)
        problems: list[str] = []
        if compute_spec_sha(doc.raw) != state.spec_sha:
            problems.append('AUTOMATIVE.md changed during the run')
        changed = lock_io.check(self.paths.root, state.protected)
        if changed:
            problems.append('protected files modified: ' + ', '.join(changed))
        try:
            protocol = self._protocol()
            drift = verify_integrity(protocol)
            if drift:
                problems.append('pinned protocol files modified: ' + ', '.join(drift))
                self._auto_fallback('pinned protocol files modified: ' + ', '.join(drift))
        except Exception as exc:  # any protocol failure is an integrity failure here
            problems.append(f'pinned protocol unavailable: {exc}')
            self._auto_fallback(f'pinned protocol unavailable: {exc}')
        if problems:
            self.halt(state, budget_rules.StopReason.INTEGRITY, '; '.join(problems), escalate=True)
            raise IntegrityError('; '.join(problems))

    def _auto_fallback(self, reason: str) -> str | None:
        """Pin the project to the parent protocol after an integrity failure; never raises."""
        try:
            parent = evolution.fallback(self.paths.spec_file, reason)
        except Exception:  # fallback is best-effort; the halt that follows is what protects the run
            return None
        self.project = self.project.reload()
        return parent

    def _check_heartbeat(self, state: RunState) -> None:
        enforcement = self.project.doc.spec.enforcement
        if not enforcement.require_hooks:
            return
        beat = read_heartbeat(self.paths.heartbeat_file)
        age = age_seconds(beat, self.clock())
        if age is None or age > enforcement.heartbeat_max_age_s:
            self.halt(
                state,
                budget_rules.StopReason.HOOKS_DEAD,
                'hook heartbeat missing or stale; hooks are required (set enforcement.require_hooks: false '
                'only for agents without hooks)',
                escalate=True,
            )
            raise IntegrityError('Hook heartbeat missing or stale; refusing to run without live hooks')

    def halt(self, state: RunState, reason: budget_rules.StopReason, detail: str, *, escalate: bool) -> None:
        """Stop the run, recording the reason; ``escalate`` marks it as needing a human."""
        state.status = RunStatus.DONE
        state.stop_reason = reason.value
        state.escalated = detail if escalate else None
        state.wall_clock_s = self._elapsed_s(state)
        self._save(state)
        self._event(state.run_id, reason.value, detail)
        ledger_io.append(
            self.paths.ledger_file,
            ledger_io.RunEndRow(
                run_id=state.run_id,
                ts=self.clock(),
                stop_reason=reason.value,
                escalated=state.escalated,
                best=state.best,
                iterations=state.iter,
                keeps=self._keeps(state.run_id),
                wall_clock_s=state.wall_clock_s,
            ),
        )

    def _keeps(self, run_id: str) -> int:
        return sum(1 for r in ledger_io.iterations(self.paths.ledger_file, run_id) if r.decision is Outcome.KEEP)

    def _measure(self, log_path: Path | None) -> VerifyResult:
        metric = self.project.doc.spec.metric
        return measure(self.runner, metric.verify, self.paths.root, metric.timeout_s, metric.repeats, log_path)

    # ----- lifecycle -----------------------------------------------------------------------------------

    def start(
        self,
        name: str | None = None,
        *,
        iterations: int | None = None,
        minutes: int | None = None,
        bench_task: str | None = None,
        seed: int | None = None,
        session_id: str | None = None,
        model: str | None = None,
    ) -> RunState:
        """Check preconditions, measure the baseline, branch, lock, and record ``run_start``."""
        spec = self.project.doc.spec
        existing = self.load_state()
        if existing is not None and existing.status is not RunStatus.DONE:
            raise StateError(f'Run {existing.run_id} is {existing.status.value}; end or resume it first')
        if not self.git.is_repo():
            raise GitError('Not a git repository; run `git init` and commit first')
        if not self.git.has_commits():
            raise GitError('Repository has no commits; commit AUTOMATIVE.md and your files first')
        base_branch = self.git.current_branch()
        if base_branch is None:
            raise GitError('Detached HEAD; check out a branch first')
        in_scope_dirty, protected_dirty, other_dirty = self._classify_dirty()
        dirty = protected_dirty + other_dirty + in_scope_dirty
        if dirty:
            raise GitError('Working tree is dirty; commit or stash first: ' + ', '.join(dirty))
        protocol = self._protocol()
        drift = verify_integrity(protocol)
        if drift:
            raise IntegrityError('Pinned protocol files are modified: ' + ', '.join(drift))

        started = self.clock()
        run_id = new_run_id(name or self.paths.root.name, started)
        log_path = self.paths.iter_dir(run_id, 0) / 'verify.log'
        baseline = self._measure(log_path)
        if not baseline.ok or baseline.score is None:
            raise VerifyError(
                f'Baseline verify failed ({baseline.outcome.value}); fix the verify command before starting.\n'
                f'{baseline.tail}'
            )
        branch = f'{BRANCH_PREFIX}/{started.strftime("%Y%m%d-%H%M")}-{run_id.rsplit("-", 1)[-1]}'
        if self.git.branch_exists(branch):
            raise GitError(f'Branch {branch} already exists')
        self.git.create_branch(branch)
        baseline_commit = self.git.head()

        rel_protected = lock_io.resolve_protected(self.paths.root, spec.protected)
        hashes = lock_io.snapshot(self.paths.root, rel_protected)
        spec_sha = compute_spec_sha(self.project.doc.raw)
        lock_io.write_lock(
            self.paths.lock_file,
            lock_io.LockFile(
                protected=hashes, spec_sha=spec_sha, protocol_manifest_sha=manifest_sha(protocol.manifest)
            ),
        )
        state = RunState(
            run_id=run_id,
            protocol=spec.protocol,
            branch=branch,
            base_branch=base_branch,
            baseline_commit=baseline_commit,
            head=baseline_commit,
            baseline=Score(score=baseline.score, samples=baseline.samples),
            best=Best(score=baseline.score, iter=0, commit=baseline_commit),
            started_at=started,
            updated_at=started,
            protected=hashes,
            spec_sha=spec_sha,
            bench_task=bench_task,
            seed=seed,
            session_id=session_id,
            model=model,
            mode=Mode.RUN,
        )
        self._save(state)
        budget = spec.budget.model_dump()
        if iterations is not None:
            budget['iterations'] = iterations
        if minutes is not None:
            budget['minutes'] = minutes
        ledger_io.append(
            self.paths.ledger_file,
            ledger_io.RunStartRow(
                run_id=run_id,
                ts=started,
                protocol_version=spec.protocol,
                branch=branch,
                base_branch=base_branch,
                baseline_commit=baseline_commit,
                baseline=Score(score=baseline.score, samples=baseline.samples),
                spec_sha=spec_sha,
                protected=hashes,
                metric=spec.metric.model_dump(mode='json'),
                budget=budget,
                model=model,
                disclosure=disclosure_card(
                    model=model,
                    protocol_version=spec.protocol,
                    metric=spec.metric.model_dump(mode='json'),
                    budget=budget,
                    require_hooks=spec.enforcement.require_hooks,
                ),
            ),
        )
        self._write_budget_override(budget)
        self.record_shown('start', render_brief(self.brief()))
        return state

    def _write_budget_override(self, budget: dict[str, object]) -> None:
        """Persist per-run budget overrides next to the state so later commands honour them."""
        (self.paths.dotdir / 'budget.json').write_text(json.dumps(budget), encoding='utf-8')

    def _budget(self) -> BudgetSpec:
        path = self.paths.dotdir / 'budget.json'
        if path.is_file():
            return BudgetSpec.model_validate(json.loads(path.read_text(encoding='utf-8')))
        return self.project.doc.spec.budget

    def try_change(
        self,
        message: str,
        hypothesis: str,
        *,
        predict: str | None = None,
        strategy_ids: tuple[str, ...] = (),
        mode: str = 'improve',
    ) -> TryOutcome:
        """Commit the in-scope diff, verify it, decide, revert on non-keep, and record the row."""
        state = self.require_state()
        if state.status is not RunStatus.ACTIVE:
            raise StateError(f'Run {state.run_id} is {state.status.value}; `automative run resume` first')
        if state.pending is not None:
            raise StateError('A previous try is still pending; run `automative run resume` first')
        spec = self.project.doc.spec
        metric = spec.metric
        self._check_heartbeat(state)
        self._check_integrity(state)
        self._check_context(state)
        context_sha = state.view_sha()
        in_scope, protected_dirty, other_dirty = self._classify_dirty()
        if protected_dirty:
            detail = 'new protected files: ' + ', '.join(protected_dirty)
            self.halt(state, budget_rules.StopReason.INTEGRITY, detail, escalate=True)
            raise IntegrityError('Changes to protected paths: ' + ', '.join(protected_dirty))
        if other_dirty:
            self._event(state.run_id, 'refused', 'out-of-scope changes', files=list(other_dirty))
            raise ScopeError(
                'Changes outside scope; revert them (or `automative discard` for in-scope files): '
                + ', '.join(other_dirty)
            )
        if not in_scope:
            self._event(state.run_id, 'no_op', 'try called with no in-scope changes')
            raise ScopeError('No in-scope changes to try; edit a file inside `scope` first')

        assert state.best is not None
        best_before = state.best.score
        predicted = parse_prediction(predict, best_before)
        iteration = state.iter + 1
        parent = self.git.head()
        parent_iter = state.checked_out if state.checked_out is not None else state.best.iter
        state.checked_out = None
        trailers = [f'Hypothesis: {hypothesis}', f'Run: {state.run_id}']
        if predicted is not None:
            trailers.insert(1, f'Predict: {predicted:g}')
        if strategy_ids:
            trailers.insert(1, f'Strategies: {",".join(strategy_ids)}')
        self.git.stage(in_scope)
        commit = self.git.commit(f'{COMMIT_PREFIX}(i{iteration}): {message}\n\n' + '\n'.join(trailers))
        ref = ref_for_try(state.run_id, iteration)
        self.git.update_ref(ref, commit)
        state.pending = Pending(iter=iteration, commit=commit, started_at=self.clock())
        state.iter = iteration
        self._save(state)

        iter_dir = self.paths.iter_dir(state.run_id, iteration)
        log_path = iter_dir / 'verify.log'
        verify = self._measure(log_path)
        guard = GuardResult(GuardStatus.NONE, None, '')
        heldout_score: float | None = None
        heldout_ok = True
        if verify.ok and verify.score is not None:
            guard = run_guards(self.runner, metric.guard, self.paths.root, metric.timeout_s, log_path)
            improved = is_improvement(verify.score, best_before, metric.direction, metric.threshold)
            if improved and guard.status is not GuardStatus.FAIL and metric.heldout:
                held = measure(self.runner, metric.heldout, self.paths.root, metric.timeout_s, 1, log_path)
                heldout_score = held.score
                heldout_ok = held.ok and self._heldout_not_worse(state, held.score)
        decision = self.policy.judge(
            verify_outcome=verify.outcome,
            score=verify.score,
            best=best_before,
            direction=metric.direction,
            threshold=metric.threshold,
            guard=guard.status,
            heldout_ok=heldout_ok,
        )
        (iter_dir / 'verify.json').parent.mkdir(parents=True, exist_ok=True)
        (iter_dir / 'verify.json').write_text(
            ledger_io.VerifyRecord(
                outcome=verify.outcome,
                score=verify.score,
                samples=verify.samples,
                runtime_s=verify.runtime_s,
                exit_code=verify.exit_code,
                log=self.paths.relative(log_path),
            ).model_dump_json(indent=2),
            encoding='utf-8',
        )

        revert_commit: str | None = None
        if decision.kept:
            assert verify.score is not None
            state.best = Best(score=verify.score, iter=iteration, commit=commit)
            state.tries_since_best = 0
            state.head = commit
        else:
            revert_commit = self.git.revert_head()
            state.head = revert_commit
            if decision.outcome in (Outcome.DISCARD, Outcome.GUARD_FAIL):
                state.tries_since_best += 1
        if decision.outcome in (Outcome.CRASH, Outcome.TIMEOUT, Outcome.METRIC_ERROR):
            state.consecutive_errors += 1
        else:
            state.consecutive_errors = 0
        state.pending = None
        state.stop_hook.blocks_since_last_try = 0
        state.wall_clock_s = self._elapsed_s(state)

        prediction_error: float | None = None
        if predicted is not None and decision.delta is not None and best_before != 0:
            prediction_error = abs(decision.delta - predicted) / abs(best_before) * 100.0
        row = ledger_io.IterationRow(
            run_id=state.run_id,
            iter=iteration,
            ts=self.clock(),
            protocol_version=state.protocol,
            mode=mode,
            change=message,
            hypothesis=hypothesis,
            predicted_delta=predicted,
            strategy_ids=strategy_ids,
            parent_iter=parent_iter,
            context_sha=context_sha,
            commit=commit,
            parent_commit=parent,
            ref=ref,
            revert_commit=revert_commit,
            files_changed=in_scope,
            verify=ledger_io.VerifyRecord(
                outcome=verify.outcome,
                score=verify.score,
                samples=verify.samples,
                runtime_s=verify.runtime_s,
                exit_code=verify.exit_code,
                log=self.paths.relative(log_path),
            ),
            guard=ledger_io.GuardRecord(status=guard.status, failed_cmd=guard.failed_cmd),
            heldout_score=heldout_score,
            best_before=best_before,
            observed_delta=decision.delta,
            delta_pct=decision.delta_pct,
            prediction_error_pct=prediction_error,
            decision=decision.outcome,
            decision_reason=decision.reason,
            failure_category=decision.failure_category,
            denied_tool_calls=state.denied_tool_calls,
            wall_clock_s=state.wall_clock_s,
        )
        ledger_io.append(self.paths.ledger_file, row)
        note = f'{decision.outcome.value} score={verify.score} delta={decision.delta} iter={iteration}'
        self.git.add_note(commit, note)
        self._record_strategy_evidence(state, row)

        status = budget_rules.evaluate(state, self._budget(), metric)
        if status.should_stop:
            assert status.stop_reason is not None
            self.halt(state, status.stop_reason, status.message, escalate=status.escalate)
        else:
            self._save(state)
        assert state.best is not None
        outcome = TryOutcome(
            row=row, decision=decision, budget=status, best=state.best.score, stopped=status.should_stop
        )
        text = render_try(outcome)
        self.record_shown('try', text, args={'iter': iteration})
        return replace(outcome, text=text)

    def _heldout_not_worse(self, state: RunState, score: float | None) -> bool:
        if score is None:
            return False
        rows = ledger_io.iterations(self.paths.ledger_file, state.run_id)
        previous = [r.heldout_score for r in rows if r.decision is Outcome.KEEP and r.heldout_score is not None]
        if not previous:
            return True
        best_held = previous[-1]
        direction = self.project.doc.spec.metric.direction
        return score <= best_held if direction is Direction.LOWER else score >= best_held

    def _record_strategy_evidence(self, state: RunState, row: ledger_io.IterationRow) -> None:
        """Accrue evidence for the strategies cited on this try."""
        if row.strategy_ids:
            strategy_io.record_evidence(self.paths.strategies_file, row)

    def discard(self, reason: str = '') -> tuple[str, ...]:
        """Abandon the current attempt: restore in-scope edits and revert a pending commit."""
        state = self.require_state()
        in_scope, protected_dirty, other_dirty = self._classify_dirty()
        self.git.restore(in_scope)
        if state.pending is not None:
            self.git.revert_head()
            state.head = self.git.head()
            state.pending = None
        self._event(state.run_id, 'abandoned', reason, files=list(in_scope))
        state.stop_hook.blocks_since_last_try = 0
        state.checked_out = None
        self._save(state)
        self.record_shown('discard', 'Discarded in-scope changes: ' + (', '.join(in_scope) or '(none)'))
        return protected_dirty + other_dirty

    def checkout(self, iteration: int) -> tuple[str, tuple[str, ...]]:
        """Restore the in-scope files of attempt ``iteration`` (0 = baseline) so the next try builds on it.

        Commits stay linear on the run branch; the tree is logical, recorded as ``parent_iter`` on the
        next iteration row. Returns the revision restored from and the files that changed.
        """
        state = self.require_state()
        if state.status is not RunStatus.ACTIVE:
            raise StateError(f'Run {state.run_id} is {state.status.value}; `automative run resume` first')
        if state.pending is not None:
            raise StateError('A previous try is still pending; run `automative run resume` first')
        in_scope, protected_dirty, other_dirty = self._classify_dirty()
        if in_scope or protected_dirty or other_dirty:
            raise ScopeError(
                'Working tree has changes; `automative try` or `automative discard` them before a checkout: '
                + ', '.join(in_scope + protected_dirty + other_dirty)
            )
        if iteration == 0:
            rev = state.baseline_commit
        else:
            rows = {r.iter: r for r in ledger_io.iterations(self.paths.ledger_file, state.run_id)}
            row = rows.get(iteration)
            if row is None:
                raise StateError(f'No attempt i{iteration} in run {state.run_id}')
            rev = row.ref
        spec = self.project.doc.spec
        changed = self.git.changed_files('HEAD', rev)
        scoped = tuple(p for p in changed if classify(p, spec.scope, spec.protected) is Classification.IN_SCOPE)
        self.git.restore_from(rev, scoped)
        state.checked_out = iteration
        state.stop_hook.blocks_since_last_try = 0
        self._save(state)
        self._event(
            state.run_id,
            'checkout',
            f'working tree restored from i{iteration}',
            iteration=iteration,
            files=list(scoped),
        )
        self.record_shown(
            'checkout',
            f'Working tree now matches attempt i{iteration} ({rev}) for: '
            + (', '.join(scoped) or '(no differences from best)')
            + f'. The next try records i{iteration} as its parent.',
            args={'iteration': iteration},
        )
        return rev, scoped

    def pause(self) -> RunState:
        state = self.require_state()
        if state.status is RunStatus.DONE:
            raise StateError('Run is already done')
        if state.status is RunStatus.PAUSED:
            return state
        state.status = RunStatus.PAUSED
        state.paused_at = self.clock()
        self._save(state)
        self._event(state.run_id, 'paused')
        return state

    def resume(self, *, reverify: bool = False) -> RunState:
        """Repair a crashed or paused run and make it active again."""
        state = self.require_state()
        if state.status is RunStatus.DONE:
            raise StateError(f'Run {state.run_id} is done ({state.stop_reason}); start a new run')
        if state.paused_at is not None:
            state.paused_total_s += (self.clock() - state.paused_at).total_seconds()
            state.paused_at = None
        if state.pending is not None:
            self.git.revert_head()
            self._event(state.run_id, 'abandoned', f'pending try i{state.pending.iter} reverted on resume')
            state.pending = None
            state.head = self.git.head()
        if self.git.current_branch() != state.branch:
            self.git.checkout(state.branch)
        state.status = RunStatus.ACTIVE
        state.escalated = None
        state.stop_hook.blocks_since_last_try = 0
        self._save(state)
        self._check_integrity(state)
        if reverify:
            result = self._measure(None)
            self._event(state.run_id, 'reverify', result.outcome.value, score=result.score)
        self._event(state.run_id, 'resumed')
        self.record_shown('resume', render_brief(self.brief()))
        return state

    def verify_only(self) -> VerifyResult:
        """Measure the current tree without committing or deciding."""
        state = self.load_state()
        if state is not None and state.status is RunStatus.ACTIVE:
            self._check_integrity(state)
        return self._measure(None)

    def end(self, reason: str = '') -> ledger_io.RunSummary:
        """Finish the run, record ``run_end``, and commit the ledger and catalogue."""
        state = self.require_state()
        if state.status is not RunStatus.DONE:
            if state.pending is not None:
                self.git.revert_head()
                state.pending = None
            self.halt(state, budget_rules.StopReason.ENDED, reason, escalate=False)
        summary = ledger_io.summarize(ledger_io.read(self.paths.ledger_file), state.run_id)
        evolution.record_telemetry(
            {
                'ts': self.clock().isoformat(),
                'protocol_version': state.protocol,
                'run_id': state.run_id,
                'project': self.paths.root.name,
                'iterations': summary.iterations,
                'keeps': summary.keeps,
                'keep_rate': summary.keep_rate,
                'improvement_pct': summary.improvement_pct,
                'stop_reason': state.stop_reason,
                'escalated': state.escalated,
                'denied_tool_calls': state.denied_tool_calls,
                'bench_task': state.bench_task,
                'seed': state.seed,
            }
        )
        bookkeeping = tuple(
            self.paths.relative(p) for p in (self.paths.ledger_file, self.paths.strategies_file) if p.is_file()
        )
        if bookkeeping and self.git.current_branch() == state.branch:
            self.git.stage(bookkeeping)
            if self.git.staged_files():
                self.git.commit(f'{COMMIT_PREFIX}(end): {state.run_id}')
        return summary

    def brief(self, strategies: tuple[str, ...] = ()) -> Brief:
        """Assemble the recitation block."""
        doc = self.project.doc
        spec = doc.spec
        state = self.load_state()
        protocol_path: str | None
        try:
            protocol_path = str(self._protocol().skill_file)
        except Exception:  # the brief must render even if the protocol is missing
            protocol_path = None
        budget = self._budget()
        if state is None:
            return Brief(
                exists=False,
                status='none',
                run_id=None,
                goal=doc.goal,
                metric_name=spec.metric.name,
                direction=spec.metric.direction.value,
                verify_cmd=spec.metric.verify,
                baseline=None,
                best=None,
                best_iter=None,
                iteration=0,
                iterations_budget=budget.iterations,
                minutes_used=0.0,
                minutes_budget=budget.minutes,
                tries_since_best=0,
                plateau_patience=budget.plateau_patience,
                recent=(),
                strategies=strategies,
                protocol_version=spec.protocol,
                protocol_path=protocol_path,
                stop_reason=None,
                escalated=None,
                pending=False,
                scope=spec.scope,
            )
        return Brief(
            exists=True,
            status=state.status.value,
            run_id=state.run_id,
            goal=doc.goal,
            metric_name=spec.metric.name,
            direction=spec.metric.direction.value,
            verify_cmd=spec.metric.verify,
            baseline=state.baseline.score if state.baseline else None,
            best=state.best.score if state.best else None,
            best_iter=state.best.iter if state.best else None,
            iteration=state.iter,
            iterations_budget=budget.iterations,
            minutes_used=self._elapsed_s(state) / 60.0,
            minutes_budget=budget.minutes,
            tries_since_best=state.tries_since_best,
            plateau_patience=budget.plateau_patience,
            recent=ledger_io.tail(self.paths.ledger_file, 5, state.run_id),
            strategies=strategies,
            protocol_version=state.protocol,
            protocol_path=protocol_path,
            stop_reason=state.stop_reason,
            escalated=state.escalated,
            pending=state.pending is not None,
            scope=spec.scope,
            checked_out=state.checked_out,
        )
