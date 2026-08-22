# Workflow — Interactive Wires

**Owns:** `assets/wire.blend`, `addon/wires/wire_factory.py`,
`addon/operators/wire_place.py`

**Depends on:**
- `data/breadboard_grid.json` and a `nearest_hole(pos) -> (hole_id, pos)`
  lookup function (from whoever owns breadboard/placement)
- The netlist format (from whoever owns the solver) — you only need to know
  what custom properties they expect on a wire object

---

## Phase 0 — Setup (10 min)

1. `git pull`, `git lfs pull` — make sure you have the latest
   `breadboard_grid.json` and any existing `addon/` scaffolding.
2. Confirm with the placement owner: the exact function signature for
   nearest-hole lookup, and the coordinate space it returns (world space?
   local to the breadboard object?). Don't guess this — mismatched
   coordinate spaces are the #1 cause of wires snapping to the wrong spot.
3. Confirm with the solver owner: what custom properties a wire needs to
   carry (likely `hole_a`, `hole_b`, or the two bus-group IDs directly).

---

## Phase 1 — Static wire asset + factory function

1. In a fresh Blender scene, create a single Bezier curve object as your
   reference: 3 control points (start, sagging midpoint, end), bevel depth
   set for a plausible wire thickness, a simple colored material (start
   with one solid color — red or black — you'll parametrize later).
2. Save this as `assets/wire.blend` — this is your reference/template,
   used as a preview, not something you'll hand-edit repeatedly (the whole
   point is you'll drive its shape from code).
3. Write `addon/wires/wire_factory.py` with a `create_wire(name)` function
   that builds a wire object procedurally in code (don't rely on
   appending the `.blend` file at runtime — creating it directly via `bpy`
   is simpler and avoids link/append complexity):
   - Bezier curve, 3 points, bevel depth + resolution set, `fill_mode =
     'FULL'`, auto handle types.
4. Test in the Scripting tab: call `create_wire("test")`, confirm a
   reasonable-looking wire object appears at the default position.
5. Once confirmed, this stays as your permanent factory function — move
   on, don't polish the visual yet.

**Done when:** calling `create_wire()` from the Python console produces a
correctly-shaped wire object with the right bevel/material.

---

## Phase 2 — Endpoint update function (the core driver)

1. In the same file, write `update_wire_endpoints(wire_obj, start_pos,
   end_pos, sag=0.015)`:
   - Sets point 0 to `start_pos`, point 2 to `end_pos`.
   - Computes the midpoint and offsets it downward by `sag` for point 1.
   - Re-sets auto handle types (needed if you're re-adding/changing point
     counts) and calls `wire_obj.data.update_tag()`.
2. Test manually: create a wire, call
   `update_wire_endpoints(wire, (0,0,0), (0.5,0,0))`, confirm it visually
   updates. Call it again with different coordinates, confirm it moves
   without creating a new object.
3. Sanity-check with zero-length input (`start_pos == end_pos`) — this
   will be used as the "preview" state before the second click lands, so
   it must not throw an error or produce a degenerate/invisible curve that
   breaks later updates.

**Done when:** you can call this function repeatedly on the same object
and watch it reshape live, including the zero-length edge case.

---

## Phase 3 — Two-click placement operator

1. Write `addon/operators/wire_place.py` with a modal operator
   (`WIRE_OT_place`) implementing this state machine:
   - `invoke`: create a new wire via `create_wire()`, set state to
     `PICK_START`, register the modal handler.
   - `modal`, `MOUSEMOVE`: look up nearest hole under the cursor via the
     placement owner's function; call `update_wire_endpoints` — in
     `PICK_START` state, both ends track the cursor (zero-length preview);
     in `PICK_END` state, start is fixed and end tracks the cursor.
   - `modal`, `LEFTMOUSE` press: in `PICK_START`, lock in the start hole
     and switch to `PICK_END`. In `PICK_END`, lock in the end hole, call
     your netlist-registration function (Phase 4), return `{'FINISHED'}`.
   - `modal`, `ESC`: delete the in-progress wire object, return
     `{'CANCELLED'}`.
2. Register the operator in `addon/__init__.py`, bind it to a button in
   the component tray (coordinate with whoever owns the tray UI — this is
   likely the same person who owns R/C/L placement).
3. Test end to end inside Blender: click the wire tool, move the mouse
   over the breadboard (confirm snapping + preview), click once, move
   again (confirm the fixed end stays put), click again (confirm it
   finalizes), press Escape mid-placement on a separate attempt (confirm
   cleanup).

**Done when:** you can place a wire between two arbitrary holes entirely
by clicking, with a live preview and working cancel.

---

## Phase 4 — Netlist hookup

1. Write `register_wire_in_netlist(wire_obj, hole_a, hole_b)`: sets custom
   properties on the wire object matching exactly what the solver owner
   expects (confirmed in Phase 0) — e.g. `wire_obj["hole_a"] = hole_a`,
   `wire_obj["hole_b"] = hole_b`.
2. Call this from the operator's `PICK_END` completion step (Phase 3,
   already stubbed in).
3. Write a matching removal-side function, `unregister_wire(wire_obj)` —
   even if deletion is largely handled elsewhere, wires need this because
   removing a wire changes which bus groups are merged, not just what's
   visible. Confirm with the solver owner whether they need a signal
   *before* or *after* the object is deleted (they'll likely want the
   hole IDs *before* deletion, so read the custom properties first, then
   delete the object).
4. Sync test with the solver owner: place a wire between two holes,
   confirm their netlist builder actually picks up the merge (this may
   need to wait until their side is ready — don't block on it, just flag
   it as an open integration point).

**Done when:** every placed wire carries the two custom properties the
solver needs, and removal is confirmed to signal correctly on their side.

---

## Phase 5 — Deletion / cleanup

1. Add wire selection (click an existing wire to select it, not to start
   a new placement — make sure your operator's `invoke` doesn't fire when
   clicking on an already-placed wire).
2. Add a delete path (Delete key while a wire is selected, or a per-object
   "remove" affordance if that's the convention the tray/placement owner
   is using for R/C/L too — match their pattern rather than inventing a
   different one for wires).
3. On delete: read `hole_a`/`hole_b` custom properties, call whatever
   "un-merge" hook the solver owner exposes (or, if they only rebuild the
   whole netlist from scratch each time rather than incrementally
   merging, you may not need this at all — confirm which approach they're
   using before building it).
4. Remove the Blender object (`bpy.data.objects.remove(wire_obj,
   do_unlink=True)`), also cleaning up its curve datablock
   (`bpy.data.curves.remove(...)`) to avoid orphaned data accumulating in
   the file.

**Done when:** placing and deleting wires repeatedly doesn't leave orphaned
data or break the netlist state.

---

## Phase 6 — Visual polish (do this last, not before core function works)

1. Parametrize wire color — either a fixed palette students pick from, or
   auto-colored by placement order, driven by a material property rather
   than manual per-object edits.
2. Add a "dragging/invalid" tint (e.g. red while `PICK_END` and hovering
   somewhere invalid — off the breadboard, or the same hole as start) vs.
   normal color once placed.
3. Tune the `sag` value and bevel thickness against the final breadboard
   scale once it's finalized — these are easy to eyeball wrong before the
   full scene is assembled.
4. Optional: vary sag or add slight randomized curve noise so multiple
   wires on screen don't look mechanically identical.

**Done when:** wires look intentional next to the final breadboard/scene,
not just functionally correct.

---

## Checklist

- [ ] Confirmed `nearest_hole()` signature + coordinate space with
      placement owner
- [ ] Confirmed required custom properties with solver owner
- [ ] `create_wire()` factory function
- [ ] `update_wire_endpoints()` including zero-length edge case
- [ ] Two-click modal operator with working preview + cancel
- [ ] `register_wire_in_netlist()` wired into placement completion
- [ ] `unregister_wire()` / delete path with no orphaned data
- [ ] Integration test with solver owner confirms merges register
- [ ] Visual polish pass (color, invalid-state tint, sag/scale tuning)
