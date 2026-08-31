import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest import mock

import uld_stack_manager


class UldStackManagerTests(unittest.TestCase):
    def setUp(self):
        self._old_shared = os.environ.get("FLOW_SHARED_STATE_DIR")
        self._tmp = Path(__file__).resolve().parents[1] / "_codex_uld_tests" / uuid.uuid4().hex
        self._tmp.mkdir(parents=True, exist_ok=True)
        os.environ["FLOW_SHARED_STATE_DIR"] = str(self._tmp)

    def tearDown(self):
        if self._old_shared is None:
            os.environ.pop("FLOW_SHARED_STATE_DIR", None)
        else:
            os.environ["FLOW_SHARED_STATE_DIR"] = self._old_shared
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_add_from_list_preserves_top_to_bottom_order_and_position(self):
        stack = uld_stack_manager.create_stack("GHA 1")

        self.assertTrue(uld_stack_manager.add_from_list_to_stack(stack["id"], ["PMC001", "PAG002"]))
        stored = uld_stack_manager.get_stacks()[0]["ulds"]
        self.assertEqual(stored, ["PAG002", "PMC001"])

        revision = uld_stack_manager.get_stacks()[0]["revision"]
        self.assertTrue(
            uld_stack_manager.add_from_list_to_stack(
                stack["id"],
                ["AKE003"],
                position=1,
                expected_revision=revision,
            )
        )
        stored = uld_stack_manager.get_stacks()[0]["ulds"]
        self.assertEqual(stored, ["PAG002", "AKE003", "PMC001"])

    def test_move_between_stacks_removes_from_source_and_inserts_at_target_position(self):
        source = uld_stack_manager.create_stack("Source")
        target = uld_stack_manager.create_stack("Target")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(source["id"], ["PMC001", "PMC002", "PMC003"]))
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(target["id"], ["PMC900"]))

        stacks = {s["name"]: s for s in uld_stack_manager.get_stacks()}
        self.assertTrue(
            uld_stack_manager.move_between_stacks(
                source["id"],
                target["id"],
                ["PMC002"],
                position=1,
                source_expected_revision=stacks["Source"]["revision"],
                target_expected_revision=stacks["Target"]["revision"],
            )
        )

        stacks = {s["name"]: s for s in uld_stack_manager.get_stacks()}
        self.assertEqual(stacks["Source"]["ulds"], ["PMC003", "PMC001"])
        self.assertEqual(stacks["Target"]["ulds"], ["PMC002", "PMC900"])

    def test_remove_from_stack_only_touches_named_stack(self):
        first = uld_stack_manager.create_stack("First")
        second = uld_stack_manager.create_stack("Second")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(first["id"], ["PMC001"]))
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(second["id"], ["PMC002"]))

        first_revision = next(s for s in uld_stack_manager.get_stacks() if s["id"] == first["id"])["revision"]
        self.assertTrue(uld_stack_manager.remove_from_stack(first["id"], ["PMC001"], expected_revision=first_revision))

        stacks = {s["name"]: s for s in uld_stack_manager.get_stacks()}
        self.assertEqual(stacks["First"]["ulds"], [])
        self.assertEqual(stacks["Second"]["ulds"], ["PMC002"])

    def test_reorder_stack_uses_backend_bottom_to_top_order(self):
        stack = uld_stack_manager.create_stack("Reorder")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(stack["id"], ["PMC001", "PMC002", "PMC003"]))

        revision = uld_stack_manager.get_stacks()[0]["revision"]
        self.assertTrue(uld_stack_manager.reorder_stack(stack["id"], ["PMC001", "PMC003", "PMC002"], expected_revision=revision))

        stored = uld_stack_manager.get_stacks()[0]["ulds"]
        self.assertEqual(stored, ["PMC001", "PMC003", "PMC002"])

    def test_reorder_stack_rejects_partial_or_foreign_contents(self):
        stack = uld_stack_manager.create_stack("Reorder")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(stack["id"], ["PMC001", "PMC002"]))
        before = uld_stack_manager.get_stacks()[0]

        with self.assertRaises(uld_stack_manager.StackConflict):
            uld_stack_manager.reorder_stack(
                stack["id"],
                ["PMC001"],
                expected_revision=before["revision"],
            )
        with self.assertRaises(uld_stack_manager.StackConflict):
            uld_stack_manager.reorder_stack(
                stack["id"],
                ["PMC001", "PMC002", "AKE999"],
                expected_revision=before["revision"],
            )

        after = uld_stack_manager.get_stacks()[0]
        self.assertEqual(after["ulds"], before["ulds"])
        self.assertEqual(after["revision"], before["revision"])

    def test_add_from_list_persists_stack_gha_and_rejects_mixed_gha(self):
        stack = uld_stack_manager.create_stack()
        self.assertTrue(
            uld_stack_manager.add_from_list_to_stack(
                stack["id"],
                ["PMC001"],
                stack_gha="Menzies",
            )
        )
        stored = uld_stack_manager.get_stacks()[0]
        self.assertEqual(stored["stack_gha"], "Menzies")

        with self.assertRaises(uld_stack_manager.StackValidationError):
            uld_stack_manager.add_from_list_to_stack(
                stack["id"],
                ["AKE002"],
                expected_revision=stored["revision"],
                stack_gha="Celebi",
            )

        unchanged = uld_stack_manager.get_stacks()[0]
        self.assertEqual(unchanged["ulds"], ["PMC001"])
        self.assertEqual(unchanged["stack_gha"], "Menzies")

    def test_repeated_add_does_not_duplicate_uld(self):
        stack = uld_stack_manager.create_stack()
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(stack["id"], ["PMC001"]))
        revision = uld_stack_manager.get_stacks()[0]["revision"]

        self.assertTrue(
            uld_stack_manager.add_from_list_to_stack(
                stack["id"],
                ["PMC001"],
                expected_revision=revision,
            )
        )
        stored = uld_stack_manager.get_stacks()[0]
        self.assertEqual(stored["ulds"], ["PMC001"])

    def test_create_stack_is_idempotent_for_same_client_operation(self):
        first = uld_stack_manager.create_stack(client_op_id="create-1")
        second = uld_stack_manager.create_stack(client_op_id="create-1")

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(uld_stack_manager.get_stacks()), 1)

    def test_distinct_auto_create_calls_make_distinct_empty_stacks(self):
        first = uld_stack_manager.create_stack()
        second = uld_stack_manager.create_stack()

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual([s["name"] for s in uld_stack_manager.get_stacks()], ["GHA 1", "GHA 2"])

    def test_create_stack_with_canonical_gha_uses_numbered_name(self):
        stack = uld_stack_manager.create_stack("Menzies")

        stored = uld_stack_manager.get_stacks()[0]
        self.assertEqual(stack["name"], "GHA 1")
        self.assertEqual(stored["name"], "GHA 1")
        self.assertEqual(stored["stack_gha"], "Menzies")

    def test_manual_uld_backfills_stack_gha(self):
        stack = uld_stack_manager.create_stack()

        self.assertTrue(
            uld_stack_manager.add_manual_uld_to_stack(
                stack["id"],
                uld_number="PMC001",
                awb="123",
                gha="Celebi",
            )
        )

        stored = uld_stack_manager.get_stacks()[0]
        self.assertEqual(stored["stack_gha"], "Celebi")

    def test_create_stack_with_ulds_is_atomic_and_idempotent_for_same_selection(self):
        first = uld_stack_manager.create_stack_with_ulds(
            uld_numbers=["PMC001", "AKE002"],
            client_op_id="create-ulds-1",
            stack_gha="Celebi",
        )
        second = uld_stack_manager.create_stack_with_ulds(
            uld_numbers=["PMC001", "AKE002"],
            client_op_id="create-ulds-2",
        )

        self.assertIsNotNone(first)
        self.assertEqual(second["id"], first["id"])
        stacks = uld_stack_manager.get_stacks()
        self.assertEqual(len(stacks), 1)
        self.assertEqual(stacks[0]["ulds"], ["AKE002", "PMC001"])
        self.assertEqual(stacks[0]["stack_gha"], "Celebi")

    def test_reactivated_uld_can_leave_older_dispatched_stack(self):
        old = uld_stack_manager.create_stack("Old dispatch")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(old["id"], ["PMC40729MU"]))
        stored_old = uld_stack_manager.get_stacks()[0]
        self.assertTrue(uld_stack_manager.set_stack_dispatched(old["id"], expected_revision=stored_old["revision"]))
        target = uld_stack_manager.create_stack("Current stack")

        with self.assertRaises(uld_stack_manager.StackLocked):
            uld_stack_manager.add_from_list_to_stack(target["id"], ["PMC40729MU"])

        self.assertTrue(uld_stack_manager.add_from_list_to_stack(
            target["id"],
            ["PMC40729MU"],
            allow_dispatched_ulds={"PMC40729MU"},
        ))

        by_id = {stack["id"]: stack for stack in uld_stack_manager.get_stacks()}
        self.assertEqual(by_id[old["id"]]["ulds"], [])
        self.assertEqual(by_id[target["id"]]["ulds"], ["PMC40729MU"])

    def test_create_stack_with_ulds_does_not_reuse_empty_create_op_collision(self):
        empty = uld_stack_manager.create_stack(client_op_id="same-op")

        created = uld_stack_manager.create_stack_with_ulds(
            uld_numbers=["PMC001", "AKE002"],
            client_op_id="same-op",
            stack_gha="Menzies",
        )

        self.assertIsNotNone(created)
        self.assertNotEqual(created["id"], empty["id"])
        stacks = uld_stack_manager.get_stacks()
        self.assertEqual(len(stacks), 1)
        self.assertEqual(stacks[0]["id"], created["id"])
        self.assertEqual(stacks[0]["ulds"], ["AKE002", "PMC001"])
        self.assertEqual(stacks[0]["stack_gha"], "Menzies")

    def test_create_stack_with_ulds_backfills_missing_gha_on_exact_reuse(self):
        first = uld_stack_manager.create_stack_with_ulds(
            uld_numbers=["PMC001", "AKE002"],
            client_op_id="create-ulds-no-gha",
        )

        second = uld_stack_manager.create_stack_with_ulds(
            uld_numbers=["AKE002", "PMC001"],
            client_op_id="create-ulds-with-gha",
            stack_gha="AS Cargo",
        )

        self.assertEqual(second["id"], first["id"])
        stacks = uld_stack_manager.get_stacks()
        self.assertEqual(len(stacks), 1)
        self.assertEqual(stacks[0]["stack_gha"], "AS Cargo")

    def test_create_stack_with_ulds_moves_from_existing_stack_without_orphaning_created_stack(self):
        source = uld_stack_manager.create_stack("Source")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(source["id"], ["PMC001", "AKE002", "PAG003"]))

        created = uld_stack_manager.create_stack_with_ulds(uld_numbers=["PMC001", "AKE002"])

        stacks = {s["id"]: s for s in uld_stack_manager.get_stacks()}
        self.assertEqual(stacks[source["id"]]["ulds"], ["PAG003"])
        self.assertEqual(stacks[created["id"]]["ulds"], ["AKE002", "PMC001"])

    def test_create_stack_with_ulds_drops_source_stack_emptied_by_the_move(self):
        # A stack holding only the pulled ULDs must not survive as an empty husk
        # when its contents go into a freshly created stack.
        source = uld_stack_manager.create_stack("Source")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(source["id"], ["PMC001"]))

        # AKE002 is loose, so the new stack's set differs from the source set and
        # the exact-match reuse does not apply — the source is left empty and must
        # be pruned.
        created = uld_stack_manager.create_stack_with_ulds(uld_numbers=["PMC001", "AKE002"])

        ids = {s["id"] for s in uld_stack_manager.get_stacks()}
        self.assertNotIn(source["id"], ids)
        self.assertIn(created["id"], ids)
        self.assertEqual(len(uld_stack_manager.get_stacks()), 1)
        self.assertEqual(uld_stack_manager.get_stacks()[0]["ulds"], ["AKE002", "PMC001"])

    def test_create_stack_with_ulds_keeps_preexisting_empty_stack(self):
        # A deliberately-empty stack (a drag target) must be left untouched.
        empty_target = uld_stack_manager.create_stack("Drag target")

        uld_stack_manager.create_stack_with_ulds(uld_numbers=["PMC001", "AKE002"])

        ids = {s["id"] for s in uld_stack_manager.get_stacks()}
        self.assertIn(empty_target["id"], ids)

    def test_rename_uld_updates_stack_and_records_override(self):
        stack = uld_stack_manager.create_stack("Rename")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(stack["id"], ["PMC001", "PAG002"]))

        self.assertTrue(uld_stack_manager.rename_uld_everywhere("PMC001", "PMC999"))

        stored = uld_stack_manager.get_stacks()[0]["ulds"]
        self.assertIn("PMC999", stored)
        self.assertNotIn("PMC001", stored)
        self.assertEqual(uld_stack_manager.get_uld_renames().get("PMC001"), "PMC999")

    def test_rename_uld_normalizes_and_rejects_noop(self):
        stack = uld_stack_manager.create_stack("Rename")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(stack["id"], ["PMC001"]))
        # Dashes/spaces/case are normalized away → identical → no-op.
        self.assertFalse(uld_stack_manager.rename_uld_everywhere("PMC001", "pmc-001"))

    def test_rename_uld_rejects_collision_with_existing_number(self):
        stack = uld_stack_manager.create_stack("Rename")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(stack["id"], ["PMC001", "PAG002"]))
        # PAG002 already exists in a stack → renaming PMC001 onto it must fail.
        self.assertFalse(uld_stack_manager.rename_uld_everywhere("PMC001", "PAG002"))

    def test_rename_uld_repoints_existing_override_chain(self):
        stack = uld_stack_manager.create_stack("Rename")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(stack["id"], ["PMC001"]))
        self.assertTrue(uld_stack_manager.rename_uld_everywhere("PMC001", "PMC002"))
        self.assertTrue(uld_stack_manager.rename_uld_everywhere("PMC002", "PMC003"))
        renames = uld_stack_manager.get_uld_renames()
        # Original Excel key still maps to the latest number; no stale chain.
        self.assertEqual(renames.get("PMC001"), "PMC003")
        self.assertNotIn("PMC002", renames)
        self.assertEqual(uld_stack_manager.get_stacks()[0]["ulds"], ["PMC003"])

    def test_update_uld_details_updates_stack_override_and_audit(self):
        stack = uld_stack_manager.create_stack("Edit")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(stack["id"], ["PMC001"]))

        with mock.patch.dict(os.environ, {"USERNAME": "tester"}):
            self.assertTrue(
                uld_stack_manager.update_uld_details(
                    "PMC001",
                    "PMC009",
                    awbs=["222", "333"],
                    gha="Celebi",
                    previous_awbs=["111"],
                    previous_gha="Menzies",
                )
            )

        stored = uld_stack_manager.get_stacks()[0]
        self.assertEqual(stored["ulds"], ["PMC009"])
        overrides = uld_stack_manager.get_uld_overrides()
        self.assertEqual(overrides["PMC001"]["uld_number"], "PMC009")
        self.assertEqual(overrides["PMC001"]["awbs"], ["222", "333"])
        self.assertEqual(overrides["PMC001"]["gha"], "Celebi")
        self.assertEqual(overrides["PMC001"]["edited_by"], "tester")
        self.assertTrue(overrides["PMC001"]["edited_at"])
        self.assertEqual(
            overrides["PMC001"]["change_summary"],
            "ULD: PMC001 -> PMC009; AWB: 111 -> 222, 333; GHA: Menzies -> Celebi",
        )

    def test_update_uld_details_revert_clears_override_and_rename(self):
        stack = uld_stack_manager.create_stack("Revert")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(stack["id"], ["PMC001"]))

        # Edit away from the source, then back to it with revert=True.
        self.assertTrue(uld_stack_manager.update_uld_details("PMC001", "PMC009", awbs=["222"], gha="Celebi"))
        self.assertTrue(
            uld_stack_manager.update_uld_details("PMC009", "PMC001", awbs=["111"], gha="Menzies", revert=True)
        )

        stored = uld_stack_manager.get_stacks()[0]
        self.assertEqual(stored["ulds"], ["PMC001"])           # number restored
        self.assertNotIn("PMC001", uld_stack_manager.get_uld_overrides())  # no lingering override
        self.assertNotIn("PMC009", uld_stack_manager.get_uld_overrides())
        self.assertEqual(uld_stack_manager.get_uld_renames(), {})          # rename cleared

    def test_update_uld_details_preserves_manual_stack_metadata_audit(self):
        stack = uld_stack_manager.create_stack("Manual edit")
        self.assertTrue(
            uld_stack_manager.add_manual_uld_to_stack(
                stack["id"],
                uld_number="PMC001",
                awb="111",
                gha="Menzies",
            )
        )

        with mock.patch.dict(os.environ, {"USERNAME": "manual-user"}):
            self.assertTrue(uld_stack_manager.update_uld_details("PMC001", "PMC009", awbs=["222"], gha="Celebi"))

        manual = uld_stack_manager.get_stacks()[0]["manual_ulds"]["PMC009"]
        self.assertEqual(manual["awb"], "222")
        self.assertEqual(manual["awbs"], ["222"])
        self.assertEqual(manual["gha"], "Celebi")
        self.assertEqual(manual["edited_by"], "manual-user")
        self.assertTrue(manual["edited_at"])
        self.assertEqual(manual["change_summary"], "ULD: PMC001 -> PMC009; AWB: 111 -> 222; GHA: Menzies -> Celebi")

    def test_delete_stack_removes_persisted_stack(self):
        stack = uld_stack_manager.create_stack("Delete me")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(stack["id"], ["PMC001"]))

        revision = uld_stack_manager.get_stacks()[0]["revision"]
        self.assertTrue(uld_stack_manager.delete_stack(stack["id"], expected_revision=revision))

        self.assertEqual(uld_stack_manager.get_stacks(), [])

    def test_delete_stack_invalidates_display_cache(self):
        stack = uld_stack_manager.create_stack("Delete me")
        cached = uld_stack_manager.get_stacks_cached()
        self.assertEqual(cached[0]["id"], stack["id"])

        revision = uld_stack_manager.get_stacks()[0]["revision"]
        self.assertTrue(uld_stack_manager.delete_stack(stack["id"], expected_revision=revision))

        self.assertEqual(uld_stack_manager.get_stacks_cached(), [])

    def test_set_stack_dispatched_marks_prepared_and_locks_active_edits(self):
        stack = uld_stack_manager.create_stack("Truck")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(stack["id"], ["PMC001"]))

        revision = uld_stack_manager.get_stacks()[0]["revision"]
        self.assertTrue(uld_stack_manager.set_stack_dispatched(stack["id"], True, expected_revision=revision))

        stored = uld_stack_manager.get_stacks()[0]
        self.assertTrue(stored["dispatched"])
        self.assertTrue(stored["prepared"])
        self.assertTrue(stored.get("dispatched_at"))
        with self.assertRaises(uld_stack_manager.StackLocked):
            uld_stack_manager.add_from_list_to_stack(stored["id"], ["PMC002"], expected_revision=stored["revision"])

    def test_undispatch_returns_stack_fully_editable(self):
        stack = uld_stack_manager.create_stack("Truck")
        self.assertTrue(uld_stack_manager.add_from_list_to_stack(stack["id"], ["PMC001"]))
        self.assertTrue(uld_stack_manager.set_stack_dispatched(stack["id"], True))

        # Revert: dispatched + prepared flags cleared, stack editable again.
        self.assertTrue(uld_stack_manager.set_stack_dispatched(stack["id"], False))
        stored = uld_stack_manager.get_stacks()[0]
        self.assertNotIn("dispatched", stored)
        self.assertNotIn("prepared", stored)
        # No StackLocked now that it is back in the active, unlocked state.
        self.assertTrue(
            uld_stack_manager.add_from_list_to_stack(stored["id"], ["PMC002"], expected_revision=stored["revision"])
        )

    def test_add_uld_to_stack_moves_single_uld_without_duplicates(self):
        first = uld_stack_manager.create_stack("First")
        second = uld_stack_manager.create_stack("Second")
        self.assertTrue(
            uld_stack_manager.add_manual_uld_to_stack(
                first["id"],
                uld_number="PAG18804O3",
                awb="92162070142",
                gha="Celebi",
            )
        )

        revision = next(s for s in uld_stack_manager.get_stacks() if s["id"] == second["id"])["revision"]
        self.assertTrue(uld_stack_manager.add_uld_to_stack(second["id"], "PAG18804O3", expected_revision=revision))

        stacks = {s["name"]: s for s in uld_stack_manager.get_stacks()}
        self.assertEqual(stacks["First"]["ulds"], [])
        self.assertEqual(stacks["Second"]["ulds"], ["PAG18804O3"])
        self.assertEqual(stacks["Second"]["manual_ulds"]["PAG18804O3"]["awb"], "92162070142")

    def test_add_manual_uld_persists_metadata_and_normalizes_number(self):
        stack = uld_stack_manager.create_stack("Manual")

        ok = uld_stack_manager.add_manual_uld_to_stack(
            stack["id"],
            uld_number="PAG18804\u041e3",
            awb="921-62070142",
            gha="Celebi",
            am_time="2026-05-31T08:00",
        )

        self.assertTrue(ok)
        stored = uld_stack_manager.get_stacks()[0]
        self.assertEqual(stored["ulds"], ["PAG18804O3"])
        self.assertEqual(stored["manual_ulds"]["PAG18804O3"]["awb"], "921-62070142")
        self.assertEqual(stored["manual_ulds"]["PAG18804O3"]["gha"], "Celebi")

    def test_add_manual_uld_allows_blank_awb(self):
        stack = uld_stack_manager.create_stack("Manual")

        ok = uld_stack_manager.add_manual_uld_to_stack(
            stack["id"],
            uld_number="PAG18804O3",
            awb="",
            gha="Celebi",
        )

        self.assertTrue(ok)
        stored = uld_stack_manager.get_stacks()[0]
        self.assertEqual(stored["manual_ulds"]["PAG18804O3"]["awb"], "")
        self.assertEqual(stored["manual_ulds"]["PAG18804O3"]["awbs"], [])


if __name__ == "__main__":
    unittest.main()
