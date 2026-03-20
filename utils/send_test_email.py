#!/usr/bin/env python3
"""Send a test email with PDF attachment to trigger the email Logic App."""

import asyncio
import base64
import sys
from pathlib import Path

import aiohttp

TENANT_ID = "2ce91bb1-0177-45b5-a98c-9c2f7ebe64de"
CLIENT_ID = "93350d2a-45d4-4bb0-bd21-5438c2f6cc7f"
MAILBOX = "admin@M365x66851375.onmicrosoft.com"

# PDF to attach
PDF_PATH = Path(__file__).parent.parent / "infrastructure" / "AZ_SERVICES.pdf"


async def get_token(client_secret: str) -> str:
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data) as resp:
            if resp.status != 200:
                print(f"Token error {resp.status}: {await resp.text()}")
                sys.exit(1)
            return (await resp.json())["access_token"]


async def send_email(token: str) -> None:
    pdf_bytes = PDF_PATH.read_bytes()
    pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")

    payload = {
        "message": {
            "subject": "Test PE Capital Call - Email Logic App Test",
            "body": {
                "contentType": "Text",
                "content": "This is an automated test email with a PDF attachment to verify the email ingestion Logic App pipeline.",
            },
            "toRecipients": [
                {"emailAddress": {"address": MAILBOX}}
            ],
            "attachments": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": "AZ_SERVICES.pdf",
                    "contentType": "application/pdf",
                    "contentBytes": pdf_b64,
                }
            ],
        },
        "saveToSentItems": "false",
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        url = f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/sendMail"
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 202:
                print(f"Email sent successfully to {MAILBOX}")
                print(f"  Subject: Test PE Capital Call - Email Logic App Test")
                print(f"  Attachment: AZ_SERVICES.pdf ({len(pdf_bytes)} bytes)")
            else:
                print(f"Send failed {resp.status}: {await resp.text()}")
                sys.exit(1)


async def main():
    if len(sys.argv) < 2:
        print("Usage: python send_test_email.py <client_secret>")
        sys.exit(1)
    secret = sys.argv[1]
    print("Acquiring token...")
    token = await get_token(secret)
    print("Token acquired. Sending email...")
    await send_email(token)


if __name__ == "__main__":
    asyncio.run(main())
