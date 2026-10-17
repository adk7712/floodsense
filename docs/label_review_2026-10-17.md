# Flood-label second check, 17 Oct 2026

Akul's session drafted 11 candidate flood events on 16 Oct (commit `0b1edd4`). adk's session then
checked each one with AI agents on 17 Oct: the page was fetched, the quote was matched word for word,
the date came from the dateline, the time precision was set, the planning area was found by
point-in-polygon on URA MP2019, reported floods were told apart from warnings, and reader-submitted
pages were checked for corroboration.

**Result: signed off by adk on 17 Oct.** adk opened the sources and accepted the agents' check
(AI-assisted). 8 rows kept with `verified_by` = `adk` (2 fixes and 4 notes applied, as below); 3
rows moved to `docs/flood_events_rejected.md`. The label file now has 88 verified events and no
unverified rows.

## Verdicts

| # | Event (date, place, area) | Source | Verdict | What adk should look at |
|---|---|---|---|---|
| 1 | 2017-01-23, Craig Rd / Tanjong Pagar Rd (OUTRAM) | [Wildsingapore repost of CNA, TODAY](https://wildsingaporenews.blogspot.com/2017/01/heavy-downpour-causes-flash-floods.html) | PASS | Nothing urgent. The whole road is 150 m or more from any boundary. |
| 2 | 2017-01-23, Maxwell Rd (DOWNTOWN CORE) | [Same repost as row 1](https://wildsingaporenews.blogspot.com/2017/01/heavy-downpour-causes-flash-floods.html) | PASS, add boundary note | The 30 m flood stretch straddles the OUTRAM / DOWNTOWN CORE line. Keep DOWNTOWN CORE. |
| 3 | 2017-02-08, Hill St / Stamford Rd (MUSEUM) | [Wildsingapore repost of CNA, AsiaOne](https://wildsingaporenews.blogspot.com/2017/02/flash-floods-hit-orchard-road-bugis.html) | PASS, add boundary note | The junction is 5 to 8 m from DOWNTOWN CORE. MUSEUM is a coin flip at polygon level. |
| 4 | 2017-02-08, Orchard Blvd towards Paterson Rd (RIVER VALLEY, then ORCHARD) | [Same repost as row 3](https://wildsingaporenews.blogspot.com/2017/02/flash-floods-hit-orchard-road-bugis.html) | FIX | Change area RIVER VALLEY to ORCHARD and event_id to `2017-02-08-orchard-orchard-blvd`. The road runs along the ORCHARD / RIVER VALLEY line. |
| 5 | 2017-04-27, Hougang Ave 1 (HOUGANG) | [Stomp reader video](https://www.stomp.sg/singapore-seen/singapore/workers-wade-through-shin-deep-water-after-heavy-rain-causes-flood-at-kovan) | REJECT | Reader video only. No corroboration found. Year comes from page metadata only. |
| 6 | 2017-07-14, Tomlinson Rd / Cuscaden Rd (ORCHARD) | [Stomp, relaying LTA and PUB tweets](https://www.stomp.sg/singapore-seen/flash-floods-in-some-areas-of-singapore) | FIX | Change time_precision from `exact` to `approx_15min`. The 12:39 and 12:59 times are tweet times, not flood start and end. |
| 7 | 2017-12-15, Pasir Ris Farmway (PASIR RIS) | [Wildsingapore repost of Straits Times](https://wildsingaporenews.blogspot.com/2017/12/flash-floods-in-eastern-singapore-cars.html) | KEEP (adk decided, 17 Oct) | Source type: news report (ST) of an anonymous online video. No PUB or other news corroboration found. |
| 8 | 2018-11-12, Woodlands Ave 2 (WOODLANDS) | [Stomp reader videos](https://www.stomp.sg/singapore-seen/stompers-experience-flash-floods-in-woodlands-during-heavy-rain) | REJECT | Reader videos only. No corroboration found. Year comes from page metadata only. |
| 9 | 2021-11-09, King's Road (BUKIT TIMAH) | [Stomp, PUB spokesman statement](https://www.stomp.sg/singapore-seen/singapore-hit-by-flash-floods-during-tuesdays-storm); corroborated by [MS News](https://mustsharenews.com/flood-bukit-timah/) | PASS | PUB's own statement is on the page, and MS News reports the same ponding. Nearest boundary is 155 m away. |
| 10 | 2022-11-07, BKE road shoulder near Kranji Exit 8 (no single area) | [Stomp, PUB statement](https://www.stomp.sg/singapore-seen/car-drives-through-floodwater-on-bke-can-go-kayaking-on-highway-in-future); corroborated by [Mothership](https://mothership.sg/2022/11/bke-flooded-drains-blocked/) | REJECT | The flood is real. But the expressway there is within about 10 m of the SUNGEI KADUT, MANDAI and WOODLANDS lines, and no source gives a direction or km. |
| 11 | 2022-11-13, behind Block 48 Stirling Rd (QUEENSTOWN) | [Stomp, reader video and town council reply](https://www.stomp.sg/singapore-seen/flooding-at-stirling-road-town-council-says-no-issue-with-estate-drainage-system-will) | KEEP (adk decided, 17 Oct) | Source type: Tanjong Pagar Town Council statement only, plus a reader video. No PUB or news report found. |

## Steps for adk

1. Open each source link above. Start with rows 2, 3, 4, 6, 7 and 11, since those need a judgement.
   For each one, check that the quote is on the page and that the date and time match what it says.
2. Reply "sign off" in chat when you are done. The AI session will then:
   - move rows 5, 8 and 10 to `docs/flood_events_rejected.md`, with the reasons in the table above;
   - apply the fixes to rows 4 and 6;
   - add the boundary notes to rows 2 and 3, and the source-type notes to rows 7 and 11;
   - set `verified_by` to your initials on the 8 kept rows (1, 2, 3, 4, 6, 7, 9 and 11).
3. After that, the model is re-run with the updated labels.

## Not checked

- Original CNA, TODAY and Straits Times articles for rows 1, 2, 3 and 7. Only the Wildsingapore reposts were read.
- The TheOnlineCitizen page for row 1. Only a search snippet was seen (UNVERIFIED).
- The LTA tweet at 9.23am for rows 1 and 2. Seen in a search snippet only (UNVERIFIED).
- PUB X, Facebook and Telegram archives for 2017 and 2018. These cannot be searched, so "no corroboration found" does not mean no report exists.
- The Torque page for row 6 returned 403. Its early-2018 mention of the junction flooding is UNVERIFIED.
- The exact BKE stretch for row 10. No source gives a direction or km.
- The "Kranji Exit 8" wording is Stomp's, not Mothership's. Mothership does not mention Kranji or Exit 8.

## Per-row evidence

1. **Craig Rd / Tanjong Pagar Rd, 2017-01-23.** Quote found in the TODAY passage. Date from "Monday (Jan 23)" and the dateline "23 Jan 17". Junction not geocoded (Overpass timed out). Craig Rd points are OUTRAM, 150 to 161 m from BUKIT MERAH and 189 to 235 m from DOWNTOWN CORE. Corroboration: the repost reproduces CNA, TODAY and TNP, all citing PUB.
2. **Maxwell Rd, 2017-01-23.** Quote found in the CNA passage ("At 10am, the flash flood at Maxwell Road had subsided"). Date as row 1. Junction with South Bridge Rd is OUTRAM, 2 to 17 m from DOWNTOWN CORE. Kadayanallur St point is DOWNTOWN CORE, 29 m from OUTRAM. Corroboration: same repost as row 1.
3. **Hill St / Stamford Rd, 2017-02-08.** Quote found in the CNA repost (PUB tweet, "around 6pm"). Date from the CNA dateline (8 Feb 17) and AsiaOne (9 Feb 2017). Junction nodes are MUSEUM, 5 m from DOWNTOWN CORE, and DOWNTOWN CORE, 8 m from MUSEUM. Corroboration: AsiaOne relays the same PUB tweet.
4. **Orchard Blvd towards Paterson Rd, 2017-02-08.** Quote found in AsiaOne ("towards Paterson Road", PUB tweet at about 6pm). Date as row 3. Three of five geocodes are ORCHARD. The Paterson Rd end is ORCHARD, 2 m from RIVER VALLEY, so the fix is moderate confidence.
5. **Hougang Ave 1, 2017-04-27.** Quote found verbatim ("at 5pm today (April 27)"). No year in body. Metadata gives 2017-04-27. No PUB, ST, CNA, TODAY or AsiaOne report found. Location is HOUGANG, 590 m from any other area.
6. **Tomlinson Rd / Cuscaden Rd, 2017-07-14.** Quote found ("hit by flash floods today (July 14) afternoon"). Year from metadata only. Tweets: LTATrafficNews at 12:39 (flooded), PUB at 12:59 (subsided). Junction is ORCHARD, 121 m from TANGLIN and 142 m from RIVER VALLEY.
7. **Pasir Ris Farmway, 2017-12-15.** Quote found in both sentences. Date from the ST dateline "15 Dec 17" and "Friday (Dec 15)". Farmway 1 to 3 are all PASIR RIS, with the nearest other area 1.0 to 1.2 km away. The only evidence is an unattributed video "going around online". Searches of CNA, TODAY and Mothership found nothing.
8. **Woodlands Ave 2, 2018-11-12.** Quote found, built from two joined fragments. Body says "Nov 12" with no year. Metadata gives 2018-11-13. Location is WOODLANDS, 313 to 710 m from MANDAI. Only reader videos. Mothership has a flood on 10 Nov 2018 in another area.
9. **King's Road, 2021-11-09.** Quote found in the PUB spokesman statement ("around 3.30pm... along a 50m stretch of King's Road"). Date from "Tuesday afternoon (Nov 9)" and "Wednesday (Nov 10)". King's Road point is BUKIT TIMAH, 260 m from TANGLIN. Nearest point is the park at 155 m. Corroboration: [MS News](https://mustsharenews.com/flood-bukit-timah/), published 10 Nov 2021, reports the same ponding.
10. **BKE road shoulder, 2022-11-07.** Quote found verbatim (PUB, LTA feedback "at around 7.20pm"). Date from "Monday (Nov 7)" and confirmed by Mothership. Samples along the road flip between SUNGEI KADUT, MANDAI and WOODLANDS, with boundaries 5 to 14 m away. Corroboration: [Mothership](https://mothership.sg/2022/11/bke-flooded-drains-blocked/) reports the 100 m stretch but not Kranji.
11. **Stirling Rd, 2022-11-13.** Quote found verbatim ("Sunday (Nov 13)"). Year from metadata (2022-11-15) and a weekday check. Location is QUEENSTOWN, 443 m from TANGLIN. Tanjong Pagar Town Council says the flood happened and blames public drains along the service road. No PUB or news report found.
