# Source Registry

All sources are registered in `config/sources.yaml` with metadata used for authority weighting, difficulty calibration, and corpus statistics.

## Active Sources

### joy-of-vex-youtube

| Field | Value |
|-------|-------|
| **Name** | Joy of VEX (YouTube - Peter Arcara) |
| **Type** | YouTube transcript + OCR code extraction |
| **URL** | [Playlist](https://youtube.com/playlist?list=PLTXmnikJEYnBtSfn4LwKx5vpopwrInp18) |
| **Authority** | 0.9 (Matt Estela content, recorded by SideFX staff) |
| **Difficulty Range** | beginner - intermediate |
| **VEX Contexts** | sop |
| **Houdini Era** | 18.0-19.5 |
| **Status** | active |
| **Chunks** | 1,633 |

**Notes**: Imported via `scripts/import_joy_of_vex.py` from the vex_rag_pipeline. Over-granular segmentation from timestamp-based chunking produces many near-duplicate chunks. Functions field is always empty in current data.

## Planned Sources

### cgwiki-vex

| Field | Value |
|-------|-------|
| **Name** | tokeru.com cgwiki - VEX pages |
| **Type** | Web scrape |
| **URL** | [tokeru.com/cgwiki](https://tokeru.com/cgwiki/) |
| **Authority** | 0.95 (Matt Estela, primary author) |
| **Difficulty Range** | beginner - advanced |
| **VEX Contexts** | sop, dop, cop |
| **Status** | planned |

**Notes**: The original text versions of Joy of VEX (JoyOfVex01-20) plus the HoudiniVex tips page. Higher quality than YouTube transcript extraction. This is the highest-priority new source.

### sidefx-vex-reference

| Field | Value |
|-------|-------|
| **Name** | SideFX VEX Function Reference |
| **Type** | Web scrape |
| **URL** | [VEX Functions](https://www.sidefx.com/docs/houdini/vex/functions/) |
| **Authority** | 1.0 (Official documentation) |
| **Difficulty Range** | reference |
| **VEX Contexts** | all |
| **Status** | planned |

**Notes**: One chunk per function. Essential for the "what does X do?" query pattern. May need local HTML fallback if scraping is blocked.

### kiryha-vex-artists

| Field | Value |
|-------|-------|
| **Name** | VEX for Artists (kiryha) |
| **Type** | GitHub wiki |
| **URL** | [kiryha/Houdini wiki](https://github.com/kiryha/Houdini/wiki/vex-for-artists) |
| **Authority** | 0.7 |
| **Difficulty Range** | beginner - intermediate |
| **VEX Contexts** | sop |
| **Status** | planned |

### vex-pattern-library

| Field | Value |
|-------|-------|
| **Name** | Production VEX Pattern Library |
| **Type** | Local skill files |
| **Path** | `~/.claude/skills/vex-pattern-library/` |
| **Authority** | 0.85 (Production-tested, self-authored) |
| **Difficulty Range** | intermediate - expert |
| **VEX Contexts** | sop, dop |
| **Status** | planned |

## Authority Scale

| Score | Meaning | Example |
|-------|---------|---------|
| 1.0 | Official documentation | SideFX docs |
| 0.9-0.95 | Recognized expert, primary source | Matt Estela (cgwiki, Joy of VEX) |
| 0.8-0.89 | Production-tested, vetted | Internal pattern libraries |
| 0.7-0.79 | Community resource, well-regarded | kiryha wiki, Entagma |
| 0.5-0.69 | Forum post, unvetted | odforce.net, SideFX forums |
| < 0.5 | Unverified, user-submitted | Raw submissions |
