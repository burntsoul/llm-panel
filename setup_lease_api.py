#!/usr/bin/env python3
"""
Setup helper for lease + proxy API configuration.

Usage:
    python3 setup_lease_api.py --generate-token
    python3 setup_lease_api.py --show-config
    python3 setup_lease_api.py --init
"""

import os
import sys
import secrets
import argparse
from pathlib import Path

def generate_token(length: int = 32) -> str:
    """Generate a cryptographically secure random token."""
    return secrets.token_hex(length // 2)

def show_config():
    """Display environment variable configuration."""
    print("=== Lease + Proxy API Configuration ===\n")
    print("Add these to your .env file or system environment:\n")
    
    token = generate_token()
    
    config = {
        "LLM_AGENT_TOKEN": token,
        "LLM_BASE_URL": "http://192.168.8.33:11434",
        "LLM_READINESS_PATH": "/api/tags",
        "LEASE_DEFAULT_TTL": "3600",
        "LLM_READINESS_TIMEOUT": "120",
        "LLM_READINESS_POLL_INTERVAL": "2.0",
        "POWER_MODE": "Medium",
    }
    
    for key, value in config.items():
        print(f"export {key}=\"{value}\"")
    
    print("\n" + "="*50)
    print("Generated token (keep this secret):")
    print(f"  {token}")
    print("="*50 + "\n")

def update_secrets_file():
    """Update llm_secrets.py with token if not already configured."""
    secrets_path = Path(__file__).parent / "llm_secrets.py"
    
    if not secrets_path.exists():
        print(f"Warning: {secrets_path} not found")
        return
    
    content = secrets_path.read_text(encoding="utf-8")
    
    # Check if LLM_AGENT_TOKEN already set
    if "LLM_AGENT_TOKEN" in content:
        print("LLM_AGENT_TOKEN already configured in llm_secrets.py")
        return
    
    token = generate_token()
    
    # Add the token to the file
    new_content = content.rstrip() + f"\n\n# Lease + Proxy API\nLLM_AGENT_TOKEN = \"{token}\"\n"
    
    secrets_path.write_text(new_content, encoding="utf-8")
    
    print(f"Updated {secrets_path} with LLM_AGENT_TOKEN")
    print(f"Generated token: {token}")
    print("\nKeep this token secret! Use it in the Authorization header:")
    print(f"  Authorization: Bearer {token}\n")

def main():
    parser = argparse.ArgumentParser(
        description="Setup helper for lease + proxy API"
    )
    parser.add_argument(
        "--generate-token",
        action="store_true",
        help="Generate a new authentication token",
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Show environment variable configuration",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize by updating secrets file",
    )
    
    args = parser.parse_args()
    
    if not any([args.generate_token, args.show_config, args.init]):
        parser.print_help()
        return
    
    if args.generate_token:
        token = generate_token()
        print(f"Generated token: {token}")
    
    if args.show_config:
        show_config()
    
    if args.init:
        update_secrets_file()

if __name__ == "__main__":
    main()
