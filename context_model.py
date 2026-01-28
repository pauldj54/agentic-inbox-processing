from pydantic import BaseModel
from agent_framework import ContextProvider, Context 

class UserMemory(BaseModel):
    favourite_colour: str | None = None

class FavouriteColourMemory(ContextProvider):
    def __init__(self, chat_client, memory: UserMemory = None):
        self.chat_client = chat_client
        self.memory = memory or UserMemory()

    async def invoking(self, messages, **kwargs) -> Context:
        # Extract user messages to find favourite colour
        print("[Memory] Invoking FavouriteColourMemory...")
        # Before the agent responds: provide context about what we know
        instructions = []
        if self.memory.favourite_colour:
            instructions.append(f"The user's favourite colour is {self.memory.favourite_colour}.")
            print(f"[Memory] Retrieved known favourite colour: {self.memory.favourite_colour}")
        else:
            instructions.append("The user's favourite colour is unknown.")
        return Context(instructions="\n".join(instructions))
    
    async def invoked(self, request_messages, response_messages=None, invoke_exception=None, **kwargs) -> None:
        print("[Memory] Invoked FavouriteColourMemory: updating favourite colour if mentioned...")
        # After the agent responds: update memory if user mentioned their favourite colour
        for msg in request_messages:
            print(f"[Memory] Inspecting message from role {msg.role.value}: {msg.text}")
            if msg.role.value == 'user' and"my favourite colour is" in msg.text.lower():
                colour = msg.text.lower().split("my favourite colour is")[-1].strip().rstrip('.')
                if colour:
                    self.memory.favourite_colour = colour.capitalize()
                print(f"[Memory] Updated favourite colour to: {self.memory.favourite_colour}")
