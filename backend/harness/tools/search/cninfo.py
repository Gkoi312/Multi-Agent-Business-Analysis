"""
CNINFO (巨潮资讯) adapter — Chinese listed company announcements.

巨潮资讯 is the official designated disclosure platform by the CSRC
(China Securities Regulatory Commission) for all Chinese listed companies.
It contains annual reports, prospectuses, material announcements, and more.

Free and open — no API key required.
Covers: Shanghai (SSE), Shenzhen (SZSE), and Beijing (BSE) stock exchanges.

Usage::

    adapter = CninfoAdapter()
    results = adapter.search(SearchQuery(
        query="宁德时代",
        source_type="annual",
        freshness_hint="recent",
    ))
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.parse
from typing import Any

from harness.tools.search.base import SearchDocument, SearchQuery, SearchTool

# CNINFO full-text announcement search endpoint
_CNINFO_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"

# Form type mapping for source_type → CNINFO announcement category
_CATEGORY_MAP: dict[str, str] = {
    "annual": "category_ndbg_szsh;category_ndbg_bj",       # 年度报告
    "quarterly": "category_bndbg_szsh;",                     # 半年度/季度报告
    "ipo": "category_zgzss_szsh;category_zgzss_bj",         # 招股说明书
    "current": "category_lsgg_szsh;category_lsgg_bj",        # 临时公告
    "all": "",
}

# Freshness → date range string for seDate parameter
_FRESHNESS_MAP: dict[str, str] = {
    "recent": "",   # CNINFO defaults to recent; exact date computed below
    "balanced": "",
    "any": "",
}


class CninfoAdapter(SearchTool):
    """Search Chinese listed company announcements via 巨潮资讯.

    Maps ``SearchQuery`` fields:
    - query → company name or announcement keyword
    - source_type → announcement category (annual, quarterly, ipo, current, all)
    - freshness_hint → date range ("recent" → 6 months, "balanced" → 2 years)
    - site_hints → stock codes to narrow search (e.g. ["000001", "600000"])
    """

    name = "cninfo"

    def __init__(self, timeout: int = 15):
        self._timeout = timeout

    # ------------------------------------------------------------------
    def search(self, query: SearchQuery, **kwargs) -> list[SearchDocument]:
        from datetime import datetime, timedelta

        today = datetime.utcnow()
        # Compute date range for seDate parameter
        date_map = {
            "recent": (today - timedelta(days=180)).strftime("%Y-%m-%d"),
            "balanced": (today - timedelta(days=730)).strftime("%Y-%m-%d"),
            "any": "",
        }
        start_date = date_map.get(query.freshness_hint, "")
        se_date = f"{start_date}~{today.strftime('%Y-%m-%d')}" if start_date else ""

        category = _CATEGORY_MAP.get(query.source_type, "")

        # Use site_hints as stock codes if present
        stock_code = ""
        if query.site_hints:
            # Take the first hint that looks like a 6-digit stock code
            for hint in query.site_hints:
                if len(hint) == 6 and hint.isdigit():
                    stock_code = hint
                    break

        form_data = urllib.parse.urlencode({
            "pageNum": 1,
            "pageSize": min(query.max_results, 30),
            "column": "",
            "tabName": "fulltext",
            "plate": "sz;sh;bj",   # all three exchanges
            "stock": stock_code,
            "searchkey": query.query,
            "seDate": se_date,
            "category": category,
            "trade": "",
            "secid": "",
            "sortName": "date",
            "sortType": "desc",
        }).encode("utf-8")

        req = urllib.request.Request(_CNINFO_URL, data=form_data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
        req.add_header("User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0")
        req.add_header("Accept", "application/json, text/plain, */*")
        req.add_header("Referer", "http://www.cninfo.com.cn/new/fulltextSearch")
        req.add_header("Origin", "http://www.cninfo.com.cn")

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(body)
        except Exception:
            return []

        if not isinstance(data, dict):
            return []

        announcements = data.get("announcements") or []
        if not isinstance(announcements, list):
            return []

        results: list[SearchDocument] = []
        for ann in announcements:
            if not isinstance(ann, dict):
                continue

            sec_name = str(ann.get("secName", "") or "")
            sec_code = str(ann.get("secCode", "") or "")
            title = str(ann.get("announcementTitle", "") or "")
            adjunct_url = str(ann.get("adjunctUrl", "") or "")

            # Convert timestamp (milliseconds) to ISO date
            ann_time = ann.get("announcementTime")
            pub_date = ""
            if ann_time:
                try:
                    ts = int(ann_time) / 1000.0
                    pub_date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                except (ValueError, OSError):
                    pass

            # Build PDF URL
            if adjunct_url:
                if adjunct_url.startswith("http"):
                    full_url = adjunct_url
                else:
                    full_url = f"http://static.cninfo.com.cn/{adjunct_url.lstrip('/')}"
            else:
                ann_id = str(ann.get("announcementId", "") or ann.get("id", "") or "")
                full_url = (
                    f"http://www.cninfo.com.cn/new/disclosure/detail?"
                    f"announcementId={ann_id}"
                    if ann_id else ""
                )

            snippet = (
                f"公司: {sec_name} ({sec_code})\n"
                f"公告: {title}\n"
                f"日期: {pub_date}"
            )

            results.append(SearchDocument(
                url=full_url,
                canonical_url=full_url,
                title=f"{sec_name} — {title}",
                raw_content=snippet,
                source_type=query.source_type or "announcement",
                provider=self.name,
                published_date=pub_date,
                raw=ann,
            ))

        return results[: query.max_results]

    def health_check(self) -> bool:
        try:
            form_data = urllib.parse.urlencode({
                "pageNum": 1, "pageSize": 1, "column": "",
                "tabName": "fulltext", "plate": "sz;sh",
                "stock": "", "searchkey": "年报", "seDate": "",
                "category": "", "trade": "", "secid": "",
                "sortName": "date", "sortType": "desc",
            }).encode("utf-8")
            req = urllib.request.Request(_CNINFO_URL, data=form_data, method="POST")
            req.add_header("User-Agent", "Mozilla/5.0")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.status == 200
        except Exception:
            return False
