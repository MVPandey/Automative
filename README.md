# Automative

Automative runs an improvement loop on anything you can score with a command. You write down a goal,
list the files an agent may change, and give it a command that prints a number. Your coding agent
(Claude Code first; Codex and OpenHands can load the same skill) then makes one change at a time,
measures it, keeps it if the number moved the right way, and reverts it if not. A small CLI does the
measuring, the deciding, and the reverting, so the model never gets to grade its own work.

Code is just the medium the agent works in. The thing being improved can be:

| Domain | Files in `scope` | What `verify` prints |
|---|---|---|
| Performance and engineering | `src/**` | benchmark ms, bundle bytes, test coverage % |
| ML research | `train.py`, `config.yaml` | validation bits per byte, eval accuracy |
| Prompt and agent design | `prompts/system.md` | pass rate on a held out eval set |
| Trading and finance | `strategy/params.toml` | out of sample Sharpe from a backtest |
| Writing and research drafts | `paper/sections/*.md` | a rubric score from a protected judge script, word budget |
| Data pipelines | `pipeline/**` | rows per second, error rate, cost per run |

Anything the metric cannot see goes into `guard` commands (tests, type checks, constraint checkers)
or into the plain prose constraints the agent rereads every iteration.

## How it works

1. `automative init` writes `AUTOMATIVE.md`: the goal, the metric, the verify and guard commands,
   `scope`, `protected` files, and a budget. You commit it.
2. In Claude Code you type `/automative` (from any other agent, run `automative session brief`). The
   agent reads a short brief, makes one change inside `scope`, and runs
   `automative try -m "what changed" --hypothesis "why it should help"`.
3. `try` commits, runs the verifier, decides, reverts anything that is not a keep, and appends a row
   to the ledger. The agent only ever sees the score that `try` printed.
4. Protected files (the verifier, the tests, the spec itself) are hashed at the start of the run.
   Editing one stops the run. Hooks refuse edits outside `scope` and direct git commands, and a Stop
   hook sends the agent back into the loop until the budget, a plateau, or the target ends it. If the
   contract names a `heldout` command, every keep is re-checked on it, and the agent is told only
   pass or fail: the numbers are sealed in `.automative/heldout/`, which the hooks refuse to read.
5. What the agent learns goes into a strategy catalogue. The protocol the agent follows is versioned;
   it only changes after a benchmark shows the new version does better, and one line pins it back.

`docs/DESIGN.md` explains the reasoning and the research behind it. `examples/` has tasks you can run,
and `docs/BENCHMARKS.md` lists public task sets that fit the contract.

## Install

```sh
# the CLI (scores, decisions, git, budgets, hooks)
uv tool install git+https://github.com/MVPandey/Automative      # or: uv tool install . from a checkout

# the Claude Code plugin (thin skill plus hooks), from a checkout:
claude --plugin-dir /path/to/Automative
# or, once published as a marketplace plugin:
#   /plugin marketplace add MVPandey/Automative && /plugin install automative
```

Other agents (Codex, OpenHands) load the same skill from `.agents/skills/automative/`. Set
`enforcement.require_hooks: false` in `AUTOMATIVE.md` for them, since they have no hook heartbeat.
The CLI still checks scope and protected file hashes on every `try`.

## A ten minute demo, start to finish

`examples/sortbench` has a deliberately slow `dedupe_and_sort()`: a hand written insertion sort over
a list that is deduplicated with `x in seen` on a list. `bench.py` times it on 4,000 seeded integers
and prints the median in milliseconds. Tests pin the behaviour. The contract says: change only
`src/slowsort/**/*.py`, never `bench.py` or `tests/`, stop after 8 tries or 10 minutes or 4 tries
without a new best.

```sh
scripts/demo.sh            # copies the example to a temp dir, inits git, runs doctor, starts the run
cd <printed dir> && claude # type /automative, or drive it headless as below
```

(`scripts/demo.sh examples/algotune/pagerank` does the same for any of the six AlgoTune tasks in
`examples/algotune/`.)

This is the run behind the numbers below, driven headless so nothing was typed by hand:

```sh
claude -p 'Run `automative session brief`, read the pinned protocol file it names, and follow it
until the harness says the run is done. Then run `automative run end`.' \
  --plugin-dir /path/to/Automative --permission-mode bypassPermissions --max-turns 80
```

`run start` measured the baseline (8.648 ms), created the branch, and hashed `bench.py`, the tests,
and the contract. The agent read the brief, made one change, and called `try`. Seven times:

```
iter  decision   score    delta    change
1     keep       4.707    -3.941   Replace the hand-written insertion sort with sorted()
2     keep       0.091    -4.616   Track seen values in a set instead of testing list membership
3     keep       0.034    -0.057   Build the set in C with set(values) and sort it directly
4     discard    0.034     0       Sort the list in place instead of calling sorted() on the set
5     discard    0.034     0       Build the set with a set display {*values} instead of calling set()
6     discard    0.035    +0.001   Use frozenset(values) instead of set(values)
7     discard    0.035    +0.001   Build an empty set and update() it from values instead of set(values)
```

After i7 the harness stopped the run itself: four tries without a new best is the plateau limit in
the contract. Claude Code 2.1, 20 turns, 3.4 minutes, $2.08. What the record shows afterwards:

```
$ automative report
Run r-20260826-0012-sortbench: 7 tries, 3 kept, 4 discarded, 0 errors, keep rate 43%
Baseline 8.648 to best 0.034 (-99.6%) at i3
Prediction calibration: mean |error| 4.4% of incumbent

$ automative audit
Run r-20260826-0012-sortbench: 17 shown rows, 7 tries
Surfaces: brief x1, hook:deny x1, hook:prompt-submit x1, hook:session-start x1, ledger x2, ...
OK: every try cites a view the agent was shown.

$ git log --oneline | head -4
9b1e67e automative(end): r-20260826-0012-sortbench
bc9fe23 Revert "automative(i7): Build an empty set and update() it from values instead of set(values)"
ed6bc5e automative(i7): Build an empty set and update() it from values instead of set(values)
a6b783c Revert "automative(i6): Use frozenset(values) instead of set(values)"
```

Each discard is a commit and its revert, so every attempt is still inspectable. The agent predicted
each delta before measuring and was within 4.4% on average. It recorded two strategies in the
catalogue on the way. The one `hook:deny` was the hook refusing a shell write into `.automative/`,
which the agent reported at the end instead of working around. (It was right to complain: the path
was its own notes file, and that refusal was a bug, fixed since.) The best commit is on the run
branch; `git merge automative/20260826-0012-sortbench` lands it.

## The contract: `AUTOMATIVE.md`

```yaml
---
protocol: 1.0.0                    # pinned protocol version; change this one line to roll back
metric: {name: bench_ms, direction: lower, verify: python bench.py, guard: [python -m unittest discover -s tests],
         repeats: 3, min_improvement: "2%", timeout_s: 120}
scope: [src/slowsort/**/*.py]      # the only files the agent may change
protected: [bench.py, tests/**]    # hashed for the whole run
budget: {iterations: 30, minutes: 120, plateau_patience: 8}
---
# Goal
One paragraph. The agent rereads it every iteration.
```

## The CLI

| Command | What it does |
|---|---|
| `init`, `doctor` | write the contract; check git, the verify command, hooks, and the protocol |
| `run start`, `run pause`, `run resume`, `run end` | lifecycle; `start` measures the baseline and hashes the protected files |
| `session brief` (alias `status`) | the short brief, at most 15 lines (exit 0 active, 3 done, 4 no run, 5 paused) |
| `try -m ... --hypothesis ... [--predict] [--strategies]` | the one iteration primitive: commit, verify, decide, keep or revert, log |
| `discard`, `verify`, `ledger`, `report` | recovery and inspection; `report --heldout` shows the sealed held-out scores (for humans, after the run) |
| `checkout N`, `tree` | build the next try on an earlier attempt (0 is the baseline) instead of on the best; show the attempt tree |
| `audit` | replay the ledger: was every try made against a brief the agent was actually shown, and does every stored text still match its hash |
| `strategy add`, `show`, `suggest`, `set-status`, `merge`, `promote-global` | the learnings catalogue (an append only op log that the CLI curates) |
| `protocol list`, `show`, `pin`, `install`, `candidate create`, `candidate validate`, `promote`, `reject`, `changelog` | the versioned protocol store, with bounded edits and instant rollback |
| `bench freeze`, `list`, `run [--driver claude-p\|dsh\|manual]`, `report`; `evolve --propose`, `evolve --bench` | the benchmark suite and the promotion gate |

Everything the CLI prints for the agent (the brief, each verdict, hook refusals, suggestions, even
`verify`) is written to the ledger as a `shown` row with a hash of the text and of the run state it
was rendered from. `try` refuses to run against a state the agent has not been shown, and `audit`
checks the chain afterwards. The rule is the one DeepSeek Harness applies to its session log: if it
reached the model, it is in the log.

## Layout

```
src/automative/                    CLI and harness: runloop, verify, decide, budget, ledger, hooks,
                                   strategies, evolution, bench
src/automative/protocol/versions/  bundled, checksummed protocol versions (0.0.0-null baseline, 1.0.0)
skills/ commands/ agents/ hooks/   the Claude Code plugin: thin loader plus enforcement hooks
integrations/dsh/                  the same enforcement as a native DeepSeek Harness plugin
examples/sortbench/                demo target
examples/algotune/                 six AlgoTune tasks rehosted with dependency-free references
examples/memecoin/                 hourly memecoin strategy against a fee-aware backtest with a held-out window
scripts/build_bench.sh             runs every example once and freezes it into the benchmark suite
docs/DESIGN.md                     why it is built this way
```
