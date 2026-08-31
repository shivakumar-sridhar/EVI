r"""gear_pair — a spur gear and its meshing pinion, for driving a rotary encoder.

Why a *pair* and not a single gear: the two halves are only useful if their module,
pressure angle and backlash agree exactly and they are mounted at the one centre
distance that makes them mesh. Split across two calls those are three things a
client has to get right and nothing checks. Built together they are structural, and
the read-back can state the centre distance and the ratio — which is the number the
user actually wants, because it is what multiplies their encoder's resolution.

* **Involute flanks, sampled as a polyline.** The tooth flank is the involute of the
  base circle, evaluated at ``_FLANK_POINTS`` radii and joined with straight
  segments. On a 20mm gear each segment spans well under a tenth of a millimetre,
  which is inside the STL export tolerance the rest of this package uses — the
  facets are finer than the printer can resolve. It is drawn as one closed outline
  for the whole gear rather than a tooth unioned N times: one boolean instead of
  twenty-three, and no coincident faces to go wrong.

* **Below the base circle the flank is radial.** A real gear has a trochoid there,
  cut by the hob's tip. Cutting a straight line to the root circle instead is the
  standard simplification for printed gears: it removes slightly more material than
  a trochoid, so teeth never bind, and the contact never happens down there anyway.

* **The pinion is built already phased to mesh.** A gear with a tooth centred at 0
  degrees needs the pinion to present a tooth *space* at 180 degrees. Odd tooth
  counts do that unaided; even ones are rotated half a pitch. Rotating a gear about
  its own axis costs nothing in print pose, and it means "place the pinion at
  (centre_distance, 0) and it meshes" is true rather than nearly true.

Tooth geometry, one flank::

        ra  ___/‾‾\___          <- tip circle: rp + module
        rp  ---/----\---        <- pitch circle: module * teeth / 2
        rb    /      \          <- base circle: rp * cos(pressure angle)
        rf  _/        \_        <- root circle: rp - 1.25 * module
             |        |
             radial below rb

Resolution, which is the whole point::

    shaft degrees per encoder count = 360 / (4096 * gear_teeth / pinion_teeth)

    23:12 on an AS5600 -> 0.088 deg per count at the magnet
                       -> 0.046 deg per count at the shaft
"""

from __future__ import annotations

import math
from typing import Literal

from build123d import (
    Align,
    Box,
    Cone,
    Cylinder,
    Location,
    Part,
    Polyline,
    RegularPolygon,
    Rotation,
    extrude,
    make_face,
)
from evee.templates.errors import TemplateError
from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ClampHubSpec",
    "GearFitTrialParams",
    "TRIAL_PART_NAMES",
    "gear_fit_trial",
    "trial_spec_sentence",
    "CounterboreSpec",
    "GearPairParams",
    "PART_NAMES",
    "TemplateError",
    "centre_distance",
    "face_widths",
    "gear_pair",
    "hub_angle",
    "inner_dims",
    "mesh_rotation",
    "resolved_spec_sentence",
]

PART_NAMES = ("gear", "pinion")

#: A fit trial prints one gear per candidate bore. Three is not a soft limit: the
#: registry's part_names is a fixed tuple checked against what a build returns, so
#: the count is part of the template's identity rather than a parameter.
TRIAL_PART_NAMES = ("bore_1", "bore_2", "bore_3")

#: How a clamp hub grips its shaft. "pinch" slits the hub and pulls it shut with a
#: screw across an ear; "grub" threads set screws radially through the hub wall.
#: The ear reaches well past the hub's own diameter, which is fine on a shaft with
#: nothing else near it and fatal next to anything the gear has to turn beside.
ClampStyle = Literal["pinch", "grub"]

#: Addendum and dedendum as multiples of the module. The 1.25 dedendum is the
#: standard full-depth tooth: 0.25 of clearance under the mating tip.
_ADDENDUM = 1.0
_DEDENDUM = 1.25

#: Points sampled along one involute flank, across the tip arc, and across one
#: root arc. Raising these costs facets, not accuracy of the pitch geometry.
_FLANK_POINTS = 14
_TIP_POINTS = 6
_ROOT_POINTS = 6

#: Material left between a bore and the root circle. Thinner than this and the rim
#: splits when something is pressed into the bore.
_MIN_RIM = 1.0

#: Narrowest flat the tip of a tooth may end in. Below this the tooth has run to a
#: point, which prints as a fin and wears immediately.
_MIN_TIP_LAND = 0.2

#: Fewest teeth worth generating at all — below this the involute is mostly gone.
_MIN_TEETH = 6

#: Wall left around a clamp hub's pinch screw, and between the slit and the bore.
_MIN_EAR_WALL = 1.5

#: Wall a grub screw needs to thread into. Less than this and an M3 tapped straight
#: into printed plastic strips the first time it is tightened onto a shaft.
_MIN_THREAD_ENGAGEMENT = 2.5

#: How far a cutter pokes past a face it is meant to break through. Coincident
#: boolean faces are where OCC gets ambiguous — the same trick box.py uses.
_OVERCUT = 1.0

#: Floor for a tapered cutter's narrow end. A cone extrapolated an _OVERCUT past
#: the bore can reach zero or negative radius on a small hole with a steep taper,
#: and a degenerate cone is an OCC failure rather than a smaller hole.
_MIN_CUTTER_RADIUS = 0.05

#: Counts of teeth in an AS5600 revolution, for the resolution read-back. The
#: encoder is 12-bit; this is not a gear property and never enters the geometry.
_ENCODER_COUNTS = 4096


class CounterboreSpec(BaseModel):
    """A wider, shallower hole at one end of a gear's bore — a seat for a magnet.

    Cut at the **+Z end in print pose**, so its floor is an upward-facing annulus
    printed onto solid material. At the other end the same step would be a ceiling
    bridging over the bore, and a magnet seat that droops is a magnet that sits
    crooked — which the AS5600 reads as a wobble in the angle.
    """

    model_config = ConfigDict(extra="forbid")

    diameter: float = Field(
        gt=0,
        description=(
            "Counterbore diameter in mm. For a magnet, its own diameter plus about "
            "0.2mm — snug enough not to rattle, loose enough not to split the rim."
        ),
    )
    depth: float = Field(
        gt=0,
        description=(
            "How deep the counterbore goes, in mm. Set it to the magnet's thickness "
            "and the magnet finishes flush with the face, which is what makes the "
            "air gap a dimension you can design rather than one you discover."
        ),
    )


class ClampHubSpec(BaseModel):
    """A boss on the gear's face, slit and pinched by a screw, that grips a shaft.

    A plain bore does not grip anything. For an encoder that matters more than
    usual: a gear that slips does not fail loudly, it just reports less rotation
    than happened, and nothing downstream can tell.

    The hub sits on the **+Z face in print pose**, above the teeth, so the toothed
    disc prints first on the bed and the hub stacks on top of it — no overhanging
    ring. Mount the gear with the hub facing *away* from the pinion: the ear
    reaches past the tip circle, and it clears the pinion only by sitting at a
    different station along the shaft.
    """

    model_config = ConfigDict(extra="forbid")

    style: ClampStyle = Field(
        default="pinch",
        description=(
            "pinch: a slit hub pulled shut by a screw through an ear. Grips hardest "
            "and marks nothing, but the ear reaches far past the hub. grub: set "
            "screws threaded radially through the hub wall — everything stays inside "
            "the hub's own diameter, at the cost of denting the shaft."
        ),
    )
    diameter: float = Field(
        gt=0,
        description="Hub outside diameter in mm. Needs real wall over the bore.",
    )
    length: float = Field(
        gt=0,
        description=(
            "How far the hub stands off the gear face in mm. This is the grip "
            "length on the shaft — short hubs cock on the shaft rather than clamp."
        ),
    )
    slit_width: float = Field(
        default=1.2,
        gt=0,
        description=(
            "Width of the radial cut that lets the hub close, in mm. Must be wider "
            "than the nozzle can bridge or the printer will weld it shut."
        ),
    )
    screw_diameter: float = Field(
        default=3.4,
        gt=0,
        description="Clearance hole through the ear in mm. 3.4 suits M3, 2.8 M2.5.",
    )
    ear_width: float = Field(
        default=12.0,
        gt=0,
        description="Ear length along the screw axis in mm — screw head to nut.",
    )
    ear_depth: float = Field(
        default=7.0,
        gt=0,
        description="How far the ear stands out past the hub, radially, in mm.",
    )
    nut_across_flats: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Hex pocket for a captive nut, across the flats, in mm — 5.5 for M3. "
            "0, the default, leaves a plain hole and you hold the nut yourself. A "
            "trap needs a deeper ear than a plain hole does; the refusal says how "
            "much deeper."
        ),
    )
    nut_depth: float = Field(
        default=2.7,
        ge=0,
        description="How deep the hex pocket goes in mm. An M3 nut is 2.4 thick.",
    )
    grub_diameter: float = Field(
        default=2.5,
        gt=0,
        description=(
            "Tapping hole for each grub screw in mm — 2.5 for M3, 2.05 for M2.5. "
            "A tapping size, not a clearance: the thread is cut in the plastic."
        ),
    )
    grub_count: int = Field(
        default=2,
        ge=1,
        description=(
            "How many grub screws. Two is the default for a reason: one screw shoves "
            "the shaft to the far side of its bore, moving the gear's centre by the "
            "whole bore clearance. Two at an angle wedge it against the wall between."
        ),
    )
    grub_spacing: float = Field(
        default=120.0,
        gt=0,
        lt=360.0,
        description="Angle between grub screws in degrees. 120 with two screws.",
    )
    grub_taper: float = Field(
        default=0.0,
        ge=0,
        description=(
            "How much wider each grub hole is at the hub face than where it breaks "
            "into the bore, in mm of diameter. The hole becomes one continuous cone "
            "through the whole wall rather than a funnel sitting on a straight hole: "
            "the screw enters easily and keeps tightening the whole way in, cutting "
            "thread against a wall that is always closing on it. The narrow end stays "
            "at grub_diameter, so grip where it meets the shaft is unchanged. Keep it "
            "slight — 0.9 in a 4mm wall is about 13 degrees included. A taper wider "
            "than the wall is a countersink rather than a tapped hole, and is refused."
        ),
    )

    @model_validator(mode="after")
    def _check_style(self) -> "ClampHubSpec":
        """Refuse settings that belong to the other style rather than ignore them.

        Silently dropping a field someone typed is how you end up with a gear that
        clamps nothing and a person certain they asked for a nut trap.
        """
        pinch_only = {"slit_width", "screw_diameter", "ear_width", "ear_depth",
                      "nut_across_flats", "nut_depth"}
        grub_only = {"grub_diameter", "grub_count", "grub_spacing", "grub_taper"}
        wrong = (grub_only if self.style == "pinch" else pinch_only) & self.model_fields_set
        if wrong:
            other = "grub" if self.style == "pinch" else "pinch"
            raise TemplateError(
                f"gear_hub style={self.style!r} does not use "
                f"{', '.join(sorted(wrong))} — {'that belongs' if len(wrong) == 1 else 'those belong'} "
                f"to style={other!r}"
            )
        return self


class GearPairParams(BaseModel):
    """Validated parameters for :func:`gear_pair`.

    ``extra="forbid"`` for the same reason as every other template here: the schema
    is what a client model fills in, and server-side rejection of an invented field
    is the guarantee, not the client's decoder.

    There are no house defaults for gears in ``config/defaults.toml`` — module and
    tooth count are not fit tuning, they are the design. ``pressure_angle`` and
    ``backlash`` do have defaults, and those are conventions rather than anything
    measured on this machine.
    """

    model_config = ConfigDict(extra="forbid")

    module: float = Field(
        gt=0,
        description=(
            "Tooth size in mm — pitch diameter divided by tooth count. Both gears "
            "share it; two gears of different module do not mesh. On a 0.4mm nozzle "
            "0.8 is comfortable, 1.0 is robust, 0.5 is about the floor."
        ),
    )
    gear_teeth: int = Field(
        ge=_MIN_TEETH,
        description=(
            "Teeth on the large gear — the one on the measured shaft. With module, "
            "this sets its size: outside diameter = module * (teeth + 2)."
        ),
    )
    pinion_teeth: int = Field(
        ge=_MIN_TEETH,
        description=(
            "Teeth on the pinion — the one carrying the encoder magnet. Fewer teeth "
            "means a higher ratio and finer shaft resolution, at the cost of a "
            "weaker, more undercut tooth."
        ),
    )
    thickness: float = Field(
        gt=0,
        description=(
            "Face width in mm — how tall the gears are. Applies to both unless "
            "pinion_thickness overrides it for the pinion."
        ),
    )
    pinion_thickness: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Face width of the pinion in mm, when it differs from the gear's. "
            "Omit to make them equal. A taller pinion is a normal thing to want — "
            "it tolerates axial misalignment and gives a deeper bore — but only the "
            "narrower of the two ever carries tooth contact, so it buys no strength."
        ),
    )
    gear_bore: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Through hole in the large gear in mm, for the shaft it rides on. "
            "0 leaves it solid. A plain bore does not grip a smooth shaft on its own."
        ),
    )
    pinion_bore: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Through hole in the pinion in mm — a magnet seat, or an axle. "
            "0 leaves it solid."
        ),
    )
    pressure_angle: float = Field(
        default=20.0,
        ge=14.5,
        le=30.0,
        description=(
            "Flank angle in degrees. 20 is the standard and what most stock gears "
            "use. Raising it towards 25 lets a small pinion avoid undercut, at the "
            "cost of higher separating force."
        ),
    )
    backlash: float = Field(
        default=0.15,
        ge=0,
        description=(
            "Play between the teeth in mm, taken off the tooth thickness at the "
            "pitch circle. Zero binds on a printed gear; 0.15 suits FDM. For an "
            "encoder this is the hysteresis you will read when the shaft reverses."
        ),
    )

    pinion_counterbore: CounterboreSpec | None = Field(
        default=None,
        description=(
            "A magnet seat in the +Z end of the pinion's bore. Omit for a plain "
            "bore all the way through."
        ),
    )
    gear_hub: ClampHubSpec | None = Field(
        default=None,
        description=(
            "A slit, screw-pinched boss on the gear that actually grips the shaft. "
            "Omit and the gear is free to slip, which for an encoder is a silent "
            "error rather than a loud one."
        ),
    )

    @model_validator(mode="after")
    def _check_geometry(self) -> "GearPairParams":
        _validate(
            self.module,
            self.gear_teeth,
            self.pinion_teeth,
            self.thickness,
            self.gear_bore,
            self.pinion_bore,
            self.pressure_angle,
            self.backlash,
            self.pinion_thickness,
            self.pinion_counterbore,
            self.gear_hub,
        )
        return self


# --------------------------------------------------------------------------- #
# Tooth arithmetic
# --------------------------------------------------------------------------- #


def _involute(angle: float) -> float:
    """inv(a) = tan(a) - a. The polar lag of an involute at pressure angle *a*."""
    return math.tan(angle) - angle


def pitch_radius(module: float, teeth: int) -> float:
    """Where the two gears roll on each other without slipping."""
    return module * teeth / 2


def tip_radius(module: float, teeth: int) -> float:
    """Outside radius. This is the one a caliper measures."""
    return pitch_radius(module, teeth) + _ADDENDUM * module


def root_radius(module: float, teeth: int) -> float:
    """Bottom of the tooth space. Everything inside this is solid rim."""
    return pitch_radius(module, teeth) - _DEDENDUM * module


def centre_distance(params: GearPairParams) -> float:
    """Axle spacing that makes the pair mesh — the sum of the pitch radii.

    Not a suggestion: move the axles apart and backlash grows until the teeth skip;
    move them together and the pair binds regardless of how much backlash was cut.
    """
    return pitch_radius(params.module, params.gear_teeth) + pitch_radius(
        params.module, params.pinion_teeth
    )


def face_widths(params: GearPairParams) -> tuple[float, float]:
    """(gear, pinion) face widths in mm, with the pinion's override resolved.

    One place decides this, so the geometry and the read-back cannot disagree about
    how tall the pinion is.
    """
    return params.thickness, (
        params.thickness if params.pinion_thickness is None else params.pinion_thickness
    )


def ratio(params: GearPairParams) -> float:
    """Pinion turns per gear turn. This is what multiplies encoder resolution."""
    return params.gear_teeth / params.pinion_teeth


def hub_angle(teeth: int) -> float:
    """Where to put the clamp slit, in degrees: the tooth *gap* nearest +Y.

    The slit has to cut the toothed disc as well as the hub — a hub slit alone
    cannot close, because the uncut disc behind it holds the bore round. Landing
    it in a gap means it removes root material rather than splitting a tooth down
    the middle, which would leave a half tooth that clicks through every mesh.
    """
    pitch = 360.0 / teeth
    index = round(90.0 / pitch - 0.5)
    return (index + 0.5) * pitch


def mesh_rotation(teeth: int) -> float:
    """Rotation in degrees that puts a tooth *space* at 180 degrees.

    The gear is drawn with a tooth centred at 0 degrees, so the pinion facing it
    across the line of centres must present a gap there. An odd tooth count already
    does — teeth at 2*pi*k/z put a space at pi — and an even one is half a pitch out.
    """
    return 0.0 if teeth % 2 else 180.0 / teeth


def _tip_land(module: float, teeth: int, pressure_angle: float, backlash: float) -> float:
    """Width of the flat left at the tip of a tooth, in mm.

    Runs to zero as tooth count falls or backlash grows; a negative value means the
    flanks crossed before reaching the tip and there is no tooth to draw.
    """
    alpha = math.radians(pressure_angle)
    r_pitch = pitch_radius(module, teeth)
    r_base = r_pitch * math.cos(alpha)
    r_tip = tip_radius(module, teeth)

    half_at_pitch = (math.pi * module / 2 - backlash) / (2 * r_pitch)
    half_at_base = half_at_pitch + _involute(alpha)
    half_at_tip = half_at_base - _involute(math.acos(min(1.0, r_base / r_tip)))
    return 2 * half_at_tip * r_tip


def undercut_limit(pressure_angle: float) -> int:
    """Fewest teeth that avoid undercut at this pressure angle: 2 / sin^2(a).

    Undercut is not an error — printed gears run undercut all the time — but it
    thins the tooth root, so the read-back says when a gear is below the limit.
    """
    alpha = math.radians(pressure_angle)
    return math.ceil(2 / math.sin(alpha) ** 2)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def _validate(
    module: float,
    gear_teeth: int,
    pinion_teeth: int,
    thickness: float,
    gear_bore: float,
    pinion_bore: float,
    pressure_angle: float,
    backlash: float,
    pinion_thickness: float | None = None,
    pinion_counterbore: "CounterboreSpec | None" = None,
    gear_hub: "ClampHubSpec | None" = None,
) -> None:
    """Cross-field checks. Raises :class:`TemplateError` naming the bad values.

    Shared by the bare function and the Pydantic model, so a direct caller gets the
    same guarantees a client does.
    """
    if module <= 0:
        raise TemplateError(f"module must be positive, got {module}mm")
    if thickness <= 0:
        raise TemplateError(f"thickness must be positive, got {thickness}mm")
    if pinion_thickness is not None and pinion_thickness <= 0:
        raise TemplateError(
            f"pinion_thickness must be positive, got {pinion_thickness}mm "
            f"(omit it to match thickness={thickness}mm)"
        )
    if not 14.5 <= pressure_angle <= 30.0:
        raise TemplateError(
            f"pressure_angle={pressure_angle} degrees is outside the usable range "
            f"14.5 to 30 (20 is standard)"
        )
    if backlash < 0:
        raise TemplateError(f"backlash must be >= 0, got {backlash}mm")

    tooth_thickness = math.pi * module / 2
    if backlash >= tooth_thickness:
        raise TemplateError(
            f"backlash={backlash}mm is wider than the {tooth_thickness:.3f}mm tooth "
            f"it is cut from at module {module}mm"
        )

    for name, teeth, bore in (
        ("gear", gear_teeth, gear_bore),
        ("pinion", pinion_teeth, pinion_bore),
    ):
        if teeth < _MIN_TEETH:
            raise TemplateError(
                f"{name}_teeth={teeth} is too few to cut a usable involute "
                f"(minimum {_MIN_TEETH})"
            )

        land = _tip_land(module, teeth, pressure_angle, backlash)
        if land < _MIN_TIP_LAND:
            raise TemplateError(
                f"{name} teeth run to a point: {teeth} teeth at module {module}mm "
                f"and {pressure_angle} degrees leaves a {land:.3f}mm tip, under the "
                f"{_MIN_TIP_LAND}mm minimum (add teeth, or raise pressure_angle)"
            )

        # A counterbore is wider than the bore it sits in, so it — not the bore —
        # is what decides whether there is rim left under the teeth.
        widest = bore
        if name == "pinion" and pinion_counterbore is not None:
            widest = max(widest, pinion_counterbore.diameter)

        r_root = root_radius(module, teeth)
        if r_root <= 0:
            raise TemplateError(
                f"{name} root circle has no radius at module {module}mm with "
                f"{teeth} teeth"
            )
        if widest > 0 and widest / 2 + _MIN_RIM > r_root:
            label = (
                f"{name}_bore={bore}mm"
                if widest == bore
                else f"the {widest}mm counterbore in the {name}"
            )
            raise TemplateError(
                f"{label} leaves "
                f"{r_root - widest / 2:.2f}mm of rim under the tooth roots, under the "
                f"{_MIN_RIM}mm minimum (shrink it, add teeth, or raise module: "
                f"the roots sit at {2 * r_root:.2f}mm diameter)"
            )

    _validate_counterbore(
        pinion_counterbore,
        pinion_bore,
        thickness if pinion_thickness is None else pinion_thickness,
    )
    _validate_clamp_hub(gear_hub, gear_bore, module, gear_teeth)



def _validate_counterbore(
    spec: "CounterboreSpec | None", bore: float, face_width: float
) -> None:
    """A counterbore must be wider than its bore and shallower than the gear."""
    if spec is None:
        return

    if spec.diameter <= bore:
        raise TemplateError(
            f"pinion_counterbore diameter={spec.diameter}mm is not wider than the "
            f"{bore}mm bore it sits in — a counterbore that does not step out is "
            f"just a bore (raise it, or lower pinion_bore)"
        )
    if spec.depth >= face_width:
        raise TemplateError(
            f"pinion_counterbore depth={spec.depth}mm reaches through the "
            f"{_fmt(face_width)}mm face, which leaves no step for anything to seat "
            f"against (lower it, or raise pinion_thickness)"
        )


def _validate_grub_hub(spec: "ClampHubSpec", bore: float) -> None:
    """A grub screw needs wall to thread into, and room for its neighbours."""
    wall = (spec.diameter - bore) / 2
    # A taper spends no depth — the hole is threaded over the whole wall either way,
    # just at a diameter that closes as it goes. What it can do is open the far end
    # so wide that there is nothing left to cut into near the face.
    if spec.grub_taper > wall:
        raise TemplateError(
            f"gear_hub grub_taper={spec.grub_taper}mm opens each hole out by more "
            f"than the {wall:.2f}mm wall it is cut through, which makes it a "
            f"countersink rather than a tapped hole — the screw would find nothing "
            f"to bite near the face (lower grub_taper to at most {wall:.2f}mm)"
        )
    if wall < _MIN_THREAD_ENGAGEMENT:
        raise TemplateError(
            f"gear_hub diameter={spec.diameter}mm gives {wall:.2f}mm of wall for a "
            f"grub screw to thread into, under the {_MIN_THREAD_ENGAGEMENT}mm "
            f"minimum (raise diameter to at least "
            f"{bore + 2 * _MIN_THREAD_ENGAGEMENT}mm)"
        )
    # Measured at the face, where a tapered hole is widest — the hub is no longer as
    # long beside the hole as grub_diameter alone suggests.
    entry = spec.grub_diameter + spec.grub_taper
    if entry + 2 * _MIN_EAR_WALL > spec.length:
        widest = (
            f"grub_diameter={spec.grub_diameter}mm"
            if spec.grub_taper <= 0
            else (
                f"grub_diameter={spec.grub_diameter}mm opened to {entry:.2f}mm at the "
                f"face by grub_taper={spec.grub_taper}mm"
            )
        )
        raise TemplateError(
            f"gear_hub {widest} in a {spec.length}mm long hub leaves under "
            f"{_MIN_EAR_WALL}mm of hub either side of the hole (raise length to at "
            f"least {entry + 2 * _MIN_EAR_WALL:.2f}mm)"
        )

    if spec.grub_count > 1:
        # Holes are drilled at the bore, where the circle is smallest and they are
        # closest together. Checking at the outside would pass a pair that breaks
        # into one slot on the inside.
        chord = 2 * (bore / 2) * math.sin(math.radians(spec.grub_spacing) / 2)
        if chord < spec.grub_diameter + _MIN_EAR_WALL:
            raise TemplateError(
                f"gear_hub grub screws {spec.grub_spacing} degrees apart are "
                f"{chord:.2f}mm apart where they break into the {bore}mm bore, and "
                f"a {spec.grub_diameter}mm hole needs {spec.grub_diameter + _MIN_EAR_WALL}mm "
                f"(widen grub_spacing, or use one screw)"
            )
        # A taper inverts the reasoning above at the far end: the hole is widest
        # exactly at the face, the place the bore check says not to look. A pair
        # that clears where it breaks in can still merge into one slot outside.
        if spec.grub_taper > 0:
            outer_chord = (
                2 * (spec.diameter / 2) * math.sin(math.radians(spec.grub_spacing) / 2)
            )
            if outer_chord < entry + _MIN_EAR_WALL:
                raise TemplateError(
                    f"gear_hub grub screws {spec.grub_spacing} degrees apart are "
                    f"{outer_chord:.2f}mm apart on the {spec.diameter}mm hub face, "
                    f"where grub_taper={spec.grub_taper}mm opens each hole out to "
                    f"{entry:.2f}mm and they need {entry + _MIN_EAR_WALL:.2f}mm "
                    f"(lower grub_taper, or widen grub_spacing)"
                )
        if spec.grub_count * spec.grub_spacing > 360:
            raise TemplateError(
                f"{spec.grub_count} grub screws {spec.grub_spacing} degrees apart "
                f"wrap past a full turn and would land on top of each other"
            )


def _validate_clamp_hub(
    spec: "ClampHubSpec | None", bore: float, module: float, teeth: int
) -> None:
    """A clamp hub has to have wall to clamp with, and an ear to pinch."""
    if spec is None:
        return

    if bore <= 0:
        raise TemplateError(
            "gear_hub needs a gear_bore to clamp onto; a hub around a solid gear "
            "grips nothing"
        )
    if spec.diameter <= bore + 2 * _MIN_RIM:
        raise TemplateError(
            f"gear_hub diameter={spec.diameter}mm leaves "
            f"{(spec.diameter - bore) / 2:.2f}mm of wall over the {bore}mm bore, "
            f"under the {_MIN_RIM}mm minimum (need at least "
            f"{bore + 2 * _MIN_RIM}mm)"
        )

    if spec.style == "grub":
        _validate_grub_hub(spec, bore)
        return

    if spec.slit_width >= spec.ear_width - 2 * _MIN_EAR_WALL:
        raise TemplateError(
            f"gear_hub slit_width={spec.slit_width}mm consumes the "
            f"{spec.ear_width}mm ear, leaving nothing either side to pinch with"
        )
    if spec.screw_diameter + 2 * _MIN_EAR_WALL > spec.ear_depth:
        raise TemplateError(
            f"gear_hub screw_diameter={spec.screw_diameter}mm in a "
            f"{spec.ear_depth}mm deep ear leaves under {_MIN_EAR_WALL}mm of wall "
            f"around the hole (raise ear_depth to at least "
            f"{spec.screw_diameter + 2 * _MIN_EAR_WALL}mm)"
        )
    if spec.nut_across_flats > 0:
        if spec.nut_across_flats + 2 * _MIN_EAR_WALL > spec.ear_depth:
            raise TemplateError(
                f"gear_hub nut_across_flats={spec.nut_across_flats}mm does not fit "
                f"in a {spec.ear_depth}mm deep ear (raise ear_depth to at least "
                f"{spec.nut_across_flats + 2 * _MIN_EAR_WALL}mm)"
            )
        if spec.nut_depth * 2 >= spec.ear_width:
            raise TemplateError(
                f"gear_hub nut_depth={spec.nut_depth}mm eats more than half the "
                f"{spec.ear_width}mm ear, so the screw has nothing to pull against"
            )

    # The slit lands in a tooth gap, so this only fails on gears too coarse to have
    # a gap wider than the cut.
    gap_width = 2 * root_radius(module, teeth) * math.pi / teeth
    if spec.slit_width >= gap_width:
        raise TemplateError(
            f"gear_hub slit_width={spec.slit_width}mm is wider than the "
            f"{gap_width:.2f}mm gap between tooth roots, so the cut would take out "
            f"whole teeth (narrow the slit, or use more teeth)"
        )


# --------------------------------------------------------------------------- #
# Read-back
# --------------------------------------------------------------------------- #


def inner_dims(params: GearPairParams) -> None:
    """Gears have no interior. The registry's read-back skips it."""
    return None


def _fmt(value: float) -> str:
    """Millimetre value without pointless trailing zeros: 20.0 -> '20'."""
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def resolved_spec_sentence(params: GearPairParams) -> str:
    """The Gate 1 read-back, templated from *validated* params.

    Leads with the diameters, because those are what a caliper checks, and ends with
    the encoder resolution, because that is the number the pair exists to change and
    it is not readable off any dimension.
    """
    gear_od = 2 * tip_radius(params.module, params.gear_teeth)
    pinion_od = 2 * tip_radius(params.module, params.pinion_teeth)
    spacing = centre_distance(params)
    drive = ratio(params)
    gear_face, pinion_face = face_widths(params)

    # Only the narrower face is ever in contact, so an uneven pair says both numbers
    # and which one is doing the work.
    if pinion_face == gear_face:
        thickness_text = f"{_fmt(gear_face)}mm thick"
    else:
        thickness_text = (
            f"gear {_fmt(gear_face)}mm thick and pinion {_fmt(pinion_face)}mm, "
            f"meeting over {_fmt(min(gear_face, pinion_face))}mm of face"
        )

    text = (
        f"Module {_fmt(params.module)}mm, {_fmt(params.pressure_angle)} degree "
        f"pressure angle, {_fmt(params.backlash)}mm backlash, "
        f"{thickness_text}. "
        f"Gear {params.gear_teeth} teeth, {_fmt(gear_od)}mm outside diameter"
        f"{_bore_phrase(params.gear_bore)}. "
        f"Pinion {params.pinion_teeth} teeth, {_fmt(pinion_od)}mm outside diameter"
        f"{_bore_phrase(params.pinion_bore)}"
        f"{_counterbore_phrase(params.pinion_counterbore)}. "
        f"{_hub_phrase(params.gear_hub, params.gear_bore)}"
        f"Mount the axles {_fmt(spacing)}mm apart. "
        f"Ratio {drive:.3f}:1, so the pinion turns {drive:.3f} times per shaft turn."
    )

    counts = _ENCODER_COUNTS * drive
    text += (
        f" On a 12-bit magnetic encoder that is {360 / counts:.4f} degrees of shaft "
        f"per count, against {360 / _ENCODER_COUNTS:.4f} degrees geared 1:1."
    )

    limit = undercut_limit(params.pressure_angle)
    undercut = [
        name
        for name, teeth in (("gear", params.gear_teeth), ("pinion", params.pinion_teeth))
        if teeth < limit
    ]
    if undercut:
        text += (
            f" The {' and '.join(undercut)} "
            f"{'is' if len(undercut) == 1 else 'are'} below the {limit}-tooth "
            f"undercut limit at this pressure angle, so the tooth roots are thinned. "
            f"It runs, but it is the weak point."
        )
    return text


def _counterbore_phrase(spec: "CounterboreSpec | None") -> str:
    """Names the seat and which face it opens on — you cannot see that on a plate."""
    if spec is None:
        return ""
    return (
        f", with a {_fmt(spec.diameter)}mm x {_fmt(spec.depth)}mm deep seat in one "
        f"face for a magnet"
    )


def _hub_phrase(spec: "ClampHubSpec | None", bore: float) -> str:
    """The hub, and the one assembly instruction that is not visible on the part.

    Which way round the gear goes on the shaft is not a dimension and not a shape —
    it is the difference between a clamp that works and an ear that hits the pinion
    once a revolution.
    """
    if spec is None:
        return ""

    if spec.style == "grub":
        screws = "screw" if spec.grub_count == 1 else "screws"
        spacing = (
            "" if spec.grub_count == 1 else f" {_fmt(spec.grub_spacing)} degrees apart"
        )
        wall = (spec.diameter - bore) / 2
        hole = (
            f"tapped {_fmt(spec.grub_diameter)}mm"
            if spec.grub_taper <= 0
            else (
                f"tapered {_fmt(spec.grub_diameter + spec.grub_taper)}mm at the face "
                f"down to {_fmt(spec.grub_diameter)}mm where it meets the shaft"
            )
        )
        return (
            f"The gear has a {_fmt(spec.diameter)}mm x {_fmt(spec.length)}mm hub "
            f"gripped by {spec.grub_count} grub {screws}{spacing}, {hole} through "
            f"{wall:.1f}mm of wall. Nothing reaches past the hub, so it fits either "
            f"way round. Grub screws mark the shaft. "
        )

    fastening = (
        f"{_fmt(spec.screw_diameter)}mm screw"
        if spec.nut_across_flats <= 0
        else (
            f"{_fmt(spec.screw_diameter)}mm screw against a "
            f"{_fmt(spec.nut_across_flats)}mm nut, dropped in from the top"
        )
    )
    return (
        f"The gear has a {_fmt(spec.diameter)}mm x {_fmt(spec.length)}mm clamp hub, "
        f"slit through to the bore and pinched by a {fastening}. "
        f"Fit it with the hub facing AWAY from the pinion: the ear reaches past the "
        f"teeth and clears the pinion only by sitting further along the shaft. "
    )


def _bore_phrase(bore: float) -> str:
    """', Nmm bore' or nothing. Solid is worth saying out loud, hence the wording."""
    return f", {_fmt(bore)}mm bore" if bore > 0 else ", no bore (solid)"


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #

#: Centred in X and Y, sitting on Z=0 — print pose, like every other template here.
_ON_BED = (Align.CENTER, Align.CENTER, Align.MIN)


def _outline_points(
    module: float,
    teeth: int,
    pressure_angle: float,
    backlash: float,
    phase: float = 0.0,
) -> list[tuple[float, float]]:
    """The closed outline of one whole gear, tooth 0 centred on +X plus *phase*.

    ``phase`` is in radians and is baked into the points rather than left in the
    part's location. A location is not durable here: ``Shape.locate`` replaces one
    outright, so a pinion phased by rotating the solid would silently un-phase the
    first time anything downstream placed it somewhere. Points cannot be undone.

    Walks tooth by tooth: up one flank, across the tip, down the other flank, then
    around the root arc to the next tooth. Endpoints are emitted once — a repeated
    point is a zero-length edge and OCC refuses the wire outright.
    """
    alpha = math.radians(pressure_angle)
    r_pitch = pitch_radius(module, teeth)
    r_base = r_pitch * math.cos(alpha)
    r_tip = tip_radius(module, teeth)
    r_root = root_radius(module, teeth)

    half_at_pitch = (math.pi * module / 2 - backlash) / (2 * r_pitch)
    half_at_base = half_at_pitch + _involute(alpha)

    def half_width(radius: float) -> float:
        """Half the tooth's angular width at *radius*, from the tooth centreline."""
        return half_at_base - _involute(math.acos(min(1.0, r_base / radius)))

    # The involute only exists outside the base circle. Inside it, the flank is a
    # radial line down to the root, held at the base circle's angular width.
    r_flank_start = max(r_base, r_root)
    half_at_tip = half_width(r_tip)

    points: list[tuple[float, float]] = []

    def add(radius: float, angle: float) -> None:
        points.append((radius * math.cos(angle), radius * math.sin(angle)))

    for index in range(teeth):
        centre = phase + 2 * math.pi * index / teeth
        next_centre = phase + 2 * math.pi * (index + 1) / teeth

        if r_root < r_base:
            add(r_root, centre - half_at_base)
        for step in range(_FLANK_POINTS):
            radius = r_flank_start + (r_tip - r_flank_start) * step / (_FLANK_POINTS - 1)
            add(radius, centre - half_width(radius))
        for step in range(1, _TIP_POINTS):
            add(r_tip, centre - half_at_tip + 2 * half_at_tip * step / (_TIP_POINTS - 1))
        for step in range(_FLANK_POINTS - 2, -1, -1):
            radius = r_flank_start + (r_tip - r_flank_start) * step / (_FLANK_POINTS - 1)
            add(radius, centre + half_width(radius))
        if r_root < r_base:
            add(r_root, centre + half_at_base)

        # Root arc to the next tooth. Its far end is the next tooth's first point,
        # so it is left off here.
        arc_start = centre + half_at_base
        arc_end = next_centre - half_at_base
        for step in range(1, _ROOT_POINTS - 1):
            add(r_root, arc_start + (arc_end - arc_start) * step / (_ROOT_POINTS - 1))

    return points


def _along_x(part: Part, position: tuple[float, float, float]) -> Part:
    """Lay a Z-axis solid down along X and put it at *position*.

    The location is composed rather than set: ``locate`` would replace the rotation
    and leave the cutter pointing up the Z axis, where it would quietly miss.
    """
    return (Location(position) * Rotation(0, 90, 0)) * part


def _counterbore_cutter(spec: "CounterboreSpec", face_width: float) -> Part:
    """The magnet seat, cut down from the +Z face. Its floor stays inside the gear."""
    cutter = Cylinder(
        radius=spec.diameter / 2, height=spec.depth + _OVERCUT, align=_ON_BED
    )
    return cutter.locate(Location((0, 0, face_width - spec.depth)))


def _grub_hub_parts(
    spec: "ClampHubSpec",
    thickness: float,
    hub_radius: float,
    sink: float,
    bore: float = 0.0,
) -> tuple[Part, Part]:
    """A plain boss with set screws threaded radially through its wall.

    Nothing here reaches past ``hub_radius``, which is the entire point: a pinch
    ear does, and next to a pinion or a bracket that is a collision rather than a
    styling choice.

    ``bore`` is needed only by ``grub_taper``, which anchors its narrow end where
    the hole breaks into the bore. Without it the cone would have to be anchored
    somewhere arbitrary, and the grip diameter at the shaft would drift with the
    taper instead of staying at ``grub_diameter``.
    """
    hub = Cylinder(
        radius=hub_radius, height=spec.length + sink, align=_ON_BED
    ).locate(Location((0, 0, thickness - sink)))

    holes = None
    for index in range(spec.grub_count):
        # Drilled from outside the hub inward, stopping past the bore so the hole
        # actually breaks through to the shaft rather than bottoming in the wall.
        axis_z = thickness + spec.length / 2
        if spec.grub_taper > 0:
            # ONE cone through the whole wall — not a funnel sitting on a straight
            # hole, which is what a mouth chamfer gives you and which looks, on
            # screen and in the hand, like a wide opening above a parallel bore.
            #
            # Anchored at the two surfaces that mean something: exactly
            # grub_diameter where it breaks into the bore, opened by grub_taper at
            # the face. Anchoring anywhere else lets the taper quietly change the
            # diameter at the shaft, which is the one dimension here doing a job.
            inner = bore / 2
            wall = hub_radius - inner
            slope = (spec.grub_taper / 2) / wall  # radius gained per mm outward
            lo, hi = inner - _OVERCUT, hub_radius + _OVERCUT
            cutter = _along_x(
                Cone(
                    # Both ends run _OVERCUT past their surface along the same
                    # slope, so each breaks out on a real edge instead of leaving
                    # OCC to decide about a coincident one. Floored because a steep
                    # taper on a small hole can extrapolate through zero.
                    bottom_radius=max(
                        spec.grub_diameter / 2 - slope * _OVERCUT, _MIN_CUTTER_RADIUS
                    ),
                    top_radius=spec.grub_diameter / 2 + slope * (wall + _OVERCUT),
                    height=hi - lo,
                ),
                ((lo + hi) / 2, 0, axis_z),
            )
        else:
            reach = hub_radius + _OVERCUT
            cutter = _along_x(
                Cylinder(radius=spec.grub_diameter / 2, height=reach),
                (reach / 2, 0, axis_z),
            )
        turned = Rotation(0, 0, 90.0 + index * spec.grub_spacing) * cutter
        holes = turned if holes is None else holes + turned

    return hub, holes


def _clamp_hub_parts(
    spec: "ClampHubSpec", thickness: float, teeth: int, bore: float = 0.0
) -> tuple[Part, Part]:
    """(material to add, material to remove) for a clamp hub on the +Z face.

    Returned as a pair rather than applied here so the caller can add every solid
    before cutting every hole — otherwise the ear's screw hole gets filled straight
    back in by the ear that is unioned after it.
    """
    hub_radius = spec.diameter / 2
    # Sunk into the gear so the union has no coincident face, capped at half the
    # disc so a hub on a thin gear cannot poke out of the bottom.
    sink = min(thickness / 2, _OVERCUT)
    ear_centre_y = hub_radius + spec.ear_depth / 2
    ear_centre_z = thickness + spec.length / 2

    # The hub may sink into the disc: it lives inside the root circle, where the
    # disc is solid rim. The EAR may not. It reaches past the tip circle, so a
    # sunk ear fills five tooth gaps over 60 degrees of arc — out at the radius the
    # pinion sweeps through. That is a crash on every revolution, and on screen it
    # looks like a slightly chunky hub.
    if spec.style == "grub":
        return _grub_hub_parts(spec, thickness, hub_radius, sink, bore)

    hub = Cylinder(
        radius=hub_radius, height=spec.length + sink, align=_ON_BED
    ).locate(Location((0, 0, thickness - sink)))
    ear = Box(
        spec.ear_width, spec.ear_depth, spec.length, align=_ON_BED
    ).locate(Location((0, ear_centre_y, thickness)))

    # Radial cut from the axis out past the ear, through disc and hub alike.
    reach = hub_radius + spec.ear_depth + _OVERCUT
    slit = Box(
        spec.slit_width, reach, thickness + spec.length + 2 * _OVERCUT, align=_ON_BED
    ).locate(Location((0, reach / 2, -_OVERCUT)))

    holes = _along_x(
        Cylinder(radius=spec.screw_diameter / 2, height=spec.ear_width + 2 * _OVERCUT),
        (0, ear_centre_y, ear_centre_z),
    )

    if spec.nut_across_flats > 0 and spec.nut_depth > 0:
        # Hex pocket on the screw axis, plus a channel straight up to the ear's top
        # face. The channel is what makes this printable: a closed pocket would
        # need a ceiling bridged over thin air in the middle of a small part, and
        # a drooped nut trap does not take a nut. Open at the top, there is no
        # ceiling at all — and the nut drops in from above rather than being
        # pressed into a slot it cannot reach.
        pocket = extrude(
            RegularPolygon(
                radius=spec.nut_across_flats / 2, side_count=6, major_radius=False
            ),
            amount=spec.nut_depth + _OVERCUT,
        )
        holes = holes + _along_x(
            pocket, (-spec.ear_width / 2 - _OVERCUT, ear_centre_y, ear_centre_z)
        )
        channel = Box(
            spec.nut_depth + _OVERCUT,
            spec.nut_across_flats,
            spec.length / 2 + _OVERCUT,
            align=_ON_BED,
        )
        holes = holes + channel.locate(
            Location(
                (
                    -spec.ear_width / 2 + (spec.nut_depth - _OVERCUT) / 2,
                    ear_centre_y,
                    ear_centre_z,
                )
            )
        )

    # The slit and ear are drawn along +Y, which is already 90 degrees round, so
    # the turn is the *difference* to the gap angle. Rotating by the full angle
    # lands them at 90 + angle, and on a 30-tooth gear that is 180 degrees — a
    # tooth centre, split straight down the middle.
    turn = Rotation(0, 0, hub_angle(teeth) - 90.0)
    # hub + ear joined first, so the ear reaches the gear through the hub's overlap
    # rather than only through its own coplanar bottom face.
    return turn * (hub + ear), turn * (slit + holes)


def _one_gear(
    module: float,
    teeth: int,
    thickness: float,
    bore: float,
    pressure_angle: float,
    backlash: float,
    phase: float = 0.0,
    counterbore: "CounterboreSpec | None" = None,
    hub: "ClampHubSpec | None" = None,
) -> Part:
    """One gear, flat on the bed, tooth 0 centred on +X plus *phase*, bore drilled."""
    outline = Polyline(
        *_outline_points(module, teeth, pressure_angle, backlash, phase), close=True
    )
    gear = extrude(make_face(outline), amount=thickness)

    # Every solid first, then every hole. A hole cut before the ear is unioned on
    # would be filled straight back in, and the part would look right on screen.
    cutters = []
    if hub is not None:
        added, removed = _clamp_hub_parts(hub, thickness, teeth, bore)
        gear = gear + added
        cutters.append(removed)

    if bore > 0:
        # Overshoots both faces so neither boolean face is coincident with one that
        # is already there — the same trick the box's cavity uses.
        height = thickness + (hub.length if hub is not None else 0.0)
        cutter = Cylinder(radius=bore / 2, height=height + 2 * _OVERCUT, align=_ON_BED)
        cutters.append(cutter.locate(Location((0, 0, -_OVERCUT))))

    if counterbore is not None:
        cutters.append(_counterbore_cutter(counterbore, thickness))

    for cutter in cutters:
        gear = gear - cutter
    return gear


def gear_pair(
    module: float,
    gear_teeth: int,
    pinion_teeth: int,
    thickness: float,
    gear_bore: float = 0.0,
    pinion_bore: float = 0.0,
    pressure_angle: float = 20.0,
    backlash: float = 0.15,
    pinion_thickness: float | None = None,
    pinion_counterbore: "CounterboreSpec | None" = None,
    gear_hub: "ClampHubSpec | None" = None,
) -> tuple[Part, Part]:
    """Build a spur gear and the pinion that meshes with it.

    All dimensions are in millimetres. Both parts sit on the Z=0 plane in print
    orientation — flat, which is how a gear must print: teeth built up in layers
    around the axis, never bridged across it.

    The pinion comes back already rotated to mesh, so placing it at
    ``(centre_distance(params), 0)`` engages the teeth without further phasing.

    Returns ``(gear, pinion)``.

    Raises:
        TemplateError: the parameters cannot produce valid geometry.
    """
    _validate(
        module,
        gear_teeth,
        pinion_teeth,
        thickness,
        gear_bore,
        pinion_bore,
        pressure_angle,
        backlash,
        pinion_thickness,
        pinion_counterbore,
        gear_hub,
    )

    gear = _one_gear(
        module,
        gear_teeth,
        thickness,
        gear_bore,
        pressure_angle,
        backlash,
        hub=gear_hub,
    )
    pinion = _one_gear(
        module,
        pinion_teeth,
        thickness if pinion_thickness is None else pinion_thickness,
        pinion_bore,
        pressure_angle,
        backlash,
        phase=math.radians(mesh_rotation(pinion_teeth)),
        counterbore=pinion_counterbore,
    )
    return gear, pinion


class GearFitTrialParams(BaseModel):
    """Three of the same gear, bored three different sizes, to find what fits.

    For the case where the thing a gear has to grip cannot be measured properly —
    a moulded knob, a taper, a worn shaft. Printing three and trying them costs one
    plate and one bed check; discovering the bore is wrong costs a print each time,
    which is how this template came to exist.

    Tooth geometry is shared, so whichever one fits *is* the gear: it meshes with
    the pinion the gear pair already made, at the same centre distance.
    """

    model_config = ConfigDict(extra="forbid")

    module: float = Field(gt=0, description="Tooth size in mm. Match the pinion.")
    teeth: int = Field(
        ge=_MIN_TEETH, description="Tooth count. Match whatever the pair was designed to."
    )
    thickness: float = Field(gt=0, description="Face width in mm.")
    bores: list[float] = Field(
        min_length=len(TRIAL_PART_NAMES),
        max_length=len(TRIAL_PART_NAMES),
        description=(
            f"Exactly {len(TRIAL_PART_NAMES)} candidate bore diameters in mm, one "
            f"per printed gear. Spread them by more than the printer's own error — "
            f"0.5mm apart tells you something, 0.1mm apart tells you about your "
            f"printer."
        ),
    )
    pressure_angle: float = Field(
        default=20.0, ge=14.5, le=30.0, description="Flank angle in degrees."
    )
    backlash: float = Field(
        default=0.15, ge=0, description="Play between teeth in mm at the pitch circle."
    )
    hub: ClampHubSpec | None = Field(
        default=None,
        description=(
            "A clamp hub on every trial gear, so the trial tests the part you will "
            "actually fit rather than a plain ring. Checked against each bore in "
            "turn: a wall thick enough at the smallest bore can be too thin at the "
            "largest."
        ),
    )

    @model_validator(mode="after")
    def _check_geometry(self) -> "GearFitTrialParams":
        land = _tip_land(self.module, self.teeth, self.pressure_angle, self.backlash)
        if land < _MIN_TIP_LAND:
            raise TemplateError(
                f"teeth run to a point: {self.teeth} teeth at module {self.module}mm "
                f"leaves a {land:.3f}mm tip, under the {_MIN_TIP_LAND}mm minimum"
            )

        r_root = root_radius(self.module, self.teeth)
        for index, bore in enumerate(self.bores, start=1):
            if bore <= 0:
                raise TemplateError(f"bores[{index - 1}]={bore}mm must be positive")
            if bore / 2 + _MIN_RIM > r_root:
                raise TemplateError(
                    f"bores[{index - 1}]={bore}mm leaves {r_root - bore / 2:.2f}mm of "
                    f"rim under the tooth roots, under the {_MIN_RIM}mm minimum — the "
                    f"roots sit at {2 * r_root:.2f}mm diameter, so the widest bore "
                    f"worth trying is {2 * (r_root - _MIN_RIM):.2f}mm"
                )

        # Every bore, not just one: the hub's wall is measured from the bore, so a
        # trial that spans 6 to 8mm can be comfortable at one end and too thin at
        # the other, and only the failing one would show it.
        for bore in self.bores:
            _validate_clamp_hub(self.hub, bore, self.module, self.teeth)

        if len(set(self.bores)) != len(self.bores):
            raise TemplateError(
                f"bores={self.bores} repeats a size, so the trial has fewer "
                f"candidates than parts and you cannot tell the prints apart"
            )
        return self


def trial_spec_sentence(params: GearFitTrialParams) -> str:
    """Read-back for a fit trial. Says how to tell the prints apart afterwards."""
    listed = ", ".join(f"{_fmt(bore)}mm" for bore in params.bores)
    outside = 2 * tip_radius(params.module, params.teeth)
    return (
        f"{len(params.bores)} trial gears, {params.teeth} teeth at module "
        f"{_fmt(params.module)}mm, {_fmt(outside)}mm outside diameter, "
        f"{_fmt(params.thickness)}mm thick. Bores: {listed}. "
        f"They are identical apart from the hole, so measure before you mix them "
        f"up — on the plate they run smallest bore first, left to right. "
        f"Tooth geometry is unchanged, so whichever one fits meshes with the pinion "
        f"from the pair at its own centre distance. "
        # The widest bore leaves the thinnest wall, and that is the one that decides
        # whether a grub screw has anything to thread into.
        f"{_hub_phrase(params.hub, max(params.bores))}"
    )


def gear_fit_trial(params: GearFitTrialParams) -> tuple[Part, ...]:
    """Build one gear per candidate bore, smallest first."""
    return tuple(
        _one_gear(
            params.module,
            params.teeth,
            params.thickness,
            bore,
            params.pressure_angle,
            params.backlash,
            hub=params.hub,
        )
        for bore in sorted(params.bores)
    )


def trial_inner_dims(params: GearFitTrialParams) -> None:
    """A gear has no interior."""
    return None


def build_trial(params: GearFitTrialParams) -> tuple[Part, ...]:
    """Registry entry point for the fit trial."""
    return gear_fit_trial(params)


def build(params: GearPairParams) -> tuple[Part, Part]:
    """Registry entry point: build from a validated params model."""
    return gear_pair(
        module=params.module,
        gear_teeth=params.gear_teeth,
        pinion_teeth=params.pinion_teeth,
        thickness=params.thickness,
        gear_bore=params.gear_bore,
        pinion_bore=params.pinion_bore,
        pressure_angle=params.pressure_angle,
        backlash=params.backlash,
        pinion_thickness=params.pinion_thickness,
        pinion_counterbore=params.pinion_counterbore,
        gear_hub=params.gear_hub,
    )
