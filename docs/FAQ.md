# FAQ

Formulas and rules are in [METHODS.md](METHODS.md).

## Do I need a GitHub account?

Not always. When the site shows sign-in buttons (ORCID, iNaturalist, GitHub or an email link), sign in and press Submit, and a relay bot opens the pull request for you. If the site shows no sign-in, either create a GitHub account and use Propose edit, or download your range with Export GeoJSON and send it to a maintainer. An email sign-in is unverified, and a maintainer merging it counts as the vouch. See [CONTRIBUTING.md](../CONTRIBUTING.md) and [worker/README.md](../worker/README.md).

## Can I dispute a range?

Yes, by adding your own. Use Edit on the range you disagree with, or draw from scratch, add your sources, and propose it. You cannot overwrite someone else's file: each existing file stays bound to its owner. Both ranges then stay on the map as separate voters, and the agreement map shows where they differ. To discuss the disagreement, use Copy link to this view. The link opens the same species with the same ranges, layers and map position, so a colleague sees what you see. Attach it to your pull request or an email.

## Why is my score 0, or why is there no score?

- "Not yet scored": the scores are recomputed on each push to the main branch and weekly, so a newly merged range has none until the next run. If GBIF cannot be reached during a run, scores are left empty.
- "No records since edit yet": no GBIF record in the species window has an event date on or after your merge date. Credit is then 0 and skill is empty. This is normal for a new range.
- A credit near 0 means the new records inside your range are about what its area would catch by chance.
- Ranges with no GBIF record inside them at all are marked `no_evidence` and not scored. A file that nearly duplicates your own earlier file is marked `superseded`.

The date used is the merge date, not the date you drew the range.

## Why does the range stop at the border?

USGS GAP ranges, imported for 13 taxa, cover the conterminous United States only, so a GAP polygon ends at the Canadian border. That means not mapped, not absent. Draw the northern part yourself, or compare against the other voters. See [SOURCES.md](SOURCES.md).

## Can I use this for a Red List assessment?

Not as a replacement for the Red List process. Red List maps are made by assessors working with a Red List Authority and published by the IUCN Red List Unit. You can export a RangeCraft range, with the sources you entered, and give it to an assessor as supporting evidence, like other expert data. Whether it is used is the assessor's decision. "Export for Red List assessors" in step 4 writes GeoJSON with the IUCN Mapping Standards attributes (SCI_NAME, PRESENCE, ORIGIN, SEASONAL, COMPILER, YEAR, CITATION and others), so the assessor can load it directly. The scores are not an assessment of extinction risk. Birds are assessed through BirdLife International.

## Do exports record where a copy came from?

Yes. An export records the source a copy was derived from (for example the community range or imported source you pressed Edit on), alongside the display name and sources you enter.

## How do I read Range check honestly?

Range check counts GBIF records in square bins against the range it names in the summary ("vs official outline", "vs your draft", or the ticked source such as "vs USGS GAP"). Red bins have records outside the range, so the range may be too small, or some records are misidentified. Blue bins are inside the range with no records yet, which usually reflects sampling effort or habitat, not absence. A high red share can come from where observers go.

Coverage matters too. A source with a coverage note, such as USGS GAP (conterminous US only) or a national-scope NatureServe Canada EBAR range (Canada only), is compared only inside that area, approximated by a box. Bins outside it are hatched grey, meaning not mapped by this source, and are left out of the percentages. Records in Canada are therefore not counted against a GAP range.
When the species has a habitat model (the Habitat model layer, from openbiodiversity.ca), each bin is also scored for suitability. "Suitable" means a score at least as high as the one 90% of in-range bins with records reach. The summary states this threshold. Bins are then split further:

- Red: records outside the range in suitable habitat. The range is likely too small.
- Dark red: records outside the range in unsuitable habitat. Check the IDs ("Help verify these IDs on iNaturalist" in the popup) or the model.
- Blue: inside the range, suitable, no records yet. Check survey effort.
- Grey: inside the range, low suitability. Check the range edge.
- Teal: suitable bins just outside the range with no records. These are worth surveying. "Survey targets (CSV)" downloads the 50 best as a list with iNaturalist and GBIF links.

The habitat model is itself a model, so treat a disagreement between it, the range and the records as a question to check, not a verdict. Suitability is read at each bin's centre only.


## Can I trace the official outline?

Please do not. The ECCC/IUCN outline is shown for reference only, and its licence does not allow republishing under CC BY 4.0. A range declared as an edit of an outline (`basis` of `expert-outline-edit`) needs a `derived_from` source, and its geometry is left out of the public export unless that source has a permissive licence (`PERMISSIVE_LICENSES` in `scripts/rangelib.py`). Its score is still shown.

## Does a bigger range score better?

No. Credit subtracts the number of records a range of that area would catch by chance, so a range that covers the whole window gets about 0 credit and no skill value. A range scores well only if it holds more new records than its area predicts.

## How long until my score means anything?

The code sets no minimum number of records. With a handful of records one observation moves the score a lot. Look at the counts (inside over total since the edit) next to the skill value, and treat a score as weak evidence until there are many records spread over several sites.

## Does a low score mean my range is wrong?

Not necessarily. GBIF records follow observers, not animals, and range maps are outlines that are empty in many places inside them. A remote range can score low simply because few people visit. See the known weaknesses in [METHODS.md](METHODS.md#4-known-weaknesses).

## What does the agreement map show?

Colour shows the share of voters whose range covers each place. A voter is one contributor (their latest range for the species) or one imported source. The summary gives the area where at least half agree, the area where at least one voter disagrees, and an agreement index from 0 to 1. With one voter the index is 1, so it says nothing. Agreement is a count of opinions, not a measure of truth.

## What do the verified and self-declared badges mean?

They concern the expertise links on the Contributors tab, not the range. Verified means the relay signed the person in with ORCID or iNaturalist, or that the iNaturalist or ORCID profile links to their GitHub login. Self-declared means someone typed an id in. Unverified means unconfirmed, not false.

## Is my email address published?

The relay does not write it to the repository. An email sign-in is stored publicly as a hash prefix, and the address lives only in the relay's storage ([worker/README.md](../worker/README.md)). Your display name, any GitHub, ORCID and iNaturalist ids you enter, and the sources text are published in the range file under CC BY 4.0.
