"""The working rules kb gives coding agents (session brief, AGENTS.md)."""

AGENT_RULES: list[str] = [
    "1. Use the kb context injected at session start, or run `kb context --changed` / `kb context --module MOD-x`.",
    "2. Cite KB IDs for every statement about the product.",
    "3. Never act against a live ADR or INV. To change one, add a superseding record in your PR and stop for review.",
    "4. When implementing a requirement, put `r[impl <ID>]` in the code and `r[verify <ID>]` in its test, using the requirement's ID.",
    "5. Finish with `kb check --changed`; the task is not done until it passes.",
]
