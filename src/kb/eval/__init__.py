"""The extraction evaluation layer (0.4.0): a blind second extraction, deterministic alignment, a judge, findings.

kb makes no model calls here either: the user's agent follows the briefs, and kb validates what it writes.
"""


class EvalError(ValueError):
    """A usage or state error of `kb eval` (exit 2)."""
