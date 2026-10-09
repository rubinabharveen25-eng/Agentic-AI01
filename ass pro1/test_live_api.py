"""
test_live_api.py - Standalone optional script for testing real Google Gemini API connection.
Only runs against real Gemini API if GEMINI_API_KEY is configured in .env or passed via CLI.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

import config

def main():
    print("=" * 60)
    print("GOOGLE GEMINI API — LIVE CONNECTION TEST")
    print("=" * 60)

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "your_actual_key_here":
        print("\n[ERROR] GEMINI_API_KEY is missing or set to placeholder in .env.")
        print("Please set your real Gemini API key from https://aistudio.google.com/ in .env:")
        print("GEMINI_API_KEY=AIzaSy...")
        sys.exit(1)

    model = os.getenv("PLANNER_MODEL", "gemini-2.5-flash").strip()
    print(f"\nTarget Model: {model}")
    print(f"API Key: {config.mask_secret(api_key)}")
    print("Connecting to Google Gemini API...")

    success, msg, diag = config.test_api_connection(api_key=api_key, model=model)

    if success:
        print(f"\n[SUCCESS] {msg}")
        print(f"Diagnostic response: {diag}")
        sys.exit(0)
    else:
        print(f"\n[FAILURE] {msg}")
        if diag:
            print(f"Diagnostics: {diag}")
        sys.exit(1)

if __name__ == "__main__":
    main()
