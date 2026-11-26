// Range check wiring: toggle, counting, map layer, popup. Logic lives in rangecheck.js.
import { loadRange, densityTile } from './api.js';
import * as rc from './rangecheck.js';
import { sdmRawBox } from './official.js';
import { fetchCites, addLines } from './cite.js';

const $ = id => document.getElementById(id);
const filter = () => $('rc-filter').value;

// state: { taxon, official }. Returns { clear, legend }; clear() resets the toggle and layer (call on taxon change).
export function initRangeCheck({ map, state, onChange, drawing, draft = () => [], say = () => {} }) {
  const PLAIN = { outside: 'Records outside your range: range may be too small (or misIDs)', gap: 'Inside your range, no records yet (check effort or habitat)', unmapped: 'Not mapped by this source' };
  const HABITAT = { outside: 'Records outside your range, good habitat: range likely too small', outsideLow: 'Records outside your range, poor habitat: check IDs or the model',
    gap: 'Inside your range, good habitat, no records yet (check effort)', lowgap: 'Inside your range, poor habitat (check the range edge)', survey: 'Good habitat outside, unsurveyed', unmapped: PLAIN.unmapped };
  let pts = [], LABELS = PLAIN, present = new Set(), threshold = null, targets = '';
  // Samples the raw habitat model over the box in one request; null when it cannot be read.
  async function loadSuit(slug, box) {
    try {
      box = [Math.max(-180, box[0]), Math.max(-85, box[1]), Math.min(180, box[2]), Math.min(85, box[3])];
      const r = await fetch(sdmRawBox(slug, box));
      if (!r.ok) return null;
      const bmp = await createImageBitmap(await r.blob(), { colorSpaceConversion: 'none' }), g = new OffscreenCanvas(bmp.width, bmp.height).getContext('2d', { willReadFrequently: true });
      g.drawImage(bmp, 0, 0);
      return rc.sampler(box, bmp.width, g.getImageData(0, 0, bmp.width, bmp.height).data);
    } catch { return null; }
  }
  const LAYERS = ['rc-fill', 'rc-hatch'];
  let rcRun = 0, hasUnmapped = false;
  // Fade the GBIF heat map while checking so red outside-range bins stay distinct from dense occurrences.
  const dimOcc = on => map.getLayer('occ') && map.setPaintProperty('occ', 'raster-opacity', on ? 0.35 : 1);
  function clear(msg = '') {
    rcRun++; pts = [];
    for (const id of LAYERS) if (map.getLayer(id)) map.removeLayer(id);
    if (map.getSource('rc')) map.removeSource('rc');
    $('rc-toggle').checked = false;
    dimOcc(false);
    $('rc-summary').textContent = msg;
    $('rc-summary').title = '';
    $('rc-targets').hidden = true;
    onChange();
  }
  // What the check compares against: { fc, ref, coverage: { area, label } | null }. A drawing wins, then the official outline, then the ticked community ranges.
  async function reference() {
    const mine = draft();
    if (mine.length) return { fc: { type: 'FeatureCollection', features: mine }, ref: 'your draft' };
    if (state.official?.fc) return { fc: state.official.fc, ref: 'official outline' };
    const boxes = [...document.querySelectorAll('#versions input:checked')];
    const fcs = await Promise.all(boxes.map(b => loadRange(b.dataset.file)));
    return { fc: { type: 'FeatureCollection', features: fcs.flatMap(fc => fc.features ?? []) }, ref: [...new Set(boxes.map(b => b.dataset.author))].join(' + '), coverage: rc.coverageOf(fcs) };
  }
  // GBIF occurrence bins over a box: { size, tiles, counts }. Stops early when stale() turns true.
  async function binCounts(box, taxon, mode, stale, progress) {
    const { size, tiles } = rc.pickZoom(box), counts = new Map();
    let next = 0, done = 0;
    await Promise.all(Array.from({ length: 4 }, async () => {
      while (next < tiles.length && !stale()) {
        const t = tiles[next++];
        rc.addTile(counts, t, await densityTile(taxon.key, t, undefined, mode));
        if (!stale()) progress(Math.round((100 * ++done) / tiles.length));
      }
    }));
    return { size, tiles, counts };
  }
  // Grid cells with records whose centre falls outside fc, without touching the map layer.
  async function outsideCount(fc) {
    const box = rc.bbox(fc), taxon = state.taxon;
    if (!box) return 0;
    const stale = () => state.taxon !== taxon;
    const { size, counts } = await binCounts(box, taxon, filter(), stale, () => {});
    if (stale()) return null;
    // Same coverage rule as the full check: records beyond a partial source's coverage are unmapped, not outside.
    return rc.classify(counts, fc, size, { coverage: rc.coverageOf([fc])?.area }).cells.filter(c => c.status === 'outside').length;
  }
  // Grid cells with records whose centre falls inside fc (null if the species changed meanwhile); honours the Records filter.
  async function recordsIn(fc) {
    const box = rc.bbox(fc), taxon = state.taxon;
    if (!box) return 0;
    const stale = () => state.taxon !== taxon;
    const { size, counts } = await binCounts(box, taxon, filter(), stale, () => {});
    return stale() ? null : rc.cellsIn(counts, fc, size);
  }
  async function runRangeCheck(scroll = false) {
    const run = ++rcRun, taxon = state.taxon, mode = filter();
    const stale = () => run !== rcRun || state.taxon !== taxon;
    try {
      const { fc, ref, coverage } = await reference();
      const box = rc.bbox(fc);
      if (!box) { clear('Show a community range or load the official outline first.'); return; }
      $('rc-summary').textContent = 'Counting GBIF records...';
      const { size, tiles, counts } = await binCounts(box, taxon, mode, stale, pct => { $('rc-summary').textContent = `Counting GBIF records... ${pct}%`; });
      if (stale()) return;
      let suit = null, why = 'none';
      if (state.official) {
        $('rc-summary').textContent = 'Reading the habitat model...';
        suit = await loadSuit(state.official.slug, rc.tilesBox(tiles, size)); why = suit ? 'ok' : 'unreadable';
        if (stale()) return;
      }
      const res = rc.classify(counts, fc, size, { coverage: coverage?.area, suit }), inRange = rc.inside(fc);
      // Bin centres (not records) inside the range: GBIF bins are coarse, so the AOO built from them is a screening lower bound.
      pts = [...counts].filter(([, n]) => n > 0).map(([k]) => rc.centre(...k.split(',').map(Number), size)).filter(c => inRange(...c));
      threshold = res.threshold; LABELS = threshold == null ? PLAIN : HABITAT;
      present = new Set(res.cells.map(c => c.status));
      hasUnmapped = res.unmapped > 0;
      for (const id of LAYERS) if (map.getLayer(id)) map.removeLayer(id);
      if (map.getSource('rc')) map.removeSource('rc');
      if (!map.hasImage('rc-hatch')) map.addImage('rc-hatch', hatch());
      map.addSource('rc', { type: 'geojson', data: rc.toGeoJSON(res.cells) });
      map.addLayer({ id: 'rc-hatch', type: 'fill', source: 'rc', filter: ['==', ['get', 'status'], 'unmapped'], paint: { 'fill-pattern': 'rc-hatch' } }, 'occ');
      map.addLayer({ id: 'rc-fill', type: 'fill', source: 'rc', filter: ['!=', ['get', 'status'], 'unmapped'], paint: {
        'fill-color': ['match', ['get', 'status'], 'outside', rc.COLORS.outside, 'outsideLow', rc.COLORS.outsideLow, 'lowgap', rc.COLORS.lowgap, 'survey', rc.COLORS.survey, rc.COLORS.gap],
        // red fades for stray records: 1-2 records are faint, 100+ solid
        'fill-opacity': ['case', ['in', ['get', 'status'], ['literal', ['gap', 'lowgap', 'survey']]], 0.3, ['interpolate', ['linear'], ['get', 'count'], 1, 0.12, 3, 0.3, 10, 0.5, 100, 0.75]] } }, 'occ');
      const covered = coverage?.label, sum = $('rc-summary'), list = document.createElement('ul');
      list.replaceChildren(...rc.summaryItems(res).map(t => Object.assign(document.createElement('li'), { textContent: t })));
      sum.replaceChildren(Object.assign(document.createElement('em'), { textContent: 'Rough guide: gaps often mean nobody has looked there yet.' }), document.createElement('br'), Object.assign(document.createElement('strong'), { textContent: rc.verdict(res, [ref, rc.RECORD_NOTE[mode] && `based on ${rc.RECORD_NOTE[mode]}`].filter(Boolean).join(', ')) }), list);
      sum.title = rc.summaryTip(res, covered, why);
      targets = rc.surveyCsv(res.cells, taxon);
      $('rc-targets').hidden = targets.split('\n').length < 3;
      onChange();
      if (scroll) { sum.style.scrollMarginTop = '4.5rem'; sum.scrollIntoView({ block: 'start' }); } // below the sticky step bar, clear of the sticky propose card at the bottom
    } catch (e) { if (!stale()) clear(`Range check failed: ${e.message}`); }
  }
  $('rc-targets').addEventListener('click', () => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([targets], { type: 'text/csv' }));
    a.download = `${state.taxon.name.replace(/\W+/g, '-').toLowerCase()}-survey-targets.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
  $('rc-toggle').addEventListener('change', e => { if (e.target.checked) { dimOcc(true); runRangeCheck(true); } else clear(); });
  // Re-run when the compared layer changes (official toggle, ticks, drawing); debounced because drawing fires many changes.
  let timer;
  const refresh = () => { if ($('rc-toggle').checked) { clearTimeout(timer); timer = setTimeout(runRangeCheck, 500); } };
  $('versions').addEventListener('change', refresh);
  $('rc-filter').addEventListener('change', refresh);
  const hatch = () => {
    const c = Object.assign(document.createElement('canvas'), { width: 8, height: 8 }), g = c.getContext('2d');
    g.strokeStyle = '#8a8a8a'; g.lineWidth = 1.2; g.beginPath(); g.moveTo(0, 8); g.lineTo(8, 0); g.moveTo(-1, 1); g.lineTo(1, -1); g.moveTo(7, 9); g.lineTo(9, 7); g.stroke();
    return g.getImageData(0, 0, 8, 8);
  };
  for (const id of LAYERS) map.on('click', id, e => {
    if (drawing()) return;
    const p = e.features[0].properties, el = document.createElement('div');
    el.append(p.count === 0 ? `${LABELS[p.status]}.` : `${LABELS[p.status]} (${p.count} record${p.count === 1 ? '' : 's'}).`);
    if (p.status === 'gap' || p.status === 'survey') {
      for (const [text, href] of [['Look on iNaturalist', rc.inatCellUrl(state.taxon.name, p)], ['Look on GBIF', rc.gbifCellUrl(state.taxon.key, p)]]) {
        el.append(Object.assign(document.createElement('a'), { href, textContent: text, target: '_blank', rel: 'noopener' }));
      }
      el.append(Object.assign(document.createElement('small'), { textContent: 'No records is not absence: it may only mean nobody has looked.' }));
    }
    if (p.status === 'outside' || p.status === 'outsideLow')
      el.append(Object.assign(document.createElement('a'), { href: rc.inatIdentifyUrl(state.taxon.name, p), textContent: 'Help verify these IDs on iNaturalist', target: '_blank', rel: 'noopener' }));
    if (p.count > 0) {
      const b = Object.assign(document.createElement('button'), { type: 'button', textContent: 'Cite records here', className: 'linkbtn' });
      b.addEventListener('click', async () => {
        try {
          const { text, added } = addLines($('sources').value, await fetchCites(state.taxon.key, p, undefined, filter()));
          $('sources').value = text;
          say(added ? `Added ${added} record${added === 1 ? '' : 's'} to Sources.` : 'No new records to add.');
        } catch (e) { say(`Could not fetch records: ${e.message}`); }
      });
      el.append(b);
    }
    new maplibregl.Popup().setLngLat(e.lngLat).setDOMContent(el).addTo(map);
  });
  const tip = () => `Suitable = habitat score of at least ${threshold}, which 90% of in-range grid cells with records reach.`;
  const legend = () => map.getLayer('rc-fill') ? Object.keys(LABELS).filter(k => threshold == null ? k !== 'unmapped' || hasUnmapped : present.has(k)).map(k => ({ label: LABELS[k], swatch: rc.COLORS[k], ...(threshold != null && k !== 'unmapped' && { title: tip() }) })) : [];
  return { clear, legend, refresh, outsideCount, recordsIn, points: () => pts };
}
