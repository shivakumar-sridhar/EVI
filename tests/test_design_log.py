"""Tests for the design ledger.

The ledger's whole claim is that a later session can rebuild a part from it rather
than measuring the mesh. So the load-bearing test is not that a line was written —
it is that what was written *round-trips into the same solid*. Everything else here
protects that property.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from evee.design_log import (
    fingerprint,
    history,
    latest,
    names,
    record_design,
    slug,
)
from evee.templates.gear import ClampHubSpec
from evee.templates.mount import ShaftSensorMountParams, shaft_sensor_mount

MOUNT = dict(
    bore=6.0,
    hub={"style": "grub", "diameter": 14.0, "length": 10.0},
    board_length=25.4,
    board_width=22.86,
    hole_spacing_tangential=20.32,
    hole_spacing_radial=17.78,
)


def resolved(**overrides) -> dict:
    """The resolved parameter dump, which is what the ledger is supposed to hold."""
    return ShaftSensorMountParams(**{**MOUNT, **overrides}).model_dump()


def typed(**overrides) -> dict:
    """Only what was passed — the dump that is guaranteed to validate back."""
    return ShaftSensorMountParams(**{**MOUNT, **overrides}).model_dump(
        exclude_defaults=True
    )


# --------------------------------------------------------------------------- #
# The property the ledger exists for
# --------------------------------------------------------------------------- #


def test_a_recorded_design_rebuilds_into_the_same_solid():
    """The one test that matters.

    A ledger that stores a readable summary of a part is a ledger that cannot
    rebuild it. What goes in has to be the resolved parameter set, complete enough
    to feed straight back into the params model and get the same geometry out.
    """
    params = resolved()
    (original,) = shaft_sensor_mount(ShaftSensorMountParams(**MOUNT))

    record_design(
        template="shaft_sensor_mount",
        params=params,
        params_input=typed(),
        spec_sentence="whatever the read-back said",
        name="encoder mount",
    )

    stored = latest("encoder mount")
    (rebuilt,) = shaft_sensor_mount(ShaftSensorMountParams(**stored.params_input))

    assert rebuilt.volume == pytest.approx(original.volume, rel=1e-9)
    assert rebuilt.bounding_box().size.X == pytest.approx(
        original.bounding_box().size.X, rel=1e-9
    )


def test_the_resolved_dump_alone_would_not_rebuild_anything():
    """Why the ledger keeps two parameter sets instead of the obvious one.

    A full ``model_dump`` marks every field as explicitly set, and ClampHubSpec
    refuses pinch-only fields on a grub hub *because they were set* — deliberately,
    so a nut trap someone asked for is never silently dropped. The consequence is
    that the model declines to accept its own dump, and a ledger holding only that
    would look complete and rebuild nothing.

    Pinning it here so the day someone "simplifies" the ledger down to one field,
    this fails and says why rather than surfacing as a broken history.
    """
    with pytest.raises(ValidationError, match="does not use"):
        ShaftSensorMountParams(**resolved())

    # The typed-only dump does round-trip, which is what params_input holds.
    assert ShaftSensorMountParams(**typed())


def test_both_parameter_sets_are_stored():
    """One to read, one to rebuild from. Losing either loses a property."""
    record_design(
        template="shaft_sensor_mount",
        params=resolved(),
        params_input=typed(),
        spec_sentence="",
        name="encoder mount",
    )
    stored = latest("encoder mount")

    # The resolved set carries values nobody typed...
    assert stored.params["platform_thickness"] == 3.0
    # ...and the input set carries only what was.
    assert "platform_thickness" not in stored.params_input
    assert stored.params_input["bore"] == 6.0


def test_a_ledger_written_before_params_input_existed_still_loads():
    """Old lines have no params_input. They fall back to the resolved set rather
    than hydrating as None and blowing up the first read after an upgrade."""
    record_design(
        template="shaft_sensor_mount", params=typed(), spec_sentence="", name="old"
    )
    assert latest("old").params_input == latest("old").params


def test_the_ledger_holds_defaults_that_were_never_typed():
    """The failure mode that motivated recording resolved params rather than the
    call's arguments: a mesh shows a default's consequence and never the fact that
    nobody chose it, so an omitted value has to be in the file explicitly."""
    record_design(
        template="shaft_sensor_mount",
        params=resolved(),
        spec_sentence="",
        name="encoder mount",
    )
    stored = latest("encoder mount").params

    # None of these were passed in MOUNT.
    assert stored["platform_thickness"] == 3.0
    assert stored["hole_diameter"] == 2.7
    assert stored["margin"] == 2.0
    assert stored["hub"]["grub_diameter"] == 2.5


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #


def test_versions_count_up_under_one_name():
    for bore in (6.0, 6.2, 6.4):
        record_design(
            template="shaft_sensor_mount",
            params=resolved(bore=bore),
            spec_sentence="",
            name="encoder mount",
        )
    versions = history(name="encoder mount")
    assert [r.version for r in versions] == [3, 2, 1]
    assert [r.params["bore"] for r in versions] == [6.4, 6.2, 6.0]


def test_an_unchanged_rerun_does_not_mint_a_version():
    """The design gate is an iteration loop by construction — the tool description
    tells the client to call it again — so counting calls would number the same
    shape v1 through v9 and bury the versions that actually differ."""
    first = record_design(
        template="shaft_sensor_mount", params=resolved(), spec_sentence="", name="mount"
    )
    again = record_design(
        template="shaft_sensor_mount", params=resolved(), spec_sentence="", name="mount"
    )

    assert first.created is True
    assert again.created is False
    assert again.record.version == 1
    assert len(history(name="mount")) == 1


def test_returning_to_an_earlier_shape_is_a_new_version():
    """Only the immediately preceding version is compared. Going back to a shape you
    tried before is a real design event: something was learned in between, and the
    ledger is a history of what happened, not a set of distinct shapes."""
    record_design(template="shaft_sensor_mount", params=resolved(bore=6.0),
                  spec_sentence="", name="mount")
    record_design(template="shaft_sensor_mount", params=resolved(bore=7.0),
                  spec_sentence="", name="mount")
    back = record_design(template="shaft_sensor_mount", params=resolved(bore=6.0),
                         spec_sentence="", name="mount")

    assert back.created is True
    assert back.record.version == 3
    assert [r.params["bore"] for r in history(name="mount")] == [6.0, 7.0, 6.0]


def test_two_parts_from_one_template_do_not_become_versions_of_each_other():
    """The reason a name exists at all. Grouping by template would file an unrelated
    mount as v2 of the encoder one, and a later 'what did we do last time' would
    answer with somebody else's part."""
    record_design(template="shaft_sensor_mount", params=resolved(),
                  spec_sentence="", name="encoder mount")
    record_design(template="shaft_sensor_mount", params=resolved(bore=8.0),
                  spec_sentence="", name="spare mount")

    assert set(names()) == {"encoder-mount", "spare-mount"}
    assert latest("encoder mount").params["bore"] == 6.0
    assert latest("spare mount").params["bore"] == 8.0
    assert latest("encoder mount").version == 1
    assert latest("spare mount").version == 1


def test_a_missing_name_falls_back_to_the_template():
    record_design(template="shaft_sensor_mount", params=resolved(), spec_sentence="")
    assert latest("shaft_sensor_mount").name == "shaft-sensor-mount"


# --------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "given, expected",
    [
        ("encoder mount", "encoder-mount"),
        ("Encoder Mount", "encoder-mount"),
        ("encoder_mount", "encoder-mount"),
        ("  encoder   mount  ", "encoder-mount"),
        ("shaft_sensor_mount", "shaft-sensor-mount"),
        ("mount v2", "mount-v2"),
    ],
)
def test_names_reduce_to_one_key(given, expected):
    """'Encoder Mount' and 'encoder mount' are the same part. Anything else means
    typing it differently silently starts a second history."""
    assert slug(given) == expected


def test_a_name_that_reduces_to_nothing_is_refused():
    """Refused rather than sanitised into something unrecognisable: a name that
    cannot survive slugging is one the owner should be told about, not one they
    should meet later as a mystery second part."""
    with pytest.raises(ValueError, match="does not reduce"):
        slug("!!!")


# --------------------------------------------------------------------------- #
# Robustness — the ledger must never take a design down with it
# --------------------------------------------------------------------------- #


def test_a_corrupt_line_does_not_hide_the_rest(isolated_design_log):
    record_design(template="shaft_sensor_mount", params=resolved(),
                  spec_sentence="", name="mount")
    with isolated_design_log.open("a", encoding="utf-8") as fh:
        fh.write("{ this is not json\n\n")
    record_design(template="shaft_sensor_mount", params=resolved(bore=7.0),
                  spec_sentence="", name="mount")

    assert [r.version for r in history(name="mount")] == [2, 1]


def test_an_unwritable_ledger_returns_none_rather_than_raising(
    monkeypatch, isolated_design_log
):
    """The parts are already exported and correct by the time this is called. A lost
    line of bookkeeping is not worth failing them over — the same rule the print log
    follows for an approved print."""
    monkeypatch.setattr(
        "evee.design_log.DESIGN_LOG",
        isolated_design_log.parent / "nope" / "x" / "log.jsonl",
    )
    monkeypatch.setattr(
        "pathlib.Path.mkdir",
        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")),
    )
    assert record_design(
        template="shaft_sensor_mount", params=resolved(), spec_sentence=""
    ) is None


def test_history_of_an_unknown_part_is_empty_not_an_error():
    """Silence, and the tool description says to read it as silence: a part designed
    before the ledger existed is absent from it and was still made."""
    assert history(name="never designed") == []
    assert latest("never designed") is None


def test_the_ledger_is_json_lines_on_disk(isolated_design_log):
    """Same shape as print_log.jsonl, and for the same reason: appendable without
    rewriting, and survivable one bad line at a time."""
    record_design(template="shaft_sensor_mount", params=resolved(),
                  spec_sentence="read back", name="mount")
    lines = isolated_design_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["name"] == "mount"
    assert entry["version"] == 1
    assert entry["source"] == "design_part"
    assert entry["params"]["bore"] == 6.0


def test_a_bad_source_is_a_programming_error_not_a_silent_write():
    """Unlike an unwritable disk, this one is the caller's mistake and reaching the
    ledger with an unknown provenance would make 'reconstructed' meaningless."""
    with pytest.raises(ValueError, match="source must be"):
        record_design(
            template="shaft_sensor_mount",
            params=resolved(),
            spec_sentence="",
            source="guessed",
        )


def test_reconstructed_records_are_marked_as_such():
    """A measured-off-a-mesh record and a captured one must not look alike: one is
    as good as its measurement and the other is exact."""
    record_design(
        template="shaft_sensor_mount",
        params=resolved(),
        spec_sentence="",
        name="encoder mount",
        source="reconstructed",
        note="recovered from the printed STL; volume matched to 0.016%",
    )
    stored = latest("encoder mount")
    assert stored.source == "reconstructed"
    assert "0.016%" in stored.note
    assert "[reconstructed]" in stored.summary()


def test_the_fingerprint_ignores_key_order():
    a = {"bore": 6.0, "hub": {"diameter": 14.0, "length": 10.0}}
    b = {"hub": {"length": 10.0, "diameter": 14.0}, "bore": 6.0}
    assert fingerprint(a) == fingerprint(b)


def test_a_hub_spec_object_survives_the_round_trip():
    """params arrive as a model dump, and a nested model that serialised to
    something json could not write would take the whole record with it."""
    params = ShaftSensorMountParams(
        **{**MOUNT, "hub": ClampHubSpec(style="grub", diameter=14.0, length=10.0)}
    ).model_dump()
    record_design(template="shaft_sensor_mount", params=params,
                  spec_sentence="", name="mount")
    assert latest("mount").params["hub"]["style"] == "grub"
