# Flood-label second check, 16 Oct 2026

adk's session drafted 14 flood events (2017–2021, commit `fef4165`). Akul's session checked each one
against its source on 16 Oct: page fetched, quote found, date and year from the dateline, time,
and planning area by point-in-polygon on the URA MP2019 polygons. Where possible the original CNA,
Straits Times or TODAY article was also read. The findings are in each row's `notes`
(`data/reference/flood_events.csv`). **`verified_by` stays empty until Akul signs off**: the check
was done by an AI session, and the column records the person who confirmed it.

**Result: 14 of 14 hold up; none rejected. Signed off by Akul on 16 Oct (`verified_by` = `Akul9`).** Decisions: Geylang and Hougang kept as rain-day floods (Hougang's cause changed to `rain`); Rochor Road kept as DOWNTOWN CORE.

| Event | Source re-read | Original reached | Open point for the sign-off |
|---|---|---|---|
| 2017-10-30 Newton, Mount Elizabeth | wildsingapore repost | CNA (Wayback) | Point ~85 m from the ORCHARD boundary |
| 2017-10-30 Downtown Core, Stamford Rd | repost | ST, CNA | Junction ~90 m from MUSEUM/ROCHOR |
| 2018-01-09 Outram, Craig Rd | repost | ST | One passing sentence, no second source |
| 2018-01-30 Seletar, Seletar North Link | repost | TODAY, ST | Start 5.17pm (PUB); ST says about 5.15pm |
| 2018-05-26 Orchard, Kramat Lane | archived TOC page | ST | ~85 m from the NEWTON boundary |
| 2018-06-26 Clementi, Toh Tuck Ave | ADInet | ST | none |
| 2018-06-26 Bukit Timah, Lorong Kismis | repost | CNA (Wayback) | ST calls it "Jalan Kismis" |
| 2018-06-29 Bukit Timah, Lorong Kismis / Dunearn Rd | repost | not reachable | none |
| 2018-09-19 Ang Mo Kio, Ave 6 | Mothership | PUB tweets not reachable | none |
| 2018-11-11 Geylang, Lorong 23 | repost | not reachable | **Cause:** choked construction drain in heavy rain, kept as `rain` |
| 2019-06-03 Downtown Core, Rochor Rd | Coconuts | — | **Planning area ambiguous** (77 m from ROCHOR); Republic Blvd, clearly DOWNTOWN CORE, flooded the same day |
| 2020-11-02 Hougang, Ave 3 | PUB Clarifications page | — | **Cause `other`** (construction runoff); single PUB source plus a MustShareNews photo |
| 2021-07-12 Bukit Timah, Dunearn Rd | MustShareNews | — | none |
| 2021-07-12 Sengkang, TPE slip road | MustShareNews | — | Slip road not geocoded; near PUNGGOL |

**Decisions for the sign-off:**
1. **Construction-related floods:** Geylang (2018-11-11) is `rain` and Hougang (2020-11-02) is
   `other`, though both are blocked or overflowing construction drains during heavy rain. Pick one rule.
   Simplest: keep both as rain-day floods, or drop both from training.
2. **Rochor Road (2019-06-03):** keep it as DOWNTOWN CORE (Republic Blvd supports a Downtown Core flood
   that day), or leave it out.

**To sign off:** set `verified_by` to your initials on the rows you accept. Setting it is what makes a
row count for training.

## New candidates (11), drafted 16 Oct by Akul's session

Added unverified at the end of `flood_events.csv`: seven from 2017, and one each from 2018 and 2021,
plus two from 2022. Every page was fetched, every quote string-matched, and every planning area
point-in-polygon tested. **adk should do their second check**, since Akul's side drafted them. Rows
whose notes need a human look: Orchard Blvd (RIVER VALLEY or ORCHARD?), the BKE shoulder near
Kranji (SUNGEI KADUT is a best guess), reader-video-only rows (Pasir Ris Farmway, Hougang Ave 1,
Woodlands Ave 2, Stirling Rd), and Stomp pages whose year comes from page metadata. What was skipped
and why is in `docs/flood_events_rejected.md`.
