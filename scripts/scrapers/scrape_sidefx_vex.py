"""Scrape VEX function reference from SideFX documentation.

Creates one chunk per VEX function with signature, description, parameters,
return type, example code, context availability, and related functions.

The SideFX docs may block automated scraping. If scraping fails, this script
falls back to parsing locally saved HTML files from a configured directory.

Usage:
    python scripts/scrapers/scrape_sidefx_vex.py
    python scripts/scrapers/scrape_sidefx_vex.py --local-docs /path/to/saved/html
    python scripts/scrapers/scrape_sidefx_vex.py --dry-run
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

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

SOURCE_ID = "sidefx-vex-reference"
SOURCE_AUTHORITY = 1.0
BASE_URL = "https://www.sidefx.com/docs/houdini/vex/functions"
REQUEST_DELAY = 2.0  # be respectful to SideFX servers
USER_AGENT = "vex-corpus/0.2.0 (VEX knowledge pipeline)"

OUTPUT_DIR = PROJECT_ROOT / "output" / "sidefx_reference"

# VEX context categories as used in SideFX docs
CONTEXT_MAP = {
    "any": [VEXContext.SOP.value, VEXContext.DOP.value, VEXContext.COP.value,
            VEXContext.CHOP.value, VEXContext.CVEX.value, VEXContext.MATERIAL.value],
    "shading": [VEXContext.MATERIAL.value],
    "cop": [VEXContext.COP.value],
    "chop": [VEXContext.CHOP.value],
    "sop": [VEXContext.SOP.value],
    "dop": [VEXContext.DOP.value],
    "cvex": [VEXContext.CVEX.value],
    "fog": [VEXContext.MATERIAL.value],
    "surface": [VEXContext.MATERIAL.value],
    "displacement": [VEXContext.MATERIAL.value],
    "light": [VEXContext.MATERIAL.value],
}


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def fetch_function_index(session: requests.Session) -> list[dict]:
    """Fetch the main VEX functions index page and extract function links.

    Returns list of dicts: {name, url, category, brief}
    """
    resp = session.get(f"{BASE_URL}/index.html", timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    functions = []

    # SideFX function index has function names in links organized by category
    current_category = ""

    for element in soup.find_all(["h2", "h3", "a", "dt", "dd"]):
        if element.name in ("h2", "h3"):
            current_category = element.get_text(strip=True).lower()
            continue

        if element.name == "a":
            href = element.get("href", "")
            name = element.get_text(strip=True)
            if href and name and not href.startswith("#") and not href.startswith("http"):
                # Clean up the href
                if not href.endswith(".html"):
                    href = href + ".html"
                functions.append({
                    "name": name,
                    "url": f"{BASE_URL}/{href}",
                    "category": current_category,
                    "brief": "",
                })

        if element.name == "dt":
            link = element.find("a")
            if link:
                href = link.get("href", "")
                name = link.get_text(strip=True)
                brief = ""
                dd = element.find_next_sibling("dd")
                if dd:
                    brief = dd.get_text(strip=True)[:200]
                if href and name:
                    if not href.endswith(".html"):
                        href = href + ".html"
                    functions.append({
                        "name": name,
                        "url": f"{BASE_URL}/{href}",
                        "category": current_category,
                        "brief": brief,
                    })

    return functions


def fetch_function_page(url: str, session: requests.Session) -> str | None:
    """Fetch a single function documentation page."""
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code == 403:
            return None  # Blocked
        resp.raise_for_status()
        return resp.text
    except requests.RequestException:
        return None


def parse_function_page(html: str, func_info: dict) -> ChunkV2 | None:
    """Parse a SideFX function page into a ChunkV2 chunk."""
    soup = BeautifulSoup(html, "html.parser")

    # Extract function signature
    signature = ""
    sig_el = soup.find("pre", class_="signature") or soup.find("code", class_="signature")
    if sig_el:
        signature = sig_el.get_text(strip=True)
    else:
        # Try the first <pre> in the page
        first_pre = soup.find("pre")
        if first_pre:
            text = first_pre.get_text(strip=True)
            if func_info["name"] in text and "(" in text:
                signature = text

    # Extract description
    description = ""
    desc_section = soup.find("div", class_="content") or soup.find("article") or soup.body
    if desc_section:
        paras = desc_section.find_all("p")
        desc_parts = []
        for p in paras:
            text = p.get_text(strip=True)
            if text and len(text) > 10:
                desc_parts.append(text)
            if len(desc_parts) >= 3:
                break
        description = "\n\n".join(desc_parts)

    # Extract example code
    examples = []
    for pre in soup.find_all("pre"):
        code = pre.get_text(strip=True)
        if code and func_info["name"] in code and len(code) > 20:
            # Skip the signature itself
            if code != signature:
                examples.append(code)

    # Extract parameters
    params = []
    param_section = soup.find("dl") or soup.find("table", class_="parameters")
    if param_section:
        dts = param_section.find_all("dt")
        dds = param_section.find_all("dd")
        for dt, dd in zip(dts, dds):
            params.append(f"{dt.get_text(strip=True)}: {dd.get_text(strip=True)[:150]}")

    # Extract return type from signature
    return_type = ""
    if signature:
        # Pattern: "type funcname(..."
        m = re.match(r'(\w+(?:\[\])?)\s+\w+\s*\(', signature)
        if m:
            return_type = m.group(1)

    # Extract context/availability
    contexts = []
    for ctx_name, ctx_values in CONTEXT_MAP.items():
        if ctx_name in func_info.get("category", "").lower():
            contexts.extend(ctx_values)
    if not contexts:
        contexts = [VEXContext.SOP.value]

    # Extract related functions
    related = []
    see_also = soup.find(string=re.compile(r"See also|Related", re.IGNORECASE))
    if see_also:
        parent = see_also.find_parent()
        if parent:
            for link in parent.find_all("a"):
                name = link.get_text(strip=True)
                if name and name != func_info["name"]:
                    related.append(name)

    # Build content
    content_parts = []
    if signature:
        content_parts.append(f"Signature: {signature}")
    if return_type:
        content_parts.append(f"Returns: {return_type}")
    if description:
        content_parts.append(description)
    if params:
        content_parts.append("Parameters:\n" + "\n".join(f"  - {p}" for p in params))
    if related:
        content_parts.append(f"Related: {', '.join(related)}")

    content = "\n\n".join(content_parts)
    if not content:
        return None

    code_blocks = []
    if signature:
        code_blocks.append(CodeBlock(code=signature, line_context="signature", is_complete=True))
    for ex in examples:
        code_blocks.append(CodeBlock(code=ex, line_context="example", is_complete=True))

    func_name = func_info["name"]
    chunk = ChunkV2(
        id=f"sidefx_ref_{func_name.lower()}",
        content=content,
        code_blocks=code_blocks,
        content_type=ContentType.REFERENCE.value,
        difficulty=Difficulty.INTERMEDIATE.value,  # Reference is context-dependent
        vex_context=list(set(contexts)),
        source_id=SOURCE_ID,
        source_url=func_info["url"],
        source_authority=SOURCE_AUTHORITY,
        title=func_name,
        section="VEX Function Reference",
        functions_referenced=[func_name] + related[:5],
        pipeline_version=PIPELINE_VERSION,
    )
    return chunk


def parse_local_html(html_dir: Path) -> list[ChunkV2]:
    """Parse locally saved SideFX HTML files."""
    chunks = []
    html_files = sorted(html_dir.glob("*.html"))
    print(f"  Found {len(html_files)} local HTML files")

    for fpath in html_files:
        func_name = fpath.stem
        if func_name in ("index", "_index"):
            continue

        html = fpath.read_text(encoding="utf-8", errors="replace")
        func_info = {
            "name": func_name,
            "url": f"{BASE_URL}/{func_name}.html",
            "category": "",
            "brief": "",
        }
        chunk = parse_function_page(html, func_info)
        if chunk:
            chunks.append(chunk)

    return chunks


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scrape_all(
    local_docs: Path | None = None,
    dry_run: bool = False,
    output_dir: Path | None = None,
    max_functions: int = 0,
) -> list[ChunkV2]:
    """Scrape SideFX VEX function reference."""
    output_dir = output_dir or OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    # Use local docs if provided
    if local_docs and local_docs.is_dir():
        print(f"Parsing local HTML from: {local_docs}")
        chunks = parse_local_html(local_docs)
        print(f"  Parsed {len(chunks)} function chunks")
    else:
        # Try scraping
        print("Fetching VEX function index from SideFX...")
        try:
            functions = fetch_function_index(session)
            print(f"  Found {len(functions)} functions")
        except requests.RequestException as e:
            print(f"  [ERROR] Could not fetch index: {e}")
            print(f"  [INFO] To use local docs, save HTML files to a directory and use --local-docs")
            return []

        if dry_run:
            print(f"\n[DRY RUN] Would scrape {len(functions)} function pages")
            for f in functions[:10]:
                print(f"  {f['name']}: {f['url']}")
            if len(functions) > 10:
                print(f"  ... and {len(functions) - 10} more")
            return []

        # Scrape individual function pages
        if max_functions > 0:
            functions = functions[:max_functions]

        chunks = []
        blocked = 0

        for i, func in enumerate(functions):
            print(f"  [{i+1}/{len(functions)}] {func['name']}...", end=" ", flush=True)

            html = fetch_function_page(func["url"], session)
            if html is None:
                print("BLOCKED/FAILED")
                blocked += 1
                if blocked >= 3:
                    print("\n  [WARN] Multiple blocks detected. SideFX may be rate-limiting.")
                    print("  [INFO] Save docs locally and use --local-docs instead.")
                    break
                continue

            chunk = parse_function_page(html, func)
            if chunk:
                chunks.append(chunk)
                print(f"OK ({len(chunk.code_blocks)} code blocks)")
            else:
                print("empty")

            time.sleep(REQUEST_DELAY)

    if not chunks:
        print("No chunks produced.")
        return []

    # Write output
    outfile = output_dir / "sidefx_vex_reference.jsonl"
    with open(outfile, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")

    print(f"\nTotal: {len(chunks)} function reference chunks")
    print(f"Output: {outfile}")

    return chunks


def main():
    parser = argparse.ArgumentParser(description="Scrape SideFX VEX function reference")
    parser.add_argument("--local-docs", type=Path, help="Path to locally saved HTML files")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be scraped")
    parser.add_argument("--output", type=Path, help="Output directory")
    parser.add_argument("--max", type=int, default=0, help="Max functions to scrape (0=all)")
    args = parser.parse_args()

    scrape_all(
        local_docs=args.local_docs,
        dry_run=args.dry_run,
        output_dir=args.output,
        max_functions=args.max,
    )


if __name__ == "__main__":
    main()
