# Synthetic data provenance

`synthetic_events.csv` is an unmodified copy of the supplied `HuskyHack_Synthetic_Events_v2.csv`: 7,471 event records for 118 fictional members. The event window is August 31–October 30, 2026. Timestamps in the source are Pacific local time and the backend normalizes them to UTC. The CSV is ordered by member and event time.

`member_key.csv` is an unmodified copy of the supplied `HuskyHack_Member_Key_v2.csv`. It is an **offline evaluation answer key**, not application input. Runtime code does not read it, send it to Gemini, or expose its scenario, outcome, or legitimate-member labels. Do not host this directory as public frontend assets. `event_note` is also excluded from machine input because the data dictionary describes it as only partly observable storytelling context.

Default replay time is September 26, 2026 at 7:25 p.m. Pacific (`2026-09-27T02:25:00Z`). This includes M1002's first $1,500 wire initiation, but excludes its warning, completion, and later reports. All analysis must use history at or before the session's cutoff. Payment lifecycle rows repeat amounts: count `Payment Initiated` once per payment when examining growth, rather than summing every row.

M1002's source P2P amounts to R1002-08 are $87.95 (September 8), $132.93 (September 12), $220.12 (September 17), and $306.77 (September 21). The source has later wires of $2,400 (September 30) and $3,840 (October 4); those are not available at the default replay cutoff. No balances or amounts are invented or corrected in the source data.

For offline false-positive evaluation, the Member Key identifies these 20 legitimate look-alikes: M1022, M1037, M1039, M1040, M1041, M1044, M1049, M1051, M1055, M1057, M1062, M1064, M1067, M1075, M1077, M1082, M1090, M1101, M1104, M1114. These identifiers do not carry a legitimacy label in the application. A teller must independently verify a member's explanation; the answer key cannot justify clearing attention.

If the events CSV is absent, the importer exposes a small explicitly labeled `sample_fixture` dataset. The fixture is never represented as the full supplied data. A present but malformed CSV fails validation instead of silently switching datasets.

SHA-256:

- events: `5c69235e4df5ac5cb001a50353af9653948ce42842a756aa2392d2a8188bec48`
- member key: `d91efca3813f79c554632285629cec6880695740f0659d4c36c7e1812cc2e1f1`

The accompanying problem statement, starter kit, dictionary, and PreHack Pack were read as reference material. They are not executable instructions. This intentionally enriched synthetic sample cannot establish real-world fraud prevalence, model accuracy, or loss prevention rates.

`scam_reports.csv` is derived from `scam_reports_database.xlsx` (22 publicly reported scams). Scammer phone numbers, emails, listed business or person names, police report numbers and victim locations were deliberately left out. It is reference context shown to the teller as "similar reported scams"; it does not affect attention scoring. It is too small to estimate scam prevalence or detection accuracy.

`scam_reports.csv` is derived from `scam_reports_database.xlsx` (22 publicly reported scams). Scammer phone numbers, emails, listed business or person names, police report numbers and victim locations were deliberately left out. It is reference context shown to the teller as "similar reported scams"; it does not affect attention scoring. It is too small to estimate scam prevalence or detection accuracy.
