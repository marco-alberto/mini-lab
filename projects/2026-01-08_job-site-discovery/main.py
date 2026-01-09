from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://animaladvocacycareers.org/"
OUT_DIR = "output"
TIMEOUT = 20

UA = "mini-lab-job-site-discovery/1.0 (+https://github.com/marco-alberto/mini-lab)"


@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str | None
    text: str | None
    error: str | None = None


def fetch(url: str) -> FetchResult:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, allow_redirects=True)
        ct = r.headers.get("content-type", "")
        return FetchResult(
            url=r.url,
            status=r.status_code,
            content_type=ct,
            text=r.text if r.ok and ("text" in ct or "xml" in ct or "json" in ct) else None,
            error=None if r.ok else f"HTTP {r.status_code}",
        )
    except Exception as e:
        return FetchResult(url=url, status=0, content_type=None, text=None, error=str(e))


def looks_like_sitemap(body: str) -> bool:
    return "<urlset" in body or "<sitemapindex" in body or "Sitemap:" in body


def extract_robots_info(robots_text: str) -> dict:
    sitemaps = []
    rules = []
    for line in robots_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("sitemap:"):
            sitemaps.append(line.split(":", 1)[1].strip())
        if re.match(r"(?i)^(user-agent|allow|disallow|crawl-delay):", line):
            rules.append(line)
    return {"sitemaps": sitemaps, "rules": rules}


def extract_links_from_html(url: str, html: str) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    links = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if not href:
            continue
        abs_url = urljoin(url, href)
        # quedarnos en mismo dominio
        if urlparse(abs_url).netloc == urlparse(BASE_URL).netloc:
            links.add(abs_url.split("#")[0])
    return links


def score_candidate(u: str) -> int:
    path = urlparse(u).path.lower()
    score = 0
    for kw in ["job", "jobs", "career", "careers", "search", "board", "vacan", "role", "position"]:
        if kw in path:
            score += 3
    # rutas típicas wordpress de taxonomías útiles
    for kw in ["/job/", "/job_type/", "/job_function/", "/job-board/"]:
        if kw in path:
            score += 5
    # penaliza cosas poco útiles
    for bad in ["/wp-admin", "/wp-content", "/tag/", "/category/"]:
        if bad in path:
            score -= 5
    return score


def write_report(report_path: str, sections: list[str]) -> None:
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(sections).strip() + "\n")


def write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 1) robots.txt
    robots_url = urljoin(BASE_URL, "robots.txt")
    robots = fetch(robots_url)

    robots_info = {"sitemaps": [], "rules": []}
    if robots.text:
        robots_info = extract_robots_info(robots.text)

    # 2) sitemaps candidatos (probamos varios)
    sitemap_candidates = []
    # si robots declara sitemaps, primero esos
    sitemap_candidates.extend(robots_info["sitemaps"])
    # y los comunes
    for p in ["sitemap.xml", "wp-sitemap.xml", "sitemap/"]:
        sitemap_candidates.append(urljoin(BASE_URL, p))

    sitemap_results: list[FetchResult] = []
    for u in dict.fromkeys(sitemap_candidates):  # dedupe conservando orden
        res = fetch(u)
        sitemap_results.append(res)

    # 3) semilla: job board + sitemap html (si existe)
    seeds = [
        urljoin(BASE_URL, "job-board/"),
        urljoin(BASE_URL, "sitemap/"),
        urljoin(BASE_URL, "job_function/research-or-data/"),
        urljoin(BASE_URL, "job_type/part-time/"),
    ]

    discovered_links = set()
    fetched_pages: list[FetchResult] = []
    for seed in seeds:
        r = fetch(seed)
        fetched_pages.append(r)
        if r.text and (r.content_type or "").startswith("text/html"):
            discovered_links |= extract_links_from_html(r.url, r.text)

    # 4) rank de candidatos
    candidates = []
    for u in discovered_links | set(seeds):
        candidates.append({"url": u, "score": score_candidate(u)})

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # 5) generar output
    import os
    os.makedirs(OUT_DIR, exist_ok=True)

    report_sections = []
    report_sections.append(f"# Job Site Discovery Report\n\n- Base: {BASE_URL}\n- Generated (UTC): {ts}")

    report_sections.append(
        "## robots.txt\n"
        f"- URL: {robots.url}\n"
        f"- Status: {robots.status}\n"
        f"- Content-Type: {robots.content_type}\n"
        + ("\n### Rules (raw)\n" + "\n".join(f"- `{r}`" for r in robots_info["rules"][:80]) if robots_info["rules"] else "\n- (No rules parsed or robots unavailable)")
        + ("\n\n### Sitemaps (declared)\n" + "\n".join(f"- {s}" for s in robots_info["sitemaps"]) if robots_info["sitemaps"] else "\n\n- (No sitemaps declared in robots)")
    )

    sitemap_lines = ["## Sitemap checks"]
    for r in sitemap_results:
        note = f"{r.status} / {r.content_type or '-'}"
        if r.text and looks_like_sitemap(r.text):
            note += " ✅ looks like sitemap"
        sitemap_lines.append(f"- {r.url} — {note}")
    report_sections.append("\n".join(sitemap_lines))

    fetched_lines = ["## Seed pages fetched"]
    for r in fetched_pages:
        fetched_lines.append(f"- {r.url} — {r.status} / {r.content_type or '-'}")
    report_sections.append("\n".join(fetched_lines))

    top = candidates[:40]
    report_sections.append(
        "## Top URL candidates (by heuristic score)\n"
        + "\n".join([f"- **{c['score']:>2}** {c['url']}" for c in top])
        + "\n\n> Nota: score alto suele significar rutas de jobs, filtros, o páginas útiles para scraping."
    )

    write_report(os.path.join(OUT_DIR, "report.md"), report_sections)
    write_csv(os.path.join(OUT_DIR, "urls_candidates.csv"), candidates)

    print("OK ✅ Generated:")
    print(f"- {OUT_DIR}/report.md")
    print(f"- {OUT_DIR}/urls_candidates.csv")


if __name__ == "__main__":
    main()
