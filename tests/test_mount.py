"""Geometry tests for shaft_sensor_mount.

Volume, bounding box and containment, per CLAUDE.md. The mount's own risk is that
almost everything about it is a plate with holes, so the few things that can be
wrong — the bore, whether the board clears the clamp, whether a hole actually goes
through — are exactly the ones a picture does not show.
"""

from __future__ import annotations

import math

import pytest
from build123d import Align, Cylinder, Location
from pydantic import ValidationError

from evee.templates.errors import TemplateError
from evee.templates.gear import ClampHubSpec
from evee.templates.mount import (
    ShaftSensorMountParams,
    board_offset,
    inner_dims,
    resolved_spec_sentence,
    shaft_sensor_mount,
)

#: An Adafruit BNO08x on a 7mm shaft: 25.4 x 22.86, holes 20.32 x 17.78.
RIG = dict(
    bore=7.0,
    hub={"style": "grub", "diameter": 14.0, "length": 10.0},
    board_length=25.4,
    board_width=22.86,
    hole_spacing_tangential=20.32,
    hole_spacing_radial=17.78,
)

_ON_BED = (Align.CENTER, Align.CENTER, Align.MIN)


@pytest.fixture(scope="module")
def mount():
    (part,) = shaft_sensor_mount(ShaftSensorMountParams(**RIG))
    return part


def probe(radius: float, base: float, height: float, at=(0.0, 0.0)):
    return Cylinder(radius=radius, height=height, align=_ON_BED).locate(
        Location((at[0], at[1], base))
    )


def test_it_is_one_solid_flat_on_the_bed(mount):
    """Two solids would mean the board pad never reached the hub — an island that
    slices happily and prints as two loose pieces."""
    assert len(mount.solids()) == 1
    assert mount.bounding_box().min.Z == pytest.approx(0.0, abs=1e-6)


def test_the_bore_goes_through_platform_and_hub(mount):
    """It has to pass the shaft, which does not stop at the platform."""
    params = ShaftSensorMountParams(**RIG)
    height = params.platform_thickness + params.hub.length
    assert (mount & probe(RIG["bore"] / 2 - 1e-3, -0.5, height + 1)).volume == (
        pytest.approx(0.0, abs=1e-6)
    )


def test_the_grub_screws_reach_the_bore(mount):
    """A hole that stops in the wall grips nothing and looks identical outside."""
    params = ShaftSensorMountParams(**RIG)
    at_bore = RIG["bore"] / 2 + 0.4
    mid = params.platform_thickness + params.hub.length / 2

    for index in range(params.hub.grub_count):
        angle = math.radians(90.0 + index * params.hub.grub_spacing)
        point = probe(
            0.5, mid - 0.5, 1.0,
            at=(at_bore * math.cos(angle), at_bore * math.sin(angle)),
        )
        assert (mount & point).volume == pytest.approx(0.0, abs=1e-6)


def test_all_four_board_holes_go_right_through(mount):
    """They take a screw and a nut underneath, so a blind hole is useless — and
    from above a blind hole and a through hole are the same picture."""
    params = ShaftSensorMountParams(**RIG)
    offset = board_offset(params)
    height = params.platform_thickness + params.standoff_height

    for radial in (-1, 1):
        for tangential in (-1, 1):
            at = (
                offset + radial * RIG["hole_spacing_radial"] / 2,
                tangential * RIG["hole_spacing_tangential"] / 2,
            )
            column = probe(params.hole_diameter / 2 - 1e-3, -0.5, height + 1, at=at)
            assert (mount & column).volume == pytest.approx(0.0, abs=1e-6), (
                f"hole at {at} does not go through"
            )


def test_the_board_sits_clear_of_the_clamp():
    """The shaft runs on through the hub in both directions, so there is nowhere
    over the axis for a board. The offset has to put its near edge past the hub."""
    params = ShaftSensorMountParams(**RIG)
    near_edge = board_offset(params) - params.board_width / 2

    assert near_edge > params.hub.diameter / 2
    # Derived, not typed: a wider hub pushes the board out by the same amount.
    wide = ShaftSensorMountParams(
        **{**RIG, "hub": {**RIG["hub"], "diameter": 18.0}}
    )
    assert board_offset(wide) - board_offset(params) == pytest.approx(2.0)


def test_the_standoffs_lift_the_board_off_the_platform(mount):
    """A board resting on its own solder joints rocks, and an IMU reports rocking
    as motion."""
    params = ShaftSensorMountParams(**RIG)
    top = params.platform_thickness + params.standoff_height
    assert mount.bounding_box().max.Z == pytest.approx(
        params.platform_thickness + params.hub.length, abs=1e-6
    )

    offset = board_offset(params)
    at = (offset + RIG["hole_spacing_radial"] / 2, RIG["hole_spacing_tangential"] / 2)
    # Material right below the board's corner, up to the standoff's top face...
    assert (mount & probe(params.standoff_diameter / 2, top - 0.5, 0.4, at=at)).volume > 0
    # ...and nothing above it, or the board could not sit down.
    assert (mount & probe(params.standoff_diameter / 2, top + 0.1, 1.0, at=at)).volume == (
        pytest.approx(0.0, abs=1e-6)
    )


def test_a_flat_mount_can_skip_the_standoffs():
    solid, = shaft_sensor_mount(ShaftSensorMountParams(**{**RIG, "standoff_height": 0.0}))
    lifted, = shaft_sensor_mount(ShaftSensorMountParams(**RIG))
    assert solid.volume < lifted.volume


@pytest.mark.parametrize(
    "overrides, expected",
    [
        pytest.param(
            {"hub": {"style": "pinch", "diameter": 14.0, "length": 10.0}},
            "reaches far past the hub",
            id="pinch_ear_would_hit_the_board",
        ),
        pytest.param(
            {"hole_spacing_radial": 30.0},
            "would sit off the edge",
            id="hole_pattern_wider_than_the_board",
        ),
        pytest.param(
            {"standoff_diameter": 3.0},
            "of material around a",
            id="standoff_too_small_for_its_hole",
        ),
        pytest.param(
            {"margin": 0.0, "standoff_diameter": 8.0},
            "overhang the platform",
            id="standoffs_hang_off_the_edge",
        ),
        pytest.param(
            {"bore": 20.0},
            "not bigger than the",
            id="bore_wider_than_the_hub",
        ),
    ],
)
def test_impossible_mounts_are_rejected_with_a_message_naming_the_problem(
    overrides, expected
):
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        ShaftSensorMountParams(**{**RIG, **overrides})
    assert expected in str(excinfo.value)


def test_the_hole_pattern_refusal_hints_at_the_likely_mistake():
    """Swapping the two board axes is the easy error and produces a pattern wider
    than the board, so the message says to check which way round they go."""
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        ShaftSensorMountParams(**{**RIG, "hole_spacing_radial": 30.0})
    assert "which board dimension runs which way" in str(excinfo.value)


def test_the_read_back_warns_that_the_cable_winds_up():
    """The consequence of putting the sensor on the rotating part, and the one thing
    about this design that is not visible in it."""
    sentence = resolved_spec_sentence(ShaftSensorMountParams(**RIG))
    assert "winds the lead up" in sentence
    assert "19.93mm out from the shaft axis" in sentence


def test_a_tapered_hub_reads_back_both_ends_of_the_cone():
    """A taper spends no depth — the hole is threaded over the whole wall either
    way — so the wall figure is unchanged and what needs saying is the two
    diameters it runs between. Quoting only the tapping size would describe the
    narrow end of a hole that is 0.9mm wider where you actually meet it."""
    tapered = ShaftSensorMountParams(
        **{**RIG, "hub": {**RIG["hub"], "grub_taper": 0.9}}
    )
    sentence = resolved_spec_sentence(tapered)
    assert "tapered 3.4mm at the hub face down to 2.5mm" in sentence
    assert "through 3.5mm of wall" in sentence

    # A parallel hole says "tapped", and names one diameter because it has one.
    plain = resolved_spec_sentence(ShaftSensorMountParams(**RIG))
    assert "tapped 2.5mm through 3.5mm of wall" in plain
    assert "tapered" not in plain


def test_the_params_model_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ShaftSensorMountParams(**RIG, cable_channel=True)


def test_a_mount_has_no_interior():
    assert inner_dims(ShaftSensorMountParams(**RIG)) is None
