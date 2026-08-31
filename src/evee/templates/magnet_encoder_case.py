r"""magnet_encoder_case — an inverted AS5600 carrier, glued over a magnet.

The direct way to measure a hinge: glue a diametric magnet to the moving part, park
the encoder chip a fraction of a millimetre above it, and read the angle. No gear
train, no shaft to clamp, no centre distance — which is why this is a different part
from :mod:`evee.templates.yoke`, whose entire structure is a collar running on a
shaft. Here there is no shaft anywhere in the problem.

**The board is upside down, and that is the difficulty.** Everything on an Adafruit
breakout is on one face — sensor, connectors, passives — so inverting it points all
of them at the magnet, not just the chip. The chip is nowhere near the tallest of
them. Seat such a board on plain posts and the *connectors* decide how high it sits,
holding the sensor millimetres further from the magnet than anything intended, and
the part looks perfectly correct while doing it. ``underside_height`` is what that
check is made against, and it is a required parameter for that reason.

**The post height is derived and must stay derived.** From the glue face up::

    board face = magnet_proud + air_gap + package_height
                 |              |         |
                 |              |         `- chip stands proud of the inverted board
                 |              `- what the datasheet specifies: magnet face to chip
                 `- how far the magnet stands above the surface being glued to

Typing a post height instead folds three measurements into one number that nothing
can check, and the failure is silent: it assembles, it reads, and the angle is
merely wrong. :func:`board_face` owns that sum for the same reason
``yoke.board_face`` does.

**The glue line is in the stack and nothing here can see it.** A 0.2mm bead of epoxy
lifts the whole case 0.2mm and adds 0.2mm to the air gap. The read-back says so,
because it is the one term in the sum this part does not control.

**Open sides, on purpose.** Walls rising to the board would sit exactly where the
STEMMA cables leave the connectors, and would need port cutouts whose positions are
a fourth thing to measure. A carrier holds the board; it does not need to enclose it.

Print pose, section through the middle::

        screws
          |  |
       [==|==|==]        <- sensor board, component face DOWN
        |  ##  |         <- chip, hanging toward the magnet
       ||      ||        <- posts, rising to the derived board face
       ||      ||        <- connectors hang in the open air beside them
    ___||______||___     <- base plate
    |     ____     |
    |____|    |____|     <- rim, standing below the glue face
          |()|           <- magnet on the hinge, up through the window
    ==================   <- the handle
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
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evee.templates.errors import TemplateError

__all__ = [
    "PART_NAMES",
    "MagnetEncoderCaseParams",
    "board_face",
    "case_footprint",
    "inner_dims",
    "magnet_encoder_case",
    "resolved_spec_sentence",
]

PART_NAMES = ("case",)

#: Material left around a screw post's pilot hole. A self-tapping screw wedges the
#: post open as it cuts, so this is more than a clearance hole would need.
_POST_WALL = 1.1

#: Clearance between the magnet and the window it comes up through. The magnet is
#: glued onto a hinge by hand and will not be exactly where it was meant to be.
_MAGNET_CLEARANCE = 1.0

#: Air left under the board's tallest underside component. Small because it buys
#: nothing except not touching, and every millimetre here is one the air gap pays.
_UNDERSIDE_CLEARANCE = 0.5

#: Narrowest strip of base plate left anywhere — beside a connector slot, or
#: between a slot and the magnet window. Two perimeters at a 0.4mm nozzle.
_MIN_RAIL = 0.8

#: Cutters overshoot by this so no boolean face is coincident with an existing one.
_OVERCUT = 1.0

_ON_BED = (Align.CENTER, Align.CENTER, Align.MIN)


class MagnetEncoderCaseParams(BaseModel):
    """Validated parameters for :func:`magnet_encoder_case`.

    Board dimensions are inputs rather than a built-in table, the same decision the
    shaft mount records: a table of board sizes in here is a second copy of a number
    printed on the board, and it goes stale the first time the outline is revised.
    """

    model_config = ConfigDict(extra="forbid")

    # ---- the sensor board ------------------------------------------------- #
    board_length: float = Field(
        gt=0, description="Board's long dimension in mm, across the outline."
    )
    board_width: float = Field(gt=0, description="Board's short dimension in mm.")
    hole_spacing_length: float = Field(
        gt=0, description="Mounting hole spacing along the board's long axis, in mm."
    )
    hole_spacing_width: float = Field(
        gt=0, description="Mounting hole spacing along the board's short axis, in mm."
    )
    screw_diameter: float = Field(
        default=2.3,
        gt=0,
        description=(
            "Pilot hole in each post in mm — 2.3 takes a self-tapping M2.5. A tapping "
            "size, not a clearance: the screw cuts its own thread in the plastic."
        ),
    )
    package_height: float = Field(
        default=1.75,
        gt=0,
        description=(
            "How far the sensor package stands off the board, in mm. 1.75 for an "
            "AS5600 in SOIC-8. This is why the gap is not measured to the board: get "
            "it wrong and the whole stack sits off by exactly this much."
        ),
    )
    underside_height: float = Field(
        gt=0,
        description=(
            "The TALLEST thing on the board's component face, in mm — on an Adafruit "
            "breakout a STEMMA QT connector, not the sensor. Inverted, all of it "
            "points down. Required, not defaulted: it is the number that decides "
            "whether the board can sit as low as the air gap wants."
        ),
    )
    chip_offset_length: float = Field(
        default=0.0,
        description=(
            "Sensor centre along the board's long axis, in mm from the board's "
            "centre. Signed. This is what puts the chip over the magnet, and it is "
            "the one measurement that cannot be recovered once assembled."
        ),
    )
    chip_offset_width: float = Field(
        default=0.0,
        description="Sensor centre along the board's short axis, in mm from centre.",
    )

    # ---- what it sits over ------------------------------------------------ #
    magnet_diameter: float = Field(
        gt=0, description="Diameter of the magnet on the hinge, in mm."
    )
    magnet_proud: float = Field(
        ge=0,
        description=(
            "How far the magnet stands above the surface being glued to, in mm. "
            "0 if it is flush or sunk."
        ),
    )
    air_gap: float = Field(
        gt=0,
        description=(
            "Magnet face to the bottom of the sensor package, in mm. The AS5600 wants "
            "roughly 0.5 to 3; nearer the middle tolerates a hand-glued magnet better "
            "than either extreme. Not a knob to tune afterwards — it is set by a post "
            "height that gets printed."
        ),
    )

    # ---- the case itself -------------------------------------------------- #
    base_thickness: float = Field(
        default=2.0,
        gt=0,
        description=(
            "Base plate thickness in mm. It carries the posts and takes the glue, and "
            "it eats into the room the connectors have to hang in."
        ),
    )
    rim_height: float = Field(
        default=1.2,
        ge=0,
        description=(
            "How far the lip stands below the glue face, in mm. It gives adhesive a "
            "channel to sit in and bridges a handle that is not quite flat. 0 for a "
            "plain flat base."
        ),
    )
    rim_width: float = Field(
        default=1.6, gt=0, description="Thickness of that lip, in mm."
    )
    post_diameter: float = Field(
        default=4.5, gt=0, description="Diameter of each screw post, in mm."
    )
    connector_relief_width: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Width of a slot cut clean through the plate and rim at BOTH ends, in mm, "
            "measured across the board's short axis. It turns the plate into an H so "
            "the connectors hang through it toward the handle instead of landing on "
            "the plate. That is worth doing: without it the plate top is the floor "
            "the connectors must clear, and every millimetre of plate is a "
            "millimetre the air gap has to give back. 0 for a solid plate."
        ),
    )
    connector_relief_depth: float = Field(
        default=0.0,
        ge=0,
        description=(
            "How far each slot reaches in from the end of the plate, in mm. Enough to "
            "clear the connector body's footprint; it never needs to reach the magnet "
            "window."
        ),
    )
    margin: float = Field(
        default=1.5,
        ge=0,
        description="Base plate material outside the board's footprint, in mm.",
    )

    @model_validator(mode="after")
    def _check_geometry(self) -> "MagnetEncoderCaseParams":
        _validate(self)
        return self


def board_face(params: MagnetEncoderCaseParams) -> float:
    """Height of the board's component face above the glue face, in mm.

    The one sum in this part that matters, kept in one place so it cannot be got
    wrong twice. Everything else here is a bracket holding this plane.
    """
    return params.magnet_proud + params.air_gap + params.package_height


def case_footprint(params: MagnetEncoderCaseParams) -> tuple[float, float]:
    """Outer (length, width) of the base plate in mm."""
    return (
        params.board_length + 2 * params.margin,
        params.board_width + 2 * params.margin,
    )


def _deck(params: MagnetEncoderCaseParams) -> float:
    """Top of the base plate, above the glue face — where the posts start."""
    return params.rim_height + params.base_thickness


def inner_dims(params: MagnetEncoderCaseParams) -> tuple[float, float, float]:
    """(length, width, height) of the space the board's underside hangs in."""
    length, width = case_footprint(params)
    return (length, width, board_face(params) - _deck(params))


def _post_centres(params: MagnetEncoderCaseParams) -> list[tuple[float, float]]:
    """The four post centres, board centred on the origin."""
    return [
        (sx * params.hole_spacing_length / 2, sy * params.hole_spacing_width / 2)
        for sx in (-1, 1)
        for sy in (-1, 1)
    ]


def _window_radius(params: MagnetEncoderCaseParams) -> float:
    """Radius of the hole the magnet comes up through."""
    return params.magnet_diameter / 2 + _MAGNET_CLEARANCE


def _validate(params: MagnetEncoderCaseParams) -> None:
    """Cross-field checks. Raises :class:`TemplateError` naming the bad values."""
    for axis, spacing, extent in (
        ("length", params.hole_spacing_length, params.board_length),
        ("width", params.hole_spacing_width, params.board_width),
    ):
        if spacing >= extent:
            raise TemplateError(
                f"hole_spacing_{axis}={spacing}mm is not inside the {extent}mm board "
                f"it is measured across — the holes would sit off the edge (check "
                f"which board dimension runs which way)"
            )

    for axis, offset, extent in (
        ("length", params.chip_offset_length, params.board_length),
        ("width", params.chip_offset_width, params.board_width),
    ):
        if abs(offset) > extent / 2:
            raise TemplateError(
                f"chip_offset_{axis}={offset}mm puts the sensor off the {extent}mm "
                f"board it is measured on"
            )

    if params.screw_diameter + 2 * _POST_WALL > params.post_diameter:
        raise TemplateError(
            f"post_diameter={params.post_diameter}mm leaves under {_POST_WALL}mm of "
            f"material around a {params.screw_diameter}mm pilot hole, and a "
            f"self-tapping screw splits a post that thin (raise post_diameter to at "
            f"least {params.screw_diameter + 2 * _POST_WALL}mm)"
        )

    face = board_face(params)
    deck = _deck(params)

    relieved = params.connector_relief_width > 0 and params.connector_relief_depth > 0

    # The reason this template exists at all. Everything on the inverted board points
    # down, and the tallest of it is never the sensor.
    #
    # What the connectors have to clear depends on whether there is plate under them.
    # Relieved, they hang through the slot and the floor is the handle itself, which
    # is the whole point of cutting it: the plate stops spending air gap.
    headroom = face if relieved else face - deck
    if params.underside_height + _UNDERSIDE_CLEARANCE > headroom:
        floor = "handle" if relieved else "base plate"
        needed = (
            params.underside_height
            + _UNDERSIDE_CLEARANCE
            + (0.0 if relieved else deck)
            - params.magnet_proud
            - params.package_height
        )
        fix = (
            ""
            if relieved
            else (
                f", or cut connector reliefs so they hang through the plate instead "
                f"of landing on it, or take {params.base_thickness}mm of "
                f"base_thickness and {params.rim_height}mm of rim_height down"
            )
        )
        raise TemplateError(
            f"the board's underside carries something {params.underside_height}mm "
            f"tall and there is only {headroom:.2f}mm between the {floor} and the "
            f"board — the connectors would land on the {floor}, not the sensor on "
            f"the magnet. Raise air_gap to at least {needed:.2f}mm{fix}"
        )

    # A post over the magnet window has nothing to stand on, and a post that merely
    # overlaps its edge stands on a rim of plate a nozzle cannot lay properly.
    window = _window_radius(params)
    for cx, cy in _post_centres(params):
        gap = math.hypot(cx - params.chip_offset_length, cy - params.chip_offset_width)
        if gap - params.post_diameter / 2 < window:
            raise TemplateError(
                f"a {params.post_diameter}mm post at ({cx:.1f}, {cy:.1f}) reaches to "
                f"{gap - params.post_diameter / 2:.2f}mm from the sensor centre, "
                f"inside the {window:.2f}mm window the {params.magnet_diameter}mm "
                f"magnet comes up through — the post would have no plate under it "
                f"(shrink post_diameter, or move the sensor off the hole pattern)"
            )

    # The window has to be a hole in a plate, not a plate that is mostly hole.
    length, width = case_footprint(params)
    for axis, extent, offset in (
        ("length", length, params.chip_offset_length),
        ("width", width, params.chip_offset_width),
    ):
        edge = extent / 2 - abs(offset) - window
        if edge < params.margin:
            raise TemplateError(
                f"the {params.magnet_diameter}mm magnet window leaves {edge:.2f}mm of "
                f"base plate at the {axis} edge, under the {params.margin}mm margin "
                f"(raise margin, or move the sensor toward the board's centre)"
            )

    if relieved:
        # The slot must not undercut a post. Posts sit on the hole pattern, the slots
        # are centred across it, so the two only ever meet in the width direction —
        # which makes this one comparison rather than a rectangle-circle test.
        rail = (
            params.hole_spacing_width / 2
            - params.post_diameter / 2
            - params.connector_relief_width / 2
        )
        # Tolerance, or a value landing exactly on the minimum is refused by
        # float representation alone — 6.35 - 2.25 - 3.3 is not quite 0.8.
        if rail < _MIN_RAIL - 1e-9:
            raise TemplateError(
                f"connector_relief_width={params.connector_relief_width}mm leaves "
                f"{rail:.2f}mm of plate between the slot and a {params.post_diameter}mm "
                f"post, under the {_MIN_RAIL}mm minimum — the post would be standing "
                f"on the edge of a hole (narrow the slot, or widen "
                f"hole_spacing_width if the board allows it)"
            )
        # It also has to stop short of the magnet window, or the plate falls in two.
        inner = length / 2 - params.connector_relief_depth
        reach = abs(params.chip_offset_length) + _window_radius(params)
        if inner <= reach + _MIN_RAIL:
            raise TemplateError(
                f"connector_relief_depth={params.connector_relief_depth}mm brings the "
                f"slot to {inner:.2f}mm from the centre, into the magnet window that "
                f"reaches {reach:.2f}mm — the plate would come apart (shorten "
                f"connector_relief_depth)"
            )

    if 2 * params.rim_width >= min(length, width):
        raise TemplateError(
            f"rim_width={params.rim_width}mm closes off a {min(length, width):.1f}mm "
            f"case, leaving no channel for adhesive (lower rim_width)"
        )


def resolved_spec_sentence(params: MagnetEncoderCaseParams) -> str:
    """The Gate 1 read-back, templated from *validated* params."""
    face = board_face(params)
    length, width = case_footprint(params)
    chip = (
        "centred on the board"
        if not (params.chip_offset_length or params.chip_offset_width)
        else (
            f"{_fmt(params.chip_offset_length)} x {_fmt(params.chip_offset_width)}mm "
            f"off the board's centre"
        )
    )
    rim = (
        "a plain flat base"
        if params.rim_height <= 0
        else (
            f"a {_fmt(params.rim_width)}mm rim standing "
            f"{_fmt(params.rim_height)}mm below the glue face to hold adhesive"
        )
    )
    # Built as its own string rather than a ternary spliced into the return: an
    # inline conditional in the middle of an implicit-concatenation chain swallows
    # every fragment above it, and the sentence silently loses its first half.
    if params.connector_relief_width > 0 and params.connector_relief_depth > 0:
        clearance = (
            f"{_fmt(params.connector_relief_width)} x "
            f"{_fmt(params.connector_relief_depth)}mm slots are cut clean through "
            f"plate and rim at both ends, so the "
            f"{_fmt(params.underside_height)}mm connectors hang through toward the "
            f"handle with {face - params.underside_height:.2f}mm to spare instead of "
            f"landing on the plate — which is what lets the air gap be this small."
        )
    else:
        clearance = (
            f"There is {face - _deck(params):.2f}mm of clear air under the board for "
            f"{_fmt(params.underside_height)}mm connectors."
        )
    return (
        f"Inverted encoder case, {_fmt(length)} x {_fmt(width)}mm on the handle, "
        f"holding a {_fmt(params.board_length)} x {_fmt(params.board_width)}mm board "
        f"component-face down on four posts. The board face lands {face:.2f}mm above "
        f"the glue face, and that height is derived rather than chosen: "
        f"{_fmt(params.magnet_proud)}mm of magnet standing proud, plus the "
        f"{_fmt(params.air_gap)}mm air gap, plus {_fmt(params.package_height)}mm of "
        f"sensor package. The sensor sits {chip}, and that is what has to end up over "
        f"the magnet — the magnet comes up through a "
        f"{2 * _window_radius(params):.1f}mm window directly under it. "
        f"{_fmt(params.screw_diameter)}mm pilot holes on a "
        f"{_fmt(params.hole_spacing_length)} x {_fmt(params.hole_spacing_width)}mm "
        f"pattern for self-tapping screws, and {rim}. "
        f"{clearance} "
        f"Glue thickness adds straight to the air gap: a 0.2mm bead makes it "
        f"{params.air_gap + 0.2:.2f}mm, and nothing in the part can see that."
    )


def _fmt(value: float) -> str:
    return f"{value:g}"


def magnet_encoder_case(params: MagnetEncoderCaseParams) -> tuple[Part]:
    """Build the case. One solid, glue face down, board seating plane up."""
    length, width = case_footprint(params)
    face = board_face(params)
    deck = _deck(params)

    body = Box(length, width, params.base_thickness, align=_ON_BED).locate(
        Location((0, 0, params.rim_height))
    )

    if params.rim_height > 0:
        # Stands BELOW the base plate, so the part prints rim-down and the adhesive
        # sits in the channel it makes instead of being squeezed out flat.
        rim = Box(length, width, params.rim_height, align=_ON_BED) - Box(
            length - 2 * params.rim_width,
            width - 2 * params.rim_width,
            params.rim_height + 2 * _OVERCUT,
            align=_ON_BED,
        ).locate(Location((0, 0, -_OVERCUT)))
        body = body + rim

    # Posts stand on the plate, not on the handle: they have to be part of one solid,
    # and a column resting on the surface being glued to would be held by the glue
    # bead alone at exactly the height that matters.
    for cx, cy in _post_centres(params):
        post = Cylinder(
            radius=params.post_diameter / 2, height=face - deck, align=_ON_BED
        ).locate(Location((cx, cy, deck)))
        body = body + post

    # Every solid on, then every hole through — a pilot cut before its post is
    # unioned would be filled straight back in and the part would look right.
    window = Cylinder(
        radius=_window_radius(params),
        height=deck + 2 * _OVERCUT,
        align=_ON_BED,
    ).locate(Location((params.chip_offset_length, params.chip_offset_width, -_OVERCUT)))
    body = body - window

    if params.connector_relief_width > 0 and params.connector_relief_depth > 0:
        # Straight through plate AND rim, both ends: the connectors have to reach
        # past everything this part puts under them, which is the whole reason the
        # slots exist. Leaving the rim across the end would put a 1.2mm bar exactly
        # where the connector wants to be.
        for sign in (-1, 1):
            # Spans from the plate's end edge inward by connector_relief_depth,
            # plus an _OVERCUT poking out so the end face is cut on a real edge.
            slot = Box(
                params.connector_relief_depth + _OVERCUT,
                params.connector_relief_width,
                deck + 2 * _OVERCUT,
                align=_ON_BED,
            ).locate(
                Location(
                    (
                        sign
                        * (
                            length / 2
                            - params.connector_relief_depth / 2
                            + _OVERCUT / 2
                        ),
                        0,
                        -_OVERCUT,
                    )
                )
            )
            body = body - slot

    for cx, cy in _post_centres(params):
        # Stops inside the plate rather than breaking out of the glue face: a screw
        # poking through would hold the case off the handle by its own point.
        depth = face - deck - params.rim_height
        pilot = Cylinder(
            radius=params.screw_diameter / 2,
            height=depth + _OVERCUT,
            align=_ON_BED,
        ).locate(Location((cx, cy, face - depth)))
        body = body - pilot

    return (body,)


def build(params: MagnetEncoderCaseParams) -> tuple[Part]:
    """Registry entry point: build from a validated params model."""
    return magnet_encoder_case(params)
