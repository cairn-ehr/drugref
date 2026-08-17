# Contributing to Drugref

## Code house rules

These rules apply to every language and every layer of the repository, including
tests, migrations, scripts, services, and GUI code.

1. **Document code inline.** Modules, public types, public members, and functions
   require docstrings or the language's equivalent documentation comments. Add
   focused inline comments where a constraint, invariant, security boundary, or
   non-obvious algorithm would otherwise be implicit. A descriptive name does not
   replace the mandatory function docstring.
2. **Do not use magic numbers.** Behavioural limits, durations, sizes, offsets, and
   other non-obvious numeric values must be named constants with documentation.
   Literal fixture data and self-describing declarative style values are not magic;
   repeated design values should still become named CSS custom properties, and
   non-obvious breakpoints or layout calculations need an adjacent purpose comment.
3. **Type dynamically typed code.** Python and other languages without enforced
   static types require complete parameter and return type hints. Avoid untyped
   escape hatches when a precise type can be expressed. TypeScript remains in strict
   mode, and explicit return types are preferred at module and component boundaries.
4. **Prefer pure reusable functions.** Keep transformation, validation, formatting,
   and decision logic free of I/O and mutable state where meaningful. Put reusable
   logic in focused modules; keep framework components, commands, and handlers thin.

New or changed code must follow these rules in the same change. When touching older
code, bring the affected unit into compliance rather than adding more local debt.
