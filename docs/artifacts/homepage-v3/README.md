# Homepage concept v3

A visual direction for the homepage redesign. It is a concept, not an implementation.

- Live design (private): https://claude.ai/artifact/9EEAYXPmu8PuP1w84QkRH4
- Strategy and copy: [`docs/Homepage-Strategy.md`](../../Homepage-Strategy.md)
- Boards: `Main.dc.html` (desktop page), `Phone.dc.html` (first screens at phone width), `canvas.json` (layout)

## Direction (2026-11-26, second pass)

Visual first: each section is a short headline and one moving picture. Visible words went from about 1,490 to about 640. Motion is CSS only, has a pause button, and stops for visitors who ask for reduced motion (they see the finished picture).

## The film

`film/tour.mp4` (26 s, 1920×1080, silent) with cover `film/poster.jpg`. Made with Remotion from real Build Studio screens captured at about 5 times size (2026-11-26) using practice data. Render with `npx remotion render src/index.jsx Tour out/tour.mp4 --scale=1.5`.

To rebuild: copy `shots/*.png` and `conxa-cloud/frontend/public/conxa-icon.png` into `film/public/`, then run `npm install` and `npm run render` in `film/`.

Remotion is free for individuals and very small teams; larger companies need a paid licence. Check its terms before relying on it.

## Images

The `.dc.html` files use images stored in the live design. Local copies:

| Image id in the file | Local copy | Cut from (`docs/testing/evidence/`) |
|---|---|---|
| `b07eadbd1c2e66e6d79951b03972ad2e` | `shots/shot-record.png` | `WF-011/s3.png` |
| `7342defa3d36a32fc2c3263c1233358a` | `shots/shot-question.png` | `LIVE-PASS-2026-11-22/05-q3.png` |
| `2b91b7a8a0e272d693275e5c4f42e02a` | `shots/shot-test.png` | `WF-011/retest/hp-testcard.png` |
| `db496f40086dc1e4c82016b7db37c16b` | `shots/shot-why-failed.png` | `WF-012/12.1.png` |
| (not used since the second pass) | `shots/shot-row-question.png` | `LIVE-PASS-2026-11-22/05-row-question.png` |
| `f1cba9cb24929483a5981882b44df4bf` | `film/tour.mp4` | rendered |
| `3492c3a8b580eceb87f3808e3cfb0c8c` | `film/poster.png` | rendered |
| `d0185d75deb1fc91a5456fe4ede1539b` | `conxa-cloud/frontend/public/conxa-icon.png` | brand logo |

The screenshots and the film show practice data from end-to-end testing. Replace them with fresh captures from a demo workspace before anything goes live (see the shot list in the strategy, section 8).

The hero animation, the repair and safety scenes, the app chains, the data-flow and publishing pictures are illustrations.


## Status (2026-11-26)
The first design link stopped opening, so the design was re-created at the link above with the sharper film and all later edits. Old comment threads did not carry over.
