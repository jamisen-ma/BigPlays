from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx
from bs4 import BeautifulSoup

from bigplays.utils.time_utils import utc_now


DEFAULT_API_BASE = "https://ppv.to/api"
DEFAULT_STREAM_PAGE_TEMPLATE = "https://ppv.to/s/{uri_name}"


@dataclass
class PPVStream:
    id: int
    name: str
    tag: str
    poster: Optional[str]
    uri_name: str
    starts_at: Optional[int]
    ends_at: Optional[int]
    always_live: bool
    category_name: Optional[str]
    iframe: Optional[str] = None
    allowpaststreams: Optional[bool] = None

    def is_live_now(self, now: Optional[datetime] = None) -> bool:
        if self.always_live:
            return True
        now = now or utc_now()
        if self.starts_at and self.ends_at:
            return self.starts_at <= int(now.timestamp()) <= self.ends_at
        return False


@dataclass
class PPVCategory:
    id: int
    category: str
    always_live: bool
    streams: List[PPVStream]


def fetch_streams(api_base: str = DEFAULT_API_BASE, timeout: float = 10.0) -> List[PPVCategory]:
    url = f"{api_base}/streams"
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
    cats: List[PPVCategory] = []
    for c in data.get("streams", []):
        streams: List[PPVStream] = []
        for s in c.get("streams", []):
            streams.append(
                PPVStream(
                    id=int(s.get("id")),
                    name=s.get("name", ""),
                    tag=s.get("tag", ""),
                    poster=s.get("poster"),
                    uri_name=s.get("uri_name", ""),
                    starts_at=s.get("starts_at"),
                    ends_at=s.get("ends_at"),
                    always_live=bool(s.get("always_live", 0)),
                    category_name=s.get("category_name"),
                    iframe=s.get("iframe"),
                    allowpaststreams=bool(s.get("allowpaststreams", 0)),
                )
            )
        cats.append(
            PPVCategory(
                id=int(c.get("id")),
                category=c.get("category", ""),
                always_live=bool(c.get("always_live", 0)),
                streams=streams,
            )
        )
    return cats


def extract_hls_from_html(html: str) -> Optional[str]:
    # Try tags first
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["source", "video", "a", "script"]):
        for attr in ("src", "data-src", "href"):
            val = tag.get(attr)
            if isinstance(val, str) and ".m3u8" in val:
                return val
    # Fallback: regex search
    m = re.search(r"https?://[^'\"\s>]+\.m3u8[^'\"\s<]*", html)
    if m:
        return m.group(0)
    return None


def resolve_hls_url(stream: PPVStream, api_base: str = DEFAULT_API_BASE, page_template: str = DEFAULT_STREAM_PAGE_TEMPLATE, timeout: float = 10.0) -> Optional[str]:
    # If API directly provides an iframe URL, fetch it
    candidate_pages: List[str] = []
    if stream.iframe:
        candidate_pages.append(stream.iframe)
    # Also try known page template using uri_name
    if stream.uri_name:
        candidate_pages.append(page_template.format(uri_name=stream.uri_name))

    with httpx.Client(follow_redirects=True, timeout=timeout, headers={"User-Agent": "bigplays/0.1"}) as client:
        for page in candidate_pages:
            try:
                r = client.get(page)
                if r.status_code >= 400:
                    continue
                hls = extract_hls_from_html(r.text)
                if hls:
                    # Resolve relative URLs against page
                    hls_abs = httpx.URL(hls).join(r.url).human_repr() if not hls.startswith("http") else hls
                    return hls_abs
            except Exception:
                continue
    return None


def list_live_streams(categories: Optional[List[str]] = None, api_base: str = DEFAULT_API_BASE) -> List[PPVStream]:
    cats = fetch_streams(api_base=api_base)
    result: List[PPVStream] = []
    for c in cats:
        if categories and c.category not in categories:
            continue
        for s in c.streams:
            if s.is_live_now():
                result.append(s)
    return result


