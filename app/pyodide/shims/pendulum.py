"""Stdlib stand-in for the slice of ``pendulum`` the toolkit uses.

``pendulum`` 3 ships a Rust extension with no Pyodide wheel. The toolkit calls
exactly three things (``ena_submission_toolkit``):

* ``pendulum.now().format("YYYYMMDD-HHmmss")`` — submission aliases;
* ``pendulum.parse(text, exact=True)`` → ``Date``, raising
  ``pendulum.parsing.ParserError`` — hold-until validation;
* ``pendulum.today().date().add(years=n)`` and comparing ``Date``s.

Browser bundle only: it is never on the CPython path, where the real
``pendulum`` is installed. Upstream fix is the toolkit using ``datetime``.
"""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace


class ParserError(ValueError):
    pass


parsing = SimpleNamespace(ParserError=ParserError)

# Longest tokens first, so "MM" is not eaten by "mm" and vice versa.
_FORMAT_TOKENS = (("YYYY", "%Y"), ("MM", "%m"), ("DD", "%d"), ("HH", "%H"), ("mm", "%M"), ("ss", "%S"))


class Date(_dt.date):
    def add(self, years: int = 0) -> Date:
        try:
            return self.replace(year=self.year + years)
        except ValueError:  # 29 February into a non-leap year, as pendulum does
            return self.replace(year=self.year + years, day=28)


class DateTime(_dt.datetime):
    def format(self, fmt: str) -> str:
        for token, directive in _FORMAT_TOKENS:
            fmt = fmt.replace(token, directive)
        return self.strftime(fmt)

    def date(self) -> Date:  # type: ignore[override]
        return Date(self.year, self.month, self.day)


def now() -> DateTime:
    return DateTime.now()


def today() -> DateTime:
    return DateTime.combine(_dt.date.today(), _dt.time())


def parse(text: str, exact: bool = False) -> Date:
    # ponytail: dates only — the toolkit never parses a time or duration.
    try:
        parsed = _dt.date.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise ParserError(str(exc)) from None
    return Date(parsed.year, parsed.month, parsed.day)
