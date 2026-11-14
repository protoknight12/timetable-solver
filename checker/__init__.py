"""Independent timetable checker (FE-04).

Takes the same plain data the solver got (``SolverInput``) and the solver's
``SolveResult`` and returns every rule it breaks. It imports only the standard
library and ``schema/``. It never imports or copies ``solver/``: every rule is
re-implemented here from TECH_STACK.md section 6 (CLAUDE.md rules 2 and 3).
"""

from checker.core import Violation, check

__all__ = ["Violation", "check"]
