# Things to point it at

Automative needs a task with three properties: files an agent may change, a command that prints a
number, and a check the number cannot fool. These public task sets have all three. Each row says what
`scope`, `verify`, and `guard` would be, and what it costs to run.

| Task set | Tasks | `scope` | `verify` prints | `guard` | Cost |
|---|---|---|---|---|---|
| [AlgoTune](https://github.com/oripress/AlgoTune) (NeurIPS 2025, MIT) | 154 math, physics, and CS problems | one `solver.py` per task | speedup over the reference implementation | outputs must equal the reference's | CPU, seconds to minutes per verify |
| [OpenEvolve examples](https://github.com/algorithmicsuperintelligence/openevolve) (Apache-2.0) | circle packing (n=26, best known 2.635), function minimization, adaptive sort, symbolic regression | one program file | the evaluator's score | validity check inside the evaluator | CPU, seconds |
| [Karpathy autoresearch](https://github.com/karpathy/autoresearch) | one: nanochat pretraining | `train.py` | `val_bpb` after a fixed 5 minute run | none needed (the metric is the loss) | one GPU, 5 minutes per verify |
| [RE-Bench](https://github.com/METR/ai-rd-tasks) (MIT) | 7 ML research engineering tasks | the task's solution directory | the task's own `score` (log loss, log runtime, win rate) | built into each scorer | GPUs for most; hours |
| [modded-nanogpt speedrun](https://github.com/KellerJordan/modded-nanogpt) | one | `train_gpt.py` | wall-clock to reach 3.28 val loss | the loss target itself | 8xH100 |
| [MLE-bench](https://github.com/openai/mle-bench) (Lite) | 22 Kaggle competitions | the solution directory | the competition metric on a held out split | leaderboard grader | CPU or GPU, hours |
| [GEPA / DSPy tasks](https://github.com/gepa-ai/gepa) | HotpotQA, HoVer, IFBench, PUPA | a prompt file | pass rate on a train split | `heldout`: pass rate on a validation split | LLM calls per verify |

AlgoTune is the best first target: it is the same shape as `examples/sortbench` 154 times over, it
runs on a laptop, correctness is checked by the task itself, and the reference-relative speedup is
already the number the leaderboard reports. Freezing a dozen of its tasks with `bench freeze` gives
the L2 gate the six informative tasks it needs. Circle packing is the best cheap non-code-shaped
target: the artifact is a program, but the thing being optimized is a geometric construction with a
known best, so the normalized score has a real ceiling.

Two shapes to be careful with. Loss-based ML tasks are noisy across seeds; set `repeats` and a
`min_improvement` above the spread you measure, or the ledger fills with keeps that are noise.
Prompt-optimization tasks overfit the train split within a handful of tries; `heldout` is not
optional there.

What is missing from all of these is a set of tasks that are not code at all in the artifact: a
trading strategy against a held out year, a draft against a rubric script. Those have to be built per
project. The contract is the same; only the verifier differs.
