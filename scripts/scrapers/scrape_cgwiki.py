"""Scrape VEX-related pages from tokeru.com/cgwiki (Matt Estela).

cgwiki is a VitePress SPA. Page content is embedded in JS module files
as rendered HTML inside template literals. This scraper:
  1. Fetches the main page to discover the VitePress hash map
  2. Fetches each page's JS module file (full, not lean)
  3. Extracts the HTML content from the JS string
  4. Parses into ChunkV2 chunks at heading boundaries

Usage:
    python scripts/scrapers/scrape_cgwiki.py
    python scripts/scrapers/scrape_cgwiki.py --dry-run
    python scripts/scrapers/scrape_cgwiki.py --pages JoyOfVex01 JoyOfVex02
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.schema import (
    PIPELINE_VERSION,
    ChunkV2,
    CodeBlock,
    ContentType,
    Difficulty,
    VEXContext,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SOURCE_ID = "cgwiki-vex"
SOURCE_AUTHORITY = 0.95
BASE_URL = "https://tokeru.com/cgwiki"
REQUEST_DELAY = 1.0  # seconds between requests
USER_AGENT = "vex-corpus/0.2.0 (VEX knowledge pipeline)"

DEFAULT_PAGES = (
    ["JoyOfVex"] +
    [f"JoyOfVex{i:02d}" for i in range(1, 21)] +
    ["HoudiniVex1", "HoudiniVex2", "HoudiniVex3"]
)

OUTPUT_DIR = PROJECT_ROOT / "output" / "cgwiki"

# VEX indicators for ambiguous code detection
_VEX_INDICATORS = re.compile(
    r'@\w+|ch[fivs]?\(|point\(|prim\(|set\w+attrib\(|'
    r'pcopen\(|pcfind\(|nearpoints?\(|addpoint\(|addprim\(|'
    r'\bvector\b|\bfloat\b|\bint\b|\bforeach\b'
)

_NAV_PATTERN = re.compile(r'\s*(prev|this|next)\s*:', re.IGNORECASE)


# ---------------------------------------------------------------------------
# VitePress content extraction
# ---------------------------------------------------------------------------

def discover_hash_map(session: requests.Session) -> dict[str, str]:
    """Fetch any cgwiki page and extract the VitePress hash map.

    Returns a dict mapping lowercase page names (e.g. 'joyofvex01.md') to
    their content hashes.
    """
    resp = session.get(f"{BASE_URL}/JoyOfVex.html", timeout=30)
    resp.raise_for_status()

    m = re.search(r'window\.__VP_HASH_MAP__=JSON\.parse\("(.*?)"\)', resp.text)
    if not m:
        raise RuntimeError("Could not find VitePress hash map")

    raw = m.group(1).encode().decode('unicode_escape')
    return json.loads(raw)


def discover_asset_prefix(session: requests.Session) -> str:
    """Find the base URL prefix for VitePress assets.

    VitePress modulepreload links reveal the exact prefix, which may include
    a subdirectory (e.g. /cgwiki/assets/).
    """
    resp = session.get(f"{BASE_URL}/JoyOfVex.html", timeout=30)
    resp.raise_for_status()

    m = re.search(r'modulepreload"[^>]+href="(/[^"]+/assets/)', resp.text)
    if m:
        prefix = m.group(1)
        # Convert to full URL
        return f"https://tokeru.com{prefix}"
    return f"{BASE_URL}/assets/"


def fetch_page_content(
    page_name: str,
    hash_map: dict[str, str],
    asset_prefix: str,
    session: requests.Session,
) -> str | None:
    """Fetch a page's JS module and extract the embedded HTML content.

    Returns the raw HTML string or None if the page can't be fetched.
    """
    # Look up the hash
    md_key = f"{page_name.lower()}.md"
    page_hash = hash_map.get(md_key)
    if not page_hash:
        # Try case variations
        for k, v in hash_map.items():
            if k.lower() == md_key.lower():
                page_hash = v
                break
    if not page_hash:
        print(f"  [WARN] No hash found for {page_name}")
        return None

    # Fetch the full JS module (not .lean.js)
    url = f"{asset_prefix}{page_name}.md.{page_hash}.js"
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [WARN] Failed to fetch {url}: {e}")
        return None

    text = resp.text

    # Extract HTML content from the JS module.
    # VitePress embeds content via a function call like: VAR=FN('HTML',NUM)
    # where FN is a single-letter variable (e, n, etc.) imported from framework.
    # The opening delimiter may be ' or ` and the closing may differ (minifier quirk).
    # Reliable strategy: find the first <h1 tag, trace back to the opening
    # delimiter, then find the closing DELIM,DIGITS) pattern near end of file.

    h1_idx = text.find("<h1")
    if h1_idx < 0:
        # Some pages may start with <h2 or <p
        h1_idx = text.find("<h2")
    if h1_idx < 0:
        h1_idx = text.find("<p")
    if h1_idx < 0:
        return None

    # The content starts at the first HTML tag. Trace back to find the
    # opening delimiter (the char just before <h1 after the function call).
    # Pattern: LETTER('  or LETTER(`
    content_start = h1_idx

    # Find end: the last occurrence of QUOTE,DIGITS) before the render function
    end_match = re.search(r"['\"`],\s*\d+\)\s*,\s*\w+=\[", text[content_start:])
    if end_match:
        content_end = content_start + end_match.start()
    else:
        # Fallback: search from end of file
        end_match2 = re.search(r"['\"`],\s*\d+\)", text[-300:])
        if end_match2:
            content_end = len(text) - 300 + end_match2.start()
        else:
            return None

    content = text[content_start:content_end]

    # Unescape JS string escaping
    content = content.replace("\\'", "'")

    return content


# ---------------------------------------------------------------------------
# HTML parsing into chunks
# ---------------------------------------------------------------------------

def _extract_section_label(page_name: str) -> str:
    if page_name == "HoudiniVex":
        return "HoudiniVex Tips"
    if page_name == "JoyOfVex":
        return "Joy of VEX Index"
    m = re.match(r"JoyOfVex(\d+)", page_name)
    if m:
        return f"Joy of VEX Day {int(m.group(1))}"
    return page_name


def _estimate_difficulty(page_name: str) -> str:
    m = re.match(r"JoyOfVex(\d+)", page_name)
    if not m:
        if page_name == "HoudiniVex":
            return Difficulty.INTERMEDIATE.value
        return Difficulty.BEGINNER.value
    day = int(m.group(1))
    if day <= 5:
        return Difficulty.BEGINNER.value
    if day <= 13:
        return Difficulty.INTERMEDIATE.value
    return Difficulty.ADVANCED.value


def _is_nav_text(text: str) -> bool:
    """Check if text is prev/this/next navigation."""
    return bool(_NAV_PATTERN.match(text.strip())) and len(text.strip()) < 150


def _clean_heading(text: str) -> str:
    """Clean heading text (remove anchors, pilcrows, link chars, etc.)."""
    # Remove Unicode zero-width spaces, pilcrows, replacement chars, anchors
    text = re.sub(r'[\u00b6\u200b\u200c\u200d\ufeff\ufffd\u2002\u2003\u2009]', '', text)
    # Remove VitePress anchor link markers (often rendered as invisible chars)
    text = re.sub(r'\s*\u200b?\s*$', '', text)
    return text.strip().rstrip('\u200b \t')


def parse_page_html(html: str, page_name: str) -> list[ChunkV2]:
    """Parse embedded HTML into ChunkV2 chunks split at headings."""
    soup = BeautifulSoup(html, "html.parser")
    section = _extract_section_label(page_name)
    difficulty = _estimate_difficulty(page_name)

    chunks = []
    current_heading = section
    current_prose = []
    current_code_blocks = []
    chunk_seq = 0

    def flush():
        nonlocal chunk_seq, current_heading, current_prose, current_code_blocks
        prose = "\n\n".join(p.strip() for p in current_prose if p.strip())
        if not prose and not current_code_blocks:
            current_prose = []
            current_code_blocks = []
            return

        # Skip nav-only sections
        if _is_nav_text(prose) and not current_code_blocks:
            current_prose = []
            current_code_blocks = []
            return

        chunk_seq += 1
        chunk_id = f"cgwiki_{page_name.lower()}_{chunk_seq:03d}"

        code_blocks = []
        for code_text in current_code_blocks:
            code_blocks.append(CodeBlock(
                code=code_text,
                is_complete=not code_text.rstrip().endswith("..."),
            ))

        chunk = ChunkV2(
            id=chunk_id,
            content=prose,
            code_blocks=code_blocks,
            content_type=ContentType.CONCEPT.value,
            difficulty=difficulty,
            vex_context=[VEXContext.SOP.value],
            source_id=SOURCE_ID,
            source_url=f"{BASE_URL}/{page_name}.html",
            source_authority=SOURCE_AUTHORITY,
            title=_clean_heading(current_heading),
            section=section,
            pipeline_version=PIPELINE_VERSION,
        )
        chunks.append(chunk)
        current_prose = []
        current_code_blocks = []

    # Walk all top-level elements
    for element in soup.children:
        if isinstance(element, NavigableString):
            text = str(element).strip()
            if text:
                current_prose.append(text)
            continue

        if not isinstance(element, Tag):
            continue

        # Heading -> flush previous chunk
        if element.name in ("h1", "h2", "h3", "h4"):
            flush()
            current_heading = _clean_heading(element.get_text())
            continue

        # Code blocks: <div class="language-*"> wraps <pre><code>
        if element.name == "div" and element.get("class"):
            classes = " ".join(element.get("class", []))
            if "language-" in classes:
                pre = element.find("pre")
                if pre:
                    code_text = pre.get_text().strip()
                    if code_text:
                        current_code_blocks.append(code_text)
                continue

        # Standalone <pre> blocks
        if element.name == "pre":
            code_text = element.get_text().strip()
            if code_text:
                current_code_blocks.append(code_text)
            continue

        # Regular content elements (p, ul, ol, blockquote, table, etc.)
        # Extract any nested code blocks first
        nested_pres = element.find_all("pre")
        for np in nested_pres:
            code_text = np.get_text().strip()
            if code_text:
                current_code_blocks.append(code_text)
            np.decompose()

        # Also extract inline VEX code from <code> tags if substantial
        inline_codes = element.find_all("code")
        for ic in inline_codes:
            code_text = ic.get_text().strip()
            # Only extract as code block if it's multi-line or clearly VEX
            if code_text and "\n" in code_text and _VEX_INDICATORS.search(code_text):
                current_code_blocks.append(code_text)
                ic.decompose()

        text = element.get_text(separator=" ", strip=True)
        text = re.sub(r'\s+', ' ', text).strip()
        if text and not _is_nav_text(text):
            current_prose.append(text)

    flush()
    return chunks


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def scrape_all(
    pages: list[str] | None = None,
    dry_run: bool = False,
    output_dir: Path | None = None,
) -> list[ChunkV2]:
    """Scrape cgwiki pages and output ChunkV2 JSONL files."""
    pages = pages or DEFAULT_PAGES
    output_dir = output_dir or OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    print("Discovering VitePress hash map...")
    hash_map = discover_hash_map(session)
    asset_prefix = discover_asset_prefix(session)
    print(f"  Found {len(hash_map)} pages in hash map")
    print(f"  Asset prefix: {asset_prefix}")

    if dry_run:
        print(f"\n[DRY RUN] Would scrape {len(pages)} pages:")
        for p in pages:
            key = f"{p.lower()}.md"
            status = "found" if key in hash_map else "NOT IN HASH MAP"
            print(f"  {p}: {status}")
        return []

    all_chunks = []
    total_code = 0

    print(f"\nScraping {len(pages)} pages...")

    for i, page_name in enumerate(pages):
        print(f"  [{i+1}/{len(pages)}] {page_name}...", end=" ", flush=True)

        html = fetch_page_content(page_name, hash_map, asset_prefix, session)
        if html is None:
            print("FAILED (no content)")
            continue

        chunks = parse_page_html(html, page_name)
        code_count = sum(len(c.code_blocks) for c in chunks)
        total_code += code_count
        all_chunks.extend(chunks)

        print(f"{len(chunks)} chunks, {code_count} code blocks")

        if i < len(pages) - 1:
            time.sleep(REQUEST_DELAY)

    # Write per-page JSONL
    page_chunks: dict[str, list[ChunkV2]] = {}
    for chunk in all_chunks:
        page = chunk.source_url.split("/")[-1].replace(".html", "")
        page_chunks.setdefault(page, []).append(chunk)

    for page, pchunks in sorted(page_chunks.items()):
        outfile = output_dir / f"{page.lower()}.jsonl"
        with open(outfile, "w", encoding="utf-8") as f:
            for c in pchunks:
                f.write(json.dumps(c.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")

    # Write combined output
    combined = output_dir / "cgwiki_all.jsonl"
    with open(combined, "w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps(chunk.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")

    print(f"\nTotal: {len(all_chunks)} chunks, {total_code} code blocks")
    print(f"Output: {output_dir}")

    return all_chunks


def main():
    parser = argparse.ArgumentParser(description="Scrape VEX content from tokeru.com/cgwiki")
    parser.add_argument("--pages", nargs="+", help="Specific page names to scrape")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be scraped")
    parser.add_argument("--output", type=Path, help="Output directory")
    args = parser.parse_args()

    scrape_all(pages=args.pages, dry_run=args.dry_run, output_dir=args.output)


if __name__ == "__main__":
    main()
