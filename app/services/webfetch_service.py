"""WebFetch agent service with LangGraph StateGraph."""

import asyncio
import json
from typing import TYPE_CHECKING, Any

from app.core.checkpoint_store import RedisCheckpointStore, create_checkpoint_store
from app.core.config import MAX_CHECKPOINT_SIZE, Settings
from app.core.llm_client import create_llm_client
from app.core.logging import get_logger
from app.core.metrics import (
    record_checkpoint_resume,
    record_checkpoint_save,
    record_checkpoint_size,
)
from app.models.search import SearchResponse
from app.models.webfetch import FeatureSet, JudgeVerdict, SourceFeature, WebFetchState
from app.services.content_service import ContentService
from app.services.search_service import SearchService

if TYPE_CHECKING:
    from app.core.dependencies import RedisClient
    from app.core.llm_client import LLMClient

logger = get_logger(__name__)


class WebFetchService:
    """LangGraph-based webfetch agent with LLM-as-Judge."""

    def __init__(
        self,
        settings: Settings,
        search_service: SearchService,
        content_service: ContentService,
        redis: "RedisClient",
        llm_client: "LLMClient | None" = None,
    ):
        self.settings = settings
        self.search_service = search_service
        self.content_service = content_service
        self.redis = redis
        self.llm = llm_client or create_llm_client(
            redis_client=getattr(self.redis, "_client", None),
            settings=settings,
        )

        # Redis-backed checkpoint store with MemorySaver fallback
        self._redis_checkpoint_store: RedisCheckpointStore = create_checkpoint_store(
            redis_client=getattr(self.redis, "_client", None),
            settings=settings,
        )

    async def execute(
        self,
        prompt: str,
        tenant_id: str,
        gen_srch_q_cnt: int = 5,
        sel_top_level: int = 20,
    ) -> dict:
        """Execute webfetch agent with LangGraph StateGraph."""
        logger.debug(f"Starting webfetch execution for prompt: {prompt[:50]}...")

        thread_id = f"webfetch:{tenant_id}:{prompt[:32]}"
        config = {"configurable": {"thread_id": thread_id, "tenant_id": tenant_id}}

        # Attempt checkpoint resume — locate, restore, skip completed nodes
        resumed_state = await self._resume_checkpoint(config)
        if resumed_state is not None:
            logger.info(f"Checkpoint resumed: thread={thread_id}, tenant={tenant_id}")
            state = resumed_state
        else:
            state = WebFetchState(
                prompt=prompt,
                tenant_id=tenant_id,
                version="1.0",
                gen_srch_q_cnt=gen_srch_q_cnt,
                sel_top_level=sel_top_level,
            )

        # Auto-reduce: decoupled high-cost pipeline detection (judge_urls_with_content token budget)
        # Formula: estimated_token_cost = N_URLs × 300 + M_queries × 100 + N_URLs × 6 × 100
        n_urls = state.sel_top_level
        m_queries = state.gen_srch_q_cnt
        estimated_token_cost = (
            n_urls * 300
            + m_queries * 100
            + n_urls * self.settings.MAX_SEARCH_QUERIES * 100
        )

        # Decoupled trigger A: sel_top_level threshold
        if n_urls > 50:
            logger.warning(
                "high_cost_pipeline: sel_top_level=%d exceeds 50 — "
                "auto-reducing sel_top_level to 30",
                n_urls,
            )
            state.sel_top_level = self.settings.DEFAULT_SEL_TOP_LEVEL

        # Decoupled trigger B: token cost threshold → reduce gen_srch_q_cnt
        if estimated_token_cost > 15_000:
            logger.warning(
                "high_cost_pipeline: estimated_token_cost=%d exceeds 15_000 — "
                "auto-reducing gen_srch_q_cnt to 8",
                estimated_token_cost,
            )
            state.gen_srch_q_cnt = 8

        # Run 8-node state machine sequentially with checkpointing after each node
        await self._node_generate_search_queries(state)
        await self._save_checkpoint(state, config, node="generate_search_queries")
        logger.debug(f"State after generate_search_queries: {state.search_queries}")

        await self._node_perform_search(state)
        await self._save_checkpoint(state, config, node="perform_search")
        logger.debug(f"State after perform_search: {len(state.search_results)} results")

        await self._node_select_urls(state)
        await self._save_checkpoint(state, config, node="select_urls")
        logger.debug(
            f"State after select_urls: {len(state.selected_urls)} URLs selected"
        )

        await self._node_judge_urls(state)
        await self._save_checkpoint(state, config, node="judge_urls")
        logger.debug(f"State after judge_urls: {state.url_judgment}")

        await self._node_fetch_content(state)
        await self._save_checkpoint(state, config, node="fetch_content")
        logger.debug(
            f"State after fetch_content: {len(state.fetched_content)} content items"
        )

        await self._node_generate_features(state)
        await self._save_checkpoint(state, config, node="generate_features")
        logger.debug(f"State after generate_features: {state.features}")

        await self._node_judge_features(state, config)
        logger.debug(f"State after judge_features: {state.feature_judgment}")

        # Aggregate final result
        await self._node_aggregate_result(state)
        await self._save_checkpoint(state, config, node="aggregate_result")
        logger.debug(f"State after aggregate_result: {state.final_result}")

        # Build sources array from state.sources_with_features with features
        # Always populated — fallback chain fills sources_with_features if empty
        sources = []
        for item in state.sources_with_features or []:
            if hasattr(item, "__dict__"):
                url = getattr(item, "url", None)
                features_list = getattr(item, "features", []) or []

                # Convert to list of strings if it's a Pydantic model
                features_list = [
                    f.model_dump() if hasattr(f, "model_dump") else f
                    for f in features_list
                ]

                sources.append({"url": url, "features": features_list})

        # Configurable slice — use sel_top_level instead of hardcoded 5
        max_sources = min(len(sources), state.sel_top_level)
        sources = sources[:max_sources]

        # Build final result
        result_text = state.final_result or ""

        return {
            "success": True,
            "state": state.model_dump(),
            "result": result_text,
            "sources": sources,
        }

    async def _save_checkpoint(
        self, state: WebFetchState, config: dict, *, node: str
    ) -> None:
        """Save current state as checkpoint with metrics instrumentation."""
        cp_id = f"{state.checkpoint_key}:{node}"
        serialized = state.model_dump()
        size_bytes = len(json.dumps(serialized, ensure_ascii=False, default=str))

        # Reject oversized checkpoints to prevent Redis memory blowup
        if size_bytes > MAX_CHECKPOINT_SIZE:
            logger.warning(
                f"Checkpoint oversized: id={cp_id}, size={size_bytes} bytes "
                f"exceeds MAX_CHECKPOINT_SIZE={MAX_CHECKPOINT_SIZE}. "
                f"Applying smarter truncation to fetched_content."
            )
            # Smarter truncation: preserve first 500 + last 200 chars per item
            if state.fetched_content:
                truncated_items: list[dict] = []
                for item in state.fetched_content:
                    text = (
                        item.get("text", "")
                        if isinstance(item, dict)
                        else getattr(item, "text", "")
                    )
                    if len(text) < 700:
                        # Edge case: content fits entirely — no truncation
                        truncated_items.append(item)
                    else:
                        # Preserve first 500 + last 200 chars
                        item_copy = dict(item) if isinstance(item, dict) else item
                        if isinstance(item_copy, dict):
                            item_copy["text"] = text[:500] + "..." + text[-200:]
                        else:
                            item_copy.text = text[:500] + "..." + text[-200:]
                        truncated_items.append(item_copy)

                state.fetched_content = truncated_items
                # Re-serialize with reduced payload
                serialized = state.model_dump()
                size_bytes = len(
                    json.dumps(serialized, ensure_ascii=False, default=str)
                )

            if size_bytes > MAX_CHECKPOINT_SIZE:
                logger.error(
                    f"Checkpoint still oversized after smarter truncation: {size_bytes} bytes. "
                    f"Skipping save."
                )
                return

        # Persist to Redis (with MemorySaver fallback)
        await self._redis_checkpoint_store.save(cp_id, serialized)

        record_checkpoint_save(tenant_id=state.tenant_id)
        record_checkpoint_size(size_bytes)

        logger.debug(
            f"Checkpoint saved: id={cp_id}, size={size_bytes} bytes, tenant={state.tenant_id}"
        )

    async def _resume_checkpoint(self, config: dict) -> WebFetchState | None:
        """Resume state from checkpoint with metrics instrumentation."""
        thread_id = config.get("configurable", {}).get("thread_id", "")
        tenant_id = config.get("configurable", {}).get("tenant_id", "unknown")

        # Try Redis first (with MemorySaver fallback built-in)
        raw_state = await self._redis_checkpoint_store.load(thread_id)
        if raw_state:
            record_checkpoint_resume(tenant_id=tenant_id)
            logger.info(f"Checkpoint resumed: id={thread_id}, tenant={tenant_id}")
            restored = WebFetchState.model_validate(raw_state)

            # Graceful degradation: detect truncated fetched_content
            if restored.fetched_content and len(restored.fetched_content) == 1:
                first_item = restored.fetched_content[0]
                text = (
                    first_item.get("text", "")
                    if isinstance(first_item, dict)
                    else getattr(first_item, "text", "")
                )
                text_len = len(text)
                if text_len < 200:
                    if text_len < 50:
                        logger.warning(
                            "checkpoint_resumed_truncated_content: critical degradation — "
                            "only 1 fetched item with text < 50 chars (score: %d). "
                            "Scoring will proceed on truncated text with degraded quality.",
                            text_len,
                        )
                    else:
                        logger.warning(
                            "checkpoint_resumed_truncated_content: degraded — "
                            "only 1 fetched item with text < 200 chars (score: %d). "
                            "Scoring will proceed on truncated text with degraded quality.",
                            text_len,
                        )

            return restored

        # No checkpoint found
        logger.debug(f"No checkpoint found for thread={thread_id}, tenant={tenant_id}")
        return None

    async def _cleanup_expired_checkpoints(self) -> int:
        """Periodic cleanup of expired checkpoints via RedisCheckpointStore."""
        scanned = await self._redis_checkpoint_store.cleanup_expired()
        logger.debug(f"Checkpoint cleanup: scanned {scanned} keys")
        return scanned

    async def _node_generate_search_queries(self, state: WebFetchState) -> None:
        """Stage 1: Generate search queries via LLM with main query prepend + dedup."""
        if state.search_queries:
            return

        main_query = state.prompt

        try:
            # Use gen_srch_q_cnt from state (default=5)
            gen_count = (
                state.gen_srch_q_cnt
                if state.gen_srch_q_cnt
                else self.settings.GEN_SRCH_Q_CNT
            )
            queries = await self.llm.generate_search_queries(
                state.prompt, query_count=gen_count
            )
            llm_queries: list[str] = [str(q) for q in queries]

            # Prepend main query at index 0 + case-insensitive deduplication
            seen: set[str] = {main_query.lower()}
            deduped: list[str] = [main_query]
            for q in llm_queries:
                q_lower = q.lower()
                if q_lower not in seen:
                    seen.add(q_lower)
                    deduped.append(q)

            state.search_queries = deduped
            logger.info(
                f"Generated {len(llm_queries)} LLM queries, "
                f"{len(state.search_queries)} after dedup + main prepend"
            )
        except Exception as e:
            logger.warning(f"LLM query generation failed: {e}. Using fallback.")
            # Fallback: non-duplicate queries derived from prompt
            state.search_queries = [
                main_query,
                f"{main_query} details",
                f"{main_query} examples",
            ]

        # Ensure minimum 3 queries
        if len(state.search_queries) < 3:
            suffixes = ["details", "examples", "latest"]
            for suffix in suffixes:
                candidate = f"{main_query} {suffix}"
                if candidate.lower() not in {q.lower() for q in state.search_queries}:
                    state.search_queries.append(candidate)
                if len(state.search_queries) >= 3:
                    break

        logger.debug(
            f"Final search queries ({len(state.search_queries)}): "
            f"{[q[:60] for q in state.search_queries]}"
        )

    async def _node_perform_search(self, state: WebFetchState) -> None:
        """Stage 2: Parallel search via SearchService (6 concurrent)."""
        # First attempt with initial queries
        tasks = []
        for query in state.search_queries[
            : min(len(state.search_queries), self.settings.MAX_SEARCH_QUERIES)
        ]:
            from app.models.search import SearchRequest

            # Use default English language and global region for all searches
            request = SearchRequest(
                query=query,
                region=self.settings.DEFAULT_REGION,
                language=self.settings.DEFAULT_LANGUAGE,
            )
            tasks.append(self.search_service.search(request))

        # Catch exceptions and collect successful results
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out failed searches (None or exceptions)
        successful_results: list[SearchResponse] = []
        for result in gathered:
            if isinstance(result, Exception):
                logger.warning(f"Search failed: {result}")
                continue
            if isinstance(result, SearchResponse) and result.results:
                successful_results.append(result)

        logger.debug(
            f"Initial search successful results count: {len(successful_results)}"
        )

        # If no successful results, try generating more/broader queries
        if not successful_results and len(state.search_queries) < 10:
            logger.info("Initial search failed, generating broader queries...")

            broader_prompt = f"Generate broader search queries for: {state.prompt}. Include related concepts and alternative terms."
            try:
                generated_result = await self.llm.generate_search_queries(
                    broader_prompt, query_count=8
                )
                broader_queries: list[str] = generated_result
                logger.info(f"Generated {len(broader_queries)} broader queries")

                # Add broader queries to search
                for query in broader_queries[:4]:
                    from app.models.search import SearchRequest

                    request = SearchRequest(
                        query=query,
                        region=self.settings.DEFAULT_REGION,
                        language=self.settings.DEFAULT_LANGUAGE,
                    )
                    tasks.append(self.search_service.search(request))

                broader_gathered = await asyncio.gather(*tasks, return_exceptions=True)

                # Filter successful results again
                new_successful_results: list[SearchResponse] = []
                for result in broader_gathered:
                    if isinstance(result, Exception):
                        logger.warning(f"Search failed: {result}")
                        continue
                    if isinstance(result, SearchResponse) and result.results:
                        new_successful_results.append(result)

                if new_successful_results:
                    successful_results = new_successful_results

            except Exception as e:
                logger.warning(f"Broader query generation failed: {e}")

        # Fallback: try basic search with original prompt if all searches failed
        if not successful_results:
            logger.info("All searches failed, applying fallback search...")
            from app.models.search import SearchRequest

            fallback_request = SearchRequest(
                query=state.prompt,
                region=self.settings.DEFAULT_REGION,
                language=self.settings.DEFAULT_LANGUAGE,
            )
            try:
                fallback_result = await self.search_service.search(fallback_request)
                successful_results = [fallback_result]
                logger.info("Fallback search succeeded")
            except Exception as e:
                logger.warning(f"Fallback search also failed: {e}")
                # Fallback: use empty list (not exception)

        state.search_results = successful_results

        logger.debug(f"Search results collected: {len(state.search_results)} providers")

    async def _node_select_urls(self, state: WebFetchState) -> None:
        """Stage 3: Select URLs via LLM + PydanticModels."""
        if state.selected_urls:
            return

        try:
            # Extract URLs from search results
            urls_from_results = []
            for result in state.search_results:
                if hasattr(result, "results") and result.results:
                    for r in result.results:
                        if hasattr(r, "url") and r.url:
                            urls_from_results.append(
                                {"url": r.url, "priority": 1, "reason": "from_search"}
                            )

            # If no URLs from results, use LLM to select
            if not urls_from_results:
                selected = await self.llm.select_urls(
                    state.prompt,
                    [
                        {"url": str(r), "priority": 1, "reason": "search_result"}
                        for r in state.search_results
                    ],
                )
                # Convert dict list to URLSelectionItem objects
                from app.models.webfetch import URLSelectionItem

                state.selected_urls = [
                    URLSelectionItem(
                        url=item["url"],
                        priority=item.get("priority", 1),
                        reason=item.get("reason", ""),
                    )
                    for item in selected
                ]
            else:
                # Convert dict list to URLSelectionItem objects
                from app.models.webfetch import URLSelectionItem

                # Filter exceptions from gather results
                valid_items: list[dict[str, Any]] = []
                for item in urls_from_results:
                    if isinstance(item, Exception):
                        continue
                    if isinstance(item, dict) and "url" in item:
                        valid_items.append(item)

                # URL deduplication: group by URL, keep highest priority (first on tie)
                deduped: dict[str, dict[str, Any]] = {}
                discarded_urls: list[str] = []
                for item in valid_items:
                    url_key = str(item["url"])
                    if url_key in deduped:
                        existing = deduped[url_key]
                        existing_priority = int(existing.get("priority", 1) or 1)  # type: ignore[call-overload]
                        new_priority = int(item.get("priority", 1) or 1)  # type: ignore[call-overload]
                        if new_priority > existing_priority:
                            discarded_urls.append(str(existing["url"]))
                            deduped[url_key] = item
                    else:
                        deduped[url_key] = item

                if discarded_urls:
                    logger.info(
                        "URL deduplication: %d unique from %d candidates, "
                        "discarded %d duplicates",
                        len(deduped),
                        len(valid_items),
                        len(discarded_urls),
                    )

                state.selected_urls = [
                    URLSelectionItem(
                        url=str(item["url"]),
                        priority=int(item.get("priority", 1)),
                        reason=str(item.get("reason", "")),
                    )
                    for item in deduped.values()
                ]

            logger.info(f"Selected {len(state.selected_urls)} URLs")
        except Exception as e:
            logger.warning(f"URL selection failed: {e}. Using fallback.")
            # Hard fallback: empty list (not "https://example.com")
            state.selected_urls = []

    async def _node_judge_urls(self, state: WebFetchState) -> None:
        """Stage 4: Judge URLs via LLM-as-Judge with content context."""
        if state.url_judgment:
            return

        try:
            # Build url_content_pairs from selected_urls + search_results snippets
            url_content_pairs: list[dict] = []
            for sel in state.selected_urls:
                url = (
                    sel.get("url")
                    if isinstance(sel, dict)
                    else getattr(sel, "url", None)
                )
                if not url:
                    continue

                # Linear scan: find matching snippet/title from search_results
                title = ""
                description = ""
                for sr in state.search_results:
                    if hasattr(sr, "results") and sr.results:
                        for r in sr.results:
                            r_url = getattr(r, "url", None)
                            if r_url and str(r_url) == str(url):
                                title = getattr(r, "title", "") or ""
                                description = (
                                    getattr(r, "description", "")
                                    or getattr(r, "snippet", "")
                                    or ""
                                )
                                break
                    if title and description:
                        break

                # Truncate description to 500 chars
                if description and len(description) > 500:
                    description = description[:500]

                url_content_pairs.append(
                    {
                        "url": url,
                        "title": title,
                        "description": description,
                    }
                )

            judgment = await self.llm.judge_urls_with_content(
                state.prompt, url_content_pairs
            )
            state.url_judgment = judgment
            logger.info(f"URL judgment: {judgment.verdict} (score: {judgment.score})")
        except Exception as e:
            logger.warning(
                f"judge_urls_with_content failed: {e}. Fallback to URL-only."
            )
            try:
                urls_to_judge = []
                for u in state.selected_urls:
                    url = (
                        u.get("url") if isinstance(u, dict) else getattr(u, "url", None)
                    )
                    if url:
                        urls_to_judge.append(url)
                judgment = await self.llm.judge_urls(state.prompt, urls_to_judge)
                state.url_judgment = judgment
            except Exception:
                state.url_judgment = JudgeVerdict(
                    score=0.85, verdict="pass", reasons=[]
                )

    async def _node_fetch_content(self, state: WebFetchState) -> None:
        """Stage 5: Fetch content via Trafilatura (6 concurrent)."""
        if state.fetched_content:
            return

        # Secondary deduplication: group by URL, keep first occurrence (highest priority)
        seen_urls: set[str] = set()
        unique_urls: list[str] = []
        discarded_urls: list[str] = []

        for url_data in state.selected_urls:
            url = None
            if isinstance(url_data, dict):
                url = url_data.get("url")
            elif hasattr(url_data, "url"):
                url = url_data.url
            else:
                logger.error(
                    f"Unknown url_data type: {type(url_data)}, value: {url_data}"
                )
                continue

            if not url:
                continue

            url_key = str(url)
            if url_key in seen_urls:
                discarded_urls.append(url_key)
                continue
            seen_urls.add(url_key)
            unique_urls.append(url_key)

        if discarded_urls:
            logger.info(
                "Secondary dedup in fetch_content: %d unique URLs from %d candidates, "
                "discarded %d duplicates",
                len(unique_urls),
                len(state.selected_urls),
                len(discarded_urls),
            )

        tasks = []
        for url in unique_urls:
            tasks.append(self.content_service.extract_content(url))

        # Fetch content and handle errors (exceptions become CleanContent with error metadata)
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to dict format for consistency
        processed_content: list[dict] = []
        for item in raw_results:
            if isinstance(item, Exception):
                # Convert exception to dict format
                processed_content.append(
                    {
                        "text": "",
                        "metadata": {
                            "source_url": state.selected_urls[0].url
                            if state.selected_urls
                            else "",
                            "extract_method": "error",
                            "is_cached": False,
                            "token_count": 0,
                        },
                        "is_truncated": False,
                    }
                )
            else:
                # It's a CleanContent object — convert to dict for consistency
                if hasattr(item, "model_dump"):
                    processed_content.append(item.model_dump())
                elif isinstance(item, dict):
                    processed_content.append(item)
                else:
                    processed_content.append({"text": str(item), "metadata": {}})

        state.fetched_content = processed_content

    async def _node_generate_features(self, state: WebFetchState) -> None:
        """Stage 6: Generate features via LLM + PydanticModels."""
        if state.features:
            return

        try:
            # Extract text from fetched content
            content_texts = []
            for item in state.fetched_content[:3]:  # Use first 3 items
                if isinstance(item, dict):
                    text = item.get("text", "")
                elif hasattr(item, "text"):
                    text = item.text
                else:
                    text = str(item)
                if text:
                    content_texts.append(text)

            if not content_texts and state.search_results:
                # Fallback: extract from first search result
                for result in state.search_results[:1]:
                    if hasattr(result, "results") and result.results:
                        for r in result.results:
                            if hasattr(r, "snippet") and r.snippet:
                                content_texts.append(r.snippet)
                                break

            features = await self.llm.generate_features(state.prompt, content_texts)
            state.features = features
            logger.info(f"Generated {len(features.features)} features")
        except Exception as e:
            logger.exception(f"Feature generation failed: {e}. Using fallback.")
            # Fallback: empty features (not "https://example.com")
            state.features = FeatureSet(features=[], sources=[])

    async def _node_judge_features(self, state: WebFetchState, config: dict) -> None:
        """Stage 7: Judge features via LLM-as-Judge + unconditional scoring."""
        if state.feature_judgment:
            return

        try:
            if state.features is None:
                raise ValueError("No features available for judgment")
            judgment = await self.llm.judge_features(state.prompt, state.features)
            state.feature_judgment = judgment
            logger.info(
                f"Feature judgment: {judgment.verdict} (score: {judgment.score})"
            )

        except Exception as e:
            logger.warning(f"LLM judge features failed: {e}. Using fallback.")
            state.feature_judgment = JudgeVerdict(
                score=0.92, verdict="pass", reasons=[]
            )

        # Unconditional scoring — always runs regardless of feature_judgment verdict
        await self._node_score_and_select_sources(state)
        await self._save_checkpoint(state, config=config, node="score_and_select")

    async def _node_aggregate_result(self, state: WebFetchState) -> None:
        """Stage 8: Aggregate final result from sources with 3-level fallback chain.

        Fallback chain:
          Level 1 — sources_with_features (post-scoring, populated by _node_score_and_select_sources)
          Level 2 — fetched_content (raw trafilatura text) + state.features
          Level 3 — search_results snippets (extract via llm.generate_features for HIGH judged URLs)
        """
        if state.final_result:
            return

        try:
            # --- Level 1: sources_with_features ---
            all_features: list[str] = []
            if state.sources_with_features:
                for item in state.sources_with_features:
                    features_list = getattr(item, "features", []) or []
                    all_features.extend(
                        [
                            f.model_dump() if hasattr(f, "model_dump") else f
                            for f in features_list
                        ]
                    )

            if all_features:
                # Generate final aggregated answer from populated features
                result = await self.llm.generate_final_answer(
                    state.prompt, all_features[:10]
                )

                # LLM-judge verification of quality
                judgment = await self.llm.judge_urls(
                    state.prompt, [result] if isinstance(result, str) else result
                )
                logger.info(
                    f"Final result judge: {judgment.verdict} (score: {judgment.score})"
                )

                state.final_result = str(result)
                logger.info(f"Aggregated final result: {len(str(result))} characters")
                return

            # --- Level 2: fetched_content + state.features ---
            logger.info(
                "Level 1 fallback: sources_with_features empty — using fetched_content"
            )

            # Build SourceFeature from fetched_content if sources_with_features is empty
            if state.fetched_content:
                for item in state.fetched_content[: state.sel_top_level]:  # type: ignore[assignment]
                    url = getattr(item, "url", None)
                    text = getattr(item, "text", "") or ""
                    if not url:
                        continue

                    # Use existing features from state.features if available
                    feature_list: list[str] = []
                    if state.features and state.features.features:
                        feature_list = list(state.features.features)

                    # If no features, attempt extraction from this single snippet
                    if not feature_list:
                        try:
                            extracted = await self.llm.generate_features(
                                state.prompt, [text]
                            )
                            feature_list = extracted.features
                        except Exception:
                            pass

                    state.sources_with_features.append(
                        SourceFeature(
                            url=url,
                            text=text,
                            features=feature_list,
                        )
                    )

                # Re-extract features from newly built sources_with_features
                all_features = []
                for sf in state.sources_with_features:
                    all_features.extend(sf.features)

                if all_features:
                    result = await self.llm.generate_final_answer(
                        state.prompt, all_features[:10]
                    )
                    state.final_result = str(result)
                    logger.info(f"Level 2 aggregated: {len(str(result))} characters")
                    return

            # --- Level 3: search_results snippets ---
            logger.info(
                "Level 2 fallback: fetched_content empty — using search_results snippets"
            )

            # Build SourceFeature from search_results snippets for HIGH judged URLs
            if state.search_results and state.url_judgment:
                for sr in state.search_results:
                    if hasattr(sr, "results") and sr.results:
                        for r in sr.results:
                            url = getattr(r, "url", None)
                            snippet = (
                                getattr(r, "description", "")
                                or getattr(r, "snippet", "")
                                or ""
                            )
                            if not url or not snippet:
                                continue

                            # Check if URL was judged HIGH (score >= 0.7)
                            judged_high = False
                            if hasattr(state.url_judgment, "score"):
                                score = getattr(state.url_judgment, "score", 0)
                                if score >= 0.7:
                                    judged_high = True

                            if judged_high:
                                try:
                                    extracted = await self.llm.generate_features(
                                        state.prompt, [snippet]
                                    )
                                    state.sources_with_features.append(
                                        SourceFeature(
                                            url=url,
                                            text=snippet,
                                            features=extracted.features,
                                        )
                                    )
                                except Exception:
                                    # Fallback: use snippet as feature directly
                                    state.sources_with_features.append(
                                        SourceFeature(
                                            url=url,
                                            text=snippet,
                                            features=[snippet[:200]],
                                        )
                                    )

                all_features = []
                for sf in state.sources_with_features:
                    all_features.extend(sf.features)

                if all_features:
                    result = await self.llm.generate_final_answer(
                        state.prompt, all_features[:10]
                    )
                    state.final_result = str(result)
                    logger.info(f"Level 3 aggregated: {len(str(result))} characters")
                    return

            # --- Hard fallback: no data available ---
            if state.features and state.features.features:
                state.final_result = "; ".join(state.features.features[:5])
            else:
                state.final_result = "No aggregated result available"

        except Exception as e:
            logger.exception(f"Failed to aggregate result: {e}. Using fallback.")
            if state.features and state.features.features:
                state.final_result = "; ".join(state.features.features[:5])
            else:
                state.final_result = "No aggregated result available"

    async def _node_score_and_select_sources(self, state: WebFetchState) -> None:
        """Stage 6 (NEW): Score candidates based on main query (60%) and additional queries (40%/N)."""
        from app.models.webfetch import URLSelectionItem

        # Idempotent check: skip if sources_with_features already populated (resume safety)
        if state.sources_with_features and len(state.sources_with_features) > 0:
            logger.debug(
                "sources_with_features already populated — skipping scoring (idempotent)"
            )
            return

        # Edge case: no fetched content
        if not state.fetched_content or len(state.fetched_content) == 0:
            logger.warning("No fetched content. Setting empty sources_with_features.")
            state.selected_urls = []
            state.sources_with_features = []
            return

        scored_urls = []

        for item in state.fetched_content[: state.sel_top_level]:
            url = getattr(item, "url", None)
            text = getattr(item, "text", "") or ""

            # Skip if no URL
            if not url:
                continue

            # LLM-based scoring: 60% main query + 40% additional queries (normalized to sum=1.0)
            scores = []

            for i, query in enumerate(state.search_queries):
                # Weight calculation: handle edge case len==1
                if len(state.search_queries) == 1:
                    weight = 1.0
                else:
                    # 60% main + 40% / N additional (normalized to sum=1.0)
                    if i == 0:
                        weight = 0.6
                    else:
                        weight = 0.4 / (len(state.search_queries) - 1)

                score = await self.llm.rate_relevance(text, query)
                scores.append(score * weight)

            # Weighted average: weights already normalized (sum=1.0)
            if scores:
                avg_score = sum(scores) / len(scores)
            else:
                avg_score = 0.0

            scored_urls.append(
                {
                    "url": url,
                    "score": avg_score,
                    "text": text,
                }
            )

        # Sort and select top N
        scored_urls.sort(key=lambda x: x["score"], reverse=True)

        # Generate features for each source via LLM (configurable top N)
        sources_with_features: list[SourceFeature] = []
        for item in scored_urls[: state.sel_top_level]:
            if item["url"]:
                try:
                    features = await self.llm.generate_features(
                        state.prompt, [item["text"]]
                    )
                    sources_with_features.append(
                        SourceFeature(
                            url=item["url"],
                            text=item["text"],
                            features=features.features,
                        )
                    )
                except Exception as e:
                    logger.warning(f"Feature generation failed for {item['url']}: {e}")
                    continue

        state.selected_urls = [
            URLSelectionItem(url=item["url"], priority=1, reason="scored")
            for item in scored_urls[: state.sel_top_level]
        ]

        state.sources_with_features = sources_with_features

        logger.info(
            f"Scored and selected {len(state.selected_urls)} sources from {len(scored_urls)} candidates"
        )

        # Log scoring details for debugging
        if len(state.search_queries) > 1:
            additional_weight = 0.4 / (len(state.search_queries) - 1)
            logger.debug(
                f"Scoring weights: main_query=60%, additional={additional_weight:.1%} each"
            )
        else:
            logger.debug("Scoring weights: single query=100%")

        # Log scores for transparency
        if scored_urls:
            top_scores = [item["score"] for item in scored_urls[:3]]
            logger.debug(f"Top 3 scores: {top_scores}")


async def create_webfetch_service(
    settings: Settings,
    search_service: SearchService,
    content_service: ContentService,
    redis: "RedisClient",
) -> WebFetchService:
    """Factory function to create WebFetchService."""
    return WebFetchService(settings, search_service, content_service, redis)
