"""Write a pull-request review comment for changed range files: what each one adds and removes.

Each proposed range is compared with the range it edits (`parent`, a patch's `base`, or the
`derived_from` publisher), else the closest existing range of the taxon. The comment holds an
area table and a `geojson` block GitHub renders as a map: added green, removed red, kept grey.

Reads data only, never runs PR code: `review.py <base-ref> <head-ref> [out.md]`.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from score import gbif_count
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from rangelib import IOU_DUPLICATE, ROOT, RangeError, area_km2, iou, load_range, shape, wkt_limited

SITE = "https://pollocklab.github.io/rangecraft/"
MARKER = "<!-- rangecraft-review -->"
MAX_GEOJSON = 50_000  # GitHub comments cap at 65,536 characters
MAX_BODY = 60_000  # margin for GitHub counting UTF-16 units, where emoji take two
STYLE = {
    "added": {"fill": "#2e9e44", "stroke": "#1f6e2f", "fill-opacity": 0.55},
    "removed": {"fill": "#d1342f", "stroke": "#8f1d1a", "fill-opacity": 0.55},
    "kept": {"fill": "#8a8a8a", "stroke": "#8a8a8a", "fill-opacity": 0, "stroke-width": 1},
}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def changed_ranges(base: str, head: str) -> list[str]:
    out = git("diff", "--name-only", "--diff-filter=AM", f"{base}...{head}", "--", "data/ranges")
    return [p for p in out.split() if p.endswith(".geojson") and p.count("/") == 3]


def load_at(ref: str, path: str) -> dict:
    """load_range on a file at a git ref, kept in its taxon directory (load_range checks the name)."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / Path(path).parent.name / Path(path).name
        f.parent.mkdir()
        f.write_text(git("show", f"{ref}:{path}"))
        return load_range(f)


def effective(rng: dict, base_geom: BaseGeometry | None) -> BaseGeometry:
    """A patch applied to its base (add, then cut); any other range as is."""
    if rng["meta"].get("basis") != "official-patch" or base_geom is None:
        return rng["geometry"]
    feats = rng["features"]
    ops = {
        op: unary_union([shape(f["geometry"]) for f in feats if f["properties"].get("op") == op])
        for op in ("add", "cut")
    }
    return base_geom.union(ops["add"]).difference(ops["cut"])


def reference(path: str, rng: dict, base: str) -> tuple[str, dict, str] | None:
    """(repo path, range, why) of the range this one edits, read at the base ref."""
    taxon_dir = Path(path).parent
    existing = [
        p
        for p in git("ls-tree", "--name-only", base, f"{taxon_dir}/").split()
        if p.endswith(".geojson") and p != path
    ]
    meta = rng["meta"]
    named = []
    if meta.get("parent"):
        p = (
            meta["parent"]
            if meta["parent"].startswith("data/")
            else f"data/ranges/{meta['parent']}"
        )
        named.append((p, "edits"))
    if meta.get("base"):
        named.append((f"{taxon_dir}/{meta['base']}.geojson", "patches"))
    for p, why in named:
        if p in existing:
            return p, load_at(base, p), why
    loaded = []
    for p in existing:
        try:
            loaded.append((p, load_at(base, p)))
        except RangeError:
            continue
    src = meta.get("derived_from")
    for p, r in loaded:
        if src and r["meta"].get("author") == src:
            return p, r, f"derived from {src}"
    if not loaded:
        return None
    p, r = max(loaded, key=lambda pr: iou(pr[1]["geometry"], rng["geometry"]))
    return p, r, "closest existing range"


def diff_map(new: BaseGeometry, old: BaseGeometry) -> tuple[str, int]:
    """GeoJSON of added, removed and kept areas that fits a comment, and how many parts were left out.

    Parts go in largest first, simplified to ~1 km, until the budget is spent: an edit's slivers
    along an unchanged border are the first to go."""
    layers = {"added": new.difference(old), "removed": old.difference(new), "kept": new.intersection(old)}
    parts = sorted(((g.area, k, g) for k, lay in layers.items() for g in getattr(lay, "geoms", [lay])
                    if not g.is_empty), key=lambda t: -t[0])
    feats, size, left = [], 0, 0
    for _, k, g in parts:
        f = {"type": "Feature", "properties": {"change": k, **STYLE[k]},
             "geometry": json.loads(json.dumps(mapping(g.simplify(0.01)), default=float),
                                    parse_float=lambda v: round(float(v), 4))}
        n = len(json.dumps(f, separators=(",", ":")))
        if size + n > MAX_GEOJSON:
            left += 1
            continue
        feats.append(f)
        size += n
    # Slivers vanish at the zoom GitHub opens on, so the biggest changes also get a pin.
    pins = [{"type": "Feature", "properties": {"change": k, "marker-color": STYLE[k]["fill"], "marker-size": "small"},
             "geometry": {"type": "Point", "coordinates": [round(c.x, 4), round(c.y, 4)]}}
            for k in ("added", "removed")
            for _, _, g in [t for t in parts if t[1] == k and area_km2(t[2]) >= 1][:5]
            for c in [g.representative_point()]]
    return json.dumps({"type": "FeatureCollection", "features": feats + pins}, separators=(",", ":")), left


def map_block(new: BaseGeometry, old: BaseGeometry) -> list[str]:
    text, left = diff_map(new, old)
    note = [f"{left} smaller pieces are not drawn; the table counts them."] if left else []
    return ["", "```geojson", text, "```", *note]


def preview_url(path: str, head: str, vs: str | None = None) -> str:
    """Site URL drawing this file at the PR's commit, as a revision of range `vs` (green added, red removed).

    Empty unless every part matches what the site accepts, so a crafted file name cannot break out."""
    sha, taxon, name = git("rev-parse", head).strip(), Path(path).parent.name, Path(path).name
    if not (re.fullmatch(r"[0-9a-f]{40}", sha) and taxon.isdigit() and re.fullmatch(r"[\w.-]+\.geojson", name)):
        return ""
    url = f"{SITE}#taxon={taxon}&official=1&preview={sha}/{taxon}/{name}"
    return url + f"&vs={vs}" if vs and re.fullmatch(r"\d+/\w[\w.-]*", vs) else url


def largest_changes(new: BaseGeometry, old: BaseGeometry, url: str, n: int = 3) -> list[str]:
    """Bullets for the n biggest added and removed pieces, each linking to the site zoomed on it."""
    out = []
    for icon, verb, g in (("🟩", "added", new.difference(old)), ("🟥", "removed", old.difference(new))):
        parts = sorted(getattr(g, "geoms", [g]), key=lambda p: -area_km2(p))[:n]
        for p in parts:
            km2 = area_km2(p)
            if km2 < 1:
                continue
            c = p.representative_point()
            span = max(p.bounds[2] - p.bounds[0], p.bounds[3] - p.bounds[1], 0.05)
            zoom = max(3, min(11, round(9 - math.log2(span))))
            where = f"{abs(c.y):.2f}°{'N' if c.y >= 0 else 'S'}, {abs(c.x):.2f}°{'E' if c.x >= 0 else 'W'}"
            label = f"{icon} {fmt(km2)} km² {verb} near {where}"
            out.append(f"- [{label}]({url}&at={zoom}/{c.y:.4f}/{c.x:.4f})" if url else f"- {label}")
    return ["", "**Largest changes** (click to zoom in on the map):", "", *out] if out else []


def records(taxon_key, geom: BaseGeometry) -> str:
    """GBIF occurrence count inside geom, or a dash when GBIF is unreachable."""
    if geom.is_empty or area_km2(geom) < 1:
        return "0"
    try:
        return f"{gbif_count(taxon_key, wkt_limited(geom)[0]):,}"
    except (requests.RequestException, RangeError, KeyError, ValueError):
        return "–"


def plain(value, limit: int = 200) -> str:
    """PR-supplied text as one line with markdown, HTML and mention characters escaped."""
    text = " ".join(str(value or "").split())[:limit]
    return re.sub(r"([\\`*_{}\[\]()<>#+!|~@:-])", r"\\\1", text)


def fmt(km2: float) -> str:
    return f"{km2:,.0f}"


def section(path: str, head: str, base: str) -> str:
    name = path.removeprefix("data/ranges/")
    try:
        doc = json.loads(git("show", f"{head}:{path}"))
        rng = load_at(head, path)
    except RangeError as e:
        return f"### `{name}`\n\nNot reviewable: {e}\n"
    rng["features"] = doc["features"]
    meta = rng["meta"]
    who = plain(meta.get("author"))  # submitter is not shown: validate.py, not this comment, vouches for it
    lines = [
        f"### {plain(meta.get('scientificName') or meta['taxonKey'])} by {who}",
        "",
        f"`{name}` · basis `{meta.get('basis') or 'unset'}`",
    ]
    if meta.get("message"):
        lines += ["", f"> {plain(meta['message'])}"]
    if meta.get("sources"):
        lines += ["", f"**Sources:** {plain(meta['sources'], 600)}"]
    ref = reference(path, rng, base)
    vs = ref and ref[0].removeprefix("data/ranges/").removesuffix(".geojson")
    url = preview_url(path, head, vs)
    if url:
        lines[2] += f" · [**Open on RangeCraft: changes vs the reference, with GBIF records**]({url})"
    if ref is None:
        new = rng["geometry"]
        lines += [
            "",
            f"First range for this species: {fmt(area_km2(new))} km².",
            *map_block(new, new.difference(new)),
        ]
        return "\n".join(lines) + "\n"
    ref_path, ref_rng, why = ref
    old = ref_rng["geometry"]
    new = effective(rng, old)
    a_old, a_new = area_km2(old), area_km2(new)
    added, removed = area_km2(new.difference(old)), area_km2(old.difference(new))
    score = iou(new, old)
    key = meta["taxonKey"]
    pct = (lambda v: f"{100 * v / a_old:+.1f}%") if a_old else (lambda v: "")
    lines += [
        "",
        f"Compared with **{plain(ref_rng['meta'].get('author'))}** (`{ref_path.removeprefix('data/ranges/')}`, {why}).",
        "",
        "| | km² | vs reference | GBIF records |",
        "|---|---:|---:|---:|",
        f"| Reference | {fmt(a_old)} | | |",
        f"| Proposed | {fmt(a_new)} | {pct(a_new - a_old)} | |",
        f"| 🟩 Added | {fmt(added)} | {pct(added)} | {records(key, new.difference(old))} |",
        f"| 🟥 Removed | {fmt(removed)} | {pct(-removed)} | {records(key, old.difference(new))} |",
        f"| Overlap (IoU) | {score:.2f} | | |",
        "",
        ("Records in the added area support the change; records in the removed area argue against it. "
        "Counts are all-time GBIF records with coordinates and no geospatial issue, over an outline "
        "simplified to fit the GBIF API, so small slivers may be missed."),
    ]
    if score >= IOU_DUPLICATE:
        lines += [
            "",
            f"⚠️ Near-duplicate of the reference (IoU ≥ {IOU_DUPLICATE}): little would change.",
        ]
    lines += largest_changes(new, old, url)
    lines += map_block(new, old)
    return "\n".join(lines) + "\n"


def join_capped(head: str, sections: list[str]) -> str:
    """head and sections joined; whole sections are dropped from the end to stay under MAX_BODY."""
    body, kept = head, 0
    for sec in sections:
        if len(body) + 2 + len(sec) > MAX_BODY - 200:  # room for the note
            body += f"\n\n_Truncated to fit GitHub's comment size limit: {len(sections) - kept} of {len(sections)} files are not shown._"
            break
        body, kept = f"{body}\n\n{sec}", kept + 1
    return body


def main(argv: list[str]) -> int:
    base, head = argv[1], argv[2]
    paths = changed_ranges(base, head)
    if not paths:
        return 0
    body = join_capped(
        MARKER + "\n## Range review\n\nGreen is added, red removed, grey kept. "
        "Drag and zoom the maps; areas are geodesic.",
        [section(p, head, base) for p in paths],
    )
    if len(argv) > 3:
        Path(argv[3]).write_text(body)
    else:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
