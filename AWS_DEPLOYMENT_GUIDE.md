# AWS Elastic Beanstalk Deployment Setup Guide

Complete guide to deploy Pickoo Backend to AWS Elastic Beanstalk with MongoDB Atlas integration.

## 📋 Prerequisites

1. **AWS Account** with admin access
2. **MongoDB Atlas** account (or any MongoDB instance)
3. **GitHub repository** with workflow access
4. **Domain name** (optional, but recommended)

---

## 🔧 Part 1: AWS Setup

### Step 1: Create IAM User for GitHub Actions

1. Go to **AWS Console** → **IAM** → **Users** → **Create user**

2. **User details:**
   - Username: `github-actions-pickoo`
   - Access type: ✅ Programmatic access

3. **Attach permissions:**
   - Click "Attach policies directly"
   - Select these policies:
     - ✅ `AWSElasticBeanstalkFullAccess`
     - ✅ `AmazonS3FullAccess`
     - ✅ `CloudWatchLogsFullAccess`

4. **Create user** and **save credentials:**
   - Access Key ID: `AKIAXXXXXXXXXXXXXXXX`
   - Secret Access Key: `wJalrXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX`
   
   ⚠️ **IMPORTANT**: Save these immediately - you won't see the secret key again!

### Step 2: Create S3 Bucket for Deployments

```bash
# Using AWS CLI
aws s3 mb s3://pickoo-backend-deployments --region us-east-1

# Or via AWS Console:
# Go to S3 → Create bucket → Name: "pickoo-backend-deployments"
```

### Step 3: Create Elastic Beanstalk Application

#### Option A: Using AWS Console (Easier for first time)

1. Go to **Elastic Beanstalk** → **Create Application**

2. **Application information:**
   - Application name: `pickoo-backend`
   - Platform: `Python`
   - Platform branch: `Python 3.12 running on 64bit Amazon Linux 2023`
   - Platform version: `(recommended)`

3. **Application code:**
   - Select "Sample application" (we'll deploy our code via GitHub Actions)

4. **Configuration presets:**
   - Select "Single instance (free tier eligible)" for testing
   - Or "High availability" for production

5. **Service access:**
   - Create new service role: `aws-elasticbeanstalk-service-role`

6. **VPC and networking:**
   - Use default VPC or create custom one
   - Select at least 2 availability zones for HA

7. **Instance configuration:**
   - EC2 instance type: `t3.small` (minimum) or `t3.medium` (recommended)
   - Root volume: 10 GB GP3

8. **Environment properties** (add these):
   ```
   PICKOO_MONGO_URI = <your-mongodb-uri>
   PICKOO_JWT_SECRET = <your-jwt-secret>
   PICKOO_GEMINI_API_KEY = <your-gemini-key>
   ```

9. **Review and create**

10. **Save the environment name**: `pickoo-backend-env`

#### Option B: Using AWS CLI (Advanced)

```bash
# Create application
aws elasticbeanstalk create-application \
  --application-name pickoo-backend \
  --description "Pickoo AI Photo Editor Backend API"

# Create environment
aws elasticbeanstalk create-environment \
  --application-name pickoo-backend \
  --environment-name pickoo-backend-env \
  --solution-stack-name "64bit Amazon Linux 2023 v4.0.0 running Python 3.12" \
  --option-settings \
    Namespace=aws:autoscaling:launchconfiguration,OptionName=InstanceType,Value=t3.small \
    Namespace=aws:elasticbeanstalk:environment,OptionName=EnvironmentType,Value=SingleInstance
```

### Step 4: Configure Environment Variables in Elastic Beanstalk

Go to **Elastic Beanstalk** → **Your Environment** → **Configuration** → **Software** → **Edit**

Add environment variables:
```
PICKOO_PROCESSOR_MODE = existing
PICKOO_GEMINI_API_KEY = your-gemini-api-key
PICKOO_GEMINI_MODEL = gemini-2.0-flash-exp
PICKOO_MONGO_URI = mongodb+srv://username:password@cluster.mongodb.net/pickoo
PICKOO_JWT_SECRET = your-secure-jwt-secret-key
PICKOO_JWT_EXP_MINUTES = 60
PICKOO_STRIPE_SECRET_KEY = sk_live_xxxx (if using Stripe)
```

---

## 🗄️ Part 2: MongoDB Atlas Setup

### Step 1: Create MongoDB Atlas Cluster

1. Go to [MongoDB Atlas](https://www.mongodb.com/cloud/atlas)
2. Sign up / Log in
3. **Create New Cluster**:
   - Cloud Provider: AWS
   - Region: `us-east-1` (same as your EB app)
   - Cluster Tier: M0 (Free) or M10 (Production)
   - Cluster Name: `pickoo-cluster`

### Step 2: Create Database User

1. **Database Access** → **Add New Database User**
2. Username: `pickoo-admin`
3. Password: Generate secure password
4. Database User Privileges: **Atlas admin** or **Read and write to any database**
5. **Add User**

### Step 3: Whitelist IP Addresses

1. **Network Access** → **Add IP Address**
2. For AWS Elastic Beanstalk:
   - Option 1: **Allow Access from Anywhere** (`0.0.0.0/0`) - Easiest
   - Option 2: Add specific AWS IP ranges (more secure)
3. **Confirm**

### Step 4: Get Connection String

1. **Clusters** → **Connect** → **Connect your application**
2. Driver: **Python** / Version: **3.12 or later**
3. Copy connection string:
   ```
   mongodb+srv://pickoo-admin:<password>@pickoo-cluster.xxxxx.mongodb.net/pickoo?retryWrites=true&w=majority
   ```
4. Replace `<password>` with your actual password
5. Replace `pickoo` at the end with your database name

### Step 5: Create Database and Collections

```python
# Connect and create collections
from motor.motor_asyncio import AsyncIOMotorClient

client = AsyncIOMotorClient("your-mongodb-uri")
db = client.pickoo

# Create collections
await db.create_collection("users")
await db.create_collection("transactions")
await db.create_collection("payments")

# Create indexes
await db.users.create_index("email", unique=True)
await db.transactions.create_index("transaction_id", unique=True)
await db.transactions.create_index("user_id")
```

---

## 🔐 Part 3: GitHub Secrets Configuration

Go to: https://github.com/maheshus007/pickoo-backend/settings/secrets/actions

### Required Secrets:

| Secret Name | Description | Where to Get |
|------------|-------------|--------------|
| `AWS_ACCESS_KEY_ID` | AWS IAM user access key | From IAM user creation (Step 1) |
| `AWS_SECRET_ACCESS_KEY` | AWS IAM user secret key | From IAM user creation (Step 1) |
| `EB_S3_BUCKET` | S3 bucket for deployments | From Step 2: `pickoo-backend-deployments` |
| `MONGO_URI` | MongoDB connection string | From MongoDB Atlas (Step 4) |
| `JWT_SECRET` | JWT signing secret | Generate: `openssl rand -base64 32` |
| `GEMINI_API_KEY` | Google Gemini API key | https://makersuite.google.com/app/apikey |
| `STRIPE_SECRET_KEY` | Stripe secret key (optional) | https://dashboard.stripe.com/apikeys |
| `STRIPE_PUBLISHABLE_KEY` | Stripe publishable key | https://dashboard.stripe.com/apikeys |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook secret | https://dashboard.stripe.com/webhooks |

### Add Each Secret:

```bash
# 1. Click "New repository secret"
# 2. Name: AWS_ACCESS_KEY_ID
# 3. Value: AKIAXXXXXXXXXXXXXXXX
# 4. Click "Add secret"

# Repeat for all secrets above
```

### Generate Secure JWT Secret:

**Windows PowerShell:**
```powershell
# Method 1: Using .NET
[Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Minimum 0 -Maximum 256 }))

# Method 2: Simple random string
-join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
```

**Linux/Mac:**
```bash
openssl rand -base64 32
```

---

## 🚀 Part 4: Deployment

### Option 1: Automatic Deployment (Push to main)

```bash
# Commit your changes
git add .
git commit -m "feat: Ready for AWS deployment"

# Push to main branch (triggers deployment)
git push origin main
```

### Option 2: Manual Deployment (Workflow Dispatch)

1. Go to GitHub → **Actions** tab
2. Select **"Deploy to AWS Elastic Beanstalk"** workflow
3. Click **"Run workflow"**
4. Select environment: `staging` or `production`
5. Click **"Run workflow"**

### Monitor Deployment

1. **GitHub Actions**: https://github.com/maheshus007/pickoo-backend/actions
   - Watch real-time logs
   - See test results
   - Verify deployment status

2. **AWS Elastic Beanstalk Console**:
   - Go to your environment
   - Check "Health" tab
   - View "Logs" tab
   - Monitor "Events" tab

---

## ✅ Part 5: Verification

### 1. Check Application Health

```bash
# Get your EB environment URL
aws elasticbeanstalk describe-environments \
  --application-name pickoo-backend \
  --environment-names pickoo-backend-env \
  --query "Environments[0].CNAME" \
  --output text

# Test health endpoint
curl https://your-env-url.us-east-1.elasticbeanstalk.com/health
```

Expected response:
```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

### 2. Verify MongoDB Connection

```bash
# Test from deployed app
curl https://your-env-url.elasticbeanstalk.com/debug/settings
```

### 3. Test API Endpoints

```bash
# Test authentication
curl -X POST https://your-env-url.elasticbeanstalk.com/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"testpass123"}'

# Test tools endpoint
curl https://your-env-url.elasticbeanstalk.com/tools
```

---

## 🔧 Part 6: Configuration Files Explained

### Files Created by Workflow:

#### 1. `Procfile`
Tells Elastic Beanstalk how to run your app:
```
web: uvicorn main:app --host 0.0.0.0 --port 8000
```

#### 2. `.ebextensions/01_packages.config`
Installs system packages needed for Python libraries:
```yaml
packages:
  yum:
    git: []
    gcc: []
    python3-devel: []
    libjpeg-devel: []  # For Pillow
    zlib-devel: []     # For image processing
```

#### 3. `.ebextensions/02_python.config`
Python-specific configuration:
```yaml
option_settings:
  aws:elasticbeanstalk:application:environment:
    PYTHONPATH: "/var/app/current:$PYTHONPATH"
  aws:elasticbeanstalk:container:python:
    WSGIPath: main:app
```

#### 4. `.ebextensions/03_health_check.config`
Health check configuration:
```yaml
option_settings:
  aws:elasticbeanstalk:environment:process:default:
    HealthCheckPath: /health
    HealthCheckInterval: 30
```

---

## 🐛 Troubleshooting

### Issue: Deployment Fails

**Check logs:**
```bash
# Download logs
eb logs -a pickoo-backend -e pickoo-backend-env

# Or via AWS Console:
# Elastic Beanstalk → Environment → Logs → Request Logs → Last 100 Lines
```

### Issue: Health Check Failing

1. Check `/health` endpoint is accessible
2. Verify security group allows inbound traffic on port 8000
3. Check environment variables are set correctly

### Issue: MongoDB Connection Fails

1. Verify MongoDB URI format
2. Check IP whitelist in MongoDB Atlas
3. Ensure database user has correct permissions
4. Test connection string locally first

### Issue: Import Errors

1. Ensure all dependencies in `requirements.txt`
2. Check `.ebextensions/01_packages.config` has necessary system packages
3. Verify Python version matches (3.12)

---

## 📊 Monitoring & Maintenance

### View Logs

```bash
# Stream logs
eb logs --stream

# Download full logs
eb logs -a pickoo-backend -e pickoo-backend-env
```

### Scale Application

```bash
# Scale up
aws elasticbeanstalk update-environment \
  --application-name pickoo-backend \
  --environment-name pickoo-backend-env \
  --option-settings \
    Namespace=aws:autoscaling:launchconfiguration,OptionName=InstanceType,Value=t3.medium

# Enable auto-scaling
aws elasticbeanstalk update-environment \
  --application-name pickoo-backend \
  --environment-name pickoo-backend-env \
  --option-settings \
    Namespace=aws:elasticbeanstalk:environment,OptionName=EnvironmentType,Value=LoadBalanced
```

### Monitor Costs

- Go to AWS **Cost Explorer**
- Set up billing alerts
- Monitor Elastic Beanstalk usage
- Check MongoDB Atlas usage

---

## 🔄 Update Workflow Configuration

Edit `.github/workflows/deploy-aws.yml`:

```yaml
env:
  AWS_REGION: us-east-1  # Change to your region
  EB_APPLICATION_NAME: pickoo-backend  # Your app name
  EB_ENVIRONMENT_NAME: pickoo-backend-env  # Your env name
```

---

## 💡 Best Practices

1. **Use separate environments**:
   - `pickoo-backend-staging` for testing
   - `pickoo-backend-production` for live

2. **Enable CloudWatch monitoring**:
   - Set up alarms for errors
   - Monitor response times
   - Track API usage

3. **Backup MongoDB**:
   - Enable automatic backups in Atlas
   - Set retention period to 7+ days

4. **Use custom domain**:
   - Register domain in Route 53
   - Configure SSL certificate
   - Point to EB environment

5. **Implement CI/CD best practices**:
   - Run tests before deployment
   - Use staging environment first
   - Enable manual approval for production

---

## 📞 Support

- **AWS Support**: https://console.aws.amazon.com/support
- **MongoDB Atlas Support**: https://support.mongodb.com
- **GitHub Actions Docs**: https://docs.github.com/actions

---

**Setup Complete!** 🎉

Your Pickoo Backend will now automatically deploy to AWS Elastic Beanstalk when you push to the `main` branch.

**Deployment URL**: https://your-env-name.us-east-1.elasticbeanstalk.com
