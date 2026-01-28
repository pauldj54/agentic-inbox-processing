from agent_framework import ChatAgent
from agent_framework.azure import AzureAIClient
from dotenv import load_dotenv
from azure.identity import AzureCliCredential
from context_model import FavouriteColourMemory
import asyncio

load_dotenv('.env01')

async def main():
    print("=" * 65)
    print("Starting Simple Chat Agent with Long-Term Memory (context provider)...")
    print("=" * 65)

    chat_client = AzureAIClient(credential=AzureCliCredential())
    
    #Create the memory context provider
    mem_provider = FavouriteColourMemory(chat_client)

    # Create the agent and attach the memory context provider
    async with ChatAgent(
        name="Simple-Chat-Agent-With-Memory",
        chat_client=chat_client,
        instructions="You are a helpful assistant. Ask the user about their favourite colour if unknown.",
        context_providers=[mem_provider]
    ) as agent:
        # Create a new thread for the conversation
        thread = agent.get_new_thread()

        # Interact with the agent
        print("User: Hello, How are you?\n")
        response1 = await agent.run("Hello, How are you?", thread=thread)
        print("Agent Response:", response1.text)

        # User shares their favourite colour
        print("\nUser: My favourite colour is blue.\n")
        response2 = await agent.run("My favourite colour is blue.", thread=thread)
        print("Agent Response:", response2.text)

        # In the next interaction, the agent should remember the favourite colour
        print("\nUser: What is my favourite colour?\n") 
        response3 = await agent.run("What is my favourite colour?", thread=thread)
        print("\nAgent Response:\n", response3.text)

if __name__ == "__main__":
    asyncio.run(main())