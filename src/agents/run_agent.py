#!/usr/bin/env python3
"""
Email Classification Agent Runner
==================================
Script to run the email classification agent locally.
Processes emails from the intake queue and routes them based on classification.

Usage:
    python run_agent.py [--once] [--max-emails N]

Options:
    --once          Process one email and exit
    --max-emails N  Process at most N emails before exiting (default: unlimited)
"""

import asyncio
import argparse
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agents.email_classifier_agent import EmailClassificationAgent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)

# Suppress noisy Azure SDK credential-chain debug/info messages
logging.getLogger("azure.identity").setLevel(logging.WARNING)
logging.getLogger("azure.servicebus").setLevel(logging.WARNING)
logging.getLogger("azure.core").setLevel(logging.WARNING)


def load_environment():
    """Load environment variables from .env files."""
    # Try multiple .env file locations
    env_files = [
        Path(__file__).parent.parent.parent / '.env',
        Path(__file__).parent.parent.parent / '.env01',
        Path(__file__).parent.parent.parent / '.env.local',
    ]
    
    for env_file in env_files:
        if env_file.exists():
            logger.info(f"Loading environment from: {env_file}")
            load_dotenv(env_file)
            break
    else:
        logger.warning("No .env file found. Using system environment variables.")
    
    # Validate required environment variables
    required_vars = [
        'SERVICEBUS_NAMESPACE',
        'COSMOS_ENDPOINT',
        'DOCUMENT_INTELLIGENCE_ENDPOINT',
        'AZURE_AI_PROJECT_ENDPOINT',
    ]
    
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        logger.info("Please set these in your .env file or environment:")
        for var in missing:
            logger.info(f"  - {var}")
        sys.exit(1)

    # ── Pipeline mode configuration (optional — defaults to "full") ──
    VALID_PIPELINE_MODES = {"full", "triage-only"}
    raw_mode = os.getenv("PIPELINE_MODE")
    if raw_mode is None:
        logger.warning("PIPELINE_MODE not set. Defaulting to 'full' (full-pipeline mode).")
        os.environ["PIPELINE_MODE"] = "full"
    else:
        pipeline_mode = raw_mode.strip().lower()
        if pipeline_mode not in VALID_PIPELINE_MODES:
            logger.error(
                f"Invalid PIPELINE_MODE '{raw_mode}'. "
                f"Valid: {VALID_PIPELINE_MODES}. Defaulting to 'full'."
            )
            os.environ["PIPELINE_MODE"] = "full"
        else:
            os.environ["PIPELINE_MODE"] = pipeline_mode

    # Triage-complete queue name (default: "triage-complete")
    if not os.getenv("TRIAGE_COMPLETE_QUEUE"):
        os.environ["TRIAGE_COMPLETE_QUEUE"] = "triage-complete"

    # Other queue names (with defaults)
    if not os.getenv("HUMAN_REVIEW_QUEUE"):
        os.environ["HUMAN_REVIEW_QUEUE"] = "human-review"
    if not os.getenv("ARCHIVAL_PENDING_QUEUE"):
        os.environ["ARCHIVAL_PENDING_QUEUE"] = "archival-pending"
    if not os.getenv("DISCARDED_QUEUE"):
        os.environ["DISCARDED_QUEUE"] = "discarded"

    # Optional external Service Bus namespace for triage-complete queue (no default)


async def run_agent_loop(max_emails: int = None, once: bool = False):
    """
    Main agent processing loop.
    
    Args:
        max_emails: Maximum number of emails to process (None = unlimited)
        once: If True, process one email and exit
    """
    logger.info("=" * 60)
    logger.info("Email Classification Agent Starting")
    logger.info("=" * 60)
    
    agent = EmailClassificationAgent()
    processed_count = 0
    
    try:
        while True:
            # Check if we've hit the maximum
            if max_emails and processed_count >= max_emails:
                logger.info(f"Reached maximum email count ({max_emails}). Exiting.")
                break
            
            if once and processed_count >= 1:
                logger.info("Processed one email (--once mode). Exiting.")
                break
            
            # Process next email
            logger.info("-" * 40)
            logger.info(f"Waiting for next email from intake queue...")
            
            result = await agent.process_next_email()
            
            if result:
                processed_count += 1
                logger.info(f"✅ Processed email #{processed_count}")
                logger.info(f"   Email ID: {result.get('email_id', 'N/A')}")
                logger.info(f"   Category: {result.get('category', 'N/A')}")
                logger.info(f"   Confidence: {result.get('confidence', 0):.1%}")
                logger.info(f"   Routed to: {result.get('routed_to', 'N/A')}")
            else:
                # No message received - wait before polling again
                logger.debug("No emails in queue. Waiting 10 seconds...")
                await asyncio.sleep(10)
                
    except KeyboardInterrupt:
        logger.info("\n⚠️ Interrupted by user. Shutting down...")
    except Exception as e:
        logger.error(f"❌ Agent error: {e}", exc_info=True)
        raise
    finally:
        logger.info("=" * 60)
        logger.info(f"Agent stopped. Processed {processed_count} emails.")
        logger.info("=" * 60)


def main():
    """Entry point for the agent runner."""
    parser = argparse.ArgumentParser(
        description='Email Classification Agent Runner',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run continuously (production mode)
    python run_agent.py
    
    # Process one email and exit (testing)
    python run_agent.py --once
    
    # Process 5 emails then exit
    python run_agent.py --max-emails 5
        """
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Process one email and exit'
    )
    parser.add_argument(
        '--max-emails',
        type=int,
        default=None,
        help='Maximum number of emails to process'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    
    args = parser.parse_args()
    
    # Set log level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Load environment
    load_environment()
    
    # Log configuration
    logger.info("Configuration:")
    logger.info(f"  Service Bus: {os.getenv('SERVICEBUS_NAMESPACE')}")
    logger.info(f"  Cosmos DB: {os.getenv('COSMOS_ENDPOINT')}")
    logger.info(f"  Document Intelligence: {os.getenv('DOCUMENT_INTELLIGENCE_ENDPOINT')}")
    logger.info(f"  AI Project: {os.getenv('AZURE_AI_PROJECT_ENDPOINT')}")
    logger.info(f"  Pipeline Mode: {os.getenv('PIPELINE_MODE', 'full')}")
    logger.info(f"  Triage Queue: {os.getenv('TRIAGE_COMPLETE_QUEUE', 'triage-complete')}")
    triage_ns = os.getenv("TRIAGE_COMPLETE_SB_NAMESPACE")
    if triage_ns:
        logger.info(f"  Triage SB Namespace: {triage_ns} (external)")
    logger.info(f"  Mode: {'Once' if args.once else 'Continuous'}")
    if args.max_emails:
        logger.info(f"  Max Emails: {args.max_emails}")
    
    # Run the agent loop
    asyncio.run(run_agent_loop(
        max_emails=args.max_emails,
        once=args.once
    ))


if __name__ == '__main__':
    main()
