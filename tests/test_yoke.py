"""Geometry tests for encoder_yoke.

Per CLAUDE.md these assert on volume, bounding boxes and containment. The yoke adds
a kind of assertion the other templates do not need: **keep-out**. Most of this part
is defined by what it must not touch — a gear sweeping a disc, a pinion turning on
its axle — and none of that shows up in a volume or a bounding box.
"""

from __future__ import annotations

import pytest
from build123d import Align, Cylinder, Location
from pydantic import ValidationError

from evee.templates.yoke import (
    _MIN_RUNNING_CLEARANCE,
    EncoderYokeParams,
    TemplateError,
    board_face,
    encoder_yoke,
    inner_dims,
    resolved_spec_sentence,
    shoulder_face,
)

#: The instrument build: 5mm shaft, 30:12 gears at 16.8mm, Adafruit AS5600 board.
RIG = dict(
    tube_diameter=5.0,
    centre_distance=16.8,
    gear_tip_diameter=25.6,
    pinion_tip_diameter=11.2,
    pinion_length=8.0,
    board_length=25.4,
    board_width=17.78,
    hole_spacing_long=20.32,
    hole_spacing_short=12.7,
)

_ON_BED = (Align.CENTER, Align.CENTER, Align.MIN)


@pytest.fixture(scope="module")
def yoke():
    (part,) = encoder_yoke(EncoderYokeParams(**RIG))
    return part


def column(radius: float, base: float, height: float, at=(0.0, 0.0)):
    """A cylinder standing at *at*, for probing keep-out volumes."""
    return Cylinder(radius=radius, height=height, align=_ON_BED).locate(
        Location((at[0], at[1], base))
    )


# --------------------------------------------------------------------------- #
# Keep-out — what this part is actually for
# --------------------------------------------------------------------------- #


def test_nothing_enters_the_disc_the_gear_sweeps(yoke):
    """The load-bearing test.

    The gear turns with the shaft and its tip circle (12.8mm) reaches further from
    the shaft axis than the board's near mounting holes do (10.45mm). So the obvious
    place to stand a column is inside the gear. Nothing may sit within the tip
    circle plus clearance, anywhere between the axle shoulder and the board — that
    is the whole band the gear could be mounted in.
    """
    params = EncoderYokeParams(**RIG)
    keep_out = RIG["gear_tip_diameter"] / 2 + _MIN_RUNNING_CLEARANCE
    base = shoulder_face(params)
    height = board_face(params) - base

    assert (yoke & column(keep_out, base, height)).volume == pytest.approx(
        0.0, abs=1e-6
    )


def test_nothing_enters_the_volume_the_pinion_turns_in(yoke):
    """The pinion runs from the shoulder up to the board, on the pinion axis."""
    params = EncoderYokeParams(**RIG)
    keep_out = RIG["pinion_tip_diameter"] / 2 + _MIN_RUNNING_CLEARANCE
    base = shoulder_face(params)

    probe = column(keep_out, base, RIG["pinion_length"], at=(RIG["centre_distance"], 0))
    assert (yoke & probe).volume == pytest.approx(0.0, abs=1e-6)


def test_the_far_wall_is_only_just_outside_the_pinion(yoke):
    """A keep-out test passes trivially if the part is nowhere near the thing it is
    keeping out of. Something must sit just past the boundary, or the test above is
    proving only that the yoke is small."""
    params = EncoderYokeParams(**RIG)
    keep_out = RIG["pinion_tip_diameter"] / 2 + _MIN_RUNNING_CLEARANCE + 0.6
    base = shoulder_face(params)

    probe = column(keep_out, base, RIG["pinion_length"], at=(RIG["centre_distance"], 0))
    assert (yoke & probe).volume > 0.0


def test_the_columns_stand_as_close_to_the_gear_as_they_are_allowed(yoke):
    """Same argument for the gear side: grow the keep-out slightly and the near
    columns must appear inside it."""
    params = EncoderYokeParams(**RIG)
    generous = RIG["gear_tip_diameter"] / 2 + _MIN_RUNNING_CLEARANCE + 0.8
    base = shoulder_face(params)
    height = board_face(params) - base

    assert (yoke & column(generous, base, height)).volume > 0.0


# --------------------------------------------------------------------------- #
# The Z stack
# --------------------------------------------------------------------------- #


def test_the_board_face_is_built_up_from_the_chip_not_the_board():
    """The gap is specified to the top of the package. Leave the package out and the
    whole assembly sits 1.75mm low, which is the pinion touching the chip rather
    than a reading that is merely wrong."""
    params = EncoderYokeParams(**RIG)

    assert shoulder_face(params) == pytest.approx(3.0 + 6.0)
    assert board_face(params) == pytest.approx(9.0 + 8.0 + 1.0 + 1.75)

    # The term everybody forgets has to actually move the answer.
    flat = EncoderYokeParams(**{**RIG, "chip_height": 0.1})
    assert board_face(params) - board_face(flat) == pytest.approx(1.65)


def test_a_longer_pinion_lifts_the_board_by_the_same_amount():
    """The gap is only correct for the pinion it was sized against."""
    short = EncoderYokeParams(**RIG)
    long = EncoderYokeParams(**{**RIG, "pinion_length": 10.0})
    assert board_face(long) - board_face(short) == pytest.approx(2.0)


def test_the_part_prints_flat_with_nothing_hanging(yoke):
    """One solid, sitting on the bed. Two solids would mean a column that never
    reached the plate, which slices as an island printing on air."""
    box = yoke.bounding_box()
    assert box.min.Z == pytest.approx(0.0, abs=1e-9)
    assert len(yoke.solids()) == 1


def test_the_board_face_is_the_top_of_the_part(yoke):
    """Anything above it would hold the board off its own mounting face."""
    params = EncoderYokeParams(**RIG)
    assert yoke.bounding_box().max.Z == pytest.approx(board_face(params), abs=1e-6)


# --------------------------------------------------------------------------- #
# Holes
# --------------------------------------------------------------------------- #


def test_the_collar_is_bored_right_through(yoke):
    """The shaft passes through the whole part, not into a pocket."""
    params = EncoderYokeParams(**RIG)
    bore = RIG["tube_diameter"] + params.tube_clearance
    probe = column(bore / 2 - 1e-3, -1.0, params.plate_thickness + params.collar_length + 2)

    assert (yoke & probe).volume == pytest.approx(0.0, abs=1e-6)


def test_the_collar_bore_is_a_running_fit_not_a_press_fit():
    """The shaft turns inside it. A bore equal to the shaft is a brake."""
    params = EncoderYokeParams(**RIG)
    assert params.tube_clearance > 0
    (part,) = encoder_yoke(params)

    snug = column(RIG["tube_diameter"] / 2 + 0.05, -1.0, params.plate_thickness + 2)
    assert (part & snug).volume == pytest.approx(0.0, abs=1e-6)


def test_all_four_board_pilots_are_drilled(yoke):
    """Two of them sit in cantilevered beams and two in the far wall, so a missing
    hole is entirely plausible and invisible from above."""
    params = EncoderYokeParams(**RIG)
    top = board_face(params)

    for dx in (-1, 1):
        for dy in (-1, 1):
            at = (
                RIG["centre_distance"] + dx * RIG["hole_spacing_short"] / 2,
                dy * RIG["hole_spacing_long"] / 2,
            )
            probe = column(params.hole_pilot / 2 - 1e-3, top - 4.0, 3.9, at=at)
            assert (yoke & probe).volume == pytest.approx(0.0, abs=1e-6), (
                f"no pilot hole at {at}"
            )


def test_the_axle_hole_is_on_the_pinion_axis(yoke):
    """Concentric with the board's centre, because that is where the sensor die is.
    Off by more than a millimetre and the AS5600 reads a distorted angle."""
    params = EncoderYokeParams(**RIG)
    probe = column(
        params.axle_pilot / 2 - 1e-3, 0.0, shoulder_face(params),
        at=(RIG["centre_distance"], 0),
    )
    assert (yoke & probe).volume == pytest.approx(0.0, abs=1e-6)

    # ...and it is a blind tapped hole in a boss, not a hole in thin air: material
    # must surround it.
    ring = column(params.boss_diameter / 2, 0.0, shoulder_face(params),
                  at=(RIG["centre_distance"], 0))
    assert (yoke & ring).volume > 0.0


def test_the_tether_slot_goes_through_the_plate(yoke):
    """It takes a tie back to the handle. A blind slot takes nothing."""
    params = EncoderYokeParams(**RIG)
    back = params.collar_diameter / 2 + 2.0
    at = (-back + params.tether_slot / 2 + 1.0, 0.0)
    probe = column(params.tether_slot / 2 - 0.2, -0.5, params.plate_thickness + 1, at=at)
    assert (yoke & probe).volume == pytest.approx(0.0, abs=1e-6)


def test_the_tether_slot_can_be_left_out():
    solid, = encoder_yoke(EncoderYokeParams(**{**RIG, "tether_slot": 0.0}))
    slotted, = encoder_yoke(EncoderYokeParams(**RIG))
    assert solid.volume > slotted.volume


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "overrides, expected",
    [
        pytest.param(
            # Not a small centre distance: on a 5mm shaft the axle boss hits the
            # gear long before the board reaches the tube. It takes a wide board.
            {"board_width": 30.0},
            "foul the turning shaft",
            id="board_hits_the_shaft",
        ),
        pytest.param(
            {"gear_tip_diameter": 33.0},
            "they would collide",
            id="gear_swallows_the_axle_boss",
        ),
        pytest.param(
            {"collar_diameter": 6.0},
            "of wall over a",
            id="collar_wall_too_thin",
        ),
        pytest.param(
            {"boss_diameter": 4.0},
            "of wall around a",
            id="boss_wall_too_thin",
        ),
        pytest.param(
            {"air_gap": 3.0},
            "outside the 0.5 to 1.5mm",
            id="air_gap_out_of_spec",
        ),
    ],
)
def test_impossible_yokes_are_rejected_with_a_message_naming_the_problem(
    overrides, expected
):
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        EncoderYokeParams(**{**RIG, **overrides})
    assert expected in str(excinfo.value)


def test_the_board_clash_refusal_says_what_would_fix_it():
    """A refusal that only says no makes the next guess a guess. This one has to
    name the centre distance that works, because the fix is more gear teeth."""
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        EncoderYokeParams(**{**RIG, "board_width": 30.0})

    message = str(excinfo.value)
    assert "raise centre_distance to at least" in message
    # 2.5 shaft surface + 15 half board + 0.4 clearance
    assert "17.90" in message


def test_the_axle_boss_is_what_actually_limits_the_centre_distance():
    """On a 5mm shaft the board has room to spare; it is the boss against the gear's
    teeth that sets the floor. Worth pinning, because the constraint moved when the
    shaft went from 10mm to 5mm and the old reasoning still reads plausibly."""
    # 16.8 clears by a tenth: boss inner edge 13.3, gear tip 12.8, 0.4 required.
    EncoderYokeParams(**{**RIG, "centre_distance": 16.8})

    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        EncoderYokeParams(**{**RIG, "centre_distance": 16.5})
    assert "they would collide" in str(excinfo.value)


def test_the_params_model_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        EncoderYokeParams(**RIG, mounting_style="clamp")


def test_a_yoke_has_no_interior():
    assert inner_dims(EncoderYokeParams(**RIG)) is None


def test_the_read_back_names_what_cannot_be_seen_on_the_model():
    sentence = resolved_spec_sentence(EncoderYokeParams(**RIG))

    assert "16.8mm off the shaft axis" in sentence
    assert "Board face 19.75mm above the base" in sentence
    # The two things a person can get wrong while holding a correct part.
    assert "a pinion of a different length changes the gap" in sentence
    assert "component side DOWN" in sentence
