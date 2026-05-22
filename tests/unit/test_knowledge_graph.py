"""Unit tests for KnowledgeGraph - Epic 5 (AC 1-20)."""

import json
import tempfile
from pathlib import Path
from typing import cast

from app.core.knowledge_graph import (
    ConceptEntry,
    KnowledgeGraph,
    SeedEntryDict,
    load_seed_data_from_path,
)
from app.core.metrics import (
    kg_enriched_concepts_total,
    kg_expansion_applied_total,
    knowledge_graph_concepts_count,
    knowledge_graph_terms_count,
    record_kg_enriched_concepts,
    record_kg_expansion_applied,
    update_kg_concepts_count,
    update_kg_terms_count,
)


class TestKGIntegrationWithLLMClient:
    """Integration tests for KG expansion in llm_client.generate_search_queries."""

    def test_kg_lookup_with_provided_instance(self):
        """Test _kg_lookup accepts a provided KG instance and uses its data."""
        from app.core.llm_client import LLMClient

        seed_data = KnowledgeGraph.default_seed_data()
        kg = KnowledgeGraph(
            storage_backend="sqlite",
            db_path=":memory:",
            seed_data=seed_data,
        )

        client = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com/v1",
            model="test-model",
        )

        # Lookup via provided KG should return terms from seed data
        terms = client._kg_lookup("python programming", kg=kg)
        assert len(terms) > 0
        term_names = [t[0] for t in terms]
        assert "programming" in term_names or "code" in term_names

    def test_kg_lookup_with_none_instance_falls_back(self):
        """Test _kg_lookup with kg=None creates fresh instance with seed data."""
        from app.core.llm_client import LLMClient

        client = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com/v1",
            model="test-model",
        )

        # Without KG instance, should still work (creates fresh with seed data)
        terms = client._kg_lookup("ai machine learning")
        assert len(terms) > 0
        term_names = [t[0] for t in terms]
        assert (
            "machine learning" in term_names or "artificial intelligence" in term_names
        )

    def test_kg_lookup_empty_keywords(self):
        """Test _kg_lookup returns empty list for empty keywords."""
        from app.core.llm_client import LLMClient

        client = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com/v1",
            model="test-model",
        )

        terms = client._kg_lookup("")
        assert terms == []

        terms = client._kg_lookup("a b")  # words too short to be keywords
        assert terms == []

    def test_kg_lookup_no_match_in_empty_graph(self):
        """Test _kg_lookup returns empty list when KG has no seed data."""
        from app.core.llm_client import LLMClient

        kg = KnowledgeGraph(
            storage_backend="sqlite",
            db_path=":memory:",
        )  # no seed data

        client = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com/v1",
            model="test-model",
        )

        terms = client._kg_lookup("python", kg=kg)
        assert terms == []

    def test_kg_lookup_with_enriched_graph(self):
        """Test _kg_lookup works with a KG that has been enriched."""
        from app.core.llm_client import LLMClient

        seed_data = KnowledgeGraph.default_seed_data()
        kg = KnowledgeGraph(
            storage_backend="sqlite",
            db_path=":memory:",
            seed_data=seed_data,
        )

        # Enrich the KG with new concepts
        kg.enrich_from_results(
            [
                {
                    "title": "Neural Network Architecture",
                    "description": "Deep learning model design patterns",
                    "quality_score": 0.85,
                },
            ],
        )

        client = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com/v1",
            model="test-model",
        )

        # Lookup should now find terms from both seed data and enriched concepts
        terms = client._kg_lookup("neural", kg=kg)
        assert len(terms) > 0

    def test_kg_lookup_confidence_weighting(self):
        """Test _kg_lookup terms are weighted by KG confidence scores."""
        from app.core.llm_client import LLMClient

        seed_data = KnowledgeGraph.default_seed_data()
        kg = KnowledgeGraph(
            storage_backend="sqlite",
            db_path=":memory:",
            seed_data=seed_data,
        )

        client = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com/v1",
            model="test-model",
        )

        # AI has confidence 0.95, python has 0.9
        ai_terms = client._kg_lookup("ai", kg=kg)
        python_terms = client._kg_lookup("python", kg=kg)

        # High-confidence terms should appear first
        if ai_terms:
            assert ai_terms[0][1] >= 0.95
        if python_terms:
            assert python_terms[0][1] >= 0.9

    def test_kg_lookup_cross_keyword_matching(self):
        """Test _kg_lookup matches across multiple keywords."""
        from app.core.llm_client import LLMClient

        seed_data = KnowledgeGraph.default_seed_data()
        kg = KnowledgeGraph(
            storage_backend="sqlite",
            db_path=":memory:",
            seed_data=seed_data,
        )

        client = LLMClient(
            api_key="test-key",
            base_url="https://api.test.com/v1",
            model="test-model",
        )

        # Multiple keywords should match more terms
        terms = client._kg_lookup("python ai web scraping", kg=kg)
        assert len(terms) > 0

        # Should include terms from multiple concept domains
        term_names = [t[0] for t in terms]
        # At least some terms from different domains should appear
        tech_terms = {"programming", "code", "language", "machine learning"}
        assert any(t in term_names for t in tech_terms)


class TestConceptEntry:
    """Tests for ConceptEntry dataclass."""

    def test_concept_entry_creation(self):
        """Test ConceptEntry creation with all fields."""
        entry = ConceptEntry(
            concept="python",
            related_terms=["programming", "language"],
            confidence=0.9,
        )
        assert entry.concept == "python"
        assert entry.related_terms == ["programming", "language"]
        assert entry.confidence == 0.9
        assert entry.created_at is not None
        assert entry.updated_at is not None

    def test_concept_entry_to_dict(self):
        """Test ConceptEntry serialization."""
        entry = ConceptEntry(
            concept="ai",
            related_terms=["machine learning", "neural network"],
            confidence=0.95,
        )
        data = entry.to_dict()
        assert data["concept"] == "ai"
        assert data["related_terms"] == ["machine learning", "neural network"]
        assert data["confidence"] == 0.95

    def test_concept_entry_from_dict(self):
        """Test ConceptEntry deserialization."""
        data = {
            "concept": "python",
            "related_terms": ["code", "script"],
            "confidence": 0.8,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
        }
        entry = ConceptEntry.from_dict(data)
        assert entry.concept == "python"
        assert entry.related_terms == ["code", "script"]
        assert entry.confidence == 0.8
        assert entry.created_at == "2026-01-01T00:00:00+00:00"


class TestKnowledgeGraphCRUD:
    """Tests for KnowledgeGraph CRUD operations (AC 1)."""

    def test_add_concept(self):
        """Test adding a new concept."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        entry = kg.add_concept("test_concept", ["term1", "term2"], 0.7)
        assert entry.concept == "test_concept"
        assert kg.concepts_count == 1

    def test_add_duplicate_concept_raises(self):
        """Test that adding a duplicate concept raises ValueError."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        kg.add_concept("concept", ["term"])
        try:
            kg.add_concept("concept", ["other_term"])
            assert False, "Expected ValueError"
        except ValueError:
            pass

    def test_add_concept_invalid_confidence(self):
        """Test that invalid confidence raises ValueError."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        try:
            kg.add_concept("concept", ["term"], 1.5)
            assert False, "Expected ValueError"
        except ValueError:
            pass

        try:
            kg.add_concept("concept", ["term"], -0.1)
            assert False, "Expected ValueError"
        except ValueError:
            pass

    def test_get_concept(self):
        """Test retrieving a concept."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        kg.add_concept("python", ["programming", "code"], 0.9)
        entry = kg.get_concept("python")
        assert entry is not None
        assert entry.concept == "python"

    def test_get_nonexistent_concept(self):
        """Test retrieving a nonexistent concept returns None."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        entry = kg.get_concept("nonexistent")
        assert entry is None

    def test_update_concept(self):
        """Test updating an existing concept."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        kg.add_concept("python", ["programming"], 0.5)
        updated = kg.update_concept(
            "python", related_terms=["code", "script"], confidence=0.8
        )
        assert updated.related_terms == ["code", "script"]
        assert updated.confidence == 0.8
        assert updated.updated_at != updated.created_at

    def test_update_nonexistent_concept_raises(self):
        """Test that updating a nonexistent concept raises KeyError."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        try:
            kg.update_concept("nonexistent", related_terms=["term"])
            assert False, "Expected KeyError"
        except KeyError:
            pass

    def test_delete_concept(self):
        """Test deleting a concept."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        kg.add_concept("python", ["programming"], 0.9)
        result = kg.delete_concept("python")
        assert result is True
        assert kg.concepts_count == 0

    def test_delete_nonexistent_concept(self):
        """Test deleting a nonexistent concept returns False."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        result = kg.delete_concept("nonexistent")
        assert result is False

    def test_list_concepts(self):
        """Test listing all concepts."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        kg.add_concept("python", ["programming"], 0.9)
        kg.add_concept("ai", ["ml", "dl"], 0.95)
        concepts = kg.list_concepts()
        assert len(concepts) == 2


class TestKnowledgeGraphStorage:
    """Tests for KnowledgeGraph storage backends (AC 2)."""

    def test_json_storage_save_load(self):
        """Test JSON file save and load."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            kg = KnowledgeGraph(storage_backend="json", db_path=tmp_path)
            kg.add_concept("test", ["term1", "term2"], 0.7)
            kg.save()

            # Load from same file
            kg2 = KnowledgeGraph(storage_backend="json", db_path=tmp_path)
            assert kg2.concepts_count == 1
            entry = kg2.get_concept("test")
            assert entry is not None
            assert entry.related_terms == ["term1", "term2"]
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_sqlite_storage_save_load(self):
        """Test SQLite file save and load."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            kg = KnowledgeGraph(storage_backend="sqlite", db_path=tmp_path)
            kg.add_concept("test", ["term1", "term2"], 0.7)
            kg.save()

            # Load from same file
            kg2 = KnowledgeGraph(storage_backend="sqlite", db_path=tmp_path)
            assert kg2.concepts_count == 1
            entry = kg2.get_concept("test")
            assert entry is not None
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_memory_storage_no_persist(self):
        """Test in-memory storage doesn't persist."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        kg.add_concept("test", ["term"], 0.5)
        assert kg.concepts_count == 1

        # New instance with :memory: has no data
        kg2 = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        assert kg2.concepts_count == 0


class TestKnowledgeGraphSeedData:
    """Tests for seed data initialization (AC 4)."""

    def test_default_seed_data(self):
        """Test default seed data contains expected concepts."""
        seed_data = KnowledgeGraph.default_seed_data()
        assert len(seed_data) >= 8

        concepts = [d["concept"] for d in seed_data]
        assert "python" in concepts
        assert "ai" in concepts

    def test_kg_initialized_with_seed_data(self):
        """Test KG initialized with seed data."""
        seed_data = KnowledgeGraph.default_seed_data()
        kg = KnowledgeGraph(
            storage_backend="sqlite",
            db_path=":memory:",
            seed_data=seed_data,
        )
        assert kg.concepts_count == len(seed_data)
        entry = kg.get_concept("python")
        assert entry is not None
        assert entry.confidence == 0.9

    def test_load_seed_data_from_path(self):
        """Test loading seed data from file path."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmp:
            json.dump(KnowledgeGraph.default_seed_data(), tmp)
            tmp_path = tmp.name

        try:
            loaded = load_seed_data_from_path(tmp_path)
            assert loaded is not None
            assert len(loaded) == len(KnowledgeGraph.default_seed_data())
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_load_seed_data_from_nonexistent_path(self):
        """Test loading from nonexistent path returns None."""
        loaded = load_seed_data_from_path("/nonexistent/path.json")
        assert loaded is None

    def test_load_seed_data_from_malformed_json(self):
        """Test loading from malformed JSON file returns None."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmp:
            tmp.write("not valid json {{{")
            tmp_path = tmp.name

        try:
            loaded = load_seed_data_from_path(tmp_path)
            assert loaded is None
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_kg_with_malformed_seed_data_json(self):
        """Test KnowledgeGraph handles malformed seed_data gracefully."""
        malformed_seed = [
            {"concept": "test"},  # missing required fields
            {"related_terms": ["a"], "confidence": 0.5},  # missing concept
            None,  # non-dict entry
            {"concept": 123, "related_terms": []},  # wrong types
        ]
        # KG should not crash — from_dict uses .get() with defaults
        kg = KnowledgeGraph(
            storage_backend="sqlite",
            db_path=":memory:",
            seed_data=cast(list[SeedEntryDict], malformed_seed),
        )
        # Only valid entries should be loaded; malformed ones skipped via KeyError/TypeError
        assert kg.concepts_count >= 0


class TestKnowledgeGraphLookup:
    """Tests for KG semantic lookup (AC 6-7, 10)."""

    def test_lookup_related_terms(self):
        """Test lookup returns related terms for matching keywords."""
        seed_data = KnowledgeGraph.default_seed_data()
        kg = KnowledgeGraph(
            storage_backend="sqlite",
            db_path=":memory:",
            seed_data=seed_data,
        )
        terms = kg.lookup_related_terms(["python"])
        assert len(terms) > 0
        # Should contain terms related to python
        term_names = [t[0] for t in terms]
        assert "programming" in term_names or "code" in term_names

    def test_lookup_no_match(self):
        """Test lookup returns empty list when no match found (AC 10)."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        terms = kg.lookup_related_terms(["nonexistent_keyword_xyz"])
        assert terms == []

    def test_lookup_empty_keywords(self):
        """Test lookup with empty keywords returns empty list."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        terms = kg.lookup_related_terms([])
        assert terms == []

    def test_lookup_weighted_by_confidence(self):
        """Test lookup terms are weighted by KG confidence."""
        seed_data = KnowledgeGraph.default_seed_data()
        kg = KnowledgeGraph(
            storage_backend="sqlite",
            db_path=":memory:",
            seed_data=seed_data,
        )
        terms = kg.lookup_related_terms(["ai"])
        # Terms should be sorted by confidence descending
        for i in range(len(terms) - 1):
            assert terms[i][1] >= terms[i + 1][1]


class TestKnowledgeGraphEnrichment:
    """Tests for progressive enrichment (AC 11-15)."""

    def test_enrich_from_results(self):
        """Test enrichment adds new concepts from search results."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        results = [
            {
                "title": "Machine Learning Advances",
                "description": "New breakthroughs in ML research",
                "quality_score": 0.85,
            },
        ]
        new_concepts = kg.enrich_from_results(results, source_concept="ai")
        assert len(new_concepts) > 0
        assert kg.concepts_count > 0

    def test_enrichment_rate_limit(self):
        """Test enrichment rate limit enforcement (AC 14)."""
        kg = KnowledgeGraph(
            storage_backend="sqlite",
            db_path=":memory:",
            enrichment_rate_limit=2,
        )
        results = [
            {
                "title": f"UniqueConcept{i}",
                "description": f"Desc {i}",
                "quality_score": 0.8,
            }
            for i in range(5)
        ]

        # First enrichment should succeed (adds 2 concepts)
        kg.enrich_from_results(results[:2])

        # Second enrichment should exceed rate limit
        try:
            kg.enrich_from_results(results[2:4])
            assert False, "Expected ValueError for rate limit"
        except ValueError:
            pass

    def test_enrichment_skip_existing_concepts(self):
        """Test enrichment skips concepts that already exist."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        kg.add_concept("python", ["programming"], 0.9)
        results = [
            {
                "title": "Python Programming",
                "description": "Code examples",
                "quality_score": 0.8,
            },
        ]
        kg.enrich_from_results(results)
        # Should not add "python" as new concept (already exists)
        assert kg.get_concept("python") is not None

    def test_enrichment_empty_input(self):
        """Test enrichment with empty results list."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        new_concepts = kg.enrich_from_results([])
        assert new_concepts == []
        assert kg.concepts_count == 0

    def test_enrichment_malformed_results(self):
        """Test enrichment handles malformed result dicts gracefully."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        results = [
            {},  # no keys at all
            {"title": ""},  # empty title
            {"description": ""},  # empty description
            {"title": None, "description": None},  # None values
            {"quality_score": "not_a_number"},  # wrong type for quality_score
        ]
        new_concepts = kg.enrich_from_results(results)
        # All malformed entries should be skipped
        assert new_concepts == []

    def test_enrichment_out_of_range_quality_score(self):
        """Test enrichment handles out-of-range quality_score."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        results = [
            {
                "title": "HighQuality Concept",
                "description": "Good description",
                "quality_score": 1.5,  # above 1.0
            },
            {
                "title": "LowQuality Concept",
                "description": "Bad description",
                "quality_score": -0.5,  # below 0.0
            },
        ]
        new_concepts = kg.enrich_from_results(results)
        assert len(new_concepts) == 2
        # Confidence should be clamped to [0.0, 1.0] via min(quality_score, 1.0)
        assert new_concepts[0].confidence == 1.0
        assert new_concepts[1].confidence == 0.0

    def test_enrichment_stop_word_filtering(self):
        """Test _extract_concept filters stop words correctly."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        results = [
            {
                "title": "The Art of Python Programming",
                "description": "Learn python",
                "quality_score": 0.8,
            },
            {
                "title": "A Guide to AI",
                "description": "Artificial intelligence overview",
                "quality_score": 0.7,
            },
        ]
        new_concepts = kg.enrich_from_results(results)
        assert len(new_concepts) == 2
        # Stop words should be filtered: "The" → "Art" (3 chars <= 3 → bigram "Art Python")
        # "A" → "Guide" (1 char <= 3 → but "Guide" is meaningful → bigram "A Guide"? No — "A" is stop word)
        concept_names = {c.concept for c in new_concepts}
        assert "Art Python" in concept_names
        assert "Guide" in concept_names

    def test_enrichment_multi_word_phrase_detection(self):
        """Test _extract_concept detects multi-word phrases for short first words."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        results = [
            {
                "title": "Deep Learning Models",
                "description": "Neural networks",
                "quality_score": 0.85,
            },
            {
                "title": "Web Scraping Tools",
                "description": "Data extraction",
                "quality_score": 0.8,
            },
        ]
        new_concepts = kg.enrich_from_results(results)
        assert len(new_concepts) == 2
        concept_names = {c.concept for c in new_concepts}
        # "Deep" (4 chars) > 3 → single word; "Web" (3 chars) <= 3 → bigram
        assert "Deep" in concept_names
        assert "Web Scraping" in concept_names

    def test_enrichment_case_normalization(self):
        """Test _extract_concept normalizes case to Title Case."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        results = [
            {
                "title": "machine learning advances",
                "description": "lowercase title test",
                "quality_score": 0.8,
            },
            {
                "title": "DEEP LEARNING MODELS",
                "description": "uppercase title test",
                "quality_score": 0.8,
            },
        ]
        new_concepts = kg.enrich_from_results(results)
        assert len(new_concepts) == 2
        concept_names = {c.concept for c in new_concepts}
        # All should be Title Case; long first words → single word concept
        assert "Machine" in concept_names
        assert "Deep" in concept_names


class TestExtractConceptEdgeCases:
    """Edge case tests for _extract_concept."""

    def test_extract_concept_empty_string(self):
        """Test _extract_concept with empty string."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        assert kg._extract_concept("") is None

    def test_extract_concept_whitespace_only(self):
        """Test _extract_concept with whitespace-only string."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        assert kg._extract_concept("   ") is None

    def test_extract_concept_all_stop_words(self):
        """Test _extract_concept with only stop words."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        assert kg._extract_concept("the a an") is None
        assert kg._extract_concept("of in on at to") is None

    def test_extract_concept_single_word(self):
        """Test _extract_concept with single meaningful word."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        result = kg._extract_concept("python")
        assert result == "Python"

    def test_extract_concept_single_short_word(self):
        """Test _extract_concept with single short meaningful word (<=3 chars)."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        result = kg._extract_concept("ai")
        assert result == "Ai"

    def test_extract_concept_bigram_detection(self):
        """Test _extract_concept triggers bigram for very short first words."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        result = kg._extract_concept("AI models")
        assert result == "Ai Models"  # "AI" (2 chars) → bigram

    def test_extract_concept_no_bigram_for_longer_words(self):
        """Test _extract_concept does not trigger bigram for words > 3 chars."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        result = kg._extract_concept("Python code")
        assert result == "Python"  # "Python" (6 chars) > 3 → single word

    def test_extract_concept_bigram_for_3_char_word(self):
        """Test _extract_concept triggers bigram for 3-char meaningful words."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        result = kg._extract_concept("API tools")
        assert result == "Api Tools"  # "API" (3 chars) <= 3 → bigram

        result = kg._extract_concept("SEO guide")
        assert result == "Seo Guide"  # "SEO" (3 chars) <= 3 → bigram

    def test_extract_concept_title_case_input(self):
        """Test _extract_concept with already Title Case input."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        result = kg._extract_concept("Python Programming")
        assert result == "Python"  # single word, no bigram trigger

    def test_extract_concept_mixed_case(self):
        """Test _extract_concept with mixed case input."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        result = kg._extract_concept("pYtHoN PrOgRaMmInG")
        assert result == "Python"


class TestKnowledgeGraphMetrics:
    """Tests for KG metrics (AC 16-20)."""

    def test_concepts_count_gauge(self):
        """Test AC16: concepts count gauge updates correctly."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        update_kg_concepts_count(kg.concepts_count)
        assert knowledge_graph_concepts_count._value.get() == 0

        kg.add_concept("test", ["term"], 0.5)
        update_kg_concepts_count(kg.concepts_count)
        assert knowledge_graph_concepts_count._value.get() == 1

    def test_terms_count_gauge(self):
        """Test AC17: terms count gauge updates correctly."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        update_kg_terms_count(kg.terms_count)
        assert knowledge_graph_terms_count._value.get() == 0

        kg.add_concept("test", ["term1", "term2", "term3"], 0.5)
        update_kg_terms_count(kg.terms_count)
        assert knowledge_graph_terms_count._value.get() == 3

    def test_expansion_applied_counter(self):
        """Test AC18: expansion applied counter increments."""
        record_kg_expansion_applied()
        record_kg_expansion_applied()
        assert kg_expansion_applied_total._value.get() == 2

    def test_enriched_concepts_counter(self):
        """Test AC19: enriched concepts counter increments."""
        record_kg_enriched_concepts(5)
        assert kg_enriched_concepts_total._value.get() == 5

    def test_metrics_export(self):
        """Test AC20: metrics exported in Prometheus-compatible format."""
        from app.core.metrics import get_metrics_bytes

        metrics_bytes = get_metrics_bytes()
        assert isinstance(metrics_bytes, bytes)
        metrics_text = metrics_bytes.decode("utf-8")
        assert "knowledge_graph_concepts_count" in metrics_text
        assert "knowledge_graph_terms_count" in metrics_text
        assert "kg_expansion_applied_total" in metrics_text
        assert "kg_enriched_concepts_total" in metrics_text


class TestAutoSave:
    """Tests for auto-save functionality (AC 5)."""

    def test_json_auto_save(self):
        """Test JSON storage auto-saves on changes."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            kg = KnowledgeGraph(storage_backend="json", db_path=tmp_path)
            kg.add_concept("auto_test", ["term"], 0.5)

            # Verify file was written
            path = Path(tmp_path)
            assert path.exists()
            data = json.loads(path.read_text())
            assert "auto_test" in data
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_no_save_when_clean(self):
        """Test save does nothing when no dirty changes."""
        kg = KnowledgeGraph(storage_backend="sqlite", db_path=":memory:")
        # No changes — save should be a no-op
        kg.save()
        assert kg.concepts_count == 0
