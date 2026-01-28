import asyncio
from agent_framework.azure import AzureAIClient
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv
from typing import Annotated
from pydantic import Field


# Define tool functions outside main for cleaner organization
def search_web(
        query: Annotated[str, Field(description="The search query to look up on the web")]
        ) -> str:
    """ Mock web search tool function."""
    print("search_web tool is in action...")
    return f"Top search result for '{query}': Example Domain - This domain is for use in illustrative examples in documents."

def send_email(
        to: Annotated[str, Field(description="Recipient email address")],
        subject: Annotated[str, Field(description="Subject of the email")],
        body: Annotated[str, Field(description="Body content of the email")]
        ) -> str:
    """ Mock send email tool function."""
    print("send_email tool is in action...")
    return f"Email sent to {to} with subject '{subject}' and body '{body}'."

def calculate(
        operation: Annotated[str, Field(description="The mathematical operation to perform, e.g., 'add', 'subtract', 'multiply', 'divide'")],
        a: Annotated[float, Field(description="The first number")],
        b: Annotated[float, Field(description="The second number")]
        ) -> float:
    """ Simple calculator tool function."""
    print("calculate tool is in action...")
    ops = {
        'add': a + b,
        'subtract': a - b,
        'multiply': a * b,
        'divide': a / b if b != 0 else 'Error: Division by zero'
    }
    return ops.get(operation, 0)


async def main():
    load_dotenv('.env01')

    print("=" * 65)
    print("Practical tool implementation with proper lifecycle management...")
    print("=" * 65)
    print("Tool functions defined.")

    # Use async context manager for proper resource lifecycle management
    async with (
        DefaultAzureCredential() as credential,
        AzureAIClient(credential=credential).create_agent(
            name="MultiToolAgent",
            instructions="You are a helpful agent that can perform web searches, send emails, and do calculations using the provided tools.",
            tools=[search_web, send_email, calculate]
        ) as agent,
    ):
        print("Agent created with multiple tools registered.\n")

        # Create a thread for conversation continuity (optional - use if you want context across queries)
        thread = agent.get_new_thread()

        # Test with different queries
        print("Experiment: Testing multiple tools...\n")
        print("-" * 65)

        test_queries = [
            "Search the web for 'example domain'.",
            "Calculate the sum of 15 and 27.",
            "Send an email to admin@contoso.com with subject 'Meeting Reminder' and body 'Don't forget our meeting tomorrow at 10 AM.'",
            "Find some Python tutorials and create a list."
        ]
        
        for query in test_queries:
            result = await agent.run(query, thread=thread)
            print(f"\nUser: {query}")
            print(f"Agent: {result.text}")

        print("\n" + "-" * 65)
        print("Session completed. Resources cleaned up automatically.")
        
    # Resources (credential, agent) are automatically cleaned up here
    print("=" * 65)

        
if __name__ == "__main__":
    asyncio.run(main())