"""
Test agent using Azure AI Agent Framework with Bing grounding and DevUI.
Creates a persistent agent in Azure AI Foundry that shows in the portal.
"""

import os
import logging
from dotenv import load_dotenv

from agent_framework import ChatAgent, HostedWebSearchTool
from agent_framework_azure_ai import AzureAIAgentClient
from agent_framework_devui import serve
from azure.identity.aio import AzureCliCredential

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv('.env01')

PROJECT_ENDPOINT = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
MODEL_DEPLOYMENT = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
BING_CONNECTION_ID = os.getenv("BING_CONNECTION_ID")

print(f"Project Endpoint: {PROJECT_ENDPOINT}")
print(f"Model Deployment: {MODEL_DEPLOYMENT}")
print(f"Bing Connection ID: {BING_CONNECTION_ID}")


def main():
    """Create agent with Bing grounding and launch DevUI."""
    logger.info("Creating Bing grounding search tool...")
    
    # Create Bing grounding search tool with connection ID
    # The connection_id links to the Bing grounding resource in Azure AI Foundry
    bing_search_tool = HostedWebSearchTool(
        description="Search the web for current information using Bing grounding. Use this tool to verify facts and find up-to-date information.",
    )
    # Set the connection_id as an additional property for Azure AI
    bing_search_tool.connection_id = BING_CONNECTION_ID

    logger.info("Creating Azure AI Agent client...")
    
    # Create the Azure AI Agent client - this connects to Azure AI Foundry
    # and will create/manage agents in the service
    client = AzureAIAgentClient(
        project_endpoint=PROJECT_ENDPOINT,
        model_deployment_name=MODEL_DEPLOYMENT,
        credential=AzureCliCredential(),
        agent_name="Fact-Checking-Agent-DevUI",
        agent_description="An agent that assists journalists in fact-checking articles using Bing search.",
        should_cleanup_agent=False,  # Keep agent persistent in Azure AI Foundry
        env_file_path=".env01",
    )

    logger.info("Creating ChatAgent with Bing grounding tool...")
    
    # Create the ChatAgent with the client and Bing tool
    agent = ChatAgent(
        chat_client=client,
        name="Fact-Checking-Agent",
        description="An agent that assists journalists in fact-checking articles.",
        instructions=(
            "You are a helpful assistant that helps journalists fact-check articles "
            "by providing accurate and reliable information. Use the Bing search tool "
            "to find up-to-date information when fact-checking claims. Always cite your sources."
        ),
        tools=[bing_search_tool],
    )

    logger.info(f"Agent created: {agent.name}")
    logger.info("Starting DevUI at http://localhost:8090")

    # Launch DevUI with the agent
    serve(
        entities=[agent],
        port=8090,
        auto_open=True,
        instrumentation_enabled=True,  # Enable tracing for tool call visibility
    )


if __name__ == "__main__":
    main()
