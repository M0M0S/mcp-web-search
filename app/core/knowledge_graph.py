"""Knowledge Graph for semantic query expansion and progressive enrichment.

Supports SQLite and JSON file storage backends. Provides CRUD operations for
concepts and related terms, seed data initialization, auto-save on changes,
and progressive enrichment from successful search results.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

import structlog

logger = structlog.get_logger("knowledge-graph")


class SearchResultDict(TypedDict):
    """TypedDict for search result entries passed to enrich_from_results."""

    title: str
    description: str
    quality_score: float


class SeedEntryDict(TypedDict):
    """TypedDict for seed data entries (includes serialized ConceptEntry fields)."""

    concept: str
    related_terms: list[str]
    confidence: float
    created_at: str
    updated_at: str


@dataclass
class ConceptEntry:
    """A single concept entry in the Knowledge Graph.

    Attributes:
        concept: The canonical concept name.
        related_terms: List of semantically related terms.
        confidence: Confidence score in range [0.0, 1.0].
        created_at: ISO 8601 timestamp of creation.
        updated_at: ISO 8601 timestamp of last update.
    """

    concept: str
    related_terms: list[str] = field(default_factory=list)
    confidence: float = 0.5
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> SeedEntryDict:
        """Serialize to dictionary."""
        return {
            "concept": self.concept,
            "related_terms": self.related_terms,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: SeedEntryDict) -> ConceptEntry:
        """Deserialize from dictionary."""
        return cls(
            concept=data["concept"],
            related_terms=data.get("related_terms", []),
            confidence=data.get("confidence", 0.5),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            updated_at=data.get("updated_at", datetime.now(timezone.utc).isoformat()),
        )


class KnowledgeGraph:
    """Semantic Knowledge Graph with configurable storage backend.

    Stores concepts and their related terms for semantic query expansion.
    Supports SQLite and JSON file storage. Auto-saves on changes.
    Supports progressive enrichment from search results.

    Attributes:
        storage_backend: Storage backend type ('sqlite' or 'json').
        db_path: Path to SQLite database or JSON file.
        enrichment_rate_limit: Maximum new concepts per hour.
        _enrichment_timestamps: Track enrichment events for rate limiting.
    """

    def __init__(
        self,
        storage_backend: str = "sqlite",
        db_path: str | None = None,
        seed_data: list[SeedEntryDict] | None = None,
        enrichment_rate_limit: int = 10,
    ) -> None:
        self.storage_backend = storage_backend
        self.enrichment_rate_limit = enrichment_rate_limit

        if storage_backend == "sqlite":
            self.db_path = db_path or ":memory:"
        else:
            self.db_path = db_path or "knowledge_graph.json"

        self._concepts: dict[str, ConceptEntry] = {}
        self._enrichment_timestamps: list[float] = []
        self._dirty: bool = False

        # Load seed data if provided
        if seed_data:
            for entry in seed_data:
                if not isinstance(entry, dict):
                    logger.warning(
                        "kg_seed_data_invalid_entry_type",
                        entry_type=type(entry).__name__,
                    )
                    continue
                try:
                    concept = ConceptEntry.from_dict(cast(SeedEntryDict, entry))
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning(
                        "kg_seed_data_invalid_entry",
                        error=type(exc).__name__,
                        concept=entry.get("concept", "<unknown>"),
                    )
                    continue
                self._concepts[concept.concept] = concept

        # Initialize from storage
        self._load()

        # Mark as clean after load
        self._dirty = False

    @property
    def concepts_count(self) -> int:
        """Return total number of concepts in the graph."""
        return len(self._concepts)

    @property
    def terms_count(self) -> int:
        """Return total number of related terms across all concepts."""
        return sum(len(entry.related_terms) for entry in self._concepts.values())

    # --- CRUD operations ---

    def get_concept(self, concept: str) -> ConceptEntry | None:
        """Retrieve a concept entry by name.

        Args:
            concept: Canonical concept name.

        Returns:
            ConceptEntry if found, None otherwise.
        """
        return self._concepts.get(concept)

    def add_concept(
        self, concept: str, related_terms: list[str], confidence: float = 0.5
    ) -> ConceptEntry:
        """Add a new concept to the graph.

        Args:
            concept: Canonical concept name.
            related_terms: List of semantically related terms.
            confidence: Confidence score in range [0.0, 1.0].

        Returns:
            The created ConceptEntry.

        Raises:
            ValueError: If concept already exists or confidence out of range.
        """
        if concept in self._concepts:
            raise ValueError(f"Concept '{concept}' already exists")

        if not 0.0 <= confidence <= 1.0:
            raise ValueError(
                f"Confidence must be in range [0.0, 1.0], got {confidence}"
            )

        entry = ConceptEntry(
            concept=concept,
            related_terms=related_terms,
            confidence=confidence,
        )
        self._concepts[concept] = entry
        self._dirty = True
        self._auto_save()
        return entry

    def update_concept(
        self,
        concept: str,
        related_terms: list[str] | None = None,
        confidence: float | None = None,
    ) -> ConceptEntry:
        """Update an existing concept entry.

        Args:
            concept: Canonical concept name.
            related_terms: New related terms (optional).
            confidence: New confidence score (optional).

        Returns:
            The updated ConceptEntry.

        Raises:
            KeyError: If concept does not exist.
        """
        entry = self._concepts.get(concept)
        if entry is None:
            raise KeyError(f"Concept '{concept}' not found")

        if related_terms is not None:
            entry.related_terms = related_terms
        if confidence is not None:
            if not 0.0 <= confidence <= 1.0:
                raise ValueError(
                    f"Confidence must be in range [0.0, 1.0], got {confidence}"
                )
            entry.confidence = confidence

        entry.updated_at = datetime.now(timezone.utc).isoformat()
        self._dirty = True
        self._auto_save()
        return entry

    def delete_concept(self, concept: str) -> bool:
        """Delete a concept from the graph.

        Args:
            concept: Canonical concept name.

        Returns:
            True if deleted, False if not found.
        """
        if concept not in self._concepts:
            return False

        del self._concepts[concept]
        self._dirty = True
        self._auto_save()
        return True

    def list_concepts(self) -> list[ConceptEntry]:
        """Return all concept entries in the graph."""
        return list(self._concepts.values())

    # --- KG lookup for query expansion ---

    def lookup_related_terms(self, keywords: list[str]) -> list[tuple[str, float]]:
        """Look up related terms matching given keywords.

        Matches keywords against concept names and existing related terms.
        Returns terms weighted by concept confidence.

        Args:
            keywords: List of keyword strings to match.

        Returns:
            List of (term, confidence) tuples sorted by confidence descending.
        """
        matched_terms: dict[str, float] = {}

        for keyword in keywords:
            keyword_lower = keyword.lower()

            # Match against concept names
            for concept_name, entry in self._concepts.items():
                if keyword_lower in concept_name.lower():
                    for term in entry.related_terms:
                        current = matched_terms.get(term, 0.0)
                        matched_terms[term] = max(current, entry.confidence)

            # Match against existing related terms
            for entry in self._concepts.values():
                for term in entry.related_terms:
                    if keyword_lower in term.lower():
                        current = matched_terms.get(term, 0.0)
                        matched_terms[term] = max(current, entry.confidence)

        # Sort by confidence descending
        sorted_terms = sorted(matched_terms.items(), key=lambda x: x[1], reverse=True)
        return sorted_terms

    # --- Progressive enrichment ---

    def enrich_from_results(
        self,
        search_results: list[SearchResultDict],
        source_concept: str | None = None,
    ) -> list[ConceptEntry]:
        """Enrich the Knowledge Graph from successful search results.

        Extracts new concepts and related terms from result titles and
        descriptions. Applies rate limiting to prevent excessive enrichment.

        Args:
            search_results: List of search result dicts with 'title' and
                'description' keys.
            source_concept: Optional source concept name for attribution.

        Returns:
            List of newly added ConceptEntry objects.

        Raises:
            ValueError: If enrichment rate limit exceeded.
        """
        # Check rate limit (max N new concepts per hour)
        now = datetime.now(timezone.utc).timestamp()
        one_hour_ago = now - 3600
        self._enrichment_timestamps = [
            t for t in self._enrichment_timestamps if t > one_hour_ago
        ]

        if len(self._enrichment_timestamps) >= self.enrichment_rate_limit:
            logger.warning(
                "kg_enrichment_rate_limit_exceeded",
                current_count=len(self._enrichment_timestamps),
                limit=self.enrichment_rate_limit,
            )
            raise ValueError(
                f"Enrichment rate limit exceeded: {len(self._enrichment_timestamps)} "
                f"/ {self.enrichment_rate_limit} new concepts per hour"
            )

        new_concepts: list[ConceptEntry] = []

        for result in search_results:
            title = result.get("title", "")
            description = result.get("description", "")
            quality_score = result.get("quality_score", 0.5)

            if not title and not description:
                continue

            # Extract potential concept from title
            concept_name = self._extract_concept(title)
            if not concept_name or concept_name in self._concepts:
                continue

            # Extract related terms from title and description
            related_terms = self._extract_related_terms(title, description)

            # Confidence derived from quality score, clamped to [0.0, 1.0]
            confidence = max(min(quality_score, 1.0), 0.0)

            entry = ConceptEntry(
                concept=concept_name,
                related_terms=related_terms,
                confidence=confidence,
            )
            self._concepts[concept_name] = entry
            new_concepts.append(entry)

        if new_concepts:
            for _ in new_concepts:
                self._enrichment_timestamps.append(now)
            self._dirty = True
            logger.info(
                "kg_enriched_concepts",
                count=len(new_concepts),
                source_concept=source_concept,
                concepts=[c.concept for c in new_concepts],
            )

        return new_concepts

    def _extract_concept(self, text: str) -> str | None:
        """Extract a canonical concept name from text.

        Improved extraction with stop-word filtering, case normalization,
        and multi-word phrase detection.

        Args:
            text: Input text to extract from.

        Returns:
            Extracted concept name or None.
        """
        if not text:
            return None

        # Stop words to filter out (common English words that are not meaningful
        # as standalone concepts)
        STOP_WORDS: frozenset[str] = frozenset(
            {
                "the",
                "a",
                "an",
                "and",
                "or",
                "but",
                "in",
                "on",
                "at",
                "to",
                "for",
                "of",
                "with",
                "by",
                "from",
                "is",
                "are",
                "was",
                "were",
                "be",
                "been",
                "being",
                "have",
                "has",
                "had",
                "do",
                "does",
                "did",
                "will",
                "would",
                "could",
                "should",
                "may",
                "might",
                "can",
                "this",
                "that",
                "it",
                "its",
                "as",
                "if",
                "so",
                "no",
                "not",
                "yes",
            }
        )

        # Clean and split
        words = text.strip().split()
        if not words:
            return None

        # Filter out stop words, keep meaningful words
        meaningful_words = [w for w in words if w.lower() not in STOP_WORDS]
        if not meaningful_words:
            return None

        # Try multi-word phrase detection (bigram):
        # If first two meaningful words are not stop words and together form
        # a more meaningful concept than the first word alone, use bigram.
        if len(meaningful_words) >= 2:
            first = meaningful_words[0]
            second = meaningful_words[1]
            # Use bigram if the first word is very short (<=3 chars) and the second
            # is not a stop word — very short words are more likely to be stop words
            # that slipped through or abbreviations.
            if len(first) <= 3:
                bigram = f"{first} {second}"
                return bigram.title()

        # Single word concept
        concept = meaningful_words[0]
        return concept.title()

    def _extract_related_terms(self, title: str, description: str) -> list[str]:
        """Extract related terms from title and description.

        Filters stop words (same set as _extract_concept) and takes up to 5
        meaningful words as related terms.

        Args:
            title: Search result title.
            description: Search result description.

        Returns:
            List of extracted related terms.
        """
        # Stop words to filter out (same as _extract_concept)
        STOP_WORDS: frozenset[str] = frozenset(
            {
                "the",
                "a",
                "an",
                "and",
                "or",
                "but",
                "in",
                "on",
                "at",
                "to",
                "for",
                "of",
                "with",
                "by",
                "from",
                "is",
                "are",
                "was",
                "were",
                "be",
                "been",
                "being",
                "have",
                "has",
                "had",
                "do",
                "does",
                "did",
                "will",
                "would",
                "could",
                "should",
                "may",
                "might",
                "can",
                "this",
                "that",
                "it",
                "its",
                "as",
                "if",
                "so",
                "no",
                "not",
                "yes",
            }
        )

        terms: list[str] = []

        for text in [title, description]:
            if not text:
                continue
            words = text.strip().split()
            # Filter out stop words, take up to 5 meaningful words
            meaningful = [w for w in words if w.lower() not in STOP_WORDS]
            terms.extend(meaningful[:5])

        # Deduplicate preserving order
        return list(dict.fromkeys(terms))

    # --- Persistence ---

    def save(self) -> None:
        """Persist the Knowledge Graph to storage.

        Only saves if there are dirty changes.
        """
        if not self._dirty:
            return

        if self.storage_backend == "sqlite":
            self._save_sqlite()
        else:
            self._save_json()

        self._dirty = False

    def _auto_save(self) -> None:
        """Auto-save if dirty. Called after each mutation."""
        self.save()

    def _load(self) -> None:
        """Load the Knowledge Graph from storage."""
        if self.storage_backend == "sqlite":
            self._load_sqlite()
        else:
            self._load_json()

    def _save_sqlite(self) -> None:
        """Save concepts to SQLite database."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            # Create table if not exists
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS concepts (
                    concept TEXT PRIMARY KEY,
                    related_terms TEXT,
                    confidence REAL,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )

            # Upsert all concepts
            for entry in self._concepts.values():
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO concepts
                    (concept, related_terms, confidence, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        entry.concept,
                        json.dumps(entry.related_terms),
                        entry.confidence,
                        entry.created_at,
                        entry.updated_at,
                    ),
                )

            conn.commit()
        finally:
            conn.close()

    def _load_sqlite(self) -> None:
        """Load concepts from SQLite database."""
        if self.db_path == ":memory:":
            return

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS concepts (
                    concept TEXT PRIMARY KEY,
                    related_terms TEXT,
                    confidence REAL,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )

            cursor.execute(
                "SELECT concept, related_terms, confidence, created_at, updated_at FROM concepts"
            )
            rows = cursor.fetchall()

            for row in rows:
                data = {
                    "concept": row[0],
                    "related_terms": json.loads(row[1]),
                    "confidence": row[2],
                    "created_at": row[3],
                    "updated_at": row[4],
                }
                entry = ConceptEntry.from_dict(cast(SeedEntryDict, data))
                self._concepts[entry.concept] = entry
        finally:
            conn.close()

    def _save_json(self) -> None:
        """Save concepts to JSON file."""
        data = {entry.concept: entry.to_dict() for entry in self._concepts.values()}

        # Ensure directory exists
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _load_json(self) -> None:
        """Load concepts from JSON file."""
        path = Path(self.db_path)
        if not path.exists():
            return

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return

        data = json.loads(content)

        for concept_name, entry_data in data.items():
            entry = ConceptEntry.from_dict(cast(SeedEntryDict, entry_data))
            self._concepts[entry.concept] = entry

    # --- Seed data ---

    @staticmethod
    def default_seed_data() -> list[SeedEntryDict]:
        """Return default seed data for common search concepts.

        Covers tech, science, and news domains.
        """
        now = datetime.now(timezone.utc).isoformat()
        return [
            # Tech domain
            {
                "concept": "python",
                "related_terms": [
                    "programming",
                    "language",
                    "code",
                    "script",
                    "development",
                ],
                "confidence": 0.9,
                "created_at": now,
                "updated_at": now,
            },
            {
                "concept": "ai",
                "related_terms": [
                    "artificial intelligence",
                    "machine learning",
                    "neural network",
                    "deep learning",
                    "LLM",
                ],
                "confidence": 0.95,
                "created_at": now,
                "updated_at": now,
            },
            {
                "concept": "web scraping",
                "related_terms": [
                    "data extraction",
                    "html parsing",
                    "crawler",
                    "bot",
                    "automation",
                ],
                "confidence": 0.8,
                "created_at": now,
                "updated_at": now,
            },
            {
                "concept": "api",
                "related_terms": ["endpoint", "rest", "http", "request", "response"],
                "confidence": 0.85,
                "created_at": now,
                "updated_at": now,
            },
            # Science domain
            {
                "concept": "climate",
                "related_terms": [
                    "weather",
                    "environment",
                    "global warming",
                    "ecology",
                    "sustainability",
                ],
                "confidence": 0.85,
                "created_at": now,
                "updated_at": now,
            },
            {
                "concept": "biology",
                "related_terms": ["life", "organism", "evolution", "genetics", "cell"],
                "confidence": 0.8,
                "created_at": now,
                "updated_at": now,
            },
            # News domain
            {
                "concept": "technology news",
                "related_terms": [
                    "innovation",
                    "startup",
                    "tech industry",
                    "gadgets",
                    "software",
                ],
                "confidence": 0.75,
                "created_at": now,
                "updated_at": now,
            },
            {
                "concept": "science news",
                "related_terms": [
                    "research",
                    "discovery",
                    "experiment",
                    "paper",
                    "journal",
                ],
                "confidence": 0.75,
                "created_at": now,
                "updated_at": now,
            },
        ]


# --- Seed data loading ---


def load_seed_data_from_path(
    path: str | None,
) -> list[dict[str, str | float | list[str]]] | None:
    """Load seed data from a JSON file path.

    Args:
        path: Path to seed data JSON file.

    Returns:
        List of seed data dicts or None if path not provided/file not found.
    """
    if not path:
        return None

    seed_path = Path(path)
    if not seed_path.exists():
        logger.warning(
            "kg_seed_data_file_not_found",
            path=str(seed_path),
        )
        return None

    with open(seed_path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            logger.warning(
                "kg_seed_data_json_decode_error",
                path=str(seed_path),
            )
            return None
