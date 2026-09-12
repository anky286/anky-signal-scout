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
        with st.spinner("Contacting the AI..."):
            try:
                response = client.responses.create(
                    model="gpt-5-mini",
                    input=(
                        "In one short sentence, confirm that you are ready "
                        "to research these technology topics: "
                        f"{', '.join(content_pillars)}."
                    )
                )

                st.success("OpenAI connection successful!")
                st.write(response.output_text)

            except Exception as error:
                st.error(f"Connection failed: {error}")
