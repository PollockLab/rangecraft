"""Import real range polygons into data/ranges/<GBIF taxonKey>/<source>.geojson.

Only sources whose licence allows CC BY 4.0 republishing are imported as geometry (see
docs/SOURCES.md): USGS GAP (US public domain), DFO aquatic species at risk (OGL-Canada) and
NatureServe Canada EBAR (CC BY 4.0).
iNaturalist taxon ranges are often IUCN-derived and their licence is not exposed by the API, so
they are only linked, in data/references.json. Existing source files are kept unless --force.
--offline does nothing (CI fallback).
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import zipfile

import numpy as np
import requests
import shapefile
import shapely
from pyproj import CRS, Transformer
from shapely import set_precision
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union

from inat_experts import taxon_id
from rangelib import RANGES_DIR, ROOT, RangeError, load_range

GBIF_MATCH = "https://api.gbif.org/v1/species/match"
SB = "https://www.sciencebase.gov/catalog/"
DFO = ("https://egisp.dfo-mpo.gc.ca/arcgis/rest/services/open_data_donnees_ouvertes/"
       "dfo_sara_distribution/MapServer/0/query")
EBAR = ("https://gis.natureserve.ca/arcgis/rest/services/EBAR-KBA/EBARPublic/FeatureServer/1/query")
CKAN = "https://open.canada.ca/data/api/action/package_show?id=e0fabad5-9379-4077-87b9-5705f28c490b"
REFERENCES = ROOT / "data" / "references.json"
MAX_BYTES = 450_000
MAX_PARTS = 150  # rangelib allows 200
# Canadian vertebrates from openbiodiversity.ca public/sdm-species.json.
GAP_TAXA = [
    "Lithobates sylvaticus", "Ambystoma maculatum", "Anaxyrus americanus", "Pseudacris crucifer",
    "Chelydra serpentina", "Thamnophis sirtalis", "Castor canadensis", "Lynx canadensis",
    "Ursus americanus", "Martes pennanti", "Haliaeetus leucocephalus", "Strix varia",
    "Cyanocitta cristata",
]
DFO_TAXA = ["Enhydra lutris", "Eumetopias jubatus", "Dermochelys coriacea"]
# Exact binomials with an EBAR range map. Never match by synonym or genus + species of a longer name.
EBAR_TAXA = ["Chelydra serpentina"]
# coverage_area -> note. src/rangecheck.js COVERAGE holds the matching areas.
COVERAGE = {
    "conterminous-us": "Conterminous US only. Absence north of the border means not mapped, not absent.",
    "canada": "Canada only. Absence outside Canada means not mapped, not absent.",
}
UA = {"User-Agent": "RangeCraft (github.com/PollockLab/rangecraft)"}


def get(url, **kw):
    time.sleep(0.5)
    # ScienceBase answers a custom User-Agent with a Cloudflare challenge (403), the requests default gets JSON.
    r = requests.get(url, headers=None if url.startswith(SB) else UA, timeout=120, **kw)
    r.raise_for_status()
    return r


def gbif_key(name: str) -> int:
    d = get(GBIF_MATCH, params={"name": name, "kingdom": "Animalia"}).json()
    if d.get("matchType") != "EXACT" or d.get("rank") != "SPECIES":
        raise RangeError(f"GBIF has no exact species match for {name}: {d.get('matchType')}")
    return int(d["usageKey"])


def pick_gap_item(items: list[dict], name: str) -> dict | None:
    """The species-level CONUS range item, e.g. 'Wood Frog (Lithobates sylvaticus) aWOFRx_CONUS_2001v1 Range Map'."""
    pat = re.compile(rf"\({re.escape(name)}\) \w+_CONUS_2001v1 Range Map$")
    return next((i for i in items if pat.search(i.get("title", ""))), None)


def publication_date(item: dict) -> str:
    for d in item.get("dates", []):
        if d.get("type") == "Publication":
            return d["dateString"]
    raise RangeError(f"no publication date on ScienceBase item {item.get('id')}")


def simplify_parts(geom, tol: float, min_area: float):
    """Drop specks, fill holes, simplify, keep the largest MAX_PARTS parts. Units of the CRS.

    Holes are filled: simplification never removes them, and they stop a range fitting the
    GBIF query URL when scored.
    """
    parts = [Polygon(p.exterior)
             for p in (geom.geoms if hasattr(geom, "geoms") else [geom]) if p.area >= min_area]
    parts = sorted(parts, key=lambda p: -p.area)[:MAX_PARTS]
    g = unary_union(parts).simplify(tol, preserve_topology=True)
    return g if g.is_valid else g.buffer(0)


def read_gap_zip(blob: bytes):
    """Union of every polygon in the zip's shapefile, plus the .prj WKT."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    base = next(n[:-4] for n in z.namelist() if n.endswith(".shp"))
    rd = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), dbf=io.BytesIO(z.read(base + ".dbf")),
                          shx=io.BytesIO(z.read(base + ".shx")))
    geom = unary_union([shape(s.__geo_interface__).buffer(0) for s in rd.shapes()])
    return geom, z.read(base + ".prj").decode()


def fit(geom, tol: float, min_area: float, to4326=lambda g: g) -> dict:
    """GeoJSON MultiPolygon, simplified (doubling tol) until the file is under MAX_BYTES."""
    for _ in range(8):
        g = set_precision(to4326(simplify_parts(geom, tol, min_area)), 1e-3)
        if g.geom_type == "Polygon":
            g = MultiPolygon([g])
        out = mapping(g)
        if len(json.dumps(out)) < MAX_BYTES:
            return out
        tol *= 2
    raise RangeError("cannot simplify below size limit")


def doc(name, key, geometry, author, created, sources, lic, coverage=None):
    return {"type": "FeatureCollection",
            "properties": {"taxonKey": key, "scientificName": name, "author": author, "created": created, "basis": "new",
                           "derived_from": author, "derived_from_license": lic, "sources": sources,
                           **({"coverage": COVERAGE[coverage], "coverage_area": coverage} if coverage else {})},
            "features": [{"type": "Feature", "properties": {}, "geometry": geometry}]}


def gap(name: str, _meta=None):
    q = get(SB + "items", params={"q": name, "format": "json", "max": 10, "fields": "title,id"}).json()
    hit = pick_gap_item(q["items"], name)
    if not hit:
        raise RangeError(f"no GAP range item for {name}")
    item = get(SB + f"item/{hit['id']}", params={"format": "json"}).json()
    zurl = next(f["url"] for f in item["files"] if f["name"].endswith(".zip"))
    geom, prj = read_gap_zip(get(zurl).content)
    t = Transformer.from_crs(CRS.from_wkt(prj), 4326, always_xy=True)
    geometry = fit(geom, 3000, 20e6, lambda g: shapely.transform(g, lambda c: np.column_stack(t.transform(c[:, 0], c[:, 1]))))
    src = (f"{item['citation']} Item: {SB}item/{item['id']}. Range of the conterminous United States "
           "only (GAP does not map Canada or Alaska). Simplified (about 3 km) from HUC12 units, holes filled.")
    return geometry, publication_date(item), src, "USGS GAP", "US-PD", "usgs-gap", "conterminous-us"


def arcgis_features(url: str, where: str, fields: str, oid: str) -> list[dict]:
    """All GeoJSON features matching where, in EPSG:4326, paged past the server's record limit."""
    out = []
    while True:
        page = get(url, params={"where": where, "outFields": fields, "outSR": 4326,
                                "geometryPrecision": 4, "f": "geojson", "resultOffset": len(out),
                                "resultRecordCount": 1000, "orderByFields": oid}).json().get("features", [])
        out += page
        if len(page) < 1000:
            return out


def dfo(name: str, meta: dict):
    feats = arcgis_features(DFO, f"SCIENTIFIC='{name}'", "SCIENTIFIC", "OBJECTID")
    if not feats:
        raise RangeError(f"DFO returned no features for {name}")
    geom = unary_union([shape(f["geometry"]).buffer(0) for f in feats])
    geometry = fit(geom, 0.02, 1e-4)
    src = (f"Fisheries and Oceans Canada, Species at Risk Distribution (Range), {meta['url']}. "
           "Contains information licensed under the Open Government Licence - Canada. "
           "Union of all SARA populations. Simplified, holes filled.")
    return geometry, meta["date"], src, "Fisheries and Oceans Canada", "OGL-Canada-2.0", "dfo-sara", None


def pick_ebar_map(feats: list[dict]) -> list[dict]:
    """Ecoshapes of one range map: global scope before national, then the newest rangedate."""
    best = max(feats, key=lambda f: (f["properties"]["rangemapscope"] == "G", f["properties"]["rangedate"] or 0))
    return [f for f in feats if f["properties"]["rangemapid"] == best["properties"]["rangemapid"]]


def ebar(name: str, _meta=None):
    feats = arcgis_features(EBAR, f"national_scientific_name='{name}'",
                            "rangemapid,presence,rangemapscope,rangedate,rangestage,natureserveexplorerurl", "row_number")
    if not feats:
        raise RangeError(f"EBAR has no range map for {name}")
    feats = pick_ebar_map(feats)
    p = feats[0]["properties"]
    # Present (P) and expected (X) ecoshapes are the range. Historical (H) ones and blank ones (removed in expert review) are not.
    shapes = [shape(f["geometry"]).buffer(0) for f in feats if f["properties"]["presence"] in ("P", "X")]
    if not shapes:
        raise RangeError(f"EBAR range map {p['rangemapid']} for {name} has no present or expected ecoshape")
    geometry = fit(unary_union(shapes), 0.02, 1e-4)
    created = time.strftime("%Y-%m-%d", time.gmtime(p["rangedate"] / 1000))
    national = p["rangemapscope"] != "G"
    src = (f"NatureServe Canada, Ecosystem-based Automated Range (EBAR), range map {p['rangemapid']}, "
           f"{p['rangestage']}, {p['natureserveexplorerurl'] or EBAR[:-8]}. EBAR © NatureServe Canada 2026, "
           f"CC BY 4.0. {'Range within Canada only. ' if national else ''}Union of the ecoshapes coded "
           "present or expected, historical and removed ones left out. Simplified, holes filled.")
    return geometry, created, src, "NatureServe Canada", "CC-BY-4.0", "natureserve-ebar", "canada" if national else None


def dfo_meta() -> dict:
    p = get(CKAN).json()["result"]
    return {"date": p["date_published"][:10], "url": "https://open.canada.ca/data/en/dataset/" + p["id"]}


def inat_reference(key: int, name: str) -> dict | None:
    """Link to the iNaturalist range if one exists. No geometry: licence unknown, often IUCN-derived."""
    tid = taxon_id(name)
    if not tid:
        return None
    url = f"https://www.inaturalist.org/taxa/{tid}/range.kml"
    try:
        ok = requests.get(url, headers=UA, timeout=60).status_code == 200
    except requests.RequestException:
        ok = False
    return ({"taxonKey": key, "source": "iNaturalist taxon range", "url": url,
             "license": "unspecified; uploaded per taxon, many ranges derive from IUCN",
             "note": "Link only. Geometry is not redistributed."} if ok else None)


def write_references(refs: list[dict]) -> None:
    old = json.loads(REFERENCES.read_text()) if REFERENCES.exists() else []
    merged = {(r["taxonKey"], r["source"]): r for r in old + refs}
    REFERENCES.write_text(json.dumps(sorted(merged.values(), key=lambda r: (r["taxonKey"], r["source"])),
                                     indent=2) + "\n")


def import_one(name: str, fn, slug: str, meta: dict, force: bool) -> int:
    key = gbif_key(name)
    path = RANGES_DIR / str(key) / f"{slug}.geojson"
    if force or not path.exists():
        geometry, created, src, author, lic, _, coverage = fn(name, meta)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc(name, key, geometry, author, created, src, lic, coverage)) + "\n")
        try:
            load_range(path)  # same checks as CI
        except RangeError:
            path.unlink()
            raise
        print(f"{name}: {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")
    return key


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="no-op")
    ap.add_argument("--force", action="store_true", help="re-import existing source files")
    args = ap.parse_args()
    if args.offline:
        return 0
    meta, refs, fails = dfo_meta(), [], 0
    for name, fn, slug in ([(n, gap, "usgs-gap") for n in GAP_TAXA] + [(n, dfo, "dfo-sara") for n in DFO_TAXA]
                           + [(n, ebar, "natureserve-ebar") for n in EBAR_TAXA]):
        try:
            key = import_one(name, fn, slug, meta, args.force)
            ref = inat_reference(key, name)
            refs += [ref] if ref else []
        except (RangeError, requests.RequestException, StopIteration, OSError) as e:
            print(f"ERROR {name}: {e}", file=sys.stderr)
            fails += 1
    write_references(refs)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
