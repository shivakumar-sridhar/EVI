"""Vetted parametric templates.

Every buildable shape lives here behind a Pydantic params model. Nothing in this
pipeline generates freeform geometry: the extraction model (Phase 4) picks a
template name and fills its params, and that is the whole of its authority.

``TemplateSpec.description`` is written to be read by a model — Phase 4 renders
the registry straight into the extraction prompt.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from build123d import Part
from pydantic import BaseModel

from evee.config import template_autoreload
from evee.templates.errors import (
    TemplateReloadError,
    UnknownTemplateError,
)
from evee.templates import box, gear, magnet_encoder_case, mount, yoke

__all__ = [
    "TEMPLATE_REGISTRY",
    "TemplateReloadError",
    "TemplateSpec",
    "UnknownTemplateError",
    "get_template",
    "refresh",
    "template_registry",
]


@dataclass(frozen=True)
class TemplateSpec:
    """Everything the pipeline needs to know about one template."""

    name: str
    description: str
    params_model: type[BaseModel]
    build: Callable[[BaseModel], tuple[Part, ...]]
    part_names: tuple[str, ...]
    #: Gate 1 read-back, templated in Python from validated params.
    spec_sentence: Callable[[BaseModel], str]
    #: Usable interior (l, w, h) in mm, or None for templates without an interior.
    inner_dims: Callable[[BaseModel], tuple[float, float, float] | None]

    def validate_params(self, params: dict) -> BaseModel:
        """Coerce a raw dict into this template's params model."""
        return self.params_model.model_validate(params)


TEMPLATE_REGISTRY: dict[str, TemplateSpec] = {
    "gear_pair": TemplateSpec(
        name="gear_pair",
        description=(
            "A spur gear and the pinion that meshes with it, for driving a rotary "
            "encoder from a turning shaft. Use for measuring rotation, stepping a "
            "sensor up or down from a shaft, or any two-gear reduction. Both gears "
            "share one module and pressure angle; face widths may differ, and "
            "only the narrower one carries contact. The read-back gives the "
            "centre distance to mount the axles at and the ratio, which is what "
            "multiplies an encoder's resolution: a 2:1 pair halves the degrees per "
            "count at the shaft. Bores are plain through holes and do not grip a "
            "smooth shaft on their own, so the gear can carry a slit clamp hub "
            "that grips one, and the pinion can carry a counterbored seat for a "
            "magnet. Produces two printable solids: the gear and the pinion."
        ),
        params_model=gear.GearPairParams,
        build=gear.build,
        part_names=gear.PART_NAMES,
        spec_sentence=gear.resolved_spec_sentence,
        inner_dims=gear.inner_dims,
    ),
    "encoder_yoke": TemplateSpec(
        name="encoder_yoke",
        description=(
            "A one-piece bracket that holds a magnetic encoder board over a pinion "
            "geared to a rotating shaft. A collar runs on the shaft and sets the "
            "gear pair's centre distance from inside the part, so nothing depends "
            "on where the bracket is clamped; a tether slot stops it turning and "
            "locates nothing. The board's mounting face is derived from the pinion "
            "length, the air gap and the height of the sensor package, because the "
            "gap is specified to the top of the package and not to the board. It "
            "also needs the gear and pinion tip diameters, not to build them, but "
            "to keep every column out of the disc the gear sweeps. Produces one "
            "printable solid."
        ),
        params_model=yoke.EncoderYokeParams,
        build=yoke.build,
        part_names=yoke.PART_NAMES,
        spec_sentence=yoke.resolved_spec_sentence,
        inner_dims=yoke.inner_dims,
    ),
    "shaft_sensor_mount": TemplateSpec(
        name="shaft_sensor_mount",
        description=(
            "A clamp hub on a shaft with a flat platform beside it for a sensor "
            "board — the simple way to measure shaft rotation, by bolting an "
            "orientation IMU to the shaft and letting it report its own angle. No "
            "gears, no centre distance. Board dimensions and hole pattern are "
            "inputs, so it takes any breakout with four corner holes. The board "
            "turns with the shaft, so its cable winds up. Produces one printable "
            "solid."
        ),
        params_model=mount.ShaftSensorMountParams,
        build=mount.build,
        part_names=mount.PART_NAMES,
        spec_sentence=mount.resolved_spec_sentence,
        inner_dims=mount.inner_dims,
    ),
    "magnet_encoder_case": TemplateSpec(
        name="magnet_encoder_case",
        description=(
            "A bracket that holds a magnetic encoder board upside down over a magnet "
            "glued to something that turns — a hinge, a knob, a pivot. Use when the "
            "rotating part can carry a magnet directly and there is no shaft to "
            "clamp and no room for gears. The case glues to the fixed side; the "
            "magnet comes up through a window in its base plate and the sensor chip "
            "sits directly above it. The post height is DERIVED from the magnet's "
            "stand-off, the air gap and the height of the sensor package, because "
            "the gap is specified to the package and not to the board. Inverting the "
            "board points its connectors at the magnet too, so their height is a "
            "required input and is checked against the room under the board. Board "
            "dimensions and hole pattern are inputs, so it takes any breakout with "
            "four corner holes. Sides are open, for cable access. Produces one "
            "printable solid."
        ),
        params_model=magnet_encoder_case.MagnetEncoderCaseParams,
        build=magnet_encoder_case.build,
        part_names=magnet_encoder_case.PART_NAMES,
        spec_sentence=magnet_encoder_case.resolved_spec_sentence,
        inner_dims=magnet_encoder_case.inner_dims,
    ),
    "gear_fit_trial": TemplateSpec(
        name="gear_fit_trial",
        description=(
            "Three copies of one spur gear, bored three different sizes, printed on "
            "one plate. Use when the thing the gear has to grip cannot be measured "
            "properly — a moulded knob, a taper, a worn shaft — and you would "
            "otherwise find out the bore is wrong one print at a time. Tooth "
            "geometry is shared, so whichever one fits IS the gear and meshes with "
            "the pinion from gear_pair. Produces three printable solids."
        ),
        params_model=gear.GearFitTrialParams,
        build=gear.build_trial,
        part_names=gear.TRIAL_PART_NAMES,
        spec_sentence=gear.trial_spec_sentence,
        inner_dims=gear.trial_inner_dims,
    ),
    "box_with_lid": TemplateSpec(
        name="box_with_lid",
        description=(
            "A rectangular box with a separate press-fit lid. Use for storage "
            "boxes, enclosures, trays, parts bins, and containers. Dimensions "
            "given are OUTER unless the request says otherwise; the usable "
            "interior is smaller by the wall thickness. Walls can carry "
            "rectangular openings ('ports') for cables, connectors or buttons, "
            "and the cavity floor can carry cylindrical posts ('standoffs') that "
            "a PCB rests on, screws into, or both. The base is solid unless "
            "'floor_holes' asks for holes straight through it, open on both "
            "sides — for a magnet, a sensor window, a shaft or a cable gland. "
            "Those holes are round by default and can be hexagonal instead, to "
            "seat a hex spacer or nut so it cannot turn. "
            "Produces two printable solids: the body and the lid."
        ),
        params_model=box.BoxWithLidParams,
        build=box.build,
        part_names=box.PART_NAMES,
        spec_sentence=box.resolved_spec_sentence,
        inner_dims=box.inner_dims,
    ),
}


# --------------------------------------------------------------------------- #
# Picking up edited templates without restarting the server
# --------------------------------------------------------------------------- #
#
# An MCP server is a subprocess the editor starts once and keeps for hours, and
# Python caches imported modules. So a template added or edited while it ran was
# invisible until the client reconnected — five times in one session, before anyone
# admitted that is a strange thing to have to know about a design tool.
#
# Reloading a *submodule* is not enough on its own: TEMPLATE_REGISTRY holds direct
# references to each params model and build function, captured when this file last
# ran. Reload gear.py alone and the registry still points at the previous classes.
# So any change reloads every template module and then re-executes this one.
#
# Which means TEMPLATE_REGISTRY is rebound to a *new dict*. Anything that did
# `from evee.templates import TEMPLATE_REGISTRY` keeps the old one and never sees a
# new template — the same shape of bug as patching a name at its definition site
# while the caller holds the value. Read it through template_registry() instead;
# `test_no_module_binds_the_registry_dict` is the guard.

_PACKAGE_DIR = Path(__file__).resolve().parent

#: Never reloaded. Its classes are caught by name elsewhere, and a reload would mint
#: new ones that the standing `except` clauses no longer match — see errors.py.
_STABLE = frozenset({"errors.py"})

#: Modification times of the sources behind the registry, as last loaded.
_STAMPS: dict[str, int] = {}


def _current_stamps() -> dict[str, int]:
    """mtime of every source file in this package, keyed by name."""
    stamps = {}
    for path in sorted(_PACKAGE_DIR.glob("*.py")):
        try:
            stamps[path.name] = path.stat().st_mtime_ns
        except OSError:
            continue
    return stamps


def _drop_bytecode() -> None:
    """Delete this package's cached bytecode so a reload recompiles from source.

    Python decides a ``.pyc`` is still good by comparing the source's mtime **in
    whole seconds** and its size. Save a file twice inside one second without
    changing its length — rename a string, flip a number — and the loader reuses
    the previous bytecode. ``importlib.reload`` then appears to work and quietly
    serves the older code, which is precisely the failure this whole mechanism
    exists to prevent, wearing a better disguise.

    The stamps here are nanoseconds, so the change is detected even when the loader
    cannot see it; dropping the cache is what makes the detection count.
    """
    for path in sorted(_PACKAGE_DIR.glob("*.py")):
        if path.name in _STABLE:
            continue
        cached = Path(importlib.util.cache_from_source(str(path)))
        try:
            cached.unlink()
        except OSError:
            continue
    importlib.invalidate_caches()


def refresh() -> None:
    """Reload template sources that have changed on disk, if autoreload is on.

    Cheap when nothing moved: a stat per file, against an OpenCascade boolean on
    the other side of the call.

    Raises:
        TemplateReloadError: a source would not import — a half-saved file, most
            likely. The stamps are left untouched so the next call tries again,
            and the error says which file rather than quietly serving stale code.
    """
    global _STAMPS

    if not template_autoreload():
        return

    current = _current_stamps()
    if current == _STAMPS:
        return

    package = sys.modules[__name__]
    _drop_bytecode()
    try:
        # Submodules first, then this file, which rebuilds the registry against
        # whatever the submodules now define.
        stable = {f"{__name__}.{Path(n).stem}" for n in _STABLE}
        for module in list(sys.modules.values()):
            name = getattr(module, "__name__", "")
            if name in stable:
                continue
            if name.startswith(f"{__name__}.") and name.count(".") == __name__.count(".") + 1:
                importlib.reload(module)
        importlib.reload(package)
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        raise TemplateReloadError(
            f"a template source changed but would not import: {error}"
        ) from error

    package._STAMPS = current


def template_registry() -> dict[str, TemplateSpec]:
    """The live registry, after reloading any template whose source changed.

    Read through ``sys.modules`` rather than the module-global, because a reload
    rebinds the name and this function object may predate it.
    """
    refresh()
    return sys.modules[__name__].TEMPLATE_REGISTRY


def get_template(name: str) -> TemplateSpec:
    """Look up a template, with a message listing what does exist."""
    registry = template_registry()
    try:
        return registry[name]
    except KeyError:
        raise UnknownTemplateError(
            f"no template named {name!r}; registered templates are "
            f"{sorted(registry)}"
        ) from None


_STAMPS = _current_stamps()
