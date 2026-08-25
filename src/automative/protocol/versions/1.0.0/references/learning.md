# Learning

The strategy catalogue (`.automative/strategies.jsonl`) is shared across runs of this project and,
once promoted, across projects. Entries are only ever added or amended by small operations. The CLI
decides when an entry becomes `validated` or `rejected` from the evidence.

Record an entry when:

- a keep shows a lever (`works`: what to do and when it applies);
- a discard shows a dead end with a clear cause (`fails`);
- you learn something about the artifact or the metric that future runs need (`insight`);
- something breaks the build or the verifier in a way that is not obvious (`avoid`).

Format: `automative strategy add --kind works --when "<when it applies>" "<the action>"`.

Do not record routine parameter tweaks, restatements of the goal, or anything you cannot phrase as an
action someone else could take. Cite the entries you apply with `--strategies` on `try` so the
evidence accrues to them. Write entries the way `references/writing.md` says: plain, specific, with
the number.
