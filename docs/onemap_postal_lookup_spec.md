# Postal code lookup (OneMap): spec only, NOT built (T6)

Status: **spec for review. Nothing is built.** Build only after the human checks below are done
and the user says so. Everything about OneMap's terms, endpoints and limits is **UNVERIFIED**:
it comes from memory, not from the OneMap site, and must be read there first.

**Goal:** a resident types a 6-digit postal code and the app selects their planning area.

**Inputs:** a 6-digit Singapore postal code (string; strip spaces; reject anything else before
any network call).

**Outputs:** the matched URA planning area (one of the 55 names in `URA_PLANNING_AREAS`) and
the point it came from. Preferred route: take the latitude/longitude OneMap returns and find
the area with the zone polygons we already hold (`FloodAlert.zone` does this for alerts), so the
area name comes from our own boundaries and not from OneMap's text. The address and the postal
code are not stored, logged or put in the URL.

**Token and secrets:** OneMap's API may need an access token (UNVERIFIED which endpoints).
If so, it is stored as the Databricks secret `floodsense/onemap_token` and, locally, in the
gitignored `.env` as `FLOODSENSE_ONEMAP_TOKEN`. Never in the repo, the logs or this file. The
token expires (UNVERIFIED how often), so the code must handle a 401 by failing safe (below), not
by retrying in a loop.

**Failure behaviour:** the postal-code box is optional and the area selector keeps working.
Invalid format, no match, a postal code outside Singapore's 55 areas, a timeout, a 401/429 or
any other error shows one plain line ("We couldn't find that postal code. Choose your area from
the list.") and leaves the current area unchanged. No guessing, no nearest-area fallback. Short
timeout (about 5 s), no retry storms, cache results in memory only for the session.

**Tests:** unit tests with the HTTP call patched (valid, invalid, no result, timeout, 401, a
point outside every area); an AppTest that the selector still works when the lookup fails.

**Licence and terms to check before building (human):**
1. OneMap's terms of use and API terms (Singapore Land Authority): is use in a public demo app
   allowed, is a token or registration needed, what are the rate limits?
2. Whether results may be cached or stored, and the required attribution wording.
3. Privacy: a postal code can identify a household, so send it only to OneMap, never log it.
4. How the token is issued and renewed, and that it will be stored as a secret.

**Effort:** about half a day once the terms are cleared.
