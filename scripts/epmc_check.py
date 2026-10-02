#!/usr/bin/env python3
"""Проверка запасного поиска Europe PMC REST (без SearXNG)."""

import json
import sys
import urllib.parse
import urllib.request

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "eprenetapopt TP53"
    url = BASE + "?" + urllib.parse.urlencode(
        {"query": query, "format": "json", "pageSize": 4, "resultType": "core"}
    )
    resp = urllib.request.urlopen(url, timeout=60)
    data = json.loads(resp.read().decode("utf-8", "replace"))
    hits = (data.get("resultList") or {}).get("result") or []
    print(f"query={query!r} hitCount={data.get('hitCount')} results={len(hits)}")
    for item in hits[:4]:
        pmid = item.get("pmid")
        title = (item.get("title") or "")[:100]
        print(f"- PMID {pmid}: {title}")
    if not hits:
        sys.exit(1)


if __name__ == "__main__":
    main()
