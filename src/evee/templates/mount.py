r"""shaft_sensor_mount — a clamp hub with a flat platform for a sensor board.

The simple version of measuring shaft rotation: bolt an orientation IMU to the shaft
and let it report its own angle. No gear train, no pinion, no magnet, no centre
distance to hold — the sensor turns with the thing being measured, which is why this
is four parameters and a plate rather than three templates that have to agree.

* **The bore is the only fit that matters.** Everything else is a plate with holes
  in it. Grub screws take up slack by shoving the shaft to the far side of the bore,
  so a loose bore is a mount that sits crooked, and a crooked IMU reads a constant
  offset in two axes that looks exactly like a calibration problem.

* **The platform sits under the hub, not over it.** Printed hub-up, the bore comes
  out round and vertical, which is the one dimension that has to be accurate. Put
  the platform on top and the bore prints sideways as a bridged hole.

* **The board clears the hub rather than overlapping it.** It has to: the shaft runs
  on through the hub in both directions, so there is nowhere over the axis to put a
  board. The offset is derived from the hub and the board, not typed in.

Print pose, looking along the shaft::

              grub screws
                  |
             [====##====]                <- hub, bore through
             |         |     [board]     <- standoffs, 2mm
        [====================#####====]  <- platform, on the bed
                  |
              shaft passes through
"""

from __future__ import annotations

from build123d import (
    Align,
    Box,
    Cylinder,
    Location,
    Part,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evee.templates.errors import TemplateError
from evee.templates.gear import ClampHubSpec, _grub_hub_parts

__all__ = [
    "PART_NAMES",
    "ShaftSensorMountParams",
    "board_offset",
    "inner_dims",
    "resolved_spec_sentence",
    "shaft_sensor_mount",
]

PART_NAMES = ("mount",)

#: Gap between the hub's outside and the nearest edge of the board.
_HUB_GAP = 1.5

#: Material left around a board hole. These are CLEARANCE holes — a screw passes
#: through and pulls against a nut, it does not cut a thread and wedge the boss
#: open — so this is far less than a self-tapping boss needs. A standard M2.5
#: standoff is 5mm around a 2.7mm hole, and that is 1.15mm of wall.
_HOLE_WALL = 1.0

#: Cutters overshoot by this so no boolean face is coincident with an existing one.
_OVERCUT = 1.0


class ShaftSensorMountParams(BaseModel):
    """Validated parameters for :func:`shaft_sensor_mount`.

    Board dimensions are inputs rather than a built-in table: the same mount takes a
    BNO08x, an MPU-6050 or anything else with four corner holes, and a table of
    board sizes in here would be a second copy of a number that lives on the board.
    """

    model_config = ConfigDict(extra="forbid")

    bore: float = Field(
        gt=0,
        description=(
            "Hole for the shaft in mm. A close fit, not a clearance hole — grub "
            "screws turn slack into tilt. Measure the shaft, or print a fit trial."
        ),
    )
    hub: ClampHubSpec = Field(
        description=(
            "The clamp that grips the shaft. Use style='grub' unless the shaft can "
            "take a pinch ear reaching well past the hub."
        ),
    )
    board_length: float = Field(
        gt=0, description="Board's long dimension in mm. Runs tangentially."
    )
    board_width: float = Field(
        gt=0, description="Board's short dimension in mm. Runs radially, outward."
    )
    hole_spacing_tangential: float = Field(
        gt=0, description="Mounting hole spacing along the board's long axis, in mm."
    )
    hole_spacing_radial: float = Field(
        gt=0, description="Mounting hole spacing along the board's short axis, in mm."
    )
    hole_diameter: float = Field(
        default=2.7,
        gt=0,
        description=(
            "Clearance hole through the platform in mm — 2.7 passes M2.5. A "
            "clearance hole, not a pilot: the screw takes a nut underneath."
        ),
    )
    standoff_height: float = Field(
        default=2.0,
        ge=0,
        description=(
            "How far the board is lifted off the platform in mm. Not decoration: a "
            "board resting on its own solder joints rocks, and an IMU that rocks "
            "reports it. 0 sits the board flat."
        ),
    )
    standoff_diameter: float = Field(
        default=5.0, gt=0, description="Diameter of each standoff boss in mm."
    )
    platform_thickness: float = Field(
        default=3.0, gt=0, description="Platform thickness in mm."
    )
    margin: float = Field(
        default=2.0,
        ge=0,
        description="Platform material left outside the board's footprint, in mm.",
    )

    @model_validator(mode="after")
    def _check_geometry(self) -> "ShaftSensorMountParams":
        _validate(self)
        return self


def board_offset(params: ShaftSensorMountParams) -> float:
    """Distance from the shaft axis to the board's centre, in mm.

    Derived rather than typed: it is whatever puts the board's near edge clear of
    the hub, and a hand-typed value that disagrees is a board fouling the clamp.
    """
    return params.hub.diameter / 2 + _HUB_GAP + params.board_width / 2


def inner_dims(params: ShaftSensorMountParams) -> None:
    """A platform has no interior."""
    return None


def _validate(params: ShaftSensorMountParams) -> None:
    """Cross-field checks. Raises :class:`TemplateError` naming the bad values."""
    if params.hub.style != "grub":
        raise TemplateError(
            f"hub style={params.hub.style!r} is not supported here yet; a pinch ear "
            f"reaches far past the hub and this mount has a board sitting right "
            f"beside it (use style='grub')"
        )

    wall = (params.hub.diameter - params.bore) / 2
    if wall <= 0:
        raise TemplateError(
            f"hub diameter={params.hub.diameter}mm is not bigger than the "
            f"{params.bore}mm bore, so there is no hub"
        )

    for axis, spacing, extent in (
        ("tangential", params.hole_spacing_tangential, params.board_length),
        ("radial", params.hole_spacing_radial, params.board_width),
    ):
        if spacing >= extent:
            raise TemplateError(
                f"hole_spacing_{axis}={spacing}mm is not inside the "
                f"{extent}mm board it is measured across — the holes would sit off "
                f"the edge (check which board dimension runs which way)"
            )

    reach = params.standoff_diameter / 2
    if params.hole_diameter + 2 * _HOLE_WALL > params.standoff_diameter:
        raise TemplateError(
            f"standoff_diameter={params.standoff_diameter}mm leaves under "
            f"{_HOLE_WALL}mm of material around a {params.hole_diameter}mm hole "
            f"(raise it to at least {params.hole_diameter + 2 * _HOLE_WALL}mm)"
        )

    # A standoff at the board's corner must land on the platform, not over its edge.
    if reach > params.margin + (params.board_width - params.hole_spacing_radial) / 2:
        raise TemplateError(
            f"the standoffs overhang the platform: a {params.standoff_diameter}mm "
            f"boss at the hole pattern needs more than the {params.margin}mm margin "
            f"(raise margin, or shrink standoff_diameter)"
        )


def resolved_spec_sentence(params: ShaftSensorMountParams) -> str:
    """The Gate 1 read-back, templated from *validated* params."""
    offset = board_offset(params)
    outer = offset + params.board_width / 2 + params.margin
    lift = params.platform_thickness + params.standoff_height
    # A taper spends no depth: the hole is threaded over the whole wall either way,
    # at a diameter that closes as it goes. So the full wall is the honest figure,
    # and what needs saying is the two diameters it runs between.
    wall = (params.hub.diameter - params.bore) / 2
    hole = (
        f"tapped {_fmt(params.hub.grub_diameter)}mm"
        if params.hub.grub_taper <= 0
        else (
            f"tapered {_fmt(params.hub.grub_diameter + params.hub.grub_taper)}mm at "
            f"the hub face down to {_fmt(params.hub.grub_diameter)}mm where they "
            f"break through to the shaft"
        )
    )
    return (
        f"Shaft sensor mount for a {_fmt(params.bore)}mm shaft. "
        f"{_fmt(params.hub.diameter)}mm x {_fmt(params.hub.length)}mm hub gripped by "
        f"{params.hub.grub_count} grub screws {hole} through "
        f"{wall:.1f}mm of wall. "
        f"Board platform for a {_fmt(params.board_length)} x "
        f"{_fmt(params.board_width)}mm board, its centre {_fmt(offset)}mm out from "
        f"the shaft axis and reaching {_fmt(outer)}mm at the far edge. "
        f"{_fmt(params.hole_diameter)}mm clearance holes on a "
        f"{_fmt(params.hole_spacing_tangential)} x "
        f"{_fmt(params.hole_spacing_radial)}mm pattern, "
        f"{_fmt(params.standoff_height)}mm standoffs, so the board sits "
        f"{_fmt(lift)}mm above the platform's underside. "
        f"The board turns with the shaft, so allow slack in its cable — a rotation "
        f"you can measure is a rotation that winds the lead up."
    )


def _fmt(value: float) -> str:
    """Millimetre value without pointless trailing zeros."""
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #

_ON_BED = (Align.CENTER, Align.CENTER, Align.MIN)


def _platform(params: ShaftSensorMountParams) -> Part:
    """A disc around the hub, merged with a pad under the board."""
    disc = Cylinder(
        radius=params.hub.diameter / 2 + params.margin,
        height=params.platform_thickness,
        align=_ON_BED,
    )
    offset = board_offset(params)
    pad_length = params.board_width + 2 * params.margin
    pad = Box(
        pad_length,
        params.board_length + 2 * params.margin,
        params.platform_thickness,
        align=_ON_BED,
    ).locate(Location((offset, 0, 0)))

    # A neck between them, so a small board and a large hub still make one solid.
    neck = Box(
        offset, params.hub.diameter, params.platform_thickness, align=_ON_BED
    ).locate(Location((offset / 2, 0, 0)))
    return disc + neck + pad


def _standoffs(params: ShaftSensorMountParams) -> tuple[Part | None, Part]:
    """(bosses, holes). Holes are cut through the platform as well as the bosses."""
    offset = board_offset(params)
    bosses = None
    holes = None
    height = params.platform_thickness + params.standoff_height

    for radial in (-1, 1):
        for tangential in (-1, 1):
            at = (
                offset + radial * params.hole_spacing_radial / 2,
                tangential * params.hole_spacing_tangential / 2,
            )
            if params.standoff_height > 0:
                boss = Cylinder(
                    radius=params.standoff_diameter / 2,
                    height=height,
                    align=_ON_BED,
                ).locate(Location((at[0], at[1], 0)))
                bosses = boss if bosses is None else bosses + boss

            hole = Cylinder(
                radius=params.hole_diameter / 2,
                height=height + 2 * _OVERCUT,
                align=_ON_BED,
            ).locate(Location((at[0], at[1], -_OVERCUT)))
            holes = hole if holes is None else holes + hole

    return bosses, holes


def shaft_sensor_mount(params: ShaftSensorMountParams) -> tuple[Part]:
    """Build the mount. One solid, platform flat on the bed, hub standing up."""
    body = _platform(params)

    hub_solid, hub_cuts = _grub_hub_parts(
        params.hub,
        params.platform_thickness,
        params.hub.diameter / 2,
        min(params.platform_thickness / 2, _OVERCUT),
        params.bore,
    )
    body = body + hub_solid

    bosses, holes = _standoffs(params)
    if bosses is not None:
        body = body + bosses

    # Every solid on, then every hole through — a hole cut before a boss is unioned
    # would be filled back in and the part would look right on screen.
    body = body - hub_cuts
    body = body - holes
    body = body - Cylinder(
        radius=params.bore / 2,
        height=params.platform_thickness + params.hub.length + 2 * _OVERCUT,
        align=_ON_BED,
    ).locate(Location((0, 0, -_OVERCUT)))
    return (body,)


def build(params: ShaftSensorMountParams) -> tuple[Part]:
    """Registry entry point: build from a validated params model."""
    return shaft_sensor_mount(params)
