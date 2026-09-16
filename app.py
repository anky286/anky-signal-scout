import calendar
import json
from datetime import datetime, timedelta, timezone
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
    page_icon="📡",
    layout="wide",
)

client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

st.title("📡 Anky Signal Scout")
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
        with st.spinner("Collecting recent news and analysing signals..."):
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

                    analysis_response = client.responses.parse(
                        model="gpt-5-mini",
                        input=[
                            {
                                "role": "system",
                                "content": """
You are the analysis stage of Anky Signal Scout.

Select up to five of the strongest supplied candidates for a
critical LinkedIn post or article. Use only the supplied facts.

For each selected candidate:
- Preserve its candidate_id exactly.
- Explain why it matters to enterprises.
- Identify a specific overlooked tension or double standard.
- Suggest a sharp LinkedIn angle for Ankita.
- Avoid generic promotional language.
- Do not claim that something is trending on LinkedIn.
""",
                            },
                            {
                                "role": "user",
                                "content": json.dumps(
                                    model_input, ensure_ascii=False
                                ),
                            },
                        ],
                        text_format=OpportunityList,
                    )

                    parsed_opportunities = (
                        analysis_response.output_parsed.opportunities
                    )
                    items_by_id = {
                        item["candidate_id"]: item for item in rss_items
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
                            "Recent RSS items were found, but no content "
                            "opportunities were selected."
                        )
                    else:
                        st.success(
                            f"{len(valid_opportunities)} recent content "
                            "opportunities found."
                        )
                        st.subheader("Content opportunities")

                        for opportunity in valid_opportunities:
                            source_item = items_by_id[
                                opportunity.candidate_id
                            ]

                            st.markdown("---")
                            st.subheader(source_item["title"])
                            st.caption(
                                "Published: "
                                f"{source_item['publication_datetime']} | "
                                f"Source: {source_item['source_name']}"
                            )

                            st.markdown("**What happened**")
                            st.write(opportunity.summary)

                            st.markdown("**Why it matters**")
                            st.write(opportunity.why_it_matters)

                            st.markdown("**Overlooked tension**")
                            st.write(opportunity.overlooked_tension)

                            st.markdown("**Possible LinkedIn angle**")
                            st.write(opportunity.linkedin_angle)

                            st.markdown(
                                f"**Source:** [{source_item['source_name']}]"
                                f"({source_item['source_url']})"
                            )

                with st.expander("Validation details"):
                    st.write(
                        f"RSS items accepted by Python: {len(rss_items)}"
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
                            f"— {item['publication_datetime']} "
                            f"— {item['source_name']}"
                        )

            except Exception as error:
                st.error(f"Search failed: {error}")
