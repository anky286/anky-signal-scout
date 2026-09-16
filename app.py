import calendar
import json
import re
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote_plus, urlparse

import feedparser
import streamlit as st
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel


APP_VERSION = 8

SOURCE_TYPE = Literal[
    "Official announcement",
    "Independent reporting",
    "Press release or syndication",
    "Opinion or analysis",
    "Unclear",
]


class ContentOpportunity(BaseModel):
    candidate_id: int
    summary: str
    why_it_matters: str
    overlooked_tension: str
    linkedin_angle: str


class OpportunityList(BaseModel):
    opportunities: list[ContentOpportunity]


class EvidenceSource(BaseModel):
    title: str
    publisher: str
    publication_date: str
    source_type: SOURCE_TYPE
    url: str


class CandidateVerification(BaseModel):
    candidate_id: int
    verification_status: Literal[
        "Verified",
        "Company-reported",
        "Single-source claim",
        "Opinion",
        "Unmatched",
    ]
    source_type: SOURCE_TYPE
    evidence_title: str
    evidence_publisher: str
    evidence_publication_date: str
    verified_facts: str
    verification_note: str
    primary_source_url: str
    supporting_sources: list[EvidenceSource]


class VerificationList(BaseModel):
    verifications: list[CandidateVerification]


def is_web_url(value):
    return isinstance(value, str) and value.startswith(("http://", "https://"))


TITLE_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "the",
    "to",
    "with",
}


def title_tokens(value, publisher=""):
    title = value.casefold().strip()
    publisher_suffix = publisher.casefold().strip()

    if publisher_suffix and title.endswith(f" - {publisher_suffix}"):
        title = title[: -(len(publisher_suffix) + 3)]

    return {
        token
        for token in re.findall(r"[a-z0-9]+", title)
        if token not in TITLE_STOPWORDS and len(token) > 1
    }


def title_match_score(candidate_title, evidence_title, publisher=""):
    candidate_tokens = title_tokens(candidate_title, publisher)
    evidence_tokens = title_tokens(evidence_title)

    if not candidate_tokens or not evidence_tokens:
        return 0.0

    overlap = len(candidate_tokens & evidence_tokens) / min(
        len(candidate_tokens), len(evidence_tokens)
    )
    sequence = SequenceMatcher(
        None,
        " ".join(sorted(candidate_tokens)),
        " ".join(sorted(evidence_tokens)),
    ).ratio()
    return max(overlap, sequence)


def publisher_matches(rss_publisher, evidence_publisher):
    rss_tokens = title_tokens(rss_publisher)
    evidence_tokens = title_tokens(evidence_publisher)
    return bool(rss_tokens and evidence_tokens and rss_tokens & evidence_tokens)


def source_domain(url):
    if not is_web_url(url):
        return ""
    return urlparse(url).netloc.casefold().removeprefix("www.")


def parse_evidence_date(value):
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (AttributeError, TypeError, ValueError):
        return None


def harden_verification(item, verification, cutoff_time, current_time):
    problems = []
    verification.primary_source_url = (
        verification.primary_source_url
        if is_web_url(verification.primary_source_url)
        else ""
    )

    match_score = title_match_score(
        item["title"],
        verification.evidence_title,
        item["source_name"],
    )
    evidence_date = parse_evidence_date(
        verification.evidence_publication_date
    )

    if not verification.primary_source_url:
        problems.append("the exact evidence URL is missing")
    if match_score < 0.55:
        problems.append(
            f"headline match was too weak ({match_score:.2f})"
        )
    if not publisher_matches(
        item["source_name"], verification.evidence_publisher
    ):
        problems.append("the evidence publisher does not match the RSS publisher")
    if evidence_date is None:
        problems.append("the evidence publication date is missing or invalid")
    elif not cutoff_time.date() <= evidence_date <= current_time.date():
        problems.append("the evidence publication date is outside the window")

    if problems:
        verification.verification_status = "Unmatched"
        verification.verification_note = (
            "Rejected by Python because " + "; ".join(problems) + "."
        )
        verification.supporting_sources = []
        return verification

    primary_domain = source_domain(verification.primary_source_url)
    clean_supporting_sources = []
    seen_domains = {primary_domain}

    for source in verification.supporting_sources:
        domain = source_domain(source.url)
        source_date = parse_evidence_date(source.publication_date)
        source_match_score = title_match_score(
            item["title"], source.title, item["source_name"]
        )

        if (
            not domain
            or domain in seen_domains
            or source_date is None
            or not cutoff_time.date()
            <= source_date
            <= current_time.date()
            or source_match_score < 0.35
            or source.source_type
            not in {"Official announcement", "Independent reporting"}
        ):
            continue

        seen_domains.add(domain)
        clean_supporting_sources.append(source)

    verification.supporting_sources = clean_supporting_sources
    credible_second_source = bool(clean_supporting_sources)
    has_independent_reporting = (
        verification.source_type == "Independent reporting"
        or any(
            source.source_type == "Independent reporting"
            for source in clean_supporting_sources
        )
    )

    if verification.verification_status == "Verified" and not (
        credible_second_source and has_independent_reporting
    ):
        verification.verification_status = "Single-source claim"
        verification.verification_note = (
            "Downgraded by Python because the exact story did not have "
            "a qualifying independent corroborating source."
        )

    if (
        verification.verification_status == "Company-reported"
        and verification.source_type
        not in {"Official announcement", "Press release or syndication"}
    ):
        verification.verification_status = "Single-source claim"
        verification.verification_note = (
            "Downgraded by Python because the evidence was not an "
            "official announcement or identifiable company release."
        )

    return verification


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
    layout="wide",
)

client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

st.title("Anky Signal Scout")
st.write("Discover technology trends worth analysing on LinkedIn.")

st.sidebar.header("Search settings")
st.sidebar.caption(f"App version: v{APP_VERSION}")

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

1. First locate the exact RSS article using its headline, RSS publisher,
   and publication date. Do not substitute a related article.
2. Record the exact evidence headline, publisher, publication date in
   YYYY-MM-DD format, and direct URL.
3. Only after the exact article is located, look for reputable,
   independent corroboration of the same event.
4. For every supporting source, record its headline, publisher,
   publication date, source type, and direct URL.
5. Distinguish a new development from an opinion article.
6. Do not treat repetition of the same press release as independent
   corroboration.
7. Preserve the candidate_id.

Use these status definitions:

- Verified: supported by an authoritative primary source and at least
  one credible independent source, or by two credible independent sources.
- Company-reported: supported by an official company source, but the
  important outcome or performance claims are self-reported.
- Single-source claim: only one source supports the claim, or the
  available sources are too weak to establish it confidently.
- Opinion: the RSS item is primarily commentary and does not report a
  discrete new development.
- Unmatched: the exact RSS article could not be located. A thematically
  related article is not a match.

For each candidate, report:

- candidate_id
- verification status
- source type
- exact evidence headline
- exact evidence publisher
- exact evidence publication date
- only the facts supported by the sources
- a short explanation of the status
- the exact RSS article's direct URL
- structured details for independent supporting sources

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
- Do not replace the RSS candidate with a related story.
- Copy evidence headlines, publishers, and dates exactly from the
  research.
- If the exact RSS article was not found, label it Unmatched and leave
  its evidence fields empty.
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

                    rss_items_by_id = {
                        item["candidate_id"]: item for item in rss_items
                    }
                    verifications_by_id = {}
                    for verification in (
                        verification_response.output_parsed.verifications
                    ):
                        rss_item = rss_items_by_id.get(
                            verification.candidate_id
                        )
                        if (
                            rss_item is not None
                            and
                            verification.candidate_id
                            not in verifications_by_id
                        ):
                            verifications_by_id[
                                verification.candidate_id
                            ] = harden_verification(
                                rss_item,
                                verification,
                                cutoff_time,
                                current_time,
                            )

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
                                    "Unmatched",
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
                                    "evidence_title": (
                                        verification.evidence_title
                                    ),
                                    "evidence_publisher": (
                                        verification.evidence_publisher
                                    ),
                                    "evidence_publication_date": (
                                        verification.evidence_publication_date
                                    ),
                                    "evidence_source_url": (
                                        verification.primary_source_url
                                    ),
                                    "supporting_sources": [
                                        source.model_dump()
                                        for source in (
                                            verification.supporting_sources
                                        )
                                    ],
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
                                        "**Evidence source:** "
                                        f"[{verification.evidence_title}]"
                                        f"({verification.primary_source_url})"
                                    )
                                    st.caption(
                                        f"{verification.evidence_publisher} | "
                                        f"{verification.evidence_publication_date}"
                                    )
                                else:
                                    st.markdown(
                                        f"**RSS source:** "
                                        f"[{source_item['source_name']}]"
                                        f"({source_item['source_url']})"
                                    )

                                if verification.supporting_sources:
                                    st.markdown("**Corroborating sources:**")
                                    for source in (
                                        verification.supporting_sources
                                    ):
                                        st.markdown(
                                            f"- [{source.title}]({source.url}) "
                                            f"â€” {source.publisher}, "
                                            f"{source.publication_date}"
                                        )

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
