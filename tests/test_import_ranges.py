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
               "2018-08-15", "src", "US-PD", "conterminous-us")
    p = tmp_path / "2427072"
    p.mkdir()
    f = p / "usgs-gap.geojson"
    f.write_text(json.dumps(d))
    m = ir.load_range(f)["meta"]
    assert "github" not in m and m["basis"] == "new" and m["derived_from_license"] == "US-PD"
    assert m["coverage"] == ir.COVERAGE["conterminous-us"] and m["coverage_area"] == "conterminous-us"


def test_us_pd_is_exportable():
    assert exportable({"basis": "expert-outline-edit", "derived_from_license": "US-PD"})


def test_imported_source_credits_publisher_not_committer():
    from rangelib import imported_source
    assert imported_source({"author": "USGS GAP", "derived_from": "USGS GAP", "basis": "new"}) == "USGS GAP"
    assert imported_source({"author": "Ana", "derived_from": "USGS GAP", "basis": "edit"}) is None
    assert imported_source({"author": "USGS GAP", "derived_from": "USGS GAP", "basis": "new", "github": "x"}) is None
    ebar = {"author": "NatureServe Canada", "derived_from": "NatureServe Canada", "basis": "new"}
    assert imported_source(ebar) == "NatureServe Canada"
    assert imported_source({"author": "Me Inc", "derived_from": "Me Inc", "basis": "new"}) is None


def test_edits_of_published_outlines_are_not_source():
    """A patch on, or edit of, a publisher's outline is the editor's work, so it keeps the git identity."""
    from rangelib import imported_source
    assert imported_source({"author": "Ann", "basis": "official-patch", "base": "usgs-gap"}) is None
    assert imported_source({"author": "USGS GAP", "basis": "official-patch", "derived_from": "USGS GAP"}) is None
    assert imported_source({"author": "Ann", "basis": "expert-outline-edit",
                            "derived_from": "USGS GAP 2020, data/ranges/1/x.geojson"}) is None


def ebar_feature(mapid, presence, scope, date_ms, x):
    return {"type": "Feature", "geometry": ir.mapping(box(x, 50, x + 1, 51)),
            "properties": {"rangemapid": mapid, "presence": presence, "rangemapscope": scope, "rangedate": date_ms,
                           "rangestage": "Expert Reviewed", "natureserveexplorerurl": "https://explorer.example/1"}}


def test_arcgis_features_pages_until_a_short_page(monkeypatch):
    offsets = []

    class Resp:
        def __init__(self, n):
            self.n = n

        def json(self):
            return {"features": [{}] * self.n}

    def fake_get(url, params):
        offsets.append(params["resultOffset"])
        return Resp(1000 if params["resultOffset"] == 0 else 3)

    monkeypatch.setattr(ir, "get", fake_get)
    assert len(ir.arcgis_features("u", "1=1", "*", "oid")) == 1003 and offsets == [0, 1000]


def test_pick_ebar_map_prefers_global_then_newest():
    old_g, new_n = ebar_feature(1, "P", "G", 1_000, 0), ebar_feature(2, "P", "N", 9_000, 0)
    assert ir.pick_ebar_map([new_n, old_g, new_n]) == [old_g]
    newer_n = ebar_feature(3, "P", "N", 9_500, 0)
    assert ir.pick_ebar_map([new_n, newer_n]) == [newer_n]


def test_ebar_dissolves_present_and_expected_only_and_notes_national_scope(monkeypatch):
    feats = [ebar_feature(7, "P", "N", 1773920338000, -100), ebar_feature(7, "X", "N", 1773920338000, -99),
             ebar_feature(7, None, "N", 1773920338000, -98), ebar_feature(7, "H", "N", 1773920338000, -90)]
    monkeypatch.setattr(ir, "arcgis_features", lambda url, where, *a: feats if "'Chelydra serpentina'" in where else [])
    geometry, created, src, author, lic, slug, coverage = ir.ebar("Chelydra serpentina")
    g = ir.shape(geometry)
    assert g.contains(Point(-99.5, 50.5)) and g.contains(Point(-98.5, 50.5))
    # blank presence is an ecoshape removed in expert review, H is historical
    assert not g.contains(Point(-97.5, 50.5)) and not g.contains(Point(-89.5, 50.5))
    assert (created, author, lic, slug, coverage) == ("2026-03-19", "NatureServe Canada", "CC-BY-4.0",
                                                      "natureserve-ebar", "canada")
    assert "range map 7" in src and "EBAR © NatureServe Canada 2026" in src
    d = ir.doc("Chelydra serpentina", 1, geometry, author, created, src, lic, coverage)["properties"]
    assert d["coverage"].startswith("Canada only") and d["coverage_area"] == "canada"
    with pytest.raises(RangeError):
        ir.ebar("Chelydra serpentina serpentina")


def test_ebar_global_scope_has_no_coverage_note_and_historical_only_fails(monkeypatch):
    monkeypatch.setattr(ir, "arcgis_features", lambda *a: [ebar_feature(7, "P", "G", 1773920338000, -100)])
    assert ir.ebar("x")[6] is None
    assert "coverage" not in ir.doc("x", 1, None, "NatureServe Canada", "2026-03-19", "s", "CC-BY-4.0")["properties"]
    monkeypatch.setattr(ir, "arcgis_features", lambda *a: [ebar_feature(7, "H", "N", 1773920338000, -100)])
    with pytest.raises(RangeError):
        ir.ebar("x")


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


def test_import_one_skips_per_source_file(monkeypatch, tmp_path):
    monkeypatch.setattr(ir, "RANGES_DIR", tmp_path)
    monkeypatch.setattr(ir, "ROOT", tmp_path)
    monkeypatch.setattr(ir, "gbif_key", lambda name: 5)
    calls = []

    def fn(name, meta):
        calls.append(name)
        return (ir.fit(box(-100, 50, -90, 60), 0.01, 0), "2026-03-19", "s", "NatureServe Canada", "CC-BY-4.0",
                "natureserve-ebar", "canada")

    (tmp_path / "5").mkdir()
    (tmp_path / "5" / "usgs-gap.geojson").write_text("{}")
    assert ir.import_one("A b", fn, "natureserve-ebar", {}, False) == 5
    ir.import_one("A b", fn, "natureserve-ebar", {}, False)
    assert calls == ["A b"]
    ir.import_one("A b", fn, "natureserve-ebar", {}, True)
    assert calls == ["A b", "A b"]
    assert ir.load_range(tmp_path / "5" / "natureserve-ebar.geojson")["meta"]["coverage_area"] == "canada"
