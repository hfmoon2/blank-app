import streamlit as st
import json
import os
from datetime import datetime
import pandas as pd
import hashlib
from pathlib import Path
from supabase import create_client

st.set_page_config(page_title="Power Annotation", layout="wide")

# Password gate
if "auth_ok" not in st.session_state:
    st.session_state.auth_ok = False

app_pw = st.secrets.get("APP_PASSWORD")

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
def load_tutorial(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    if isinstance(obj, dict) and isinstance(obj.get("steps"), list):
        return obj["steps"]
    raise ValueError("tutorial.json must be a JSON object with a top-level 'steps' list.")


def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

@st.cache_resource
def get_supabase():
    url = st.secrets.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY")
    if not url or not key:
        st.error("Missing SUPABASE_URL / SUPABASE_KEY in secrets.")
        st.stop()
    return create_client(url, key)

def load_existing_annotations(annotator: str):
    """Return dict: case_id -> record(payload) for this annotator"""
    sb = get_supabase()
    res = (
        sb.table("annotations")
        .select("case_id,payload")
        .eq("annotator", annotator)
        .execute()
    )
    rows = res.data or []
    return {r["case_id"]: r["payload"] for r in rows}

def upsert_annotation(case_id: str, annotator: str, record: dict):
    sb = get_supabase()
    row = {
        "case_id": case_id,
        "annotator": annotator,
        "payload": record,
        "updated_at": datetime.utcnow().isoformat(),
    }
    sb.table("annotations").upsert(row).execute()


def case_option_label(case, existing_dict):
    cid = case.get("id", "unknown")
    rel = (case.get("meta", {}) or {}).get("relationship_type", "Unknown")
    mark = "✅" if cid in existing_dict else "⬜"
    return f"{mark} {cid})"


def render_script(script):
    for t in script:
        speaker = t.get("speaker", "Unknown")
        text = t.get("text", "")
        st.markdown(f"**{speaker}:** {text}")

def get_case_display_name(case):
    cid = case.get("id", "unknown")
    rel = case.get("meta", {}).get("relationship_type", "Unknown")
    return f"{cid} ({rel})"

def render_conversation(conv):
    for turn in conv:
        speaker = turn.get("speaker", "Speaker")
        text = turn.get("text", "")
        st.markdown(f"**{speaker}:** {text}")

def render_tag_reference(step):
    st.write(step.get("title", "Power Source Tags"))
    groups = step.get("groups", [])
    for g in groups:
        st.subheader(g.get("group", ""))
        for t in g.get("tags", []):
            with st.expander(t["tag"]):
                st.markdown(f"**Definition:** {t.get('definition','')}")
                cues = t.get("cues", [])
                if cues:
                    st.markdown("**Common cues:**")
                    st.markdown("\n".join([f"- {c}" for c in cues]))
                ex = t.get("mini_example")
                if ex:
                    st.markdown(f"**Mini example:** {ex}")

def render_content(step):
    st.header(step.get("title", ""))
    if "bullets" in step:
        st.markdown("\n".join([f"- {b}" for b in step["bullets"]]))
    for p in step.get("paragraphs", []):
        st.write(p)
    if "two_column" in step:
        tc = step["two_column"]
        c1, c2 = st.columns(2)
        with c1:
            st.subheader(tc.get("left_title",""))
            st.markdown("\n".join([f"- {b}" for b in tc.get("left_bullets",[])]))
        with c2:
            st.subheader(tc.get("right_title",""))
            st.markdown("\n".join([f"- {b}" for b in tc.get("right_bullets",[])]))
    if "checklist" in step:
        st.subheader(step.get("checklist_title","Checklist"))
        st.markdown("\n".join([f"- {b}" for b in step.get("checklist",[])]))
    if "tie_guidance" in step:
        st.subheader("Tie guidance")
        st.markdown("\n".join([f"- {b}" for b in step.get("tie_guidance",[])]))
    if step.get("callout"):
        st.info(step["callout"])

def render_walkthrough(step):
    st.header(step.get("title","Walkthrough"))
    st.write(step.get("prompt",""))
    render_conversation(step.get("conversation", []))

    gold = step.get("gold", {})
    st.subheader("Suggested answer")
    st.markdown(f"- **Winner:** {gold.get('winner','')}")
    st.markdown(f"- **Tags:** {', '.join(gold.get('tags', [])) or '(none)'}")
    if step.get("rationale"):
        st.markdown(f"**Why:** {step['rationale']}")

def render_practice(step, all_tags):
    st.header(step.get("title","Practice"))
    st.write(step.get("prompt",""))
    render_conversation(step.get("conversation", []))

    sid = step.get("id", "practice")
    form_key = f"practice_form_{sid}"

    with st.form(key=form_key):
        winner = st.radio(
            "Winner",
            options=["A", "B", "Tie"],
            horizontal=True,
            key=f"practice_winner_{sid}",
        )
        tags = st.multiselect(
            "Power source tags (multi-select)",
            options=all_tags,
            key=f"practice_tags_{sid}",
        )
        submitted = st.form_submit_button("Check answer")

    if submitted:
        gold = step.get("gold", {})
        gold_winner = gold.get("winner", "")
        gold_tags = set(gold.get("tags", []))

        # Map A/B labels to actual gold if gold uses speakers (A/B/Name)
        user_winner = winner
        # If gold is "Tie" keep as-is; else allow both formats
        if gold_winner in ["A", "B", "Tie"]:
            correct_winner = gold_winner
        else:
            # If gold uses a speaker name (e.g., "PopularKid"), treat A/B as unknown here
            correct_winner = gold_winner

        st.subheader("Feedback")
        st.markdown(f"**Suggested winner:** {gold_winner}")
        st.markdown(f"**Suggested tags:** {', '.join(gold_tags) or '(none)'}")
        if step.get("rationale"):
            st.markdown(f"**Why:** {step['rationale']}")

        # lightweight correctness signal (only if gold uses A/B/Tie)
        if correct_winner in ["A", "B", "Tie"]:
            if user_winner == correct_winner:
                st.success("Winner matches the suggested answer.")
            else:
                st.warning("Winner differs from the suggested answer.")

        user_tags = set(tags)
        if user_tags == gold_tags:
            st.success("Tags match the suggested answer.")
        else:
            st.info("Tags do not exactly match (that can be OK). Use the rationale to align your reasoning.")


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
    options=["Harley", "Stella", "Other..."],
    index=0
)
if st.session_state.annotator == "Other...":
    st.session_state.annotator = st.sidebar.text_input("Type your name", value="")

existing = load_existing_annotations(st.session_state.annotator)

st.sidebar.divider()

# Quick reference (collapsible)
with st.sidebar.expander("📘 Quick Reference", expanded=False):
    st.markdown("""
**Evidence**:
- Controls outcomes (what/whether/when/how)
- Issues directives and gets compliance
- Gatekeeps access/resources
- Enforces norms/reputation
- Leverages expertise/information

**Resource Tags**:
- ROLE: formal/relational authority (parent, elder, workplace superior).
- RESOURCE: money, tools, time, transportation, access to services.
- GATEKEEP: permission, invitations, introductions, approvals.
- STATUS: popularity, prestige, social standing in a group.
- INFO/EXPERTISE: evidence or knowledge advantage; technical authority.
- TIME/URGENCY: deadline pressure enabling commands or immediate compliance.
- NORM/REPUTATION: shame, public image, "what people will think".
- EMOTIONAL LEVERAGE: guilt, obligation, fear of abandonment.
- COERCION: threats, punishment, intimidation.
- COALITION: third-party support ("everyone agrees"), mobilizing others.                                

""")

st.sidebar.divider()

# Progress
cases = load_cases(DATA_PATH)
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
    rows = list(load_existing_annotations(st.session_state.annotator).values())
    if not rows:
        st.sidebar.info("No annotations yet.")
    else:
        jsonl = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
        st.sidebar.download_button(
            label="Click to download",
            data=jsonl.encode("utf-8"),
            file_name=f"{st.session_state.annotator}.jsonl",
            mime="application/jsonl",
        )

if st.sidebar.button("⬇️ Download CSV"):
    rows = list(load_existing_annotations(st.session_state.annotator).values())
    if not rows:
        st.sidebar.info("No annotations yet.")
    else:
        df = pd.json_normalize(rows)
        st.sidebar.download_button(
            label="Click to download",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name=f"{st.session_state.annotator}.csv",
            mime="text/csv",
        )


# Main Area

if st.session_state.mode == "Tutorial":
    st.markdown("## 📚 Interactive Tutorial")
    st.caption("Learn by exploring a few examples before annotation.")

    try:
        steps = load_tutorial(TUTORIAL_PATH)
    except Exception as e:
        st.error(f"Failed to load tutorial.json: {e}")
        st.stop()

    if not steps:
        st.info("No steps found in tutorial.json.")
        st.stop()

    if "tutorial_step" not in st.session_state:
        st.session_state.tutorial_step = 0

    step = max(0, min(st.session_state.tutorial_step, len(steps) - 1))
    st.session_state.tutorial_step = step
    item = steps[step]

    t = item.get("type", "content")

    if t == "content":
        render_content(item)

    elif t == "tag_reference":
        render_tag_reference(item)

    elif t == "walkthrough":
        render_walkthrough(item)

    elif t == "practice":
        all_tags = POWER_SOURCE_TAGS
        render_practice(item, all_tags)

    else:
        st.warning(f"Unknown tutorial step type: {t}")
        st.json(item)

    c1, c2, c3 = st.columns([1, 1, 3])
    with c1:
        if st.button("⬅️ Back", key=f"tut_back_{step}", disabled=(step == 0)):
            st.session_state.tutorial_step = step - 1
            st.rerun()
    with c2:
        if st.button("Next ➡️", key=f"tut_next_{step}", disabled=(step == len(steps) - 1)):
            st.session_state.tutorial_step = step + 1
            st.rerun()
    with c3:
        st.caption(f"Progress: {step+1}/{len(steps)}")

    if step == len(steps) - 1:
        if st.button("Proceed to Annotation", key="tut_to_annotate"):
            st.session_state.mode = "Annotate"
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
        st.markdown("**Power source tags**")

        # --- checkbox grid state key (per case) ---
        grid_key = f"tags_grid_{case_id}"

        # initialize per-case selected tags once
        if grid_key not in st.session_state:
            st.session_state[grid_key] = [
                t for t in default_tags if t in POWER_SOURCE_TAGS]

        selected = set(st.session_state[grid_key])

        # layout: 2 columns (like your screenshot); you can change to 3/4 if you want
        n_cols = 2
        cols = st.columns(n_cols)

        for i, tag in enumerate(POWER_SOURCE_TAGS):
            col = cols[i % n_cols]
            cb_key = f"{grid_key}_{tag}"

            # initialize checkbox state once
            if cb_key not in st.session_state:
                st.session_state[cb_key] = (tag in selected)

            checked = col.checkbox(tag, key=cb_key)

        # collect after rendering
        new_selected = []
        for tag in POWER_SOURCE_TAGS:
            if st.session_state.get(f"{grid_key}_{tag}", False):
                new_selected.append(tag)

        tags = new_selected
        st.session_state[grid_key] = tags


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
            upsert_annotation(case_id, st.session_state.annotator, record)
            st.success("Saved!")
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

