# Automative design

Automative is an improvement loop for anything a command can score: code, ML training configs,
prompts, trading strategy parameters, research drafts. Your coding agent supplies the ideas and the
edits. The `automative` CLI supplies everything a model should not be trusted with: the score, the
keep or discard decision, the revert, the budget, and the record of what happened.

## The idea

Self improvement loops work when the thing that judges a change and the thing that measures it are
both out of the agent's reach. The model proposing changes is the cheap part. This is the most
repeated finding in the 2023 to 2026 literature (Huang et al. 2310.01798, Kamoi et al. 2406.01297,
SkillOpt 2605.23904, AHE 2604.25850, "Rethinking the Evaluation of Harness Evolution" 2607.12227) and
it is what every working harness we looked at has in common: Karpathy's `autoresearch`, AIDE, the
Darwin Gödel Machine, GEPA, Anthropic's long running agent harness, OpenAI's harness engineering
write up, Aider, SWE-agent.

## Three levels

| Level | What gets better | Who writes it | What gates it |
|---|---|---|---|
| L0, the run | the files in `scope` | the agent edits; the CLI commits, verifies, decides, reverts | the metric, the guard commands, an optional held out check |
| L1, the strategy catalogue | reusable lessons in `.automative/strategies.jsonl` | the agent proposes with `strategy add`; the CLI promotes or rejects from evidence | counts of times a strategy helped or hurt; a buffer of rejected entries |
| L2, the protocol | the versioned instructions the agent follows (`SKILL.md`, `references/`, `rules.toml`) | the agent edits a copy; the CLI turns the diff into a small set of bounded operations | a benchmark against the current version and against a bare loop, on held out tasks, then a human confirms |

Each level can only write through the CLI. Each level's budget, verifier, and scope live one level
up, in `AUTOMATIVE.md`, which a human owns and the CLI hashes at the start of every run. So L2 cannot
extend its own budget or weaken its own verifier. The failure modes reported for STOP, the Darwin
Gödel Machine, and the AI Scientist are ruled out by construction rather than by asking nicely.

## What `automative try` does

1. Refuses if a previous try is still pending, hooks are dead, the spec or a protected file has
   changed, files outside `scope` are dirty, or there is nothing to try.
2. Stages only the in scope files. Commits `automative(iN): <change>` with `Hypothesis:` and
   `Predict:` trailers. Pins the commit under `refs/automative/<run>/<iter>` so nothing is lost.
3. Runs the verify command `repeats` times and takes the median. Then the guard commands. Then the
   held out command, if there is one and the metric improved.
4. Decides with a pure function: keep only if the score improved by at least `min_improvement`, every
   guard passed, and the held out metric did not get worse. A tie is a discard.
5. On anything but a keep, adds a revert commit. Appends the ledger row, writes a git note, updates
   the budget, plateau, and error counters, and stops the run if a stop rule fired.

There is no `keep` command. The agent cannot report a score; it can only read the one `try` printed.

## Enforcement

Deterministic controls first, prompt instructions last.

| What agents have actually done | What stops it here |
|---|---|
| edited the tests or the verifier (the DGM faked test logs; Claude 3.7 special cased tests) | `protected` files are hashed at `run start`; the PreToolUse hook refuses edits; the PostToolUse hook rehashes, restores, and halts |
| reported a better number than they got (METR: at least 16% of "successes" on long tasks were fake) | there is no channel for it; the score comes from a subprocess the CLI ran |
| extended a timeout or loosened the verifier (the AI Scientist; o3 rewrote the timer) | budgets and the verify command live in the hashed spec; every `try` checks the hash |
| declared the job done without checking | "done" is computed by the CLI; the Stop hook sends the agent back at most three times, then pauses the run as stalled |
| widened scope or ran git directly | the hook refuses edits outside `scope` and `git commit`, `reset`, `checkout --`, `stash`; `try` stages scope files only |
| disabled the hooks | settings, hooks, and plugin paths are refused; `ConfigChange` exits 2; `try` refuses to run if the hook heartbeat is stale |
| kept fighting the harness | every refusal is a ledger event; five of them stop the run and flag it for a human |
| erased the evidence | the ledger is append only; discards are reverts, not resets; every try keeps a ref |

What still depends on the prompt: honest hypotheses, honest `--predict` values, and edits that are
inside scope but pointless. The report's calibration numbers and the `heldout` command make those
visible.

Held-out scores are never written down. When the contract names a `heldout` command, a try that
improved the training metric is measured on it together with the incumbent, in one invocation that
swaps the in-scope files and swaps them back, and only `pass` or `fail` reaches the ledger and the
verdict. The first memecoin run showed why: with the numbers in the ledger, every try was a peek and
the agent selected on the held-out window. The data behind the command is declared under `sealed`;
the hooks refuse to read it, refuse the held-out command outside `try`, and refuse `sudo`. Every tool
call is recorded in `.automative/trace.jsonl`, and `audit` flags what the hooks did not catch. For
data that must be unreadable rather than merely refused, `enforcement.heldout_user` runs the held-out
command as a second OS user that owns the sealed files (`docs/SEALED-VERIFIER.md`). Humans get the
numbers afterwards with `report --heldout`, which re-measures instead of remembering.

## Context is a cache

The agent gets a brief of at most 15 lines every iteration (`session brief`). The SessionStart hook
prints it again after a context reset and the Stop hook includes it when it blocks an early exit.
The protocol is loaded in layers: a thin `SKILL.md` loader, then the pinned protocol (at most 400
lines), then `references/` when needed. Strategies come back top k, never as a dump. This follows
Anthropic's context engineering advice, the Manus lesson about restating the goal, and the ACE and
Adaptive Auto-Harness results showing that prompts rewritten wholesale grow until they stop working.

## Why the L2 gate is strict

"Rethinking the Evaluation of Harness Evolution" (2607.12227) shows that most reported gains from
evolving a harness disappear when you compare against plain repeated sampling at the same budget on
tasks the search never saw. So:

- any finished run can be frozen into a task (`bench freeze`) with its baseline, the best score the
  run reached, the noise floor, and a fixed one in three held out split;
- a bench run evaluates the candidate, the current version, and the bare "null" protocol on the same
  tasks, budget, seeds, and model, in stages (train tasks with one seed, then held out tasks with
  every seed, then a second train seed) so bad candidates are cut early;
- promotion needs all of: held out mean at least δ above the incumbent (or within δ with 10% fewer
  tokens), no single task worse by more than ε = 0.10, no train versus held out overfit pattern, no
  integrity events, cost inside the caps, and beating the null protocol. Then a human runs
  `protocol promote --confirm`;
- tasks that cannot tell versions apart are marked uninformative, not deleted;
- rejected candidates are remembered, and anything close to one is refused before it costs anything;
- rolling back is one line in `AUTOMATIVE.md`, and happens on its own after an integrity failure.

## Model-visible means logged

The ledger started as a record of decisions. It now also records what the agent was shown. Every
brief, verdict, refusal, suggestion list, and `verify` result the CLI prints for the agent is appended
as a `shown` row carrying a hash of the text and a hash of the run state (`view_sha`) it was rendered
from. Each iteration row stores the `view_sha` the try was made against, and `try` refuses when the
last thing shown does not match the current state. `automative audit` replays a run and reports any
try whose view was never shown and any stored text that no longer matches its hash.

The idea comes from DeepSeek Harness, whose session log asserts at runtime that anything reaching
the model can be rebuilt from the log. Here it closes a specific gap: the agent could previously act
on a brief that was true three commands ago, and nothing in the record would have said so.

## The attempt tree

Commits on the run branch stay linear, but the ledger now records a logical tree. Each iteration row
names its `parent_iter`: the best at the time, or the attempt the working tree was restored from with
`automative checkout N`. `checkout` restores attempt N's in-scope files (0 is the baseline) without
touching the index, so the next `try` builds on it and the discard path still works. `automative tree`
draws it. The selection rule is unchanged (a keep must beat the global best), which is what keeps this
a ratchet rather than a random walk; what the tree adds is the ability to debug a crashed attempt or
branch from a near miss instead of only ever editing the incumbent. A population or Pareto policy
would plug into the same seam.

## Runtimes

The Claude Code plugin is the reference surface. `integrations/dsh` carries the same enforcement as a
native DeepSeek Harness plugin on its typed interception points, and `bench run --driver dsh` runs a
benchmark cell under `dsh --profile headless`. Scoring one protocol version under two runtimes is the
cheapest way to find out how much of an observed difference is the harness rather than the protocol.

## What is left out of v1, on purpose

Population and Pareto policies (the seams are there: the `Policy` protocol, a ref per try,
`parent_iter` and `samples[]` in the ledger). LLM judges as a mode (a protected script that prints a
number already works). Docker sandboxing (seam: `Runner`). A UI (the ledger is JSONL). Any prose only
"protocol" without a CLI enforcing it.

## Reading

Kamoi et al. 2406.01297. Huang et al. 2310.01798. Karpathy, `autoresearch`. AIDE 2502.13138. Darwin
Gödel Machine 2505.22954. GEPA 2507.19457. AIRA 2507.02554. SkillOpt 2605.23904. "Stop comparing LLM
agents without disclosing the harness" 2605.23950. "Rethinking the evaluation of harness evolution"
2607.12227. Anthropic, "Effective context engineering for AI agents" and "Effective harnesses for
long running agents". Manus, "Context engineering for AI agents: lessons". OpenAI, "Harness
engineering".
