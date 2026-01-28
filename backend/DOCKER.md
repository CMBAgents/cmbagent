# CMBAgent Backend Docker Deployment

This guide explains how to deploy the CMBAgent backend using Docker.

## Architecture

```
User's Machine (Electron/Browser)          Your Server (Docker)
─────────────────────────────────          ────────────────────
┌─────────────────────────────┐            ┌─────────────────────────────┐
│  CMBAgent UI                │            │  cmbagent-backend container │
│  - Renders interface        │◄──────────►│  - FastAPI + uvicorn        │
│  - Executes code locally    │  WebSocket │  - Orchestrates AI agents   │
│  - Stores files locally     │            │  - Makes LLM API calls      │
└─────────────────────────────┘            └─────────────────────────────┘
```

## Prerequisites

- Docker and Docker Compose installed
- API keys for LLM providers (OpenAI, Anthropic, etc.)
- Firebase credentials (for authentication)

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/CMBAgents/cmbagent.git
cd cmbagent/backend
```

### 2. Set up credentials

```bash
# Create credentials directory
mkdir -p credentials

# Copy your Firebase service account JSON
cp /path/to/your/firebase-credentials.json credentials/

# Create .env file from example
cp .env.example .env

# Edit .env with your API keys
nano .env
```

### 3. Build and run

```bash
# Build and start the container
docker compose up -d

# Check logs
docker compose logs -f

# Check health
curl http://localhost:8000/api/health
```

### 4. Connect your frontend

Set your frontend's `NEXT_PUBLIC_BACKEND_URL` to point to your server:

```bash
# In cmbagent-ui/.env.local
NEXT_PUBLIC_BACKEND_URL=http://your-server:8000
# Or with HTTPS
NEXT_PUBLIC_BACKEND_URL=https://your-domain.com
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key | (required) |
| `ANTHROPIC_API_KEY` | Anthropic API key | (optional) |
| `GEMINI_API_KEY` | Google Gemini API key | (optional) |
| `UVICORN_WORKERS` | Number of server workers | 4 |
| `CMBAGENT_WORK_DIR` | Directory for task outputs | /app/workdir |

### Scaling

Adjust the number of workers based on expected load:

```bash
# For ~50 concurrent users
UVICORN_WORKERS=4

# For ~100 concurrent users
UVICORN_WORKERS=8

# For ~200+ concurrent users
# Consider running multiple containers with a load balancer
```

### Memory

Each active task uses approximately 200-500MB of memory. Adjust the Docker memory limits accordingly:

```yaml
# In docker-compose.yml
deploy:
  resources:
    limits:
      memory: 8G  # For ~20 concurrent tasks
```

## Production Deployment

### With HTTPS (recommended)

Use a reverse proxy like nginx or Traefik for HTTPS termination:

```bash
# Example with nginx
docker run -d \
  --name nginx \
  -p 443:443 \
  -v ./nginx.conf:/etc/nginx/nginx.conf:ro \
  -v ./certs:/etc/nginx/certs:ro \
  --link cmbagent-backend \
  nginx:alpine
```

### With Tailscale Funnel

If you're already using Tailscale:

```bash
# Expose the backend via Tailscale Funnel
tailscale funnel 8000
```

## Monitoring

### View logs

```bash
docker compose logs -f cmbagent-backend
```

### Check container status

```bash
docker compose ps
```

### Resource usage

```bash
docker stats cmbagent-backend
```

## Troubleshooting

### Container won't start

1. Check logs: `docker compose logs`
2. Verify credentials file exists: `ls credentials/`
3. Verify .env file: `cat .env`

### Authentication errors

1. Ensure Firebase credentials are correctly mounted
2. Check that `GOOGLE_APPLICATION_CREDENTIALS` points to the right file
3. Verify Firestore rules allow access

### High memory usage

1. Reduce `UVICORN_WORKERS`
2. Increase Docker memory limit
3. Implement task queuing for high load

## Updating

```bash
# Pull latest code
git pull

# Rebuild and restart
docker compose down
docker compose build --no-cache
docker compose up -d
```

## Backup

Task outputs are stored in `./workdir`. Back this up regularly:

```bash
# Backup
tar -czf backup-$(date +%Y%m%d).tar.gz workdir/

# Restore
tar -xzf backup-YYYYMMDD.tar.gz
```
