Fixtures reconstructed from published report rows, not original API payloads.

- 20261002_visible.json: HTML at parent of 57a748f0ed928c63a28d2a517fd41979061c0d48.
- 20261003_twse.json: HTML at 57a748f0ed928c63a28d2a517fd41979061c0d48 (all 9 rows TWSE).
- Source attribution: the nine codes in the TWSE-only 10/3 report are TWSE;
  remaining codes in the 10/2 report are TPEx. Conditions omitted by HTML
  are empty; duplicate announcement rows cannot be faithfully reconstructed.
- 10/2: published total 38 announcements, 33 visible unexpired stock rows.
- 10/3: 3 TPEx rows expired on 10/2 (36053, 36054, 7772); 21 TPEx
  unexpired rows plus 9 fresh TWSE rows = 30 protected stocks.

punish_source_state.json is a one-time recovery seed from these visible rows.
The first successful source fetch replaces the seed with full raw announcements.
Do not claim an exact 38-announcement replay: the HTML omits duplicate/expired
announcements, and the original API payloads were not persisted.

Regression suite: python -m unittest discover -s tests -v

Snapshots persist to the repository independently of normal Pages publication.
Failure/incomplete response retains unexpired source records and returns exit 2.
A loss of more than 50% of unexpired announcements requires a second healthy,
complete response with identical announcement keys (including duplicate counts).
Unconfirmed shrink retains old rows and accepts fresh rows; report delivery is
blocked. This consistency check cannot detect an upstream service returning the
same internally incomplete payload twice.
