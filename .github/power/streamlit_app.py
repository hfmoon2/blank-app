import streamlit as st
import json
import os
from datetime import datetime
import pandas as pd
import hashlib
from pathlib import Path

# Password gate
if "auth_ok" not in st.session_state:
    st.session_state.auth_ok = False

app_pw = st.secrets.get("APP_PASSWORD")

if not app_pw:
    st.error("APP_PASSWORD not set. Add it to Streamlit secrets.")
    st.stop()

if not st.session_state.auth_ok:
    st.title("Power Annotation")
    pwd = st.text_input("Password", type="password")
    if pwd and pwd == app_pw:
        st.session_state.auth_ok = True
        st.rerun()
    elif pwd:
        st.error("Wrong password.")
    st.stop()


def make_case_id(c: dict, idx: int) -> str:
    # try scenario id + names + relationship
    meta = c.get("meta", {}) if isinstance(c.get("meta"), dict) else {}
    scenario_id = (meta.get("scenario", {}) or {}).get("id") if isinstance(meta.get("scenario"), dict) else None
    rel = meta.get("relationship_type", "Unknown")
    n1 = meta.get("name1", "")
    n2 = meta.get("name2", "")

    base = f"{scenario_id}|{rel}|{n1}|{n2}".strip()
    if base and base != "None|Unknown||":
        h = hashlib.md5(base.encode("utf-8")).hexdigest()[:10]
        return f"case_{h}"

    # fallback: stable-ish by index
    return f"idx_{idx}"


# Config
BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = str(BASE_DIR / "data" / "cases.jsonl")
TUTORIAL_PATH = str(BASE_DIR / "data" / "tutorial.json")
OUTPUT_DIR = "/tmp/outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)


POWER_SOURCE_TAGS = [
    "ROLE", "RESOURCE", "GATEKEEP", "STATUS", "INFO/EXPERTISE", "TIME/URGENCY",
    "NORM/REPUTATION", "EMOTIONAL LEVERAGE", "COERCION", "COALITION"
]

st.set_page_config(page_title="Power Annotation", layout="wide")


# Utilities
@st.cache_data
def load_cases(path: str):
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)

            # normalize raw
            raw = c.get("raw")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    # raw is a string but not valid json
                    raw = {"script": []}
            elif raw is None:
                raw = {"script": []}
            elif not isinstance(raw, dict):
                raw = {"script": []}

            # ensure script
            script = raw.get("script", [])
            if not isinstance(script, list):
                script = []
            raw["script"] = script
            c["raw"] = raw

            if not c.get("id"): 
                c["id"] = make_case_id(c, idx)

            cases.append(c)
    return cases

@st.cache_data
def load_tutorial(path: str):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def annot_path(annotator: str):
    ensure_output_dir()
    safe = "".join([c for c in annotator if c.isalnum() or c in ("_", "-")]).strip()
    if not safe:
        safe = "anonymous"
    return os.path.join(OUTPUT_DIR, f"{safe}.jsonl")

def load_existing_annotations(path: str):
    """Return dict: case_id -> annotation record"""
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            out[rec["case_id"]] = rec
    return out

def case_option_label(case, existing_dict):
    cid = case.get("id", "unknown")
    rel = (case.get("meta", {}) or {}).get("relationship_type", "Unknown")
    mark = "✅" if cid in existing_dict else "⬜"
    return f"{mark} {cid})"


def upsert_annotation(path: str, case_id: str, record: dict):
    existing = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    existing.append(json.loads(line))

    replaced = False
    for i, rec in enumerate(existing):
        if rec.get("case_id") == case_id:
            existing[i] = record
            replaced = True
            break
    if not replaced:
        existing.append(record)

    with open(path, "w", encoding="utf-8") as f:
        for rec in existing:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

def render_script(script):
    for t in script:
        speaker = t.get("speaker", "Unknown")
        text = t.get("text", "")
        st.markdown(f"**{speaker}:** {text}")

def get_case_display_name(case):
    cid = case.get("id", "unknown")
    rel = case.get("meta", {}).get("relationship_type", "Unknown")
    return f"{cid} ({rel})"


# State

cases = load_cases(DATA_PATH)
tutorial = load_tutorial(TUTORIAL_PATH)

if "mode" not in st.session_state:
    st.session_state.mode = "Tutorial"
if "case_idx" not in st.session_state:
    st.session_state.case_idx = 0
if "tutorial_step" not in st.session_state:
    st.session_state.tutorial_step = 0
if "annotator" not in st.session_state:
    st.session_state.annotator = "Harley"


# Sidebar

st.sidebar.title("🧭 Power Annotation")
st.sidebar.subheader("Annotator")
st.session_state.annotator = st.sidebar.selectbox(
    "Choose annotator",
    options=["Harley", "Annotator2", "Annotator3", "Other..."],
    index=0
)
if st.session_state.annotator == "Other...":
    st.session_state.annotator = st.sidebar.text_input("Type your name", value="Harley")

out_path = annot_path(st.session_state.annotator)
existing = load_existing_annotations(out_path)

st.sidebar.divider()

# Quick reference (collapsible)
with st.sidebar.expander("📘 Quick Reference", expanded=False):
    st.markdown("""
**Winner (Power Holder)**: who has greater effective power over the whole conversation.

**Evidence**:
- Controls outcomes (what/whether/when/how)
- Issues directives and gets compliance
- Gatekeeps access/resources
- Enforces norms/reputation
- Leverages expertise/information
""")
    st.markdown("**Power source tags** (pick all that apply):")
    st.write(", ".join(POWER_SOURCE_TAGS))

st.sidebar.divider()

# Progress
total = len(cases)
done = len(existing)
st.sidebar.subheader("Progress")
st.sidebar.metric("Annotated", done)
st.sidebar.metric("Total", total)
if total > 0:
    st.sidebar.progress(done / total)

st.sidebar.divider()

# Mode
st.sidebar.subheader("Mode")
st.session_state.mode = st.sidebar.radio(
    "Select mode",
    options=["Tutorial", "Annotate"],
    index=0 if st.session_state.mode == "Tutorial" else 1
)

# Jump to case (only in annotate mode)
if st.session_state.mode == "Annotate":
    st.sidebar.subheader("Jump to Case")

    show_only_unannotated = st.sidebar.checkbox("Show only unannotated", value=False)

    def label(i, case):
        cid = case.get("id", f"idx_{i}")
        rel = (case.get("meta", {}) or {}).get("relationship_type", "Unknown")
        mark = "✅" if cid in existing else "⬜"
        return f"{mark} [{i:05d}] {cid} ({rel})"

    full_labels = [label(i, c) for i, c in enumerate(cases)]

    if "jump_case" not in st.session_state:
        st.session_state.jump_case = full_labels[st.session_state.case_idx]

    def sync_jump_case():
        st.session_state.jump_case = full_labels[st.session_state.case_idx]

    def on_jump_change():
        chosen = st.session_state.jump_case
        st.session_state.case_idx = full_labels.index(chosen)

    def go_next():
        st.session_state.case_idx = min(st.session_state.case_idx + 1, len(cases) - 1)
        sync_jump_case()

    def go_prev():
        st.session_state.case_idx = max(st.session_state.case_idx - 1, 0)
        sync_jump_case()


    if show_only_unannotated:
        labels = [lab for i, lab in enumerate(full_labels) if cases[i].get("id") not in existing]
        if not labels:
            st.sidebar.info("All cases annotated.")
            labels = full_labels
    else:
        labels = full_labels

    st.sidebar.selectbox(
    "Case",
    options=labels,
    key="jump_case",
    on_change=on_jump_change
    )


st.sidebar.divider()

# Export
st.sidebar.subheader("Export Annotations")
if st.sidebar.button("⬇️ Download JSONL"):
    if os.path.exists(out_path):
        with open(out_path, "rb") as f:
            st.sidebar.download_button(
                label="Click to download",
                data=f,
                file_name=os.path.basename(out_path),
                mime="application/jsonl"
            )
    else:
        st.sidebar.info("No annotations yet.")


# Main Area

if st.session_state.mode == "Tutorial":
    st.markdown("## 📚 Interactive Tutorial")
    st.caption("Learn by exploring a few examples before annotation.")

    if not tutorial:
        st.info("No tutorial.json found. Create data/tutorial.json to enable tutorial steps.")
        st.markdown("### Minimal tutorial.json format")
        st.code("""
[
  {
    "title": "Example 1: Parent–Child",
    "instruction": "Look for directives and compliance.",
    "case": { ... same structure as cases ... },
    "suggested_label": {
      "winner": "Hang",
      "power_sources": ["ROLE", "TIME/URGENCY"]
    }
  }
]
""".strip())
    else:
        step = st.session_state.tutorial_step
        step = max(0, min(step, len(tutorial)-1))
        st.session_state.tutorial_step = step

        item = tutorial[step]
        st.markdown(f"### Step {step+1} of {len(tutorial)} — {item.get('title','')}")
        st.write(item.get("instruction",""))

        case = item["case"]
        meta = case.get("meta", {})
        col1, col2 = st.columns([2, 1], gap="large")

        with col1:
            st.markdown("#### Conversation")
            script = case.get("raw", {}).get("script", [])
            render_script(script)

        with col2:
            st.markdown("#### Suggested answer (for learning)")
            sug = item.get("suggested_label", {})
            st.write("**Winner:**", sug.get("winner", ""))
            st.write("**Power sources:**", ", ".join(sug.get("power_sources", [])))

        c1, c2, c3 = st.columns([1, 1, 3])
        with c1:
            if st.button("⬅️ Back", disabled=(step == 0)):
                st.session_state.tutorial_step -= 1
                st.rerun()
        with c2:
            if st.button("Next ➡️", disabled=(step == len(tutorial)-1)):
                st.session_state.tutorial_step += 1
                st.rerun()

else:
    # Annotate mode
    case = cases[st.session_state.case_idx]
    case_id = case.get("id", f"idx_{st.session_state.case_idx}")
    meta = case.get("meta", {})
    name1 = meta.get("name1", "Speaker 1")
    name2 = meta.get("name2", "Speaker 2")

    st.markdown("## ✍️ Annotation")
    st.caption(f"Case: {case_id} | Relationship: {meta.get('relationship_type','Unknown')}")

    col_left, col_right = st.columns([2.2, 1], gap="large")

    with col_left:
        st.markdown("### Conversation")
        script = case.get("raw", {}).get("script", [])
        render_script(script)

    with col_right:
        st.markdown("### Label this conversation")

        # Pre-fill if already annotated
        prev = existing.get(case_id)

        winner_options = ["Tie", name1, name2]
        default_winner = "Tie"
        default_tags = []

        if prev:
            default_winner = prev.get("winner", "Tie")
            default_tags = prev.get("power_sources", [])

        winner_key = f"winner_{case_id}"
        winner = st.radio("Winner", options=winner_options, index=winner_options.index(default_winner) if default_winner in winner_options else 0, key=winner_key)
        tags_key = f"tags_{case_id}"
        tags = st.multiselect("Power source tags",options=POWER_SOURCE_TAGS,default=[t for t in default_tags if t in POWER_SOURCE_TAGS],key=tags_key)

        if st.button("✅ Save annotation", type="primary"):
            record = {
                "case_id": case_id,
                "annotator": st.session_state.annotator,
                "timestamp": datetime.utcnow().isoformat(),
                "winner": winner,
                "power_sources": tags,
                "meta_snapshot": {
                    "relationship_type": meta.get("relationship_type"),
                    "role1": meta.get("role1"),
                    "role2": meta.get("role2"),
                    "name1": name1,
                    "name2": name2,
                }
            }
            upsert_annotation(out_path, case_id, record)
            st.success("Saved!")

            # refresh progress cache
            st.rerun()

        st.divider()
        st.markdown("### Navigation")
        c1, c2 = st.columns(2)
        with c1:
            st.button("⬅️ Previous", on_click=go_prev, disabled=(st.session_state.case_idx == 0), key="nav_prev")
        with c2:
            st.button("Next ➡️", on_click=go_next, disabled=(st.session_state.case_idx == len(cases)-1), key="nav_next")



    # Small footer progress
    st.markdown("---")
    st.caption(f"Annotated: {done} / {total}")

