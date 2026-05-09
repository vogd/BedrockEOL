#!/usr/bin/env python3
"""Cross-reference Bedrock inference profiles with model lifecycle data."""
import json
import sys
from datetime import datetime, date

# Lifecycle data from https://docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle.html
LEGACY_MODELS = [
    {"model_id": "amazon.nova-canvas-v1:0", "model_name": "Nova Canvas", "provider": "Amazon", "legacy_date": "2026-03-30", "eol_date": "2026-09-30", "ext_date": ""},
    {"model_id": "amazon.nova-reel-v1:0", "model_name": "Nova Reel", "provider": "Amazon", "legacy_date": "2026-03-30", "eol_date": "2026-09-30", "ext_date": ""},
    {"model_id": "amazon.nova-reel-v1:1", "model_name": "Nova Reel", "provider": "Amazon", "legacy_date": "2026-03-30", "eol_date": "2026-09-30", "ext_date": ""},
    {"model_id": "amazon.nova-premier-v1:0", "model_name": "Nova Premier", "provider": "Amazon", "legacy_date": "2026-03-13", "eol_date": "2026-09-14", "ext_date": ""},
    {"model_id": "amazon.nova-sonic-v1:0", "model_name": "Nova Sonic", "provider": "Amazon", "legacy_date": "2026-03-13", "eol_date": "2026-09-14", "ext_date": ""},
    {"model_id": "amazon.titan-image-generator-v2:0", "model_name": "Titan Image Generator G1 v2", "provider": "Amazon", "legacy_date": "2025-12-30", "eol_date": "2026-06-30", "ext_date": ""},
    {"model_id": "amazon.titan-tg1-large", "model_name": "Titan Text Large", "provider": "Amazon", "legacy_date": "2025-02-08", "eol_date": "2025-10-27", "ext_date": ""},
    {"model_id": "anthropic.claude-3-haiku-20240307-v1:0", "model_name": "Claude 3 Haiku", "provider": "Anthropic", "legacy_date": "2026-03-10", "eol_date": "2026-09-10", "ext_date": "2026-06-10"},
    {"model_id": "anthropic.claude-3-sonnet-20240229-v1:0", "model_name": "Claude 3 Sonnet", "provider": "Anthropic", "legacy_date": "2026-01-30", "eol_date": "2026-07-30", "ext_date": "2026-04-30"},
    {"model_id": "anthropic.claude-3-5-sonnet-20240620-v1:0", "model_name": "Claude 3.5 Sonnet", "provider": "Anthropic", "legacy_date": "2026-01-30", "eol_date": "2026-07-30", "ext_date": "2026-04-30"},
    {"model_id": "anthropic.claude-3-5-sonnet-20241022-v2:0", "model_name": "Claude 3.5 Sonnet v2", "provider": "Anthropic", "legacy_date": "2026-01-30", "eol_date": "2026-07-30", "ext_date": "2026-04-30"},
    {"model_id": "anthropic.claude-3-7-sonnet-20250219-v1:0", "model_name": "Claude 3.7 Sonnet", "provider": "Anthropic", "legacy_date": "2025-10-28", "eol_date": "2026-04-28", "ext_date": "2026-01-27"},
    {"model_id": "anthropic.claude-3-5-haiku-20241022-v1:0", "model_name": "Claude 3.5 Haiku", "provider": "Anthropic", "legacy_date": "2025-12-19", "eol_date": "2026-06-19", "ext_date": "2026-03-19"},
    {"model_id": "anthropic.claude-opus-4-20250514-v1:0", "model_name": "Claude Opus 4", "provider": "Anthropic", "legacy_date": "2025-10-01", "eol_date": "2026-05-31", "ext_date": "2026-03-01"},
    {"model_id": "anthropic.claude-sonnet-4-20250514-v1:0", "model_name": "Claude Sonnet 4", "provider": "Anthropic", "legacy_date": "2026-04-14", "eol_date": "2026-10-14", "ext_date": "2026-07-14"},
    {"model_id": "cohere.command-r-v1:0", "model_name": "Command R", "provider": "Cohere", "legacy_date": "2026-02-19", "eol_date": "2026-08-19", "ext_date": "2026-05-19"},
    {"model_id": "cohere.command-r-plus-v1:0", "model_name": "Command R+", "provider": "Cohere", "legacy_date": "2026-02-19", "eol_date": "2026-08-19", "ext_date": "2026-05-19"},
    {"model_id": "meta.llama3-1-405b-instruct-v1:0", "model_name": "Llama 3.1 405B Instruct", "provider": "Meta", "legacy_date": "2026-01-07", "eol_date": "2026-07-07", "ext_date": "2026-04-07"},
    {"model_id": "meta.llama3-2-11b-instruct-v1:0", "model_name": "Llama 3.2 11B Instruct", "provider": "Meta", "legacy_date": "2026-01-07", "eol_date": "2026-07-07", "ext_date": "2026-04-07"},
    {"model_id": "meta.llama3-2-1b-instruct-v1:0", "model_name": "Llama 3.2 1B Instruct", "provider": "Meta", "legacy_date": "2026-01-07", "eol_date": "2026-07-07", "ext_date": "2026-04-07"},
    {"model_id": "meta.llama3-2-3b-instruct-v1:0", "model_name": "Llama 3.2 3B Instruct", "provider": "Meta", "legacy_date": "2026-01-07", "eol_date": "2026-07-07", "ext_date": "2026-04-07"},
    {"model_id": "meta.llama3-2-90b-instruct-v1:0", "model_name": "Llama 3.2 90B Instruct", "provider": "Meta", "legacy_date": "2026-01-07", "eol_date": "2026-07-07", "ext_date": "2026-04-07"},
]

def extract_model_id(model_arn):
    """Extract model ID from ARN like arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-haiku-20240307-v1:0"""
    if '/foundation-model/' in model_arn:
        return model_arn.split('/foundation-model/')[-1]
    return model_arn.split('/')[-1]

def load_profiles(path):
    """Load JSONL profiles, deduplicate by profile resource_id"""
    profiles = {}
    with open(path) as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                key = (rec['resource_id'], rec['model_arn'])
                profiles[key] = rec
    return list(profiles.values())

def main():
    today = date(2026, 5, 8)
    profiles = load_profiles(sys.argv[1] if len(sys.argv) > 1 else 'profiles.json')

    # Build lookup: model_id -> lifecycle info
    lifecycle_lookup = {m['model_id']: m for m in LEGACY_MODELS}

    # Match profiles to lifecycle
    at_risk = []
    for p in profiles:
        mid = extract_model_id(p['model_arn'])
        if mid in lifecycle_lookup:
            lc = lifecycle_lookup[mid]
            eol = datetime.strptime(lc['eol_date'], '%Y-%m-%d').date()
            days_left = (eol - today).days
            status = 'EOL PASSED' if days_left < 0 else 'LEGACY'
            at_risk.append({
                'profile_name': p['resource_name'],
                'account': p['source_account_id'],
                'region': p['source_region'],
                'model_id': mid,
                'model_name': lc['model_name'],
                'provider': lc['provider'],
                'lifecycle_status': status,
                'eol_date': lc['eol_date'],
                'days_until_eol': days_left,
                'ext_date': lc['ext_date'],
                'tags': p.get('tags', ''),
            })

    # Sort by urgency
    at_risk.sort(key=lambda x: x['days_until_eol'])

    # Print report
    print("=" * 120)
    print(f"BEDROCK MODEL LIFECYCLE RISK REPORT — {today.isoformat()}")
    print(f"Profiles analyzed: {len(profiles)} | At-risk profiles: {len(at_risk)}")
    print("=" * 120)

    if not at_risk:
        print("\n✅ No inference profiles are using legacy/EOL models.")
        return

    # Group by status
    eol_passed = [r for r in at_risk if r['lifecycle_status'] == 'EOL PASSED']
    legacy = [r for r in at_risk if r['lifecycle_status'] == 'LEGACY']

    if eol_passed:
        print(f"\n🔴 EOL PASSED — {len(eol_passed)} profiles using DEAD models (will fail on invocation):")
        print("-" * 120)
        print(f"{'Profile':<35} {'Account':<14} {'Region':<12} {'Model':<40} {'EOL Date':<12} {'Days'}")
        print("-" * 120)
        for r in eol_passed:
            print(f"{r['profile_name']:<35} {r['account']:<14} {r['region']:<12} {r['model_id']:<40} {r['eol_date']:<12} {r['days_until_eol']}")

    if legacy:
        print(f"\n🟡 LEGACY — {len(legacy)} profiles using models approaching EOL:")
        print("-" * 120)
        print(f"{'Profile':<35} {'Account':<14} {'Region':<12} {'Model':<40} {'EOL Date':<12} {'Days Left':<10} {'Ext Access'}")
        print("-" * 120)
        for r in legacy:
            ext = r['ext_date'] if r['ext_date'] else '—'
            print(f"{r['profile_name']:<35} {r['account']:<14} {r['region']:<12} {r['model_id']:<40} {r['eol_date']:<12} {r['days_until_eol']:<10} {ext}")

    # Summary by model
    print(f"\n{'=' * 120}")
    print("SUMMARY BY MODEL (unique profiles using each legacy/EOL model):")
    print("-" * 120)
    model_counts = {}
    for r in at_risk:
        k = (r['model_id'], r['model_name'], r['eol_date'], r['lifecycle_status'])
        if k not in model_counts:
            model_counts[k] = set()
        model_counts[k].add(r['profile_name'])

    print(f"{'Model ID':<50} {'Status':<12} {'EOL Date':<12} {'Profiles'}")
    print("-" * 120)
    for (mid, name, eol, status), profiles_set in sorted(model_counts.items(), key=lambda x: x[0][2]):
        print(f"{mid:<50} {status:<12} {eol:<12} {len(profiles_set)} ({', '.join(sorted(profiles_set)[:3])}{'...' if len(profiles_set) > 3 else ''})")

if __name__ == '__main__':
    main()
