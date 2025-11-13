#!/usr/bin/env python3
"""
Upload secrets using gcloud CLI (works without Application Default Credentials)
Includes duplicate detection and safe cleanup
"""

import subprocess
import sys
from pathlib import Path
import json
import re

BASE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ID = "ai-quant-trading"

def load_env_file(file_path: Path):
    """Load secrets from .env file"""
    secrets = {}
    if not file_path.exists():
        return secrets
    
    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if value and not value.startswith('your_'):
                secrets[key] = value
    return secrets

def normalize_secret_name(name: str) -> str:
    """Normalize secret name for comparison"""
    return name.lower().replace('_', '-').replace(' ', '-')

def get_existing_secrets():
    """Get list of existing secrets"""
    result = subprocess.run(
        ['gcloud', 'secrets', 'list', '--project', PROJECT_ID, '--format=json'],
        capture_output=True, text=True
    )
    
    if result.returncode != 0:
        print(f"❌ Error listing secrets: {result.stderr}")
        return []
    
    try:
        secrets = json.loads(result.stdout)
        return [s['name'].split('/')[-1] for s in secrets]
    except Exception as e:
        print(f"⚠️  Error parsing secrets: {e}")
        return []

def get_secret_value(secret_id: str) -> str:
    """Get the latest value of a secret"""
    result = subprocess.run(
        ['gcloud', 'secrets', 'versions', 'access', 'latest', 
         '--secret', secret_id, '--project', PROJECT_ID],
        capture_output=True, text=True
    )
    
    if result.returncode == 0:
        return result.stdout.strip()
    return ""

def delete_secret(secret_id: str) -> bool:
    """Delete a secret"""
    result = subprocess.run(
        ['gcloud', 'secrets', 'delete', secret_id, '--project', PROJECT_ID, '--quiet'],
        capture_output=True, text=True
    )
    return result.returncode == 0

def create_secret(secret_id: str, value: str) -> bool:
    """Create a new secret"""
    # Create secret
    result = subprocess.run(
        ['gcloud', 'secrets', 'create', secret_id, 
         '--project', PROJECT_ID, '--replication-policy=automatic', '--quiet'],
        capture_output=True, text=True
    )
    
    if result.returncode != 0 and "already exists" not in result.stderr.lower():
        print(f"   ⚠️  Secret might already exist: {result.stderr[:100]}")
    
    # Add version
    process = subprocess.Popen(
        ['gcloud', 'secrets', 'versions', 'add', secret_id, 
         '--project', PROJECT_ID, '--data-file=-'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = process.communicate(input=value)
    
    return process.returncode == 0

def update_secret(secret_id: str, value: str) -> bool:
    """Add a new version to existing secret"""
    process = subprocess.Popen(
        ['gcloud', 'secrets', 'versions', 'add', secret_id, 
         '--project', PROJECT_ID, '--data-file=-'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = process.communicate(input=value)
    return process.returncode == 0

def find_duplicates(existing_secrets):
    """Find duplicate secrets (same normalized name)"""
    groups = {}
    for secret_id in existing_secrets:
        normalized = normalize_secret_name(secret_id)
        if normalized not in groups:
            groups[normalized] = []
        groups[normalized].append(secret_id)
    
    return {k: v for k, v in groups.items() if len(v) > 1}

def cleanup_duplicates(existing_secrets):
    """Clean up duplicate secrets"""
    duplicates = find_duplicates(existing_secrets)
    
    if not duplicates:
        print("✅ No duplicates found")
        return 0
    
    print(f"\n⚠️  Found {len(duplicates)} duplicate groups:\n")
    
    deleted = 0
    for normalized, variants in duplicates.items():
        print(f"📦 Group: {normalized}")
        print(f"   Variants: {', '.join(variants)}")
        
        # Get values
        values = {}
        for variant in variants:
            val = get_secret_value(variant)
            values[variant] = val
        
        # Determine which to keep (prefer standard format)
        keep = None
        for variant in variants:
            if normalize_secret_name(variant) == normalized:
                keep = variant
                break
        
        if not keep:
            keep = variants[0]
        
        print(f"   ✅ Keeping: {keep}")
        
        # Delete others if same value
        for variant in variants:
            if variant != keep:
                if values.get(variant) == values.get(keep):
                    print(f"   🗑️  Deleting duplicate '{variant}' (same value)")
                    if delete_secret(variant):
                        deleted += 1
                        print(f"      ✅ Deleted")
                    else:
                        print(f"      ❌ Failed to delete")
                else:
                    print(f"   ⚠️  '{variant}' has different value - keeping both")
        print()
    
    return deleted

def main():
    print("="*80)
    print("UPLOADING SECRETS TO GOOGLE CLOUD SECRET MANAGER")
    print("="*80)
    print(f"\n📁 Project: {PROJECT_ID}")
    print(f"📂 Base Directory: {BASE_DIR}\n")
    
    # Load secrets
    oanda_file = BASE_DIR / "oanda_config.env"
    news_file = BASE_DIR / "news_api_config.env"
    
    all_secrets = {}
    if oanda_file.exists():
        print(f"📄 Loading: {oanda_file.name}")
        all_secrets.update(load_env_file(oanda_file))
    if news_file.exists():
        print(f"📄 Loading: {news_file.name}")
        all_secrets.update(load_env_file(news_file))
    
    if not all_secrets:
        print("❌ No secrets found in .env files")
        return
    
    print(f"✅ Loaded {len(all_secrets)} secrets\n")
    
    # Get existing secrets
    print("📋 Checking existing secrets...")
    existing_secrets = set(get_existing_secrets())
    print(f"   Found {len(existing_secrets)} existing secrets\n")
    
    # Clean up duplicates
    deleted = cleanup_duplicates(existing_secrets)
    if deleted > 0:
        print(f"✅ Cleaned up {deleted} duplicate secret(s)\n")
        # Refresh existing secrets
        existing_secrets = set(get_existing_secrets())
    
    # Map keys to secret names
    critical_keys = {
        'OANDA_API_KEY': 'oanda-api-key',
        'OANDA_ACCOUNT_ID': 'oanda-account-id',
        'PRIMARY_ACCOUNT': 'primary-account',
        'ALPHA_VANTAGE_API_KEY': 'alpha-vantage-api-key',
        'NEWS_API_KEY': 'news-api-key',
        'FINNHUB_API_KEY': 'finnhub-api-key',
        'TELEGRAM_TOKEN': 'telegram-token',
        'TELEGRAM_CHAT_ID': 'telegram-chat-id',
    }
    
    # Upload secrets
    print("="*80)
    print("UPLOADING SECRETS")
    print("="*80)
    
    uploaded = 0
    skipped = 0
    updated = 0
    
    for key, value in sorted(all_secrets.items()):
        secret_id = critical_keys.get(key, normalize_secret_name(key))
        
        print(f"\n🔑 {key} → {secret_id}")
        
        if secret_id in existing_secrets:
            # Check if value is same
            existing_value = get_secret_value(secret_id)
            if existing_value == value:
                print(f"   ⏭️  Already exists with same value - skipping")
                skipped += 1
            else:
                print(f"   ⚠️  Exists with different value")
                print(f"      Existing: {existing_value[:10] if existing_value else 'None'}...")
                print(f"      New:      {value[:10]}...")
                response = input(f"      Update? (yes/no): ").strip().lower()
                if response == 'yes':
                    if update_secret(secret_id, value):
                        print(f"   ✅ Updated")
                        updated += 1
                    else:
                        print(f"   ❌ Failed to update")
                else:
                    print(f"   ⏭️  Skipped")
                    skipped += 1
        else:
            # Create new
            if create_secret(secret_id, value):
                print(f"   ✅ Created")
                uploaded += 1
            else:
                print(f"   ❌ Failed to create")
    
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print(f"✅ Created: {uploaded}")
    print(f"🔄 Updated: {updated}")
    print(f"⏭️  Skipped: {skipped}")
    print(f"🗑️  Duplicates cleaned: {deleted}")
    print("\n✅ Done!")

if __name__ == "__main__":
    main()

