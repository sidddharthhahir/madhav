"""Answer generation and the citation guarantee that makes it trustworthy.

Note: the `validate` submodule is deliberately NOT re-exported under its own
name -- doing so shadows the module with the function, so
`from gita.answer import validate as V` would silently hand back the function
and every `V.<anything_else>` lookup would fail. The function is exposed as
`validate_answer` instead.
"""

from . import validate as validate_module
from .validate import (Citation, Report, Verdict, cited_verse_ids,
                       known_verse_ids)
from .validate import validate as validate_answer

__all__ = ["Citation", "Report", "Verdict", "cited_verse_ids",
           "known_verse_ids", "validate_answer", "validate_module"]
