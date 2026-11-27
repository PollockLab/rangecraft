import json
import re

import review
from shapely.geometry import box, shape


def test_diff_map_layers_and_budget(monkeypatch):
    new, old = box(0, 0, 2, 1), box(1, 0, 3, 1)
    text, left = review.diff_map(new, old)
    feats = json.loads(text)["features"]
    got = {f["properties"]["change"]: shape(f["geometry"]) for f in feats if f["geometry"]["type"] != "Point"}
    pins = {f["properties"]["change"]: shape(f["geometry"]) for f in feats if f["geometry"]["type"] == "Point"}
    assert pins.keys() == {"added", "removed"} and pins["added"].within(box(0, 0, 1, 1))
    assert got["added"].equals(box(0, 0, 1, 1)) and got["removed"].equals(box(2, 0, 3, 1))
    assert got["kept"].equals(box(1, 0, 2, 1)) and left == 0
    monkeypatch.setattr(review, "MAX_GEOJSON", 1)
    text, left = review.diff_map(new, old)
    assert left == 3 and all(f["geometry"]["type"] == "Point" for f in json.loads(text)["features"])


def test_patch_applies_add_then_cut():
    feats = [{"geometry": box(1, 0, 2, 1).__geo_interface__, "properties": {"op": "add"}},
             {"geometry": box(0, 0, 0.5, 1).__geo_interface__, "properties": {"op": "cut"}}]
    rng = {"meta": {"basis": "official-patch"}, "features": feats, "geometry": None}
    assert review.effective(rng, box(0, 0, 1, 1)).equals(box(0.5, 0, 2, 1))


def test_plain_neutralises_markdown():
    out = review.plain("x\n\n## [click](http://e.vil) <img> @org/team `c`")
    assert "\n" not in out
    assert re.search(r"(?<!\\)[`\[\]()<>#@]", out) is None  # every control character escaped


def test_preview_url_only_for_safe_names(monkeypatch):
    monkeypatch.setattr(review, "git", lambda *a: "a" * 40 + "\n")
    url = review.preview_url("data/ranges/1/x-1.geojson", "h", "1/usgs-gap")
    assert url.endswith("preview=" + "a" * 40 + "/1/x-1.geojson&vs=1/usgs-gap")
    assert review.preview_url("data/ranges/1/x)[y](z.geojson", "h") == ""
    assert "vs=" not in review.preview_url("data/ranges/1/x.geojson", "h", "1/a)(b")


def test_largest_changes_link_to_each_piece():
    out = review.largest_changes(box(0, 0, 2, 1), box(1, 0, 3, 1), "U")
    assert any(o.startswith("- [🟩") and "&at=" in o and "0.50°N, 0.50°E" in o for o in out)
    assert any(o.startswith("- [🟥") and "2.50°E" in o for o in out)


def test_records_skips_empty_and_survives_gbif_outage(monkeypatch):
    assert review.records(1, box(0, 0, 0, 0)) == "0"

    def down(*a):
        raise review.requests.ConnectionError

    monkeypatch.setattr(review, "gbif_count", down)
    assert review.records(1, box(0, 0, 1, 1)) == "–"


def test_join_capped_keeps_whole_sections_under_limit():
    small = ["a" * 10, "b" * 10]
    assert review.join_capped("H", small) == "H\n\n" + "\n\n".join(small)
    assert review.join_capped("H", []) == "H"
    big = ["x" * 25_000] * 4
    out = review.join_capped("H", big)
    assert len(out) < 65_536 and out.count("x" * 25_000) == 2
    assert out.endswith("2 of 4 files are not shown._")
