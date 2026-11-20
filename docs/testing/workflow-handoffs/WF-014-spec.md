# WF-014 spec — Files at scale (source: 01 §WF-14, lines 414-432)

## WF-14 — Files at scale — 🔄 partly passed 2026-11-17 (P-34…P-36): 14.1 (local stand-in), 14.2 (20 files), 14.4–14.7 pass; 14.2 pilot pre-check, 14.3, 14.8 not run; real public hosts blocked

**Ask the owner before starting** (uploads go to public file hosts). Generated dummy files only.
Already passed: interleaved one-at-a-time download→upload identity at n=5 (P-10).
**Sites:** S15, S16, S1, S3; upload targets with `<input type="file" multiple>`: filebin.net,
`blueimp.github.io/jQuery-File-Upload`, `demo.automationtesting.in/FileUpload.html`.

| # | Test | Must happen | If wrong |
|---|---|---|---|
| 14.1 | **All at once:** upload 20 files to a bin → "Download files" → one ZIP | ZIP extracted at download; 20 files | S1 |
| 14.2 | Re-upload them to a real multi-file input. Pre-check: record picking 3 files, replay with a folder of 3; then a folder of 20 | Every file arrives once (count on the site); folder expanded in name order | S1 |
| 14.3 | Single-file input given a folder | Immediate plain refusal; no recovery; zero tokens | S1 |
| 14.4 | **Cross-run:** run A with `report-01…20`, run B with `invoice-01…20` | Each run gets a fresh `~/.conxa/downloads/{runId}/`; run B uploads only its own files | S0 |
| 14.5 | Retention: run A's folder after run B starts; after `CONXA_RUN_RETENTION_DAYS` | Still there inside the window; swept after | S2 |
| 14.6 | 5 downloads, 3 with the same suggested name | All 5 kept, or a plain failure. A silent overwrite with a "success" is S0 (W-3) | S0 |
| 14.7 | Link whose new tab downloads at once | File saved | S1 |
| 14.8 | 0-byte file; 200 MB file; name with spaces and `é`; a download that never finishes | Each defined; the stuck one ends with a plain timeout | S1 |

**Failures go to:** EXEC-15 (all-at-once), W-3 (collision, new TODO at top severity), W-7 (retention).
