from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_groq import ChatGroq

load_dotenv()


@tool
def get_stock_snapshot(ticker: str) -> str:
    """Get basic information about a stock ticker."""

    # Temporary fake data.
    # We will replace this with real financial data later.
    snapshots = {
        "AAPL": "Apple: sample price $210, sample revenue growth 5%.",
        "MSFT": "Microsoft: sample price $450, sample revenue growth 12%.",
        "BDC": "Belden: sample price $125, sample revenue growth 3%.",
    }

    return snapshots.get(
        ticker.upper(),
        f"No sample data is available for {ticker.upper()}.",
    )


model = ChatGroq(
    model="llama-3.1-8b-instant",
    temperature=0,
)

agent = create_agent(
    model=model,
    tools=[get_stock_snapshot],
    system_prompt="""
You are a simple stock research assistant.

Always use the stock tool before discussing a company.
Clearly state that the current data is sample data.
Do not give investment advice.
""",
)

result = agent.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": "Analyze AAPL using the available stock tool.",
            }
        ]
    }
)

print(result["messages"][-1].content)
