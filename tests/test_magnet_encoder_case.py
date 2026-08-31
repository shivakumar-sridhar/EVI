"""Geometry tests for magnet_encoder_case.

Volume, bounding box and containment, per CLAUDE.md. What is different about this
part is that almost everything that can go wrong with it is a *height*, and heights
do not show up in a bounding box once there is a post of the right length somewhere
in the part. So the tests measure planes directly.

The two that matter: the board face has to be the derived sum and not a number, and
the magnet window has to follow the sensor rather than the board's centre. Both fail
silently in the assembled part — it goes together, it reads, the angle is wrong.
"""

from __future__ import annotations

import pytest
from build123d import Align, Cylinder, Location
from pydantic import ValidationError

from evee.templates.errors import TemplateError
from evee.templates.magnet_encoder_case import (
    _MAGNET_CLEARANCE,
    MagnetEncoderCaseParams,
    board_face,
    case_footprint,
    inner_dims,
    magnet_encoder_case,
    resolved_spec_sentence,
)

#: An Adafruit-shaped breakout over a 6mm magnet on a hinge. Thin base and no rim,
#: because a 3mm connector and a small air gap leave very little room — which is the
#: central tension of this part and is why the fixture looks like this.
RIG = dict(
    board_length=25.4,
    board_width=17.78,
    hole_spacing_length=20.32,
    hole_spacing_width=12.7,
    underside_height=3.0,
    magnet_diameter=6.0,
    magnet_proud=1.0,
    air_gap=2.5,
    base_thickness=1.2,
    rim_height=0.0,
)

_ON_BED = (Align.CENTER, Align.CENTER, Align.MIN)


@pytest.fixture(scope="module")
def case():
    (part,) = magnet_encoder_case(MagnetEncoderCaseParams(**RIG))
    return part


def probe(radius: float, base: float, height: float, at=(0.0, 0.0)):
    return Cylinder(radius=radius, height=height, align=_ON_BED).locate(
        Location((at[0], at[1], base))
    )


# --------------------------------------------------------------------------- #
# The stack — what this part is for
# --------------------------------------------------------------------------- #


def test_the_board_face_is_the_derived_sum_and_not_a_parameter():
    """Three measurements go into where the board sits. Folding them into one typed
    post height is the failure this template exists to prevent: it assembles, it
    reads, and the angle is merely wrong."""
    params = MagnetEncoderCaseParams(**RIG)
    assert board_face(params) == pytest.approx(1.0 + 2.5 + 1.75)

    # And each term moves it by exactly its own amount.
    for field, delta in (("magnet_proud", 0.5), ("air_gap", 0.5), ("package_height", 0.5)):
        moved = MagnetEncoderCaseParams(**{**RIG, field: RIG.get(field, 1.75) + delta})
        assert board_face(moved) - board_face(params) == pytest.approx(delta)


def test_the_posts_actually_end_at_the_board_face(case):
    """The derivation is only worth anything if the printed post agrees with it."""
    params = MagnetEncoderCaseParams(**RIG)
    face = board_face(params)
    at = (RIG["hole_spacing_length"] / 2, RIG["hole_spacing_width"] / 2)

    assert case.bounding_box().max.Z == pytest.approx(face, abs=1e-6)
    # Material just below the top of the post...
    assert (case & probe(params.post_diameter / 2 - 0.3, face - 0.3, 0.2, at=at)).volume > 0
    # ...and nothing above it, or the board could not sit down on it.
    assert (case & probe(params.post_diameter / 2, face + 0.05, 1.0, at=at)).volume == (
        pytest.approx(0.0, abs=1e-9)
    )


def test_it_is_one_solid_flat_on_the_bed(case):
    """Posts stand on the base plate, not on the handle. Standing them on the surface
    being glued to would make four loose columns held by the glue bead alone — at
    exactly the height the whole part is about."""
    assert len(case.solids()) == 1
    assert case.bounding_box().min.Z == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# The window
# --------------------------------------------------------------------------- #


def test_the_magnet_window_goes_right_through_the_plate(case):
    """A blind recess looks identical from above and holds the case up off the
    handle by however much plate is left under it."""
    params = MagnetEncoderCaseParams(**RIG)
    radius = params.magnet_diameter / 2 + _MAGNET_CLEARANCE
    column = probe(radius - 1e-3, -0.5, params.base_thickness + 1.0)
    assert (case & column).volume == pytest.approx(0.0, abs=1e-6)


def test_the_window_follows_the_sensor_and_not_the_board_centre():
    """The sensor is not necessarily in the middle of its board, and the window has
    to be under the sensor. Centring it on the board instead still prints, still
    assembles, and reads an angle off a magnet it is not over."""
    # A small magnet well off centre, so "under the sensor" and "under the board's
    # middle" are two clearly separate places rather than overlapping circles.
    offset = 6.0
    over = {**RIG, "chip_offset_length": offset, "magnet_diameter": 3.0}
    params = MagnetEncoderCaseParams(**over)
    (part,) = magnet_encoder_case(params)
    radius = params.magnet_diameter / 2 + _MAGNET_CLEARANCE

    # Open where the sensor is...
    through = probe(radius - 1e-3, -0.5, params.base_thickness + 1.0, at=(offset, 0.0))
    assert (part & through).volume == pytest.approx(0.0, abs=1e-6)
    # ...and solid plate back at the board's centre, which is where it would have
    # been if the offset had been ignored.
    at_centre = probe(0.4, 0.1, params.base_thickness - 0.2, at=(0.0, 0.0))
    assert (part & at_centre).volume == pytest.approx(at_centre.volume, rel=1e-3)


# --------------------------------------------------------------------------- #
# Screws
# --------------------------------------------------------------------------- #


def test_pilot_holes_do_not_break_out_of_the_glue_face(case):
    """A screw through the base would stand the case off the handle on its own point,
    which is a gap error in the one direction nothing else can correct."""
    params = MagnetEncoderCaseParams(**RIG)
    at = (RIG["hole_spacing_length"] / 2, RIG["hole_spacing_width"] / 2)
    under = probe(params.screw_diameter / 2, 0.0, params.base_thickness * 0.4, at=at)
    assert (case & under).volume == pytest.approx(under.volume, rel=1e-3)


def test_every_post_is_drilled(case):
    params = MagnetEncoderCaseParams(**RIG)
    face = board_face(params)
    for sx in (-1, 1):
        for sy in (-1, 1):
            at = (
                sx * RIG["hole_spacing_length"] / 2,
                sy * RIG["hole_spacing_width"] / 2,
            )
            bore = probe(params.screw_diameter / 2 - 1e-3, face - 1.0, 1.0, at=at)
            assert (case & bore).volume == pytest.approx(0.0, abs=1e-6), (
                f"post at {at} has no pilot hole"
            )


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


def test_connectors_that_will_not_fit_under_the_board_are_refused():
    """The reason this template exists, and the check that fired the first time it
    was run on plausible numbers. Inverting the board points the connectors at the
    magnet too, and they are far taller than the sensor."""
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        MagnetEncoderCaseParams(**{**RIG, "air_gap": 1.0, "base_thickness": 2.0})
    message = str(excinfo.value)
    assert "connectors would land on the base plate" in message
    # The message has to say what to do about it, in the caller's own units.
    assert "Raise air_gap to at least" in message


def test_the_refusal_names_the_base_and_rim_as_the_other_way_out():
    """Raising the air gap can take the sensor out of its useful range, so the
    message names the two dimensions that buy headroom without touching it."""
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        MagnetEncoderCaseParams(**{**RIG, "air_gap": 1.0, "rim_height": 1.2})
    assert "base_thickness" in str(excinfo.value)
    assert "rim_height" in str(excinfo.value)


def test_a_post_standing_over_the_window_is_refused():
    """It would have no plate under it. On screen it looks like a post."""
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        MagnetEncoderCaseParams(
            **{**RIG, "hole_spacing_length": 8.0, "hole_spacing_width": 6.0}
        )
    assert "would have no plate under it" in str(excinfo.value)


@pytest.mark.parametrize(
    "overrides, expected",
    [
        pytest.param(
            {"hole_spacing_width": 30.0},
            "which board dimension runs which way",
            id="hole_pattern_wider_than_the_board",
        ),
        pytest.param(
            {"chip_offset_length": 20.0},
            "puts the sensor off the",
            id="sensor_off_its_own_board",
        ),
        pytest.param(
            {"post_diameter": 3.0},
            "splits a post that thin",
            id="post_too_thin_to_tap",
        ),
        pytest.param(
            {"chip_offset_length": 11.0},
            "of base plate at the length edge",
            id="window_breaks_out_of_the_plate",
        ),
    ],
)
def test_impossible_cases_are_rejected_with_a_message_naming_the_problem(
    overrides, expected
):
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        MagnetEncoderCaseParams(**{**RIG, **overrides})
    assert expected in str(excinfo.value)


def test_the_params_model_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        MagnetEncoderCaseParams(**RIG, cable_channel=True)


# --------------------------------------------------------------------------- #
# Read-back
# --------------------------------------------------------------------------- #


def test_the_read_back_says_the_glue_line_is_in_the_stack():
    """The one term in the sum this part cannot control, and the one a person can
    still do something about — by using less adhesive."""
    sentence = resolved_spec_sentence(MagnetEncoderCaseParams(**RIG))
    assert "Glue thickness adds straight to the air gap" in sentence
    assert "2.70mm" in sentence


def test_the_read_back_shows_the_height_as_a_sum_not_a_number():
    """Quoting only '5.25mm posts' would hide which of the three measurements to go
    and re-check when the reading is off."""
    sentence = resolved_spec_sentence(MagnetEncoderCaseParams(**RIG))
    assert "5.25mm above the glue face" in sentence
    assert "1mm of magnet standing proud" in sentence
    assert "2.5mm air gap" in sentence
    assert "1.75mm of sensor package" in sentence


def test_inner_dims_report_the_room_under_the_board():
    params = MagnetEncoderCaseParams(**RIG)
    length, width, height = inner_dims(params)
    assert (length, width) == case_footprint(params)
    assert height == pytest.approx(board_face(params) - params.base_thickness)


# --------------------------------------------------------------------------- #
# Connector reliefs — the H
# --------------------------------------------------------------------------- #

RELIEVED = {
    **RIG,
    "magnet_proud": 2.0,
    "air_gap": 1.5,
    "base_thickness": 2.0,
    "rim_height": 1.2,
    "connector_relief_width": 6.4,
    "connector_relief_depth": 5.0,
}


@pytest.fixture(scope="module")
def relieved():
    (part,) = magnet_encoder_case(MagnetEncoderCaseParams(**RELIEVED))
    return part


def test_the_reliefs_go_clean_through_plate_and_rim(relieved):
    """Half a slot is no slot. Leaving the rim across the end would put a 1.2mm bar
    exactly where the connector needs to be, and it would not be visible from above."""
    params = MagnetEncoderCaseParams(**RELIEVED)
    length, _ = case_footprint(params)
    deck = params.rim_height + params.base_thickness

    for sign in (-1, 1):
        at = (sign * (length / 2 - 1.0), 0.0)
        column = probe(params.connector_relief_width / 2 - 0.2, -0.5, deck + 1.0, at=at)
        assert (relieved & column).volume == pytest.approx(0.0, abs=1e-6), (
            f"slot at the {'+' if sign > 0 else '-'} end is not open"
        )


def test_the_h_is_still_one_solid(relieved):
    """Two slots and a window in one plate is three chances to cut it in half."""
    assert len(relieved.solids()) == 1


def test_the_rails_beside_the_posts_survive(relieved):
    """The slot runs between the two posts at each end, and what is left beside it
    is what holds the post to the rest of the plate."""
    params = MagnetEncoderCaseParams(**RELIEVED)
    length, _ = case_footprint(params)
    # Midway between the slot edge and the post's inner edge.
    y = (params.connector_relief_width / 2 + params.hole_spacing_width / 2
         - params.post_diameter / 2) / 2
    at = (length / 2 - 1.0, y)
    strip = probe(0.2, params.rim_height + 0.2, params.base_thickness - 0.4, at=at)
    assert (relieved & strip).volume == pytest.approx(strip.volume, rel=1e-3)


def test_relieved_connectors_are_measured_to_the_handle_not_the_plate():
    """The entire point of cutting the slots. Unrelieved, a 3mm connector over a
    3.2mm deck needs a 5.75mm board face; relieved it hangs through and 5.25mm is
    plenty, which is 0.5mm of air gap bought back."""
    solid = {**RELIEVED, "connector_relief_width": 0.0, "connector_relief_depth": 0.0}
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        MagnetEncoderCaseParams(**solid)
    assert "land on the base plate" in str(excinfo.value)
    # The message offers the slots as the way out, not just a bigger gap.
    assert "cut connector reliefs" in str(excinfo.value)

    # Same numbers, slots cut: accepted.
    assert MagnetEncoderCaseParams(**RELIEVED)


def test_a_relief_that_undercuts_a_post_is_refused():
    """A post standing on the edge of a hole tears off the first time the screw is
    tightened, and on screen it is a post."""
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        MagnetEncoderCaseParams(**{**RELIEVED, "connector_relief_width": 9.0})
    assert "standing on the edge of a hole" in str(excinfo.value)


def test_a_relief_that_reaches_the_magnet_window_is_refused():
    """Slot plus window meeting in the middle is a plate in two pieces."""
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        MagnetEncoderCaseParams(**{**RELIEVED, "connector_relief_depth": 11.0})
    assert "the plate would come apart" in str(excinfo.value)


def test_the_read_back_explains_what_the_slots_buy():
    sentence = resolved_spec_sentence(MagnetEncoderCaseParams(**RELIEVED))
    assert "hang through toward the handle" in sentence
    assert "lets the air gap be this small" in sentence
