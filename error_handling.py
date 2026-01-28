import asyncio
from agent_framework.azure import AzureAIClient
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv
from typing import Annotated
from pydantic import Field

# Mock weather API
USER_DATABASE = {
    1: {"name": "Alice", "email": "alice@example.com", "role": "Engineer"},
    2: {"name": "Bob", "email": "bob@example.com", "role": "Manager"},
    3: {"name": "Charlie", "email": "charlie@example.com", "role": "Analyst"}
}

# Define mocked database query tool function with error handling
def query_user_database(
        user_id: Annotated[int, Field(description="The ID of the user to look up in the database")]
        ) -> str:
    """ Mock function to query user database."""
    print("query_user_database tool is in action...")

    # Simulate a database lookup
    try:
        print(f"Looking up user with ID: {user_id}")
        # Input validation
        if not isinstance(user_id, int):
            return "[ERROR] Invalid user ID. It must be a positive integer."          
        if user_id <= 0:
            return "[ERROR] User ID must be a positive integer."
        if user_id > 1000000:
            return "[ERROR] Invalid user Id: number too large."
        # Simulate fetching user data
        if user_id in USER_DATABASE:
            print("User found in database.")
            user = USER_DATABASE[user_id]
            return f"User {user_id} info: Temp: {user['temp']}°C, Condition: {user['condition']}, Humidity: {user['humidity']}%"
        else:
            return "[NOT FOUND] User not found in the database."
    except ValueError as ve:
        return f"Input Error: {str(ve)}"

print("database query tool defined with error handling.")

async def main():   
    load_dotenv('.env01')
    print("=" * 65)
    print("Error handling demonstration...")
    print("=" * 65)

    # Use async context manager for proper resource lifecycle management
    async with (
        DefaultAzureCredential() as credential,
        AzureAIClient(credential=credential).create_agent(
            name="DatabaseAgent",
            instructions="You are a helpful agent that looks up user information from a database using the provided tool. Extract the user ID from the query and handle errors appropriately.",
            tools=[query_user_database]
        ) as agent,
    ):
        print("Agent created with multiple tools registered.\n")

        # Create a thread for conversation continuity (optional - use if you want context across queries)
        thread = agent.get_new_thread()

        # Test with different queries
        print("Experiment: Testing multiple tools...\n")
        print("-" * 65)

        test_queries = [
            ("Look up user 1","valid user - should succeed"),
            ("Find information about user 2","valid user - should succeed"),
            ("Get user 999","shoud return NOT FOUND"),
            ("Query user with Id -5","Invalied Id - should return ERROR"),
            ("What about user 0?","Invalied Id - should return ERROR"),
        ]

        for query, expected_behaviour in test_queries:
            print(f"\nUser: {query} (Expected: {expected_behaviour})")

            result = await agent.run(query, thread=thread)

            print(f"Agent: {result.text}")
        print("\n" + "-" * 65)

if __name__ == "__main__":
    asyncio.run(main())