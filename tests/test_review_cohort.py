"""Offline regressions for the finite, non-reseeding recovery review cohort."""

import contextlib
import copy
import csv
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import collect
import review_cohort
import review_fresh
import review_gate


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
VERSION = collect.TECH_SCOPE_REVIEW_VERSION


def candidate(item_id, *, hours_old=40, published_days_old=1, **overrides):
    return {
        "canonical_id": item_id,
        "title": "A new artificial intelligence research finding",
        "summary": "A source summary awaiting review",
        "source": "Test Source",
        "first_seen": collect.iso_z(NOW - timedelta(hours=hours_old)),
        "published_at": collect.iso_z(NOW - timedelta(days=published_days_old)),
        "article_frames": ["Technology Innovation"],
        "topics": ["Artificial Intelligence"],
        "collection_mode": "Daily",
        "scope_review_version": "previous-review-version",
        "status": "New",
        **overrides,
    }


def reviewed(item):
    item.update(scope_review_version=VERSION, title_ja="研究結果", summary_ja="研究の要約。")
    return item


def manifest(*ids, **overrides):
    return {
        "schema_version": 1,
        "cohort_id": "original252-recovery-test",
        "target_review_version": VERSION,
        "original_count": len(ids),
        "original_ids": list(ids),
        "active_ids": list(ids),
        **overrides,
    }


def item_ids(items):
    return [collect.review_item_id(item) for item in items]


class CohortManifestTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "review_cohort.json"

    def write(self, payload):
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def load(self, version=VERSION):
        return review_cohort.load(self.path, expected_version=version)

    def reconcile(self, items, checked_at=NOW, version=VERSION):
        return review_cohort.reconcile(
            self.path, items, checked_at,
            expected_version=version, needs_review=collect.needs_scope_review,
        )

    def test_missing_manifest_does_not_create_or_seed_a_cohort(self):
        self.assertFalse(self.load())
        with self.assertRaises(ValueError):
            review_cohort.load(self.path, VERSION, required=True)
        with self.assertRaises(ValueError):
            self.reconcile([candidate("unrelated")])
        self.assertFalse(self.path.exists())

    def test_round_trip_preserves_original_membership_and_exact_active_ids(self):
        payload = manifest("a", "b", "c", active_ids=["b"])
        self.write(payload)
        loaded = self.load()
        self.assertEqual(loaded["original_ids"], ["a", "b", "c"])
        self.assertEqual(loaded["active_ids"], ["b"])
        items = [candidate("outside"), candidate("c"), candidate("b"), candidate("a")]
        retained = review_cohort.retained_items(
            items, loaded, needs_review=collect.needs_scope_review,
        )
        self.assertEqual(item_ids(retained), ["b"])
        self.assertIs(retained[0], items[2])

    def test_invalid_manifests_fail_closed_without_rewriting(self):
        invalid = [
            [],
            {**manifest("a"), "schema_version": 2},
            {**manifest("a"), "original_ids": "a"},
            {**manifest("a"), "active_ids": "a"},
            manifest("a", "a"),
            manifest("a", active_ids=["a", "a"]),
            manifest("a", active_ids=["unapproved"]),
            manifest(""),
            manifest(" "),
            manifest(1),
            manifest("a", active_ids=[None]),
            manifest("a", completed_at=collect.iso_z(NOW)),
            manifest("a", original_count=2),
            manifest(),
            manifest(*(str(i) for i in range(253))),
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.write(payload)
                before = self.path.read_bytes()
                with self.assertRaises((ValueError, TypeError)):
                    self.load()
                self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_json_and_missing_required_fields_fail_closed(self):
        self.path.write_text("{not valid JSON", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load()
        for key in ("schema_version", "cohort_id", "target_review_version", "original_count", "original_ids", "active_ids"):
            with self.subTest(key=key):
                payload = manifest("a")
                payload.pop(key)
                self.write(payload)
                with self.assertRaises((ValueError, TypeError, KeyError)):
                    self.load()

    def test_missing_active_ledger_id_fails_instead_of_silently_completing(self):
        self.write(manifest("a", "missing"))
        before = self.path.read_bytes()
        items = [reviewed(candidate("a"))]
        with self.assertRaises(ValueError):
            review_cohort.retained_items(
                items, self.load(), needs_review=collect.needs_scope_review,
            )
        with self.assertRaises(ValueError):
            self.reconcile(items)
        self.assertEqual(self.path.read_bytes(), before)

    def test_only_active_ids_must_exist_after_prior_members_are_pruned(self):
        self.write(manifest("already-pruned", "pending", active_ids=["pending"]))
        retained = review_cohort.retained_items(
            [candidate("pending")], self.load(), needs_review=collect.needs_scope_review,
        )
        self.assertEqual(item_ids(retained), ["pending"])

    def test_duplicate_ledger_rows_resolve_once_and_preserve_unresolved_work(self):
        self.write(manifest("a"))
        pending = candidate("a")
        done = reviewed(candidate("a"))
        for items in ([done, pending], [pending, done], [pending, copy.deepcopy(pending)]):
            with self.subTest(items=items):
                retained = review_cohort.retained_items(
                    items, self.load(), needs_review=collect.needs_scope_review,
                )
                self.assertEqual(item_ids(retained), ["a"])
                self.assertTrue(collect.needs_scope_review(retained[0]))

    def test_id_fallback_is_supported_without_matching_a_different_canonical_id(self):
        self.write(manifest("fallback-id"))
        item = candidate("unused", id="fallback-id")
        with self.assertRaises(ValueError):
            review_cohort.retained_items(
                [item], self.load(), needs_review=collect.needs_scope_review,
            )
        item.pop("canonical_id")
        retained = review_cohort.retained_items(
            [item], self.load(), needs_review=collect.needs_scope_review,
        )
        self.assertEqual(item_ids(retained), ["fallback-id"])

    def test_restart_prunes_reviewed_and_excluded_without_reseeding(self):
        original = ["keep", "reviewed", "excluded"]
        self.write(manifest(*original))
        items = [candidate("keep"), reviewed(candidate("reviewed")),
                 candidate("excluded", status="Excluded", scope_review_version=VERSION),
                 candidate("new-arrival")]
        self.reconcile(items)
        restarted = self.load()
        self.assertEqual(restarted["original_ids"], original)
        self.assertEqual(restarted["active_ids"], ["keep"])
        self.assertFalse(restarted.get("completed_at"))

        # A previously resolved member becoming pending again does not expand
        # this one-off recovery authorization.
        items[1]["scope_review_version"] = "previous-review-version"
        self.reconcile(items, NOW + timedelta(hours=1))
        self.assertEqual(self.load()["active_ids"], ["keep"])
        reviewed(items[0])
        self.reconcile(items, NOW + timedelta(hours=2))
        completed = self.load()
        self.assertEqual(completed["original_ids"], original)
        self.assertEqual(completed["active_ids"], [])
        self.assertTrue(completed.get("completed_at"))
        completion_time = completed["completed_at"]

        # A restart cannot recreate the cohort from its original list.
        items[0]["scope_review_version"] = "previous-review-version"
        self.reconcile(items, NOW + timedelta(days=1))
        self.assertEqual(self.load()["active_ids"], [])
        self.assertEqual(self.load()["completed_at"], completion_time)

    def test_active_version_mismatch_fails_but_completed_cohort_stays_inactive(self):
        self.write(manifest("a"))
        with self.assertRaises(ValueError):
            self.load("new-review-version")
        with self.assertRaises(ValueError):
            self.reconcile([candidate("a")], version="new-review-version")
        self.write(manifest("a", active_ids=[], completed_at=collect.iso_z(NOW)))
        loaded = self.load("new-review-version")
        self.assertEqual(loaded["active_ids"], [])
        self.reconcile([candidate("a")], version="new-review-version")
        self.assertEqual(self.load("new-review-version")["active_ids"], [])


class CohortPrioritySelectionTests(unittest.TestCase):
    def test_pinned_member_survives_the_36_hour_boundary_and_defaults_stay_pure(self):
        pinned = candidate("pinned", hours_old=36)
        old = candidate("unrelated-history", hours_old=40)
        items = [old, pinned]
        self.assertEqual(item_ids(collect.fresh_priority_review_items(items, NOW)), ["pinned"])
        after_boundary = NOW + timedelta(seconds=1)
        self.assertEqual(collect.fresh_priority_review_items(items, after_boundary), [])
        self.assertEqual(item_ids(collect.fresh_priority_review_items(
            items, after_boundary, cohort_ids={"pinned"},
        )), ["pinned"])

    def test_union_is_oldest_first_deduplicated_and_excludes_unrelated_history(self):
        old = candidate("pinned-old", hours_old=60)
        overlap = candidate("pinned-and-fresh", hours_old=20)
        fresh = candidate("fresh", hours_old=1)
        unrelated = candidate("unrelated-history", hours_old=70)
        backfill = candidate("unrelated-backfill", hours_old=1, collection_mode="Historical Backfill")
        selected = collect.fresh_priority_review_items(
            [fresh, unrelated, overlap, backfill, old, copy.deepcopy(overlap)], NOW,
            cohort_ids={"pinned-old", "pinned-and-fresh"},
        )
        self.assertEqual(item_ids(selected), ["pinned-old", "pinned-and-fresh", "fresh"])

    def test_reviewed_and_current_version_excluded_members_are_not_retained(self):
        items = [candidate("pending"), reviewed(candidate("reviewed")),
                 candidate("excluded", status="Excluded", scope_review_version=VERSION)]
        selected = collect.fresh_priority_review_items(
            items, NOW, cohort_ids={"pending", "reviewed", "excluded"},
        )
        self.assertEqual(item_ids(selected), ["pending"])

    def test_backfill_is_only_admitted_when_its_exact_id_was_pinned(self):
        items = [candidate("pinned", hours_old=500, collection_mode="Historical Backfill"),
                 candidate("unrelated", hours_old=1, collection_mode="Historical Backfill")]
        self.assertEqual(collect.fresh_priority_review_items(items, NOW), [])
        selected = collect.fresh_priority_review_items(items, NOW, cohort_ids={"pinned"})
        self.assertEqual(item_ids(selected), ["pinned"])

    def test_pinned_queue_cannot_increase_the_requested_batch_limit(self):
        items = [candidate(f"pinned-{i:03}", hours_old=100 - i) for i in range(30)]
        priority = collect.fresh_priority_review_items(
            list(reversed(items)), NOW, cohort_ids=set(item_ids(items)),
        )
        for limit in (0, 1, 6, 12):
            with self.subTest(limit=limit):
                selected = review_fresh.select_fresh_only(items, priority, limit=limit)
                self.assertEqual(item_ids(selected), item_ids(items[:limit]))

    def test_recovery_does_not_change_workflow_frequency_or_api_caps(self):
        workflow = (ROOT / ".github/workflows/review-backlog.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("cron:"), 1)
        self.assertIn('cron: "7,37 * * * *"', workflow)
        self.assertIn('JAPANESE_SUMMARY_BATCH_SIZE: "6"', workflow)
        self.assertIn("JAPANESE_SUMMARY_BACKFILL_LIMIT: ${{ github.event_name == 'workflow_dispatch' && inputs.review_limit || github.event_name == 'push' && '100' || '12' }}", workflow)
        self.assertIn("JAPANESE_SUMMARY_REQUEST_BUDGET: ${{ github.event_name != 'schedule' && '19' || '2' }}", workflow)
        state = {"updated_at": collect.iso_z(NOW - timedelta(hours=1)),
                 "pending_priority_total": 252, "pending_priority_36h": 0,
                 "pending_recovery_cohort": 252, "pending_in_window": 252}
        decision = review_gate.evaluate_review_gate(
            {"runs": [{"run_at": collect.iso_z(NOW - timedelta(hours=1)),
                       "summary_requests": 146}]}, state, now=NOW, force=True,
        )
        self.assertFalse(decision["should_run"])


class CohortReviewIntegrationTests(unittest.TestCase):
    @contextlib.contextmanager
    def run_context(self, items, cohort, *, finish_ids=(), limit=12):
        """Exercise real selection/reconciliation while prohibiting all I/O APIs."""
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            path = Path(directory) / "review_cohort.json"
            path.write_text(json.dumps(cohort), encoding="utf-8")
            selected_ids = []

            def fake_enrich(all_items, new_items, *, selected_items, **kwargs):
                selected_ids.extend(item_ids(selected_items))
                for item in selected_items:
                    if collect.review_item_id(item) in finish_ids:
                        reviewed(item)
                return {
                    "generated": len(finish_ids), "reviewed": len(finish_ids),
                    "excluded_ids": [], "errors": 0, "requests": 0,
                    "selected": len(selected_items), "rate_limited": False,
                    "request_budget_reached": False,
                }

            replacements = {
                "REVIEW_COHORT_JSON": path,
                "ensure_seed_files": mock.Mock(),
                "load_config": mock.Mock(return_value={"sources": []}),
                "load_master": mock.Mock(return_value=items),
                "load_public_payload": mock.Mock(return_value={"items": []}),
                "load_review_state": mock.Mock(return_value={}),
                "now_utc": mock.Mock(return_value=NOW),
                "make_http_session": mock.Mock(),
                "refresh_academic_review_summaries": mock.Mock(return_value={
                    "targets": 0, "attempted": 0, "restored": 0, "errors": 0,
                }),
                "select_scope_review_items": review_fresh.select_fresh_only,
                "enrich_japanese_summaries": mock.Mock(side_effect=fake_enrich),
                "save_master": mock.Mock(),
                "publish_outputs": mock.Mock(return_value=({"items": []}, [])),
                "append_run_log": mock.Mock(return_value=[]),
                "update_workbook": mock.Mock(),
                "save_review_state": mock.Mock(),
                "fetch_source": mock.Mock(side_effect=AssertionError("No source collection allowed")),
            }
            for name, value in replacements.items():
                stack.enter_context(mock.patch.object(collect, name, value))
            stack.enter_context(mock.patch.dict(os.environ, {
                "JAPANESE_SUMMARY_BACKFILL_LIMIT": str(limit),
                "JAPANESE_SUMMARY_INCLUDE_EXPIRED": "false",
            }))
            stack.enter_context(mock.patch.object(
                collect.requests.sessions.Session, "request",
                side_effect=AssertionError("No network/API calls allowed"),
            ))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            yield path, selected_ids, replacements

    def test_expired_pinned_member_is_reviewed_and_counts_are_true_union_counts(self):
        items = [candidate("expired-pinned", hours_old=100, published_days_old=400),
                 candidate("fresh-overlap", hours_old=5),
                 candidate("fresh-only", hours_old=1),
                 candidate("unrelated-history", hours_old=80),
                 candidate("unrelated-expired", hours_old=100, published_days_old=400)]
        cohort = manifest("expired-pinned", "fresh-overlap")
        with self.run_context(items, cohort, finish_ids={"expired-pinned"}) as (path, selected, mocks):
            self.assertEqual(collect.review_backlog(365, 183), 0)
            self.assertEqual(selected, ["expired-pinned", "fresh-overlap", "fresh-only"])
            state = mocks["save_review_state"].call_args.args[0]
            self.assertEqual(state["pending_priority_total"], 2)
            self.assertEqual(state["pending_priority_36h"], 2)
            self.assertEqual(state["pending_recovery_cohort"], 1)
            self.assertEqual(state["status"], "in_progress")
            self.assertEqual(json.loads(path.read_text())["active_ids"], ["fresh-overlap"])
            mocks["fetch_source"].assert_not_called()

    def test_only_expired_pinned_pending_keeps_priority_open_without_fresh_items(self):
        items = [candidate("expired-pinned", hours_old=100, published_days_old=400)]
        with self.run_context(items, manifest("expired-pinned")) as (_, selected, mocks):
            self.assertEqual(collect.review_backlog(365, 183), 0)
            self.assertEqual(selected, ["expired-pinned"])
            state = mocks["save_review_state"].call_args.args[0]
            self.assertEqual(state["pending_priority_total"], 1)
            self.assertEqual(state["pending_priority_36h"], 0)
            self.assertEqual(state["pending_recovery_cohort"], 1)
            self.assertNotEqual(state["status"], "completed")

    def test_completion_is_persisted_after_real_review_path(self):
        items = [candidate("pinned", hours_old=50)]
        with self.run_context(items, manifest("pinned"), finish_ids={"pinned"}) as (path, _, mocks):
            self.assertEqual(collect.review_backlog(365, 183), 0)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["original_ids"], ["pinned"])
            self.assertEqual(saved["active_ids"], [])
            self.assertTrue(saved.get("completed_at"))
            state = mocks["save_review_state"].call_args.args[0]
            self.assertEqual(state["pending_priority_total"], 0)
            self.assertEqual(state["pending_recovery_cohort"], 0)
            self.assertEqual(state["status"], "completed")

    def test_missing_active_id_stops_before_review_or_any_checkpoint_write(self):
        items = [candidate("present")]
        with self.run_context(items, manifest("present", "missing")) as (path, selected, mocks):
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                collect.review_backlog(365, 183)
            self.assertEqual(selected, [])
            self.assertEqual(path.read_bytes(), before)
            for operation in ("enrich_japanese_summaries", "make_http_session",
                              "save_master", "save_review_state", "publish_outputs"):
                mocks[operation].assert_not_called()

    def test_disappearing_manifest_fails_closed_when_state_has_pending_cohort(self):
        with self.run_context([candidate("pinned")], manifest("pinned")) as (path, selected, mocks):
            path.unlink()
            mocks["load_review_state"].return_value = {"pending_recovery_cohort": 1}
            with self.assertRaises(ValueError):
                collect.review_backlog(365, 183)
            self.assertFalse(path.exists())
            self.assertEqual(selected, [])
            mocks["enrich_japanese_summaries"].assert_not_called()
            mocks["save_review_state"].assert_not_called()

    def test_empty_checkpoint_path_reconciles_resolved_members_before_early_return(self):
        with self.run_context([reviewed(candidate("pinned"))], manifest("pinned")) as (path, selected, mocks):
            mocks["load_review_state"].return_value = {
                "status": "completed", "review_version": VERSION, "pending_in_window": 0,
            }
            self.assertEqual(collect.review_backlog(365, 183), 0)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["active_ids"], [])
            self.assertTrue(saved.get("completed_at"))
            self.assertEqual(selected, [])
            mocks["enrich_japanese_summaries"].assert_not_called()

    def test_seeded_japanese_fields_are_persisted_before_early_cohort_retirement(self):
        item = candidate(
            "pinned", title="人工知能の新しい研究結果", summary="新しい人工知能技術の研究概要。",
            scope_review_version=VERSION,
        )
        self.assertTrue(collect.needs_scope_review(item))
        real_save_master = collect.save_master
        real_reconcile = review_cohort.reconcile
        with self.run_context([item], manifest("pinned")) as (path, selected, mocks):
            master_path = path.parent / "news.csv"
            mocks["load_review_state"].return_value = {
                "status": "completed", "review_version": VERSION, "pending_in_window": 0,
            }

            def persist_before_pruning(items):
                self.assertEqual(json.loads(path.read_text())["active_ids"], ["pinned"])
                real_save_master(items)

            def reconcile_after_persistence(*args, **kwargs):
                mocks["save_master"].assert_called_once()
                with master_path.open(encoding="utf-8-sig", newline="") as handle:
                    saved = list(csv.DictReader(handle))
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0]["canonical_id"], "pinned")
                self.assertEqual(saved[0]["title_ja"], item["title"])
                self.assertEqual(saved[0]["summary_ja"], item["summary"])
                return real_reconcile(*args, **kwargs)

            mocks["save_master"].side_effect = persist_before_pruning
            output = io.StringIO()
            with mock.patch.object(collect, "DATA_DIR", path.parent), \
                    mock.patch.object(collect, "MASTER_CSV", master_path), \
                    mock.patch.object(review_cohort, "reconcile", side_effect=reconcile_after_persistence) as reconcile_mock, \
                    contextlib.redirect_stdout(output):
                self.assertEqual(collect.review_backlog(365, 183), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "already_complete")
            reconcile_mock.assert_called_once()
            self.assertEqual(json.loads(path.read_text())["active_ids"], [])
            self.assertTrue(json.loads(path.read_text()).get("completed_at"))
            self.assertEqual(selected, [])
            mocks["enrich_japanese_summaries"].assert_not_called()

    def test_real_review_path_still_selects_only_twelve_from_the_original_252(self):
        items = [candidate(f"original-{i:03}", hours_old=300 - i) for i in range(252)]
        cohort = manifest(*item_ids(items))
        with self.run_context(list(reversed(items)), cohort, limit=12) as (path, selected, mocks):
            self.assertEqual(collect.review_backlog(365, 183), 0)
            self.assertEqual(selected, item_ids(items[:12]))
            state = mocks["save_review_state"].call_args.args[0]
            self.assertEqual(state["selected"], 12)
            self.assertEqual(state["pending_priority_total"], 252)
            self.assertEqual(state["pending_priority_36h"], 0)
            self.assertEqual(state["pending_recovery_cohort"], 252)
            self.assertEqual(json.loads(path.read_text())["original_ids"], cohort["original_ids"])


if __name__ == "__main__":
    unittest.main()
