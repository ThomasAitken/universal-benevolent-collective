# Repository instructions

## Python

- The project targets Python 3.13. Do not add
  `from __future__ import annotations`; it is unnecessary for this codebase.
- Use current annotation syntax and `typing.Self` when a class method needs to
  refer to its own class before the class body has finished executing.
