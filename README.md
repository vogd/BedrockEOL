# Bedrock Model Lifecycle EOL Tracker

Monitors Amazon Bedrock inference profiles and knowledge bases against the [Model Lifecycle page](https://docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle.html), alerting when models approach End-of-Life (EOL).

## What It Does

1. **Collects** inference profile and knowledge base configurations from AWS Config Aggregator (multi-account, multi-region)
2. **Parses** the Bedrock model lifecycle page to extract Legacy/EOL dates
3. **Cross-references** your deployed resources against lifecycle data
4. **Publishes** CloudWatch metrics and updates a dashboard with at-risk resources
5. **Alerts** via email (SNS) with full ARN lists when resources hit your configured threshold

## Architecture

> 📐 **Full architecture diagram**: Open `architecture.drawio` in [draw.io](https://app.diagrams.net/) for the visual block diagram.

```
EventBridge Schedule (rate: N hours)
    │ triggers
    ▼
bedrock_EOLDataProcessor Lambda
    ├── queries AWS Config Aggregator (profiles + KBs)
    ├── fetches + parses Model Lifecycle page (HTML + AI fallback)
    ├── writes JSONL → S3 (5 prefixes)
    └── starts Step Functions execution
                    │
                    ▼
        Step Functions State Machine
            1. CheckAthenaTables
            2. CreateTables (IF NOT EXISTS) → Athena
            3. PublishMetrics → bedrock_EOLMetricsPublisher Lambda
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
            CloudWatch          CloudWatch      SNS Topic
            Dashboard           Alarms          (email with
            (metrics +          (30/60/180d)    full ARN list)
             ARN table)

        Athena Tables + View ──→ QuickSight / 3rd Party BI
        S3 Raw JSON ──→ Custom integrations
```

```
┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│  AWS Config         │     │  Model Lifecycle Page │     │  CloudWatch         │
│  Aggregator         │     │  (docs.aws.amazon.com)│     │  Dashboard + Alarms │
│  (multi-account)    │     └──────────┬───────────┘     └──────────▲──────────┘
└─────────┬───────────┘                │                            │
          │                            │                            │
          ▼                            ▼                            │
┌─────────────────────────────────────────────────────┐            │
│  BedrockEOLDataProcessor Lambda                     │            │
│  - Queries Config for inference profiles + KBs      │            │
│  - Fetches & parses lifecycle HTML (with AI fallback│            │
│  - Writes JSON to S3                                │            │
└─────────────────────┬───────────────────────────────┘            │
                      │ triggers                                    │
                      ▼                                             │
┌─────────────────────────────────────────────────────┐            │
│  Step Functions State Machine                       │            │
│  1. Check Athena tables exist                       │            │
│  2. Create tables if missing (IF NOT EXISTS)        │            │
│  3. Publish CloudWatch metrics ──────────────────────────────────┘
└─────────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│  S3 Bucket (bedrockeol)                             │
│  ├── bedrock-active-models/                         │
│  ├── bedrock-legacy-models/                         │
│  ├── bedrock-eol-models/                            │
│  ├── bedrock-inference-profiles/                    │
│  └── bedrock-knowledge-bases/                       │
└─────────────────────────────────────────────────────┘
```

## Prerequisites

### 1. AWS Config Aggregator

You need an AWS Config Aggregator to read inference profiles and knowledge bases across accounts.

> 📐 **Architecture diagrams**: Open `config.drawio` in [draw.io](https://app.diagrams.net/) to see visual diagrams of both aggregator options below.

#### Option A: Organization-level aggregator (Payer/Management account)

If you have access to the management account, create an organization aggregator:

```bash
aws configservice put-configuration-aggregator \
  --configuration-aggregator-name BedrockAggregator \
  --organization-aggregation-source '{"RoleArn":"arn:aws:iam::<MGMT_ACCOUNT>:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig","AllAwsRegions":true}' \
  --region us-east-1
```

#### Option B: Account-level aggregator (specific accounts)

For aggregating from specific linked accounts without management account access:

**Step 1: Enable Config in each source account (each region with resources)**

```bash
# Run in each source account
aws configservice put-configuration-recorder \
  --configuration-recorder name=default,roleARN=arn:aws:iam::<SOURCE_ACCOUNT>:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig \
  --region <REGION>
```

**Step 2: Authorize the aggregator (run in each source account)**

```bash
AGGREGATOR_ACCOUNT_ID="<ACCOUNT_WHERE_YOU_DEPLOY_THIS_STACK>"
AGGREGATOR_REGION="us-east-1"  # region where aggregator lives

for region in $(aws ec2 describe-regions --query "Regions[].RegionName" --output text); do
  recorder=$(aws configservice describe-configuration-recorders --region $region \
    --query "ConfigurationRecorders[0].name" --output text 2>/dev/null)
  if [ "$recorder" != "None" ] && [ -n "$recorder" ]; then
    echo "Authorizing aggregator in $region..."
    aws configservice put-aggregation-authorization \
      --authorized-account-id $AGGREGATOR_ACCOUNT_ID \
      --authorized-aws-region $AGGREGATOR_REGION \
      --region $region
  fi
done
```

**Step 3: Create the aggregator (run in the aggregator account)**

```bash
# All regions from specific accounts:
aws configservice put-configuration-aggregator \
  --configuration-aggregator-name BedrockAggregator \
  --account-aggregation-sources \
    '[{"AccountIds":["111111111111","222222222222"],"AllAwsRegions":true}]' \
  --region us-east-1

# Or specific regions only:
aws configservice put-configuration-aggregator \
  --configuration-aggregator-name BedrockAggregator \
  --account-aggregation-sources \
    '[{"AccountIds":["111111111111","222222222222"],"AllAwsRegions":false,"AwsRegions":["us-east-1","us-west-2"]}]' \
  --region us-east-1
```

**Step 4: Verify data is flowing**

```bash
aws configservice describe-configuration-aggregator-sources-status \
  --configuration-aggregator-name BedrockAggregator \
  --region us-east-1 --output table
```

Each region should show `LastUpdateStatus: SUCCEEDED`. If `FAILED` with `AccessDenied`:
- Source account hasn't authorized the aggregator → run Step 2 in that account
- Config not enabled in that region → enable it first

> **Key Points:**
> - The aggregator can be created before source accounts are authorized
> - Authorization is per-region in the source account
> - `--authorized-aws-region` refers to where the aggregator lives, not the source region
> - Re-running `put-configuration-aggregator` triggers an immediate re-sync

### 2. S3 Bucket

Create a bucket to store the collected data:

```bash
aws s3 mb s3://bedrockeol --region us-west-2
```

### 3. Athena Results Bucket

You need an S3 bucket for Athena query results (can be the same bucket or separate):

```bash
aws s3 mb s3://my-athena-results --region us-west-2
```

---

## Deployment

### Parameters

| Parameter | Required | Description | How to get it |
|-----------|----------|-------------|---------------|
| `AggregatorName` | Yes | AWS Config Aggregator name | `aws configservice describe-configuration-aggregators --query 'ConfigurationAggregators[*].ConfigurationAggregatorName'` |
| `ConfigAggregatorRegion` | Yes | Region where aggregator lives | Usually `us-east-1` |
| `S3BucketName` | Yes | Bucket for collected data | The bucket you created above |
| `AthenaResultsBucket` | No | Bucket for Athena query results | Leave empty to use S3BucketName |
| `AthenaWorkGroup` | No | Athena workgroup | Default: `primary` |
| `AthenaDatabase` | No | Athena database name | Default: `default` |
| `ExecutionFrequencyHours` | No | How often to collect data (1-168) | Default: `24` |
| `NotificationEmail` | No | Email for alerts | Leave empty to skip alerts |
| `AlertThresholdDays` | No | Alert when EOL within N days | `30`, `60`, or `180`. Default: `30` |

### Deploy the stack

```bash
aws cloudformation deploy \
  --template-file 1-BedrockEOLTables.yaml \
  --stack-name bedrock-eol-tracker \
  --parameter-overrides \
    AggregatorName=BedrockAggregator \
    ConfigAggregatorRegion=us-east-1 \
    S3BucketName=bedrockeol \
    AthenaResultsBucket=my-athena-results \
    ExecutionFrequencyHours=24 \
    NotificationEmail=team@example.com \
    AlertThresholdDays=60 \
  --capabilities CAPABILITY_IAM \
  --region us-west-2
```

### First run (populate data immediately)

After deployment, trigger the data processor to collect data:

```bash
# Get the Lambda function name from stack outputs
aws lambda invoke --function-name bedrock_EOLDataProcessor \
  --region us-west-2 /tmp/out.json && cat /tmp/out.json
```

Then trigger the state machine to create Athena tables and publish metrics:

```bash
aws stepfunctions start-execution \
  --state-machine-arn $(aws cloudformation describe-stacks --stack-name bedrock-eol-tracker \
    --query 'Stacks[0].Outputs[?OutputKey==`BedrockStateMachineArn`].OutputValue' --output text --region us-west-2) \
  --input '{"AthenaDatabase":"default","AthenaWorkGroup":"primary","S3BucketName":"bedrockeol","AthenaResultsBucket":"my-athena-results"}' \
  --region us-west-2
```

---

## What Gets Created

### Lambda Functions

| Function | Purpose |
|----------|---------|
| `bedrock_EOLDataProcessor` | Queries Config Aggregator for profiles/KBs, parses lifecycle page, writes to S3 |
| `bedrock_CheckAthenaTables` | Checks if Athena tables exist |
| `bedrock_AthenaTableCreator` | Creates Athena tables (IF NOT EXISTS) + unified view |
| `bedrock_EOLMetricsPublisher` | Reads S3 data, publishes CloudWatch metrics, updates dashboard |
| `bedrock_ScheduleCreator` | Manages EventBridge schedule for periodic execution |

### How Data Collection Works

1. **Config Query**: The data processor Lambda runs an advanced query against the Config Aggregator:
   - `SELECT ... WHERE resourceType = 'AWS::Bedrock::InferenceProfile'`
   - `SELECT ... WHERE resourceType = 'AWS::Bedrock::KnowledgeBase'`

2. **Lifecycle Parsing**: Fetches the HTML from the model lifecycle page and extracts the Legacy/EOL table using:
   - **Standard parser** (Python HTMLParser) — extracts `<table>` elements, identifies columns by headers
   - **AI fallback** (Bedrock Claude Haiku) — if standard parsing fails (e.g., page layout changes), sends cleaned HTML to Claude for extraction

3. **S3 Output**: Writes JSONL files to S3:
   - `bedrock-inference-profiles/{account}-profiles.json`
   - `bedrock-knowledge-bases/{account}-knowledge-bases.json`
   - `bedrock-legacy-models/{account}-legacy.json`
   - `bedrock-eol-models/{account}-eol.json`
   - `bedrock-active-models/{account}-active.json`

### Athena Tables & View

Six tables are created pointing to the S3 data:
- `bedrock_active_models`, `bedrock_legacy_models`, `bedrock_eol_models`
- `bedrock_inference_profiles`, `bedrock_knowledge_bases`, `bedrock_model_reference`

A unified view (`bedrock_unified_view`) joins profiles to lifecycle data:
```sql
SELECT ip.*, lifecycle_status, eol_date
FROM bedrock_inference_profiles ip
LEFT JOIN bedrock_legacy_models lm
  ON regexp_extract(ip.model_arn, '[^/]+$') = lm.recommended_model_id
```

This view can be used by QuickSight or any Athena-compatible tool.

### CloudWatch Dashboard

The `Bedrock-Model-Lifecycle` dashboard shows:
- **Inference Profiles**: 🔴 Past EOL | 🟠 ≤30d | 🟡 ≤60d | 🔵 ≤180d | 🟢 Active
- **Knowledge Bases**: Same breakdown (KB EOL = full reindex required)
- **Legacy/EOL Resources table**: Full ARN, account, region, model, days remaining
- **Active Resources table**: All resources using current models

### CloudWatch Metrics (namespace: `BedrockLifecycle`)

| Metric | Dimensions | Description |
|--------|-----------|-------------|
| `ProfilesEOLPassed` | — | Profiles using dead models |
| `ProfilesAtRisk30Days` | — | Profiles with EOL ≤ 30 days |
| `ProfilesAtRisk60Days` | — | Profiles with EOL ≤ 60 days |
| `ProfilesAtRisk180Days` | — | Profiles with EOL ≤ 180 days |
| `ProfilesActive` | — | Profiles using current models |
| `KBsEOLPassed` / `KBsAtRisk*` / `KBsActive` | — | Same for Knowledge Bases |
| `DaysUntilEOL` | ResourceType, AccountId, Region, ModelId | Per-resource drill-down |

### Alarms & Alerts

When `NotificationEmail` is provided:
- CloudWatch Alarms fire when any threshold metric > 0
- The Lambda sends a **detailed email** with full ARN list, grouped by severity
- Alert threshold is configurable: 30, 60, or 180 days

To disable/enable alerts without redeploying:
- CloudWatch Console → Alarms → Select alarm → Actions → Disable/Enable alarm actions

---

## Optional: QuickSight Integration

Deploy the second template for a QuickSight SPICE dataset:

```bash
aws cloudformation deploy \
  --template-file 2-BedrockEOLDatasets.yaml \
  --stack-name bedrock-eol-quicksight \
  --parameter-overrides \
    AthenaDatabase=default \
    AthenaWorkGroup=primary \
    AthenaResultsBucket=my-athena-results \
    S3BucketName=bedrockeol \
    QuickSightUserArn=arn:aws:quicksight:us-west-2:123456789012:user/default/admin \
  --capabilities CAPABILITY_IAM \
  --region us-west-2
```

This creates an Athena datasource, SPICE dataset, and hourly refresh schedule for QuickSight dashboards.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| CWatch Dashboard shows no data | Invoke `bedrock_EOLMetricsPublisher` manually, set CW time range to 1h |
| Athena view missing | Trigger the state machine — it creates the view if tables exist |
| Config returns no data | Verify aggregator authorization in source accounts |
| Lifecycle parsing returns 0 models | Check Lambda logs — AI fallback may need Bedrock model access enabled |
| Alarms in INSUFFICIENT_DATA | Metrics haven't been published yet — invoke the Lambda |

---

## File Structure

```
├── 1-BedrockEOLTables.yaml      # Main template (data collection + Athena + CloudWatch)
├── validate_lifecycle.py        # Local validation script for testing
└── README.md                    # This file
```
