"""
SEC EDGAR adapter — US public company filings search.

Searches the SEC EDGAR full-text search index for company filings
(10-K annual reports, 10-Q quarterly reports, 8-K current events, etc.).

Free and open — no API key required.  Rate limit: 10 req/sec.
Requires a descriptive User-Agent header (SEC policy).

Register at: https://www.sec.gov/edgar/sec-api-documentation
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.request
import urllib.parse
from typing import Any

from harness.tools.search.base import SearchDocument, SearchQuery, SearchTool


def _ssl_context() -> ssl.SSLContext | None:
    """Return an SSL context, optionally skipping verification.

    Set ``SSL_NO_VERIFY=1`` in your .env if you're behind a VPN/firewall
    that intercepts TLS (common in corporate and cross-border setups).
    Uses ``_create_unverified_context`` which is more permissive than
    modifying ``create_default_context`` — required for data.sec.gov.
    """
    if os.getenv("SSL_NO_VERIFY", "").strip() in ("1", "true", "yes", "on"):
        return ssl._create_unverified_context()
    return None  # use default verification

# EDGAR full-text search (for CIK lookup) and submissions API (for actual filings)
_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions"

# Common form types for due diligence
_FORM_TYPES: dict[str, list[str]] = {
    "annual": ["10-K", "20-F"],            # annual reports
    "quarterly": ["10-Q"],                 # quarterly reports
    "current": ["8-K", "6-K"],            # current events / material changes
    "ipo": ["S-1", "F-1", "S-1/A"],      # IPO registrations
    "proxy": ["DEF 14A"],                 # proxy statements
    "all": [],                            # no filter
}


class SECEdgarAdapter(SearchTool):
    """Search SEC EDGAR for company filings.

    Maps ``SearchQuery`` fields:
    - query → full-text search query (company name, ticker, or keyword)
    - source_type → form category filter ("annual", "quarterly", "current", "ipo", "all")
    - freshness_hint → date range ("recent" → 90 days, "balanced" → 2 years, "any" → no limit)
    - site_hints → entity name hints (appended to EDGAR entity filter)
    """

    name = "sec_edgar"

    def __init__(self, user_agent: str | None = None):
        self._user_agent = user_agent or os.getenv(
            "SEC_USER_AGENT",
            "ResearchAgent/1.0 (due_diligence@example.com)",
        )

    # ------------------------------------------------------------------
    def search(self, query: SearchQuery, **kwargs) -> list[SearchDocument]:
        """Search SEC filings for a company.

        Two-step: (1) lookup CIK via EDGAR search, (2) fetch filings via
        submissions API and filter by form type.
        """
        from datetime import datetime

        cik_raw: str | None = None
        company_name = query.query.strip()

        # If site_hints provides a CIK or ticker, use it directly
        if query.site_hints:
            hint = query.site_hints[0]
            if hint.isdigit():
                cik_raw = hint  # direct CIK

        # Step 1: Lookup CIK via EDGAR search (unless already known)
        if not cik_raw:
            try:
                cik_raw, company_name = self._lookup_cik(company_name)
            except Exception:
                return []

        if not cik_raw:
            return []

        # Step 2: Fetch recent filings from submissions API
        try:
            filings = self._fetch_submissions(cik_raw, company_name)
        except Exception:
            return []

        # Step 3: Filter by form type
        form_types = _FORM_TYPES.get(query.source_type, _FORM_TYPES["all"])
        form_set: set[str] | None = set(form_types) if form_types else None

        results: list[SearchDocument] = []
        for f in filings:
            if form_set and f["form_type"] not in form_set:
                continue
            results.append(SearchDocument(
                url=f["url"],
                canonical_url=f["url"],
                title=f"{f['company']} — {f['form_type']} ({f['filing_date']})",
                raw_content=(
                    f"Company: {f['company']} (CIK {f['cik']})\n"
                    f"Form: {f['form_type']}\n"
                    f"Filing Date: {f['filing_date']}\n"
                    f"Report Date: {f.get('report_date', 'N/A')}\n"
                    f"Document: {f.get('primary_doc', '')}"
                ),
                source_type=query.source_type or "sec",
                provider=self.name,
                published_date=f["filing_date"],
                metadata={"form_type": f["form_type"], "cik": f["cik"]},
            ))

        return results[: query.max_results]

    # ------------------------------------------------------------------
    def _lookup_cik(self, company: str) -> tuple[str | None, str]:
        """Search EDGAR for a company and return (CIK, display_name)."""
        params: dict[str, Any] = {
            "q": company,
            "dateRange": "custom",
            "startdt": "2020-01-01",
            "enddt": "2030-12-31",
            "pageSize": 5,
        }
        url = f"{_SEARCH_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", self._user_agent)
        req.add_header("Accept", "application/json")

        with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        # Find the first result matching the company name (case-insensitive)
        company_lower = company.lower().strip()
        hits = self._get_hits(data)
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            src = hit.get("_source", hit) if not isinstance(hit, dict) else hit
            src = src.get("_source", src) if isinstance(src, dict) else src
            if not isinstance(src, dict):
                continue
            names = src.get("display_names") or []
            ciks = src.get("ciks") or []
            if names and ciks:
                for name in names:
                    if company_lower in str(name).lower():
                        return str(ciks[0]), str(names[0])

        # Fallback: return first result's CIK
        if hits:
            src = hits[0]
            if isinstance(src, dict):
                inner = src.get("_source", src)
                if isinstance(inner, dict):
                    ciks = inner.get("ciks") or []
                    names = inner.get("display_names") or []
                    if ciks:
                        return str(ciks[0]), str(names[0]) if names else company

        return None, company

    def _fetch_submissions(self, cik: str, _company: str = "") -> list[dict[str, str]]:
        """Fetch recent filings from the SEC submissions API."""
        # Zero-pad CIK to 10 digits
        cik_padded = cik.zfill(10)
        url = f"{_SUBMISSIONS_URL}/CIK{cik_padded}.json"
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", self._user_agent)
        req.add_header("Accept", "application/json")

        with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        recent = data.get("filings", {}).get("recent", {})
        if not recent:
            return []

        forms = recent.get("form", []) or []
        filing_dates = recent.get("filingDate", []) or []
        report_dates = recent.get("reportDate", []) or []
        accession_numbers = recent.get("accessionNumber", []) or []
        primary_docs = recent.get("primaryDocument", []) or []

        company = str(data.get("name", "") or _company or "")

        filings: list[dict[str, str]] = []
        for i in range(len(forms)):
            acc = accession_numbers[i] if i < len(accession_numbers) else ""
            acc_clean = acc.replace("-", "")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{acc_clean}/{acc}.txt"
            ) if acc else ""

            filings.append({
                "company": company,
                "cik": cik,
                "form_type": str(forms[i]),
                "filing_date": str(filing_dates[i]) if i < len(filing_dates) else "",
                "report_date": str(report_dates[i]) if i < len(report_dates) else "",
                "primary_doc": str(primary_docs[i]) if i < len(primary_docs) else "",
                "url": filing_url,
                "accession": str(acc),
            })

        return filings

    # ------------------------------------------------------------------
    @staticmethod
    def _get_hits(data: dict) -> list[dict]:
        """Extract hits from various EDGAR response shapes."""
        if not isinstance(data, dict):
            return []
        # Newer API shape
        hits = data.get("hits", {}).get("hits", [])
        if hits:
            return [h.get("_source", h) for h in hits]
        # Older API shape
        results = data.get("results", [])
        if results:
            return results
        return []

    def health_check(self) -> bool:
        try:
            req = urllib.request.Request(
                f"{_SEARCH_URL}?q=test&dateRange=custom&startdt=2024-01-01&enddt=2024-01-02&pageSize=1",
                method="GET",
            )
            req.add_header("User-Agent", self._user_agent)
            with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as resp:
                return resp.status == 200
        except Exception:
            return False
