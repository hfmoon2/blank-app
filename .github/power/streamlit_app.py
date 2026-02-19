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
def load_cases(path: str, mtime: float):
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

def upsert_annotation(case_id: str, annotator: str, payload: dict):
    """Insert/update one annotation in Supabase."""
    sb = get_supabase()
    row = {
        "case_id": case_id,
        "annotator": annotator,
        "payload": payload,
        "updated_at": datetime.utcnow().isoformat(),
    }
    sb.table("annotations").upsert(row, on_conflict="case_id,annotator").execute()

def do_save():
    winner_reason = st.session_state.get(f"winner_reason_{case_id}", "")
    tags_reason = st.session_state.get(f"tags_reason_{case_id}", "")
    winner = st.session_state.get(f"winner_{case_id}", "Tie")

    record = {
        "case_id": case_id,
        "annotator": st.session_state.annotator,
        "timestamp": datetime.utcnow().isoformat(),
        "winner": winner,
        "power_sources_s1": tags_s1,
        "power_sources_s2": tags_s2,
        "winner_reason": winner_reason,
        "tags_reason": tags_reason,
        "meta_snapshot": {
            "relationship_type": meta.get("relationship_type"),
            "role1": meta.get("role1"),
            "role2": meta.get("role2"),
            "name1": name1,
            "name2": name2,
        }
    }
    upsert_annotation(case_id, st.session_state.annotator, record)

def go_next():
    st.session_state.case_idx = min(st.session_state.case_idx + 1, len(cases) - 1)
    st.session_state._sync_jump = True

def go_prev():
    st.session_state.case_idx = max(st.session_state.case_idx - 1, 0)
    st.session_state._sync_jump = True

def save_and_next():
    do_save()
    go_next()
    st.rerun()


def case_option_label(case, existing_dict):
    cid = case.get("id", "unknown")
    rel = (case.get("meta", {}) or {}).get("relationship_type", "Unknown")
    mark = "✅" if cid in existing_dict else "⬜"
    return f"{mark} {cid} ({rel})"


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

def render_tag_checkboxes(title, tags, default_selected, key_prefix, n_cols=1):
    st.markdown(f"**{title}**")
    selected = []

    cols = st.columns(n_cols) if n_cols and n_cols > 1 else None

    for i, t in enumerate(tags):
        k = f"{key_prefix}_{t}"
        if k not in st.session_state:
            st.session_state[k] = (t in (default_selected or []))

        if cols is None:
            v = st.checkbox(t, key=k)
        else:
            with cols[i % n_cols]:
                v = st.checkbox(t, key=k)

        if v:
            selected.append(t)

    return selected

def get_selected_tags_from_state(tags, key_prefix):
    out = []
    for t in tags:
        if st.session_state.get(f"{key_prefix}_{t}", False):
            out.append(t)
    return out

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

st.sidebar.divider()

# Quick reference (collapsible)
with st.sidebar.expander("📘 Quick Reference", expanded=False):
    st.markdown("""
### Winner (Power Holder) — How to decide

**Core idea:** Power = **influence/control over the outcome** of the **initial conflict**.

**Steps (surface-level, outcome-focused):**
1) Identify **S1 & S2 intent** → their **initial expected outcome** (what each wants to happen).
2) Look at the **final outcome** of that **same initial conflict** (not later twists).
3) Decide winner:
   - **Whoever compromises loses**
   - **Whoever’s initial expected outcome changes loses**
   - **No clear change / not sure → Tie**

**Important constraints:**
- Focus on **ONE conflict only: the INITIAL one**.
- If there are twists / multiple requests mid-conversation: **ignore later conflicts** (treat as bad example).
- **Differentiate control vs threat**:
  - *Control* = actually steering what happens / what the other accepts.
  - *Threat* (esp. weak/empty threats) ≠ automatically control unless it changes the outcome.

---
**Evidence**:
- Controls outcomes (what/whether/when/how)
- Issues directives and gets compliance
- Gatekeeps access/resources
- Enforces norms/reputation
- Leverages expertise/information

**Resource Tags**:
- ROLE: Power comes from a recognized role hierarchy that legitimizes directing or evaluating the other.
- RESOURCE: Power comes from controlling money, goods, access to items, or practical support.
- GATEKEEP: Power comes from controlling whether the other can access something (an event, opportunity, information, approval, membership, decision channel).
- STATUS: Power comes from social rank, popularity, reputation, or being admired/feared (even without formal authority).
- INFO/EXPERTISE: Power comes from having specialized knowledge, credentials, or information the other lacks, which shapes decisions.
- TIME/URGENCY: Power comes from imposing urgency, deadlines, or controlling when action must happen.
- NORM/REPUTATION: Power comes from enforcing “what is proper/acceptable,” invoking duty, etiquette, family values, or “how people should behave.”
- EMOTIONAL LEVERAGE: Power comes from manipulating emotions (guilt, fear of disappointing, affection withdrawal, emotional dependency) to influence the other. Tag it when a speaker uses **emotionally loaded pressure** to influence the other’s stance/outcome,
especially via **negative emotional framing** (e.g., guilt/shame/disappointment/fear of letting someone down),
even if not explicit crying/pleading.
- COERCION: Power comes from explicit or implicit threats, punishment, or consequences imposed by the speaker.
- COALITION: Power comes from aligning with others (family, friends, rules, institutions) to increase pressure or legitimacy.                                

""")

st.sidebar.divider()

# Progress
cases = load_cases(DATA_PATH, os.path.getmtime(DATA_PATH))
current_case_ids = {c.get("id", f"idx_{i}") for i, c in enumerate(cases)}

existing_all = load_existing_annotations(st.session_state.annotator)
existing = {cid: rec for cid, rec in existing_all.items() if cid in current_case_ids}

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

# ---- Jump to case (Annotate mode) ----
if st.session_state.mode == "Annotate":
    st.sidebar.subheader("Jump to Case")
    show_only_unannotated = st.sidebar.checkbox("Show only unannotated", value=False)

    def make_label(i, case):
        cid = case.get("id", f"idx_{i}")
        rel = (case.get("meta", {}) or {}).get("relationship_type", "Unknown")
        mark = "✅" if cid in existing else "⬜"
        return f"{mark} [{i:05d}] {cid} ({rel})"

    full_labels = [make_label(i, c) for i, c in enumerate(cases)]
    label_to_idx = {lab: i for i, lab in enumerate(full_labels)}

    if show_only_unannotated:
        labels = [lab for i, lab in enumerate(full_labels) if cases[i].get("id") not in existing]
        if not labels:
            st.sidebar.info("All cases annotated.")
            labels = full_labels
    else:
        labels = full_labels

    current_label = full_labels[st.session_state.case_idx]
    if current_label not in labels:
        current_label = labels[0]

    if "_sync_jump" not in st.session_state:
        st.session_state._sync_jump = False

    if "jump_case" not in st.session_state:
        st.session_state.jump_case = current_label
    if st.session_state._sync_jump:
        st.session_state.jump_case = current_label
        st.session_state._sync_jump = False

    chosen = st.sidebar.selectbox(
        "Case",
        options=labels,
        key="jump_case",
    )

    new_idx = label_to_idx.get(chosen, st.session_state.case_idx)
    if new_idx != st.session_state.case_idx:
        st.session_state.case_idx = new_idx
        st.rerun()

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

    tags_s1, tags_s2 = [], []
    winner_reason, tags_reason = "", ""

    col_left, col_right = st.columns([2.2, 1], gap="large")
    prev = existing.get(case_id)

    with col_left:
        st.markdown("### Conversation")
        script = case.get("raw", {}).get("script", [])
        render_script(script)

        prev = existing.get(case_id)

        st.markdown("### Winner")

        # Pre-fill if already annotated
        prev = existing.get(case_id)

        # winner
        winner_options = ["Tie", name1, name2]
        default_winner = "Tie"
        if prev:
            default_winner = prev.get("winner", "Tie")

        winner_key = f"winner_{case_id}"
        if winner_key not in st.session_state:
            st.session_state[winner_key] = default_winner if default_winner in winner_options else "Tie"

        winner = st.radio(
            "",
            options=winner_options,
            index=winner_options.index(st.session_state[winner_key]),
            key=winner_key
        )

        st.markdown("### Reason")
        # ========== Part 1: Winner reason (template) ==========
        st.markdown("**Winner**")

        opt1 = f"{name2} compromised / {name2}'s initial expected outcome changed."
        opt2 = f"{name1} compromised / {name1}'s initial expected outcome changed."
        opt3 = "Neither compromised / no clear change in initial expected outcome."

        WINNER_REASON_OPTIONS = [opt1, opt2, opt3]
        winner_reason_key = f"winner_reason_{case_id}"

        # default
        if prev and prev.get("winner_reason") in WINNER_REASON_OPTIONS:
            default_idx = WINNER_REASON_OPTIONS.index(prev["winner_reason"])
        else:
            if winner == name1:
                default_idx = 1  # name2 influenced by name1
            elif winner == name2:
                default_idx = 0  # name1 influenced by name2
            else:
                default_idx = 2  # tie

        winner_reason = st.radio(
        label="",
        options=WINNER_REASON_OPTIONS,
        index=default_idx,
        key=winner_reason_key,
    )

        # ========== Part 2: Tags reason (free-form) ========== 
        st.markdown("**Power source tags**")

        tags_reason_key = f"tags_reason_{case_id}"
        default_tags_reason = prev.get("tags_reason", "") if prev else ""

        tags_reason = st.text_area(
            label="",
            key=tags_reason_key,
            value=default_tags_reason if tags_reason_key not in st.session_state else None,
            height=120,
            placeholder="Explain why you selected these power source tags (short phrases OK)."
        )


        # Navigation buttons (main script, NOT callbacks)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("⬅️ Previous", key=f"nav_prev_{case_id}", disabled=(st.session_state.case_idx == 0)):
                st.session_state.case_idx = max(st.session_state.case_idx - 1, 0)
                st.session_state._sync_jump = True
                st.rerun()

        with c2:
            if st.button("✅ Save & Next ➡️", type="primary", key=f"save_next_{case_id}"):
                tags_s1 = get_selected_tags_from_state(POWER_SOURCE_TAGS, key_prefix=f"s1_{case_id}")
                tags_s2 = get_selected_tags_from_state(POWER_SOURCE_TAGS, key_prefix=f"s2_{case_id}") 
                record = {
                "case_id": case_id,
                "annotator": st.session_state.annotator,
                "timestamp": datetime.utcnow().isoformat(),
                "winner": winner,
                "power_sources_s1": tags_s1,
                "power_sources_s2": tags_s2,
                "winner_reason": winner_reason,
                "tags_reason": tags_reason,
                "meta_snapshot": {
                    "relationship_type": meta.get("relationship_type"),
                    "role1": meta.get("role1"),
                    "role2": meta.get("role2"),
                    "name1": name1,
                    "name2": name2,
                    }
                }
                upsert_annotation(case_id, st.session_state.annotator, record)

                st.session_state.case_idx = min(st.session_state.case_idx + 1, len(cases) - 1)
                st.session_state._sync_jump = True
                st.rerun()



    with col_right:
        st.markdown("### Power source tags")

        default_s1 = prev.get("power_sources_s1", []) if prev else []
        default_s2 = prev.get("power_sources_s2", []) if prev else []

        cA, cB = st.columns(2, gap="large")
        with cA:
            _ = render_tag_checkboxes(
                title=f"{name1}",
                tags=POWER_SOURCE_TAGS,
                default_selected=default_s1,
                key_prefix=f"s1_{case_id}", 
                n_cols=1
            )
        with cB:
            _ = render_tag_checkboxes(
                title=f"{name2}",
                tags=POWER_SOURCE_TAGS,
                default_selected=default_s2,
                key_prefix=f"s2_{case_id}",
                n_cols=1
            )


    # Small footer progress
    st.markdown("---")
    st.caption(f"Annotated: {done} / {total}")

