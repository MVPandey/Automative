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
   hook sends the agent back into the loop until the budget, a plateau, or the target ends it.
5. What the agent learns goes into a strategy catalogue. The protocol the agent follows is versioned;
   it only changes after a benchmark shows the new version does better, and one line pins it back.

`docs/DESIGN.md` explains the reasoning and the research behind it. `examples/` has tasks you can run.

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

## A ten minute demo

```sh
scripts/demo.sh            # copies examples/sortbench to a temp dir, inits git, starts a run
cd <printed dir> && claude # then type /automative and watch the ledger fill up
automative ledger; automative report
```

Here is what a headless run of that demo actually did (`claude -p`, Claude Code 2.1, 28 turns,
3.3 minutes, $2.15):

```
iter  decision   score    delta    change
1     keep       4.958    -4.179   Replace hand-rolled insertion sort with built-in sorted()
2     keep       0.089    -4.869   Replace O(n*k) list membership dedupe with dict.fromkeys()
3     keep       0.035    -0.054   Use set() instead of dict.fromkeys() for dedupe
4     discard    0.038    +0.003   Bind set/sorted builtins as default args to skip LOAD_GLOBAL
5     keep       0.034    -0.001   Build list from set then sort in place instead of sorted()
6-8   discard    ...               small variants, all reverted
Baseline 9.137 -> best 0.034 (-99.6%), keep rate 50%, prediction error 9.4%, 2 strategies learned
```

Each discard is a revert pair in `git log` and each keep is a commit. The guard tests passed on
every try. The agent tried to touch `bench.py` and the tests; the hook said no.

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
| `discard`, `verify`, `ledger`, `report` | recovery and inspection |
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
docs/DESIGN.md                     why it is built this way
```
