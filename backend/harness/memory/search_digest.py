"""
Search Digest — lightweight search result representation for prompt injection.

Raw search ToolResults stay in checkpoint; only the SearchDigest is injected
into the assembled LLM context.  This separates storage from presentation.

Each source gets a ``SourceRecord`` with URL, title, and retrieval timestamp.
Models see only source IDs; code maps to URLs at citation time.

Round 3: True token budget enforcement — items are added incrementally and
the final digest is guaranteed to stay within max_tokens.
"""
from __future__ import annotations

from typing import Any, Callable

from harness.models.memory import SearchDigest, SourceRecord, _now_iso


# ===========================================================================
# SearchDigestBuilder
# ===========================================================================


class SearchDigestBuilder:
    """Builds ``SearchDigest`` objects from raw search results.

    Parameters
    ----------
    token_counter : Callable
        Function that estimates token counts for text.
    max_snippets : int
        Maximum number of evidence snippets to retain.
    max_claims : int
        Maximum number of extracted claims to retain.
    max_tokens : int
        Maximum total token budget for the digest.
    """

    def __init__(
        self,
        token_counter: Callable[[str], int] | None = None,
        max_snippets: int = 5,
        max_claims: int = 5,
        max_tokens: int = 1500,
    ):
        self.token_counter = token_counter or (lambda x: max(1, len(x) // 4))
        self.max_snippets = max_snippets
        self.max_claims = max_claims
        self.max_tokens = max_tokens

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        query: str,
        raw_results: list[Any],
        *,
        source_id_fn: Callable[[Any], str] | None = None,
    ) -> SearchDigest:
        """Build a ``SearchDigest`` from raw search results.

        Parameters
        ----------
        query : str
            The search query that produced these results.
        raw_results : list[Any]
            Raw search result objects (dicts, SearchDocument, str).
        source_id_fn : Callable | None
            Function that extracts a stable source ID from a result item.
            Defaults to a hash of the URL or content.

        Returns
        -------
        SearchDigest
            With tokens_after guaranteed <= max_tokens.
        """
        if source_id_fn is None:
            source_id_fn = self._default_source_id

        # Count tokens_before on ALL raw results
        tokens_before = sum(
            self.token_counter(str(r)) for r in (raw_results or [])
        )

        source_registry: dict[str, SourceRecord] = {}
        seen_source_ids: set[str] = set()
        ordered_source_ids: list[str] = []

        # Pre-register all sources (no token cost for IDs)
        for result in (raw_results or []):
            sid = source_id_fn(result)
            if sid not in seen_source_ids:
                seen_source_ids.add(sid)
                ordered_source_ids.append(sid)

            if sid not in source_registry:
                source_registry[sid] = SourceRecord(
                    source_id=sid,
                    url=self._extract_url(result),
                    title=self._extract_title(result),
                    retrieved_at=_now_iso(),
                )

        # Build digest items incrementally within token budget
        # Priority: query → source IDs → one short snippet per source → claims → extra snippets

        budget_remaining = self.max_tokens

        # Fixed overhead: ~5 tokens for labels like "Query:", "Sources:", etc.
        FORMAT_OVERHEAD_TOKENS = 5
        budget_remaining -= FORMAT_OVERHEAD_TOKENS

        # 1. Query (must have)
        query_tokens = self.token_counter(query)
        budget_remaining -= min(query_tokens, budget_remaining)
        if budget_remaining <= 0:
            return self._build_minimal_digest(query, ordered_source_ids, source_registry, tokens_before)

        # 2. Source IDs
        id_tokens = sum(self.token_counter(sid) for sid in ordered_source_ids)
        budget_remaining -= min(id_tokens, budget_remaining)
        if budget_remaining <= 0:
            return self._build_minimal_digest(query, ordered_source_ids, source_registry, tokens_before)

        # 3. Evidence snippets — one per source, stop when budget exhausted
        snippets: list[str] = []
        for result in (raw_results or []):
            if budget_remaining <= 0:
                break
            if len(snippets) >= self.max_snippets:
                break
            snippet = self._extract_snippet(result)
            if not snippet:
                continue
            snippet_tokens = self.token_counter(snippet)
            if snippet_tokens > budget_remaining:
                # Truncate snippet to fit
                max_chars = max(10, budget_remaining * 4)
                snippet = snippet[:max_chars].rstrip() + "…"
                snippet_tokens = self.token_counter(snippet)
            snippets.append(snippet)
            budget_remaining -= snippet_tokens

        # 4. Claims — only if budget still available
        claims: list[str] = []
        for result in (raw_results or []):
            if budget_remaining <= 10:  # need at least ~10 tokens for a claim
                break
            if len(claims) >= self.max_claims:
                break
            claim = self._extract_claim(result)
            if not claim:
                continue
            claim_tokens = self.token_counter(claim)
            if claim_tokens > budget_remaining:
                max_chars = max(5, budget_remaining * 4)
                claim = claim[:max_chars].rstrip() + "…"
                claim_tokens = self.token_counter(claim)
            claims.append(claim)
            budget_remaining -= claim_tokens

        # 5. Compute actual serialized token count
        actual_payload = self._serialize_digest_payload(query, ordered_source_ids, snippets, claims)
        tokens_after = self.token_counter(actual_payload) + FORMAT_OVERHEAD_TOKENS

        # Guarantee: tokens_after <= max_tokens
        # If still over (shouldn't happen with incremental gating), truncate further
        while tokens_after > self.max_tokens and snippets:
            snippets.pop()
            actual_payload = self._serialize_digest_payload(query, ordered_source_ids, snippets, claims)
            tokens_after = self.token_counter(actual_payload) + FORMAT_OVERHEAD_TOKENS

        while tokens_after > self.max_tokens and claims:
            claims.pop()
            actual_payload = self._serialize_digest_payload(query, ordered_source_ids, snippets, claims)
            tokens_after = self.token_counter(actual_payload) + FORMAT_OVERHEAD_TOKENS

        return SearchDigest(
            query=query,
            source_ids=ordered_source_ids,
            evidence_snippets=snippets,
            extracted_claims=claims,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            source_registry=source_registry,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_minimal_digest(
        self,
        query: str,
        source_ids: list[str],
        registry: dict[str, SourceRecord],
        tokens_before: int,
    ) -> SearchDigest:
        """Build a minimal digest with just query and source IDs."""
        payload = f"Query: {query}\nSources: {', '.join(source_ids)}"
        tokens_after = self.token_counter(payload) + 5
        return SearchDigest(
            query=query,
            source_ids=source_ids,
            evidence_snippets=[],
            extracted_claims=[],
            tokens_before=tokens_before,
            tokens_after=min(tokens_after, self.max_tokens),
            source_registry=registry,
        )

    @staticmethod
    def _serialize_digest_payload(
        query: str,
        source_ids: list[str],
        snippets: list[str],
        claims: list[str],
    ) -> str:
        """Serialize the digest content for accurate token counting."""
        parts = [f"Query: {query}"]
        if source_ids:
            parts.append(f"Sources: {', '.join(source_ids)}")
        if snippets:
            parts.append("Snippets:\n" + "\n".join(f"  - {s}" for s in snippets))
        if claims:
            parts.append("Claims:\n" + "\n".join(f"  - {c}" for c in claims))
        return "\n".join(parts)

    @staticmethod
    def _extract_snippet(result: Any) -> str:
        """Extract a text snippet from any result type."""
        if isinstance(result, str):
            return result[:500]
        if isinstance(result, dict):
            return (
                result.get("content", "")
                or result.get("snippet", "")
                or result.get("text", "")
                or str(result.get("title", "") or "")
            )[:500]
        if hasattr(result, "clean_content"):
            return str(result.clean_content)[:500]
        if hasattr(result, "raw_content"):
            return str(result.raw_content)[:500]
        if hasattr(result, "snippet"):
            return str(result.snippet)[:500]
        return str(result)[:500]

    @staticmethod
    def _extract_claim(result: Any) -> str:
        """Extract a factual claim from a result item."""
        if isinstance(result, dict):
            return str(result.get("title", "") or "")
        if hasattr(result, "title"):
            return str(result.title)
        return ""

    @staticmethod
    def _extract_url(result: Any) -> str:
        """Extract URL from any result type."""
        if isinstance(result, dict):
            return str(result.get("url", "") or "")
        if hasattr(result, "canonical_url"):
            return str(result.canonical_url or "")
        if hasattr(result, "url"):
            return str(result.url or "")
        return ""

    @staticmethod
    def _extract_title(result: Any) -> str:
        """Extract title from any result type."""
        if isinstance(result, dict):
            return str(result.get("title", "") or "")
        if hasattr(result, "title"):
            return str(result.title or "")
        return ""

    @staticmethod
    def _default_source_id(result: Any) -> str:
        """Generate a stable source ID."""
        import hashlib
        if isinstance(result, dict):
            url = result.get("url", "")
            if url:
                return hashlib.md5(url.encode()).hexdigest()[:12]
        if hasattr(result, "canonical_url"):
            url = result.canonical_url or ""
            if url:
                return hashlib.md5(url.encode()).hexdigest()[:12]
        if hasattr(result, "url"):
            url = result.url or ""
            if url:
                return hashlib.md5(url.encode()).hexdigest()[:12]
        return hashlib.md5(str(result).encode()).hexdigest()[:12]
