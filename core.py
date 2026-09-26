"""
core.py — ported, unchanged-in-logic version of the Tabbycat Static Archive
Colab notebook. app.py (Streamlit) imports this and calls run_archive().

Every function here does exactly what its notebook-cell counterpart did.
The only real changes from the notebook:
  - global variables -> an ArchiveConfig object passed around explicitly
  - print(...) -> a `log(...)` callback so the UI can show progress
  - the notebook's one hardcoded BASE_URL/SLUG/ROUNDS -> constructor args
  - normalize_base_url() is new: strips a trailing slash if present, so
    the app accepts the URL with or without one (the notebook only ever
    accepted it without).
"""

import re
import shutil
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def normalize_base_url(base_url: str) -> str:
    """Accept the base URL with or without a trailing slash; always return
    it without one, so every downstream f-string that does f"{BASE_URL}/..."
    behaves the same regardless of how the user typed it in."""
    return base_url.strip().rstrip("/")


class ArchiveConfig:
    def __init__(self, base_url: str, slug: str, num_rounds: int, out_dir: str):
        self.base_url = normalize_base_url(base_url)
        self.slug = slug.strip().strip("/")
        # Notebook used `list(range(1, ROUNDS))`. Here num_rounds is the
        # actual highest round number to try (inclusive), which is the
        # intuitive meaning shown in the UI, so we build the range as
        # 1..num_rounds inclusive.
        self.rounds = list(range(1, num_rounds + 1))
        self.tourn_url = f"{self.base_url}/{self.slug}"
        self.tourn_prefix = f"/{self.slug}"
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)


def _noop_log(msg: str):
    pass


# --------------------------------------------------------------------------
# Step 1 — Mirror Django server-rendered pages
# --------------------------------------------------------------------------

def fetch_and_save(url, out_path, log=_noop_log):
    """GET a URL and save the response body to out_path (creating dirs as needed)."""
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
    except Exception as e:
        log(f"  FAILED {url}: {e}")
        return None
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(r.content)
    log(f"  saved {url} -> {out_path}")
    return r.text


def build_django_pages(cfg: ArchiveConfig):
    OUT = cfg.out
    TOURN_URL = cfg.tourn_url
    django_pages = {
        f"{TOURN_URL}/": OUT / "index.html",
        f"{TOURN_URL}/motions/": OUT / "motions" / "index.html",
        f"{TOURN_URL}/motions/statistics/": OUT / "motions" / "statistics" / "index.html",
        f"{TOURN_URL}/feedback/progress/": OUT / "feedback" / "progress" / "index.html",
        f"{TOURN_URL}/participants/list/": OUT / "participants" / "list" / "index.html",
        f"{TOURN_URL}/participants/institutions/": OUT / "participants" / "institutions" / "index.html",
        # The Results index page lists links to every round's results and is
        # itself a plain Django page, distinct from the per-round pages below.
        f"{TOURN_URL}/results/": OUT / "results" / "index.html",
        # Pre-Allocated Sides, Check-Ins, and Schedule are all optional Tabbycat
        # features -- most tournaments don't enable them, so these fail
        # gracefully (fetch_and_save logs FAILED and moves on) for any
        # tournament that doesn't have them.
        f"{TOURN_URL}/draw/sides/": OUT / "draw" / "sides" / "index.html",
        f"{TOURN_URL}/checkins/status/people/": OUT / "checkins" / "status" / "people" / "index.html",
        f"{TOURN_URL}/schedule/": OUT / "schedule" / "index.html",
    }
    for rnd in cfg.rounds:
        round_base = OUT / "results" / "round" / str(rnd)
        django_pages[f"{TOURN_URL}/results/round/{rnd}/?view=debate"] = round_base / "index.html"
        django_pages[f"{TOURN_URL}/results/round/{rnd}/"] = round_base / "team" / "index.html"
    return django_pages


def mirror_django_pages(cfg: ArchiveConfig, log=_noop_log):
    django_pages = build_django_pages(cfg)
    log("Mirroring Django pages...")
    for url, path in django_pages.items():
        fetch_and_save(url, path, log=log)
    return django_pages


# --------------------------------------------------------------------------
# Step 2 — Render + snapshot Vue pages (with popovers/tooltips triggered)
# --------------------------------------------------------------------------

def discover_tab_pages(cfg: ArchiveConfig):
    OUT = cfg.out
    TOURN_URL = cfg.tourn_url
    BASE_URL = cfg.base_url
    homepage_path = OUT / "index.html"
    if not homepage_path.exists():
        return {}
    soup = BeautifulSoup(homepage_path.read_text(encoding="utf-8"), "html.parser")
    discovered = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full_url = urljoin(BASE_URL, href)
        if not full_url.startswith(TOURN_URL + "/tab/"):
            continue
        path_part = full_url[len(TOURN_URL):].strip("/")
        if not path_part:
            continue
        norm_url = full_url.rstrip("/") + "/"
        out_path = OUT / Path(path_part) / "index.html"
        discovered[norm_url] = out_path
    return discovered


def discover_break_category_slugs(cfg: ArchiveConfig, log=_noop_log):
    """Break categories (Open, Novice, etc.) are discovered straight from the
    homepage's own nav links to /break/<slug>/ -- the same way tab pages are
    discovered from its /tab/ links. This works for however many break
    categories a tournament has, without hardcoding category names."""
    OUT = cfg.out
    TOURN_URL = cfg.tourn_url
    BASE_URL = cfg.base_url
    homepage_path = OUT / "index.html"
    if not homepage_path.exists():
        return []
    soup = BeautifulSoup(homepage_path.read_text(encoding="utf-8"), "html.parser")
    needle = f"{TOURN_URL}/break/"
    slugs = set()
    for a in soup.find_all("a", href=True):
        full_url = urljoin(BASE_URL, a["href"])
        if not full_url.startswith(needle):
            continue
        path_part = full_url[len(needle):].strip("/")
        # Only take direct /break/<slug>/ links -- skip anything nested
        # deeper (e.g. an eligibility sub-page), and skip "bracket" itself
        # since that's the bracket-view path segment, not a category.
        if not path_part or "/" in path_part or path_part == "bracket":
            continue
        slugs.add(path_part)
    if slugs:
        log(f"Discovered {len(slugs)} break categor{'y' if len(slugs) == 1 else 'ies'} from homepage nav: {', '.join(sorted(slugs))}")
    return sorted(slugs)


def build_vue_pages(cfg: ArchiveConfig, log=_noop_log):
    OUT = cfg.out
    TOURN_URL = cfg.tourn_url
    vue_pages = discover_tab_pages(cfg)
    log(f"Discovered {len(vue_pages)} tab page(s) from homepage nav:")
    for u in vue_pages:
        log(f"    {u}")

    FALLBACK_TAB_PAGES = {
        f"{TOURN_URL}/tab/team/": OUT / "tab" / "team" / "index.html",
        f"{TOURN_URL}/tab/speaker/": OUT / "tab" / "speaker" / "index.html",
        f"{TOURN_URL}/tab/speaker/open/": OUT / "tab" / "speaker" / "open" / "index.html",
        f"{TOURN_URL}/tab/replies/": OUT / "tab" / "replies" / "index.html",
        f"{TOURN_URL}/tab/adjudicators/": OUT / "tab" / "adjudicators" / "index.html",
        f"{TOURN_URL}/tab/diversity/": OUT / "tab" / "diversity" / "index.html",
    }
    for _url, _path in FALLBACK_TAB_PAGES.items():
        if _url not in vue_pages:
            log(f"    (fallback) adding {_url} -- not found via nav discovery")
            vue_pages[_url] = _path

    # Elimination bracket pages (one per break category) are Vue-rendered,
    # same as tab pages, so they ride along in the same Playwright pass. A
    # plain snapshot is all that's needed here -- no hover/tooltip capture,
    # since the bracket's clickable links aren't something we need to
    # preserve, just its visual layout.
    for slug in discover_break_category_slugs(cfg, log=log):
        bracket_url = f"{TOURN_URL}/break/bracket/{slug}/"
        vue_pages[bracket_url] = OUT / "break" / "bracket" / slug / "index.html"

    return vue_pages


FIND_VISIBLE_D3_TOOLTIP_JS = """
() => {
    const tips = document.querySelectorAll('.d3-tooltip');
    for (const t of tips) {
        const style = getComputedStyle(t);
        if (style.display !== 'none' && style.visibility !== 'hidden' && t.innerHTML.trim()) {
            return t.innerHTML;
        }
    }
    return null;
}
"""


async def find_hoverable_point(page, el):
    """Find a point within el's bounding box that actually hit-tests back to el,
    rather than trusting the bounding-box center blindly. D3 donut/pie <path>
    arcs frequently have a bounding box whose center does NOT fall inside their
    own visible wedge (thin or wide-angle arcs especially) -- moving the real
    mouse there lands on a different, unrelated sibling arc instead."""
    await el.scroll_into_view_if_needed()

    box = await el.bounding_box()
    if not box:
        return None

    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    candidates = [(cx, cy)]
    for fx in (0.25, 0.5, 0.75):
        for fy in (0.25, 0.5, 0.75):
            candidates.append((box["x"] + box["width"] * fx, box["y"] + box["height"] * fy))

    for x, y in candidates:
        hits = await el.evaluate(
            "(el, [x, y]) => { const hit = document.elementFromPoint(x, y); "
            "return hit === el || (hit != null && el.contains(hit)); }",
            [x, y],
        )
        if hits:
            return (x, y)
    return None


async def capture_hover_d3_tooltips(page, log=_noop_log):
    hoverables = await page.query_selector_all(".hoverable")
    if not hoverables:
        return
    log(f"    found {len(hoverables)} hover-tooltip elements")
    captured = 0
    for i, el in enumerate(hoverables):
        try:
            await page.evaluate(
                "document.querySelectorAll('.hoverable').forEach(el => "
                "el.dispatchEvent(new MouseEvent('mouseleave', {bubbles:true})))"
            )
            await page.evaluate(
                "document.querySelectorAll('.d3-tooltip').forEach(t => { "
                "t.style.setProperty('display', 'none', 'important'); })"
            )
            await page.wait_for_timeout(120)

            point = await find_hoverable_point(page, el)
            if point is None:
                continue
            x, y = point
            await page.mouse.move(x, y)
            await page.mouse.move(x + 0.5, y + 0.5)

            last_html = None
            stable_html = None
            for _ in range(8):
                await page.wait_for_timeout(80)
                current = await page.evaluate(FIND_VISIBLE_D3_TOOLTIP_JS)
                if current and current == last_html:
                    stable_html = current
                    break
                last_html = current

            if stable_html:
                await el.evaluate(
                    "(el, html) => el.setAttribute('data-archived-popover', html)", stable_html
                )
                captured += 1
        except Exception:
            pass
    log(f"    captured content for {captured}/{len(hoverables)} hover elements")


DIVERSITY_EXTRACT_JS = """
() => {
    const gd = (window.vueData || {}).graphsData;
    if (!gd) return null;
    const regions = gd.regions || {};
    const order = [
        ['speakers_gender', false],
        ['speakers_categories', false],
        ['speakers_region', true],
        ['adjudicators_gender', false],
        ['adjudicators_position', false],
        ['adjudicators_region', true],
    ];
    const flat = [];
    for (const [key, isRegion] of order) {
        const sets = gd[key] || [];
        for (const set of sets) {
            const data = set.data;
            const total = data.reduce((s, d) => s + d.count, 0);
            for (const d of data) {
                flat.push({label: d.label, count: d.count, total: total, isRegion: isRegion});
            }
        }
    }

    function nicelabel(label, isRegion) {
        if (!isRegion) {
            if (label === 'Male') return 'Male identifying';
            if (label === 'NM') return 'Non-cis male identifying';
            if (label === 'Unknown') return 'Unspecified or unrecorded';
            return label;
        }
        return (regions[label] && regions[label].name) ? regions[label].name : String(label);
    }
    function percentage(count, total) {
        return ` (${Math.round((count / total) * 100)}%)`;
    }

    const hoverables = document.querySelectorAll('.hoverable');
    return {
        counts_match: hoverables.length === flat.length,
        hoverable_count: hoverables.length,
        data_count: flat.length,
        tooltips: flat.map(d => `<div class='tooltip-inner'>${d.count} ${percentage(d.count, d.total)}<br>${nicelabel(d.label, d.isRegion)}</div>`),
    };
}
"""


async def capture_diversity_tooltips_from_data(page, log=_noop_log):
    result = await page.evaluate(DIVERSITY_EXTRACT_JS)
    if not result:
        log("    WARNING: window.vueData.graphsData not found -- can't extract diversity data")
        return
    if not result["counts_match"]:
        log(
            f"    WARNING: hoverable count ({result['hoverable_count']}) != data count "
            f"({result['data_count']}) -- skipping to avoid pairing wrong tooltips to wrong arcs."
        )
        return
    await page.evaluate(
        """(tooltips) => {
            const hoverables = document.querySelectorAll('.hoverable');
            hoverables.forEach((el, i) => el.setAttribute('data-archived-popover', tooltips[i]));
        }""",
        result["tooltips"],
    )
    log(f"    captured content for {result['data_count']}/{result['data_count']} hover elements (direct data extraction)")


async def snapshot_vue_page(page, url, log=_noop_log):
    await page.goto(url, wait_until="networkidle", timeout=60000)
    await page.wait_for_timeout(1500)

    if "/tab/diversity" in url:
        await capture_diversity_tooltips_from_data(page, log=log)
    else:
        log("    skipping hover capture -- popovers on this page are pre-rendered by Vue (see shim), no active capture needed")

    return await page.content()


async def run_vue_capture(vue_pages, log=_noop_log):
    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        for url, out_path in vue_pages.items():
            log(f"Rendering {url}")
            html = await snapshot_vue_page(page, url, log=log)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(html, encoding="utf-8")
            results[url] = html
            log(f"  saved -> {out_path}")
        await browser.close()
    return results


async def mirror_vue_pages(cfg: ArchiveConfig, log=_noop_log):
    vue_pages = build_vue_pages(cfg, log=log)
    vue_html_by_url = await run_vue_capture(vue_pages, log=log)
    return vue_pages, vue_html_by_url


# --------------------------------------------------------------------------
# Step 3 — Discover + mirror participant records, ballots, and break pages
# --------------------------------------------------------------------------

EXTRA_PAGE_PATTERNS = [
    re.compile(r"/participants/(team|speaker|adjudicator)/\d+/"),
    re.compile(r"/results/debate/\d+/scoresheets/"),
    re.compile(r"/break/[\w/-]+/"),
]


def find_extra_links(html: str) -> set:
    """Scan both real <a href> tags AND links embedded inside
    data-archived-popover attribute strings."""
    found = set()
    soup = BeautifulSoup(html, "html.parser")

    def scan_soup_for_links(s):
        for a in s.find_all("a", href=True):
            href = a["href"]
            for pattern in EXTRA_PAGE_PATTERNS:
                m = pattern.search(href)
                if m:
                    found.add(m.group(0))

    scan_soup_for_links(soup)
    for el in soup.find_all(attrs={"data-archived-popover": True}):
        popover_html = el["data-archived-popover"]
        scan_soup_for_links(BeautifulSoup(popover_html, "html.parser"))

    for m in re.finditer(r'"link":\s*"([^"]+)"', html):
        candidate = m.group(1)
        for pattern in EXTRA_PAGE_PATTERNS:
            pm = pattern.search(candidate)
            if pm:
                found.add(pm.group(0))

    return found


def mirror_extra_pages(cfg: ArchiveConfig, vue_html_by_url, log=_noop_log):
    OUT = cfg.out
    TOURN_URL = cfg.tourn_url

    def extra_page_out_path(path: str) -> Path:
        return OUT / path.strip("/") / "index.html"

    fetched_paths = set()
    round_num = 0
    while True:
        round_num += 1
        found_paths = set()

        for url, html in vue_html_by_url.items():
            found_paths |= find_extra_links(html)
        for html_path in OUT.rglob("*.html"):
            found_paths |= find_extra_links(html_path.read_text(encoding="utf-8"))

        new_paths = found_paths - fetched_paths
        if not new_paths:
            log(f"Pass {round_num}: nothing new, stopping.")
            break

        log(f"Pass {round_num}: {len(new_paths)} new pages discovered")
        for path in sorted(new_paths):
            fetch_and_save(f"{TOURN_URL}{path}", extra_page_out_path(path), log=log)
            fetched_paths.add(path)

    log(f"Total extra pages mirrored: {len(fetched_paths)}")
    return fetched_paths


# --------------------------------------------------------------------------
# Step 4 — Download static assets + rewrite paths
# --------------------------------------------------------------------------

def rel_prefix(depth):
    return "../" * depth if depth > 0 else "./"


def make_rewrite_context(cfg: ArchiveConfig):
    """Returns a dict of the mutable state + closures the notebook used as
    module-level globals for this step, scoped per-run instead."""
    ctx = {
        "downloaded_static": set(),
    }

    def download_static_asset(static_path, log=_noop_log):
        if static_path in ctx["downloaded_static"]:
            return
        ctx["downloaded_static"].add(static_path)
        url = urljoin(cfg.base_url, static_path)
        out_path = cfg.out / static_path.lstrip("/")
        fetch_and_save(url, out_path, log=log)

    ctx["download_static_asset"] = download_static_asset
    return ctx


def normalize_internal_href(href: str, cfg: ArchiveConfig):
    BASE_URL = cfg.base_url
    TOURN_PREFIX = cfg.tourn_prefix
    if href.startswith(BASE_URL):
        href = href[len(BASE_URL):]
        if not href.startswith("/"):
            href = "/" + href
    if not href.startswith("/") or href.startswith("//"):
        return None
    if href.startswith("/static/"):
        return None
    if href == TOURN_PREFIX or href.startswith(TOURN_PREFIX + "/"):
        href = href[len(TOURN_PREFIX):] or "/"
    return href


def internal_path_to_relpath(path: str) -> str:
    path = path.strip("/")
    if path == "":
        return ""
    return f"{path}/"


def rewrite_view_toggle_links(soup, html_path: Path, prefix: str, cfg: ArchiveConfig) -> None:
    rel_parts = html_path.relative_to(cfg.out).parts
    if len(rel_parts) >= 4 and rel_parts[0] == "results" and rel_parts[1] == "round":
        round_dir = "/" + "/".join(rel_parts[:3]) + "/"
        for a in soup.find_all("a", href=True):
            if a["href"] == "?view=team":
                a["href"] = round_dir + "team/"
            elif a["href"] == "?view=debate":
                a["href"] = round_dir


def rewrite_links_in_fragment(fragment_html: str, prefix: str, cfg: ArchiveConfig) -> str:
    frag_soup = BeautifulSoup(fragment_html, "html.parser")
    changed = False
    for a in frag_soup.find_all("a", href=True):
        normalized = normalize_internal_href(a["href"], cfg)
        if normalized is not None:
            a["href"] = prefix + internal_path_to_relpath(normalized)
            changed = True
    return str(frag_soup) if changed else fragment_html


def rewrite_html_file(html_path: Path, cfg: ArchiveConfig, ctx, log=_noop_log):
    rel_parts = html_path.relative_to(cfg.out).parts
    depth = len(rel_parts) - 1

    if rel_parts and rel_parts[0] == "results":
        prefix = "/"
    else:
        prefix = rel_prefix(depth)

    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")

    for tag, attr in [("link", "href"), ("script", "src"), ("img", "src")]:
        for el in soup.find_all(tag):
            val = el.get(attr)
            if val and val.startswith("/static/"):
                ctx["download_static_asset"](val, log=log)
                el[attr] = prefix + val.lstrip("/")
            for dattr in ("data-light-href", "data-dark-href"):
                dval = el.get(dattr)
                if dval and dval.startswith("/static/"):
                    ctx["download_static_asset"](dval, log=log)
                    el[dattr] = prefix + dval.lstrip("/")

    for a in soup.find_all("a", href=True):
        normalized = normalize_internal_href(a["href"], cfg)
        if normalized is not None:
            a["href"] = prefix + internal_path_to_relpath(normalized)

    rewrite_view_toggle_links(soup, html_path, prefix, cfg)

    vue_mount = soup.find(id="vueMount")
    is_prerendered = vue_mount is not None and vue_mount.get_text(strip=True) != ""

    if is_prerendered:
        vue_mount["id"] = "vueMount-disabled"
    else:
        vue_data_link_re = re.compile(r'("(?:link|url)"\s*:\s*")([^"]*)(")')
        for script in soup.find_all("script"):
            if script.string and "window.vueData" in script.string:
                def _sub(m):
                    normalized = normalize_internal_href(m.group(2), cfg)
                    if normalized is None:
                        return m.group(0)
                    return m.group(1) + prefix + internal_path_to_relpath(normalized) + m.group(3)
                script.string = vue_data_link_re.sub(_sub, script.string)

    for el in soup.find_all(attrs={"data-archived-popover": True}):
        original = el["data-archived-popover"]
        el["data-archived-popover"] = rewrite_links_in_fragment(original, prefix, cfg)

    html_path.write_text(str(soup), encoding="utf-8")


def rewrite_all_html(cfg: ArchiveConfig, ctx, log=_noop_log):
    log("Rewriting HTML files (static assets + internal nav links + popover-embedded links)...")
    for html_path in cfg.out.rglob("*.html"):
        rewrite_html_file(html_path, cfg, ctx, log=log)
    log(f"Downloaded {len(ctx['downloaded_static'])} static assets so far")


CSS_URL_RE = re.compile(r"url\((['\"]?)(/static/[^'\")]+)\1\)")


def rewrite_css_file(css_path: Path, cfg: ArchiveConfig, ctx, log=_noop_log):
    depth = len(css_path.relative_to(cfg.out).parts) - 1
    prefix = rel_prefix(depth)
    text = css_path.read_text(encoding="utf-8", errors="ignore")

    def _sub(m):
        quote, path = m.group(1), m.group(2)
        ctx["download_static_asset"](path, log=log)
        return f"url({quote}{prefix}{path.lstrip('/')}{quote})"

    new_text = CSS_URL_RE.sub(_sub, text)
    if new_text != text:
        css_path.write_text(new_text, encoding="utf-8")


def rewrite_all_css(cfg: ArchiveConfig, ctx, log=_noop_log):
    changed = True
    seen_css = set()
    while changed:
        changed = False
        for css_path in cfg.out.rglob("*.css"):
            if css_path in seen_css:
                continue
            seen_css.add(css_path)
            before = len(ctx["downloaded_static"])
            rewrite_css_file(css_path, cfg, ctx, log=log)
            if len(ctx["downloaded_static"]) > before:
                changed = True
    log(f"Total static assets downloaded: {len(ctx['downloaded_static'])}")


# --------------------------------------------------------------------------
# Step 4b — Inject standalone interactivity (popovers + sortable tables)
# --------------------------------------------------------------------------

INTERACTIVITY_SHIM = """
<script data-archive-shim="1">
document.addEventListener('DOMContentLoaded', function () {

  function closeAllPopovers() {
    document.querySelectorAll('.popover[role="tooltip"]').forEach(function (p) {
      p.style.display = 'none';
    });
  }
  function positionAndShow(popover, trigger) {
    popover.style.display = 'block';
    popover.style.position = 'fixed';
    popover.style.transform = 'none';
    var rect = trigger.getBoundingClientRect();
    popover.style.top = (rect.bottom + 4) + 'px';
    popover.style.left = rect.left + 'px';
    popover.style.zIndex = 2000;
  }
  document.querySelectorAll('.popover[role="tooltip"]').forEach(function (popover) {
    var container = popover.parentElement;
    if (!container) return;
    var trigger = container.querySelector('.tooltip-trigger, [data-toggle]');
    if (!trigger) return;
    trigger.style.cursor = 'pointer';

    trigger.addEventListener('mouseenter', function () {
      closeAllPopovers();
      positionAndShow(popover, trigger);
    });
    container.addEventListener('mouseleave', function () {
      popover.style.display = 'none';
    });
    trigger.addEventListener('click', function (e) {
      e.stopPropagation();
      var isOpen = popover.style.display !== 'none';
      closeAllPopovers();
      if (!isOpen) positionAndShow(popover, trigger);
    });

    var closeBtn = popover.querySelector('.popover-close');
    if (closeBtn) closeBtn.addEventListener('click', closeAllPopovers);
  });
  document.addEventListener('click', closeAllPopovers);

  function closeArchivedPopover() {
    var existing = document.querySelector('.archived-popover-box');
    if (existing) existing.remove();
  }
  function showArchivedPopover(el) {
    closeArchivedPopover();
    var box = document.createElement('div');
    box.className = 'archived-popover-box popover bs-popover-bottom show';
    box.style.position = 'fixed';
    box.style.zIndex = 2000;
    box.style.display = 'block';
    box.innerHTML = el.getAttribute('data-archived-popover');
    document.body.appendChild(box);
    var rect = el.getBoundingClientRect();
    box.style.top = (rect.bottom + 4) + 'px';
    box.style.left = rect.left + 'px';
  }
  document.querySelectorAll('[data-archived-popover]').forEach(function (el) {
    el.style.cursor = 'pointer';
    el.addEventListener('mouseenter', function () { showArchivedPopover(el); });
    el.addEventListener('mouseleave', closeArchivedPopover);
    el.addEventListener('click', function (e) {
      e.stopPropagation();
      showArchivedPopover(el);
    });
  });

  function extractSortValue(cell) {
    var text = (cell ? cell.textContent : '').trim();
    var num = parseFloat(text.replace(/,/g, ''));
    return isNaN(num) ? text.toLowerCase() : num;
  }
  document.querySelectorAll('table').forEach(function (table) {
    var thead = table.querySelector('thead');
    var tbody = table.querySelector('tbody');
    if (!thead || !tbody) return;
    thead.querySelectorAll('th').forEach(function (th, colIndex) {
      th.style.cursor = 'pointer';
      var asc = true;
      th.addEventListener('click', function () {
        var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
        rows.sort(function (a, b) {
          var av = extractSortValue(a.children[colIndex]);
          var bv = extractSortValue(b.children[colIndex]);
          if (av < bv) return asc ? -1 : 1;
          if (av > bv) return asc ? 1 : -1;
          return 0;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
        asc = !asc;
      });
    });
  });
});
</script>
"""


def inject_interactivity_shim(html_path: Path):
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    if soup.find("script", attrs={"data-archive-shim": "1"}):
        return
    shim_soup = BeautifulSoup(INTERACTIVITY_SHIM, "html.parser")
    if soup.body:
        soup.body.append(shim_soup)
    else:
        soup.append(shim_soup)
    html_path.write_text(str(soup), encoding="utf-8")


def inject_all_shims(cfg: ArchiveConfig, log=_noop_log):
    log("Injecting standalone popover + sortable-table shim into every page...")
    for html_path in cfg.out.rglob("*.html"):
        inject_interactivity_shim(html_path)
    log("Done.")


# --------------------------------------------------------------------------
# Step 4c — Remove the Login nav link
# --------------------------------------------------------------------------

def remove_login_links(cfg: ArchiveConfig, log=_noop_log):
    removed_login_count = 0
    for html_path in cfg.out.rglob("*.html"):
        html = html_path.read_text(encoding="utf-8")
        if "accounts/login/" not in html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        changed = False
        for a in soup.find_all("a", href=True):
            if not a["href"].rstrip("/").endswith("accounts/login"):
                continue
            target = a.find_parent("li") or a
            target.decompose()
            changed = True
            removed_login_count += 1
        if changed:
            html_path.write_text(str(soup), encoding="utf-8")
    log(f"Removed {removed_login_count} Login nav link(s) across the export")
    return removed_login_count


# --------------------------------------------------------------------------
# Step 5 — Sanity check
# --------------------------------------------------------------------------

def sanity_check(cfg: ArchiveConfig, log=_noop_log):
    OUT = cfg.out
    TOURN_PREFIX = cfg.tourn_prefix
    log("Checking for any remaining absolute /static/ or internal nav references...")
    leftover = 0
    for html_path in OUT.rglob("*.html"):
        text = html_path.read_text(encoding="utf-8")
        for m in re.finditer(r'(?:href|src)="(/static/[^"]+)"', text):
            log(f"  LEFTOVER STATIC in {html_path.relative_to(OUT)}: {m.group(1)}")
            leftover += 1
        for m in re.finditer(rf'href="({re.escape(TOURN_PREFIX)}[^"]*)"', text):
            log(f"  LEFTOVER NAV LINK in {html_path.relative_to(OUT)}: {m.group(1)}")
            leftover += 1
    log(f"{leftover} leftover absolute references found" if leftover else "All clear.")
    return leftover


# --------------------------------------------------------------------------
# Step 6 — Zip the archive
# --------------------------------------------------------------------------

def zip_archive(cfg: ArchiveConfig, zip_base_path: str, log=_noop_log) -> str:
    shutil.make_archive(zip_base_path, "zip", cfg.out)
    zip_path = f"{zip_base_path}.zip"
    log(f"Zipped to {zip_path}")
    return zip_path


# --------------------------------------------------------------------------
# Orchestration — mirrors the notebook's cell run order top to bottom
# --------------------------------------------------------------------------

async def run_archive_async(base_url, slug, num_rounds, out_dir, zip_base_path, log=_noop_log):
    cfg = ArchiveConfig(base_url, slug, num_rounds, out_dir)

    log(f"Archiving: {cfg.tourn_url}")
    log(f"Output dir: {cfg.out}")

    mirror_django_pages(cfg, log=log)

    vue_pages, vue_html_by_url = await mirror_vue_pages(cfg, log=log)

    mirror_extra_pages(cfg, vue_html_by_url, log=log)

    ctx = make_rewrite_context(cfg)
    rewrite_all_html(cfg, ctx, log=log)
    rewrite_all_css(cfg, ctx, log=log)

    inject_all_shims(cfg, log=log)
    remove_login_links(cfg, log=log)

    sanity_check(cfg, log=log)

    zip_path = zip_archive(cfg, zip_base_path, log=log)
    return zip_path
