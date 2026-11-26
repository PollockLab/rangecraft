import json

import pytest
from shapely.geometry import Point, box

import import_ranges as ir
from rangelib import RangeError, exportable


def test_pick_gap_item_species_level_only():
    items = [{"title": "The Status of Fisher (Martes pennanti) at the Edge"},
             {"title": "Louisiana Black Bear (Ursus americanus luteolus) mABBEl_CONUS_2001v1 Range Map"},
             {"title": "American Black Bear (Ursus americanus) mABBEx_CONUS_2001v1 Range Map"}]
    assert ir.pick_gap_item(items, "Ursus americanus") is items[2]
    assert ir.pick_gap_item(items, "Martes pennanti") is None
    assert ir.pick_gap_item([], "Ursus americanus") is None


def test_publication_date():
    assert ir.publication_date({"dates": [{"type": "Start", "dateString": "2008"},
                                          {"type": "Publication", "dateString": "2018-08-15"}]}) == "2018-08-15"
    with pytest.raises(RangeError):
        ir.publication_date({"dates": []})


def test_simplify_parts_drops_specks_and_caps_parts():
    g = box(0, 0, 100, 100).union(box(200, 200, 201, 201))
    out = ir.simplify_parts(g, 1, 50)
    assert out.geom_type == "Polygon" and out.area == 10000
    many = [box(i * 3, 0, i * 3 + 2, 2) for i in range(ir.MAX_PARTS + 20)]
    assert len(ir.simplify_parts(ir.unary_union(many), 0.01, 0).geoms) == ir.MAX_PARTS


def test_fit_returns_multipolygon_within_size():
    out = ir.fit(box(-100, 40, -90, 50), 0.01, 0)
    assert out["type"] == "MultiPolygon"
    assert ir.shape(out).contains(Point(-95, 45))


def test_doc_matches_schema(tmp_path):
    d = ir.doc("Lithobates sylvaticus", 2427072, ir.fit(box(-100, 40, -90, 50), 0.01, 0), "USGS GAP",
               "2018-08-15", "src", "US-PD")
    p = tmp_path / "2427072"
    p.mkdir()
    f = p / "usgs-gap.geojson"
    f.write_text(json.dumps(d))
    m = ir.load_range(f)["meta"]
    assert "github" not in m and m["basis"] == "new" and m["derived_from_license"] == "US-PD"


def test_us_pd_is_exportable():
    assert exportable({"basis": "expert-outline-edit", "derived_from_license": "US-PD"})


def test_imported_source_credits_publisher_not_committer():
    from rangelib import imported_source
    assert imported_source({"author": "USGS GAP", "derived_from": "USGS GAP", "basis": "new"}) == "USGS GAP"
    assert imported_source({"author": "Ana", "derived_from": "USGS GAP", "basis": "edit"}) is None
    assert imported_source({"author": "USGS GAP", "derived_from": "USGS GAP", "basis": "new", "github": "x"}) is None
    assert imported_source({"author": "Me Inc", "derived_from": "Me Inc", "basis": "new"}) is None


def test_edits_of_published_outlines_are_not_source():
    """A patch on, or edit of, a publisher's outline is the editor's work, so it keeps the git identity."""
    from rangelib import imported_source
    assert imported_source({"author": "Ann", "basis": "official-patch", "base": "usgs-gap"}) is None
    assert imported_source({"author": "USGS GAP", "basis": "official-patch", "derived_from": "USGS GAP"}) is None
    assert imported_source({"author": "Ann", "basis": "expert-outline-edit",
                            "derived_from": "USGS GAP 2020, data/ranges/1/x.geojson"}) is None


def test_sciencebase_gets_the_default_user_agent(monkeypatch):
    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(ir.time, "sleep", lambda s: None)
    monkeypatch.setattr(ir.requests, "get", lambda url, headers, timeout: seen.update({url: headers}) or Resp())
    ir.get(ir.SB + "items")
    ir.get(ir.GBIF_MATCH)
    assert seen == {ir.SB + "items": None, ir.GBIF_MATCH: ir.UA}
