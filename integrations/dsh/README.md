# automative-dsh

Automative enforcement as a native [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
plugin. It puts the same controls the Claude Code hooks provide onto dsh's typed interception points,
so a run inside `dsh` gets scope refusals, protected-file detection, the wall-clock guard, the heartbeat,
and the "do not stop" steer without going through the generic `dsh-hooks-claude-code` bridge.

| dsh point | CLI event | What happens |
|---|---|---|
| `agent/session-start` | `session-start` | the brief is injected into the first request |
| `agent/pre-step` | `prompt-submit` | one status line is appended to each prompt |
| `tools/pre-execute` | `pre-tool-use` | `edit`, `write`, and `bash` calls that touch protected files, files outside `scope`, git history, or harness config get `{kind: 'deny'}` |
| `tools/post-execute` | `post-tool-use` | a protected file that changed anyway is restored from git, the run halts, and the result is blocked with the reason |
| `tools/post-execute` | `post-tool-batch` | when the wall-clock budget is spent, a notice rides along as extra context |
| `agent/turn-stopping` | `stop` | an active run steers the agent back into the loop; the CLI pauses the run as stalled after three refusals |

Every point also refreshes the hook heartbeat, so `enforcement.require_hooks: true` works under dsh.

## Where the policy lives

The plugin does not reimplement the rules. Each point runs `automative hook <event>` through
`ctx.shell` (so it executes in the same sandbox as the agent's own bash), feeds it the hook JSON on
stdin, and maps the answer onto dsh's decision types. That keeps one implementation of scope
classification, hash checks, denial counting, and stale-context refusal, which is the part that must
not drift between runtimes. The cost is one subprocess per intercepted tool call, about the same as
the bridge. What is native is the interception surface: typed `deny` and `block` decisions, a
`steer()` instead of a Stop-hook exit code, and the wall-clock guard, which Claude Code's
`PostToolBatch` bridge does not map.

There is no dsh equivalent of Claude Code's `ConfigChange` hook. dsh reads its configuration at boot
from YAML patches, and the headless profile runs with hot reload off, so there is no live config to
guard. The CLI still refuses to work if `AUTOMATIVE.md` or the pinned protocol changes.

## Install

```sh
cd integrations/dsh
pnpm install
pnpm run build          # lib/
pnpm test               # typecheck + unit tests for the payload/answer mapping
```

Then make the package resolvable from the dsh profile you use (a profile can hold out-of-tree
plugins; `dsh --profile web --dump-config` shows the tree it boots) and mount it with the patch:

```sh
export AUTOMATIVE_PLUGIN_ROOT=/path/to/Automative
export AUTOMATIVE_PROJECT_DIR=/path/to/your/project
dsh --profile headless --patch /path/to/Automative/integrations/dsh/cordis.patch.yml \
  "Run automative session brief, read the protocol it names, and follow it until the run ends."
```

`automative bench run --driver dsh` does exactly this for each benchmark cell, which is the point:
the same protocol version can now be scored under two runtimes.

## Status

Typechecked and unit tested against the published `0.1.1-rc.2` dsh packages. It has not been run
end to end inside a live dsh session yet; dsh is in developer preview and warns of breaking changes,
so expect to re-pin the peer dependencies. If a point stops compiling, `src/bridge.ts` (the pure
mapping) is unlikely to be the part that broke.
