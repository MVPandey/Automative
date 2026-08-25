"""``automative init``: rendering a first ``AUTOMATIVE.md`` and the ``.automative/`` bookkeeping."""

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from string import Template

from automative.errors import SpecError
from automative.paths import DOTDIR, SPEC_FILENAME

__all__ = ['GITIGNORE_ENTRIES', 'InitOptions', 'ensure_gitignore', 'render_spec', 'write_spec']

GITIGNORE_ENTRIES = (
    f'{DOTDIR}/state.json',
    f'{DOTDIR}/lock.json',
    f'{DOTDIR}/heartbeat',
    f'{DOTDIR}/budget.json',
    f'{DOTDIR}/.cli.lock',
    f'{DOTDIR}/runs/',
)


@dataclass(frozen=True, slots=True)
class InitOptions:
    """Everything the template needs."""

    goal: str
    metric_name: str
    direction: str
    verify: str
    scope: tuple[str, ...]
    protected: tuple[str, ...]
    guard: tuple[str, ...]
    protocol: str
    tags: tuple[str, ...] = ()
    timeout_s: int = 600
    repeats: int = 1
    min_improvement: str = '0'
    iterations: int = 30
    minutes: int = 120
    require_hooks: bool = True


def _yaml_list(items: tuple[str, ...]) -> str:
    return ', '.join(f'"{item}"' for item in items)


def render_spec(options: InitOptions) -> str:
    """Render the bundled template."""
    template = Template((resources.files('automative') / 'templates' / 'AUTOMATIVE.md.tmpl').read_text('utf-8'))
    return template.substitute(
        protocol=options.protocol,
        tags=_yaml_list(options.tags),
        metric_name=options.metric_name,
        direction=options.direction,
        verify=options.verify,
        guard=_yaml_list(options.guard),
        timeout_s=options.timeout_s,
        repeats=options.repeats,
        min_improvement=options.min_improvement,
        scope=_yaml_list(options.scope),
        protected=_yaml_list(options.protected),
        iterations=options.iterations,
        minutes=options.minutes,
        require_hooks='true' if options.require_hooks else 'false',
        goal=options.goal,
    )


def write_spec(root: Path, text: str, *, force: bool = False) -> Path:
    """Write ``AUTOMATIVE.md``; refuse to overwrite unless ``force``."""
    target = root / SPEC_FILENAME
    if target.exists() and not force:
        raise SpecError(f'{target} already exists; pass --force to overwrite')
    target.write_text(text, encoding='utf-8')
    (root / DOTDIR).mkdir(exist_ok=True)
    return target


def ensure_gitignore(root: Path) -> tuple[str, ...]:
    """Append the runtime-state entries to ``.gitignore``; return what was added."""
    path = root / '.gitignore'
    existing = path.read_text(encoding='utf-8').splitlines() if path.is_file() else []
    missing = tuple(entry for entry in GITIGNORE_ENTRIES if entry not in existing)
    if missing:
        prefix = '' if not existing or existing[-1] == '' else '\n'
        with path.open('a', encoding='utf-8') as handle:
            handle.write(prefix + '# automative runtime state\n' + '\n'.join(missing) + '\n')
    return missing
