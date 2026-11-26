import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bbox, inside, pickZoom, addTile, binSize, classify, sdmNote, sampler, bin, verdict, summaryItems, summaryTip, cellArea, surveyCsv, gbifCellUrl, inatCellUrl, inatIdentifyUrl, outsideLine, cellsIn, CONUS, CANADA, coverageOf } from '../src/rangecheck.js';

const sq = (w, s, e, n) => [[w, s], [e, s], [e, n], [w, n], [w, s]];
const fc = (...rings) => ({ type: 'FeatureCollection', features: [{ type: 'Feature', geometry: { type: 'Polygon', coordinates: rings } }] });

test('bbox and point-in-range respect holes', () => {
  const r = fc(sq(0, 0, 10, 10), sq(4, 4, 6, 6));
  assert.deepEqual(bbox(r), [0, 0, 10, 10]);
  const t = inside(r);
  assert.ok(t(1, 1)); assert.ok(!t(5, 5)); assert.ok(!t(11, 1));
  assert.equal(bbox({ features: [] }), null);
});

test('zoom: finest that fits the request and cell budgets', () => {
  const small = pickZoom([-80, 40, -75, 45]);
  assert.equal(small.z, 4);
  assert.ok(small.tiles.length <= 64);
  const big = pickZoom([-170, 25, -50, 70]);
  assert.ok(big.z < 4 && big.tiles.length <= 64);
  assert.equal(pickZoom([-179, -80, 179, 80]).z, 2);
});

test('addTile bins squares at native resolution', () => {
  const counts = new Map(); // tile 2/2/1: lon -90..-45, lat 45..0; its centre (2048,2048) is -67.5, 22.5
  addTile(counts, { z: 2, x: 2, y: 1 }, [{ x: 2048, y: 2048, props: { total: 7 } }, { x: 2048, y: 2048, props: { total: 1 } }]);
  assert.equal(counts.size, 1);
  const size = binSize(2), [k, n] = [...counts][0], [i, j] = k.split(',').map(Number);
  assert.equal(n, 8);
  assert.ok(Math.abs(-180 + (i + 0.5) * size + 67.5) <= size / 2 && Math.abs(90 - (j + 0.5) * size - 22.5) <= size / 2);
});

// Range: lon 0..10, lat 80..90 with 2-degree bins. Bin (i, j) has centre (2i + 1 - 180, 90 - 2j - 1).
const range = fc(sq(0, 80, 10, 90));
const key = (lon, lat) => `${Math.floor((lon + 180) / 2)},${Math.floor((90 - lat) / 2)}`;

test('classify: outside, gap, confirmed at native bins', () => {
  const counts = new Map([[key(1, 89), 5], [key(21, 89), 15], [key(23, 89), 1]]);
  const r = classify(counts, range, 2);
  assert.equal(r.confirmed, 1);
  assert.equal(r.gaps, 24); // 5 x 5 bins in range, 1 has records
  assert.deepEqual(r.cells.filter(c => c.status === 'outside').map(c => c.count), [15, 1]);
  assert.equal(r.total, 21);
  assert.ok(Math.abs(r.outsidePct - (100 * 16) / 21) < 1e-9);
  assert.ok(Math.abs(r.gapPct - (100 * 24) / 25) < 1e-9);
  assert.deepEqual(summaryItems(r), ['76% of records fall outside this range', '96% of the range has no records yet']);
  assert.equal(verdict(r, 'USGS GAP'), 'Many records fall outside this range (vs USGS GAP)');
});

test('outsideMin hides stray bins from the map but not from the summary', () => {
  const counts = new Map([[key(1, 89), 5], [key(21, 89), 15], [key(23, 89), 1]]);
  const r = classify(counts, range, 2, { outsideMin: 3 });
  assert.deepEqual(r.cells.filter(c => c.status === 'outside').map(c => c.count), [15]);
  assert.ok(Math.abs(r.outsidePct - (100 * 16) / 21) < 1e-9);
});

test('no records at all', () => {
  const r = classify(new Map(), range, 2);
  assert.equal(r.outsidePct, 0);
  assert.equal(r.gapPct, 100);
  assert.match(verdict(r), /No GBIF records/);
  assert.deepEqual(summaryItems(r), []);
});

test('area links', () => {
  const c = { w: -80, s: 40, e: -75, n: 45 };
  assert.match(inatCellUrl('Lithobates sylvaticus', c), /taxon_name=Lithobates%20sylvaticus&swlat=40&swlng=-80&nelat=45&nelng=-75/);
  assert.match(gbifCellUrl(2427072, c), /taxon_key=2427072&geometry=POLYGON/);
});

test('coverage: bins outside it are unmapped and not counted', () => {
  const counts = new Map([[key(1, 89), 5], [key(21, 89), 15], [key(-21, 89), 40]]);
  const r = classify(counts, range, 2, { coverage: fc(sq(-10, 80, 30, 90)) });
  assert.equal(r.unmapped, 40);
  assert.equal(r.total, 20);
  assert.ok(Math.abs(r.outsidePct - 75) < 1e-9);
  assert.deepEqual(r.cells.filter(c => c.status === 'unmapped').map(c => c.count), [40]);
  assert.ok(!r.cells.some(c => c.status === 'outside' && c.count === 40));
  assert.match(summaryTip(r, 'conterminous US', 'none'), /Compared within the source's coverage \(conterminous US\)/);
});

test('CONUS coverage excludes Ontario and Ottawa but keeps Chicago and Ohio', () => {
  const t = inside(CONUS);
  assert.ok(!t(-80, 46.5) && !t(-75.7, 45.4));
  assert.ok(t(-87.6, 41.9) && t(-83, 40) && t(-93, 46));
});

test('Canada coverage mirrors CONUS along the border and leaves out Alaska and Greenland', () => {
  const t = inside(CANADA), us = inside(CONUS);
  for (const p of [[-80, 46.5], [-75.7, 45.4], [-123.1, 49.3], [-63.6, 44.6], [-52.7, 47.6], [-135, 60.7], [-85, 79]]) assert.ok(t(...p) && !us(...p), `${p}`);
  for (const p of [[-87.6, 41.9], [-93, 46], [-149.9, 61.2], [-134.4, 58.3], [-51.7, 64.2]]) assert.ok(!t(...p), `${p}`);
});

test('coverageOf restricts only when every range names a known coverage area', () => {
  const r = a => ({ properties: a ? { coverage_area: a } : {}, features: [] });
  assert.equal(coverageOf([]), null);
  assert.equal(coverageOf([r('conterminous-us'), r()]), null);
  assert.equal(coverageOf([r('mars')]), null);
  assert.deepEqual(coverageOf([r('canada')]), { area: CANADA, label: 'Canada' });
  const both = coverageOf([r('conterminous-us'), r('canada'), r('canada')]);
  assert.equal(both.label, 'conterminous US + Canada');
  assert.ok(inside(both.area)(-93, 46) && inside(both.area)(-75.7, 45.4) && !inside(both.area)(-149.9, 61.2));
});

// Habitat suitability on the 2-degree grid above (range lon 0..10, lat 80..90).
test('classify with suitability splits every category and sets the 90% threshold', () => {
  const suit = (lon, lat) => lon > 40 ? null : lat > 90 || lat < 80 || lon < 0 ? 10 : lon < 8 || (lon > 12 && lon < 24) ? 80 : 10;
  const counts = new Map();
  for (const lon of [1, 3, 5, 7]) for (const lat of [89, 87, 85]) counts.set(key(lon, lat), 2); // 12 in-range bins scoring 80
  counts.set(key(9, 89), 1); // in range, scores 10: sorted [10, 80 x12], index floor(1.3) = 1 -> threshold 80
  counts.set(key(15, 89), 4); // outside, suitable
  counts.set(key(25, 89), 6); // outside, low
  const r = classify(counts, range, 2, { suit });
  assert.equal(r.threshold, 80);
  const by = s => r.cells.filter(c => c.status === s);
  assert.deepEqual(by('outside').map(c => c.count), [4]);
  assert.deepEqual(by('outsideLow').map(c => c.count), [6]);
  assert.equal(by('gap').length + by('lowgap').length, r.gaps);
  assert.equal(by('lowgap').length, r.lowGaps);
  assert.ok(by('lowgap').length > 0 && by('gap').length > 0);
  assert.equal(by('survey').length, 0); // the ring bins next to the range score 10
});

test('survey ring: suitable no-record bins one bin from the range only', () => {
  const r = classify(new Map([[key(1, 89), 3]]), range, 2, { suit: () => 50 });
  assert.equal(r.threshold, 50);
  const s = r.cells.filter(c => c.status === 'survey');
  assert.ok(s.some(c => c.w === 10) && !s.some(c => c.w === 12));
  for (const c of s) assert.ok(c.w >= -2 && c.e <= 12 && c.s >= 78 && c.n <= 92);
  assert.equal(r.cells.filter(c => c.status === 'lowgap').length, 0);
});

test('classify without a usable model keeps the plain categories', () => {
  for (const o of [{}, { suit: () => 50 }]) { // second: no in-range record to set a threshold
    const r = classify(new Map([[key(21, 89), 15]]), range, 2, o);
    assert.equal(r.threshold, null);
    assert.deepEqual([...new Set(r.cells.map(c => c.status))].sort(), ['gap', 'outside']);
  }
});

test('nodata bins count as low suitability; sdmNote wording', () => {
  const r = classify(new Map([[key(1, 89), 1]]), range, 2, { suit: lon => lon < 2 ? 40 : null });
  assert.equal(r.threshold, 40);
  assert.equal(r.lowGaps, 20); // the 4 free bins in the scoring column stay suitable
  assert.match(sdmNote(r, 'ok'), /at least 40.*90%/);
  assert.match(summaryTip(r, null, 'ok'), /at least 40.*90%.*20 in-range grid cells have low suitability/);
  assert.equal(sdmNote(r, 'none'), 'No habitat model for this species, so gaps are not habitat-checked.');
  assert.match(sdmNote({ threshold: null }, 'ok'), /not habitat-checked/);
});

test('sampler reads gray+alpha pixels of a lon/lat box', () => {
  const d = new Uint8ClampedArray(2 * 2 * 4); // box 0..10 x 0..10, 2x2 px: row 0 is the north
  d.set([5, 5, 5, 255, 6, 6, 6, 255, 7, 7, 7, 255, 8, 8, 8, 0]);
  const s = sampler([0, 0, 10, 10], 2, d);
  assert.equal(s(2, 8), 5); assert.equal(s(8, 8), 6); assert.equal(s(2, 2), 7);
  assert.equal(s(8, 2), null); // alpha 0 = nodata
  assert.equal(s(-1, 5), null); assert.equal(s(5, 11), null); // outside the box
});

test('surveyCsv ranks by score, caps, escapes, and is header-only when empty', () => {
  const c = (status, score, w) => ({ status, score, count: 0, w, s: 0, e: w + 2, n: 2 });
  const cells = [c('gap', 50, 0), c('survey', 90, 2), c('lowgap', 99, 4), c('outside', null, 6), c('gap', 70, 8)];
  const rows = surveyCsv(cells, { name: 'Rana, "x"', key: 1 }).trim().split('\n');
  assert.equal(rows[0], 'rank,kind,lat,lon,suitability,inat_url,gbif_url');
  assert.deepEqual(rows.slice(1).map(r => r.split(',').slice(0, 5).join()), ['1,frontier,1,3,90', '2,gap,1,9,70', '3,gap,1,1,50']);
  assert.match(rows[1], /Rana%2C%20%22x%22/); // url-encoded, so no stray commas or quotes
  assert.equal(surveyCsv(cells, { name: 'a', key: 1 }, 1).trim().split('\n').length, 2);
  assert.equal(surveyCsv([], { name: 'a', key: 1 }), 'rank,kind,lat,lon,suitability,inat_url,gbif_url\n');
});

test('summaryItems lists survey grid cells only with a model; verdict follows outsidePct', () => {
  const base = { total: 10, outsidePct: 0, gapPct: 5 };
  assert.equal(summaryItems({ ...base, threshold: null, survey: 3 }).length, 2);
  assert.equal(summaryItems({ ...base, threshold: 40, survey: 12 })[2], '12 grid cells just outside the range look suitable but have no records: worth surveying');
  assert.match(summaryItems({ ...base, threshold: 40, survey: 1 })[2], /^1 grid cell just outside the range looks suitable but has no records/);
  assert.equal(summaryItems({ ...base, threshold: 40, survey: 0, lowRecords: 0 }).length, 2);
  for (const [pct, text] of [[0, 'Range fits the records well'], [9.9, 'Range fits the records well'], [10, 'Range may be too small in places'], [30, 'Range may be too small in places'], [30.1, 'Many records fall outside this range']])
    assert.equal(verdict({ total: 5, outsidePct: pct }, 'official outline'), `${text} (vs official outline)`);
});

test('bin rounds large counts', () => {
  assert.deepEqual([0, 7, 9, 10, 49, 50, 99, 100, 1234, 5000, 98765].map(bin), ['0', '7', '9', 'over 10', 'over 10', 'over 50', 'over 50', 'over 100', 'over 1,000', 'over 5,000', 'over 50,000']);
});

test('inatIdentifyUrl filters taxon, bbox and quality grade', () => {
  const u = new URL(inatIdentifyUrl('Lithobates sylvaticus', { s: 1, w: 2, n: 3, e: 4 }));
  assert.equal(u.pathname, '/observations/identify');
  assert.deepEqual([...u.searchParams], [['taxon_name', 'Lithobates sylvaticus'], ['swlat', '1'], ['swlng', '2'], ['nelat', '3'], ['nelng', '4'], ['quality_grade', 'needs_id,research']]);
});

test('lowRecords counts records in outsideLow bins and drives the ID-check sentence', () => {
  const r = classify(new Map([['5,5', 3], ['5,6', 3], ['20,5', 7], ['21,5', 4]]), fc(sq(-180 + 5, 90 - 7, -180 + 7, 90 - 5)), 1, { suit: x => x < -170 ? 80 : x < -159 ? 10 : 80 });
  assert.equal(r.lowRecords, 7);
  assert.equal(summaryItems(r).at(-1), '7 records outside the range in low-suitability habitat: worth an ID check');
  assert.equal(summaryItems({ total: 1, outsidePct: 1, gapPct: 1, threshold: 40, lowRecords: 1500 })[2], 'over 1,000 records outside the range in low-suitability habitat: worth an ID check');
});

test('cellArea: 1x1 degree cell at the equator is about 12,364 km2', () => {
  assert.ok(Math.abs(cellArea({ w: 0, s: 0, e: 1, n: 1 }) / 12364 - 1) < 0.01);
});

test('area of habitat counts only in-range cells at or above the threshold', () => {
  const suit = (lon, lat) => lon < 6 ? 80 : lon < 10 ? 10 : 80; // lon 10+ is suitable but outside the range
  const r = classify(new Map([...[1, 3, 5].flatMap(x => [81, 83, 85, 87, 89].map(y => [key(x, y), 3])), [key(7, 89), 2], [key(12, 89), 1]]), range, 2, { suit }); // 15 suitable + 1 unsuitable record cell, so the 10% threshold is 80
  assert.ok(r.aohKm2 > 0 && r.aohKm2 < r.rangeKm2);
  assert.ok(Math.abs(r.rangeKm2 / cellArea({ w: 0, s: 80, e: 10, n: 90 }) - 1) < 1e-9);
  assert.match(summaryItems(r)[2], /^Area of habitat \(screening\): about [\d,]+ km² of your range is in cells the habitat model rates suitable \(\d+% of the range\)$/);
  const none = classify(new Map([[key(1, 89), 3]]), range, 2);
  assert.equal(none.aohKm2, null);
  assert.ok(!summaryItems(none).some(t => /Area of habitat/.test(t)));
});

test('recordParams builds the GBIF records filter', async () => {
  const { recordParams } = await import('../src/rangecheck.js');
  assert.equal(recordParams('all'), ''); assert.equal(recordParams(undefined), '');
  assert.equal(recordParams('since2000', 2026), '&year=2000,2026');
  assert.equal(recordParams('specimens'), '&basisOfRecord=PRESERVED_SPECIMEN');
});

test('outsideLine words the count and handles zero and one', () => {
  assert.equal(outsideLine(3), '3 GBIF grid cells with records fall outside this range.');
  assert.equal(outsideLine(1), '1 GBIF grid cell with records falls outside this range.');
  assert.match(outsideLine(0), /^No GBIF grid cells/);
});

test('cellsIn counts bins with records whose centre is inside the geometry', () => {
  const size = binSize(4), c = (i, j) => `${i},${j}`, [x, y] = [-180 + 0.5 * size, 90 - 0.5 * size];
  const counts = new Map([[c(0, 0), 5], [c(1, 0), 0], [c(5, 5), 2]]);
  assert.equal(cellsIn(counts, fc(sq(x - 0.1, y - 0.1, x + 0.1, y + 0.1)), size), 1);
  assert.equal(cellsIn(counts, fc(sq(50, 50, 51, 51)), size), 0);
  assert.equal(cellsIn(new Map(), fc(sq(0, 0, 10, 10)), size), 0);
});
