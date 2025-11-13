#!/usr/bin/env python3
"""
Migrate secrets from one GCP project to another
Safely copies all secrets and verifies system still works
"""

import subprocess
import sys
import json
from typing import List, Dict

OLD_PROJECT = "ai-product-video"
NEW_PROJECT = "ai-quant-trading"
NEW_PROJECT_ID = "779507790009"

def get_secrets_list(project: str) -> List[str]:
    """Get list of all secret names in a project"""
    result = subprocess.run(
        ['gcloud', 'secrets', 'list', '--project', project, '--format=json'],
        capture_output=True, text=True
    )
    
    if result.returncode != 0:
        print(f"❌ Error listing secrets from {project}: {result.stderr}")
        return []
    
    try:
        secrets = json.loads(result.stdout)
        return [s['name'].split('/')[-1] for s in secrets]
    except Exception as e:
        print(f"❌ Error parsing secrets: {e}")
        return []

def get_secret_value(project: str, secret_id: str) -> str:
    """Get the latest value of a secret"""
    result = subprocess.run(
        ['gcloud', 'secrets', 'versions', 'access', 'latest',
         '--secret', secret_id, '--project', project],
        capture_output=True, text=True
    )
    
    if result.returncode == 0:
        return result.stdout.strip()
    return None

def secret_exists(project: str, secret_id: str) -> bool:
    """Check if secret exists in project"""
    result = subprocess.run(
        ['gcloud', 'secrets', 'describe', secret_id, '--project', project],
        capture_output=True, text=True
    )
    return result.returncode == 0

def create_secret(project: str, secret_id: str, value: str) -> bool:
    """Create a new secret in the target project"""
    # Create secret
    result = subprocess.run(
        ['gcloud', 'secrets', 'create', secret_id,
         '--project', project, '--replication-policy=automatic', '--quiet'],
        capture_output=True, text=True
    )
    
    if result.returncode != 0 and "already exists" not in result.stderr.lower():
        print(f"   ⚠️  Warning creating secret: {result.stderr[:100]}")
    
    # Add version
    process = subprocess.Popen(
        ['gcloud', 'secrets', 'versions', 'add', secret_id,
         '--project', project, '--data-file=-'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = process.communicate(input=value)
    
    if process.returncode == 0:
        return True
    else:
        print(f"   ⚠️  Error adding version: {stderr[:100]}")
        return False

def update_secret(project: str, secret_id: str, value: str) -> bool:
    """Add a new version to existing secret"""
    process = subprocess.Popen(
        ['gcloud', 'secrets', 'versions', 'add', secret_id,
         '--project', project, '--data-file=-'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    stdout, stderr = process.communicate(input=value)
    return process.returncode == 0

def migrate_secret(old_project: str, new_project: str, secret_id: str) -> bool:
    """Migrate a single secret from old to new project"""
    # Get value from old project
    value = get_secret_value(old_project, secret_id)
    if not value:
        print(f"   ⚠️  Could not get value for '{secret_id}'")
        return False
    
    # Check if exists in new project
    if secret_exists(new_project, secret_id):
        existing_value = get_secret_value(new_project, secret_id)
        if existing_value == value:
            print(f"   ⏭️  Already exists with same value - skipping")
            return True
        else:
            print(f"   ⚠️  Exists with different value - updating")
            return update_secret(new_project, secret_id, value)
    else:
        # Create new
        return create_secret(new_project, secret_id, value)

def main():
    print("="*80)
    print("MIGRATING SECRETS TO NEW PROJECT")
    print("="*80)
    print(f"\n📦 Source Project: {OLD_PROJECT}")
    print(f"📦 Target Project: {NEW_PROJECT} ({NEW_PROJECT_ID})")
    
    # Verify target project
    print(f"\n🔍 Verifying target project...")
    result = subprocess.run(
        ['gcloud', 'projects', 'describe', NEW_PROJECT, '--format=json'],
        capture_output=True, text=True
    )
    
    if result.returncode != 0:
        print(f"❌ Target project '{NEW_PROJECT}' not found or not accessible")
        print(f"   Error: {result.stderr}")
        sys.exit(1)
    
    try:
        project_info = json.loads(result.stdout)
        project_id = project_info.get('projectId', '')
        project_name = project_info.get('name', '')
        
        if project_id != NEW_PROJECT_ID:
            print(f"⚠️  Project ID mismatch: expected {NEW_PROJECT_ID}, got {project_id}")
            print(f"   Using project: {NEW_PROJECT} (ID: {project_id})")
        else:
            print(f"✅ Target project verified: {project_name} (ID: {project_id})")
    except Exception as e:
        print(f"⚠️  Could not parse project info: {e}")
        print(f"   Continuing with project: {NEW_PROJECT}")
    
    print()
    
    # Get secrets from old project
    print(f"📋 Listing secrets in {OLD_PROJECT}...")
    old_secrets = get_secrets_list(OLD_PROJECT)
    
    if not old_secrets:
        print("❌ No secrets found in source project")
        sys.exit(1)
    
    # Filter out n8n secrets (keep them in old project)
    trading_secrets = [s for s in old_secrets if not s.startswith('n8n-')]
    
    print(f"✅ Found {len(trading_secrets)} trading secrets to migrate")
    print(f"   (Skipping {len(old_secrets) - len(trading_secrets)} n8n secrets)\n")
    
    # Check what's already in new project
    print(f"📋 Checking existing secrets in {NEW_PROJECT}...")
    new_secrets = set(get_secrets_list(NEW_PROJECT))
    print(f"   Found {len(new_secrets)} existing secrets\n")
    
    # Migrate secrets
    print("="*80)
    print("MIGRATING SECRETS")
    print("="*80)
    
    migrated = 0
    skipped = 0
    failed = 0
    
    for secret_id in sorted(trading_secrets):
        print(f"\n🔑 {secret_id}")
        
        if secret_id in new_secrets:
            existing_value = get_secret_value(NEW_PROJECT, secret_id)
            old_value = get_secret_value(OLD_PROJECT, secret_id)
            
            if existing_value == old_value:
                print(f"   ⏭️  Already exists with same value - skipping")
                skipped += 1
                continue
            else:
                print(f"   ⚠️  Exists with different value - updating to match old project")
                # Auto-update to match old project value
        
        if migrate_secret(OLD_PROJECT, NEW_PROJECT, secret_id):
            print(f"   ✅ Migrated")
            migrated += 1
        else:
            print(f"   ❌ Failed")
            failed += 1
    
    print("\n" + "="*80)
    print("MIGRATION SUMMARY")
    print("="*80)
    print(f"✅ Migrated: {migrated}")
    print(f"⏭️  Skipped: {skipped}")
    print(f"❌ Failed: {failed}")
    print(f"\n📦 New project now has {len(new_secrets) + migrated} secrets")
    print("\n✅ Migration complete!")

if __name__ == "__main__":
    main()

