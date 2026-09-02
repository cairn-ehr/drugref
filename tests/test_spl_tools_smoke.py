# tests/test_spl_tools_smoke.py
"""The SPL tools under `tools/` reach their first real call without breaking.

⇒ WHY THIS FILE EXISTS. `registry_read.load_registry` was given a named return
type carrying collision counts. `tools/spl_class_vocabulary_delta.py` and
`tools/spl_suppress_derive.py` both destructured its old 2-tuple, so both began
raising `ValueError: too many values to unpack` on their first line of real
work -- and the whole suite stayed green, because neither tool had a test of any
kind. One of them is the measurement the shipped matcher's own docstring cites
as evidence for deferring the class vocabulary.

These are deliberately SHALLOW. A tool that reads 19.3 GB cannot be run here, and
pinning its output would be pinning a measurement rather than a contract. What
they check is the thing that actually broke: that the module imports, and that
its use of shared library types still matches those types.
"""
import importlib
import pathlib

import pytest

from drugref import registry_read

_SPL_TOOLS = [
    "tools.spl_class_vocabulary_delta",
    "tools.spl_suppress_derive",
    "tools.spl_make_fixture",
]


@pytest.mark.parametrize("module_name", _SPL_TOOLS)
def test_the_tool_imports(module_name):
    """An import error here is a tool that cannot run at all."""
    assert importlib.import_module(module_name) is not None


def test_the_registry_type_cannot_be_DESTRUCTURED_by_a_tool():
    """The guard that makes the failure above impossible to repeat quietly.

    `Registry` is a frozen dataclass rather than a `NamedTuple` precisely so that
    `names, uniis = load_registry(conn)` raises at EVERY call site the moment the
    shape changes, rather than only at the ones whose arity stops matching.
    """
    registry = registry_read.Registry(
        by_name={}, by_unii={}, name_collisions=0, unii_collisions=0)
    with pytest.raises(TypeError, match="cannot unpack non-iterable"):
        _first, _second = registry


def _registry_call_lines(module_name):
    """The lines of a tool's SOURCE that bind the result of `load_registry`.

    Reads source rather than behaviour because the failure this guards is one no
    import can reach: it lives inside `main()`, behind a database connection and a
    19.3 GB corpus, so nothing short of a real run would execute the line.
    """
    module = importlib.import_module(module_name)
    source = pathlib.Path(module.__file__).read_text()
    return [line for line in source.splitlines()
            if "load_registry(" in line and "=" in line]


@pytest.mark.parametrize("module_name", _SPL_TOOLS)
def test_no_spl_tool_destructures_the_registry(module_name):
    """The rule, per tool so a failure names which one."""
    for line in _registry_call_lines(module_name):
        target = line.split("=", 1)[0]
        assert "," not in target, (
            f"{module_name} destructures load_registry: {line.strip()!r} -- "
            "Registry is a dataclass; read .by_name / .by_unii by name")


def test_the_destructuring_scan_HAS_something_to_check():
    """⇒ THE GATE ABOVE CAN FIND NOTHING AND STILL PASS, so this says how much it
    must find.

    `tools/spl_make_fixture.py` contains no `load_registry` at all, so its
    parametrisation ran zero assertions -- a gate that exists and never fires, the
    shape of issues 74, 66 and 76, inside the file written to stop a silent tool
    breakage. Worse, the same property makes the guard disarmable for the two tools
    that DO matter: alias the import, rename the function or split the call across
    two lines and the scan matches nothing and stays green.

    Two, because two tools read the registry today. A third would raise this
    number rather than slip past a scan that never looked at it.
    """
    found = {name: _registry_call_lines(name) for name in _SPL_TOOLS}
    matched = sum(len(lines) for lines in found.values())
    assert matched >= 2, (
        f"the destructuring scan matched {matched} line(s) across {_SPL_TOOLS} and "
        f"is therefore guarding almost nothing: {found}. Either a tool stopped "
        "reading the registry (drop it from _SPL_TOOLS and lower this number), or "
        "the call is written in a shape the scan cannot see (fix the scan).")


def test_the_registry_halves_cannot_be_MUTATED_by_a_consumer():
    """`frozen=True` stops rebinding a field and does nothing about the dict behind
    it. The registry is a snapshot of the identity spine read in one statement; a
    consumer that wrote into it would make the resolution depend on which caller
    ran first, and nothing downstream would say so.

    `drugcentral_resolve.Registry` has enforced this since it was written --
    same repo, same argument, one directory away.
    """
    registry = registry_read.Registry(
        by_name={"warfarin": "u1"}, by_unii={"ABC": "u1"},
        name_collisions=0, unii_collisions=0)
    with pytest.raises(TypeError):
        registry.by_name["aspirin"] = "u2"
    with pytest.raises(TypeError):
        registry.by_unii["DEF"] = "u2"
