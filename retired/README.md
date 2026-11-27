# Retired classic pages

The original standalone pages (Equity Atlas, Flows, Balances, Equity signals,
Methodology). They are kept here for reference but are no longer linked or
deployed (`retired/` is in `.vercelignore`). Everything they showed now lives
in the terminal widgets, and the current methodology is the Methodology
widget (`terminal/methodology.js`).

To view one locally, serve the repo root and open it; each page has
`<base href="../">` so its data files still resolve:

    python3 -m http.server 8000
    open http://localhost:8000/retired/dashboard.html
