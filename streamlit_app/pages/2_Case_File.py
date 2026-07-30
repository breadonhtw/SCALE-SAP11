"""Case file view: exact facts, relationship path, policy context, provenance."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from components import api_client, ui  # noqa: E402

st.set_page_config(page_title="Case File", page_icon="🛡️", layout="wide")

case_id = st.session_state.get("case_id") or st.query_params.get("case_id")
if not case_id:
    st.info("Assemble a case from an alert first.")
    st.page_link("app.py", label="← Back to queue")
    st.stop()

state = api_client.get_case(case_id)
cf = state["case_file"]
if not cf:
    st.warning("Case exists but no CaseFile assembled yet.")
    st.page_link("pages/1_Alert_Detail.py", label="← Alert detail")
    st.stop()

st.page_link("app.py", label="← Queue")
st.page_link("pages/1_Alert_Detail.py", label="← Alert detail")
st.title(f"Case file — {case_id}")
cov = cf["source_coverage"]
st.caption(f"Alert `{cf['alert_details']['alert_id']}` · schema "
           f"`{cf['schema_version']}` · assembled {cf['assembled_at']} UTC · "
           f"sections populated {cov['sections_populated']}/{cov['sections_total']}")

if cf["missing_information"]:
    with st.container(border=True):
        st.markdown("**Missing information** (absent values are declared, never inferred)")
        for m in cf["missing_information"]:
            icon = "⚠️" if m["severity"] == "warning" else "ℹ️"
            st.write(f"{icon} `{m['field']}` — {m['reason']}")

tab_facts, tab_graph, tab_policy, tab_prov = st.tabs(
    ["Exact facts", "Relationship path", "Policy context", "Provenance & freshness"])

with tab_facts:
    st.subheader("Customer profile")
    profile = cf["customer_profile"]
    ui.table(
        [{"Field": k, "Value": "—" if v is None else str(v)}
         for k, v in profile.items() if k != "citation_ids"])
    st.caption(f"Citations: {', '.join(profile['citation_ids'])}")

    st.subheader("Counterparties")
    if not cf["counterparty_profiles"]:
        st.caption("No counterparty profiles in this case file.")
    for cp in cf["counterparty_profiles"]:
        sanction = (f" — **{cp['sanctions_reference']}**"
                    if cp.get("sanctions_reference") else "")
        st.markdown(f"`{cp['company_id']}` {cp['legal_name']} · "
                    f"{cp['jurisdiction_code']} · {cp['risk_rating']}{sanction} "
                    f"[{', '.join(cp['citation_ids'])}]")

    st.subheader("Transaction timeline")
    if cf["transaction_timeline"]:
        ui.table(
            [{"Initiated (UTC)": t["initiated_at"],
              "Transaction": t["transaction_id"],
              "Amount (USD)": ui.money(t["amount_usd"], "USD"),
              "Original ccy": t["currency_original"],
              "Type": t["transaction_type"],
              "Beneficiary": t["beneficiary_company_id"],
              "Corridor": f"{t['originating_country']}→{t['destination_country']}",
              "Citations": ", ".join(t["citation_ids"])}
             for t in cf["transaction_timeline"]])
        st.caption("Amounts are exact decimal values from cited records; "
                   "the model never computes them.")
    else:
        st.caption("No transactions in this case file.")

with tab_graph:
    paths = cf["entity_relationships"]
    if not paths:
        st.caption("No relationship paths retrieved for this case.")
    for p in paths:
        st.markdown(f"**{p['question']}**")
        st.caption(f"Derivation: {p['derivation']} · workspace "
                   f"`{p['graph_workspace']}` · citation {p['citation_id']}")
        labels = {n["entity_id"]: n["label"] for n in p["nodes"]}
        dot = ["digraph G {", "rankdir=LR; node [shape=box, style=rounded];"]
        for n in p["nodes"]:
            shape = "ellipse" if n["entity_type"] == "BENEFICIAL_OWNER" else "box"
            dot.append(f'"{n["entity_id"]}" '
                       f'[label="{labels[n["entity_id"]]}", shape={shape}];')
        for e in p["edges"]:
            props = ", ".join(f"{k}={v}" for k, v in e.get("properties", {}).items())
            dot.append(f'"{e["source"]}" -> "{e["target"]}" '
                       f'[label="{e["edge_type"]}\\n{props}"];')
        dot.append("}")
        st.graphviz_chart("\n".join(dot))
        ui.table(
            [{"Edge": e["edge_id"], "Type": e["edge_type"],
              "From": labels.get(e["source"], e["source"]),
              "To": labels.get(e["target"], e["target"]),
              "Source record": f"`{e['source_table']}:{e['source_record_id']}`"}
             for e in p["edges"]])

with tab_policy:
    st.caption("Retrieved by HANA vector similarity with metadata filters. "
               "Policy content is authoritative; historical cases are reference only.")
    for pc in cf["policy_context"]:
        with st.container(border=True):
            st.markdown(f"**{pc['doc_id']} §{pc['clause_id']} — {pc['title']}** "
                        f"({pc['doc_type']}, effective {pc['effective_date']})")
            st.write(pc["excerpt"])
            st.caption(f"Similarity {pc['similarity']:.2f} · {pc['authority']} · "
                       f"citations {', '.join(pc['citation_ids'])}")
    if not cf["historical_case_references"]:
        st.caption("Historical case references: none included "
                   "(permission-filtered; excluded from urgency scoring).")

with tab_prov:
    st.subheader("Source provenance")
    ui.table(
        [{"Citation": c["citation_id"], "Type": c["source_type"],
          "Source": c["source_id"], "Locator": f"`{c['source_locator']}`",
          "Source version": c["source_version"], "Retrieved": c["retrieved_at"],
          "Region": c["region"], "Scope": c["permission_scope"]}
         for c in cf["source_provenance"]])
    st.subheader("Data freshness")
    ui.table(
        [{"Source": f["source"], "Last updated (UTC)": f["source_updated_at"]}
         for f in cf["data_freshness"]])

st.divider()
if st.button("Narrative & explanation →", type="primary"):
    st.switch_page("pages/3_Narrative.py")
