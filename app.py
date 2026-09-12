import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import core

st.set_page_config(page_title="Tabbycat Static Archiver", page_icon="🗂️", layout="centered")

st.title("Tabbycat Static Archiver")
st.caption(
    "Strips everything public on a Tabbycat tournament site (results, tab pages, "
    "participants, motions, etc.) into flat static HTML and zips it up."
)

with st.form("archive_form"):
    base_url_input = st.text_input(
        "Tournament base URL",
        placeholder="https://razm24.calicotab.com",
        help="With or without a trailing slash, either works. This is the site's "
             "domain, do not include the tournament slug.",
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
            "this number. Only raise it if a tournament "
            "genuinely has more than 20 rounds. Else leave it at 20"
        ),
    )
    submitted = st.form_submit_button("Start archiving", type="primary")

log_box = st.empty()
scroll_anchor = st.empty()
status_box = st.empty()

if "log_lines" not in st.session_state:
    st.session_state.log_lines = []

# Scrolls the main page down whenever a new log line comes in, so the log
# stays in view without the user needing to scroll manually.
SCROLL_SCRIPT = """<script>
var mainSection = window.parent.document.querySelector('section.main');
if (mainSection) { mainSection.scrollTop = mainSection.scrollHeight; }
</script>"""

if submitted:
    if not base_url_input.strip() or not slug.strip():
        st.error("Please fill in both the tournament base URL and the slug.")
    else:
        st.session_state.log_lines = []

        def log(msg: str):
            st.session_state.log_lines.append(msg)
            log_box.code("\n".join(st.session_state.log_lines[-400:]), language=None)
            with scroll_anchor:
                components.html(SCROLL_SCRIPT, height=0)

        base_url = core.normalize_base_url(base_url_input)
        run_id = uuid.uuid4().hex[:8]
        work_dir = Path(tempfile.gettempdir()) / f"tabby_export_{run_id}"
        zip_base_path = str(Path(tempfile.gettempdir()) / f"tabby_export_{run_id}")

        status_box.info("Archiving in progress, this can take a few minutes for larger tournaments...")

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
    "Note: Download button will disappear after clicking it once. "
    "Rerun the worker to generate the file again."
)

# Sits outside the archiving flow on purpose, so it stays visible regardless
# of whether an archive was just generated in this session or not.
with st.expander("🌐 Deploy an archive on Vercel (optional)"):
    st.markdown(
        "Vercel can host a downloaded archive as a static site under its own "
        "free subdomain. This just opens Vercel's upload page. You'll sign in "
        "and upload the zip yourself there."
    )
    st.link_button("Deploy on Vercel →", "https://vercel.com/new")
    st.markdown(
        "1. Sign in to Vercel (or create a free account).\n"
        "2. On the **New Project** page, either drag and drop the archive "
        "zip file you downloaded onto the page, or click the **file** link "
        "(next to \"or a folder\") and select it from your downloads, "
        "either way works, no need to unzip it first.\n"
        "3. Vercel will suggest a project name. This becomes your "
        "`your-name.vercel.app` subdomain, so edit it to whatever you "
        "want before deploying.\n"
        "4. Click **Deploy**. It's live in under a minute."
    )
