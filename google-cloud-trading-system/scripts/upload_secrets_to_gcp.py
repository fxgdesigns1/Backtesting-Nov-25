#!/usr/bin/env python3
"""
Upload all sensitive credentials to Google Cloud Secret Manager
This ensures secrets are NOT committed to Git
Includes duplicate detection and safe cleanup
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import Dict, Optional, List, Tuple

try:
    from google.cloud import secretmanager
except ImportError:
    print("❌ google-cloud-secret-manager not installed")
    print("   Install: pip install google-cloud-secret-manager")
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parents[1]

# Get project ID from environment, gcloud config, or prompt
def get_project_id():
    """Get Google Cloud Project ID from various sources"""
    # Try environment variables
    project_id = os.getenv('GOOGLE_CLOUD_PROJECT') or os.getenv('GCP_PROJECT_ID') or os.getenv('GCP_PROJECT')
    
    if project_id:
        return project_id
    
    # Try gcloud config
    try:
        result = subprocess.run(['gcloud', 'config', 'get-value', 'project'], 
                              capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    
    # Prompt user
    project_id = input("Enter your Google Cloud Project ID: ").strip()
    if not project_id:
        print("❌ Project ID required")
        sys.exit(1)
    return project_id

PROJECT_ID = get_project_id()

def load_env_file(file_path: Path) -> Dict[str, str]:
    """Load key-value pairs from .env file"""
    secrets = {}
    if not file_path.exists():
        return secrets
    
    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                # Only include actual secrets (not comments or empty values)
                if value and not value.startswith('your_'):
                    secrets[key] = value
    return secrets

def normalize_secret_name(name: str) -> str:
    """Normalize secret name for comparison (handles different formats)"""
    return name.lower().replace('_', '-').replace(' ', '-')

def find_duplicate_secrets(client: secretmanager.SecretManagerServiceClient,
                           project_id: str) -> Dict[str, List[str]]:
    """Find duplicate secrets (same normalized name)"""
    parent = f"projects/{project_id}"
    secrets = list(client.list_secrets(request={"parent": parent}))
    
    # Group by normalized name
    groups = {}
    for secret in secrets:
        secret_id = secret.name.split('/')[-1]
        normalized = normalize_secret_name(secret_id)
        
        if normalized not in groups:
            groups[normalized] = []
        groups[normalized].append(secret_id)
    
    # Return only groups with duplicates
    duplicates = {k: v for k, v in groups.items() if len(v) > 1}
    return duplicates

def get_secret_versions(client: secretmanager.SecretManagerServiceClient,
                        project_id: str, secret_id: str) -> List[Tuple[str, str]]:
    """Get all versions of a secret (version_id, create_time)"""
    parent = f"projects/{project_id}/secrets/{secret_id}"
    versions = list(client.list_secret_versions(request={"parent": parent}))
    
    version_info = []
    for version in versions:
        version_id = version.name.split('/')[-1]
        create_time = version.create_time.isoformat() if version.create_time else "unknown"
        version_info.append((version_id, create_time))
    
    # Sort by create_time (newest first)
    version_info.sort(key=lambda x: x[1], reverse=True)
    return version_info

def delete_secret_safely(client: secretmanager.SecretManagerServiceClient,
                        project_id: str, secret_id: str, keep_latest: bool = True) -> bool:
    """Safely delete a secret, keeping the latest version if requested"""
    try:
        secret_name = f"projects/{project_id}/secrets/{secret_id}"
        
        # Get versions
        versions = get_secret_versions(client, project_id, secret_id)
        
        if not versions:
            print(f"   ⚠️  No versions found for '{secret_id}'")
            return False
        
        if keep_latest and len(versions) > 1:
            # Delete all but the latest version
            latest_version = versions[0][0]
            for version_id, _ in versions[1:]:
                version_name = f"{secret_name}/versions/{version_id}"
                try:
                    client.destroy_secret_version(request={"name": version_name})
                    print(f"      ✓ Deleted old version: {version_id}")
                except Exception as e:
                    print(f"      ⚠️  Could not delete version {version_id}: {e}")
            print(f"   ✅ Kept latest version of '{secret_id}'")
            return True
        else:
            # Delete the entire secret (only if it's truly a duplicate)
            client.delete_secret(request={"name": secret_name})
            print(f"   ✅ Deleted duplicate secret '{secret_id}'")
            return True
            
    except Exception as e:
        print(f"   ❌ Error deleting '{secret_id}': {e}")
        return False

def cleanup_duplicates(client: secretmanager.SecretManagerServiceClient,
                      project_id: str) -> int:
    """Find and clean up duplicate secrets"""
    print("\n" + "="*80)
    print("CLEANING UP DUPLICATE SECRETS")
    print("="*80)
    
    duplicates = find_duplicate_secrets(client, project_id)
    
    if not duplicates:
        print("✅ No duplicates found - all secrets are unique")
        return 0
    
    print(f"\n⚠️  Found {len(duplicates)} duplicate groups:\n")
    
    deleted_count = 0
    
    for normalized_name, secret_ids in duplicates.items():
        print(f"📦 Group: {normalized_name}")
        print(f"   Variants: {', '.join(secret_ids)}")
        
        # Get values for each variant
        secret_values = {}
        for secret_id in secret_ids:
            try:
                version_name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
                response = client.access_secret_version(request={"name": version_name})
                value = response.payload.data.decode("UTF-8")
                secret_values[secret_id] = value
            except Exception as e:
                print(f"   ⚠️  Could not read '{secret_id}': {e}")
                secret_values[secret_id] = None
        
        # Find which one to keep (prefer standard format, or newest)
        # Standard format: lowercase with hyphens
        standard_format = normalized_name
        keep_secret = None
        
        for secret_id in secret_ids:
            if normalize_secret_name(secret_id) == standard_format:
                keep_secret = secret_id
                break
        
        if not keep_secret:
            # Keep the first one, delete others
            keep_secret = secret_ids[0]
        
        print(f"   ✅ Keeping: {keep_secret}")
        
        # Delete others
        for secret_id in secret_ids:
            if secret_id != keep_secret:
                # Check if values are the same
                if secret_values.get(secret_id) == secret_values.get(keep_secret):
                    print(f"   🗑️  Deleting duplicate '{secret_id}' (same value as '{keep_secret}')")
                    if delete_secret_safely(client, project_id, secret_id, keep_latest=False):
                        deleted_count += 1
                else:
                    print(f"   ⚠️  '{secret_id}' has different value - keeping both")
        
        print()
    
    return deleted_count

def check_secret_exists(client: secretmanager.SecretManagerServiceClient,
                        project_id: str, secret_id: str) -> bool:
    """Check if secret already exists"""
    try:
        secret_name = f"projects/{project_id}/secrets/{secret_id}"
        client.get_secret(request={"name": secret_name})
        return True
    except Exception:
        return False

def get_latest_secret_value(client: secretmanager.SecretManagerServiceClient,
                           project_id: str, secret_id: str) -> Optional[str]:
    """Get the latest value of an existing secret"""
    try:
        version_name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": version_name})
        return response.payload.data.decode("UTF-8")
    except Exception:
        return None

def create_or_update_secret(client: secretmanager.SecretManagerServiceClient, 
                           project_id: str, secret_id: str, secret_value: str,
                           skip_if_exists: bool = False):
    """Create or update a secret in Secret Manager"""
    parent = f"projects/{project_id}"
    secret_name = f"{parent}/secrets/{secret_id}"
    
    # Check if secret exists
    exists = check_secret_exists(client, project_id, secret_id)
    
    if exists:
        if skip_if_exists:
            existing_value = get_latest_secret_value(client, project_id, secret_id)
            if existing_value == secret_value:
                print(f"   ⏭️  Secret '{secret_id}' already exists with same value - skipping")
                return True
            else:
                print(f"   ⚠️  Secret '{secret_id}' exists with different value")
                print(f"      Existing: {existing_value[:10] if existing_value else 'None'}... (hidden)")
                print(f"      New:      {secret_value[:10]}... (hidden)")
                response = input(f"      Update? (yes/no): ").strip().lower()
                if response != 'yes':
                    print(f"   ⏭️  Skipping '{secret_id}'")
                    return False
        
        # Update existing secret
        print(f"   ✓ Secret '{secret_id}' exists, updating...")
        version = client.add_secret_version(
            request={
                "parent": secret_name,
                "payload": {"data": secret_value.encode("UTF-8")},
            }
        )
        print(f"   ✅ Updated secret '{secret_id}' (version: {version.name.split('/')[-1]})")
        return True
    else:
        # Create new secret
        print(f"   📝 Creating new secret '{secret_id}'...")
        secret = client.create_secret(
            request={
                "parent": parent,
                "secret_id": secret_id,
                "secret": {"replication": {"automatic": {}}},
            }
        )
        
        # Add version
        version = client.add_secret_version(
            request={
                "parent": secret.name,
                "payload": {"data": secret_value.encode("UTF-8")},
            }
        )
        print(f"   ✅ Created secret '{secret_id}' (version: {version.name.split('/')[-1]})")
        return True

def main():
    """Upload all secrets to Google Cloud Secret Manager"""
    print("="*80)
    print("UPLOADING SECRETS TO GOOGLE CLOUD SECRET MANAGER")
    print("="*80)
    
    print(f"\n📁 Project ID: {PROJECT_ID}")
    print(f"📂 Base Directory: {BASE_DIR}")
    
    try:
        client = secretmanager.SecretManagerServiceClient()
    except Exception as e:
        print(f"\n❌ Failed to connect to Secret Manager: {e}")
        print("\n💡 Make sure you're authenticated:")
        print("   gcloud auth application-default login")
        print("   OR")
        print("   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json")
        sys.exit(1)
    
    # Step 1: Clean up duplicates
    deleted = cleanup_duplicates(client, PROJECT_ID)
    if deleted > 0:
        print(f"\n✅ Cleaned up {deleted} duplicate secret(s)")
    
    # Step 2: Load secrets from .env files
    print("\n" + "="*80)
    print("LOADING SECRETS FROM FILES")
    print("="*80)
    
    env_files = [
        BASE_DIR / "oanda_config.env",
        BASE_DIR / "news_api_config.env",
        BASE_DIR / ".env",
    ]
    
    all_secrets = {}
    for env_file in env_files:
        if env_file.exists():
            print(f"\n📄 Loading: {env_file.name}")
            secrets = load_env_file(env_file)
            all_secrets.update(secrets)
            print(f"   ✅ Loaded {len(secrets)} secrets")
        else:
            print(f"\n⏭️  Skipping: {env_file.name} (not found)")
    
    if not all_secrets:
        print("\n⚠️  No secrets found in any .env files")
        print("   Make sure oanda_config.env or news_api_config.env exists")
        return
    
    # Step 3: Upload secrets
    print("\n" + "="*80)
    print("UPLOADING SECRETS")
    print("="*80)
    
    # Map critical keys to their secret names (lowercase with hyphens)
    critical_keys = {
        'OANDA_API_KEY': 'oanda-api-key',
        'OANDA_ACCOUNT_ID': 'oanda-account-id',
        'PRIMARY_ACCOUNT': 'primary-account',
        'ALPHA_VANTAGE_API_KEY': 'alpha-vantage-api-key',
        'NEWS_API_KEY': 'news-api-key',
        'FINNHUB_API_KEY': 'finnhub-api-key',
    }
    
    uploaded = 0
    skipped = 0
    
    for key, value in all_secrets.items():
        # Use standard format (lowercase with hyphens)
        if key in critical_keys:
            secret_id = critical_keys[key]
        else:
            secret_id = normalize_secret_name(key)
        
        print(f"\n🔑 {key} → {secret_id}")
        
        try:
            # Check if we should skip existing secrets
            if create_or_update_secret(client, PROJECT_ID, secret_id, value, skip_if_exists=True):
                uploaded += 1
            else:
                skipped += 1
        except KeyboardInterrupt:
            print(f"\n   ⏹️  Upload cancelled by user")
            break
        except Exception as e:
            print(f"   ❌ Failed to upload '{key}': {e}")
    
    print("\n" + "="*80)
    print("UPLOAD SUMMARY")
    print("="*80)
    print(f"✅ Uploaded: {uploaded}")
    print(f"⏭️  Skipped: {skipped}")
    print(f"🗑️  Duplicates cleaned: {deleted}")
    print("\n✅ Done!")

if __name__ == "__main__":
    main()

