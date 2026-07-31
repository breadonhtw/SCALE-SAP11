"""Case file view: exact facts, relationship path, policy context, provenance."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from components import api_client, theme, ui  # noqa: E402

st.set_page_config(page_title="Case File", layout="wide")
theme.inject()

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

st.page_link("app.py", label="Queue", icon=ui.ICON_BACK)
st.page_link("pages/1_Alert_Detail.py", label="Alert detail", icon=ui.ICON_BACK)
st.title(f"Case file — {case_id}")
ui.stepper(2)
st.caption(f"Alert `{cf['alert_details']['alert_id']}` · assembled "
           f"{ui.fmt_ts(cf['assembled_at'])} · source coverage "
           f"{cf['source_coverage']:.0%} · region {cf['region']}")

if cf["missing_information"]:
    with st.container(border=True):
        st.markdown(f":material/warning: **Missing information** "
                    "(absent values are declared, never inferred)")
        for m in cf["missing_information"]:
            attempted = f" (attempted: {m['attempted_source']})" if m.get("attempted_source") else ""
            st.write(f"`{m['field']}` — {m['reason']}{attempted}")

tab_facts, tab_graph, tab_policy, tab_prov = st.tabs(
    ["Exact facts", "Relationship path", "Policy context", "Provenance & freshness"])

with tab_facts:
    pe = cf["priority_explanation"]
    st.subheader("Priority")
    st.markdown(f"{ui.tier_badge(pe['urgency_tier'])} score "
                f"**{pe['urgency_score']:.1f}** · complexity "
                f"{pe['complexity_band']} · policy `{pe['policy_version']}`"
                + (f" · {theme.chip('Override', 'red')} `{pe['hard_override_code']}`"
                   if pe.get("hard_override_code") else ""),
                unsafe_allow_html=True)

    for adv in cf.get("predictive_advisories", []):
        st.info(f"{adv.get('label', ui.ADVISORY_LABEL)} — "
                f"{adv['prediction_type']}: {float(adv['prediction_value']):.1f} "
                f"(`{adv['model_name']} {adv['model_version']}`)", icon=ui.ICON_ADVISORY)

    st.subheader("Customer profile")
    profile = cf.get("customer_profile")
    if profile:
        ui.table([{"Field": k, "Value": "—" if v is None else str(v)}
                  for k, v in profile.items()])
    else:
        st.caption("No customer profile (see missing information).")

    st.subheader("Counterparties")
    if cf["counterparty_profiles"]:
        ui.table([{"Counterparty": cp["counterparty_label"],
                   "Jurisdiction": cp.get("jurisdiction_country_id") or "—",
                   "Appearances": cp["appearance_count"]}
                  for cp in cf["counterparty_profiles"]])
    else:
        st.caption("No counterparty profiles in this case file.")

    st.subheader("Transaction timeline")
    if cf["transaction_timeline"]:
        ui.table(
            [{"Occurred (SGT)": ui.fmt_ts(t.get("occurred_at")),
              "Transaction": t["transaction_id"],
              "Amount (USD)": (ui.money(str(t["amount_usd"]), "USD")
                                if t.get("amount_usd") is not None else "—"),
              "Original ccy": t.get("currency_original") or "—",
              "Direction": t.get("direction") or "—",
              "Corridor": f"{t.get('origin_country_id') or '?'}→"
                          f"{t.get('destination_country_id') or '?'}",
              "Cross-border": t.get("is_cross_border")}
             for t in cf["transaction_timeline"]])
        st.caption("Amounts are source-verbatim decimal values; "
                   "the model never computes them.")
    else:
        st.caption("No transactions in this case file.")

    st.subheader("Related alerts")
    if cf["related_alerts"]:
        ui.table([{"Alert": r["alert_id"], "Type": r.get("alert_type") or "—",
                   "Status": r.get("status") or "—",
                   "Shared company": r.get("shared_company_id") or "—"}
                  for r in cf["related_alerts"]])
    else:
        st.caption("No related alerts.")

with tab_graph:
    edges = cf["entity_relationships"]
    if not edges:
        st.caption("No relationship paths retrieved for this case.")
    else:
        st.caption("Relationship evidence from graph retrieval — every edge "
                   "carries its citation.")
        dot = ["digraph G {", "rankdir=LR; node [shape=box, style=rounded];"]
        for e in edges:
            dot.append(f'"{e["source_node"]}" -> "{e["target_node"]}" '
                       f'[label="{e["relationship_type"]}"];')
        dot.append("}")
        st.graphviz_chart("\n".join(dot))
        ui.table([{"Relationship": e["relationship_type"],
                   "From": e["source_node"], "To": e["target_node"],
                   "Citation": f"`{e['citation_id']}`"} for e in edges])

with tab_policy:
    st.caption("Retrieved by vector similarity with metadata filters. Policy "
               "content is authoritative; historical cases are reference only.")
    for pc in cf["policy_context"]:
        with st.container(border=True):
            st.markdown(f"**{pc['document_id']}** — `{pc['passage_locator']}`")
            st.write(pc["text"])
            st.caption(f"Similarity {pc['similarity_score']:.2f} · "
                       f"citation `{pc['citation_id']}`")
    if not cf["policy_context"]:
        st.caption("No policy passages retrieved.")
    if not cf["historical_case_references"]:
        st.caption("Historical case references: none included "
                   "(permission-filtered; excluded from urgency scoring).")

with tab_prov:
    st.subheader("Source provenance")
    ui.table(
        [{"Citation": c["citation_id"], "Type": c["source_type"],
          "Kind": c["evidence_kind"], "Source": c["source_id"],
          "Locator": f"`{c['source_locator']}`",
          "Retrieved": ui.fmt_ts(c["retrieved_at"]), "Region": c["region"],
          "Scope": c["permission_scope"]}
         for c in cf["source_provenance"]])
    st.subheader("Data freshness")
    ui.table(
        [{"Source": f["source_object"], "Retrieved (SGT)": ui.fmt_ts(f["retrieved_at"]),
          "Source updated": ui.fmt_ts(f.get("source_updated_at"))}
         for f in cf["data_freshness"]])

st.divider()
if st.button("Narrative & explanation →", type="primary"):
    st.switch_page("pages/3_Narrative.py")
