"""Test script to send a message and process it."""
import os
import sys
import json
import asyncio
from dotenv import load_dotenv

# Load environment
env_path = os.path.join(os.path.dirname(__file__), '.env01')
load_dotenv(env_path)

from azure.servicebus import ServiceBusClient, ServiceBusMessage
from azure.identity import AzureCliCredential

def send_test_message():
    """Send a test capital call email to the queue."""
    namespace = os.environ.get('SERVICEBUS_NAMESPACE')
    credential = AzureCliCredential()
    
    print(f"Sending test message to {namespace}...")
    
    with ServiceBusClient(f"{namespace}.servicebus.windows.net", credential) as client:
        with client.get_queue_sender('email-intake') as sender:
            test_email = {
                'id': 'test-capital-call-007',
                'from': 'admin@horizonfund.com',
                'subject': 'Capital Call Notice - Q1 2026 - Horizon Growth Fund III',
                'receivedAt': '2026-01-27T10:00:00Z',
                'bodyText': '''Dear Limited Partner,

We hereby issue a capital call for Horizon Growth Fund III.

Capital Call Details:
- Fund: Horizon Growth Fund III
- Call Amount: EUR 2,500,000
- Due Date: February 15, 2026
- Wire Reference: HGF3-CC-Q1-2026

Please wire the funds to the account specified below:
Bank: Deutsche Bank AG
IBAN: DE89370400440532013000
BIC: DEUTDEDBFRA

Best regards,
Fund Administrator''',
                'hasAttachments': False,
                'bodyPreview': 'Capital Call Notice Q1 2026'
            }
            sender.send_messages(ServiceBusMessage(json.dumps(test_email)))
            print("✅ Test email sent to email-intake queue!")

async def process_message():
    """Process the message with the agent."""
    from src.agents.email_classifier_agent import EmailClassificationAgent
    
    print("\nStarting agent to process the message...")
    agent = EmailClassificationAgent()
    
    # Try to process
    result = await agent.process_next_email()
    
    if result:
        print("\n" + "=" * 60)
        print("✅ Email Processed Successfully!")
        print("=" * 60)
        print(f"Email ID: {result.get('email_id', 'N/A')}")
        print(f"Category: {result.get('category', 'N/A')}")
        print(f"Confidence: {result.get('confidence', 0):.1%}")
        print(f"Routed to: {result.get('routed_to', 'N/A')}")
        print("=" * 60)
    else:
        print("⚠️ No email found in queue")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--send":
        send_test_message()
    elif len(sys.argv) > 1 and sys.argv[1] == "--process":
        asyncio.run(process_message())
    else:
        # Send then process
        send_test_message()
        asyncio.run(process_message())
