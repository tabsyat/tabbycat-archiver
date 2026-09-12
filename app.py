import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path

import streamlit as st

import core

st.set_page_config(page_title="Tabbycat Static Archiver", page_icon="🗂️", layout="centered")

st.title("🗂️ Tabbycat Static Archiver")
st.caption(
    "Strips everything public on a Tabbycat tournament site (results, tab pages, "
    "participants, motions, etc.) into flat static HTML and zips it up — no login, "
    "no env vars, everything here is public data."
)

with st.form("archive_form"):
    base_url_input = st.text_input(
        "Tournament base URL",
        placeholder="https://razm24.calicotab.com",
        help="With or without a trailing slash — either works. This is the site's "
             "domain, not including the tournament slug.",
    )
    slug = st.text_input(
        "Slug",
        placeholder="razm2",
        help="The tournament's slug, exactly as it appears in the URL (case sensitive).",
    )
    num_rounds = st.number_input(
        "Number of rounds to check",
        min_value=1,
        max_value=200,
        value=20,
        step=1,
        help=(
            "The archiver tries to fetch a results page for every round from 1 up to "
            "this number. 20 is the default because it comfortably covers every "
            "tournament we've seen (prelims + outrounds). Rounds beyond what a "
            "tournament actually has just fail gracefully and are skipped — so it's "
            "safe to leave this higher than needed. Only raise it if a tournament "
            "genuinely has more than 20 rounds."
        ),
    )
    submitted = st.form_submit_button("Start archiving", type="primary")

log_box = st.empty()
status_box = st.empty()

if "log_lines" not in st.session_state:
    st.session_state.log_lines = []

if submitted:
    if not base_url_input.strip() or not slug.strip():
        st.error("Please fill in both the tournament base URL and the slug.")
    else:
        st.session_state.log_lines = []

        def log(msg: str):
            st.session_state.log_lines.append(msg)
            log_box.code("\n".join(st.session_state.log_lines[-400:]), language=None)

        base_url = core.normalize_base_url(base_url_input)
        run_id = uuid.uuid4().hex[:8]
        work_dir = Path(tempfile.gettempdir()) / f"tabby_export_{run_id}"
        zip_base_path = str(Path(tempfile.gettempdir()) / f"tabby_export_{run_id}")

        status_box.info("Archiving in progress — this can take a few minutes for larger tournaments...")

        try:
            zip_path = asyncio.run(
                core.run_archive_async(
                    base_url=base_url,
                    slug=slug,
                    num_rounds=int(num_rounds),
                    out_dir=str(work_dir),
                    zip_base_path=zip_base_path,
                    log=log,
                )
            )
            status_box.success("Done! Your archive is ready below.")
            with open(zip_path, "rb") as f:
                st.download_button(
                    label="⬇️ Download archive (.zip)",
                    data=f.read(),
                    file_name=f"{slug}_archive.zip",
                    mime="application/zip",
                )
        except Exception as e:
            status_box.error(f"Archiving failed: {e}")
        finally:
            # Clean up the working directory (the zip file itself is kept until
            # the process restarts / disk is reclaimed by the host).
            shutil.rmtree(work_dir, ignore_errors=True)

st.divider()
st.caption(
    "Note: on hosts with ephemeral disks (like Render's free tier), generated zip "
    "files don't survive a restart — download it right after it's generated."
)
