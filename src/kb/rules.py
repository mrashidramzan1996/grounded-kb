"""The working rules and the playbook kb gives coding agents (session brief, AGENTS.md, the Claude Code skill)."""

AGENT_RULES: list[str] = [
    "1. Use the kb context injected at session start, or run `kb context --changed` / `kb context --module MOD-x`.",
    "2. Cite KB IDs for every statement about the product.",
    "3. Never act against a live ADR or INV. To change one, add a superseding record in your PR and stop for review.",
    "4. When implementing a requirement, put `r[impl <ID>]` in the code and `r[verify <ID>]` in its test, using the requirement's ID.",
    "5. Finish with `kb check --changed`; the task is not done until it passes.",
    "6. Drive the knowledge base yourself: run `kb todo`, do every item under Do, and ask the user every item under Ask the user, offering its options. Record each fact the user gives about a requirement (a target, how to check it, which document is right) with `kb clarify` and write each handle into kb.config.yaml, then follow up as the option says. Never invent a target, quote, handle or approval.",
]

# How an agent works through `kb todo` (A-18). The one source for the AGENTS.md block and the kb skill.
PLAYBOOK: list[str] = [
    "1. When the user asks you to set up or update the knowledge base, or when the session brief says there is kb work, run `kb todo --format json`.",
    "2. Do every `do` item, using its `command` when it has one. For a brief (`.kb/cache/ingest/brief.md` or `.kb/cache/redraft/brief.md`), follow it, then run `kb ingest --report` or `kb redraft --report` and fix what they reject.",
    "3. After `kb ingest --report` or `kb redraft --report` passes, commit the changes (before the baseline: on the current branch; after it: on your pull-request branch).",
    "4. Collect every `ask` item. Ask the user all of them in one message, numbered, showing the evidence and the lettered options. Accept short answers like \"1a, 2b\".",
    "5. If the user already answered an ask in this conversation (for example keep it flagged, or not yet), do not ask it again in the same session.",
    "6. Never answer an ask or choose an option yourself, even when a document seems to settle it; only the user answers.",
    "7. Answer owner and approver questions first: write the handles into kb.config.yaml and commit that file on its own before running any `kb redraft`.",
    "8. For each other answer, do what its option's `then` says. If the user gives a fact about a requirement (a target, how to check it, which document is right), record it first: `kb clarify --topic \"<short topic>\" --text \"<the user's answer as one sentence>\" --by <handle>`.",
    "9. `--by` is the handle of the person who answered. If you do not know it, ask for it together with the questions; never guess it from git config or invent one.",
    "10. When the user answered several questions, record every answer with `kb clarify` first, then run one `kb redraft <ID> <ID> … --new` for all of them.",
    "11. Run `kb todo` again. Repeat until there are no `do` items and no ask you have not yet put to the user in this session, then run `kb check --strict`.",
    "12. Before the baseline exists, work on the current branch. After it, make changes on a branch and open a pull request; never merge it yourself.",
]


def render(template: str) -> str:
    """Fill a template's `{{rules}}` and `{{playbook}}` placeholders."""
    return template.replace("{{rules}}", "\n".join(AGENT_RULES)).replace("{{playbook}}", "\n".join(PLAYBOOK))
