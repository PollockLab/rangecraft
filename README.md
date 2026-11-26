# RangeCraft

RangeCraft is a web map where experts draw species range maps, reviewers merge them like pull requests, and each range is later checked against new GBIF occurrence records. It complements official range maps. It does not replace them.

Live site: https://pollocklab.github.io/rangecraft/

## What it is for

- Expert-drawn ranges kept next to the official ones. Every contributor and every imported source is a separate map, so differing opinions stay visible.
- An agreement map per species. It shows where the maps overlap and where the edge is contested. See [docs/METHODS.md](docs/METHODS.md).
- Prospective scoring. A range is scored only on GBIF records whose event date is on or after the date the range was merged, compared with what its area alone would give by chance.

## Who it is for

Ecologists, taxon experts and conservation practitioners who know where a species occurs and want to record that knowledge in a form others can inspect, compare and cite. You do not need to know GIS. You can draw in the browser, or load a GeoJSON file made in a GIS.

## Quick start

1. Open the live site and pick a life form (Amphibians, Birds, Mammals and so on) to list the species that already have ranges, or search any species by name.
2. Look at the community ranges listed for it. Each has a checkbox, Edit, and a ⋯ menu that holds the score line once GBIF records have accumulated. A coverage note, when a source has one, shows under the row. The agreement map and GBIF occurrences can be toggled. If an official outline exists for the species, you can show it for reference.
3. Turn on Range check to compare GBIF records with the shown range: the official outline if it is on, your drawing if you have one, otherwise the ticked community ranges. Red bins hold records outside the range, blue bins are in range with no records yet, and hatched grey bins are outside the area a source maps. The summary names what it compared against. When the species has a habitat model, bins are also split by suitability (the score that 90% of in-range bins with records reach): red becomes "range likely too small" or "check IDs or the model", blue is kept only where habitat is suitable, and teal marks suitable bins just outside the range with no records, worth surveying.
4. Draw your own range with the draw toolbar on the map (Polygon or Freehand), or use Edit on a community range, then drag vertices or use Add area and Cut area to reshape it. Do not trace the official outline, its licence does not allow republishing under CC BY 4.0.
5. Enter a display name and your sources, then Propose edit. GitHub opens a new file under `data/ranges/<taxonKey>/`. Commit it to a branch and open a pull request. If the range is too large for a link, use Export GeoJSON and upload the file on GitHub. When the site offers sign-in (ORCID, iNaturalist, GitHub or an email link), you can press Submit without a GitHub account instead, see [worker/README.md](worker/README.md).
6. Use Copy link to this view to share the species, the layers you have on (including Range check) and the map position with a colleague, for example to discuss a disputed edge. The link does not carry an unsaved drawing: export or propose it first.

Run the site locally with `python3 -m http.server` and open http://localhost:8000. There is no build step.

## Data sources and licences

- Contributed ranges: CC BY 4.0, see [DATA_LICENSE.md](DATA_LICENSE.md). Code: MIT, see [LICENSE](LICENSE).
- Imported ranges: USGS GAP (US public domain), Fisheries and Oceans Canada species at risk (Open Government Licence - Canada) and NatureServe Canada EBAR (CC BY 4.0). Each source is its own range file and its own voter.
- Not imported, link only or reference only: IUCN, BirdLife, NatureServe Explorer, iNaturalist taxon ranges, and the ECCC/IUCN outline shown for reference. Their terms do not allow republishing as CC BY 4.0, or could not be confirmed.
- Occurrences come from [GBIF](https://www.gbif.org). Basemap by OpenFreeMap and OpenStreetMap contributors.

Per-source licences, citations and what was checked are in [docs/SOURCES.md](docs/SOURCES.md).

## How this relates to IUCN Red List range maps

RangeCraft is not part of the IUCN Red List process and does not change any Red List map. Red List assessments are made by assessors working through the relevant Red List Authority and published by the IUCN Red List Unit. An exported RangeCraft range, with the sources you entered, can be given to an assessor as supporting evidence, in the same way as any other expert data. Whether to use it is the assessor's decision. Birds go through BirdLife International. See the [FAQ](docs/FAQ.md).

## Limits

- GBIF records are biased toward places and taxa that people visit and report. A range can look wrong against GBIF because of where observers go, not where the species is.
- USGS GAP ranges cover the conterminous United States only. A GAP range that stops at the Canadian border means not mapped, not absent. National-scope NatureServe Canada EBAR ranges are the mirror: they cover Canada only.
- Scoring needs time. Only records dated after a range was merged count, so a new range shows no skill until records dated after the merge reach GBIF. The code sets no minimum number of records, so scores from few records are noisy.
- Range check counts records against a range, so it inherits GBIF's sampling bias: blue bins often mean nobody has looked. Against a source with a coverage note (USGS GAP, national-scope EBAR) it compares only inside that coverage.
- A score measures agreement with new records, not whether a range is correct or complete. Range maps are outlines of extent, and a species is absent from many places inside its range.

## Documentation

- [docs/METHODS.md](docs/METHODS.md): scoring, the agreement map, identity rules, known weaknesses.
- [docs/FAQ.md](docs/FAQ.md): practical questions.
- [docs/SOURCES.md](docs/SOURCES.md): data sources and licences.
- [CONTRIBUTING.md](CONTRIBUTING.md): how a range file is accepted and credited.
- [worker/README.md](worker/README.md): the sign-in relay for contributors without GitHub.
