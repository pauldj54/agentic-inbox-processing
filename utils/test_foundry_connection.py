"""
Test AI Foundry connectivity and permissions.

Run locally to validate that the principal can create agents before
sending any test emails through the pipeline.

Usage:
    # Test with your own identity (interactive login):
    python -m utils.test_foundry_connection

    # Override endpoint / model:
    AZURE_AI_PROJECT_ENDPOINT=https://... AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-4o \
        python -m utils.test_foundry_connection

    # Simulate the web-app managed identity (requires az login as webapp MI):
    python -m utils.test_foundry_connection --managed-identity
"""

import argparse
import os
import sys

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.ai.agents import AgentsClient


def _get_config():
    endpoint = os.getenv(
        "AZURE_AI_PROJECT_ENDPOINT",
        "https://swc-ai-foundry-demos-fy26.services.ai.azure.com/api/projects/swcFirstProject",
    )
    model = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o")
    return endpoint, model


def _step(label: str, ok: bool, detail: str = ""):
    symbol = "PASS" if ok else "FAIL"
    msg = f"[{symbol}] {label}"
    if detail:
        msg += f"  ->  {detail}"
    print(msg)
    return ok


def run_checks(use_managed_identity: bool = False) -> bool:
    endpoint, model = _get_config()
    all_ok = True

    print("=" * 60)
    print("AI Foundry Connectivity & Permission Test")
    print("=" * 60)
    print(f"Endpoint : {endpoint}")
    print(f"Model    : {model}")
    print(f"Identity : {'ManagedIdentity' if use_managed_identity else 'DefaultAzureCredential'}")
    print("-" * 60)

    # --- Step 1: acquire a credential ---
    try:
        if use_managed_identity:
            cred = ManagedIdentityCredential()
        else:
            cred = DefaultAzureCredential()
        all_ok &= _step("Credential created", True)
    except Exception as exc:
        _step("Credential created", False, str(exc))
        return False

    # --- Step 2: get an access token for Cognitive Services ---
    try:
        token = cred.get_token("https://cognitiveservices.azure.com/.default")
        _step(
            "Access token acquired (cognitiveservices scope)",
            True,
            f"expires_on={token.expires_on}",
        )
    except Exception as exc:
        _step("Access token acquired", False, str(exc))
        all_ok = False

    # --- Step 3: instantiate AgentsClient ---
    try:
        client = AgentsClient(endpoint=endpoint, credential=cred)
        all_ok &= _step("AgentsClient instantiated", True)
    except Exception as exc:
        _step("AgentsClient instantiated", False, str(exc))
        return False

    # --- Step 4: create a minimal agent (the operation that fails) ---
    agent = None
    try:
        agent = client.create_agent(
            model=model,
            name="connectivity-test-agent",
            instructions="You are a connectivity test. Reply with 'OK'.",
        )
        all_ok &= _step(
            "create_agent succeeded",
            True,
            f"agent_id={agent.id}",
        )
    except Exception as exc:
        _step("create_agent succeeded", False, str(exc))
        all_ok = False

    # --- Step 5: create a thread + send a message + run ---
    if agent:
        try:
            thread = client.threads.create()
            client.messages.create(
                thread_id=thread.id,
                role="user",
                content="Say OK",
            )
            run = client.runs.create_and_process(
                thread_id=thread.id,
                agent_id=agent.id,
            )
            all_ok &= _step(
                "Run completed",
                run.status.value == "completed",
                f"status={run.status.value}",
            )

            # Fetch the response
            msgs = client.messages.list(thread_id=thread.id)
            assistant_msgs = [
                m for m in msgs if m.role.value == "assistant"
            ]
            if assistant_msgs:
                content = assistant_msgs[0].content[0].text.value
                all_ok &= _step("Model response received", True, repr(content[:80]))
            else:
                _step("Model response received", False, "no assistant message found")
                all_ok = False
        except Exception as exc:
            _step("Run completed", False, str(exc))
            all_ok = False

    # --- Step 6: cleanup ---
    if agent:
        try:
            client.delete_agent(agent.id)
            _step("Agent cleaned up", True)
        except Exception:
            _step("Agent cleaned up", False, "(non-critical)")

    print("-" * 60)
    print(f"Result: {'ALL CHECKS PASSED' if all_ok else 'SOME CHECKS FAILED'}")
    print("=" * 60)
    return all_ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test AI Foundry connection")
    parser.add_argument(
        "--managed-identity",
        action="store_true",
        help="Use ManagedIdentityCredential instead of DefaultAzureCredential",
    )
    args = parser.parse_args()
    ok = run_checks(use_managed_identity=args.managed_identity)
    sys.exit(0 if ok else 1)
