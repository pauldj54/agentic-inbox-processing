"""
Inbox Processing Agent
Uses Microsoft Graph API to read and process emails from Outlook mailboxes.
"""

import asyncio
from agent_framework import ChatAgent
from agent_framework.azure import AzureAIClient
from azure.identity import AzureCliCredential
from dotenv import load_dotenv

# Import Graph tools
from graph_tools import get_emails, get_email_body

# =============================================================================
# Configuration
# =============================================================================
# Target inbox email address (optional - if None, uses authenticated user's inbox)
TARGET_INBOX_EMAIL = None  # Set to specific email or leave None for "me"

# Number of recent emails to fetch by default
TOP_N_EMAILS = 10


async def main():
    load_dotenv('.env01')

    print("=" * 65)
    print("Inbox Processing Agent")
    print("=" * 65)
    print(f"[Config] Target inbox: {TARGET_INBOX_EMAIL or 'Authenticated user'}")
    print(f"[Config] Default email count: {TOP_N_EMAILS}")
    print("=" * 65)

    # Create Azure AI Client for the agent's LLM
    chat_client = AzureAIClient(credential=AzureCliCredential())
    print("[Client] AzureAIClient created.")

    # Create agent with Graph tools
    async with ChatAgent(
        name="InboxAgent",
        chat_client=chat_client,
        instructions="""You are a helpful agent that can read and process emails from Outlook mailboxes.

You have access to these tools:
1. get_emails - Retrieves a list of recent emails with metadata (subject, sender, date, preview)
2. get_email_body - Retrieves the full body content of a specific email by its ID

When asked to read emails:
1. First use get_emails to get a list of recent emails
2. Summarize the results showing subject, sender, has attachements, body summary (which requires you to summarized a concatenation of the email body and subject, and date for each.
3. If the user wants details on a specific email, use get_email_body with the email ID.

Always format email information clearly and be helpful in summarizing content.""",
        tools=[get_emails, get_email_body]
    ) as agent:
        print("[Agent] Agent created with Graph tools registered.")
        print(f"[Agent] Tools: get_emails, get_email_body")
        print()

        # Create a thread for conversation continuity
        thread = agent.get_new_thread()
        print("[Thread] New conversation thread created.")

        # Interactive loop or single query
        print("\n" + "-" * 65)
        print("[Ready] You can now ask the agent to read emails.")
        print("-" * 65)

        # Example query - fetch emails
        if TARGET_INBOX_EMAIL:
            query = f"Read the latest {TOP_N_EMAILS} emails from {TARGET_INBOX_EMAIL}. Show me a summary with subject, sender, has attachements, body summary (which requires you to summarized a concatenation of the email body and subject, and date"
        else:
            query = f"Read my latest {TOP_N_EMAILS} emails. Show me a summary with subject, sender, has attachements, body summary (which requires you to summarized a concatenation of the email body and subject, and date"

        print(f"\n[Query] {query}")
        print("[Processing] Fetching emails via Microsoft Graph...")
        
        result = await agent.run(query, thread=thread)
        print(f"\n[Agent Response]\n{result.text}")

        print("\n" + "-" * 65)
        print("[Session] Completed. Resources cleaned up automatically.")
        
    print("=" * 65)
    print("[Done] Inbox Agent finished.")

        
if __name__ == "__main__":
    asyncio.run(main())