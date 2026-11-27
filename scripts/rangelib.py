"""Shared range-file loading, validation and pure scoring functions."""
from __future__ import annotations

import itertools
import json
import math
import os
import re
import subprocess
from datetime import date, datetime
from pathlib import Path

from shapely import to_wkt
from shapely.geometry import MultiPolygon, Point, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parent.parent
RANGES_DIR = ROOT / "data" / "ranges"
EARTH_R_KM = 6371.0088
REQUIRED = ("taxonKey", "author", "created")
BASES = ("expert-outline-edit", "new", "official-patch")
PERMISSIVE_LICENSES = ("CC0", "CC-BY-4.0", "OGL-Canada-2.0", "US-PD")
MAX_PARTS = 200
MAX_BBOX_RATIO = 50  # bbox area / geometry area; blocks sparse specks that inflate hits
WINDOW_BUFFER_DEG = 2.0
WKT_LIMIT = 3000  # GBIF answers 400 once the encoded GET URL passes ~3.7 KB (checked live)
IOU_DUPLICATE = 0.95
# GitHub login of the relay's App. Only PRs it opens may carry a `submitter` property.
RELAY_BOT = os.environ.get("RELAY_BOT", "rangecraft-relay[bot]")
RELAY_PROVIDERS = ("orcid", "inat", "github", "email")


class RangeError(ValueError):
    pass


def parse_created(value) -> date:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError as e:
        raise RangeError(f"created {value!r} is not an ISO date") from e


def _valid_op(p: dict) -> bool:
    """add and cut; recode keeps the area but codes it extirpated (IUCN presence 5 Extinct or 4 Possibly Extinct)."""
    return p.get("op") in ("add", "cut") or (p.get("op") == "recode" and type(p.get("presence")) is int and p["presence"] in (4, 5))


def load_range(path: Path, today: date | None = None) -> dict:
    """Parse and validate one range file. Returns {meta, geometry}. Raises RangeError."""
    today = today or date.today()
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise RangeError(f"cannot read JSON: {e}") from e
    if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection":
        raise RangeError("root must be a GeoJSON FeatureCollection")
    feats = doc.get("features")
    if not isinstance(feats, list) or not feats:
        raise RangeError("FeatureCollection has no features")
    if not all(isinstance(f, dict) for f in feats):
        raise RangeError("every feature must be an object")
    meta = dict(feats[0].get("properties") or {})
    meta.update(doc.get("properties") or {})
    missing = [k for k in REQUIRED if not meta.get(k)]
    if missing:
        raise RangeError(f"missing required properties: {', '.join(missing)}")
    if not str(meta["taxonKey"]).isdigit():
        raise RangeError(f"taxonKey {meta['taxonKey']!r} is not numeric")
    if str(meta["taxonKey"]) != Path(path).parent.name:
        raise RangeError(f"taxonKey {meta['taxonKey']} does not match directory name")
    if not isinstance(meta["author"], str) or not isinstance(meta.get("github", ""), str):
        raise RangeError("author and github must be strings")
    # created is informational only (scoring uses the git first-add date), but may not be future.
    if parse_created(meta["created"]) > today:
        raise RangeError(f"created {meta['created']} is in the future")
    orcid = meta.get("orcid")
    if orcid is not None and not valid_orcid(orcid):
        raise RangeError(f"orcid {orcid!r} is not a valid ORCID iD")
    if meta.get("submitter") is not None and not valid_submitter(meta["submitter"]):
        raise RangeError("submitter must be {provider, id, name, verified} from the relay")
    if meta.get("basis") is not None and meta["basis"] not in BASES:
        raise RangeError(f"basis must be one of {', '.join(BASES)}")
    for k in ("parent", "base", "message", "inat_login", "derived_from", "derived_from_license"):
        if meta.get(k) is not None and not isinstance(meta[k], str):
            raise RangeError(f"{k} must be a string")
    if meta.get("base") is not None and not re.fullmatch(r"[a-z0-9-]{1,80}", meta["base"]):
        raise RangeError("base must be a slug: lowercase letters, digits and hyphens, at most 80 characters")
    if meta.get("basis") == "expert-outline-edit" and not meta.get("derived_from"):
        raise RangeError("basis expert-outline-edit requires derived_from (the source outline)")
    if meta.get("basis") == "official-patch" and (
            not meta.get("base") or not all(_valid_op(f.get("properties") or {}) for f in feats)):
        raise RangeError("official-patch requires base and an op (add, cut, or recode with presence 4 or 5) on every feature")
    geoms = []
    for i, f in enumerate(feats):
        try:
            g = shape(f["geometry"])
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise RangeError(f"feature {i}: bad geometry ({e})") from e
        if g.geom_type not in ("Polygon", "MultiPolygon"):
            raise RangeError(f"feature {i}: {g.geom_type} is not Polygon/MultiPolygon")
        if not g.is_valid:
            raise RangeError(f"feature {i}: invalid geometry (self-intersection or bad ring)")
        geoms.append(g)
    geom = unary_union(geoms)
    check_geometry(geom, sparse=meta.get("basis") == "official-patch")
    return {"meta": meta, "geometry": geom}


def check_geometry(geom: BaseGeometry, sparse: bool = False) -> None:
    """Raise RangeError for out-of-range coords, too many parts, or a gamed bbox (not checked when sparse: official-patch shapes)."""
    if geom.is_empty or geom.area <= 0:
        raise RangeError("geometry is empty or has zero area")
    x0, y0, x1, y1 = geom.bounds
    if x0 < -180 or x1 > 180 or y0 < -90 or y1 > 90:
        raise RangeError("coordinates outside lon [-180,180] / lat [-90,90]")
    n = len(geom.geoms) if hasattr(geom, "geoms") else 1
    if n > MAX_PARTS:
        raise RangeError(f"{n} parts exceeds the limit of {MAX_PARTS}")
    ratio = (x1 - x0) * (y1 - y0) / geom.area
    if not sparse and ratio > MAX_BBOX_RATIO:
        raise RangeError(f"bbox is {ratio:.0f}x the geometry area (limit {MAX_BBOX_RATIO}x)")


def valid_orcid(value) -> bool:
    """ORCID iD format plus ISO 7064 mod 11-2 check character."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", value):
        return False
    digits = value.replace("-", "")
    total = 0
    for c in digits[:15]:
        total = (total + int(c)) * 2
    check = (12 - total % 11) % 11
    return digits[15] == ("X" if check == 10 else str(check))


def valid_submitter(sub) -> bool:
    """Shape of the relay's submitter property. Email identities can never be verified. github and
    inat ids are the permanent numeric ids (logins are renameable), `login` is display/lookup only."""
    return (isinstance(sub, dict) and sub.get("provider") in RELAY_PROVIDERS
            and all(isinstance(sub.get(k), str) and sub[k] for k in ("id", "name"))
            and isinstance(sub.get("verified"), bool)
            and isinstance(sub.get("login", ""), str)
            and not (sub["provider"] == "email" and sub["verified"])
            and not (sub["provider"] in ("github", "inat") and not sub["id"].isdigit()))


def file_submitter(path: Path) -> dict | None:
    """Valid collection-level `submitter` of a range file, else None."""
    try:
        sub = (json.loads(Path(path).read_text()).get("properties") or {}).get("submitter")
    except (OSError, ValueError, AttributeError):
        return None
    return sub if valid_submitter(sub) else None


def is_relay_email(email: str) -> bool:
    """True for the noreply address GitHub gives the relay App.

    GitHub sets it as the author of a squash merge of a PR the App opened, so with squash-only
    merges it is not contributor-chosen. It is not unforgeable by someone who can push to the
    default branch directly, and a PR whose author merely uses the bot's name or a look-alike email
    never matches. validate.py separately limits `submitter` to PRs from this repo's own branches.
    """
    return re.fullmatch(rf"(?:\d+\+)?{re.escape(RELAY_BOT)}@users\.noreply\.github\.com",
                        email.strip(), re.IGNORECASE) is not None


def exportable(meta: dict) -> bool:
    """Geometry may go in the CC BY export unless derived from an outline we cannot relicense."""
    if meta.get("basis") != "expert-outline-edit":
        return True
    return meta.get("derived_from_license") in PERMISSIVE_LICENSES


NOREPLY = re.compile(r"^(?:\d+\+)?([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)@users\.noreply\.github\.com$",
                     re.IGNORECASE)


PUBLISHERS = frozenset({"USGS GAP", "Fisheries and Oceans Canada", "NatureServe Canada"})


def imported_source(meta: dict) -> str | None:
    """Publisher credited for a range imported verbatim from a published source, instead of the committer.

    Only allowlisted publishers, and only files with no github owner: validate.py lets just maintainers
    add those, so a contributor cannot claim a publisher's slot by writing its name."""
    a = meta.get("author")
    ok = a in PUBLISHERS and meta.get("derived_from") == a and meta.get("basis") == "new" and not meta.get("github")
    return a if ok else None


def identity_from_commit(email: str, name: str) -> tuple[str, str]:
    """(credit key, identity). GitHub noreply email -> (login, "github"); else (name, "unverified").

    GitHub sets the noreply author email on squash merges from the PR author, so the login is
    not contributor-chosen. A free-form name or email is spoofable and stays "unverified".
    """
    m = NOREPLY.match(email.strip())
    return (m.group(1), "github") if m else ("unverified:" + (name.strip() or "unknown"), "unverified")


def git_origin(path: Path) -> tuple[str, str, str]:
    """(credit key, identity, ISO date) from the commit that first added the file.

    Uses first-parent history, so with squash merges this is the merge commit: its date is set by
    GitHub, not the contributor. Prospective cutoff and credit owner never come from file
    properties. Uncommitted files: local git user, "unverified", today. A file first added by the
    relay bot is credited to its `submitter` as "<provider>:<id>" (identity = provider if verified).
    """
    cwd = Path(path).parent
    out = subprocess.run(
        ["git", "log", "--first-parent", "--diff-filter=A", "--follow",
         "--format=%ae%x09%an%x09%cs", "--", str(path)],
        cwd=cwd, capture_output=True, text=True, check=False).stdout.strip()
    if out:
        email, name, day = out.splitlines()[-1].split("\t")
        sub = file_submitter(path) if is_relay_email(email) else None
        if sub:  # relay PR: credit the person the relay signed in, not the bot
            return (f"{sub['provider']}:{sub['id']}", sub["provider"] if sub["verified"] else "unverified", day)
        return (*identity_from_commit(email, name), day)
    who = subprocess.run(["git", "config", "user.name"], cwd=cwd, capture_output=True,
                         text=True, check=False).stdout.strip()
    return who or "unknown", "unverified", date.today().isoformat()


def taxon_windows(items: list[dict], evidence: set[str]) -> dict:
    """Null window per taxon from counted ranges only (superseded and zero-evidence excluded).

    items: dicts with id, taxonKey, geometry. evidence: ids to include.
    """
    by_taxon: dict = {}
    for it in items:
        if it["id"] in evidence:
            by_taxon.setdefault(it["taxonKey"], []).append(it["geometry"])
    return {k: window_for_taxon(v) for k, v in by_taxon.items()}


def iou(a: BaseGeometry, b: BaseGeometry) -> float:
    u = a.union(b).area
    return a.intersection(b).area / u if u else 0.0


def superseded(items: list[dict]) -> set[str]:
    """Ids not counted: same taxon and git author, IoU > 0.95 with a later range.

    items: dicts with id, taxonKey, git_author, edit_date, geometry.
    """
    out = set()
    for a in items:
        for b in items:
            if (a is not b and a["taxonKey"] == b["taxonKey"]
                    and a["git_author"] == b["git_author"]
                    and (b["edit_date"], b["id"]) > (a["edit_date"], a["id"])
                    and iou(a["geometry"], b["geometry"]) > IOU_DUPLICATE):
                out.add(a["id"])
    return out


def window_for_taxon(geoms, buf: float = WINDOW_BUFFER_DEG):
    """Null window for a taxon: bbox of the union of ALL its ranges, buffered, clamped.

    Independent of any single submission, so a submitter cannot shrink it to flatter a range.
    """
    x0, y0, x1, y1 = unary_union(list(geoms)).bounds
    return box(max(-180, x0 - buf), max(-90, y0 - buf), min(180, x1 + buf), min(90, y1 + buf))


def wkt_limited(geom: BaseGeometry, limit: int = WKT_LIMIT):
    """CCW WKT shorter than `limit` chars. Returns (wkt, tolerance_deg, simplified_geom).

    From tolerance 0.128 deg on, the smallest parts are dropped too (halving per step), because
    a many-island range cannot otherwise fit a GBIF GET URL. Scoring then undercounts, never over.
    """
    tol = 0.0
    for i in range(30):
        g = geom if tol == 0 else geom.simplify(tol, preserve_topology=True)
        parts = sorted(g.geoms if hasattr(g, "geoms") else [g], key=lambda p: -p.area)
        if i > 7:
            parts = parts[:max(1, len(parts) >> (i - 7))]
        parts = [orient(p, 1.0) for p in parts]
        g = parts[0] if len(parts) == 1 else MultiPolygon(parts)
        wkt = to_wkt(g, rounding_precision=4, trim=True)
        if len(wkt) < limit and g.is_valid and not g.is_empty:
            return wkt, tol, g
        tol = tol * 2 if tol else 0.001
    raise RangeError(f"cannot simplify geometry below {limit} WKT chars")


def _ring_area(coords) -> float:
    # Chamberlain-Duquette spherical polygon area, km^2 (signed).
    s = 0.0
    for (x1, y1), (x2, y2) in itertools.pairwise(coords):
        s += math.radians(x2 - x1) * (2 + math.sin(math.radians(y1)) + math.sin(math.radians(y2)))
    return s * EARTH_R_KM**2 / 2


def area_km2(geom: BaseGeometry) -> float:
    """Approximate geodesic area (spherical, not ellipsoidal; ~<1% error). Empty -> 0."""
    if geom.is_empty:
        return 0.0
    if geom.geom_type == "Polygon":
        a = abs(_ring_area(list(geom.exterior.coords)))
        return a - sum(abs(_ring_area(list(r.coords))) for r in geom.interiors)
    if hasattr(geom, "geoms"):
        return sum(area_km2(g) for g in geom.geoms)
    return 0.0


def count_inside(points, geom: BaseGeometry) -> int:
    """points: iterable of (lon, lat)."""
    if geom.is_empty:
        return 0
    p = prep(geom)
    return sum(1 for x, y in points if p.contains(Point(x, y)))


def credit(inside_post: int, window_post: int, area_ratio: float) -> float:
    """Prospective hits beyond random placement; negative when edits are contradicted.

    credit = inside_post - window_post * area_ratio, area_ratio = range area / window area.
    """
    return inside_post - window_post * min(1.0, max(0.0, area_ratio))


def skill(inside_post: int, window_post: int, area_ratio: float) -> float | None:
    """(inside_post/window_post - r) / (1 - r): 0 = random placement, 1 = perfect.

    None when there are no prospective records or the range fills the window (r >= 1).
    """
    if window_post <= 0 or area_ratio >= 1:
        return None
    return (inside_post / window_post - area_ratio) / (1 - area_ratio)
