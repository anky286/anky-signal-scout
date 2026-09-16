import calendar
import json
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote_plus

import feedparser
import streamlit as st
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel


class ContentOpportunity(BaseModel):
    candidate_id: int
    summary: str
    why_it_matters: str
    overlooked_tension: str
    linkedin_angle: str


class OpportunityList(BaseModel):
    opportunities: list[ContentOpportunity]


class CandidateVerification(BaseModel):
    candidate_id: int
    verification_status: Literal[
        "Verified",
        "Company-reported",
        "Single-source claim",
        "Opinion",
    ]
    source_type: Literal[
        "Official announcement",
        "Independent reporting",
        "Press release or syndication",
        "Opinion or analysis",
        "Unclear",
    ]
    verified_facts: str
    verification_note: str
    primary_source_url: str
    supporting_source_urls: list[str]


class VerificationList(BaseModel):
    verifications: list[CandidateVerification]


def is_web_url(value):
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def fetch_recent_rss_items(pillars, time_window, cutoff_time, current_time):
    when_token = {
        "Last 24 hours": "1d",
        "Last 7 days": "7d",
        "Last 30 days": "30d",
    }[time_window]

    collected_items = []
    seen_titles = set()

    for pillar in pillars:
        query = quote_plus(f'"{pillar}" technology when:{when_token}')
        feed_url = (
            "https://news.google.com/rss/search"
            f"?q={query}&hl=en-US&gl=US&ceid=US:en"
        )
        feed = feedparser.parse(feed_url)

        for entry in feed.entries[:15]:
            published_struct = entry.get("published_parsed")
            if not published_struct:
                continue

            published_time = datetime.fromtimestamp(
                calendar.timegm(published_struct),
                tz=timezone.utc,
            )

            if not cutoff_time <= published_time <= current_time:
                continue

            title = entry.get("title", "Untitled").strip()
            title_key = title.casefold()
            if title_key in seen_titles:
                continue

            source = entry.get("source", {})
            source_name = source.get("title", "Unknown source")
            summary_html = entry.get("summary", "")
            summary = BeautifulSoup(
                summary_html, "html.parser"
            ).get_text(" ", strip=True)

            seen_titles.add(title_key)
            collected_items.append(
                {
                    "title": title,
                    "publication_datetime": published_time.isoformat(),
                    "source_url": entry.get("link", ""),
                    "source_name": source_name,
                    "rss_summary": summary,
                    "matched_pillar": pillar,
                    "published_time": published_time,
                }
            )

    collected_items.sort(
        key=lambda item: item["published_time"], reverse=True
    )

    selected_items = collected_items[:10]
    for candidate_id, item in enumerate(selected_items):
        item["candidate_id"] = candidate_id

    return selected_items


st.set_page_config(
    page_title="Anky Signal Scout",
    page_icon="ðŸ“¡",
    layout="wide",
)

client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

st.title("ðŸ“¡ Anky Signal Scout")
st.write("Discover technology trends worth analysing on LinkedIn.")

st.sidebar.header("Search settings")

time_window = st.sidebar.selectbox(
    "How recent should the topics be?",
    ["Last 24 hours", "Last 7 days", "Last 30 days"],
)

content_pillars = st.sidebar.multiselect(
    "Which topics should I explore?",
    [
        "Enterprise AI",
        "AI Agents",
        "Product Management",
        "Enterprise Transformation",
        "Cloud & Infrastructure",
        "Cybersecurity",
        "E-commerce",
    ],
    default=[
        "Enterprise AI",
        "AI Agents",
        "Enterprise Transformation",
    ],
)

st.subheader("Your search")
st.write(f"**Time window:** {time_window}")
st.write(f"**Content pillars:** {', '.join(content_pillars)}")

if st.button("Find content opportunities", type="primary"):
    if not content_pillars:
        st.warning("Please select at least one content pillar.")
    else:
        with st.spinner(
            "Collecting, verifying, and analysing recent developments..."
        ):
            try:
                window_hours = {
                    "Last 24 hours": 24,
                    "Last 7 days": 168,
                    "Last 30 days": 720,
                }[time_window]

                current_time = datetime.now(timezone.utc)
                cutoff_time = current_time - timedelta(hours=window_hours)

                rss_items = fetch_recent_rss_items(
                    content_pillars,
                    time_window,
                    cutoff_time,
                    current_time,
                )

                if not rss_items:
                    st.warning(
                        "No RSS items with valid timestamps were found "
                        "within this time window."
                    )
                else:
                    model_input = []
                    for item in rss_items:
                        model_input.append(
                            {
                                "candidate_id": item["candidate_id"],
                                "title": item["title"],
                                "publication_datetime": item[
                                    "publication_datetime"
                                ],
                                "source_name": item["source_name"],
                                "rss_summary": item["rss_summary"],
                                "matched_pillar": item["matched_pillar"],
                            }
                        )

                    verification_prompt = f"""
Verify the supplied RSS candidates by searching the web.

Candidates:
{json.dumps(model_input, ensure_ascii=False)}

For every candidate_id:

1. Search for the exact development.
2. Prefer the original company announcement or original reporting.
3. Look for reputable independent corroboration.
4. Distinguish a new development from an opinion article.
5. Do not treat repetition of the same press release as independent
   corroboration.
6. Preserve the candidate_id.

Use these status definitions:

- Verified: supported by an authoritative primary source and at least
  one credible independent source, or by two credible independent sources.
- Company-reported: supported by an official company source, but the
  important outcome or performance claims are self-reported.
- Single-source claim: only one source supports the claim, or the
  available sources are too weak to establish it confidently.
- Opinion: the RSS item is primarily commentary and does not report a
  discrete new development.

For each candidate, report:

- candidate_id
- verification status
- source type
- only the facts supported by the sources
- a short explanation of the status
- the best primary or original source URL
- any independent supporting source URLs

If evidence is insufficient, say so. Never invent a URL or upgrade a
candidate merely to produce more results.
"""

                    verification_research = client.responses.create(
                        model="gpt-4.1-mini",
                        tools=[
                            {
                                "type": "web_search",
                                "search_context_size": "medium",
                            }
                        ],
                        tool_choice="required",
                        input=verification_prompt,
                    )

                    verification_response = client.responses.parse(
                        model="gpt-5-mini",
                        input=[
                            {
                                "role": "system",
                                "content": """
Extract the verification results into the required structure.

- Preserve candidate IDs exactly.
- Use only URLs explicitly present in the research.
- Do not infer missing evidence.
- If a candidate was not adequately researched, label it as a
  Single-source claim with an empty primary URL and an empty list of
  supporting URLs.
""",
                            },
                            {
                                "role": "user",
                                "content": (
                                    "Original candidates:\n"
                                    + json.dumps(
                                        model_input, ensure_ascii=False
                                    )
                                    + "\n\nVerification research:\n"
                                    + verification_research.output_text
                                ),
                            },
                        ],
                        text_format=VerificationList,
                    )

                    verifications_by_id = {}
                    for verification in (
                        verification_response.output_parsed.verifications
                    ):
                        if (
                            verification.candidate_id
                            not in verifications_by_id
                        ):
                            verification.primary_source_url = (
                                verification.primary_source_url
                                if is_web_url(
                                    verification.primary_source_url
                                )
                                else ""
                            )
                            verification.supporting_source_urls = [
                                url
                                for url in verification.supporting_source_urls
                                if is_web_url(url)
                            ]
                            verifications_by_id[
                                verification.candidate_id
                            ] = verification

                    analysis_items = []
                    excluded_items = []

                    for item in rss_items:
                        verification = verifications_by_id.get(
                            item["candidate_id"]
                        )

                        if verification is None:
                            excluded_items.append(
                                (
                                    item,
                                    "Single-source claim",
                                    "No verification result was returned.",
                                )
                            )
                            continue

                        if verification.verification_status in {
                            "Verified",
                            "Company-reported",
                        }:
                            enriched_item = item.copy()
                            enriched_item["verification"] = verification
                            analysis_items.append(enriched_item)
                        else:
                            excluded_items.append(
                                (
                                    item,
                                    verification.verification_status,
                                    verification.verification_note,
                                )
                            )

                    if not analysis_items:
                        st.warning(
                            "Recent RSS items were found, but none passed "
                            "the source-verification gate."
                        )
                    else:
                        analysis_model_input = []
                        for item in analysis_items:
                            verification = item["verification"]
                            analysis_model_input.append(
                                {
                                    "candidate_id": item["candidate_id"],
                                    "title": item["title"],
                                    "publication_datetime": item[
                                        "publication_datetime"
                                    ],
                                    "verification_status": (
                                        verification.verification_status
                                    ),
                                    "source_type": verification.source_type,
                                    "verified_facts": (
                                        verification.verified_facts
                                    ),
                                    "verification_note": (
                                        verification.verification_note
                                    ),
                                    "primary_source_url": (
                                        verification.primary_source_url
                                    ),
                                    "supporting_source_urls": (
                                        verification.supporting_source_urls
                                    ),
                                }
                            )

                        analysis_response = client.responses.parse(
                            model="gpt-5-mini",
                            input=[
                                {
                                    "role": "system",
                                    "content": """
You are the analysis stage of Anky Signal Scout.

Select up to five of the strongest supplied candidates for a
critical LinkedIn post or article. Use only the verified facts
supplied for each candidate.

For each selected candidate:
- Preserve its candidate_id exactly.
- Summarise what happened without adding unsupported details.
- If the status is Company-reported, clearly attribute performance
  or outcome claims to the company.
- Explain why it matters to enterprises.
- Identify a story-specific conflict, trade-off, or unanswered
  question supported by the available evidence.
- If the evidence does not support a meaningful tension, say that no
  clear tension was identified.
- Suggest a sharp LinkedIn angle for Ankita.
- Avoid generic promotional language.
- Do not repeatedly use formulas such as "the double standard."
- Do not claim that something is trending on LinkedIn.
""",
                                },
                                {
                                    "role": "user",
                                    "content": json.dumps(
                                        analysis_model_input,
                                        ensure_ascii=False,
                                    ),
                                },
                            ],
                            text_format=OpportunityList,
                        )

                        parsed_opportunities = (
                            analysis_response.output_parsed.opportunities
                        )
                        items_by_id = {
                            item["candidate_id"]: item
                            for item in analysis_items
                        }

                        valid_opportunities = []
                        used_ids = set()
                        for opportunity in parsed_opportunities:
                            if (
                                opportunity.candidate_id in items_by_id
                                and opportunity.candidate_id not in used_ids
                            ):
                                used_ids.add(opportunity.candidate_id)
                                valid_opportunities.append(opportunity)

                        if not valid_opportunities:
                            st.warning(
                                "Candidates passed verification, but no "
                                "content opportunities were selected."
                            )
                        else:
                            st.success(
                                f"{len(valid_opportunities)} verified "
                                "content opportunities found."
                            )
                            st.subheader("Content opportunities")

                            for opportunity in valid_opportunities:
                                source_item = items_by_id[
                                    opportunity.candidate_id
                                ]
                                verification = source_item["verification"]

                                st.markdown("---")
                                st.subheader(source_item["title"])
                                st.caption(
                                    "Published: "
                                    f"{source_item['publication_datetime']} | "
                                    "Verification: "
                                    f"{verification.verification_status} | "
                                    "Source type: "
                                    f"{verification.source_type}"
                                )

                                st.markdown("**What happened**")
                                st.write(opportunity.summary)

                                st.markdown("**Why it matters**")
                                st.write(opportunity.why_it_matters)

                                st.markdown("**Overlooked tension**")
                                st.write(opportunity.overlooked_tension)

                                st.markdown("**Possible LinkedIn angle**")
                                st.write(opportunity.linkedin_angle)

                                st.markdown("**Verification note**")
                                st.write(verification.verification_note)

                                if verification.primary_source_url:
                                    st.markdown(
                                        "**Primary source:** "
                                        f"[{verification.primary_source_url}]"
                                        f"({verification.primary_source_url})"
                                    )
                                else:
                                    st.markdown(
                                        f"**RSS source:** "
                                        f"[{source_item['source_name']}]"
                                        f"({source_item['source_url']})"
                                    )

                                if verification.supporting_source_urls:
                                    st.markdown("**Supporting sources:**")
                                    for url in (
                                        verification.supporting_source_urls
                                    ):
                                        st.markdown(f"- [{url}]({url})")

                    if excluded_items:
                        with st.expander(
                            "Excluded during source verification"
                        ):
                            for item, status, note in excluded_items:
                                st.markdown(
                                    f"- [{item['title']}]"
                                    f"({item['source_url']}) â€” "
                                    f"**{status}** â€” {note}"
                                )

                with st.expander("Validation details"):
                    st.write(
                        f"RSS items accepted by Python: {len(rss_items)}"
                    )
                    st.write(
                        "Items passed by source verification: "
                        f"{len(analysis_items) if rss_items else 0}"
                    )
                    st.write(
                        "Items excluded by source verification: "
                        f"{len(excluded_items) if rss_items else 0}"
                    )
                    st.write(
                        "Window start: "
                        f"{cutoff_time.isoformat()}"
                    )
                    st.write(
                        "Window end: "
                        f"{current_time.isoformat()}"
                    )

                    for item in rss_items:
                        st.markdown(
                            f"- [{item['title']}]({item['source_url']}) "
                            f"â€” {item['publication_datetime']} "
                            f"â€” {item['source_name']}"
                        )

            except Exception as error:
                st.error(f"Search failed: {error}")
