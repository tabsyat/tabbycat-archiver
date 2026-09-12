# Tabbycat Static Archiver

A Streamlit app version of the Tabbycat Static Archive Colab notebook.
Strips everything public on a Tabbycat tournament site into flat static
HTML/CSS/JS and zips it for download — no login, no environment
variables required, since all the data it touches is public.

## Files

- `core.py` — every function from the notebook, ported 1:1 in logic.
  The only behavior change: `normalize_base_url()` now accepts the base
  URL with **or** without a trailing slash (the notebook only accepted
  it without one).
- `app.py` — the Streamlit UI. Takes the three inputs (base URL, slug,
  number of rounds), runs the archiver, streams a log, and offers a
  download button for the resulting zip.
- `requirements.txt`, `render.yaml` — deploy config for Render.

## Inputs

- **Tournament base URL** — e.g. `https://razm24.calicotab.com`, with or
  without a trailing slash.
- **Slug** — the tournament's slug exactly as in the URL, case sensitive.
- **Number of rounds** — defaults to 20. The archiver just tries rounds
  1 through this number and skips any that 404 (most tournaments don't
  have anywhere near 20 rounds — outrounds included). Raise it only if a
  tournament genuinely has more than 20 rounds.

## Running locally

```bash
pip install -r requirements.txt
playwright install --with-deps chromium
streamlit run app.py
```

## Deploying on Render

1. Push this folder to a GitHub repo.
2. In Render, "New +" → "Blueprint", point it at the repo — it will pick
   up `render.yaml` automatically.
3. **Important:** installing Chromium via Playwright needs more RAM than
   Render's free instance type provides, and the build step (installing
   Chromium + its OS deps) is slow on the free tier's build minutes too.
   The `render.yaml` here defaults to the `starter` plan for that reason.
   You can try the free tier, but expect it to be slow or to fail during
   `playwright install --with-deps chromium`.
4. Render's disks are ephemeral on redeploy/restart — this app already
   only writes to `/tmp`, and the download button appears immediately
   after each run finishes, so nothing needs to persist between runs.

## On hold — multi-tournament linking

Some Tabbycat subdomains host multiple tournaments (accessible via a
dropdown in the top-left, e.g. `sls.calicotab.com/slspd2024/`,
`/slspd2025/`, `/slspd2026/`). Right now this app only archives one
tournament per run, and separately-archived tournaments aren't linked
to each other in their exported static pages. A previous one-off
approach hand-edited the top-left dropdown links to point at sibling
Vercel deployments after the fact. This needs more thought before it's
worth automating — deliberately left out of this version.
