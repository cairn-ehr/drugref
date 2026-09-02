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


@pytest.mark.parametrize("module_name", _SPL_TOOLS)
def test_no_spl_tool_destructures_the_registry(module_name):
    """Reads the tool's SOURCE, because the failure it guards is one no import
    can reach: it lives inside `main()`, behind a database connection and a
    19.3 GB corpus, so nothing short of a real run would execute the line."""
    module = importlib.import_module(module_name)
    source = open(module.__file__).read()
    for line in source.splitlines():
        if "load_registry(" in line and "=" in line:
            target = line.split("=", 1)[0]
            assert "," not in target, (
                f"{module_name} destructures load_registry: {line.strip()!r} -- "
                "Registry is a dataclass; read .by_name / .by_unii by name")
