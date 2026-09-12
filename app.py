import streamlit as st
from datetime import datetime, timedelta, timezone


from openai import OpenAI

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
        with st.spinner("Searching for recent technology developments..."):
            try:
                window_hours = {
    "Last 24 hours": 24,
    "Last 7 days": 168,
    "Last 30 days": 720
}[time_window]

current_time = datetime.now(timezone.utc)
cutoff_time = current_time - timedelta(hours=window_hours)
               research_prompt = f"""
You are a rigorous technology research analyst.

Current UTC time:
{current_time.isoformat()}

Cutoff time:
{cutoff_time.isoformat()}

Find up to three meaningful technology developments published
or officially announced between the cutoff time and current time.

Focus on:
{", ".join(content_pillars)}

STRICT RULES:

- Do not include anything older than the cutoff time.
- Check both the event date and the source publication date.
- Never use an old announcement merely to fill the requested number.
- If only one qualifying development exists, return only one.
- Prefer an official primary source plus one independent source.
- If only one source exists, label the topic "single-source".
- Do not claim what analysts, experts or most people think without
  evidence from multiple sources.
- Separate company claims from independently verified facts.
- Do not invent LinkedIn traction.
- Avoid generic privacy, security and job-loss questions unless the
  evidence makes them specifically relevant.
- Avoid promotional phrases such as "exploring the future" and
  "harnessing the power".

For each valid development, provide:

1. Topic
2. Exact event date
3. Exact source publication date
4. What happened
5. Why it matters for enterprises
6. What the company claims
7. What independent evidence supports or challenges the claim
8. What remains uncertain
9. A specific overlooked tension or consequence
10. A sharp LinkedIn angle for Anita
11. Supporting sources

Anita's positioning is:
Enterprise AI × Product × Transformation.

Her angle should question the popular narrative and connect the
development to implementation, governance, product design, data
quality, ownership, adoption or measurable business value.

If no qualifying developments exist, say:
"No sufficiently strong developments found within this time window."
"""

                response = client.responses.create(
                    model="gpt-4.1-mini",
                    tools=[
                        {
                            "type": "web_search",
                            "search_context_size": "low"
                        }
                    ],
                    tool_choice="required",
                    input=research_prompt
                )

                st.success("Research complete!")
                st.subheader("Content opportunities")
                st.markdown(response.output_text)

            except Exception as error:
                st.error(f"Search failed: {error}")
