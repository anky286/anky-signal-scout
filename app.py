import streamlit as st
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
                research_prompt = f"""
                Search the web for three meaningful technology developments
                from {time_window.lower()}.

                Focus on:
                {", ".join(content_pillars)}

                For each development, provide:

                1. Topic
                2. What happened
                3. Why it matters
                4. What most people are saying
                5. A critical question people may be overlooking
                6. A possible LinkedIn angle for Anita
                7. Supporting sources

                Prefer primary and authoritative sources.
                Include exact dates.
                Do not invent popularity or traction data.
                Avoid generic AI predictions.
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
