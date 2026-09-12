import streamlit as st

import json
import requests

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from datetime import datetime, timedelta, timezone
from openai import OpenAI
from pydantic import BaseModel, Field


class Candidate(BaseModel):
    topic: str
    publication_datetime: str = Field(
        description="Publication date and time in ISO 8601 format"
    )
    source_url: str
    summary: str
    why_it_matters: str
    overlooked_tension: str
    linkedin_angle: str


class CandidateList(BaseModel):
    candidates: list[Candidate]


def parse_publication_datetime(value):
    if not value:
        return None

    try:
        cleaned_value = value.strip().replace("Z", "+00:00")
        parsed_date = datetime.fromisoformat(cleaned_value)

        if parsed_date.tzinfo is None:
            return None

        return parsed_date.astimezone(timezone.utc)

    except (ValueError, TypeError):
        return None
def extract_publication_datetime_from_url(url):
    if not url or not url.startswith(("https://", "http://")):
        return None

    try:
        response = requests.get(
            url,
            timeout=10,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 AnkySignalScout/1.0"
                )
            }
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        metadata_fields = [
            ("property", "article:published_time"),
            ("property", "og:published_time"),
            ("name", "date"),
            ("name", "pub_date"),
            ("name", "publish-date"),
            ("name", "parsely-pub-date"),
            ("name", "sailthru.date"),
            ("itemprop", "datePublished")
        ]

        possible_dates = []

        for attribute, value in metadata_fields:
            tag = soup.find(
                "meta",
                attrs={attribute: value}
            )

            if tag and tag.get("content"):
                possible_dates.append(tag["content"])

        for script in soup.find_all(
            "script",
            attrs={"type": "application/ld+json"}
        ):
            try:
                data = json.loads(script.string or "{}")

                items = data if isinstance(data, list) else [data]

                for item in items:
                    if isinstance(item, dict):
                        published_date = item.get(
                            "datePublished"
                        )

                        if published_date:
                            possible_dates.append(
                                published_date
                            )

            except (json.JSONDecodeError, TypeError):
                continue

        for possible_date in possible_dates:
            try:
                parsed_date = date_parser.parse(
                    possible_date
                )

                if parsed_date.tzinfo is not None:
                    return parsed_date.astimezone(
                        timezone.utc
                    )

            except (ValueError, TypeError, OverflowError):
                continue

        return None

    except requests.RequestException:
        return None

st.set_page_config(
    page_title="Anky Signal Scout",
    page_icon="📡",
    layout="wide"
)

client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

st.title("📡 Anky Signal Scout")
st.write("Discover technology trends worth analysing on LinkedIn.")

st.sidebar.header("Search settings")

time_window = st.sidebar.selectbox(
    "How recent should the topics be?",
    ["Last 24 hours", "Last 7 days", "Last 30 days"]
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
        "E-commerce"
    ],
    default=[
        "Enterprise AI",
        "AI Agents",
        "Enterprise Transformation"
    ]
)

st.subheader("Your search")
st.write(f"**Time window:** {time_window}")
st.write(f"**Content pillars:** {', '.join(content_pillars)}")

if st.button("Find content opportunities", type="primary"):
    if not content_pillars:
        st.warning("Please select at least one content pillar.")

    else:
        with st.spinner(
            "Searching and validating recent developments..."
        ):
            try:
                window_hours = {
                    "Last 24 hours": 24,
                    "Last 7 days": 168,
                    "Last 30 days": 720
                }[time_window]

                current_time = datetime.now(timezone.utc)
                cutoff_time = current_time - timedelta(
                    hours=window_hours
                )

                discovery_prompt = f"""
Search the web for up to ten recent technology developments.

Focus on:
{", ".join(content_pillars)}

Current UTC time:
{current_time.isoformat()}

Look primarily for developments published after:
{cutoff_time.isoformat()}

For every candidate, provide:

- Topic
- Exact publication date and time with timezone
- Direct source URL
- Factual summary
- Why it matters for enterprises
- A specific overlooked tension
- A critical LinkedIn angle for Anita

Important:

- Preserve the publication date shown by the source.
- Never change an old date to make a candidate appear recent.
- If the exact date or timezone is unavailable, say it is unknown.
- Prefer official and authoritative sources.
- Do not claim LinkedIn traction.
- Do not use generic promotional language.
"""

                research_response = client.responses.create(
                    model="gpt-4.1-mini",
                    tools=[
                        {
                            "type": "web_search",
                            "search_context_size": "low"
                        }
                    ],
                    tool_choice="required",
                    include=[
                        "web_search_call.action.sources"
                    ],
                    input=discovery_prompt
                )

                consulted_sources = []

                for item in research_response.output:
                    if (
                        getattr(item, "type", None)
                        == "web_search_call"
                    ):
                        action = getattr(item, "action", None)
                        sources = (
                            getattr(action, "sources", [])
                            or []
                        )

                        for source in sources:
                            url = getattr(source, "url", None)

                            if (
                                url
                                and url not in consulted_sources
                            ):
                                consulted_sources.append(url)

                extraction_response = client.responses.parse(
                    model="gpt-5-mini",
                    input=[
                        {
                            "role": "system",
                            "content": """
Extract every candidate from the research into the required
structure.

Rules:

- Copy publication dates exactly from the research.
- Convert a verified date and time to ISO 8601 format.
- Include the timezone.
- If the date, time or timezone is missing, use an empty string.
- Never estimate or invent a publication time.
- Preserve each source URL.
"""
                        },
                        {
                            "role": "user",
                            "content": research_response.output_text
                        }
                    ],
                    text_format=CandidateList
                )

                candidates = (
                    extraction_response.output_parsed.candidates
                )

                valid_candidates = []
                rejected_candidates = []

                for candidate in candidates:
                    publication_time = (
                                extract_publication_datetime_from_url(
                                    candidate.source_url
                                )
                            )
        
                            if publication_time is not None:
                                candidate.publication_datetime = (
                                    publication_time.isoformat()
                                )
                            else:
                                candidate.publication_datetime = ""
        
                            if (
                                publication_time is not None
                                and cutoff_time
                                <= publication_time
                                <= current_time
                            ):
                                valid_candidates.append(candidate)
                            else:
                                rejected_candidates.append(candidate)
        
                if not valid_candidates:
                    st.warning(
                        "No verifiably recent developments were "
                        "found within this time window."
                    )

                else:
                    opportunity_word = (
                        "opportunity"
                        if len(valid_candidates) == 1
                        else "opportunities"
                    )

                    st.success(
                        f"{len(valid_candidates)} valid content "
                        f"{opportunity_word} found."
                    )

                    st.subheader("Content opportunities")

                    for candidate in valid_candidates:
                        st.markdown("---")
                        st.subheader(candidate.topic)

                        st.caption(
                            "Published: "
                            f"{candidate.publication_datetime}"
                        )

                        st.markdown("**What happened**")
                        st.write(candidate.summary)

                        st.markdown("**Why it matters**")
                        st.write(candidate.why_it_matters)

                        st.markdown("**Overlooked tension**")
                        st.write(candidate.overlooked_tension)

                        st.markdown(
                            "**Possible LinkedIn angle**"
                        )
                        st.write(candidate.linkedin_angle)

                        st.markdown(
                            f"**Source:** "
                            f"[{candidate.source_url}]"
                            f"({candidate.source_url})"
                        )

                with st.expander("Websites consulted"):
                    if consulted_sources:
                        for url in consulted_sources:
                            st.markdown(f"- {url}")
                    else:
                        st.write(
                            "No source metadata was returned."
                        )

                with st.expander("Validation details"):
                    st.write(
                        "Candidates discovered: "
                        f"{len(candidates)}"
                    )
                    st.write(
                        "Candidates accepted: "
                        f"{len(valid_candidates)}"
                    )
                    st.write(
                        "Candidates rejected: "
                        f"{len(rejected_candidates)}"
                    )

                    if rejected_candidates:
                        st.write(
                            "Rejected because the publication "
                            "date was missing, invalid, in the "
                            "future or outside the selected time "
                            "window:"
                        )

                        for candidate in rejected_candidates:
                            displayed_date = (
                                candidate.publication_datetime
                                or "Unverified"
                            )

                            if candidate.source_url:
                                st.markdown(
                                    f"- [{candidate.topic}]"
                                    f"({candidate.source_url}) "
                                    f"— {displayed_date}"
                                )
                            else:
                                st.write(
                                    f"- {candidate.topic} "
                                    f"— {displayed_date}"
                                )

            except Exception as error:
                st.error(f"Search failed: {error}")
