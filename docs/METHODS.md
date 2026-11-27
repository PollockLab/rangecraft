# Methods

This page describes what the code does, with the file and function that does it. If this page and the code differ, the code is right.

## 1. Prospective scoring

Code: `scripts/score.py`, `scripts/rangelib.py`. Scores are recomputed on every push to `main` and weekly (`.github/workflows/pages.yml`). If GBIF cannot be reached, the run falls back to `--offline` and writes no scores.

### Counts

All counts are GBIF occurrence search totals (`limit=0`, no sampling) with these filters: has coordinates, no geospatial issue flagged, occurrence status PRESENT, and the species `taxonKey` (`gbif_count`, `score.py`). "Post" counts add `eventDate >= edit_date`.

| Name | Meaning |
|---|---|
| `window_post` | records in the taxon window dated on or after `edit_date` |
| `inside_post` | records inside the range polygon dated on or after `edit_date` |
| `out_post` | `window_post - inside_post` |
| `r` (`area_ratio`) | range area divided by window area, capped at 1 |

### Formulas

```
credit = inside_post - window_post * r
skill  = (inside_post / window_post - r) / (1 - r)
```

- `credit` is the number of new records inside the range beyond what a randomly placed range of the same area would hold. It is negative when new records mostly fall outside. In code, `r` is clamped to the interval 0 to 1 (`credit`, `rangelib.py`).
- `skill` is 0 for random placement and 1 when every new record is inside. It is not reported (null) when `window_post` is 0 or `r` is 1 or more (`skill`, `rangelib.py`).
- Stored rounding: credit to 3 decimals, skill to 4, `r` to 6 (`score_range`, `score.py`).

Worked example: a window holds 200 new records and the range covers 25% of the window. If 120 records fall inside, credit is 120 - 200 * 0.25 = 70 and skill is (0.6 - 0.25) / 0.75 = 0.467. If 50 fall inside, credit is 0 and skill is 0.

### The window

The window is the bounding box of the union of all counted ranges for the taxon, buffered by 2 degrees and clamped to the globe (`window_for_taxon`, `WINDOW_BUFFER_DEG`). It does not depend on any one submission, so a contributor cannot shrink it to flatter their own range. It does change when another range for the same taxon is added or removed, and then every range for that taxon is rescored against the new window.

### The date

`edit_date` is the date of the first-parent commit that added the file (`git log --first-parent --diff-filter=A --follow`, `git_origin`). With squash-only merges that is the merge commit, and GitHub sets its date, so the contributor cannot choose it. The `created` property in the file is informational only. A range drawn in January and merged in March is scored on records from March onward.

### What is not scored

- Superseded files: same taxon, same git author, and a later file overlapping it with intersection over union above 0.95 (`superseded`, `IOU_DUPLICATE`). Reason `superseded`.
- Ranges with zero GBIF records inside them at any date (`inside_all` of 0). Reason `no_evidence`.
- Both are listed in `data/ranges/index.json` with `counted: false`.

### Geometry used for counting

GBIF is queried with a polygon in the URL, which must stay under 3000 characters. The polygon is simplified in steps, and from a tolerance of 0.128 degrees the smallest parts are dropped (`wkt_limited`). The code states that scoring then undercounts, never overcounts. Area is spherical (Chamberlain-Duquette), with an error the code documents as under about 1% (`area_km2`).

Range files are also limited to 200 parts and a bounding box at most 50 times the geometry area, which blocks scattered specks (`check_geometry`).

## 2. Agreement map

Code: `scripts/consensus.py`. Built per taxon from the same range files.

1. Voters. Each contributor casts one vote, using their latest file by `created`, then filename (`load_voters`). An imported source (USGS GAP, Fisheries and Oceans Canada) is its own voter (`imported_source`). A commit that is not verified votes once per file and is never pooled with other files (`credit_key`).
2. Arrangement. The boundaries of all voters are overlaid and split into the faces of the planar arrangement (`arrangement`).
3. Counting. For each face, k is the number of voters whose range contains the face's representative point (`count_faces`). Faces with the same k are merged (`dissolve`).
4. Shares. Each output feature carries `agree` (k), `voters` (n) and `share` (k/n, 4 decimals). The map colours shares in steps at 0.2, 0.4, 0.6 and 0.8 (`shareFill`, `src/consensus.js`).

Summary values (`summarize`):

- `consensus_area_km2`: area where k/n is at least 0.5.
- `contested_area_km2`: area where k is below n, so at least one voter disagrees.
- `agreement_index`: area covered by all n voters divided by area covered by at least one (`agreement_index`). It runs from 0 to 1, and is 0 with no voters. With a single voter it is 1 by construction, so read it only when n is 2 or more.

The agreement map is not a probability. It counts opinions, and a voter that copies another counts twice. Optionally `--with-official` adds the ECCC/IUCN outline as one more voter. That is for local analysis only, and its licence bars republishing the output. The published maps are built from contributors and imported sources only.

## 3. Identity and credit

Code: `scripts/rangelib.py` (`identity_from_commit`, `git_origin`, `is_relay_email`, `imported_source`), `scripts/validate.py`, `scripts/expertise.py`. Policy: [CONTRIBUTING.md](../CONTRIBUTING.md).

Credit goes to whoever the first-parent commit says added the file, never to a name written inside the file.

| Case | How it is decided | Identity |
|---|---|---|
| GitHub noreply login | The commit author email matches `<id>+<login>@users.noreply.github.com`. On a squash merge GitHub sets this from the PR author. | `github` |
| Relay submitter | The commit email is the relay bot's noreply address, so credit goes to the signed-in person named in the file's `submitter` property, keyed as `<provider>:<id>`. | the provider (`orcid`, `inat`, `github`) if verified, otherwise `unverified` |
| Email sign-in | Same path as the relay. An email sign-in can never be verified. | `unverified` |
| Publisher on the allowlist | Only `USGS GAP` and `Fisheries and Oceans Canada`, only for files with `basis` of `new`, `derived_from` equal to the publisher and no `github` owner. | `source` |
| Anything else | Credited by git author name. A free-form name is spoofable. | `unverified` |

Rules enforced in CI (`validate.py`):

- A `github` property must equal the PR author, and an existing file stays bound to its recorded owner.
- A file with no `github` owner may only be added by a maintainer or the relay.
- `submitter` may only be added by the relay bot, from a branch of this repository, on a new file. It never changes afterwards.

Expertise (`scripts/expertise.py`) is a separate, external record: iNaturalist research-grade observations and identifications, and OpenAlex publication counts by ORCID. ORCID and iNaturalist ids typed into a file are self-declared. They count as verified only when the relay signed the person in with that provider, or when the iNaturalist or ORCID profile links to the contributor's verified GitHub login. Not verified means unconfirmed, not false. Credit and skill are per-range quality measures, not a reputation score.

## 4. Known weaknesses

- Sampling bias. GBIF records come from where people look and what they report. A range that covers roads, cities and protected areas with active observers collects more new records than one covering remote habitat, whatever the true distribution. Credit compares with area, not with observer effort.
- Spatial autocorrelation. New records are not independent. One observer revisiting a site, or a bulk upload from a single survey, adds many records at one place. The code counts records, not sites, observers or grid cells, and gives no confidence interval.
- Observer effort and time. Effort changes between years and regions. Few records after the edit date make credit and skill unstable, and the code sets no minimum.
- Taxonomic synonyms. Counts use one GBIF `taxonKey` per range. Records filed under other names, splits, lumps or misidentifications are counted only to the extent GBIF assigns them to that key. Matching a species to the official outline is done by name, so a synonym mismatch can leave a species without one (`outline_slug`, `consensus.py`).
- Outline versus occurrence. Range maps are extent-of-occurrence outlines. A species is absent from many places inside its range, and an occurrence record outside it is not proof the map is wrong. Hurlbert and Jetz 2007 show that range maps match occurrence only at coarse resolution, and Merow et al. 2017 argue for combining expert maps with point records because each has different biases.
- Window effects. Skill depends on the window, which depends on the other ranges for the species.
- Coverage. USGS GAP ranges cover the conterminous United States only, and national-scope NatureServe Canada EBAR ranges cover Canada only.

## References

- Hurlbert, A. H. and Jetz, W. 2007. Species richness, hotspots, and the scale dependence of range maps in ecology and conservation. Proceedings of the National Academy of Sciences 104:13384-13389. https://doi.org/10.1073/pnas.0704469104
- Merow, C., Wilson, A. M. and Jetz, W. 2017. Integrating occurrence data and expert maps for improved species range predictions. Global Ecology and Biogeography 26:243-258. https://doi.org/10.1111/geb.12539

DOIs are those recorded in [SOURCES.md](SOURCES.md). Titles and pages were checked against Crossref on 2026-11-19.
