import importlib.util
import json
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect.py"
SPEC = importlib.util.spec_from_file_location("collector", MODULE_PATH)
collector = importlib.util.module_from_spec(SPEC)
sys.modules["collector"] = collector
assert SPEC.loader is not None
SPEC.loader.exec_module(collector)


class CollectorTests(unittest.TestCase):
    def test_exclude_retired_sources_removes_openai_news_only(self):
        items = [
            {"source": "OpenAI News", "title": "retired"},
            {"source": "Science Advances", "title": "OpenAlex venue"},
            {"source": "NEDO News", "title": "active source"},
        ]

        self.assertEqual(
            collector.exclude_retired_sources(items),
            items[1:],
        )

    def test_publication_guard_merges_current_and_previous_without_retired_sources(
        self,
    ):
        current = {
            "article_count": 1,
            "source_count": 260,
            "items": [
                {
                    "id": "current",
                    "source": "NEDO News",
                    "published_at": "2026-07-27T00:00:00Z",
                }
            ],
        }
        previous = {
            "article_count": 3,
            "source_count": 261,
            "items": [
                {
                    "id": "keep-1",
                    "source": "NEDO News",
                    "published_at": "2026-07-26T00:00:00Z",
                },
                {
                    "id": "retire",
                    "source": "OpenAI News",
                    "published_at": "2026-07-25T00:00:00Z",
                },
                {
                    "id": "keep-2",
                    "source": "Science Advances",
                    "published_at": "2026-07-24T00:00:00Z",
                },
            ],
        }

        payload = collector.preserved_public_payload(current, previous)

        self.assertEqual(payload["article_count"], 3)
        self.assertEqual(payload["source_count"], 260)
        self.assertEqual(
            [item["id"] for item in payload["items"]],
            ["current", "keep-1", "keep-2"],
        )

    def test_publication_guard_only_carries_still_unreviewed_master_items(self):
        current = {
            "updated_at": "2026-07-28T00:00:00Z",
            "history_windows": {
                "innovation_policy_days": 365,
                "technology_innovation_days": 183,
            },
            "items": [],
        }
        previous = {
            "items": [
                {
                    "id": "pending",
                    "source": "NEDO News",
                    "published_at": "2026-07-20T00:00:00Z",
                    "article_frames": ["Technology Innovation"],
                    "topics": ["Artificial Intelligence"],
                },
                {
                    "id": "excluded",
                    "source": "NEDO News",
                    "published_at": "2026-07-19T00:00:00Z",
                    "article_frames": ["Technology Innovation"],
                    "topics": ["Artificial Intelligence"],
                },
            ]
        }
        master = [
            {
                "canonical_id": "pending",
                "scope_review_version": "old-version",
            },
            {
                "canonical_id": "excluded",
                "scope_review_version": collector.TECH_SCOPE_REVIEW_VERSION,
                "status": "Excluded",
            },
        ]

        payload = collector.preserved_public_payload(
            current,
            previous,
            master,
        )

        self.assertEqual([item["id"] for item in payload["items"]], ["pending"])

    def test_publication_guard_prefers_current_version_of_duplicate_item(self):
        current = {
            "items": [
                {
                    "id": "same",
                    "source": "NEDO News",
                    "title": "更新版",
                    "url": "https://example.com/same",
                    "published_at": "2026-07-27T00:00:00Z",
                }
            ]
        }
        previous = {
            "items": [
                {
                    "id": "same",
                    "source": "NEDO News",
                    "title": "旧版",
                    "url": "https://example.com/old",
                    "published_at": "2026-07-27T00:00:00Z",
                }
            ]
        }

        payload = collector.preserved_public_payload(current, previous)

        self.assertEqual(payload["article_count"], 1)
        self.assertEqual(payload["items"][0]["title"], "更新版")

    def test_publication_guard_deduplicates_matching_canonical_url(self):
        current = {
            "items": [
                {
                    "id": "current-id",
                    "source": "NEDO News",
                    "title": "更新版",
                    "url": "https://example.com/article?utm_source=daily",
                }
            ]
        }
        previous = {
            "items": [
                {
                    "id": "previous-id",
                    "source": "NEDO News",
                    "title": "旧版",
                    "url": "https://example.com/article",
                }
            ]
        }

        payload = collector.preserved_public_payload(current, previous)

        self.assertEqual(payload["article_count"], 1)
        self.assertEqual(payload["items"][0]["id"], "current-id")

    def test_publication_guard_propagates_transitive_id_and_url_aliases(self):
        current = {
            "items": [
                {
                    "id": "current-id",
                    "source": "NEDO News",
                    "url": "https://example.com/article-a",
                }
            ]
        }
        previous = {
            "items": [
                {
                    "id": "previous-id",
                    "source": "NEDO News",
                    "url": "https://example.com/article-a",
                },
                {
                    "id": "previous-id",
                    "source": "NEDO News",
                    "url": "https://example.com/article-b",
                },
            ]
        }

        payload = collector.preserved_public_payload(current, previous)

        self.assertEqual(payload["article_count"], 1)
        self.assertEqual(payload["items"][0]["id"], "current-id")

    def test_publication_guard_drops_items_outside_history_window(self):
        current = {
            "updated_at": "2026-07-27T00:00:00Z",
            "history_windows": {
                "innovation_policy_days": 365,
                "technology_innovation_days": 183,
            },
            "items": [],
        }
        previous = {
            "items": [
                {
                    "id": "recent",
                    "source": "NEDO News",
                    "published_at": "2026-07-26T00:00:00Z",
                    "article_frames": ["Technology Innovation"],
                },
                {
                    "id": "expired",
                    "source": "NEDO News",
                    "published_at": "2025-01-01T00:00:00Z",
                    "article_frames": ["Technology Innovation"],
                },
            ]
        }

        payload = collector.preserved_public_payload(current, previous)

        self.assertEqual(payload["article_count"], 1)
        self.assertEqual(payload["items"][0]["id"], "recent")

    def test_guard_hydrates_ledger_from_purged_master(self):
        public_items = [
            {
                "id": "keep",
                "source": "NEDO News",
                "title": "公開見出し",
                "title_original": "Original title",
                "summary": "公開要約",
                "summary_original": "Original summary",
            }
        ]
        master_items = [
            {
                "canonical_id": "keep",
                "source": "NEDO News",
                "collected_at_jst": "2026-07-27T12:00:00+09:00",
                "title": "Master title",
                "summary": "Master summary",
            }
        ]

        hydrated = collector.hydrate_preserved_ledger_items(
            public_items,
            master_items,
        )

        self.assertEqual(hydrated[0]["canonical_id"], "keep")
        self.assertEqual(
            hydrated[0]["collected_at_jst"],
            "2026-07-27T12:00:00+09:00",
        )
        self.assertEqual(hydrated[0]["title_ja"], "公開見出し")
        self.assertEqual(hydrated[0]["summary_ja"], "公開要約")
        self.assertEqual(hydrated[0]["title"], "Original title")
        self.assertEqual(hydrated[0]["summary"], "Original summary")

    def test_run_fetches_sources_concurrently_and_preserves_source_order(self):
        sources = [
            {
                "active": True,
                "name": f"Source {index}",
                "organization": f"Organization {index}",
                "source_type": "Official Company",
                "region": "Global",
                "country": "Global",
                "category": "Artificial intelligence research",
                "feed_url": f"https://source{index}.example.com/feed",
                "homepage": f"https://source{index}.example.com/",
                "priority": 4,
            }
            for index in range(4)
        ]
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        def fake_fetch(
            session,
            source,
            cutoff,
            collected_at,
            backfill=False,
        ):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return [], collector.FeedResult(
                source=source,
                entries_seen=0,
                entries_kept=0,
                status="ok",
                detail="",
                elapsed_seconds=0.03,
            )

        academic_result = {
            "targets": 0,
            "attempted": 0,
            "restored": 0,
            "errors": 0,
        }
        summary_result = {
            "generated": 0,
            "reviewed": 0,
            "excluded_ids": [],
            "pending": 0,
            "errors": 0,
            "detail": "",
        }
        with (
            mock.patch.dict(os.environ, {"SOURCE_FETCH_WORKERS": "4"}),
            mock.patch.object(collector, "ensure_seed_files"),
            mock.patch.object(
                collector,
                "load_config",
                return_value={"sources": sources},
            ),
            mock.patch.object(collector, "load_master", return_value=[]),
            mock.patch.object(
                collector,
                "load_backfill_state",
                return_value={
                    "cadences": {
                        "daily": {
                            "backfill_version": collector.BACKFILL_VERSION,
                        }
                    }
                },
            ),
            mock.patch.object(
                collector,
                "now_utc",
                return_value=datetime(2026, 7, 26, tzinfo=timezone.utc),
            ),
            mock.patch.object(
                collector,
                "fetch_source",
                side_effect=fake_fetch,
            ),
            mock.patch.object(
                collector,
                "refresh_academic_review_summaries",
                return_value=academic_result,
            ),
            mock.patch.object(
                collector,
                "enrich_japanese_summaries",
                return_value=summary_result,
            ),
            mock.patch.object(collector, "save_master"),
            mock.patch.object(
                collector,
                "publish_outputs",
                return_value=({"items": []}, []),
            ),
            mock.patch.object(collector, "save_source_status") as save_status,
            mock.patch.object(collector, "save_backfill_state"),
            mock.patch.object(collector, "append_run_log", return_value=[]),
            mock.patch.object(collector, "update_workbook"),
        ):
            result = collector.run(96, 365, 183)

        self.assertEqual(result, 0)
        self.assertGreaterEqual(maximum_active, 2)
        saved_results = save_status.call_args.args[0]
        self.assertEqual(
            [result.source["name"] for result in saved_results],
            [source["name"] for source in sources],
        )

    def test_balanced_review_selection_prioritizes_underreviewed_sources(self):
        def pending(item_id, source, published):
            return {
                "canonical_id": item_id,
                "source": source,
                "published_at": published,
                "scope_review_version": "old-version",
            }

        items = [
            pending("a-1", "Source A", "2026-07-28T00:00:00Z"),
            pending("a-2", "Source A", "2026-07-27T00:00:00Z"),
            pending("b-1", "Source B", "2026-07-26T00:00:00Z"),
            pending("c-1", "Source C", "2026-07-25T00:00:00Z"),
        ]
        items.extend(
            {
                "canonical_id": f"a-reviewed-{index}",
                "source": "Source A",
                "status": "Excluded",
                "scope_review_version": collector.TECH_SCOPE_REVIEW_VERSION,
            }
            for index in range(5)
        )

        selected = collector.select_scope_review_items(
            items,
            [],
            limit=3,
            balanced=True,
        )

        self.assertEqual(
            {item["source"] for item in selected[:2]},
            {"Source B", "Source C"},
        )
        self.assertEqual(selected[2]["source"], "Source A")

    def test_balanced_review_selection_resolves_public_pending_first(self):
        items = [
            {
                "canonical_id": "backlog-newer",
                "source": "Source B",
                "published_at": "2026-07-28T00:00:00Z",
                "scope_review_version": "old-version",
            },
            {
                "canonical_id": "public-older",
                "source": "Source A",
                "published_at": "2026-07-20T00:00:00Z",
                "scope_review_version": "old-version",
            },
        ]

        selected = collector.select_scope_review_items(
            items,
            [],
            limit=1,
            balanced=True,
            priority_ids={"public-older"},
        )

        self.assertEqual(selected[0]["canonical_id"], "public-older")

    def test_taxonomy_has_eight_technology_topics_and_separate_policy_axis(self):
        self.assertEqual(len(collector.TOPIC_KEYWORDS), 8)
        self.assertIn("Space", collector.TOPIC_KEYWORDS)
        self.assertNotIn("Innovation Policy", collector.TOPIC_KEYWORDS)
        self.assertIn("Patents & Intellectual Property", collector.POLICY_AREA_KEYWORDS)

    def test_only_current_reviewed_nonexcluded_items_are_publishable(self):
        item = {
            "status": "New",
            "scope_review_version": collector.TECH_SCOPE_REVIEW_VERSION,
            "title_ja": "量子技術の新成果",
            "summary_ja": "新しい量子制御手法を実証した。",
            "article_frames": ["Technology Innovation"],
            "topics": ["Quantum"],
        }
        self.assertTrue(collector.is_publishable(item))
        item["status"] = "Excluded"
        self.assertFalse(collector.is_publishable(item))
        self.assertFalse(collector.needs_scope_review(item))
        item["scope_review_version"] = "older-review"
        self.assertTrue(collector.needs_scope_review(item))

    def test_current_review_with_missing_topic_is_queued_for_taxonomy_repair(self):
        item = {
            "status": "New",
            "scope_review_version": collector.TECH_SCOPE_REVIEW_VERSION,
            "title_ja": "新しい学会論文",
            "summary_ja": "新手法を提案した。",
            "article_frames": ["Technology Innovation"],
            "topics": [],
        }

        self.assertTrue(collector.needs_taxonomy_repair(item))
        self.assertTrue(collector.needs_scope_review(item))

        selected = collector.select_scope_review_items(
            [item],
            [],
            limit=1,
            balanced=True,
        )
        self.assertEqual(selected, [item])

    def test_reviewed_topics_require_article_evidence_not_source_category(self):
        item = {
            "status": "New",
            "scope_review_version": collector.TECH_SCOPE_REVIEW_VERSION,
            "title": "National project accelerates AI-driven scientific discovery",
            "summary": "The programme funds artificial intelligence research infrastructure.",
            "topics": ["Quantum"],
            "topic": "Quantum",
            "candidate_from_source_topic_tags": True,
            "scope_content_type": "research_breakthrough",
            "scope_focus": "量子技術",
            "scope_evidence": "The programme funds artificial intelligence research infrastructure.",
        }
        collector.normalize_reviewed_topics([item])
        self.assertEqual(item["topics"], ["Artificial Intelligence"])
        self.assertEqual(item["topic"], "Artificial Intelligence")

    def test_structured_review_preserves_niche_model_topic_without_keyword_match(self):
        item = {
            "status": "New",
            "scope_review_version": collector.TECH_SCOPE_REVIEW_VERSION,
            "title": "ProAR: Probabilistic Autoregressive Modeling for Molecular Dynamics",
            "summary": "",
            "topics": ["Artificial Intelligence"],
            "topic": "Artificial Intelligence",
            "article_frames": ["Technology Innovation"],
            "scope_content_type": "conference_paper",
            "scope_focus": "ProARによる分子動力学のモデリング",
            "scope_evidence": "確率的自己回帰フレームワークで軌道を生成する。",
        }

        collector.normalize_reviewed_topics([item])

        self.assertEqual(item["topics"], ["Artificial Intelligence"])
        self.assertEqual(item["topic"], "Artificial Intelligence")

    def test_structured_review_keeps_only_primary_unsupported_model_topic(self):
        item = {
            "status": "New",
            "scope_review_version": collector.TECH_SCOPE_REVIEW_VERSION,
            "title": "ProAR: Probabilistic Autoregressive Modeling for Molecular Dynamics",
            "summary": "",
            "topics": ["Artificial Intelligence", "Robotics"],
            "topic": "Artificial Intelligence | Robotics",
            "article_frames": ["Technology Innovation"],
            "scope_content_type": "conference_paper",
            "scope_focus": "ProARによる分子動力学のモデリング",
            "scope_evidence": "確率的自己回帰フレームワークで軌道を生成する。",
        }

        collector.normalize_reviewed_topics([item])

        self.assertEqual(item["topics"], ["Artificial Intelligence"])
        self.assertEqual(item["topic"], "Artificial Intelligence")
        self.assertFalse(collector.needs_taxonomy_repair(item))

    def test_unsubstantiated_model_topic_is_removed_without_structured_evidence(self):
        item = {
            "status": "New",
            "scope_review_version": collector.TECH_SCOPE_REVIEW_VERSION,
            "title": "Company announces a milestone",
            "summary": "The company shared an update.",
            "topics": ["Artificial Intelligence"],
            "topic": "Artificial Intelligence",
            "article_frames": ["Technology Innovation"],
            "candidate_from_source_topic_tags": True,
        }

        collector.normalize_reviewed_topics([item])

        self.assertEqual(item["topics"], [])
        self.assertEqual(item["topic"], "")

    def test_private_fundraising_is_not_innovation_policy(self):
        item = {
            "status": "New",
            "scope_review_version": collector.TECH_SCOPE_REVIEW_VERSION,
            "title": "Fusion startup raises JPY 6.06 billion",
            "summary": "The company will accelerate development of its tokamak device.",
            "topics": ["Fusion Energy"],
            "topic": "Fusion Energy",
            "article_frames": ["Innovation Policy"],
            "article_frame": "Innovation Policy",
            "innovation_policy": True,
            "policy_areas": ["R&D Funding & Tax Incentives"],
            "policy_area": "R&D Funding & Tax Incentives",
        }
        collector.normalize_reviewed_policy_axis([item])
        self.assertFalse(item["innovation_policy"])
        self.assertEqual(item["policy_areas"], [])
        self.assertEqual(item["article_frames"], ["Technology Innovation"])

    def test_canonicalize_url_removes_tracking(self):
        actual = collector.canonicalize_url(
            "https://www.example.com/story/?utm_source=rss&b=2&a=1#section"
        )
        self.assertEqual(actual, "https://example.com/story?a=1&b=2")

    def test_topic_classification_is_multilabel(self):
        topics = collector.classify_topics(
            "Government launches a national quantum computing funding programme "
            "for semiconductor research and satellite communications."
        )
        self.assertIn("Quantum", topics)
        self.assertIn("Semiconductors & Telecom", topics)
        self.assertIn("Space", topics)

    def test_source_cadence_defaults_to_daily_and_selects_due_sources(self):
        sources = [
            {"active": True, "name": "Legacy"},
            {"active": True, "name": "Daily", "cadence": "daily"},
            {"active": True, "name": "Weekly", "cadence": "weekly"},
            {"active": True, "name": "Tier B", "coverage_tier": "B"},
            {"active": True, "name": "Legacy B", "priority": 3},
            {"active": False, "name": "Inactive", "cadence": "daily"},
        ]
        self.assertEqual(
            [source["name"] for source in collector.sources_for_cadence(sources, "daily")],
            ["Legacy", "Daily"],
        )
        self.assertEqual(
            [source["name"] for source in collector.sources_for_cadence(sources, "weekly")],
            ["Weekly", "Tier B", "Legacy B"],
        )

    def test_strict_relevance_uses_source_tags_only_as_review_hints(self):
        source = {
            "name": "Example Research",
            "organization": "Example",
            "source_type": "Official Company",
            "region": "Global",
            "country": "Global",
            "category": "Artificial intelligence and quantum research",
            "priority": 4,
            "strict_relevance": True,
            "topic_tags": ["Artificial Intelligence", "Quantum"],
        }
        item = collector.build_item(
            source,
            "Company appoints a new chief financial officer",
            "https://example.com/news/cfo",
            "The appointment takes effect next month.",
            datetime(2026, 7, 26, tzinfo=timezone.utc),
            datetime(2026, 7, 27, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(item)
        self.assertTrue(item["candidate_from_source_topic_tags"])
        self.assertEqual(
            item["topics"],
            ["Artificial Intelligence", "Quantum"],
        )

    def test_source_coverage_tier_supports_explicit_and_legacy_values(self):
        self.assertEqual(collector.source_coverage_tier({"coverage_tier": "S"}), "S")
        self.assertEqual(collector.source_coverage_tier({"priority": 4}), "A")
        self.assertEqual(collector.source_coverage_tier({"priority": 3}), "B")

    def test_invalid_tier_and_cadence_are_not_silently_accepted(self):
        with self.assertRaises(ValueError):
            collector.source_coverage_tier(
                {"name": "Typo", "coverage_tier": "C"}
            )
        with self.assertRaises(ValueError):
            collector.source_cadence(
                {"name": "Typo", "cadence": "wekly"}
            )

    def test_company_and_a_tier_sources_default_to_strict_relevance(self):
        self.assertTrue(
            collector.source_requires_strict_relevance(
                {"source_type": "Official Company", "priority": 5}
            )
        )
        self.assertTrue(
            collector.source_requires_strict_relevance(
                {"source_type": "Major Media", "priority": 4}
            )
        )
        self.assertFalse(
            collector.source_requires_strict_relevance(
                {
                    "source_type": "Government",
                    "priority": 5,
                    "strict_relevance": False,
                }
            )
        )

    def test_source_topic_tags_infer_multi_topic_company_remit(self):
        self.assertEqual(
            collector.source_topic_tags(
                {
                    "category": "AI, semiconductors, robotics and quantum",
                }
            ),
            [
                "Robotics",
                "Artificial Intelligence",
                "Semiconductors & Telecom",
                "Quantum",
            ],
        )
        self.assertEqual(
            collector.source_topic_tags(
                {"category": "Space sustainability and on-orbit servicing"}
            ),
            ["Space"],
        )

    def test_model_review_can_confirm_a_product_name_from_source_topic_hints(self):
        item = {
            "status": "New",
            "scope_review_version": collector.TECH_SCOPE_REVIEW_VERSION,
            "title": "GPT-6 reaches a new scientific reasoning milestone",
            "summary": "The release improves experimental planning and tool use.",
            "topics": ["Artificial Intelligence"],
            "topic": "Artificial Intelligence",
            "article_frames": ["Technology Innovation"],
            "candidate_from_source_topic_tags": True,
            "scope_evidence": "Improves experimental planning and tool use.",
            "scope_content_type": "engineering_development",
            "scope_focus": "GPT-6の科学推論能力",
        }
        collector.normalize_reviewed_topics([item])
        self.assertEqual(item["topics"], ["Artificial Intelligence"])

    def test_backfill_version_is_tracked_separately_by_cadence(self):
        state = {
            "cadences": {
                "daily": {"backfill_version": collector.BACKFILL_VERSION},
            }
        }
        self.assertEqual(
            collector.cadence_backfill_version(state, "daily"),
            collector.BACKFILL_VERSION,
        )
        self.assertEqual(
            collector.cadence_backfill_version(state, "weekly"),
            0,
        )
        self.assertEqual(
            collector.cadence_backfill_version(
                {"backfill_version": collector.BACKFILL_VERSION},
                "daily",
            ),
            0,
        )

    def test_source_status_keeps_the_other_cadence_last_result(self):
        daily_source = {
            "active": True,
            "name": "Daily",
            "organization": "Daily Org",
            "source_type": "Government",
            "region": "Asia",
            "homepage": "https://daily.example.com/",
            "feed_url": "https://daily.example.com/feed",
            "priority": 5,
            "coverage_tier": "S",
            "cadence": "daily",
            "category": "Artificial intelligence",
        }
        weekly_source = {
            "active": True,
            "name": "Weekly",
            "organization": "Weekly Org",
            "source_type": "Scientific Publication",
            "region": "Global",
            "homepage": "https://weekly.example.com/",
            "feed_url": "https://weekly.example.com/feed",
            "priority": 3,
            "coverage_tier": "B",
            "cadence": "weekly",
            "category": "Quantum",
        }
        previous = {
            "updated_at": "2026-07-26T00:00:00Z",
            "sources": [
                {
                    "name": "Weekly",
                    "status": "ok",
                    "entries_seen": 3,
                    "entries_kept": 1,
                    "elapsed_seconds": 0.5,
                },
                {
                    "name": "OpenAI News",
                    "status": "ok",
                    "entries_seen": 5,
                    "entries_kept": 2,
                    "elapsed_seconds": 0.5,
                }
            ],
        }
        result = collector.FeedResult(
            source=daily_source,
            entries_seen=4,
            entries_kept=2,
            status="ok",
            detail="",
            elapsed_seconds=0.4,
        )
        payload = collector.source_status_payload(
            [result],
            datetime(2026, 7, 27, tzinfo=timezone.utc),
            sources=[daily_source, weekly_source],
            previous_payload=previous,
        )
        by_name = {entry["name"]: entry for entry in payload["sources"]}
        self.assertEqual(by_name["Daily"]["entries_kept"], 2)
        self.assertEqual(by_name["Weekly"]["entries_kept"], 1)
        self.assertEqual(
            by_name["Weekly"]["last_checked_at"],
            "2026-07-26T00:00:00Z",
        )
        self.assertNotIn("OpenAI News", by_name)
        self.assertEqual(payload["coverage_summary"]["registered"], 2)
        self.assertEqual(payload["coverage_summary"]["checked_once"], 2)
        self.assertEqual(payload["summary"]["checked"], 2)
        self.assertEqual(payload["summary"]["succeeded"], 2)
        self.assertEqual(payload["current_run"]["checked"], 1)

    def test_policy_classification_is_separate_from_technology_topics(self):
        text = (
            "The government expands its R&D tax credit, launches a national project, "
            "and reforms patent licensing."
        )
        self.assertEqual(collector.classify_topics(text), [])
        policy_areas = collector.classify_policy_areas(text)
        self.assertIn("R&D Funding & Tax Incentives", policy_areas)
        self.assertIn("National Programs & Strategy", policy_areas)
        self.assertIn("Patents & Intellectual Property", policy_areas)

    def test_japanese_integrated_innovation_strategy_is_policy(self):
        self.assertIn(
            "National Programs & Strategy",
            collector.classify_policy_areas("統合イノベーション戦略2026"),
        )

    def test_region_classification_overrides_global_default(self):
        region = collector.classify_region(
            "The European Commission announced a new AI investment initiative.",
            "Global",
        )
        self.assertEqual(region, "EU & Europe")

    def test_deduplication_uses_url_and_title(self):
        existing = [
            {
                "canonical_id": "old-id",
                "url": "https://example.com/a",
                "title": "Government launches national quantum strategy",
            }
        ]
        duplicate_url = {
            "canonical_id": "new-id",
            "canonical_url": "https://example.com/a",
            "title": "A different title",
            "title_fingerprint": collector.title_fingerprint("A different title"),
        }
        duplicate_title = {
            "canonical_id": "new-id-2",
            "canonical_url": "https://example.com/b",
            "title": "Government launches national quantum strategy",
            "title_fingerprint": collector.title_fingerprint(
                "Government launches national quantum strategy"
            ),
        }
        unique = {
            "canonical_id": "new-id-3",
            "canonical_url": "https://example.com/c",
            "title": "New fusion pilot reaches milestone",
            "title_fingerprint": collector.title_fingerprint(
                "New fusion pilot reaches milestone"
            ),
        }
        added, skipped = collector.deduplicate(
            [duplicate_url, duplicate_title, unique], existing
        )
        self.assertEqual(skipped, 2)
        self.assertEqual([item["canonical_id"] for item in added], ["new-id-3"])

    def test_public_item_prefers_japanese_summary(self):
        item = {
            "canonical_id": "article-1",
            "title": "New quantum programme announced",
            "title_ja": "量子技術の新プログラムを発表",
            "summary": "The government announced a new programme.",
            "summary_ja": "政府が量子技術を支援する新プログラムを発表した。",
            "article_frames": ["Innovation Policy"],
            "innovation_policy": True,
            "policy_areas": ["National Programs & Strategy"],
            "policy_relevance": 4,
        }
        public = collector.public_item(item)
        self.assertEqual(public["title"], item["title_ja"])
        self.assertEqual(public["title_original"], item["title"])
        self.assertEqual(public["summary"], item["summary_ja"])
        self.assertEqual(public["summary_original"], item["summary"])
        self.assertEqual(public["summary_language"], "ja")
        self.assertEqual(public["article_frames"], ["Innovation Policy"])
        self.assertEqual(public["policy_areas"], ["National Programs & Strategy"])

    def test_parse_japanese_summary_response(self):
        raw = json.dumps(
            {
                "items": [
                    {
                        "id": "article-1",
                        "in_scope": True,
                        "topics": ["Quantum"],
                        "is_innovation_policy": True,
                        "policy_areas": [
                            "National Programs & Strategy",
                            "R&D Funding & Tax Incentives",
                        ],
                        "policy_relevance": 5,
                        "reason": "量子研究開発政策を直接扱う。",
                        "content_type": "technology_policy",
                        "technical_focus": "量子技術の研究開発支援",
                        "scope_evidence": "政府が量子研究開発支援を拡充する。",
                        "title_ja": "量子技術の新計画",
                        "summary_ja": "政府が研究開発支援を拡充する。",
                    },
                    {
                        "id": "unknown",
                        "title_ja": "対象外",
                        "summary_ja": "対象外の記事。",
                    },
                ]
            },
            ensure_ascii=False,
        )
        parsed = collector.parse_japanese_summary_response(raw, {"article-1"})
        self.assertEqual(set(parsed), {"article-1"})
        self.assertEqual(parsed["article-1"]["title_ja"], "量子技術の新計画")
        self.assertTrue(parsed["article-1"]["in_scope"])
        self.assertEqual(parsed["article-1"]["topics"], ["Quantum"])
        self.assertTrue(parsed["article-1"]["is_innovation_policy"])
        self.assertEqual(
            parsed["article-1"]["policy_areas"],
            ["National Programs & Strategy", "R&D Funding & Tax Incentives"],
        )
        self.assertEqual(parsed["article-1"]["policy_relevance"], 5)

    def test_parse_scope_review_excludes_general_news(self):
        raw = json.dumps(
            {
                "items": [
                    {
                        "id": "article-1",
                        "in_scope": False,
                        "topics": [],
                        "is_innovation_policy": False,
                        "policy_areas": [],
                        "policy_relevance": 0,
                        "reason": "技術革新ではなく一般的な観光記事。",
                        "content_type": "none",
                        "technical_focus": "",
                        "scope_evidence": "",
                        "title_ja": "",
                        "summary_ja": "",
                    }
                ]
            },
            ensure_ascii=False,
        )
        parsed = collector.parse_japanese_summary_response(raw, {"article-1"})
        self.assertFalse(parsed["article-1"]["in_scope"])
        self.assertEqual(parsed["article-1"]["topics"], [])

    def test_parse_policy_only_article_without_technology_topic(self):
        raw = json.dumps(
            {
                "items": [
                    {
                        "id": "article-1",
                        "in_scope": True,
                        "topics": [],
                        "is_innovation_policy": True,
                        "policy_areas": ["Patents & Intellectual Property"],
                        "policy_relevance": 5,
                        "reason": "研究成果の特許化と技術移転制度を直接扱う。",
                        "content_type": "technology_policy",
                        "technical_focus": "大学研究の特許・技術移転制度",
                        "scope_evidence": "大学の特許ライセンス制度を改正する。",
                        "title_ja": "大学特許の技術移転制度を改正",
                        "summary_ja": "政府が大学研究の特許化とライセンス制度を改正する。",
                    }
                ]
            },
            ensure_ascii=False,
        )
        parsed = collector.parse_japanese_summary_response(raw, {"article-1"})
        self.assertTrue(parsed["article-1"]["in_scope"])
        self.assertEqual(parsed["article-1"]["topics"], [])
        self.assertEqual(
            parsed["article-1"]["policy_areas"],
            ["Patents & Intellectual Property"],
        )

    def test_build_item_applies_source_url_allowlist(self):
        source = {
            "name": "Example Technology",
            "organization": "Example",
            "source_type": "Major Media",
            "region": "Asia",
            "country": "Hong Kong",
            "priority": 4,
            "include_url_patterns": ["/tech/"],
        }
        entry = {
            "title": "New AI accelerator architecture",
            "link": "https://example.com/news/general-story",
            "summary": "A new AI accelerator architecture was announced.",
            "published": "2026-07-26T00:00:00Z",
        }
        self.assertIsNone(
            collector.build_item(
                source,
                entry["title"],
                entry["link"],
                entry["summary"],
                collector.now_utc(),
                collector.now_utc(),
            )
        )

    def test_build_item_accepts_cross_cutting_innovation_policy(self):
        source = {
            "name": "Policy Office",
            "organization": "Policy Office",
            "source_type": "Government",
            "region": "Asia",
            "country": "Japan",
            "category": "Science, technology and innovation policy",
            "priority": 5,
        }
        item = collector.build_item(
            source,
            "Government expands R&D tax credit and patent licensing",
            "https://example.go.jp/policy/rd-tax-patents",
            "The reform supports research investment and technology transfer.",
            collector.now_utc(),
            collector.now_utc(),
        )
        self.assertIsNotNone(item)
        self.assertEqual(item["topics"], [])
        self.assertTrue(item["innovation_policy"])
        self.assertIn("R&D Funding & Tax Incentives", item["policy_areas"])

    def test_public_history_windows_differ_by_frame(self):
        collected_at = datetime(2026, 7, 26, tzinfo=timezone.utc)
        policy_item = {
            "published_at": "2025-08-01T00:00:00Z",
            "article_frames": ["Innovation Policy"],
        }
        technology_item = {
            "published_at": "2025-08-01T00:00:00Z",
            "article_frames": ["Technology Innovation"],
        }
        recent_technology_item = {
            "published_at": "2026-02-01T00:00:00Z",
            "article_frames": ["Technology Innovation"],
        }
        self.assertTrue(
            collector.item_within_public_window(policy_item, collected_at)
        )
        self.assertFalse(
            collector.item_within_public_window(technology_item, collected_at)
        )
        self.assertTrue(
            collector.item_within_public_window(
                recent_technology_item, collected_at
            )
        )

    def test_openalex_metadata_summary_uses_topics_and_keywords(self):
        summary = collector.openalex_metadata_summary(
            {
                "topics": [{"display_name": "Quantum computing"}],
                "keywords": [
                    {"display_name": "Qubit"},
                    {"display_name": "Quantum computing"},
                ],
            }
        )
        self.assertEqual(
            summary,
            "OpenAlex research topics: Quantum computing; Qubit",
        )

    def test_openalex_abstract_restores_word_order(self):
        abstract = collector.openalex_abstract(
            {
                "abstract_inverted_index": {
                    "quantum": [1],
                    "We": [0],
                    "control.": [3],
                    "demonstrate": [2],
                }
            }
        )
        self.assertEqual(abstract, "We quantum demonstrate control.")

    def test_academic_item_requires_abstract_based_review(self):
        item = {
            "status": "New",
            "scope_review_version": collector.TECH_SCOPE_REVIEW_VERSION,
            "academic_kind": collector.ACADEMIC_KIND_JOURNAL,
            "academic_review_version": "",
            "title_ja": "量子制御の新手法",
            "summary_ja": "量子制御の新手法を実証した。",
            "article_frames": ["Technology Innovation"],
            "topics": ["Quantum"],
        }
        self.assertTrue(collector.needs_scope_review(item))
        self.assertFalse(collector.is_publishable(item))
        item[
            "academic_review_version"
        ] = collector.ACADEMIC_SCOPE_REVIEW_VERSION
        self.assertFalse(collector.needs_scope_review(item))
        self.assertTrue(collector.is_publishable(item))

    def test_deduplication_passes_academic_abstract_to_existing_item(self):
        existing = [
            {
                "canonical_id": "old-id",
                "url": "None",
                "doi": "None",
                "title": "A quantum conference paper",
            }
        ]
        candidate = {
            "canonical_id": "new-id",
            "canonical_url": "https://openalex.org/W123",
            "url": "https://openalex.org/W123",
            "doi": "",
            "title": "A quantum conference paper",
            "title_fingerprint": collector.title_fingerprint(
                "A quantum conference paper"
            ),
            "_review_summary": "We demonstrate a new quantum control method.",
        }
        added, skipped = collector.deduplicate([candidate], existing)
        self.assertEqual(added, [])
        self.assertEqual(skipped, 1)
        self.assertEqual(
            existing[0]["_review_summary"],
            "We demonstrate a new quantum control method.",
        )
        self.assertEqual(existing[0]["url"], "https://openalex.org/W123")

    def test_deduplication_prefers_curated_policy_benchmark(self):
        existing = [
            {
                "canonical_id": "old-id",
                "url": "https://example.gov/policy",
                "title": "National science strategy",
                "status": "Excluded",
                "scope_review_version": "older-review",
            }
        ]
        candidate = {
            "canonical_id": "new-id",
            "canonical_url": "https://example.gov/policy",
            "url": "https://example.gov/policy",
            "title": "National science strategy",
            "title_fingerprint": collector.title_fingerprint(
                "National science strategy"
            ),
            "title_ja": "国家科学戦略",
            "summary_ja": "政府が国家科学戦略を決定した。",
            "topics": [],
            "topic": "",
            "article_frames": ["Innovation Policy"],
            "article_frame": "Innovation Policy",
            "innovation_policy": True,
            "policy_areas": ["National Programs & Strategy"],
            "policy_area": "National Programs & Strategy",
            "policy_relevance": 5,
            "status": "New",
            "scope_review_version": collector.TECH_SCOPE_REVIEW_VERSION,
            "scope_content_type": "technology_policy",
            "scope_reason": "正式な国家戦略。",
            "scope_focus": "国家科学戦略",
            "scope_evidence": "政府が決定した。",
            "pinned_policy_benchmark": True,
        }
        added, skipped = collector.deduplicate([candidate], existing)
        self.assertEqual(added, [])
        self.assertEqual(skipped, 1)
        self.assertEqual(existing[0]["status"], "New")
        self.assertEqual(
            existing[0]["scope_review_version"],
            collector.TECH_SCOPE_REVIEW_VERSION,
        )
        self.assertEqual(existing[0]["title_ja"], "国家科学戦略")

    def test_response_decoder_detects_utf8_for_japanese_pages(self):
        response = collector.requests.Response()
        response.status_code = 200
        response._content = "統合イノベーション戦略2026".encode("utf-8")
        response.encoding = "ISO-8859-1"
        self.assertEqual(
            collector.decoded_response_text(response),
            "統合イノベーション戦略2026",
        )

    def test_static_policy_benchmarks_are_reviewed_and_publishable(self):
        source = next(
            source
            for source in collector.load_config()["sources"]
            if source["name"] == "NSF Policy Benchmarks"
        )
        items, result = collector.fetch_static_source(
            source,
            datetime(2025, 7, 26, tzinfo=timezone.utc),
            datetime(2026, 7, 26, tzinfo=timezone.utc),
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(items), 2)
        self.assertTrue(all(collector.is_publishable(item) for item in items))
        self.assertTrue(
            any("NSF X-Labs" in item["title"] for item in items)
        )

    def test_academic_abstract_is_rehydrated_before_review(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "results": [
                        {
                            "abstract_inverted_index": {
                                "We": [0],
                                "demonstrate": [1],
                                "a": [2],
                                "new": [3],
                                "quantum": [4],
                                "control": [5],
                                "method.": [6],
                            }
                        }
                    ]
                }

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

        item = {
            "academic_kind": collector.ACADEMIC_KIND_JOURNAL,
            "academic_review_version": "older-review",
            "doi": "https://doi.org/10.1234/example",
        }
        result = collector.refresh_academic_review_summaries(
            FakeSession(),
            [item],
        )
        self.assertEqual(result["restored"], 1)
        self.assertEqual(
            item["_review_summary"],
            "We demonstrate a new quantum control method.",
        )

    def test_summary_review_retries_items_omitted_by_model(self):
        items = [
            {
                "canonical_id": "article-1",
                "title": "New AI method one",
                "summary": "Researchers demonstrate a new AI method.",
                "source": "Example",
                "source_type": "Journal Article",
                "academic_kind": collector.ACADEMIC_KIND_NEWS,
                "region": "Global",
                "topics": ["Artificial Intelligence"],
                "policy_areas": [],
                "policy_relevance": 0,
                "status": "New",
            },
            {
                "canonical_id": "article-2",
                "title": "New AI method two",
                "summary": "Researchers demonstrate another AI method.",
                "source": "Example",
                "source_type": "Journal Article",
                "academic_kind": collector.ACADEMIC_KIND_NEWS,
                "region": "Global",
                "topics": ["Artificial Intelligence"],
                "policy_areas": [],
                "policy_relevance": 0,
                "status": "New",
            },
        ]

        def translated(item_id):
            return {
                "in_scope": True,
                "topics": ["Artificial Intelligence"],
                "is_innovation_policy": False,
                "policy_areas": [],
                "policy_relevance": 0,
                "reason": "AIの具体的な新手法。",
                "content_type": "research_breakthrough",
                "technical_focus": "AIの新手法",
                "scope_evidence": "新手法を実証した。",
                "title_ja": f"AI新手法 {item_id}",
                "summary_ja": "研究チームがAIの新手法を実証した。",
            }

        calls = []

        def fake_request(batch, token, model, request_stats=None):
            calls.append([item["canonical_id"] for item in batch])
            if len(calls) == 1:
                return {"article-1": translated("article-1")}
            return {
                item["canonical_id"]: translated(item["canonical_id"])
                for item in batch
            }

        with (
            mock.patch.dict(
                os.environ,
                {
                    "GITHUB_TOKEN": "test-token",
                    "JAPANESE_SUMMARY_BACKFILL_LIMIT": "10",
                    "JAPANESE_SUMMARY_BATCH_SIZE": "10",
                },
            ),
            mock.patch.object(
                collector,
                "japanese_summary_request",
                side_effect=fake_request,
            ),
            mock.patch.object(collector.time, "sleep"),
        ):
            result = collector.enrich_japanese_summaries(items, items)

        self.assertEqual(calls, [["article-1", "article-2"], ["article-2"]])
        self.assertEqual(result["reviewed"], 2)
        self.assertEqual(result["pending"], 0)

    def test_summary_review_stops_after_rate_limit_and_keeps_completed_batch(self):
        items = [
            {
                "canonical_id": f"article-{index}",
                "title": f"AI research {index}",
                "summary": "A concrete AI method was validated.",
                "published_at": f"2026-07-2{3 - index}T00:00:00Z",
                "source": f"Source {index}",
                "scope_review_version": "old-version",
            }
            for index in (1, 2)
        ]

        def translated(item_id):
            return {
                "in_scope": True,
                "topics": ["Artificial Intelligence"],
                "is_innovation_policy": False,
                "policy_areas": [],
                "policy_relevance": 0,
                "reason": "AIの具体的な新手法。",
                "content_type": "research_breakthrough",
                "technical_focus": "AIの新手法",
                "scope_evidence": "新手法を実証した。",
                "title_ja": f"AI新手法 {item_id}",
                "summary_ja": "研究チームがAIの新手法を実証した。",
            }

        calls = []

        def fake_request(batch, token, model, request_stats=None):
            calls.append([item["canonical_id"] for item in batch])
            if len(calls) == 1:
                if request_stats is not None:
                    request_stats["requests"] = 1
                return {"article-1": translated("article-1")}
            raise collector.SummaryRateLimitError(
                "rate limited",
                remaining="0",
                reset_at="123456",
            )

        with (
            mock.patch.dict(
                os.environ,
                {
                    "GITHUB_TOKEN": "test-token",
                    "JAPANESE_SUMMARY_BACKFILL_LIMIT": "2",
                    "JAPANESE_SUMMARY_BATCH_SIZE": "2",
                    "JAPANESE_SUMMARY_REQUEST_INTERVAL_SECONDS": "0",
                },
            ),
            mock.patch.object(
                collector,
                "japanese_summary_request",
                side_effect=fake_request,
            ),
            mock.patch.object(collector.time, "sleep"),
        ):
            result = collector.enrich_japanese_summaries(items, items)

        self.assertEqual(
            calls,
            [["article-1", "article-2"], ["article-2"]],
        )
        self.assertEqual(result["reviewed"], 1)
        self.assertEqual(result["pending"], 1)
        self.assertTrue(result["rate_limited"])
        self.assertEqual(result["errors"], 1)

    def test_review_only_does_not_fetch_or_update_source_collection_state(self):
        source = {
            "active": True,
            "name": "NEDO News",
            "organization": "NEDO",
            "source_type": "Government",
            "region": "Asia",
            "country": "Japan",
            "category": "Artificial intelligence",
            "feed_url": "https://example.com/feed",
            "homepage": "https://example.com/",
            "priority": 5,
        }
        item = {
            "canonical_id": "pending",
            "source": "NEDO News",
            "published_at": "2026-07-27T00:00:00Z",
            "article_frames": ["Technology Innovation"],
            "topics": ["Artificial Intelligence"],
            "scope_review_version": "old-version",
        }
        summary_result = {
            "generated": 0,
            "reviewed": 0,
            "excluded_ids": [],
            "pending": 1,
            "errors": 0,
            "selected": 1,
            "requests": 0,
            "rate_limited": False,
            "request_budget_reached": False,
            "detail": "",
        }
        with (
            mock.patch.object(collector, "ensure_seed_files"),
            mock.patch.object(
                collector,
                "load_config",
                return_value={"sources": [source]},
            ),
            mock.patch.object(collector, "load_master", return_value=[item]),
            mock.patch.object(collector, "load_public_payload", return_value={}),
            mock.patch.object(
                collector,
                "now_utc",
                return_value=datetime(2026, 7, 28, tzinfo=timezone.utc),
            ),
            mock.patch.object(collector, "make_http_session") as make_session,
            mock.patch.object(
                collector,
                "refresh_academic_review_summaries",
                return_value={
                    "targets": 0,
                    "attempted": 0,
                    "restored": 0,
                    "errors": 0,
                },
            ),
            mock.patch.object(
                collector,
                "enrich_japanese_summaries",
                return_value=summary_result,
            ),
            mock.patch.object(collector, "save_master"),
            mock.patch.object(
                collector,
                "publish_outputs",
                return_value=({"items": []}, []),
            ),
            mock.patch.object(collector, "append_run_log", return_value=[]),
            mock.patch.object(collector, "update_workbook"),
            mock.patch.object(collector, "save_review_state"),
            mock.patch.object(collector, "fetch_source") as fetch_source,
            mock.patch.object(collector, "save_source_status") as save_status,
            mock.patch.object(collector, "save_backfill_state") as save_backfill,
        ):
            result = collector.review_backlog(365, 183)

        self.assertEqual(result, 0)
        make_session.assert_called_once()
        fetch_source.assert_not_called()
        save_status.assert_not_called()
        save_backfill.assert_not_called()

    def test_public_item_includes_academic_metadata(self):
        public = collector.public_item(
            {
                "academic_kind": collector.ACADEMIC_KIND_PREPRINT,
                "review_status": "Not peer reviewed",
                "venue": "arXiv",
                "doi": "https://doi.org/10.1234/example",
                "citation_count": 7,
                "discovery_method": "OpenAlex",
            }
        )
        self.assertEqual(public["academic_kind"], "Preprint")
        self.assertEqual(public["review_status"], "Not peer reviewed")
        self.assertEqual(public["venue"], "arXiv")
        self.assertEqual(public["citation_count"], 7)

    def test_gdelt_datetime_parser(self):
        fallback = datetime(2026, 7, 26, tzinfo=timezone.utc)
        parsed = collector.parse_gdelt_datetime("20260102T030405Z", fallback)
        self.assertEqual(parsed.isoformat(), "2026-01-02T03:04:05+00:00")

    def test_listing_date_parser_supports_japanese_and_reiwa_dates(self):
        fallback = datetime(2026, 7, 26, tzinfo=timezone.utc)
        japanese = collector.parse_listing_date(
            "統合イノベーション戦略2026（2026年7月14日閣議決定）",
            fallback,
        )
        reiwa = collector.parse_listing_date("R8． 6．12", fallback)
        self.assertEqual(japanese.isoformat(), "2026-07-13T15:00:00+00:00")
        self.assertEqual(reiwa.isoformat(), "2026-06-11T15:00:00+00:00")

    def test_listing_date_parser_requires_an_explicit_date(self):
        fallback = datetime(1970, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(
            collector.parse_listing_date(
                "AI platform version 2 supports 30 research teams",
                fallback,
            ),
            fallback,
        )
        self.assertEqual(
            collector.parse_listing_date(
                "Research update 20 May 2026",
                fallback,
            ),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )

    def test_archive_url_scoring_ignores_domain_name(self):
        self.assertEqual(
            collector.archive_url_score(
                "https://technologyreview.com/general/elections",
                "policy",
            ),
            0,
        )
        self.assertGreater(
            collector.archive_url_score(
                "https://example.com/policy/quantum-research-strategy",
                "policy",
            ),
            0,
        )

    def test_site_scan_stays_on_allowlisted_domain_and_reads_article_page(self):
        listing_url = "https://research.example.com/news"
        article_url = "https://research.example.com/news/quantum-processor"
        listing_html = f"""
        <main>
          <a href="{article_url}">
            Quantum processor reaches a new control milestone
          </a>
          <a href="https://untrusted.example.net/news/copied-story">
            Copied quantum processor story
          </a>
        </main>
        """
        article_html = """
        <html>
          <head>
            <meta property="og:title"
                  content="Quantum processor reaches a new control milestone">
            <meta name="description"
                  content="Researchers demonstrated a new quantum processor control method with lower error rates.">
            <meta property="article:published_time"
                  content="2026-07-25T00:00:00Z">
          </head>
        </html>
        """

        def response(url, body):
            value = collector.requests.Response()
            value.status_code = 200
            value.url = url
            value._content = body.encode("utf-8")
            value.encoding = "utf-8"
            return value

        class FakeSession:
            def get(self, url, *args, **kwargs):
                if url == listing_url:
                    return response(listing_url, listing_html)
                if url == article_url:
                    return response(article_url, article_html)
                raise AssertionError(f"Unexpected URL: {url}")

        source = {
            "active": True,
            "name": "Example Research",
            "organization": "Example",
            "source_type": "Official Company",
            "region": "Global",
            "country": "United States",
            "category": "Quantum research",
            "feed_url": listing_url,
            "fetch_mode": "site_scan",
            "listing_url": listing_url,
            "include_link_patterns": ["/news/"],
            "homepage": listing_url,
            "priority": 5,
            "native_feed": False,
        }
        items, result = collector.fetch_source(
            FakeSession(),
            source,
            datetime(2026, 7, 20, tzinfo=timezone.utc),
            datetime(2026, 7, 26, tzinfo=timezone.utc),
            False,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "Example Research")
        self.assertEqual(items[0]["topics"], ["Quantum"])
        self.assertIn("lower error rates", items[0]["summary"])

    def test_link_list_rejects_matching_paths_on_external_domains(self):
        listing_url = "https://agency.example.com/news"
        listing_html = """
        <main>
          <a href="/news/internal-quantum-program">
            Quantum research programme opens
          </a>
          <a href="https://untrusted.example.net/news/copied-quantum-program">
            Copied quantum research programme
          </a>
        </main>
        """

        response = collector.requests.Response()
        response.status_code = 200
        response.url = listing_url
        response._content = listing_html.encode("utf-8")
        response.encoding = "utf-8"

        class FakeSession:
            def get(self, url, *args, **kwargs):
                self.assert_url = url
                return response

        source = {
            "active": True,
            "name": "Example Agency",
            "organization": "Example Agency",
            "source_type": "Government",
            "region": "Global",
            "country": "Global",
            "category": "Quantum research",
            "feed_url": listing_url,
            "fetch_mode": "link_list",
            "listing_url": listing_url,
            "include_link_patterns": ["/news/"],
            "homepage": "https://agency.example.com/",
            "priority": 5,
            "native_feed": False,
        }
        items, result = collector.fetch_source(
            FakeSession(),
            source,
            datetime(2026, 7, 20, tzinfo=timezone.utc),
            datetime(2026, 7, 26, tzinfo=timezone.utc),
            False,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0]["url"],
            "https://agency.example.com/news/internal-quantum-program",
        )

    def test_explicit_article_path_can_use_an_about_newsroom_route(self):
        source = {
            "listing_url": "https://company.example.com/about-us/newsroom",
            "homepage": "https://company.example.com/",
            "feed_url": "https://company.example.com/about-us/newsroom",
            "include_link_patterns": ["/about-us/newsroom/"],
        }
        self.assertTrue(
            collector.site_scan_link_allowed(
                source,
                "https://company.example.com/about-us/newsroom/new-robot",
            )
        )

    def test_site_scan_reads_generic_more_link_title_and_card_date(self):
        listing_url = "https://research.example.com/news"
        article_url = "https://research.example.com/news/quantum-programme"
        listing_html = f"""
        <main>
          <div class="news-card">
            <div class="date"><span>20</span> <i>May</i> <i>2026</i></div>
            <h4>National quantum research programme opens</h4>
            <a href="{article_url}"
               title="National quantum research programme opens">More</a>
          </div>
        </main>
        """
        article_html = """
        <html>
          <head>
            <meta name="description"
                  content="The programme funds quantum computing research and new laboratory infrastructure.">
          </head>
        </html>
        """

        def response(url, body):
            value = collector.requests.Response()
            value.status_code = 200
            value.url = url
            value._content = body.encode("utf-8")
            value.encoding = "utf-8"
            return value

        class FakeSession:
            def get(self, url, *args, **kwargs):
                if url == listing_url:
                    return response(listing_url, listing_html)
                if url == article_url:
                    return response(article_url, article_html)
                raise AssertionError(f"Unexpected URL: {url}")

        source = {
            "active": True,
            "name": "Example Government",
            "organization": "Example",
            "source_type": "Government",
            "region": "Middle East",
            "country": "Saudi Arabia",
            "category": "National research and innovation programmes",
            "feed_url": listing_url,
            "fetch_mode": "site_scan",
            "listing_url": listing_url,
            "include_link_patterns": ["/news/"],
            "homepage": listing_url,
            "priority": 5,
            "native_feed": False,
        }
        items, result = collector.fetch_source(
            FakeSession(),
            source,
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 26, tzinfo=timezone.utc),
            False,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published_at"][:10], "2026-05-20")
        self.assertEqual(
            items[0]["title"],
            "National quantum research programme opens",
        )

    def test_msit_scripted_listing_reads_official_record(self):
        listing_url = (
            "https://www.msit.go.kr/eng/bbs/list.do"
            "?mId=4&mPid=2&sCode=eng"
        )
        article_url_prefix = "https://www.msit.go.kr/eng/bbs/view.do?"
        listing_html = """
        <html>
          <body>
            <a href="javascript:;" onclick="fn_detail(1284);"></a>
            <script>
              var sHtml = '';
              sHtml += unescape(
                'Korea launches a national quantum research programme'
              );
              $('#td_'+'NTT_SJ'+'_0').html(sHtml);
              if ('PSTG_YMD' == 'PSTG_YMD') {
                $('#td_'+'PSTG_YMD'+'_0').html('2026-07-23');
              }
            </script>
          </body>
        </html>
        """
        article_html = """
        <html>
          <head>
            <meta name="description"
                  content="The programme funds quantum computing laboratories and research infrastructure.">
          </head>
          <body>
            <div class="view_head">
              <h2>Korea launches a national quantum research programme</h2>
            </div>
          </body>
        </html>
        """

        def response(url, body):
            value = collector.requests.Response()
            value.status_code = 200
            value.url = url
            value._content = body.encode("utf-8")
            value.encoding = "utf-8"
            return value

        class FakeSession:
            def get(self, url, *args, **kwargs):
                if url == listing_url:
                    return response(listing_url, listing_html)
                if url.startswith(article_url_prefix):
                    return response(url, article_html)
                raise AssertionError(f"Unexpected URL: {url}")

        source = {
            "active": True,
            "name": "Korea MSIT Press Releases",
            "organization": "Ministry of Science and ICT",
            "source_type": "Government",
            "region": "Asia",
            "country": "South Korea",
            "category": "Science, technology and national innovation policy",
            "feed_url": listing_url,
            "fetch_mode": "msit_script_list",
            "listing_url": listing_url,
            "bbs_seq_no": "42",
            "homepage": "https://www.msit.go.kr/eng/",
            "priority": 5,
            "native_feed": False,
        }
        items, result = collector.fetch_source(
            FakeSession(),
            source,
            datetime(2026, 7, 20, tzinfo=timezone.utc),
            datetime(2026, 7, 26, tzinfo=timezone.utc),
            False,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published_at"][:10], "2026-07-23")
        self.assertEqual(
            items[0]["discovery_method"],
            "Official MSIT scripted listing and article page",
        )

    def test_site_scan_falls_back_to_the_official_sitemap(self):
        listing_url = "https://research.example.com/news"
        sitemap_url = "https://research.example.com/sitemap.xml"
        article_url = "https://research.example.com/news/quantum-network"
        sitemap_xml = f"""
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url>
            <loc>{article_url}</loc>
            <lastmod>2026-07-25T00:00:00Z</lastmod>
          </url>
          <url>
            <loc>https://untrusted.example.net/news/copied-story</loc>
            <lastmod>2026-07-25T00:00:00Z</lastmod>
          </url>
        </urlset>
        """
        article_html = """
        <html>
          <head>
            <meta property="og:title"
                  content="Researchers demonstrate a new quantum network">
            <meta name="description"
                  content="The team demonstrated a quantum network with a new entanglement distribution method.">
            <meta property="article:published_time"
                  content="2026-07-25T00:00:00Z">
          </head>
        </html>
        """

        def response(url, body, status=200):
            value = collector.requests.Response()
            value.status_code = status
            value.url = url
            value._content = body.encode("utf-8")
            value.encoding = "utf-8"
            return value

        class FakeSession:
            def get(self, url, *args, **kwargs):
                if url == listing_url:
                    return response(listing_url, "<main></main>")
                if url == sitemap_url:
                    return response(sitemap_url, sitemap_xml)
                if url == article_url:
                    return response(article_url, article_html)
                return response(url, "", status=404)

        source = {
            "active": True,
            "name": "Example Research",
            "organization": "Example",
            "source_type": "Official Company",
            "region": "Global",
            "country": "United States",
            "category": "Quantum research",
            "feed_url": listing_url,
            "fetch_mode": "site_scan",
            "listing_url": listing_url,
            "include_link_patterns": ["/news/"],
            "homepage": listing_url,
            "priority": 5,
            "native_feed": False,
        }
        items, result = collector.fetch_source(
            FakeSession(),
            source,
            datetime(2026, 7, 20, tzinfo=timezone.utc),
            datetime(2026, 7, 26, tzinfo=timezone.utc),
            False,
        )
        self.assertEqual(result.status, "ok")
        self.assertIn("official sitemap fallback", result.detail)
        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0]["discovery_method"],
            "Official-site sitemap and page metadata",
        )
        self.assertEqual(items[0]["url"], article_url)

    def test_reuters_news_sitemap_fallback_reads_news_title_and_date(self):
        listing_url = "https://www.reuters.com/technology/"
        sitemap_url = (
            "https://www.reuters.com/arc/outboundfeeds/"
            "news-sitemap/?outputType=xml"
        )
        article_url = (
            "https://www.reuters.com/business/"
            "nvidia-openai-data-center-financing-2026-07-27/"
        )
        sitemap_xml = f"""
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
                xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
          <url>
            <loc>{article_url}</loc>
            <lastmod>2026-07-27T22:00:00Z</lastmod>
            <news:news>
              <news:publication_date>2026-07-27T21:00:00Z</news:publication_date>
              <news:title>Nvidia may guarantee OpenAI data center financing</news:title>
            </news:news>
          </url>
          <url>
            <loc>https://www.reuters.com/sports/example-2026-07-27/</loc>
            <lastmod>2026-07-27T22:10:00Z</lastmod>
            <news:news>
              <news:publication_date>2026-07-27T22:10:00Z</news:publication_date>
              <news:title>Sports team wins a tournament</news:title>
            </news:news>
          </url>
        </urlset>
        """

        def response(url, body, status=200, content_type="text/html"):
            value = collector.requests.Response()
            value.status_code = status
            value.url = url
            value._content = body.encode("utf-8")
            value.encoding = "utf-8"
            value.headers["Content-Type"] = content_type
            return value

        class FakeSession:
            def get(self, url, *args, **kwargs):
                if url == listing_url:
                    return response(url, "Unauthorized", 401)
                if url == sitemap_url:
                    return response(
                        url,
                        sitemap_xml,
                        200,
                        "application/xml",
                    )
                return response(url, "Unauthorized", 401)

        source = {
            "active": True,
            "name": "Reuters Technology & Science",
            "organization": "Reuters",
            "source_type": "Major Media",
            "region": "Global",
            "country": "Global",
            "category": "Major international reporting",
            "feed_url": listing_url,
            "fetch_mode": "site_scan",
            "listing_url": listing_url,
            "sitemap_urls": [sitemap_url],
            "include_title_patterns": [
                "openai",
                "artificial intelligence",
                "quantum",
            ],
            "homepage": listing_url,
            "priority": 5,
            "coverage_tier": "A",
            "cadence": "daily",
            "strict_relevance": True,
            "native_feed": False,
            "site_scan_daily_limit": 8,
        }
        items, result = collector.fetch_source(
            FakeSession(),
            source,
            datetime(2026, 7, 27, tzinfo=timezone.utc),
            datetime(2026, 7, 28, tzinfo=timezone.utc),
            False,
        )

        self.assertEqual(result.status, "ok")
        self.assertIn("official sitemap fallback", result.detail)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Nvidia may guarantee OpenAI data center financing")
        self.assertEqual(items[0]["published_at"], "2026-07-27T22:00:00Z")
        self.assertIn("Artificial Intelligence", items[0]["topics"])
        self.assertEqual(
            items[0]["discovery_method"],
            "Official-site sitemap and page metadata",
        )

    def test_json_retry_recovers_from_a_transient_server_error(self):
        api_url = "https://api.example.com/posts"
        calls = []

        def response(status, payload):
            value = collector.requests.Response()
            value.status_code = status
            value.url = api_url
            value._content = json.dumps(payload).encode("utf-8")
            value.encoding = "utf-8"
            return value

        class FakeSession:
            def get(self, url, *args, **kwargs):
                calls.append((url, kwargs))
                if len(calls) == 1:
                    return response(500, {"error": "temporary"})
                return response(200, {"posts": []})

        with mock.patch.object(collector.time, "sleep") as sleep:
            payload = collector.get_json_with_retry(FakeSession(), api_url)

        self.assertEqual(payload, {"posts": []})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1]["headers"]["Accept"], "application/json")
        sleep.assert_called_once()

    def test_federal_register_json_api_maps_filtered_documents(self):
        api_url = "https://www.federalregister.gov/api/v1/documents.json"
        payload = {
            "results": [
                {
                    "title": "Quantum network research funding programme",
                    "abstract": (
                        "The agency is funding quantum communication research "
                        "and laboratory infrastructure."
                    ),
                    "document_number": "2026-12345",
                    "type": "Notice",
                    "publication_date": "2026-07-24",
                    "html_url": (
                        "https://www.federalregister.gov/documents/2026/07/24/"
                        "2026-12345/quantum-network-research"
                    ),
                    "agencies": [{"name": "National Science Foundation"}],
                },
                {
                    "title": "Old semiconductor research notice",
                    "abstract": "An older semiconductor research notice.",
                    "document_number": "2026-00001",
                    "type": "Notice",
                    "publication_date": "2026-06-01",
                    "html_url": (
                        "https://www.federalregister.gov/documents/2026/06/01/"
                        "2026-00001/old-semiconductor-notice"
                    ),
                    "agencies": [{"name": "Department of Commerce"}],
                },
            ]
        }
        calls = []

        class FakeSession:
            def get(self, url, *args, **kwargs):
                calls.append((url, kwargs))
                value = collector.requests.Response()
                value.status_code = 200
                value.url = url
                value._content = json.dumps(payload).encode("utf-8")
                value.encoding = "utf-8"
                return value

        source = {
            "active": True,
            "name": "Federal Register Science & Technology",
            "organization": "Office of the Federal Register",
            "source_type": "Government",
            "region": "United States",
            "country": "United States",
            "category": "Science and technology policy",
            "feed_url": api_url,
            "api_url": api_url,
            "fetch_mode": "federal_register",
            "homepage": "https://www.federalregister.gov/",
            "priority": 4,
            "topic_tags": ["Quantum", "Semiconductors & Telecom"],
            "strict_relevance": True,
            "daily_item_limit": 4,
        }
        items, result = collector.fetch_source(
            FakeSession(),
            source,
            datetime(2026, 7, 20, tzinfo=timezone.utc),
            datetime(2026, 7, 26, tzinfo=timezone.utc),
            False,
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.entries_seen, 2)
        self.assertEqual(result.entries_kept, 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published_at"][:10], "2026-07-24")
        self.assertEqual(items[0]["organization"], "National Science Foundation")
        self.assertEqual(
            items[0]["discovery_method"],
            "Federal Register JSON API",
        )
        self.assertIn("2026-12345", items[0]["notes"])
        self.assertEqual(calls[0][0], api_url)
        self.assertEqual(
            calls[0][1]["params"]["conditions[sections][]"],
            "science-and-technology",
        )
        self.assertEqual(
            calls[0][1]["params"]["conditions[publication_date][gte]"],
            "2026-07-20",
        )
        self.assertEqual(
            calls[0][1]["params"]["conditions[publication_date][lte]"],
            "2026-07-26",
        )

    def test_federal_register_valid_empty_results_is_ok(self):
        api_url = "https://www.federalregister.gov/api/v1/documents.json"

        class FakeSession:
            def get(self, url, *args, **kwargs):
                value = collector.requests.Response()
                value.status_code = 200
                value.url = url
                value._content = b'{"results":[]}'
                value.encoding = "utf-8"
                return value

        source = {
            "name": "Federal Register Science & Technology",
            "source_type": "Government",
            "region": "United States",
            "country": "United States",
            "category": "Science and technology policy",
            "feed_url": api_url,
            "fetch_mode": "federal_register",
            "priority": 4,
        }
        items, result = collector.fetch_source(
            FakeSession(),
            source,
            datetime(2026, 7, 20, tzinfo=timezone.utc),
            datetime(2026, 7, 26, tzinfo=timezone.utc),
        )

        self.assertEqual(items, [])
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.entries_seen, 0)

    def test_moderna_reads_current_structured_newsroom_feed(self):
        api_url = "https://www.accesswire.com/qm/data/getHeadlines.json"
        payload = {
            "results": {
                "news": [
                    {
                        "topicstring": "MRNA",
                        "newsitem": [
                            {
                                "newsid": 6014596879866592,
                                "datetime": "2026-07-16T07:00:00-04:00",
                                "headline": (
                                    "Moderna doses first participant in "
                                    "tumor-targeted mRNA therapy trial"
                                ),
                                "qmsummary": (
                                    "The Phase 1 trial evaluates a new "
                                    "tumor-targeted mRNA cancer therapy."
                                ),
                                "topic": "[MRNA,BIOTECH,HEALTHC]",
                            }
                        ],
                    }
                ]
            }
        }
        calls = []

        class FakeSession:
            def get(self, url, *args, **kwargs):
                calls.append((url, kwargs))
                value = collector.requests.Response()
                value.status_code = 200
                value.url = url
                value._content = json.dumps(json.dumps(payload)).encode("utf-8")
                value.encoding = "utf-8"
                return value

        source = {
            "active": True,
            "name": "Moderna Media Center",
            "organization": "Moderna",
            "source_type": "Official Company",
            "region": "United States",
            "country": "United States",
            "category": "Biotechnology, mRNA and clinical research",
            "feed_url": "https://news.modernatx.com/",
            "fetch_mode": "moderna_press_api",
            "api_url": api_url,
            "api_symbol": "MRNA",
            "public_post_base_url": (
                "https://feeds.issuerdirect.com/news-release.html"
            ),
            "homepage": "https://news.modernatx.com/",
            "priority": 4,
            "topic_tags": ["Biotechnology", "Healthcare"],
            "strict_relevance": True,
            "daily_item_limit": 4,
        }
        items, result = collector.fetch_source(
            FakeSession(),
            source,
            datetime(2026, 7, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 26, tzinfo=timezone.utc),
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.entries_seen, 1)
        self.assertEqual(result.entries_kept, 1)
        self.assertEqual(items[0]["published_at"], "2026-07-16T11:00:00Z")
        self.assertIn("newsid=6014596879866592", items[0]["url"])
        self.assertEqual(
            items[0]["discovery_method"],
            "Moderna official newsroom JSON feed",
        )
        self.assertEqual(calls[0][1]["params"]["topics"], "MRNA")
        self.assertEqual(calls[0][1]["params"]["start"], "2026-07-01")
        self.assertEqual(calls[0][1]["params"]["end"], "2026-07-26")

    def test_abb_robotics_reads_official_newsbank_json_feed(self):
        api_url = "https://www.abb.com/conf/abbcommon/services/newsbank.json"
        feed_id = "cbcceb45d7e74cfe9a4601cee01344df"
        payload = {
            "news": {
                "items": [
                    {
                        "title": (
                            "ABB Robotics and NVIDIA define the impact of "
                            "physical AI on manufacturing"
                        ),
                        "id": 137409,
                        "newsUrlTitleSlug": (
                            "abb-robotics-and-nvidia-define-physical-ai"
                        ),
                        "scheduledPublishDate": (
                            "2026-07-17T10:00:00.1230000Z"
                        ),
                        "abstract": (
                            "The companies published new industrial robotics "
                            "research on physical AI."
                        ),
                        "newsType": "Press release",
                        "categories": [
                            {"id": "press", "name": "Press release"}
                        ],
                        "feeds": [
                            {"id": feed_id, "name": "All stories"},
                            {"id": "robotics", "name": "Robotics"},
                        ],
                    },
                    {
                        "title": "Old ABB robotics research story",
                        "id": 100,
                        "newsUrlTitleSlug": "old-abb-robotics-story",
                        "scheduledPublishDate": "2026-06-01T10:00:00Z",
                        "abstract": "An older industrial robotics story.",
                    },
                ]
            }
        }
        calls = []

        class FakeSession:
            def get(self, url, *args, **kwargs):
                calls.append((url, kwargs))
                value = collector.requests.Response()
                value.status_code = 200
                value.url = url
                value._content = json.dumps(payload).encode("utf-8")
                value.encoding = "utf-8"
                return value

        source = {
            "name": "ABB Robotics News",
            "organization": "ABB Robotics",
            "source_type": "Official Company",
            "region": "EU & Europe",
            "country": "Switzerland",
            "category": "Industrial robotics, automation and physical AI",
            "feed_url": "https://www.abb.com/global/en/areas/robotics",
            "fetch_mode": "abb_newsbank_api",
            "api_url": api_url,
            "api_feed_id": feed_id,
            "homepage": "https://www.abb.com/global/en/areas/robotics",
            "priority": 4,
            "topic_tags": ["Artificial Intelligence", "Robotics"],
            "strict_relevance": True,
            "daily_item_limit": 6,
        }
        items, result = collector.fetch_source(
            FakeSession(),
            source,
            datetime(2026, 7, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 26, tzinfo=timezone.utc),
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.entries_seen, 2)
        self.assertEqual(result.entries_kept, 1)
        self.assertEqual(
            items[0]["url"],
            (
                "https://www.abb.com/global/en/news/137409/"
                "abb-robotics-and-nvidia-define-physical-ai"
            ),
        )
        self.assertEqual(items[0]["published_at"], "2026-07-17T10:00:00Z")
        self.assertEqual(
            items[0]["discovery_method"],
            "ABB official NewsBank JSON API",
        )
        self.assertIn("Robotics", items[0]["notes"])
        self.assertEqual(calls[0][0], api_url)
        self.assertEqual(calls[0][1]["params"]["requestType"], "getNewsList")
        self.assertEqual(calls[0][1]["params"]["feedId"], feed_id)

    def test_structured_api_invalid_schema_is_error(self):
        api_url = "https://www.federalregister.gov/api/v1/documents.json"

        class FakeSession:
            def get(self, url, *args, **kwargs):
                value = collector.requests.Response()
                value.status_code = 200
                value.url = url
                value._content = b'{"documents":[]}'
                value.encoding = "utf-8"
                return value

        source = {
            "name": "Federal Register Science & Technology",
            "source_type": "Government",
            "region": "United States",
            "country": "United States",
            "category": "Science and technology policy",
            "feed_url": api_url,
            "fetch_mode": "federal_register",
            "priority": 4,
        }
        items, result = collector.fetch_source(
            FakeSession(),
            source,
            datetime(2026, 7, 20, tzinfo=timezone.utc),
            datetime(2026, 7, 26, tzinfo=timezone.utc),
        )

        self.assertEqual(items, [])
        self.assertEqual(result.status, "error")
        self.assertIn("no results list", result.detail)

    def test_fusion_energy_insights_reads_frontend_json_api(self):
        api_url = (
            "https://fusionenergyinsights-blog.kevin-strite-online.workers.dev/"
            "posts"
        )
        payload = {
            "posts": [
                {
                    "id": "new",
                    "slug": "new-fusion-magnet",
                    "title": "New superconducting magnet advances fusion energy",
                    "excerpt": (
                        "Engineers demonstrated a superconducting magnet for "
                        "a new fusion power system."
                    ),
                    "publishedAt": "2026-07-24T09:30:00.000Z",
                    "categories": [
                        {"label": "FEI Insights", "slug": "-fei-insights"}
                    ],
                },
                {
                    "id": "old",
                    "slug": "old-fusion-post",
                    "title": "Old fusion energy post",
                    "excerpt": "An older fusion energy article.",
                    "publishedAt": "2026-06-01T09:30:00.000Z",
                    "categories": [{"label": "Perspectives"}],
                },
            ],
            "total": 2,
        }
        calls = []

        class FakeSession:
            def get(self, url, *args, **kwargs):
                calls.append(url)
                value = collector.requests.Response()
                value.status_code = 200
                value.url = url
                value._content = json.dumps(payload).encode("utf-8")
                value.encoding = "utf-8"
                return value

        source = {
            "name": "Fusion Energy Insights",
            "organization": "Fusion Energy Insights",
            "source_type": "Major Media",
            "region": "Global",
            "country": "United Kingdom",
            "category": "Fusion energy analysis",
            "feed_url": api_url,
            "api_url": api_url,
            "fetch_mode": "fusion_energy_insights_api",
            "homepage": "https://www.fusionenergyinsights.com/",
            "priority": 3,
            "topic_tags": ["Fusion Energy"],
            "strict_relevance": True,
            "daily_item_limit": 4,
        }
        items, result = collector.fetch_source(
            FakeSession(),
            source,
            datetime(2026, 7, 20, tzinfo=timezone.utc),
            datetime(2026, 7, 26, tzinfo=timezone.utc),
        )

        self.assertEqual(calls, [api_url])
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.entries_seen, 2)
        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0]["url"],
            (
                "https://www.fusionenergyinsights.com/blog/post/"
                "new-fusion-magnet"
            ),
        )
        self.assertEqual(
            items[0]["discovery_method"],
            "Fusion Energy Insights JSON API",
        )
        self.assertIn("FEI Insights", items[0]["notes"])

    def test_spacex_updates_are_sorted_and_keep_distinct_anchor_identities(self):
        api_url = (
            "https://content.spacex.com/api/spacex-website/updates"
        )
        payload = [
            {
                "updateId": "first-engine-test",
                "date": "2026-05-12",
                "title": "Starship engine test advances reusable rockets",
                "contentBlocks": [
                    {
                        "heading": "Raptor test",
                        "paragraph": (
                            "SpaceX tested a <b>reusable rocket engine</b>."
                        ),
                        "listItems": [],
                    }
                ],
            },
            {
                "updateId": "second-engine-test",
                "date": "2026-05-21",
                "title": "Second Starship engine test improves reliability",
                "contentBlocks": [
                    {
                        "heading": None,
                        "paragraph": "The Starship test improved reliability.",
                        "listItems": [
                            {
                                "description": (
                                    "A new avionics controller was validated."
                                )
                            }
                        ],
                    }
                ],
            },
        ]

        class FakeSession:
            def get(self, url, *args, **kwargs):
                value = collector.requests.Response()
                value.status_code = 200
                value.url = url
                value._content = json.dumps(payload).encode("utf-8")
                value.encoding = "utf-8"
                return value

        source = {
            "name": "SpaceX Updates",
            "organization": "SpaceX",
            "source_type": "Official Company",
            "region": "United States",
            "country": "United States",
            "category": "Space launch and reusable rockets",
            "feed_url": api_url,
            "api_url": api_url,
            "fetch_mode": "spacex_updates_api",
            "listing_url": "https://www.spacex.com/updates",
            "homepage": "https://www.spacex.com/updates",
            "priority": 5,
            "topic_tags": ["Space"],
            "strict_relevance": True,
            "daily_item_limit": 8,
        }
        items, result = collector.fetch_source(
            FakeSession(),
            source,
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 26, tzinfo=timezone.utc),
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.entries_seen, 2)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["published_at"][:10], "2026-05-21")
        self.assertEqual(
            items[0]["url"],
            "https://www.spacex.com/updates#second-engine-test",
        )
        self.assertNotIn("None", items[0]["summary"])
        self.assertNotIn("<b>", items[1]["summary"])
        self.assertNotEqual(
            items[0]["canonical_id"],
            items[1]["canonical_id"],
        )
        added, duplicates = collector.deduplicate(items, [])
        self.assertEqual(len(added), 2)
        self.assertEqual(duplicates, 0)

    def test_site_scan_reachable_listing_survives_optional_sitemap_404(self):
        listing_url = "https://shell.example.com/updates"
        sitemap_url = "https://shell.example.com/sitemap.xml"

        def response(url, status):
            value = collector.requests.Response()
            value.status_code = status
            value.url = url
            value._content = (
                b"<html><body><div id='root'></div></body></html>"
                if status == 200
                else b""
            )
            value.encoding = "utf-8"
            return value

        class FakeSession:
            def get(self, url, *args, **kwargs):
                if url == listing_url:
                    return response(url, 200)
                if url == sitemap_url:
                    return response(url, 404)
                raise AssertionError(f"Unexpected URL: {url}")

        source = {
            "name": "Example Shell",
            "organization": "Example",
            "source_type": "Official Company",
            "region": "Global",
            "country": "Global",
            "category": "Quantum research",
            "feed_url": listing_url,
            "fetch_mode": "site_scan",
            "listing_url": listing_url,
            "homepage": listing_url,
            "sitemap_urls": [sitemap_url],
            "priority": 5,
        }
        items, result = collector.fetch_source(
            FakeSession(),
            source,
            datetime(2026, 7, 20, tzinfo=timezone.utc),
            datetime(2026, 7, 26, tzinfo=timezone.utc),
        )

        self.assertEqual(items, [])
        self.assertEqual(result.status, "ok")
        self.assertIn("sitemap:", result.detail)
        self.assertIn("Listing reachable", result.detail)

    def test_site_scan_primary_and_sitemap_failures_remain_error(self):
        listing_url = "https://failed.example.com/updates"
        sitemap_url = "https://failed.example.com/sitemap.xml"

        def response(url, status):
            value = collector.requests.Response()
            value.status_code = status
            value.url = url
            value._content = b""
            value.encoding = "utf-8"
            return value

        class FakeSession:
            def get(self, url, *args, **kwargs):
                if url == listing_url:
                    return response(url, 503)
                if url == sitemap_url:
                    return response(url, 404)
                raise AssertionError(f"Unexpected URL: {url}")

        source = {
            "name": "Failed Example",
            "organization": "Example",
            "source_type": "Official Company",
            "region": "Global",
            "country": "Global",
            "category": "Quantum research",
            "feed_url": listing_url,
            "fetch_mode": "site_scan",
            "listing_url": listing_url,
            "homepage": listing_url,
            "sitemap_urls": [sitemap_url],
            "priority": 5,
        }
        items, result = collector.fetch_source(
            FakeSession(),
            source,
            datetime(2026, 7, 20, tzinfo=timezone.utc),
            datetime(2026, 7, 26, tzinfo=timezone.utc),
        )

        self.assertEqual(items, [])
        self.assertEqual(result.status, "error")
        self.assertIn("listing:", result.detail)
        self.assertIn("sitemap:", result.detail)

    def test_infer_date_from_hyphenated_release_slug(self):
        actual = collector.infer_date_from_url(
            "https://www.roche.com/media/releases/med-cor-2026-07-24"
        )
        self.assertEqual(
            actual,
            datetime(2026, 7, 24, tzinfo=timezone.utc),
        )

    def test_infer_date_from_year_month_directory(self):
        actual = collector.infer_date_from_url(
            "https://english.www.gov.cn/policies/latestreleases/"
            "202607/24/content_example.html"
        )
        self.assertEqual(
            actual,
            datetime(2026, 7, 24, tzinfo=timezone.utc),
        )

    def test_page_metadata_reads_visible_labeled_date(self):
        article_url = "https://www.rdia.gov.sa/en/media-center/news/example"
        article_html = """
        <html>
          <head>
            <title>Saudi Arabia launches a new research programme</title>
          </head>
          <body>
            <main>
              <p><strong>Date</strong> 11/02/2026</p>
              <p>
                The programme supports strategic research, development and
                innovation projects in advanced technology.
              </p>
            </main>
          </body>
        </html>
        """

        response = collector.requests.Response()
        response.status_code = 200
        response.url = article_url
        response._content = article_html.encode("utf-8")
        response.encoding = "utf-8"

        class FakeSession:
            def get(self, url, *args, **kwargs):
                return response

        _, _, published = collector.page_metadata(
            FakeSession(),
            article_url,
            "Fallback title",
            datetime(1970, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(
            published,
            datetime(2026, 2, 11, tzinfo=timezone.utc),
        )

    def test_page_metadata_reads_aist_research_point_summary(self):
        article_url = "https://www.aist.go.jp/aist_j/press_release/example.html"
        article_html = """
        <html>
          <head><title>新しい半導体材料を実証</title></head>
          <body>
            <div class="point_text">
              新材料の結晶構造を制御し、従来材料より高い性能を実証した。
              量産プロセスへの適用に向けた評価も開始した。
            </div>
          </body>
        </html>
        """
        response = collector.requests.Response()
        response.status_code = 200
        response.url = article_url
        response._content = article_html.encode("utf-8")
        response.encoding = "utf-8"

        class FakeSession:
            def get(self, url, *args, **kwargs):
                return response

        _, summary, _ = collector.page_metadata(
            FakeSession(),
            article_url,
            "Fallback title",
            datetime(2026, 7, 24, tzinfo=timezone.utc),
        )

        self.assertIn("新材料の結晶構造", summary)

    def test_config_has_separate_scholarly_kinds(self):
        config = collector.load_config()
        academic_kinds = {
            source.get("academic_kind")
            for source in config["sources"]
            if source.get("fetch_mode") == "openalex"
        }
        self.assertEqual(
            academic_kinds,
            {"Journal Article", "Conference Paper", "Preprint"},
        )

    def test_config_uses_live_sources_and_diverse_company_coverage(self):
        sources = collector.load_config()["sources"]
        active_sources = [
            source for source in sources if source.get("active", True)
        ]
        self.assertFalse(
            any(source.get("fetch_mode") == "static" for source in active_sources)
        )
        companies = [
            source
            for source in active_sources
            if source.get("source_type") == "Official Company"
        ]
        self.assertGreaterEqual(len(companies), 100)
        company_names = {source["name"] for source in companies}
        self.assertTrue(
            {
                "Toyota Research Institute News",
                "TSMC News",
                "Quantinuum News",
                "Kyoto Fusioneering News",
                "Roche Media Releases",
                "Astroscale News",
                "Fujitsu Research",
                "Preferred Networks",
                "東京エレクトロン",
                "QunaSys",
                "Helical Fusion",
                "Takeda",
                "Synspective",
                "Apple Machine Learning Research",
                "AMD",
                "PsiQuantum",
                "General Fusion",
                "Isomorphic Labs",
                "ICEYE",
            }.issubset(company_names)
        )

    def test_config_includes_required_japan_and_overseas_primary_sources(self):
        sources = collector.load_config()["sources"]
        active_names = {
            source["name"] for source in sources if source.get("active")
        }
        self.assertTrue(
            {
                "NEDO ニュース",
                "NEDO 公募",
                "産総研 研究成果",
                "JST プレスリリース",
                "理化学研究所",
                "NICT",
                "JAXA",
                "QST",
                "NIMS",
                "文部科学省",
                "PMDA",
                "DARPA",
                "ARPA-E",
                "CHIPS for America",
                "UKRI",
                "CORDIS",
                "ESA",
                "KISTEP",
                "ITRI",
                "Chinese Academy of Sciences",
                "CSIRO",
                "KAUST",
            }.issubset(active_names)
        )

    def test_config_includes_requested_policy_institutes(self):
        sources = collector.load_config()["sources"]
        active_sources = {
            source["name"]: source
            for source in sources
            if source.get("active", True)
        }
        expected = {
            "Brookings TechTank": "Brookings Institution",
            "CSIS Analysis": "Center for Strategic and International Studies",
            "RAND Corporation": "RAND Corporation",
            "ITIF Publications": "Information Technology and Innovation Foundation",
        }
        for name, organization in expected.items():
            with self.subTest(name=name):
                self.assertIn(name, active_sources)
                self.assertEqual(
                    active_sources[name]["organization"],
                    organization,
                )
                self.assertEqual(
                    active_sources[name]["source_type"],
                    "Policy Institute",
                )

    def test_config_source_names_are_unique_and_tiers_have_expected_cadence(self):
        config = collector.load_config()
        sources = config["sources"]
        names = [source["name"] for source in sources]
        self.assertEqual(len(names), len(set(names)))
        active_sources = [source for source in sources if source.get("active")]
        self.assertEqual(config["expected_active_source_count"], 287)
        self.assertEqual(
            len(active_sources),
            config["expected_active_source_count"],
        )
        self.assertNotIn(
            "OpenAI News",
            {source["name"] for source in active_sources},
        )
        for source in sources:
            if not source.get("active"):
                continue
            tier = collector.source_coverage_tier(source)
            cadence = collector.source_cadence(source)
            self.assertIn(tier, collector.SOURCE_COVERAGE_TIERS)
            self.assertIn(cadence, collector.SOURCE_CADENCES)
            if source.get("coverage_tier") in {"S", "A"}:
                self.assertEqual(cadence, "daily", source["name"])
            if source.get("coverage_tier") == "B":
                self.assertEqual(cadence, "weekly", source["name"])
            if tier == "A":
                self.assertTrue(
                    collector.source_requires_strict_relevance(source),
                    source["name"],
                )
            if source.get("source_type") == "Official Company":
                self.assertTrue(
                    collector.source_requires_strict_relevance(source),
                    source["name"],
                )
                self.assertTrue(
                    collector.source_topic_tags(source),
                    source["name"],
                )
            if source.get("source_type") == "Policy Institute":
                self.assertTrue(
                    collector.source_topic_tags(source),
                    source["name"],
                )

    def test_config_includes_audited_major_media_sources(self):
        active_sources = {
            source["name"]: source
            for source in collector.load_config()["sources"]
            if source.get("active")
        }
        expected = {
            "Reuters Technology & Science": (
                "https://www.reuters.com/technology/",
                "site_scan",
                False,
            ),
            "Financial Times Technology": (
                "https://www.ft.com/technology?format=rss",
                "feed",
                True,
            ),
            "Wall Street Journal Technology": (
                "https://feeds.content.dowjones.io/public/rss/RSSWSJD",
                "feed",
                True,
            ),
            "Associated Press Technology": (
                "https://apnews.com/technology",
                "site_scan",
                False,
            ),
            "Washington Post Technology": (
                "https://www.washingtonpost.com/business/technology/",
                "site_scan",
                False,
            ),
            "日本経済新聞 テック": (
                "https://www.nikkei.com/technology/",
                "site_scan",
                False,
            ),
            "Nikkei Asia Technology": (
                "https://asia.nikkei.com/rss/feed/nar",
                "feed",
                True,
            ),
            "日経クロステック": (
                "https://xtech.nikkei.com/",
                "site_scan",
                False,
            ),
            "ITmedia AI+": (
                "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml",
                "feed",
                True,
            ),
        }
        for name, (url, fetch_mode, native_feed) in expected.items():
            with self.subTest(name=name):
                source = active_sources[name]
                self.assertEqual(source["source_type"], "Major Media")
                self.assertEqual(source["coverage_tier"], "A")
                self.assertEqual(source["cadence"], "daily")
                self.assertTrue(source["strict_relevance"])
                self.assertEqual(source["feed_url"], url)
                self.assertEqual(source.get("fetch_mode", "feed"), fetch_mode)
                self.assertEqual(source["native_feed"], native_feed)

        reuters = active_sources["Reuters Technology & Science"]
        self.assertEqual(
            reuters["sitemap_urls"],
            [
                "https://www.reuters.com/arc/outboundfeeds/"
                "news-sitemap/?outputType=xml"
            ],
        )
        self.assertIn("openai", reuters["include_title_patterns"])
        self.assertEqual(
            active_sources["Nikkei Asia Technology"]["include_url_patterns"],
            [
                "/business/technology/",
                "/business/electronics/",
                "/spotlight/artificial-intelligence/",
            ],
        )

    def test_source_title_filters_include_and_exclude(self):
        source = {
            "include_title_patterns": ["openai", "quantum"],
            "exclude_title_patterns": ["sponsored"],
        }
        self.assertTrue(
            collector.source_text_filter_allows(
                source,
                "OpenAI expands a quantum research programme",
            )
        )
        self.assertFalse(
            collector.source_text_filter_allows(
                source,
                "Sponsored: OpenAI expands a quantum programme",
            )
        )
        self.assertFalse(
            collector.source_text_filter_allows(
                source,
                "Retail sales rose in July",
            )
        )

    def test_openai_nvidia_data_center_headline_is_classified_as_ai(self):
        item = collector.build_item(
            {
                "name": "Example Media",
                "organization": "Example Media",
                "source_type": "Major Media",
                "region": "United States",
                "country": "United States",
                "category": "Major business reporting",
                "priority": 5,
                "coverage_tier": "A",
                "strict_relevance": True,
            },
            "Nvidia may guarantee OpenAI data center financing",
            "https://example.com/openai-data-center-financing",
            "The planned financing supports a large AI computing facility.",
            datetime(2026, 7, 28, tzinfo=timezone.utc),
            datetime(2026, 7, 28, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(item)
        self.assertIn("Artificial Intelligence", item["topics"])

    def test_config_uses_current_structured_and_native_source_endpoints(self):
        active_sources = {
            source["name"]: source
            for source in collector.load_config()["sources"]
            if source.get("active")
        }
        expected = {
            "Federal Register Science & Technology": (
                "federal_register",
                "https://www.federalregister.gov/api/v1/documents.json",
            ),
            "Fusion Energy Insights": (
                "fusion_energy_insights_api",
                "https://fusionenergyinsights-blog.kevin-strite-online.workers.dev/posts",
            ),
            "SpaceX Updates": (
                "spacex_updates_api",
                "https://content.spacex.com/api/spacex-website/updates",
            ),
            "Moderna Media Center": (
                "moderna_press_api",
                "https://www.accesswire.com/qm/data/getHeadlines.json",
            ),
            "ABB Robotics News": (
                "abb_newsbank_api",
                "https://www.abb.com/conf/abbcommon/services/newsbank.json",
            ),
        }
        for name, (fetch_mode, api_url) in expected.items():
            with self.subTest(name=name):
                self.assertEqual(active_sources[name]["fetch_mode"], fetch_mode)
                self.assertEqual(active_sources[name]["api_url"], api_url)

        native_feeds = {
            "Google DeepMind Blog": "https://deepmind.google/blog/rss.xml",
            "Nokia Newsroom": (
                "https://www.nokia.com/newsroom/tagfeed/en-us/"
                "tags/press__releases"
            ),
            "日本人工知能学会": "https://www.ai-gakkai.or.jp/feed/",
            "EE Times Japan": "https://rss.itmedia.co.jp/rss/2.0/eetimes.xml",
            "Bruegel": "https://www.bruegel.org/feed/analysis",
            "Tokamak Energy News": "https://tokamakenergy.com/feed/",
            "WIPO News": "https://www.wipo.int/pressroom/en/rss.xml",
            "European Research Council": "https://erc.europa.eu/rss.xml",
        }
        for name, feed_url in native_feeds.items():
            with self.subTest(name=name):
                self.assertEqual(active_sources[name]["feed_url"], feed_url)
                self.assertTrue(active_sources[name]["native_feed"])

        self.assertEqual(
            active_sources["Japan IP Strategy Headquarters"]["listing_url"],
            "https://www.cas.go.jp/jp/seisakukaigi/titeki2/index.html",
        )
        self.assertEqual(
            active_sources["Boston Dynamics Blog"]["sitemap_urls"],
            ["https://bostondynamics.com/blog-sitemap.xml"],
        )
        for name in ("産総研 お知らせ", "産総研 研究成果"):
            with self.subTest(name=name):
                self.assertEqual(active_sources[name]["fetch_mode"], "html")
                self.assertEqual(
                    active_sources[name]["html"]["date_selector"],
                    ".newsDate",
                )

    def test_config_includes_primary_policy_benchmark_sources(self):
        sources = collector.load_config()["sources"]
        names = {source["name"] for source in sources}
        self.assertTrue(
            {
                "Japan Cabinet Office STI Strategy",
                "Japan IP Strategy Headquarters",
                "JST CRDS STI Policy Reports",
                "White House OSTP News",
                "WIPO News",
                "European Patent Office News",
            }.issubset(names)
        )
        benchmark_sources = [
            source for source in sources if source["name"] in names
            and source["name"] in {
                "Japan Cabinet Office STI Strategy",
                "Japan IP Strategy Headquarters",
                "JST CRDS STI Policy Reports",
                "White House OSTP News",
                "WIPO News",
                "European Patent Office News",
            }
        ]
        self.assertTrue(
            all(
                source.get("history_window") == "policy"
                for source in benchmark_sources
            )
        )


if __name__ == "__main__":
    unittest.main()
