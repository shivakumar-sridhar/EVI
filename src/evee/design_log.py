"""The design ledger — what was built, from which parameters, and when.

This exists because the alternative is measuring a printed STL to find out what it
was, and that is not a method, it is a rescue. It worked once: every parameter of
the shaft sensor mount was recovered from its mesh and confirmed by rebuilding it
to 0.016% of the original volume. It only worked because nothing about that part
was invisible in its geometry.

Plenty is. A parameter that chooses between two equal-volume arrangements leaves
no trace. So does one that was omitted and defaulted — the mesh shows the default's
consequence, never the fact that nobody typed it. And a template whose defaults
change later makes the same mesh imply different numbers depending on when you ask.

Two rules follow, and they are the whole design:

- **The RESOLVED parameters are recorded, never what was typed.** A record saying
  ``standoff_height: omitted`` cannot rebuild anything, which makes it decoration.
  What goes in is the post-validation model dump, defaults filled in, so a rebuild
  from this file is exact regardless of what the defaults have done since.
- **Writing here can never fail a design.** Same rule the print log follows: the
  STLs are already on disk and correct by the time this is called, and losing the
  bookkeeping is not a reason to lose the part. Every entry point swallows its own
  errors and reports by returning ``None``.

Versions group under a *name*, not a template. Two unrelated mounts built from
``shaft_sensor_mount`` are two parts, not v1 and v2 of one, and no rule about which
parameters "define" a part could tell them apart without guessing on the owner's
behalf. The name is theirs to give; it falls back to the template when they do not.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evee.config import OUTPUT_DIR

__all__ = [
    "DESIGN_LOG",
    "DesignRecord",
    "Recorded",
    "history",
    "latest",
    "record_design",
    "slug",
]

#: Beside the print log and the mesh state: all three are records of what was
#: actually done, as opposed to what the code is capable of doing.
DESIGN_LOG = OUTPUT_DIR / "design_log.jsonl"

#: What a name is allowed to look like once slugged. Anything else is a filename
#: waiting to go wrong, and these end up quoted in prose read back to a human.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: How a record got here. "design_part" was captured as it happened; "reconstructed"
#: was measured off a mesh afterwards and is only as good as that measurement.
_SOURCES = ("design_part", "reconstructed")


@dataclass(frozen=True)
class Recorded:
    """What :func:`record_design` actually did.

    The distinction matters to the caller: an unchanged re-run hands back the
    standing version rather than minting a new one, and a client that reported
    "saved as v7" every time it re-rendered the same shape would be lying.
    """

    record: "DesignRecord"
    #: False when the parameters matched the standing version and nothing was written.
    created: bool


@dataclass(frozen=True)
class DesignRecord:
    """One version of one named part."""

    name: str
    template: str
    version: int
    designed_at: datetime
    #: Every parameter with its resolved value, defaults filled in. What to READ.
    params: dict[str, Any]
    #: Only what was actually passed — what to REBUILD FROM. See :mod:`evee.cad`.
    params_input: dict[str, Any]
    spec_sentence: str
    stl_paths: dict[str, str]
    bounding_boxes: dict[str, dict[str, float]]
    #: sha256 over the canonicalised params, so an unchanged re-run is recognisable.
    fingerprint: str
    source: str
    note: str | None = None

    def summary(self) -> str:
        """One line, for reading back without dumping the whole parameter set."""
        when = self.designed_at.date().isoformat()
        tail = "" if self.source == "design_part" else f" [{self.source}]"
        return f"{self.name} v{self.version}  {when}  {self.template}{tail}"


def slug(name: str) -> str:
    """Normalise a human-typed part name into the key versions group under.

    Lowercased, spaces and underscores to hyphens, runs collapsed. Deliberately
    lossy and deliberately validated afterwards rather than silently sanitised into
    something unrecognisable: a name that cannot survive this is a name the owner
    should be told about, not one they should discover as a mystery second part.
    """
    slugged = re.sub(r"[\s_]+", "-", name.strip().lower())
    slugged = re.sub(r"[^a-z0-9-]", "", slugged)
    slugged = re.sub(r"-{2,}", "-", slugged).strip("-")
    if not _NAME_RE.match(slugged):
        raise ValueError(
            f"part name {name!r} does not reduce to a usable name (got "
            f"{slugged!r}); use letters, digits, spaces or hyphens"
        )
    return slugged


def fingerprint(params: dict[str, Any]) -> str:
    """A stable hash of a resolved parameter set.

    Sorted keys and a fixed separator, so the same parameters hash the same across
    processes and Python versions. Floats are left alone: 6.0 and 6 are the same
    design and json.dumps writes both as ``6.0`` only if they arrive that way, which
    is why this is a dedupe hint and not an identity — see :func:`record_design`.
    """
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _read_raw() -> list[dict[str, Any]]:
    """Every well-formed line in the ledger. A corrupt line is skipped, not fatal."""
    path = DESIGN_LOG
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _hydrate(entry: dict[str, Any]) -> DesignRecord | None:
    """A raw line to a record, or None if it is missing anything load-bearing."""
    try:
        return DesignRecord(
            name=entry["name"],
            template=entry["template"],
            version=int(entry["version"]),
            designed_at=datetime.fromisoformat(entry["at"]),
            params=entry.get("params") or {},
            params_input=entry.get("params_input") or entry.get("params") or {},
            spec_sentence=entry.get("spec_sentence") or "",
            stl_paths=entry.get("stl_paths") or {},
            bounding_boxes=entry.get("bounding_boxes") or {},
            fingerprint=entry.get("fingerprint") or "",
            source=entry.get("source") or "design_part",
            note=entry.get("note"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def history(
    name: str | None = None,
    template: str | None = None,
    limit: int | None = None,
) -> list[DesignRecord]:
    """Matching records, newest first.

    Newest first because the question is nearly always "what did we last do", and
    a caller that wants the whole story of a part reverses a short list cheaply.
    """
    records = [r for r in (_hydrate(e) for e in _read_raw()) if r is not None]
    if name is not None:
        wanted = slug(name)
        records = [r for r in records if r.name == wanted]
    if template is not None:
        records = [r for r in records if r.template == template]
    records.sort(key=lambda r: (r.designed_at, r.version), reverse=True)
    return records[:limit] if limit else records


def latest(name: str) -> DesignRecord | None:
    """The newest version of one part, or None if it has never been designed."""
    found = history(name=name, limit=1)
    return found[0] if found else None


def names() -> dict[str, list[DesignRecord]]:
    """Every part in the ledger, each with its versions newest first."""
    grouped: dict[str, list[DesignRecord]] = {}
    for record in history():
        grouped.setdefault(record.name, []).append(record)
    return grouped


def record_design(
    template: str,
    params: dict[str, Any],
    spec_sentence: str,
    params_input: dict[str, Any] | None = None,
    stl_paths: dict[str, Any] | None = None,
    bounding_boxes: dict[str, Any] | None = None,
    name: str | None = None,
    source: str = "design_part",
    note: str | None = None,
    designed_at: datetime | None = None,
) -> Recorded | None:
    """Append one version, or return the standing one when nothing changed.

    Re-running a design with identical parameters does not mint a new version. The
    design gate is an iteration loop by construction — the tool description tells
    the client to call it again — so counting calls would number the same shape v1
    through v9 and bury the versions that differ. Only the *immediately preceding*
    version is compared: going A, B, A back to an earlier shape is a real event and
    is recorded as one, because by then something was learned in between.

    Returns a :class:`Recorded` saying which of the two happened, or ``None`` if
    the ledger could not be written — never raises. The parts are already exported
    by the time this is called and a lost line of bookkeeping is not worth failing
    them over.
    """
    if source not in _SOURCES:
        raise ValueError(f"source must be one of {_SOURCES}, got {source!r}")

    try:
        key = slug(name) if name else slug(template)
    except ValueError:
        return None

    stamp = fingerprint(params)
    existing = history(name=key)

    # Unchanged re-run of the newest version: hand back what is already recorded.
    if existing and existing[0].fingerprint == stamp and existing[0].template == template:
        return Recorded(record=existing[0], created=False)

    record = DesignRecord(
        name=key,
        template=template,
        version=max((r.version for r in existing), default=0) + 1,
        # Overridable only for backfill, where the honest date is when the part was
        # designed rather than when somebody got around to writing it down.
        designed_at=designed_at or datetime.now(UTC),
        params=params,
        params_input=params_input if params_input is not None else params,
        spec_sentence=spec_sentence,
        stl_paths={k: str(v) for k, v in (stl_paths or {}).items()},
        bounding_boxes={k: dict(v) for k, v in (bounding_boxes or {}).items()},
        fingerprint=stamp,
        source=source,
        note=note,
    )

    payload = {
        "at": record.designed_at.isoformat(timespec="seconds"),
        "name": record.name,
        "template": record.template,
        "version": record.version,
        "fingerprint": record.fingerprint,
        "source": record.source,
        "note": record.note,
        "spec_sentence": record.spec_sentence,
        "params": record.params,
        "params_input": record.params_input,
        "stl_paths": record.stl_paths,
        "bounding_boxes": record.bounding_boxes,
    }
    try:
        DESIGN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with DESIGN_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
    except (OSError, TypeError, ValueError):
        return None
    return Recorded(record=record, created=True)
