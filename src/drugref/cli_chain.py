# src/drugref/cli_chain.py
"""Chain planning: everything that can settle an invocation BEFORE a database exists.

EXTRACTED FROM cli.py in slice 5c.4, along the seam PROJECT-NOTES named -- the DB-free
argument layer versus the handlers that take a connection. cli.py was 508 lines, over
CLAUDE.md's ~500 cap, and the signing slice adds seven handlers, so the split had to
happen before them rather than after.

THIS SIDE MOVED, NOT THE HANDLERS, and the reason is structural rather than aesthetic.
cli.STEPS eagerly references the `_run_*` wrappers, so cli must import whatever module
holds them; `_handle_chain` calls the three functions below, so that module would have
to import cli. The imports are mutual, and Python raises AttributeError on a
partially-initialised module the moment the handler module is imported first.

THIS MODULE IMPORTS NOTHING FROM drugref, which is what makes that cycle impossible
rather than merely absent -- pinned by test_cli_chain_imports_nothing_from_drugref. If
a future change appears to need a drugref import here, the layering is wrong, not the
test.

DETERMINISTIC BUT NOT FILESYSTEM-FREE: `resolve_inputs` globs the downloads tree, so
its tests want a tmp_path and nothing more.
"""
import argparse
import logging
import pathlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestStep:
    """One orchestrator, as the CLI sees it.

    `inputs` pairs an ARGUMENT NAME with a GLOB relative to --downloads, and both
    consumers read the same tuple: the per-source subcommand turns each name into a
    required `--name PATH` flag, and the chain resolves the same names by glob. One
    declaration, so a step cannot grow an input the chain does not know about.

    `secondary` names the inputs this step READS BUT DOES NOT DATE (#60). A step
    records one release tag, describing its PRIMARY authority; mesh-relations reads
    two -- MED-RT states the rule, MeSH defines its object -- and writes one
    ingest_run row under source='MED-RT'. So its desc/supp inputs are dated by the
    mesh step and merely consumed here, and check_release_agreement must not read
    that as one file claimed to be two releases.

    `packaged_defaults` names the inputs that SHIP INSIDE THE PACKAGE rather than
    being downloaded (fix round 1, slice 5c.2's onchigh step) -- as (name, path)
    pairs, `path` being the file drugref carries under its own `data/` directory.
    Both consumers `inputs` already serves read this the same way: `build_parser`
    uses it as the per-source flag's `default=` instead of requiring `--name PATH`,
    and `resolve_inputs` below falls back to it when the declared glob matches
    NOTHING under `--downloads` -- a chain that selects this step must not abort
    just because no copy of drugref's own file happens to sit in the downloads tree.
    IT LIVES HERE, ON THE DECLARATION, RATHER THAN AS A NAME CHECK IN
    `resolve_inputs` OR `build_parser`, because a name-based special case
    (`if step.name == "onchigh"`) is a second list that can drift from `inputs` --
    exactly the failure mode `secondary`'s own validation below already guards
    against for a different field.

    It names INPUTS, not paths, because the declaration belongs beside the glob it
    qualifies and has to survive a glob's filename changing between releases.
    """
    name: str
    inputs: tuple[tuple[str, str], ...]
    runner: Callable[[object, dict[str, pathlib.Path], str], object]
    secondary: tuple[str, ...] = ()
    packaged_defaults: tuple[tuple[str, pathlib.Path], ...] = ()

    def __post_init__(self):
        # A typo here would exempt nothing and leave the chain refusing the very
        # invocation the exemption exists to allow -- a silent failure, in the field,
        # of a check whose whole job is to be loud. Raised at import, where STEPS is
        # built, so it cannot reach an operator.
        undeclared = set(self.secondary) - {name for name, _ in self.inputs}
        if undeclared:
            raise ValueError(
                f"{self.name}: secondary names an input this step does not declare: "
                f"{', '.join(sorted(undeclared))}")

        # SAME GUARD, SAME REASON, for the field fix round 1 added: a typo in
        # packaged_defaults would silently grant no fallback, so a chain missing the
        # real file would fail exactly as it did before this field existed -- loud at
        # import, not a mystery an operator hits in the field.
        undeclared_defaults = ({dname for dname, _ in self.packaged_defaults}
                               - {name for name, _ in self.inputs})
        if undeclared_defaults:
            raise ValueError(
                f"{self.name}: packaged_defaults names an input this step does not "
                f"declare: {', '.join(sorted(undeclared_defaults))}")


class ChainError(Exception):
    """A chain invocation that cannot be run without recording something untrue.

    One base so `main` catches the family rather than an ever-growing tuple, and so a
    future pre-flight check is caught by construction rather than by remembering.
    """


class InputResolutionError(ChainError):
    """A chain glob matched no file, or more than one.

    BOTH are errors, and the second is the one that bites: two releases left in one
    directory is the ordinary way this goes wrong, and silently taking either would
    record the wrong bytes as this run's provenance.
    """


class ReleaseError(ChainError):
    """A release tag that cannot be recorded honestly: absent, or self-contradicting.

    `ingest_run` IS HISTORY -- append-only, never corrected -- so a wrong tag is not a
    mistake an operator can take back. `writer` exists (db/025) precisely so a stale
    projection is visible; provenance that is confidently wrong defeats it more
    thoroughly than provenance that is missing.
    """


def _release_flag(step: IngestStep) -> str:
    """`mesh-relations` -> `mesh_relations_release`, the argparse destination."""
    return f"{step.name.replace('-', '_')}_release"


def resolve_inputs(downloads: pathlib.Path,
                   step: IngestStep) -> dict[str, pathlib.Path]:
    """Resolve one step's inputs under `downloads`, by the globs it declares.

    GLOBS RATHER THAN FIXED NAMES, because the real layout is irregular and a tidy
    invented convention would match nothing: releases carry their version in the
    filename (UNII_Records_26Feb2026.txt, Core_MEDRT_2026.07.06_XML.xml) and a fixed
    name would go stale on the next download.

    A ZERO-MATCH INPUT WITH A PACKAGED DEFAULT (fix round 1) resolves to that default
    instead of raising: `onc_high_priority.toml` ships inside the drugref package, not
    under `--downloads`, so an operator's downloads tree legitimately never contains
    it, and the chain must not abort for that reason alone. This is a FALLBACK, not a
    fixed answer -- an operator who deliberately drops a same-named override under
    `--downloads` still gets it back here, because that branch is only reached when
    the glob found NOTHING. Ambiguity (2+ matches) still raises regardless of any
    default: a packaged file breaking a tie between two conflicting downloaded ones
    would be the wrong file winning silently, which is worse than the operator's own
    conflict was.
    """
    defaults = dict(step.packaged_defaults)
    resolved = {}
    for name, pattern in step.inputs:
        matches = sorted(downloads.glob(pattern))
        if len(matches) == 0 and name in defaults:
            # ANNOUNCED, not silent. `glob` is non-recursive while the rest of the
            # download tree is nested (tables_as_csv/items.csv, GSRS/dump-*.gsrs), so
            # an operator who drops a corrected list one directory deeper matches
            # nothing here and gets drugref's packaged file instead -- substituting
            # our clinical content for theirs, with exit 0 and no record anyone reads.
            # (`ingest_run` does store the checksum, but nobody queries a checksum to
            # answer "did my edit land?".) WARNING so it survives a skim of the log.
            log.warning(
                "%s: no file matches '%s' under %s -- using the packaged default %s",
                step.name, pattern, downloads, defaults[name])
            resolved[name] = defaults[name]
            continue
        if len(matches) != 1:
            # "found N files" (not just "found N"): this branch only ever fires for
            # 0 or 2+ matches, so the plural reads correctly in both cases, and it is
            # the phrase an operator scanning a wall of stderr can grep for.
            raise InputResolutionError(
                f"{step.name}: expected exactly one file matching '{pattern}' under "
                f"{downloads}, found {len(matches)} files"
                + (f": {', '.join(m.name for m in matches)}" if matches else ""))
        resolved[name] = matches[0]
    return resolved


def selected_steps(args: argparse.Namespace,
                   steps: tuple[IngestStep, ...]) -> tuple[tuple[IngestStep, str], ...]:
    """The steps this chain invocation includes, in STEPS order, with their releases.

    SUPPLYING A RELEASE IS THE OPT-IN. No default set, no skip-list: a chain that ran
    feeds nobody named would record provenance nobody stated, and this project does
    not guess provenance. Returning them in STEPS order rather than flag order is what
    makes the dependency order unbreakable from the command line.

    PRESENCE, NOT TRUTHINESS, is what selects a step, and the difference is the trap
    the spec's own list names: `--medrt-release ""` is a flag the operator DID pass,
    and testing truthiness silently dropped the step it asked for -- a chain that
    reports success having never touched a feed the command line named. Absent is the
    opt-out (None); empty or blank is an error. "A convention that silently matches
    nothing is worse than none" applies to flag values exactly as it does to globs.

    THE STEP TABLE IS A PARAMETER, not a module global, and that is what let this
    function move here at all: a function's free variables resolve against the
    namespace of the module it is DEFINED in, so reading `STEPS` from a file that does
    not define it is a NameError. It is also the shape its sibling already had --
    check_release_agreement takes its plan explicitly -- so the global read was the
    anomaly. Required, with no default: an implicit table is the hidden state being
    removed here, and a wrong or empty default would select nothing while reporting
    success, which is the failure this module's own docstrings warn against three
    times over.
    """
    selected = []
    for step in steps:
        release = getattr(args, _release_flag(step), None)
        if release is None:
            continue
        if not release.strip():
            raise ReleaseError(
                f"--{step.name}-release was given an empty tag. It is the string "
                "recorded as this run's provenance, so it cannot be blank; omit the "
                "flag to leave the step out of the chain.")
        selected.append((step, release))
    return tuple(selected)


def check_release_agreement(
        plan: Sequence[tuple[IngestStep, str, dict[str, pathlib.Path]]]) -> None:
    """Refuse a chain in which one FILE is claimed to be two different releases.

    THE STEPS OVERLAP, and that is not incidental: `medrt` and `mesh-relations`
    resolve the SAME Core_MEDRT_*_XML.xml. Their release tags are stated
    independently, so `--medrt-release 2026.07.06 --mesh-relations-release 2026.05.04`
    writes two different releases into ingest_run FROM IDENTICAL BYTES. One of them is
    false, and ingest_run is history: nothing can take it back.

    `mesh` and `mesh-relations` also share desc/supp, and that overlap is NOT a
    conflict (#60): mesh-relations declares them `secondary`, so it reads them without
    dating them. Comparing those claims refused the documented four-source invocation
    for a disagreement that was never one -- two true statements about two different
    authorities.

    That is worse than a missing tag. db/025 added `writer` so an operator could see
    that one half of MED-RT is a release behind the other; this makes the two halves
    disagree on purpose, so the signal reports staleness that does not exist -- or
    hides staleness that does. A pre-flight check costs nothing and the alternative
    is uncorrectable.

    Pure, and run over the resolved plan rather than over the flags, because the
    question is about PATHS: two globs that happen to name one file must agree even
    though the flags look independent.
    """
    stated: dict[pathlib.Path, tuple[str, str]] = {}   # path -> (release, step name)
    for step, release, paths in plan:
        for name, path in paths.items():
            if name in step.secondary:
                # READ, NOT DATED. This step states no release for this file, so it
                # makes no claim that could contradict another step's. Skipping the
                # record entirely (rather than recording and tolerating a mismatch)
                # is what keeps a file dated by NO step from silently agreeing with
                # itself.
                continue
            first_release, first_step = stated.setdefault(path, (release, step.name))
            if first_release != release:
                raise ReleaseError(
                    f"{path} is read by both {first_step} and {step.name}, which were "
                    f"given different release tags ('{first_release}' and "
                    f"'{release}'). The same bytes cannot be two releases, and "
                    "ingest_run is history -- it cannot be corrected afterwards.")
