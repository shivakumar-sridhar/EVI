# EVEE — Everyday Virtual Engineering Engine

Natural language → parametric CAD → sliced G-code → Ender-3 V3 SE print, via MCP tools.

**This is one person's workflow, published so you can clone it and change it.** It is
not a product trying to fit every setup. The machine is a specific Ender-3 V3 SE, the
profile is hand-tuned for that machine, and the numbers in `config/` were arrived at by
printing things and looking at them. Fork it, point it at your printer, re-verify.

**The MCP server is the product; the model is bring-your-own.** Any MCP client drives it
— Claude Code, OpenCode, Cline, Zed. Speech, if you want it, comes from the client
(`/voice` in Claude Code); there is nothing in this repo that listens.

## Stack
build123d (CAD) · PrusaSlicer CLI (slicing) · OctoPrint REST (printer) · MCP stdio server

## This file is not a control

**Only Claude Code loads `CLAUDE.md`.** An OpenCode or Cline user gets none of it. So a
safety rule that lives only here does not exist for most users of this project.

Every constraint below must be **enforced in Python** (`server.py` or the module it
wraps) and **communicated in the MCP tool description**, which ships with the server and
every client reads. Treat this file as developer context, never as the mechanism.

If you find yourself relying on a rule here to keep something safe, that is a bug in
`server.py`.

## Non-negotiable safety rules
1. `start_print()` requires explicit `bed_confirmed_clear=True`. Never default it. No agent can clear a bed. It must never be inferred from a voice transcript — a human supplies it through a non-voice channel.
2. Never chain design → slice → print without human approval between each step. The server exposes no tool that combines them.
3. Never use OctoPrint's `print=true` upload flag. The upload and the start stay two separate REST requests, with `print=false` on the upload. **They are driven by one MCP tool** — `start_print(gcode_path, bed_confirmed_clear)` uploads then starts.
4. Never suggest starting a print when the user is asleep or away from the building.
5. `start_print` names a file, and the server proves the printer selected that file before starting. OctoPrint's start command carries no filename — see `printer.py`.

All five are Python refusals in `src/evee/printer.py` and are restated in the MCP tool
descriptions. If you ever find one holding only because of this file, that is the bug.

## House defaults (`config/defaults.toml` is the source of truth)
- Wall thickness 2.0mm · press-fit clearance 0.25mm (0.2 snug / 0.3 easy) · edge fillet 1.0mm
- Dimensions in the user's request are OUTER unless stated otherwise

## CAD conventions
- build123d, not FreeCAD's API or CadQuery
- Prefer filling a template in `src/evee/templates/` over writing new geometry. If none
  fits, say so and propose adding one — do not improvise geometry
- Always report resolved inner dimensions alongside outer

## Slicing
- Only `config/ender3_v3se.ini`. Hand-tuned. Do not generate slicer configs.
- `slice_part` exposes **no profile parameter** — that is the enforcement, not this bullet.
  No levelling parameter either: whether the stored mesh is used is *state*, decided by
  `calibration.mesh_state()`, never a knob a client can turn.
- The normal input is the plate, arranged by `cad.arrange_along_x` — the same function
  the review 3MF uses, and that sharing is the preview-equals-print guarantee. **Never**
  hand PrusaSlicer separate STLs with `--merge`: it would arrange them itself and the
  guarantee would die with nothing failing to say so.

## Testing
- `pytest tests/` — 532 tests. Geometry tests assert on volume and bounding box, never
  exact meshes.
- **Never test by starting a real print.** Use `slice_part` metadata. Refusal paths *are*
  testable live — they refuse. The upload leg is testable live only below MCP: call
  `OctoPrintClient.upload_gcode` directly, since `start_print` would go on to print.
- **No test may touch the real printer by accident.** `tests/conftest.py` repoints
  credentials at `printer.invalid` suite-wide, and patches `evee.printer`, not
  `evee.config` — the module binds the name at import, so patching the definition site
  silently does nothing and the tests would hit the real Ender.
- `conftest` also isolates `evee.calibration.MESH_STATE`. A test that left a state file
  behind would make the next *real* slice emit `M420 S1` for a mesh never stored.

---

## Current state

Eight MCP tools, three human gates. Everything below works and is verified live.

| Area | Module | Status |
|---|---|---|
| Templates | `templates/box.py` | `box_with_lid` + `ports`, `standoffs`, `lid_posts`, `floor_holes`, `floor_slots`. Printed and fitted. |
| Templates | `templates/gear.py` | `gear_pair` + `pinion_counterbore`, `gear_hub` (pinch or grub), and `gear_fit_trial`. Pair printed; mesh not yet checked in the hand. |
| Templates | `templates/yoke.py` | `encoder_yoke`. Geometry verified in tests; **not yet printed.** |
| Templates | `templates/magnet_encoder_case.py` | `magnet_encoder_case` — AS5600 inverted over a magnet glued to a hinge. Geometry verified in tests; **awaiting real measurements, not yet printed.** |
| Templates | `templates/mount.py` | `shaft_sensor_mount` — IMU on the shaft, the MVP route. Printed twice 2026-08-23, both finished. |
| Geometry, plates, previews | `cad.py` | Done |
| Slicing | `slicer.py` | Done. Case body: 55 layers, 4.28 g, 30m 16s |
| Review windows | `viewer.py` | Done. Gate 1 on by default, Gate 2 opt-in |
| Printer control | `printer.py`, `calibration.py` | Done. Real prints started, cancelled, calibrated |
| MCP server | `server.py` | Done. Verified against a real client over a subprocess |
| Design ledger | `design_log.py` | Done. `design_history` tool; `encoder-mount` v1/v2 backfilled |
| Notifications | `notify.py` | Done. Separate process, systemd unit in `packaging/` |

Removed: the voice frontend (`src/evee/voice/`). It worked; the client does it better.

Next: documentation pass and a demo recording. `BUILD_PLAN.md` is kept as the historical
build plan — where it disagrees with this file, this file wins.

---

## The machine and its profile

**Creality Ender-3 V3 SE**, not a classic Ender 3 — confirmed from the
`TARGET_MACHINE.NAME` header of the first verified print. CR Touch auto-levelling,
different mainboard, higher stock accelerations, non-interchangeable start G-code.
Bed 220×220. `config.bed_extents()` reads `bed_shape` and `max_print_height` out of the
profile — never a `220` literal — and `slice_stl` refuses an oversized part before the
slicer runs, naming the axis and the overshoot.

### The start sequence, and the four attempts it took

The first-layer bug: filament drooled during heat-up, welded to the nozzle, and got
dragged into the first layer. Three fixes shipped before the cause was found, each
reasoned from the geometry of the start block, each costing a cancelled print:

1. `Z50` → `Z2.0` — stopped strings falling, started welding a blob.
2. Park at `Y150`, purge deliberately, draw prime lines one-way — blob still stuck.
3. Cold nozzle + stored mesh — removed ~187s of soak, blob still stuck.

The observation that ended it came from watching the machine: **drool starts at about
190C, the target was 210, and the lag across that gap is the drool.** Not the probe, not
the park position, not the soak. `reference/cura_BNO_Case.gcode` — the clean Cura print
of this same part, on disk the whole time — differs in exactly the ways that matter:

| | Cura | Ours, before |
|---|---|---|
| First-layer temperature | **200C** | 210C |
| Nozzle while heating | **at the prime start, Z0.28** | parked away, Z2.0 |
| Moves between `M109` and first extrusion | **0** | 5 |
| Purge | 30mm | 24mm, thinner per mm |

Cura's trick is to be *already in position, at print height*, when the heat arrives. Ooze
is pinned to the plate exactly where the line starts, and the next command draws a fat
purge straight through it. There is no travel in which to collect a blob because there is
no travel. Fixes 1–3 all *added* movement between "hot" and "extruding". The current
profile deletes all of it and is a translation of Cura's block:

```gcode
M104 S0            ; cold through homing — nothing molten, nothing to weep
M140 S{bed}
G28
G29                ; rewritten to M420 S1 post-export when a mesh is stored
G1 Z2.0 F3000
G1 X2.0 Y20 Z0.28  ; AT the prime start, at print height, BEFORE heating
M190 S{bed}
M109 S200          ; heats in position — nothing moves after this
G1 X2.0 Y100 Z0.28 E15 F1500   ; draws immediately, and fat
G1 X2.3 Y100 Z0.28 F5000
G1 X2.3 Y20  Z0.28 E15 F1200
G92 E0 / G1 E-1 / G1 Z2.0 / G1 E1   ; retract, lift, restore
```

- `first_layer_temperature = 200` is **not a tuning knob**: 10C of overshoot past the
  drool threshold instead of 20, and 200 is what printed this part cleanly. `temperature`
  (205, later layers) is untouched — drool only matters before the first extrusion.
- `E15` twice, not Cura's `E15`/`E30`, because this profile runs `M83` relative. Same 30mm.
- `X2.0/2.3`, not Cura's `X-3/-2`, because `bed_shape` starts at 0 here.
- `Z0.28` for the prime, above the 0.2 first layer, so the purge is laid down rather than
  ploughed.
- **`tests/test_profile.py` guards all of it**, including that `Z50` and `G4 S30` never
  come back and that nothing moves between `M109` and the first extrusion.

Re-sliced across the change: 55 layers unchanged, 4.28 g (was 4.25 — the fatter prime
line), 30m 16s (was 30m 44s — the deleted travel). **Layers are the invariant to watch;
grams shift whenever `start_gcode` extrudes more.**

### Bed levelling — use the stored mesh

`calibrate_bed` probes once and saves with `G28`/`G29`/`M500`. `slicer._apply_stored_mesh`
then rewrites the one `G29` line to `M420 S1` on every slice, but only once a mesh has
demonstrably been stored. Verified by diff: 1 line of 24,810 changes, metadata identical.

- **The profile keeps `G29`**, which is always correct and is also the substitution
  anchor. `M420` never appears in the profile — a test enforces this.
- **Every uncertain case falls back to probing**: no state file, an unreadable one, one
  older than `[bed_mesh] max_age_days`, or an anchor missing or doubled.
- **That direction is the whole design.** `M420 S1` against a mesh Marlin does not have
  does not fail — it prints on a flat plane and says so only on the serial console.
  Slower and correct beats faster and silently wrong.
- **`output/bed_mesh.json` is a claim, not a reading.** OctoPrint cannot read a mesh back
  out of the printer. A firmware update or an `M502` invalidates it while the file still
  says "stored yesterday". The age limit and the `get_printer_status` prompt are the
  mitigations; the residual risk is real.
- Probing costs ~187 seconds — measured, not guessed: a plate print ran 3416s against a
  3229s estimate, and PrusaSlicer never counts `start_gcode`.

### Estimates

**PrusaSlicer's is accurate; OctoPrint's mid-print one is not.** A finished job came in
at 3416s against 3229s estimated — 5.8% over. But at 15% the web UI claimed 1h 41m
against a 53m estimate. `printTimeLeft` is byte-position based and useless before roughly
a third of the way in, because minutes of `G28`/`G29`/heating count as elapsed while
producing no progress. **Never re-tune the profile off a mid-print reading** — wait for
`print_time_seconds` on a completed job.

---

## Module notes

Each line is a thing that cost time to learn. None of them are obvious from the code.

### `slicer.py`, `viewer.py`, `cad.py`
- **PrusaSlicer's metadata is not at the end of the file.** The `filament used` /
  `estimated printing time` block sits *before* `; prusaslicer_config = begin`. A
  tail-reading parser finds nothing. `; filament used [g]` also appears a second time
  inside the config block with a different value — first match wins, not last.
- **There is no layer-count comment.** Counting `;LAYER_CHANGE` is the only way.
- **PrusaSlicer writes progress to stdout**, which under stdio transport is JSON-RPC.
  `capture_output=True` is load-bearing, not tidiness.
- **PrusaSlicer is the review app, never the slicer of record.** `--load {profile}` puts
  the verified profile in the GUI; the actual slice stays a headless `--export-gcode`,
  because a GUI export returns no metadata and would follow whatever the UI has loaded.
- **An MCP client scrubs the environment** — only `HOME`, `LOGNAME`, `PATH`, `SHELL`,
  `TERM`, `USER`. No `DISPLAY`, `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, `XAUTHORITY`.
  Reading `os.environ` reports "headless" on a desktop, so `discover_display()` finds the
  session's sockets on disk.
- **`XAUTHORITY` is required and its absence is silent.** f3d handed a `DISPLAY` it
  cannot authenticate against **exits 0 with no window and no error**, so a `DISPLAY`
  without a cookie is worse than none and discovery drops it.
- **Wayland alone does not work** with f3d — its VTK backend is X11-only. Both are
  advertised; XWayland does the work.
- **A viewer that cannot open must never fail a design or a slice.** `open_model` /
  `open_gcode` never raise; they return a reason and the tool reports it.
- **`--single-instance` was removed and must not come back.** It reuses the window but
  *appends* to its scene and never clears it — two designs left four objects stacked on
  the origin. It also made the process forward its arguments and exit, leaving no pid.
- **Each gate owns one window and replaces it**, recorded in
  `$XDG_RUNTIME_DIR/evee-viewer.json`. **Identity is the recorded argv, never the pid
  alone** — pids get reused, and closing a PrusaSlicer the human opened for their own work
  would be far worse than a stale window. Compare `/proc/<pid>/cmdline` before signalling.
- **Do not read `/proc/<pid>/cmdline` straight after spawning** — until `execve` lands it
  reads back the *parent's* argv.
- **PrusaSlicer's process is named `slic3r_main`**, not `prusa-slicer`. `pgrep -x
  prusa-slicer` finds nothing and will fool you.
- **Reap the viewers you spawn**, or each closed window lingers as a zombie under the
  long-lived server.
- **Both parts cannot be handed to the viewer as STLs** — each is centred on the origin in
  print pose, so they land in the same place and you see one shape where there are two.
  `cad.export_review_model()` writes a single `_review.3mf` with the parts spaced along X.
  Review-only; the STLs stay at the origin where a slicer needs them.
- **Gate 2's window is off by default** (`gcode_auto_open = false`). `auto_open` means
  "this machine has a screen"; `gcode_auto_open` means "I want to look at toolpaths". The
  refusal message names whichever one vetoed, so nobody edits the wrong line.

### `gear.py`
- **The tooth count is the one number a volume assertion cannot see.** A gear with
  the wrong count still has a plausible volume and a correct bounding box. Counting
  is done by intersecting a thin annulus at the pitch circle and asserting the number
  of *solids* that fall out — one island per tooth.
- **A mesh test is the only test that matters, and it is easy to write so it proves
  nothing.** Two gears sitting apart also fail to intersect. The test first asserts
  the tip circles overlap by >1mm, so a zero intersection means interleaved teeth
  with clearance rather than two parts that never met.
- **The mesh phase must be baked into the geometry, not the part's location.**
  `Shape.locate()` *replaces* a location, and `cad.arrange_along_x` moves every part
  before export — a pinion phased by rotating the solid reaches the plate un-phased,
  and nothing fails. The phase is an angular offset applied to the outline points.
- **Odd tooth counts mesh unaided; even ones need half a pitch.** Teeth centred at
  `2*pi*k/z` put a *space* at 180 degrees only when z is odd. Getting this backwards
  collides one entire tooth, so both parities are in the test matrix.
- **Order the validation so the root cause speaks first.** A backlash wider than the
  tooth also makes the teeth run to a point, and the pointed-tooth message fired
  first and sent you looking at tooth count. The backlash check now runs ahead of it.
- **The polyline root sits microns inside the true root circle.** Root arcs are
  sampled, so an exact containment assertion against `root_radius` fails by ~0.05mm3.
  Physically irrelevant at a 0.4mm nozzle; worth knowing before you debug it.
- **A feature that reaches past the tip circle must not sink into the toothed disc.**
  The clamp hub's ear is unioned with a 1mm sink, the way `box._standoff_post` sinks a
  post — and that sank a skirt through five tooth gaps out to r=17, in the exact band
  the pinion sweeps. It crashes into the pinion once a revolution and on screen it
  looks like a slightly chunky hub. The hub may sink (it lives inside the root circle,
  where the disc is solid rim); the ear may not. `test_the_hub_keeps_clear_of_the_band_
  the_pinion_sweeps` is the guard: no material outside the tip circle within the
  meshing band, full stop.
- **Geometry drawn along +Y is already 90 degrees round.** The clamp slit is placed at
  the tooth gap nearest +Y, and rotating it *by* that angle rather than by the
  difference put it at 180 degrees — a tooth centre on a 30-tooth gear, splitting a
  tooth down the middle. Counting islands at the pitch circle catches it: a split
  tooth is one island more than the gear has teeth.
- **A pinch ear does not fit next to anything.** It needs ~9mm of radial depth for an
  M3 and its nut, so it reaches well past the gear's own tip circle — fine on a bare
  shaft, fatal beside a bracket the gear has to turn next to. `ClampHubSpec.style="grub"`
  keeps every turning feature inside the hub. The guard is a test asserting no material
  outside the hub radius, above the teeth.
- **Refuse the other style's settings, never ignore them.** `model_fields_set` tells you
  what was actually typed, so a `nut_across_flats` passed to a grub hub is a refusal
  rather than a silently dropped field and a person certain they asked for a nut trap.
- **Check grub screw spacing at the bore, not at the outside.** That is where the holes
  are closest together; a pair that clears at the hub's OD can break into one slot on
  the inside. Three M3 screws at 90 degrees pass an outside check and fail a 5mm bore.
- **A nut trap wants an open top, not a closed pocket.** A pocket buried in the ear
  needs a ceiling bridged in mid-air on a small part, and a drooped trap will not take
  a nut. A hex pocket plus a channel to the top face has no ceiling anywhere, and the
  nut drops in instead of being pressed into a slot it cannot reach.
- **Counterbores open on +Z in print pose.** At the other end the step becomes a
  ceiling bridging the bore, and a magnet seat that droops holds the magnet crooked —
  which the AS5600 reports as a wobble in the angle rather than as a defect.
- **Two openings a tenth of a millimetre apart are one opening.** Floor features are
  checked for a real gap of `_MIN_FLOOR_HOLE_WALL`, not merely for non-overlap: a
  0.4mm nozzle cannot lay a 0.1mm rib, so the part that arrives is not the part that
  was checked. This caught a slot for the AS5600's STEMMA connectors leaving 0.098mm
  of floor beside the hex sockets — the two would have merged and the sockets would
  have stopped being sockets.
- **Clearance to a rectangle is measured to its nearest edge, not its centre.**
  `_slot_gap` clamps the point onto the rectangle first; measuring to the middle of a
  25mm slot calls everything beside it comfortably clear.
- **Undercut is reported, never refused.** Below `2/sin^2(a)` teeth the roots are
  thinned, and printed gears run undercut all the time. The read-back says so.

### `templates/__init__.py` — the registry reloads itself

An MCP server is a subprocess the editor starts once and keeps for hours, and Python
caches imported modules. Adding or editing a template was invisible until the client
reconnected — five times in one session before anyone called it a bug. `refresh()`
now stats the package's sources before every lookup and reloads what moved
(`[templates] autoreload` in defaults.toml turns it off). Three things it has to get
right, each of which failed first:

- **Reloading one submodule is not enough.** `TEMPLATE_REGISTRY` holds direct
  references to each params model and build function, captured when `__init__.py`
  last ran. Reload `gear.py` alone and the registry still points at the old classes.
  Any change reloads every template module *and then* this one.
- **Which rebinds `TEMPLATE_REGISTRY` to a new dict.** Anything that did
  `from evee.templates import TEMPLATE_REGISTRY` keeps the old one forever — the same
  shape of bug as patching a name at its definition site while the caller holds the
  value. Read it through `template_registry()`; `test_no_module_binds_the_registry_dict`
  enforces it by introspection.
- **`.pyc` validity is checked in whole seconds and file size.** Save a file twice
  inside one second without changing its length — rename a string, flip a digit — and
  `importlib.reload` reuses the previous bytecode, appearing to work while serving
  the older code. Our stamps are nanoseconds, so we see the change the loader cannot;
  `_drop_bytecode()` is what makes seeing it count. This was caught by an end-to-end
  check that left a poisoned `.pyc` in the tree proving it.

**`templates/errors.py` is never reloaded, and that is the whole reason it exists.**
`importlib.reload` mints new class objects, so an `except TemplateError` held from
before a reload does not catch what a freshly reloaded template raises: a tidy
validation message becomes an unhandled exception, on the first call after an edit
only. Adding the reload broke twenty tests exactly this way, every one of which
passed when run alone. Adding a class to `errors.py` needs a real restart — that is
the price of the classes being stable.

### `yoke.py`
- **Most of this part is defined by what it must not touch**, and keep-out shows up in
  no volume and no bounding box. The tests probe it directly: a cylinder on the gear's
  tip circle, through the whole band the gear could be mounted in, must intersect
  nothing. Paired with an inverse test — grow the keep-out slightly and material *must*
  appear — because a keep-out test passes trivially on a part that is simply too small.
- **The air gap is specified to the top of the chip package, not to the board.** An
  AS5600 in SOIC-8 stands ~1.75mm proud. Size the stack to the board face and the whole
  assembly sits 1.75mm low, which is the pinion touching the chip. `board_face()` owns
  that sum so it cannot be got wrong twice.
- **Clearance to a swept circle is radial, not along one axis.** The near columns are
  positioned by solving on the keep-out circle itself; adding the margin to X instead
  leaves the column's inner *corner* short of it by however far the corner is off-axis
  — 0.1mm here, and silently.
- **Which constraint binds moved when the shaft did.** At 10mm the board's edge against
  the turning shaft set the minimum centre distance; at 5mm it is the axle boss against
  the gear's teeth, and the old reasoning still reads plausibly. A test pins the boss as
  the limiter so the stale explanation cannot survive.
- **The collar on the shaft is the datum, and that is the whole design.** Centre distance
  becomes a dimension inside one printed part rather than a stack of mounting
  tolerances, and the anti-rotation tether then needs no precision at all.

### `design_log.py` — look it up, do not measure it

`output/design_log.jsonl` records every design: name, version, date, parameters,
read-back sentence, STL paths. `design_part(template, params, name=...)` writes it;
`design_history(name=..., template=...)` reads it. **Consult it before iterating on
anything that already exists** — "the mount", "make the holes bigger", "same but 8mm".

- **It exists because the alternative was measuring a mesh.** Every parameter of the
  encoder mount was recovered from its STL and confirmed by rebuilding to 0.016% of
  the original volume. That worked, and it is not a method. A parameter choosing
  between two equal-volume arrangements leaves no trace, and a defaulted value shows
  its consequence but never the fact that nobody typed it.
- **Two parameter sets are stored, and both are load-bearing.** `params` is the
  resolved dump — every value, defaults filled in — and is what to *read*.
  `params_input` is only what was passed, and is what to *rebuild from*. A full dump
  marks every field as set, and `ClampHubSpec` refuses pinch-only fields on a grub hub
  precisely *because they were set*, so **the model declines to accept its own dump.**
  Keeping both also makes default drift visible: rebuild from `params_input`, dump it,
  and any disagreement with `params` is a house default that has moved.
- **Versions group under a name, not a template.** Two unrelated mounts are two parts,
  not v1 and v2 of one, and no rule about which params "define" a part could separate
  them without guessing. Iterating means passing the *same* name; a new name starts a
  new history.
- **An unchanged re-run does not mint a version.** The design gate is an iteration
  loop by construction, so counting calls would number one shape v1–v9 and bury the
  versions that differ. Only the immediately preceding version is compared — going
  back to an earlier shape is a real event and is recorded.
- **Writing here never fails a design.** Same rule as the print log: the STLs are
  already on disk by then. `record_design` returns `None` rather than raising.
- **A version rebuilds only while the template still accepts its parameters.** The
  ledger records history; it cannot promise that a param which no longer exists will
  still validate. `encoder-mount` v2 is already orphaned this way — it holds
  `grub_lead_in`, which `grub_taper` replaced the same day. Its record still *reads*
  fine, and that is the useful half; only feeding it back would fail. Renaming a
  param strands every version that used it, so prefer adding one.
- **`source` says how much to trust it.** `design_part` was captured live;
  `reconstructed` was entered afterwards and the `note` says how it was derived. The
  two `encoder-mount` versions are both `reconstructed` — they predate the ledger.
- `conftest` isolates `evee.design_log.DESIGN_LOG`. A test that wrote to the real
  ledger would file a fixture's throwaway box as a real part and march the version
  numbers on every pytest run. **Patch the module, never `evee.config`** — and note a
  *test module* doing `from evee.design_log import DESIGN_LOG` binds the value and
  escapes the patch. That happened; it wrote junk into the real ledger.

### `magnet_encoder_case.py`
- **Inverting the board points *everything* at the magnet, not just the chip.** On an
  Adafruit breakout the STEMMA connectors are on the same face and are far taller than
  the sensor's 1.75mm, so on plain posts the *connectors* set the height and hold the
  chip millimetres too high — while the part looks right. `underside_height` is a
  required param for this reason, and the check fired the first time the template was
  run on plausible numbers. It is the whole reason this is not `box_with_lid`.
- **`board_face()` owns the sum**, as `yoke.board_face()` does: `magnet_proud +
  air_gap + package_height`. A typed post height folds three measurements into one
  unverifiable number, and the failure is silent — it assembles, it reads, the angle
  is merely wrong.
- **The glue line is in the stack and nothing in the part can see it.** A 0.2mm bead
  adds 0.2mm to the air gap. The read-back says so; it is the only term not printed.
- **The posts stand on the base plate, never on the handle.** Standing them on the
  glued surface would leave four columns that are not one solid, held only by the
  glue bead — at exactly the height the part exists to control.
- **The window follows the sensor, not the board's centre.** The chip is not
  necessarily centred on its board; centring the window instead reads an angle off a
  magnet it is not over. `chip_offset_*` is the one measurement unrecoverable once
  assembled.
- **Sides are open on purpose.** Walls to the board face would sit where the STEMMA
  cables leave, needing port cutouts whose positions are a fourth thing to measure.

### `printer.py`
- **OctoPrint's start command takes no filename.** `POST /api/job {"command":"start"}`
  prints whatever is *currently selected* — possibly something a human picked in the web
  UI hours ago. This is the least obvious hazard in the pipeline and the one most likely
  to be reintroduced by someone simplifying the code. `start_print` selects the file,
  reads the selection back, and refuses on mismatch without sending the start. Tests
  assert the refusal *and* that no `POST /api/job` went out.
- **The bed confirmation is checked with `is not True`**, not for truthiness — a model
  passing `"yes"` has produced something truthy without anyone looking at a build plate.
  Keyword-only, so it cannot arrive by position. Same for `cancel_print(confirmed=)`.
- **`stale_reason()` guards the upload.** G-code is a snapshot and neither of its inputs
  leaves a trace in it, so it compares mtime against the profile and the sibling STL.
  This exists because a start-sequence fix was verified in a scratch file and the *stale*
  copy in `output/` was uploaded and started. **The check lives in the client, not the MCP
  tool**, because that mistake was made by calling the client directly.
- **The bed check and the idle check both run before the upload**, not only inside
  `start_print`. Transferring 500 KB to a Pi and only then refusing leaves a file nobody
  asked for on the SD card. The check inside `start_print` still runs after, for the
  window where a print began mid-upload.
- **Uploads read back the stored filename** — OctoPrint sanitises names, so the
  select-and-start sequence uses what the printer called the file, never the local stem.
- **`.env` is read off disk, not from `os.environ`** — same scrubbing problem as
  `DISPLAY`. `config._dotenv()` does not export into `os.environ` either, so the key stays
  out of the environment of the slicer and viewer subprocesses.
- **`GET /api/printer` answers 409, not 200, when disconnected.** That is a state worth
  reporting, so `get_status()` falls back to `/api/connection` for the state text.
- **`mcp` 2.0 brings `httpx2`, not `httpx`** (same API), named in `pyproject.toml`
  directly rather than relied on transitively. **Its request log goes to stderr** via
  logging's last-resort handler — do not add a stdout handler.
- **`output/print_log.jsonl` records every start and cancel** before the command goes out.
  Best-effort — an unwritable log never blocks an approved print. Refusals are not
  recorded: the log is what was started, not what was asked for.
- **`cancel_print` parks the head, and that is why it is a tool at all.** The machine's own
  button does not: a bare cancel leaves a hot nozzle over the ruined part with the plate
  unreachable.
- **Parking is the only G-code this package emits**, as the module constant
  `printer._park_commands()`. No public method takes a command string and none should — a
  freeform G-code path would end the property that this server only does what it
  documents. `test_no_public_method_accepts_a_gcode_command` enforces it by introspection,
  because a comment is not a mechanism.
- **The park has to wait for real idle.** `PrinterStatus.printing` folds in OctoPrint's
  `cancelling` and `finishing` flags — exactly the state a fresh cancel leaves and exactly
  when OctoPrint rejects commands. `_wait_until_idle` polls a **fixed attempt count**, not
  a wall-clock deadline: `conftest` patches `time.sleep` to a no-op, so a deadline loop
  would spin thousands of mock requests.
- **A park failure is reported, never raised.** By then the print is already stopped,
  which is what was asked for.
- **`store_bed_mesh` confirms with a temperature sentinel.** OctoPrint answers 204 the
  moment it queues `G28`/`G29`/`M500` and exposes no "probe finished" flag, so a fourth
  command `M104 S42` is appended and the nozzle *target* is polled until it reads back.
  OctoPrint learns that target from the printer, so it cannot appear until Marlin has
  executed past `G29`. A failed probe never reaches it, the wait times out, and **no mesh
  is recorded** — fail-safe by construction rather than by care.
- **G-code we write must be ASCII.** It goes down a serial link. An em-dash in an `M420 S1`
  comment is what caught this; prose punctuation has no business in a `.gcode` file.
- **`start_print` uploads as well as starts** — owner's decision. `upload_gcode` and
  `start_print` are still separate methods and the two REST requests are still separate;
  `upload_and_print` composes them. The cost, accepted knowingly: the upload leg can no
  longer be exercised live through MCP without printing.

### `server.py`
- **`mcp` 2.0 renamed things.** `FastMCP` is gone; it is `MCPServer` from `mcp.server`.
  Fields are snake_case (`input_schema`, `is_error`), not camelCase.
- **Nothing may write to stdout** — stdio transport is JSON-RPC on that stream. build123d's
  builder logging goes to stderr, which is why it is safe; keep it that way.
- Called in-process a failing tool raises `ToolError`; over a transport the kernel turns
  the same failure into `CallToolResult(is_error=True)`. Tests handle both.
- **Validation error messages are an API.** They are the client model's only
  self-correction signal, so `_describe_error` strips Pydantic's `(root)` / `Value error,`
  noise while keeping the message naming the offending value.

### `notify.py`
- **It is a separate process, and that is the design.** `python -m evee.notify`, systemd
  user unit in `packaging/`. A poller inside the MCP server would die with the editor
  session — exactly when you have walked away and want telling.
- **It only reads.** No public entry point takes anything that could move the machine.
- **It is the first thing that records an outcome.** `print_finished` / `print_failed`
  join the commands in `print_log.jsonl`, upgrading `calibration`'s "last print was
  cancelled" proxy into a real answer. Without the daemon the log holds intent only, and a
  lone `start_print` reads as *not* bad news on purpose — staying quiet on ambiguity beats
  nagging on a guess.
- **`classify()` is a pure function of (previous, status, job)**, so the whole transition
  table is testable without a printer, a clock or a network. Two bugs were caught that way
  before it ever ran: the "was printing, now is not" branch never checked that it *is
  not*, so every mid-print poll fell into the cancel path; and it read the stale
  completion rather than the current one, so a finish at 99→100 reported as a cancel.
- **A stop below ~100% is a cancel, not a finish.** This is the only thing in the repo that
  notices somebody pressing cancel on the machine itself. OctoPrint may report completion
  as null at that moment, so the last seen figure is the fallback.
- **The first poll adopts state without announcing it**, or restarting the daemon
  mid-print claims a print just began, every time.
- **An unreachable printer is not a disconnected printer.** A blinked wifi link is logged
  and skipped; reporting a ruined print on a dropped packet teaches people to swipe these
  away. A printer that is genuinely gone answers and says it is offline.
- **Nothing about reporting may kill the watcher.** ntfy down, no webcam, bad topic — all
  swallowed and logged.
- **ntfy carries text in HTTP headers when a file is attached**, and headers are latin-1,
  so the title and message are ASCII-folded before they go out.

---

## Method notes

The durable lessons, which are about method rather than about any one change.

- **When something works elsewhere and not here, read the working artefact first — all of
  it.** Three failed fixes were designed from theory while `reference/cura_BNO_Case.gcode`
  sat unread. The note "read the working artefact first" was written after the second
  failure and then not followed: the file was opened for its levelling line, and neither
  its structure nor its temperature was looked at.
- **A number that explains a discrepancy is a finding, not a footnote.** "187 seconds" was
  measured, written down, quoted while designing two fixes, and treated as background.
- **A completed print is not proof of a clean start.** One plate print finished, was
  watched, and was signed off — and still had the blob defect; its first layer simply
  happened to survive. Watch the *first two minutes* specifically.
- **Deleting a test whose premise was disproved is correct.** Three tests encoding the
  abandoned start-sequence theories were removed rather than patched; keeping them would
  pin a design that failed four times.

---

## Decisions that override `BUILD_PLAN.md`

- **No extraction model in the pipeline** (supersedes Phase 4). The connecting client's
  model picks a template and fills its params from the JSON schema; the server validates.
  No `extract.py`, no Ollama.
- **`design_part(template, params)` — never a free-text `description`.** A free-text
  parameter would put extraction back inside the server.
- **Server-side validation is the only guarantee.** The old plan leaned on constrained
  decoding to stop a model inventing a field; we do not control an arbitrary client's
  decoder. `extra="forbid"` and the cross-field `_validate()` are the real gate.
- **Capping lid**, not rebated flush. The lid is a full-outer-dimension plate; its lip
  drops into the cavity, inset from the lid edge by `wall + clearance`. The plan's
  "`wall/2 + clearance`" described a rebated design inconsistent with its own test.
- **The cavity is a boolean subtraction, not a shell/`offset()`.** With `fillet=1.0` and
  `wall=2.0` a negative shell offset implies a −1.0mm inner radius, which OCC only
  survives via `Kind.INTERSECTION`. Subtraction is robust and gives exact inner dimensions.
- **`lid_style="sliding"` raises `NotImplementedError`** — kept in the signature and the
  Pydantic model so the schema is stable.
- **`lip_height` was added** to the signature; the plan had no way to express engagement
  depth.

## Conventions worth keeping

- Every template exposes a Pydantic params model with `extra="forbid"`, including nested
  models like `PortSpec`. `model_json_schema()` is what the client model fills, and
  server-side rejection of unknown fields is the guarantee — not the client's decoder.
- Read-back sentences are templated in Python from *validated* params
  (`resolved_spec_sentence`), never written by a model.
- **Adding a feature to a template has a fixed shape**: extend the params model → add
  cross-field checks to `_validate()` with a message naming the bad value → subtract/add
  geometry in the build function → assert on volume and bounding box in `tests/`. `ports`,
  `standoffs`, `lid_posts` and `floor_holes` are the worked examples. New *dimensions* are
  free; new *features* cost a change like this, and that is the intended trade.
- Previews are matplotlib-only (`Agg`). No pyrender/pyglet — offscreen GL on headless
  Linux is not worth the debugging time, and these images only need to answer "is the
  shape right".
