"""Geometry tests for gear_pair.

Per CLAUDE.md these assert on volume, bounding boxes and containment, never on
exact meshes. A gear adds one more kind of assertion: *counting* — a tooth count
that comes out wrong is invisible in a volume, and it is the one number the whole
part is named for.

The load-bearing test here is the mesh check. Everything else can pass on a pair
that binds solidly when you put them together.
"""

from __future__ import annotations

import math

import pytest
from build123d import Align, Cylinder, Location
from pydantic import ValidationError

from evee.templates.gear import (
    _MIN_RIM,
    ClampHubSpec,
    _validate_grub_hub,
    CounterboreSpec,
    GearFitTrialParams,
    GearPairParams,
    TemplateError,
    centre_distance,
    face_widths,
    gear_fit_trial,
    gear_pair,
    hub_angle,
    inner_dims,
    mesh_rotation,
    pitch_radius,
    ratio,
    resolved_spec_sentence,
    root_radius,
    trial_spec_sentence,
    tip_radius,
    undercut_limit,
)

#: The AS5600 encoder pair: 20mm gear on a 10mm laparoscopic shaft, driving a
#: pinion bored 5.2mm for the magnet.
ENCODER = dict(
    module=0.8,
    gear_teeth=23,
    pinion_teeth=12,
    thickness=5.0,
    gear_bore=10.0,
    pinion_bore=5.2,
)

MODULE = ENCODER["module"]
THICKNESS = ENCODER["thickness"]

_ON_BED = (Align.CENTER, Align.CENTER, Align.MIN)


@pytest.fixture(scope="module")
def pair():
    """(gear, pinion) for the encoder pair. Module-scoped — OCC is not cheap."""
    return gear_pair(**ENCODER)


def probe(radius: float, height: float = 20.0):
    """A cylinder on the axis, tall enough to swallow either gear."""
    return Cylinder(radius=radius, height=height, align=_ON_BED).locate(
        Location((0, 0, -5))
    )


# --------------------------------------------------------------------------- #
# Tooth geometry
# --------------------------------------------------------------------------- #


def test_the_gear_is_the_requested_outside_diameter(pair):
    """20mm was the ask. Tip diameter is what a caliper reads, so it is the check.

    Not a bounding box: with an odd tooth count no tooth points along -X, so the
    box comes out a few hundredths under and would need a fudge factor to assert
    against. Containment has no such problem.
    """
    gear, _ = pair
    r_tip = tip_radius(MODULE, ENCODER["gear_teeth"])

    assert r_tip * 2 == pytest.approx(20.0)
    # Everything is inside the tip circle...
    assert (gear - probe(r_tip + 1e-6)).volume == pytest.approx(0.0, abs=1e-6)
    # ...and something reaches it.
    assert (gear - probe(r_tip - 0.02)).volume > 0.0


def test_both_gears_have_the_teeth_they_were_asked_for(pair):
    """Counted, not inferred. A thin annulus at the pitch circle cuts the solid into
    exactly one piece per tooth — the only place tooth count shows up as a number."""
    for part, teeth in zip(pair, (ENCODER["gear_teeth"], ENCODER["pinion_teeth"])):
        r_pitch = pitch_radius(MODULE, teeth)
        annulus = probe(r_pitch + 0.05) - probe(r_pitch - 0.05)
        assert len((part & annulus).solids()) == teeth


def test_the_rim_under_the_roots_is_solid(pair):
    """No tooth space may reach inside the root circle, or the gear leaks light and
    the roots have nothing behind them."""
    for part, teeth, bore in zip(
        pair,
        (ENCODER["gear_teeth"], ENCODER["pinion_teeth"]),
        (ENCODER["gear_bore"], ENCODER["pinion_bore"]),
    ):
        # A facet inside the nominal root circle: the root arcs are sampled as a
        # polyline, so the built boundary is chords a couple of microns inside the
        # true circle. Physically nothing; enough to fail an exact containment.
        rim = probe(root_radius(MODULE, teeth) - 0.01) - probe(bore / 2)
        rim = rim & Cylinder(radius=50, height=THICKNESS, align=_ON_BED)
        assert (rim - part).volume == pytest.approx(0.0, abs=1e-6)


def test_bores_go_right_through(pair):
    """A blind bore would look identical from above and jam on the shaft."""
    for part, bore in zip(pair, (ENCODER["gear_bore"], ENCODER["pinion_bore"])):
        assert (part & probe(bore / 2 - 1e-3)).volume == pytest.approx(0.0, abs=1e-6)


def test_both_parts_print_flat(pair):
    """Gears print teeth-up, flat on the plate. On edge they would need support and
    every tooth would be a bridge."""
    for part in pair:
        box = part.bounding_box()
        assert box.min.Z == pytest.approx(0.0, abs=1e-9)
        assert box.size.Z == pytest.approx(THICKNESS, abs=1e-9)


# --------------------------------------------------------------------------- #
# Meshing — the test the part exists for
# --------------------------------------------------------------------------- #


def test_the_pair_meshes_at_the_stated_centre_distance(pair):
    """Put the pinion where the read-back says and the teeth must not collide.

    Every other test in this file passes on a pair that binds solid. The tip
    circles overlap by 1.6mm here, so a zero intersection is real clearance
    between interleaved teeth rather than two gears sitting apart.
    """
    gear, pinion = pair
    params = GearPairParams(**ENCODER)
    spacing = centre_distance(params)

    overlap = tip_radius(MODULE, ENCODER["gear_teeth"]) + tip_radius(
        MODULE, ENCODER["pinion_teeth"]
    ) - spacing
    assert overlap > 1.0, "teeth are not even engaged; the test proves nothing"

    assert (gear & pinion.moved(Location((spacing, 0, 0)))).volume == pytest.approx(
        0.0, abs=1e-9
    )


@pytest.mark.parametrize("pinion_teeth", [11, 12, 13, 14])
def test_meshing_survives_either_tooth_count_parity(pinion_teeth):
    """Odd counts present a space across the line of centres unaided; even ones are
    half a pitch out and get rotated. Getting that backwards collides one whole
    tooth, so both parities are checked rather than the one the design uses."""
    gear, pinion = gear_pair(
        module=MODULE,
        gear_teeth=23,
        pinion_teeth=pinion_teeth,
        thickness=THICKNESS,
        gear_bore=10.0,
    )
    spacing = pitch_radius(MODULE, 23) + pitch_radius(MODULE, pinion_teeth)

    assert (gear & pinion.moved(Location((spacing, 0, 0)))).volume == pytest.approx(
        0.0, abs=1e-9
    )


def test_the_mesh_phase_is_baked_into_the_solid_not_its_location():
    """A phase living in the part's Location is undone by the next locate() call.

    cad.arrange_along_x moves every part before export, so a pinion phased by
    rotating the solid's location would reach the plate un-phased and nothing would
    fail. Stripping the location must change nothing.
    """
    _, pinion = gear_pair(module=MODULE, gear_teeth=23, pinion_teeth=12, thickness=THICKNESS)
    stripped = pinion.located(Location())

    assert stripped.volume == pytest.approx(pinion.volume, rel=1e-9)
    gear, _ = gear_pair(module=MODULE, gear_teeth=23, pinion_teeth=12, thickness=THICKNESS)
    spacing = pitch_radius(MODULE, 23) + pitch_radius(MODULE, 12)
    assert (gear & stripped.moved(Location((spacing, 0, 0)))).volume == pytest.approx(
        0.0, abs=1e-9
    )


def test_mesh_rotation_is_half_a_pitch_for_even_counts_and_nothing_for_odd():
    assert mesh_rotation(11) == 0.0
    assert mesh_rotation(12) == pytest.approx(15.0)
    assert mesh_rotation(23) == 0.0
    assert mesh_rotation(24) == pytest.approx(7.5)


def test_more_backlash_cuts_more_metal():
    """Backlash is taken off the tooth, so it is visible in the volume. If it were
    silently ignored the pair would bind and nothing else here would notice."""
    tight, _ = gear_pair(
        module=MODULE, gear_teeth=23, pinion_teeth=12, thickness=THICKNESS, backlash=0.0
    )
    loose, _ = gear_pair(
        module=MODULE, gear_teeth=23, pinion_teeth=12, thickness=THICKNESS, backlash=0.2
    )
    assert loose.volume < tight.volume


# --------------------------------------------------------------------------- #
# Uneven face widths
# --------------------------------------------------------------------------- #


def test_the_pinion_can_be_taller_than_the_gear():
    """A deeper bore or some axial slop to absorb. Only the pinion moves."""
    gear, pinion = gear_pair(**{**ENCODER, "thickness": 3.0, "pinion_thickness": 8.0})

    assert gear.bounding_box().size.Z == pytest.approx(3.0)
    assert pinion.bounding_box().size.Z == pytest.approx(8.0)
    # Both still start on the bed: a pinion floating 5mm up would slice as an
    # island on thin air and nothing else here would catch it.
    assert pinion.bounding_box().min.Z == pytest.approx(0.0, abs=1e-9)


def test_an_uneven_pair_still_meshes():
    """Face width is along the axis and the tooth profile does not depend on it, so
    this should hold — which is exactly why it is worth pinning."""
    gear, pinion = gear_pair(**{**ENCODER, "thickness": 3.0, "pinion_thickness": 8.0})
    spacing = pitch_radius(MODULE, 23) + pitch_radius(MODULE, 12)

    assert (gear & pinion.moved(Location((spacing, 0, 0)))).volume == pytest.approx(
        0.0, abs=1e-9
    )


def test_omitting_pinion_thickness_matches_the_gear():
    """The default cannot drift: an unequal pair by accident is a pair that only
    contacts over part of its face."""
    params = GearPairParams(**ENCODER)
    assert params.pinion_thickness is None
    assert face_widths(params) == (THICKNESS, THICKNESS)

    taller = GearPairParams(**{**ENCODER, "pinion_thickness": 8.0})
    assert face_widths(taller) == (THICKNESS, 8.0)


def test_uneven_faces_are_read_back_with_the_contact_width():
    """The number that matters is neither of the two you typed: contact happens over
    the narrower face only."""
    sentence = resolved_spec_sentence(
        GearPairParams(**{**ENCODER, "thickness": 3.0, "pinion_thickness": 8.0})
    )
    assert "gear 3mm thick and pinion 8mm" in sentence
    assert "meeting over 3mm of face" in sentence

    even = resolved_spec_sentence(GearPairParams(**ENCODER))
    assert "5mm thick" in even
    assert "meeting over" not in even


def test_a_zero_or_negative_pinion_thickness_is_refused():
    with pytest.raises(ValidationError):
        GearPairParams(**{**ENCODER, "pinion_thickness": 0.0})
    with pytest.raises(TemplateError) as excinfo:
        gear_pair(**{**ENCODER, "pinion_thickness": -2.0})
    assert "pinion_thickness must be positive" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Magnet counterbore
# --------------------------------------------------------------------------- #

#: The encoder build: 3.2mm axle bore right through, 5x3 magnet seat in one end.
SEATED = dict(
    module=0.8,
    gear_teeth=30,
    pinion_teeth=12,
    thickness=3.0,
    pinion_thickness=8.0,
    gear_bore=10.0,
    pinion_bore=3.2,
)


def band(height: float, base: float = 0.0):
    """A slab of the world, for looking at one axial slice of a part."""
    return Cylinder(radius=60, height=height, align=_ON_BED).locate(
        Location((0, 0, base))
    )


def test_a_counterbore_removes_its_own_annulus():
    """Only the ring outside the bore is new material gone — the middle was already
    drilled, and counting it twice would hide a counterbore that missed."""
    plain, plain_pinion = gear_pair(**SEATED)
    _, seated = gear_pair(
        **SEATED, pinion_counterbore=CounterboreSpec(diameter=5.0, depth=3.0)
    )

    annulus = math.pi * ((5.0 / 2) ** 2 - (3.2 / 2) ** 2) * 3.0
    assert plain_pinion.volume - seated.volume == pytest.approx(annulus, rel=1e-4)


def test_the_counterbore_opens_on_the_plus_z_face_only():
    """Which end it opens on decides whether its floor prints on solid material or
    bridges over the bore. Both look the same from above."""
    _, seated = gear_pair(
        **SEATED, pinion_counterbore=CounterboreSpec(diameter=5.0, depth=3.0)
    )
    seat = probe(5.0 / 2 - 1e-3)

    # Clear through the top 3mm...
    assert (seated & seat & band(3.0, 5.0)).volume == pytest.approx(0.0, abs=1e-6)
    # ...and solid where the 5mm seat does not reach, save the 3.2mm bore itself.
    lower = seated & seat & band(4.9, 0.0)
    assert lower.volume > 0.0


def test_the_counterbore_leaves_a_step_to_seat_against():
    """A magnet needs a floor. A counterbore as deep as the face is a through bore
    with a wider top, and the magnet falls out the back."""
    _, seated = gear_pair(
        **SEATED, pinion_counterbore=CounterboreSpec(diameter=5.0, depth=3.0)
    )
    step = (probe(5.0 / 2) - probe(3.2 / 2)) & band(0.1, 4.9)
    assert (step - seated).volume == pytest.approx(0.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# Clamp hub
# --------------------------------------------------------------------------- #

HUB = dict(
    diameter=16.0, length=8.0, ear_depth=9.0, nut_across_flats=5.5, nut_depth=2.7
)


@pytest.fixture(scope="module")
def clamped():
    """The shaft gear with its clamp hub. Module-scoped — OCC is not cheap."""
    gear, _ = gear_pair(**SEATED, gear_hub=ClampHubSpec(**HUB))
    return gear


def test_the_hub_keeps_clear_of_the_band_the_pinion_sweeps(clamped):
    """The one that matters.

    The ear reaches to r=17, well past the 12.8mm tip circle, and the pinion sweeps
    inward to 11.2mm from the gear axis. They miss each other only because the ear
    sits further along the shaft. Let any of it sink into the toothed disc — a
    1mm skirt is enough — and it fouls the pinion once per revolution while looking
    on screen like a slightly chunky hub.
    """
    mesh_band = band(SEATED["thickness"])
    outside_tips = mesh_band - probe(tip_radius(MODULE, SEATED["gear_teeth"]) + 1e-6)

    assert (clamped & outside_tips).volume == pytest.approx(0.0, abs=1e-6)


def test_the_slit_lands_in_a_tooth_gap_not_through_a_tooth(clamped):
    """A slit through a tooth leaves two half teeth that click through every mesh.

    Counting islands at the pitch circle catches it: a split tooth is one island
    more than the gear has teeth.
    """
    r_pitch = pitch_radius(MODULE, SEATED["gear_teeth"])
    annulus = (probe(r_pitch + 0.05) - probe(r_pitch - 0.05)) & band(
        SEATED["thickness"]
    )
    assert len((clamped & annulus).solids()) == SEATED["gear_teeth"]


def test_the_slit_cuts_all_the_way_from_bore_to_outside(clamped):
    """A slit that stops short clamps nothing and is invisible on the part.

    Measured as a wedge of empty space: from the bore wall out past the hub, at the
    slit angle, no material may remain anywhere up the hub's height.
    """
    angle = math.radians(hub_angle(SEATED["gear_teeth"]))
    reach = HUB["diameter"] / 2
    for radius in (5.2, 6.0, 7.0, reach - 0.2):
        point = Cylinder(radius=0.3, height=HUB["length"], align=_ON_BED).locate(
            Location(
                (
                    radius * math.cos(angle),
                    radius * math.sin(angle),
                    SEATED["thickness"] + 0.5,
                )
            )
        )
        assert (clamped & point).volume == pytest.approx(0.0, abs=1e-6), (
            f"slit is welded shut at r={radius}"
        )


def test_the_hub_is_one_solid_with_the_gear(clamped):
    """The ear reaches the gear through the hub, not through a coplanar face. If the
    union failed it would come back as two solids and slice as two objects."""
    assert len(clamped.solids()) == 1


def test_the_nut_pocket_opens_at_the_top(clamped):
    """A closed pocket needs a ceiling bridged in mid-air, and a drooped nut trap
    does not take a nut. Open to the top face, there is nothing to bridge."""
    # The channel is at the far end of the ear, where the nut goes in — not at its
    # middle, which is solid and where a lazy probe would pass by hitting material.
    spec = ClampHubSpec(**HUB)
    turn = math.radians(hub_angle(SEATED["gear_teeth"]) - 90.0)
    local_x = -spec.ear_width / 2 + spec.nut_depth / 2
    local_y = spec.diameter / 2 + spec.ear_depth / 2
    x = local_x * math.cos(turn) - local_y * math.sin(turn)
    y = local_x * math.sin(turn) + local_y * math.cos(turn)

    # A column from the screw axis straight up through the top face.
    column = Cylinder(radius=0.8, height=spec.length / 2, align=_ON_BED).locate(
        Location((x, y, SEATED["thickness"] + spec.length / 2))
    )
    assert (clamped & column).volume == pytest.approx(0.0, abs=1e-6)


def test_hub_angle_is_a_gap_centre():
    """Gap centres sit half a pitch off the teeth, which are at multiples of it."""
    for teeth in (11, 12, 23, 30, 33):
        pitch = 360.0 / teeth
        offset = (hub_angle(teeth) / pitch) % 1.0
        assert offset == pytest.approx(0.5, abs=1e-9)
        assert abs(hub_angle(teeth) - 90.0) <= pitch / 2 + 1e-9


def test_a_hub_does_not_disturb_the_mesh(clamped):
    """Adding a hub must not move the teeth. Same check as the bare pair, run again
    on the gear that grew a boss."""
    _, pinion = gear_pair(**SEATED, gear_hub=ClampHubSpec(**HUB))
    spacing = pitch_radius(MODULE, SEATED["gear_teeth"]) + pitch_radius(MODULE, 12)

    assert (clamped & pinion.moved(Location((spacing, 0, 0)))).volume == pytest.approx(
        0.0, abs=1e-9
    )


@pytest.mark.parametrize(
    "kind, overrides, expected",
    [
        pytest.param(
            "counterbore",
            {"diameter": 3.0, "depth": 2.0},
            "not wider than the",
            id="counterbore_no_wider_than_its_bore",
        ),
        pytest.param(
            "counterbore",
            {"diameter": 5.0, "depth": 8.0},
            "no step for anything to seat against",
            id="counterbore_reaches_through",
        ),
        pytest.param(
            "counterbore",
            {"diameter": 9.0, "depth": 2.0},
            "of rim under the tooth roots",
            id="counterbore_eats_the_rim",
        ),
        pytest.param(
            "hub",
            {"diameter": 11.0, "length": 8.0},
            "of wall over the",
            id="hub_wall_too_thin_to_clamp",
        ),
        pytest.param(
            "hub",
            {"diameter": 16.0, "length": 8.0, "nut_across_flats": 5.5},
            "does not fit in a",
            id="nut_too_big_for_the_ear",
        ),
        pytest.param(
            "hub",
            {"diameter": 16.0, "length": 8.0, "slit_width": 9.0, "ear_width": 12.0},
            "leaving nothing either side to pinch",
            id="slit_eats_the_ear",
        ),
        pytest.param(
            "hub",
            {"diameter": 16.0, "length": 8.0, "ear_depth": 4.0},
            "of wall around the hole",
            id="screw_hole_has_no_wall",
        ),
    ],
)
def test_impossible_features_are_rejected_with_a_message_naming_the_problem(
    kind, overrides, expected
):
    key = "pinion_counterbore" if kind == "counterbore" else "gear_hub"
    spec = CounterboreSpec if kind == "counterbore" else ClampHubSpec

    with pytest.raises(TemplateError) as excinfo:
        gear_pair(**SEATED, **{key: spec(**overrides)})
    assert expected in str(excinfo.value)


def test_a_hub_on_a_solid_gear_is_refused():
    """A clamp with no bore grips nothing, and the refusal has to say that rather
    than build a boss with a hole in it."""
    with pytest.raises(TemplateError) as excinfo:
        gear_pair(**{**SEATED, "gear_bore": 0.0}, gear_hub=ClampHubSpec(**HUB))
    assert "grips nothing" in str(excinfo.value)


def test_the_new_features_are_read_back():
    params = GearPairParams(
        **SEATED,
        pinion_counterbore=CounterboreSpec(diameter=5.0, depth=3.0),
        gear_hub=ClampHubSpec(**HUB),
    )
    sentence = resolved_spec_sentence(params)

    assert "5mm x 3mm deep seat in one face for a magnet" in sentence
    assert "16mm x 8mm clamp hub" in sentence
    assert "5.5mm nut, dropped in from the top" in sentence
    # The assembly instruction that is not a dimension.
    assert "hub facing AWAY from the pinion" in sentence


def test_nested_feature_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        CounterboreSpec(diameter=5.0, depth=3.0, chamfer=0.5)
    with pytest.raises(ValidationError):
        ClampHubSpec(diameter=16.0, length=8.0, grub_screw=True)


# --------------------------------------------------------------------------- #
# Grub-screw hub
# --------------------------------------------------------------------------- #

GRUB = dict(style="grub", diameter=14.0, length=10.0)

#: The instrument build: 5mm tube, so the gear's bore drops with it.
TUBE = {**SEATED, "gear_bore": 5.0}


@pytest.fixture(scope="module")
def grubbed():
    gear, _ = gear_pair(**TUBE, gear_hub=ClampHubSpec(**GRUB))
    return gear


def test_nothing_on_a_grub_hub_reaches_past_the_hub(grubbed):
    """The reason this style exists.

    A pinch ear reaches to r=17 and collides with the yoke's axle boss, which sits
    at r=13.3 to 20.3 at the same station along the shaft. Everything on a grub hub
    has to stay inside the hub itself, above the teeth, or the clash comes back.
    """
    above_teeth = band(GRUB["length"], TUBE["thickness"] + 0.2)
    outside_hub = above_teeth - probe(GRUB["diameter"] / 2 + 1e-6)

    assert (grubbed & outside_hub).volume == pytest.approx(0.0, abs=1e-6)


def test_a_grub_hub_has_no_slit(grubbed):
    """Nothing pulls it shut, so cutting the disc open would only weaken it."""
    r_pitch = pitch_radius(MODULE, TUBE["gear_teeth"])
    annulus = (probe(r_pitch + 0.05) - probe(r_pitch - 0.05)) & band(TUBE["thickness"])
    assert len((grubbed & annulus).solids()) == TUBE["gear_teeth"]

    # And the hub is a closed ring: a thin band through its wall comes back whole.
    wall = (probe(6.9) - probe(5.2)) & band(2.0, TUBE["thickness"] + 1.0)
    assert len((grubbed & wall).solids()) == 1


@pytest.mark.parametrize("count, spacing", [(1, 120.0), (2, 120.0), (3, 120.0)])
def test_every_grub_screw_is_drilled_through_to_the_bore(count, spacing):
    """A hole that bottoms in the wall looks identical from outside and grips
    nothing. Probed on the bore wall, where it has to break through."""
    gear, _ = gear_pair(
        **TUBE,
        gear_hub=ClampHubSpec(**{**GRUB, "grub_count": count, "grub_spacing": spacing}),
    )
    radius = TUBE["gear_bore"] / 2 + 0.4

    for index in range(count):
        angle = math.radians(90.0 + index * spacing)
        point = Cylinder(radius=0.6, height=1.5, align=_ON_BED).locate(
            Location(
                (
                    radius * math.cos(angle),
                    radius * math.sin(angle),
                    TUBE["thickness"] + GRUB["length"] / 2 - 0.75,
                )
            )
        )
        assert (gear & point).volume == pytest.approx(0.0, abs=1e-6), (
            f"grub screw {index} does not reach the bore"
        )


def test_grub_screws_remove_less_than_a_pinch_clamp():
    """Sanity that the holes exist at all: a hub with screws weighs less than one
    without, and the difference is roughly the two drilled cylinders."""
    plain, _ = gear_pair(**TUBE, gear_hub=ClampHubSpec(**{**GRUB, "grub_count": 1}))
    pair, _ = gear_pair(**TUBE, gear_hub=ClampHubSpec(**{**GRUB, "grub_count": 2}))

    one_hole = math.pi * (2.5 / 2) ** 2 * ((14.0 - 5.0) / 2)
    assert plain.volume - pair.volume == pytest.approx(one_hole, rel=0.15)


@pytest.mark.parametrize(
    "overrides, expected",
    [
        pytest.param(
            {"diameter": 9.0},
            "to thread into",
            id="wall_too_thin_to_tap",
        ),
        pytest.param(
            {"length": 3.0},
            "either side of the hole",
            id="hub_too_short_for_the_hole",
        ),
        pytest.param(
            {"grub_spacing": 20.0},
            "where they break into the",
            id="screws_collide_at_the_bore",
        ),
        pytest.param(
            {"grub_count": 4, "grub_spacing": 120.0},
            "wrap past a full turn",
            id="screws_wrap_onto_each_other",
        ),
        pytest.param(
            {"grub_taper": 5.0},
            "opens each hole out by more than",
            id="taper_wider_than_the_wall_is_a_countersink",
        ),
        pytest.param(
            {"length": 6.0, "grub_taper": 2.0},
            "opened to 4.50mm at the face",
            id="tapered_hole_too_long_for_the_hub",
        ),
    ],
)
def test_impossible_grub_hubs_are_rejected(overrides, expected):
    with pytest.raises(TemplateError) as excinfo:
        gear_pair(**TUBE, gear_hub=ClampHubSpec(**{**GRUB, **overrides}))
    assert expected in str(excinfo.value)


def test_a_style_refuses_the_other_style_s_settings():
    """Silently ignoring a field someone typed is how you ship a gear that clamps
    nothing while they are certain they asked for a nut trap."""
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        ClampHubSpec(style="grub", diameter=14.0, length=10.0, nut_across_flats=5.5)
    assert "does not use" in str(excinfo.value)

    with pytest.raises((TemplateError, ValidationError)):
        ClampHubSpec(style="pinch", diameter=16.0, length=8.0, grub_count=2)


def test_pinch_is_still_the_default():
    """The style that marks nothing stays the default; grub is opted into."""
    assert ClampHubSpec(diameter=16.0, length=8.0).style == "pinch"


def _hole_radius_at(gear, y: float, axis_z: float) -> float:
    """Measure the grub hole's radius at radial station *y*, by bisection.

    Screw 0 is drilled along +Y, so its radial station is simply y, and offsets are
    taken straight up in Z from the screw axis. Probes are tiny because a tapered
    wall closes across the probe's own width — a fat probe reads the narrow end of
    whatever it spans.
    """

    def is_void(offset: float) -> bool:
        probe_ = Cylinder(radius=0.05, height=0.05).locate(
            Location((0.0, y, axis_z + offset))
        )
        return (gear & probe_).volume < 1e-12

    lo, hi = 0.3, 4.0
    assert is_void(lo), "probe started inside material, not in the hole"
    assert not is_void(hi), "probe never reached material"
    while hi - lo > 0.005:
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if is_void(mid) else (lo, mid)
    return (lo + hi) / 2


def test_a_tapered_grub_hole_is_a_cone_the_whole_way_through():
    """The bug this replaced, and the reason it is worth a real measurement.

    A mouth chamfer over a straight hole also reads as "wider at the face" — it
    passes any two-point test — and on screen it is a wide opening sitting on a
    parallel bore, which is not a taper and is exactly what got rejected. So the
    hole is measured at three stations and has to narrow *continuously*, tracking
    the intended cone rather than stepping once and going straight.
    """
    taper = 0.9
    gear, _ = gear_pair(**TUBE, gear_hub=ClampHubSpec(**{**GRUB, "grub_taper": taper}))

    face = GRUB["diameter"] / 2
    inner = TUBE["gear_bore"] / 2
    axis_z = TUBE["thickness"] + GRUB["length"] / 2
    slope = (taper / 2) / (face - inner)

    stations = [inner + 0.3, (inner + face) / 2, face - 0.3]
    measured = [_hole_radius_at(gear, y, axis_z) for y in stations]

    # Continuously narrowing inward, not one step and then parallel.
    assert measured[0] < measured[1] < measured[2]

    # And on the intended cone at every station, within the probe's own size.
    for y, got in zip(stations, measured):
        expected = 2.5 / 2 + slope * (y - inner)
        assert got == pytest.approx(expected, abs=0.07), f"at y={y}: {got:.3f}"


def test_the_narrow_end_of_a_taper_stays_at_the_tapping_diameter():
    """Grip at the shaft is the one dimension in this hub doing a job. A cone
    anchored anywhere but the bore surface would trade it away for the taper, and
    nothing about the part would look wrong."""
    gear, _ = gear_pair(**TUBE, gear_hub=ClampHubSpec(**{**GRUB, "grub_taper": 0.9}))
    inner = TUBE["gear_bore"] / 2
    axis_z = TUBE["thickness"] + GRUB["length"] / 2

    at_the_bore = _hole_radius_at(gear, inner + 0.1, axis_z)
    assert at_the_bore == pytest.approx(2.5 / 2, abs=0.07)


def test_the_taper_only_removes_material():
    """A cone opening outward can only take material away, and must take some."""
    plain, _ = gear_pair(**TUBE, gear_hub=ClampHubSpec(**GRUB))
    tapered, _ = gear_pair(**TUBE, gear_hub=ClampHubSpec(**{**GRUB, "grub_taper": 0.9}))
    assert tapered.volume < plain.volume

    # Bounded, not matched: on a flat face each hole would gain a frustum shell over
    # the 4.5mm wall, from r=1.25 to r=1.70. The hub face is round, so the cone
    # breaks out of it early and takes strictly less than that.
    wall = (GRUB["diameter"] - TUBE["gear_bore"]) / 2
    frustum = math.pi * wall / 3 * (1.25**2 + 1.25 * 1.70 + 1.70**2)
    ideal = 2 * (frustum - math.pi * 1.25**2 * wall)
    removed = plain.volume - tapered.volume
    assert 0.5 * ideal < removed < ideal


def test_mouths_that_would_merge_on_the_hub_face_are_refused():
    """The bore check reasons that the holes are closest together where they break
    in. A taper inverts that at the far end — the hole is widest at the outside —
    so a pair that clears at the bore can still merge into one slot at the face.

    Reachable only on a big shaft, where the hub and bore radii are close enough
    that the outer circle is not simply roomier. Checked against the validator
    directly: a gear that could carry a 20mm bore is a different part entirely.
    """
    spec = ClampHubSpec(
        style="grub", diameter=30.0, length=12.0, grub_taper=5.0, grub_spacing=30.0
    )
    # Clears where it breaks into the bore: 5.18mm apart, and 4.0mm is the minimum.
    with pytest.raises(TemplateError) as excinfo:
        _validate_grub_hub(spec, 20.0)
    assert "on the 30.0mm hub face" in str(excinfo.value)
    assert "opens each hole out to 7.50mm" in str(excinfo.value)


def test_the_grub_hub_reads_back_with_its_wall_thickness():
    """Thread engagement is what decides whether the screw strips, and it is not
    any number you typed — it is the difference of two of them."""
    params = GearPairParams(**TUBE, gear_hub=ClampHubSpec(**GRUB))
    sentence = resolved_spec_sentence(params)

    assert "2 grub screws 120 degrees apart" in sentence
    assert "through 4.5mm of wall" in sentence
    assert "Nothing reaches past the hub" in sentence
    assert "mark the shaft" in sentence


# --------------------------------------------------------------------------- #
# Arithmetic and read-back
# --------------------------------------------------------------------------- #


def test_centre_distance_is_the_sum_of_the_pitch_radii():
    params = GearPairParams(**ENCODER)
    assert centre_distance(params) == pytest.approx(0.8 * (23 + 12) / 2)
    assert ratio(params) == pytest.approx(23 / 12)


def test_undercut_limit_matches_the_standard_values():
    """2 / sin^2(a), rounded up: the textbook 18 at 20 degrees, 12 at 25."""
    assert undercut_limit(20.0) == 18
    assert undercut_limit(25.0) == 12


def test_the_read_back_gives_the_numbers_you_cannot_measure():
    sentence = resolved_spec_sentence(GearPairParams(**ENCODER))

    assert "20mm outside diameter, 10mm bore" in sentence
    assert "11.2mm outside diameter, 5.2mm bore" in sentence
    # The two that are not any dimension of either part.
    assert "Mount the axles 14mm apart" in sentence
    assert "Ratio 1.917:1" in sentence
    assert "0.0459 degrees of shaft per count" in sentence


def test_the_read_back_says_when_a_gear_is_undercut():
    """12 teeth at 20 degrees is under the limit. It prints and runs, so this is a
    sentence rather than a refusal — but it must not go unsaid."""
    assert "undercut limit" in resolved_spec_sentence(GearPairParams(**ENCODER))

    clear = {**ENCODER, "pinion_teeth": 20, "pinion_bore": 5.2}
    assert "undercut limit" not in resolved_spec_sentence(GearPairParams(**clear))


def test_a_solid_gear_says_so():
    params = GearPairParams(module=1.0, gear_teeth=20, pinion_teeth=20, thickness=4.0)
    assert "no bore (solid)" in resolved_spec_sentence(params)


def test_gears_have_no_interior():
    """The registry asks every template for inner dims; a gear has none, and None is
    the answer rather than a made-up bounding box."""
    assert inner_dims(GearPairParams(**ENCODER)) is None


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "overrides, expected",
    [
        pytest.param(
            {"pinion_bore": 6.0},
            "of rim under the tooth roots",
            id="bore_eats_the_rim",
        ),
        pytest.param(
            {"gear_teeth": 7, "gear_bore": 0.0, "backlash": 0.6},
            "run to a point",
            id="teeth_run_to_a_point",
        ),
        pytest.param(
            {"backlash": 1.6},
            "wider than the",
            id="backlash_wider_than_the_tooth",
        ),
        pytest.param(
            {"pressure_angle": 45.0},
            "outside the usable range",
            id="pressure_angle_out_of_range",
        ),
    ],
)
def test_impossible_gears_are_rejected_with_a_message_naming_the_problem(
    overrides, expected
):
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        gear_pair(**{**ENCODER, **overrides})
    assert expected in str(excinfo.value)


def test_the_bore_check_names_the_root_diameter_to_aim_at():
    """A message that says only 'too big' makes the next guess a guess."""
    with pytest.raises(TemplateError) as excinfo:
        gear_pair(**{**ENCODER, "pinion_bore": 6.0})

    message = str(excinfo.value)
    assert "pinion_bore=6.0mm" in message
    assert f"{2 * root_radius(MODULE, 12):.2f}mm diameter" in message
    assert str(_MIN_RIM) in message


def test_the_params_model_rejects_unknown_fields():
    """extra='forbid' is the guarantee for a new template too, not just the box."""
    with pytest.raises(ValidationError):
        GearPairParams(**ENCODER, helical_angle=15.0)


def test_too_few_teeth_is_refused_by_the_schema():
    with pytest.raises(ValidationError):
        GearPairParams(**{**ENCODER, "pinion_teeth": 4})


def test_the_bare_function_validates_as_hard_as_the_model():
    """Direct callers bypass Pydantic, so _validate has to stand on its own."""
    with pytest.raises(TemplateError):
        gear_pair(module=0.8, gear_teeth=23, pinion_teeth=12, thickness=-1.0)
    with pytest.raises(TemplateError):
        gear_pair(module=0.8, gear_teeth=23, pinion_teeth=4, thickness=5.0)


def test_module_and_teeth_actually_set_the_size():
    """OD = module * (teeth + 2) is the relationship the whole template rests on."""
    for module, teeth in ((1.0, 18), (0.5, 38), (0.8, 23)):
        assert 2 * tip_radius(module, teeth) == pytest.approx(module * (teeth + 2))
        assert 2 * root_radius(module, teeth) == pytest.approx(module * (teeth - 2.5))
        assert math.isclose(2 * pitch_radius(module, teeth), module * teeth)


# --------------------------------------------------------------------------- #
# Fit trial
# --------------------------------------------------------------------------- #

TRIAL = dict(module=0.8, teeth=30, thickness=3.0, bores=[15.0, 16.0, 17.0])


def test_a_trial_prints_one_gear_per_candidate_bore():
    parts = gear_fit_trial(GearFitTrialParams(**TRIAL))
    assert len(parts) == len(TRIAL["bores"])

    for part in parts:
        assert part.bounding_box().size.Z == pytest.approx(TRIAL["thickness"])


def test_trial_gears_come_out_smallest_bore_first():
    """They are identical apart from the hole, so the only way to tell them apart
    after printing is where they sat on the plate. The order has to be defined."""
    parts = gear_fit_trial(GearFitTrialParams(**{**TRIAL, "bores": [17.0, 15.0, 16.0]}))
    volumes = [part.volume for part in parts]

    # A bigger bore removes more metal, so smallest bore first means heaviest first.
    assert volumes == sorted(volumes, reverse=True)


def test_every_trial_gear_has_the_same_teeth():
    """The point of a trial: whichever fits is the gear. Different tooth geometry
    between them and the winner would not mesh with the pinion you already have."""
    parts = gear_fit_trial(GearFitTrialParams(**TRIAL))
    r_pitch = pitch_radius(TRIAL["module"], TRIAL["teeth"])
    annulus = (probe(r_pitch + 0.05) - probe(r_pitch - 0.05)) & band(TRIAL["thickness"])

    for part in parts:
        assert len((part & annulus).solids()) == TRIAL["teeth"]


def test_a_trial_bore_that_eats_the_rim_is_refused_with_the_widest_that_fits():
    """A refusal that names only the failure makes the next guess a guess."""
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        GearFitTrialParams(**{**TRIAL, "bores": [15.0, 16.0, 21.0]})

    message = str(excinfo.value)
    assert "under the tooth roots" in message
    # roots at 22mm diameter, less 1mm of rim per side
    assert "20.00mm" in message


def test_a_repeated_bore_is_refused():
    """Three identical prints are not a trial, and you could not tell them apart."""
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        GearFitTrialParams(**{**TRIAL, "bores": [16.0, 16.0, 17.0]})
    assert "repeats a size" in str(excinfo.value)


def test_a_trial_needs_exactly_as_many_bores_as_it_prints():
    """part_names is a fixed tuple the registry checks the build against, so the
    count is part of the template rather than a parameter."""
    for bores in ([15.0, 16.0], [15.0, 16.0, 17.0, 18.0]):
        with pytest.raises(ValidationError):
            GearFitTrialParams(**{**TRIAL, "bores": bores})


def test_the_trial_read_back_says_how_to_tell_them_apart():
    sentence = trial_spec_sentence(GearFitTrialParams(**TRIAL))
    assert "15mm, 16mm, 17mm" in sentence
    assert "smallest bore first, left to right" in sentence


def test_a_trial_can_carry_the_hub_it_will_be_fitted_with():
    """Testing a plain ring tells you the bore fits; it does not tell you the part
    you actually need fits."""
    hub = ClampHubSpec(style="grub", diameter=14.0, length=10.0)
    parts = gear_fit_trial(GearFitTrialParams(**{**TRIAL, "bores": [6.0, 7.0, 8.0], "hub": hub}))

    for part in parts:
        assert part.bounding_box().size.Z == pytest.approx(TRIAL["thickness"] + hub.length)


def test_every_bore_in_a_trial_is_checked_against_the_hub():
    """A wall thick enough at the smallest bore can be too thin at the largest, and
    only the failing one would show it — so the check runs per bore, not once."""
    hub = ClampHubSpec(style="grub", diameter=14.0, length=10.0)

    # 6 to 8 leaves 4.0mm down to 3.0mm of wall: fine throughout.
    GearFitTrialParams(**{**TRIAL, "bores": [6.0, 7.0, 8.0], "hub": hub})

    # Stretch the top of the range and the widest bore alone breaks it.
    with pytest.raises((TemplateError, ValidationError)) as excinfo:
        GearFitTrialParams(**{**TRIAL, "bores": [6.0, 7.0, 10.0], "hub": hub})
    assert "to thread into" in str(excinfo.value)


def test_a_hubbed_trial_reads_back_its_thinnest_wall():
    """Quoting the wall at the smallest bore would describe the easiest of the three
    and say nothing about the one most likely to strip."""
    hub = ClampHubSpec(style="grub", diameter=14.0, length=10.0)
    sentence = trial_spec_sentence(
        GearFitTrialParams(**{**TRIAL, "bores": [6.0, 7.0, 8.0], "hub": hub})
    )
    assert "through 3.0mm of wall" in sentence
    assert "distance. The gear" in sentence, "missing space before the hub phrase"
