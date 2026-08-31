r"""encoder_yoke — the bracket that holds a magnetic encoder over a geared pinion.

One printed part carries three things that must agree with each other: a collar that
runs on the shaft, an axle for the pinion, and a mounting face for the sensor board.
Splitting them across parts would put the AS5600's +/-1mm off-axis budget and the
gear pair's centre distance into a stack of assembly tolerances. Here they are
dimensions inside one solid.

* **The collar on the shaft is the datum.** Centre distance is then fixed by the
  part, not by wherever a bracket happened to clamp. Whatever stops the yoke turning
  — a tether to the instrument's handle — needs no precision at all, because it is
  not locating anything.

* **The air gap is derived, never typed.** It is measured to the *chip package*, and
  the package stands ~1.75mm off the board. Sizing the stack to the board face
  instead puts the pinion 1.75mm too low, which is a crash into the chip rather than
  a bad reading. ``board_face()`` does that arithmetic once.

* **Everything prints upward off the base plate.** No overhang anywhere: the collar
  and the axle boss are cylinders standing on the plate, the columns are vertical
  walls, and the only horizontal span — the beam under the board — is a bridge
  between two columns rather than a cantilever.

* **The gear's swept disc is a no-go volume.** The gear turns with the shaft, and
  its tip circle is larger than the distance from the shaft to the board's near
  mounting holes. Anything structural crossing the gear's station has to sit outside
  that radius, which is why the near columns start where they do rather than at the
  board's edge.

Print pose, looking along the shaft (Z up, X toward the pinion)::

              board face  ---- [==========================]  z = board_face()
                                |                        |
                          near column               far wall
                                |     [ pinion ]       |
        gear disc turns here .......  [ ###### ]  ......   (no structure inside r)
                                |     [ ###### ]       |
                                |      axle boss        |
        base plate  [=====##=====================================]  z = 0
                        collar
"""

from __future__ import annotations

import math

from build123d import (
    Align,
    Box,
    Cylinder,
    Location,
    Part,
)
from evee.templates.errors import TemplateError
from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "EncoderYokeParams",
    "PART_NAMES",
    "TemplateError",
    "board_face",
    "encoder_yoke",
    "inner_dims",
    "resolved_spec_sentence",
    "shoulder_face",
]

PART_NAMES = ("yoke",)

#: Clearance kept between anything printed and anything that turns. Under this and
#: a part that measures fine binds once a layer comes out fat.
_MIN_RUNNING_CLEARANCE = 0.4

#: Wall left around a tapped hole before the boss splits on the first screw.
_MIN_BOSS_WALL = 1.5

#: Cutters overshoot by this so no boolean face is coincident with an existing one.
_OVERCUT = 1.0

#: Height of an AS5600 in SOIC-8. The air gap is measured to the top of this, not to
#: the board it stands on.
_DEFAULT_CHIP_HEIGHT = 1.75


class EncoderYokeParams(BaseModel):
    """Validated parameters for :func:`encoder_yoke`.

    The gear pair's own numbers appear here as *clearance* inputs — tip diameters and
    the centre distance. The yoke does not build a gear; it has to keep out of the
    way of one, and it can only check that if it is told how big the gear is.
    """

    model_config = ConfigDict(extra="forbid")

    tube_diameter: float = Field(
        gt=0,
        description=(
            "Outside diameter of the shaft the collar runs on, in mm. Measure it — "
            "the collar bore and every radial clearance come off this."
        ),
    )
    centre_distance: float = Field(
        gt=0,
        description=(
            "Distance from the shaft axis to the pinion axis in mm. Must match the "
            "gear pair exactly: this is the number that makes the teeth mesh."
        ),
    )
    gear_tip_diameter: float = Field(
        gt=0,
        description=(
            "Tip diameter of the gear on the shaft, in mm. Nothing is built from it; "
            "it defines the disc that turns, which no structure may enter."
        ),
    )
    pinion_tip_diameter: float = Field(
        gt=0,
        description="Tip diameter of the pinion, in mm. Same purpose as above.",
    )
    pinion_length: float = Field(
        gt=0,
        description=(
            "Face width of the pinion in mm. Sets how far the board sits above the "
            "axle shoulder, so a pinion of the wrong length moves the air gap."
        ),
    )
    board_length: float = Field(
        gt=0,
        description="Sensor board's long dimension in mm. Runs tangentially.",
    )
    board_width: float = Field(
        gt=0,
        description=(
            "Sensor board's short dimension in mm. Runs radially, towards the shaft "
            "— which is what decides whether the board clears the turning tube."
        ),
    )
    hole_spacing_long: float = Field(
        gt=0,
        description="Mounting hole spacing along the board's long axis, in mm.",
    )
    hole_spacing_short: float = Field(
        gt=0,
        description="Mounting hole spacing along the board's short axis, in mm.",
    )
    hole_pilot: float = Field(
        default=2.1,
        gt=0,
        description=(
            "Pilot hole for the board screws in mm. A pilot for a self-tapper "
            "cutting its own thread, not a clearance hole: 2.1 suits M2.5."
        ),
    )
    air_gap: float = Field(
        default=1.0,
        gt=0,
        description=(
            "Gap from the magnet face to the top of the chip PACKAGE, in mm. The "
            "AS5600 wants 0.5 to 1.5. Not measured to the board — see chip_height."
        ),
    )
    chip_height: float = Field(
        default=_DEFAULT_CHIP_HEIGHT,
        gt=0,
        description=(
            "How far the sensor package stands off its board, in mm. 1.75 for the "
            "SOIC-8 the AS5600 comes in. This is the term everybody forgets."
        ),
    )
    axle_pilot: float = Field(
        default=2.5,
        gt=0,
        description="Tapping hole for the pinion axle in mm. 2.5 threads M3.",
    )
    boss_diameter: float = Field(
        default=7.0,
        gt=0,
        description=(
            "Axle boss diameter in mm. Its top face is the shoulder the pinion "
            "registers against, so it also has to be wider than the pinion's bore."
        ),
    )
    boss_height: float = Field(
        default=6.0,
        gt=0,
        description="How far the axle boss stands off the base plate, in mm.",
    )
    plate_thickness: float = Field(
        default=3.0,
        gt=0,
        description="Base plate thickness in mm.",
    )
    wall: float = Field(
        default=3.0,
        gt=0,
        description="Thickness of the columns and the far wall, in mm.",
    )
    collar_diameter: float = Field(
        default=10.0,
        gt=0,
        description="Outside diameter of the collar that runs on the shaft, in mm.",
    )
    collar_length: float = Field(
        default=5.0,
        gt=0,
        description=(
            "How far the collar stands off the plate, in mm. Add the plate to get "
            "the bearing length on the shaft."
        ),
    )
    tube_clearance: float = Field(
        default=0.3,
        gt=0,
        description=(
            "Gap per diameter between the collar bore and the shaft, in mm. This is "
            "a running fit — the shaft turns inside it — not a press fit."
        ),
    )
    tether_slot: float = Field(
        default=4.0,
        ge=0,
        description=(
            "Width of the slot in the base plate for a tie back to the handle, in "
            "mm. It stops the yoke turning and does nothing else. 0 omits it."
        ),
    )

    @model_validator(mode="after")
    def _check_geometry(self) -> "EncoderYokeParams":
        _validate(self)
        return self


# --------------------------------------------------------------------------- #
# The Z stack — one place, because everything downstream depends on it
# --------------------------------------------------------------------------- #


def shoulder_face(params: EncoderYokeParams) -> float:
    """Height of the face the pinion sits on, in mm above the bed."""
    return params.plate_thickness + params.boss_height


def board_face(params: EncoderYokeParams) -> float:
    """Height of the board's component face — the surface it bolts down against.

    Built up from the chip rather than down from the board: shoulder, pinion, air
    gap, then the height of the package itself. Leave the package out and the whole
    assembly sits 1.75mm low, which is the pinion touching the chip.
    """
    return (
        shoulder_face(params)
        + params.pinion_length
        + params.air_gap
        + params.chip_height
    )


def _column_x(params: EncoderYokeParams) -> tuple[float, float]:
    """(near, far) X of the two column faces nearest the shaft, in mm.

    The near column has to clear the gear's swept disc, and the gear is *wider* than
    the distance out to the board's near holes — so the limit is not the board, it
    is the chord of the gear's tip circle at the column's own Y.
    """
    half_span = params.hole_spacing_long / 2
    inner_y = half_span - params.wall / 2
    # Solve on the no-go circle itself, not on the tip circle plus a margin in X.
    # The column's nearest point is its inner *corner*, and adding the clearance to
    # X leaves that corner short of it by however much the corner is off-axis.
    keep_out = params.gear_tip_diameter / 2 + _MIN_RUNNING_CLEARANCE
    near = math.sqrt(max(keep_out * keep_out - inner_y * inner_y, 0.0))

    far = params.centre_distance + params.pinion_tip_diameter / 2
    return near, far + _MIN_RUNNING_CLEARANCE


def inner_dims(params: EncoderYokeParams) -> None:
    """A bracket has no interior. The registry asks; the answer is None."""
    return None


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def _validate(params: EncoderYokeParams) -> None:
    """Cross-field checks. Raises :class:`TemplateError` naming the bad values."""
    bore = params.tube_diameter + params.tube_clearance
    if params.collar_diameter <= bore + 2 * _MIN_BOSS_WALL:
        raise TemplateError(
            f"collar_diameter={params.collar_diameter}mm leaves "
            f"{(params.collar_diameter - bore) / 2:.2f}mm of wall over a {bore:.2f}mm "
            f"bore, under the {_MIN_BOSS_WALL}mm minimum (need at least "
            f"{bore + 2 * _MIN_BOSS_WALL:.2f}mm)"
        )

    if params.axle_pilot + 2 * _MIN_BOSS_WALL > params.boss_diameter:
        raise TemplateError(
            f"boss_diameter={params.boss_diameter}mm leaves under {_MIN_BOSS_WALL}mm "
            f"of wall around a {params.axle_pilot}mm tapped hole (raise it to at "
            f"least {params.axle_pilot + 2 * _MIN_BOSS_WALL}mm)"
        )

    # The boss stands at the pinion axis and the gear turns past it.
    boss_inner = params.centre_distance - params.boss_diameter / 2
    gear_tip = params.gear_tip_diameter / 2
    if boss_inner < gear_tip + _MIN_RUNNING_CLEARANCE:
        raise TemplateError(
            f"the axle boss reaches {boss_inner:.2f}mm from the shaft axis and the "
            f"gear's teeth reach {gear_tip:.2f}mm — they would collide (need "
            f"{_MIN_RUNNING_CLEARANCE}mm of clearance: raise centre_distance, or "
            f"shrink boss_diameter to {2 * (params.centre_distance - gear_tip - _MIN_RUNNING_CLEARANCE):.2f}mm)"
        )

    # The board is centred on the pinion axis because the sensor die is centred on
    # the board. Its near edge is what meets the turning shaft.
    board_edge = params.centre_distance - params.board_width / 2
    tube_surface = params.tube_diameter / 2
    if board_edge < tube_surface + _MIN_RUNNING_CLEARANCE:
        raise TemplateError(
            f"the board's near edge sits {board_edge:.2f}mm from the shaft axis and "
            f"the shaft's surface is at {tube_surface:.2f}mm, so the board would "
            f"foul the turning shaft (raise centre_distance to at least "
            f"{tube_surface + params.board_width / 2 + _MIN_RUNNING_CLEARANCE:.2f}mm, "
            f"which means more teeth on the gear)"
        )

    near, far = _column_x(params)
    if near + params.wall >= far:
        raise TemplateError(
            f"no room between the gear and the pinion for the board's columns: the "
            f"near one can start at {near:.2f}mm and the far wall at {far:.2f}mm"
        )

    hole_near = params.centre_distance - params.hole_spacing_short / 2
    if hole_near < near:
        raise TemplateError(
            f"the board's near mounting holes sit {hole_near:.2f}mm from the shaft "
            f"axis but the nearest a column can stand is {near:.2f}mm, because the "
            f"gear's tip circle sweeps everything inside that (a larger "
            f"centre_distance moves the holes out)"
        )

    if params.air_gap > 1.5:
        raise TemplateError(
            f"air_gap={params.air_gap}mm is outside the 0.5 to 1.5mm the AS5600 "
            f"specifies; the reading degrades rather than failing, which is worse"
        )


# --------------------------------------------------------------------------- #
# Read-back
# --------------------------------------------------------------------------- #


def _fmt(value: float) -> str:
    """Millimetre value without pointless trailing zeros."""
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def resolved_spec_sentence(params: EncoderYokeParams) -> str:
    """The Gate 1 read-back, templated from *validated* params.

    Leads with the two numbers the part exists to hold — centre distance and air gap
    — and then says the one thing that cannot be seen on the model: that the gap is
    only correct for a pinion of the stated length.
    """
    return (
        f"Encoder yoke for a {_fmt(params.tube_diameter)}mm shaft. Collar bore "
        f"{_fmt(params.tube_diameter + params.tube_clearance)}mm running on the "
        f"shaft, holding the pinion axle {_fmt(params.centre_distance)}mm off the "
        f"shaft axis — the same centre distance as the gear pair. "
        f"Axle boss {_fmt(params.boss_diameter)}mm, tapped "
        f"{_fmt(params.axle_pilot)}mm, its shoulder {_fmt(shoulder_face(params))}mm "
        f"above the base. Board face {_fmt(board_face(params))}mm above the base, "
        f"with {_fmt(params.hole_pilot)}mm pilots on a "
        f"{_fmt(params.hole_spacing_short)} x {_fmt(params.hole_spacing_long)}mm "
        f"pattern. That height is the sum of a {_fmt(params.pinion_length)}mm "
        f"pinion, a {_fmt(params.air_gap)}mm air gap and a "
        f"{_fmt(params.chip_height)}mm chip package — a pinion of a different "
        f"length changes the gap and nothing will say so. "
        f"Mount the board component side DOWN, facing the pinion."
    )


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #

_ON_BED = (Align.CENTER, Align.CENTER, Align.MIN)


def _plate(params: EncoderYokeParams, near: float, far: float) -> Part:
    """The base plate, spanning the collar at one end and the far wall at the other."""
    back = params.collar_diameter / 2 + 2.0
    length = far + params.wall + back
    width = max(params.hole_spacing_long + params.wall + 3.0, params.collar_diameter)

    plate = Box(length, width, params.plate_thickness, align=_ON_BED)
    return plate.locate(Location(((far + params.wall - back) / 2, 0, 0)))


def _collar(params: EncoderYokeParams) -> Part:
    """The bearing collar. Stands on the plate and is bored through it."""
    return Cylinder(
        radius=params.collar_diameter / 2,
        height=params.plate_thickness + params.collar_length,
        align=_ON_BED,
    )


def _axle_boss(params: EncoderYokeParams) -> Part:
    """The boss whose top face the pinion sits on."""
    return Cylinder(
        radius=params.boss_diameter / 2,
        height=shoulder_face(params),
        align=_ON_BED,
    ).locate(Location((params.centre_distance, 0, 0)))


def _columns(params: EncoderYokeParams, near: float, far: float) -> Part:
    """Two near columns, a far wall, and the beams that bridge between them.

    The beams are bridges, not cantilevers: both ends land on a column, so the whole
    part prints off the bed with nothing hanging in air.
    """
    top = board_face(params)
    half_span = params.hole_spacing_long / 2
    beam_depth = 3.0
    solid = None

    for sign in (1, -1):
        column = Box(params.wall, params.wall, top, align=_ON_BED).locate(
            Location((near + params.wall / 2, sign * half_span, 0))
        )
        beam = Box(far - near, params.wall, beam_depth, align=_ON_BED).locate(
            Location(((near + far) / 2, sign * half_span, top - beam_depth))
        )
        solid = column if solid is None else solid + column
        solid = solid + beam

    wall = Box(
        params.wall, params.hole_spacing_long + params.wall, top, align=_ON_BED
    ).locate(Location((far + params.wall / 2, 0, 0)))
    return solid + wall


def _board_pilots(params: EncoderYokeParams) -> Part:
    """Pilot holes for the board screws, drilled down from the board face."""
    top = board_face(params)
    depth = 6.0
    cutter = None
    for dx in (-1, 1):
        for dy in (-1, 1):
            hole = Cylinder(
                radius=params.hole_pilot / 2, height=depth + _OVERCUT, align=_ON_BED
            ).locate(
                Location(
                    (
                        params.centre_distance + dx * params.hole_spacing_short / 2,
                        dy * params.hole_spacing_long / 2,
                        top - depth,
                    )
                )
            )
            cutter = hole if cutter is None else cutter + hole
    return cutter


def _tether(params: EncoderYokeParams) -> Part:
    """A slot through the base plate for a tie back to the handle.

    Anti-rotation only. It carries no location, which is the point: the collar
    already holds the centre distance, so this end can be as rough as it likes.
    """
    back = params.collar_diameter / 2 + 2.0
    return Box(
        params.tether_slot,
        params.tether_slot * 2.5,
        params.plate_thickness + 2 * _OVERCUT,
        align=_ON_BED,
    ).locate(Location((-back + params.tether_slot / 2 + 1.0, 0, -_OVERCUT)))


def encoder_yoke(params: EncoderYokeParams) -> tuple[Part]:
    """Build the yoke. One solid, flat on the bed, everything printing upward.

    Returns a one-tuple so the registry's ``part_names`` contract holds.
    """
    near, far = _column_x(params)

    body = _plate(params, near, far)
    body = body + _collar(params)
    body = body + _axle_boss(params)
    body = body + _columns(params, near, far)

    bore = params.tube_diameter + params.tube_clearance
    body = body - Cylinder(
        radius=bore / 2,
        height=params.plate_thickness + params.collar_length + 2 * _OVERCUT,
        align=_ON_BED,
    ).locate(Location((0, 0, -_OVERCUT)))

    body = body - Cylinder(
        radius=params.axle_pilot / 2,
        height=shoulder_face(params) + _OVERCUT,
        align=_ON_BED,
    ).locate(Location((params.centre_distance, 0, -_OVERCUT)))

    body = body - _board_pilots(params)
    if params.tether_slot > 0:
        body = body - _tether(params)
    return (body,)


def build(params: EncoderYokeParams) -> tuple[Part]:
    """Registry entry point: build from a validated params model."""
    return encoder_yoke(params)
