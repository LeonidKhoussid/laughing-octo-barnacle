# Server deployment

Use an updated source ZIP or a copied current project directory for each server update. Do not rely on `git pull` alone because the required changes can be uncommitted locally.

## Copy source and start the judge endpoint

From the workstation that has the updated source archive, copy it to the server:

```bash
scp artifacts/alfagen_source.zip SERVER_USER@201.34.146.180:/tmp/alfagen_source.zip
```

On the server, preserve any existing `.env` before extracting. The source ZIP excludes `.env`, models, dependencies, and build output, so this command updates source files without replacing deployed keys.

```bash
APP_DIR=/opt/alfagen
mkdir -p "$APP_DIR"
test ! -f "$APP_DIR/.env" || cp -p "$APP_DIR/.env" "$APP_DIR/.env.pre-update"
unzip -oq /tmp/alfagen_source.zip -d "$APP_DIR"
cd "$APP_DIR"
```

Start the basic judge configuration:

Docker Engine and the Compose plugin must already be installed. If the old service uses Python, systemd, or a different Compose project, stop that service with its original launcher before binding port 8000; do not run two gateways on the same port. If it uses this Compose project, `up --build -d` replaces the container itself.

```bash
cd /opt/alfagen
docker compose config --quiet
docker compose up --build -d
docker compose ps
```

This basic configuration needs neither `.env` nor API keys. It starts one MemoryVault worker with four NER CPU threads and exposes the no-key competition endpoint at `POST /process`; protected demo consumers remain unavailable until their keys are configured. This is the appropriate initial configuration for the 4-CPU, 8-GB judge server because MemoryVault cannot be shared by multiple workers.

The image uses CPU Linux wheels, builds the React UI with `npm ci`, and downloads SHA-256-verified semantic models into a separate cached build stage. The model download is about 1.16 GB, so allow outbound access and enough disk space for the image and model layer. App-code-only changes reuse the cached model stage unless `configs/semantic-models.json` or `scripts/download_semantic_models.py` changes.

`PII_MAX_BODY_BYTES=2097152` admits the measured UTF-8 request size for the required 100k-token case. `PII_MAX_PROCESSING_SECONDS=9` stays below the checker timeout; this does not demonstrate the one-second latency target. `PII_MAX_IN_FLIGHT=8` bounds expensive concurrent work on the four-CPU baseline; excess requests receive 429 rather than building a long inference queue. If an existing `.env` sets the previous value of 64, change that value to 8 before rebuilding. This reduces queuing but does not establish 1000 successful RPS.

## Verify readiness and reversible processing

```bash
cd /opt/alfagen
ready=0
for attempt in $(seq 1 45); do
  if curl --max-time 2 -fsS http://127.0.0.1:8000/health | grep -q '"ready":true'; then
    ready=1
    break
  fi
  sleep 2
done
test "$ready" = 1 || { docker compose logs --tail=100 gateway; exit 1; }
curl --max-time 5 -fsS http://127.0.0.1:8000/health
curl --max-time 5 -fsS http://201.34.146.180:8000/health
```

Run this paired synthetic smoke check against the no-key competition endpoint:

```bash
python3 - <<'PY'
import json
from urllib.request import Request, urlopen

url = "http://127.0.0.1:8000/process"
original = "Контакт: test@example.com"
payload_id = "server-smoke-1"

def process(payload):
    request = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=10) as response:
        return json.load(response)["result"]

masked = process({"payload_id": payload_id, "payload": original})
assert masked != original
assert process({"payload_id": payload_id, "payload": masked}) == original
print("paired /process smoke: PASSED")
PY
```

Health confirms policy and vault access. It does not prove target throughput or latency.

## Bounded five-minute HTTP measurement

Do not run this measurement until the service is healthy and the host is otherwise idle. It is an open-loop five-minute diagnostic at the clarified mean-load reference of 330 scheduled requests per second, with a 200-request client concurrency cap; it can show that the service is overloaded and does not assume that 330 requests per second will succeed.

Prefer a separate load-generator machine with a copy of the source ZIP; replace the URL below with the server's public URL. Running the generator on the server shares its CPU and is only a local diagnostic. Create a small benchmark environment without changing the running container or its `.env`:

```bash
cd /opt/alfagen
python3 -m venv .bench-venv
.bench-venv/bin/pip install httpx
```

Then run the one bounded measurement. `required` rotates the generator's synthetic cases for all 17 required categories, while `public` and `mixed` exercise the two context paths; all input is synthetic.

```bash
cd /opt/alfagen
.bench-venv/bin/python scripts/benchmark_process.py \
  --base-url http://127.0.0.1:8000 \
  --mode open \
  --rps 330 \
  --duration 300 \
  --concurrency 200 \
  --restore-every 1 \
  --mix required,public,mixed \
  --timeout 10 \
  --output artifacts/appendix-b-open-330rps-5min.json
```

`offered_rps` is scheduled slots divided by 300 seconds. `attempted` and `started_within_window` count HTTP requests the client actually launched after late and client-capacity drops. `successful_within_window_rps` counts only correct HTTP results completed inside the five-minute window; report it separately from completed-during-drain, errors, 429 responses, and dropped slots. A constant 330-RPS run does not reproduce the judge's unspecified ramp or demonstrate the 1000-RPS peak target. For a separate fixed-rate stress run, use `--rps 1000` and a different output filename. Neither run has retries, so neither duplicates the official tester's retry schedule.

## Optional protected demo consumers

Only configure keys when using demo or admin routes:

```bash
cd /opt/alfagen
test -f .env || cp .env.example .env
chmod 600 .env
nano .env
```

Set non-empty values for the required `*_DEMO_API_KEY` variables and `PII_CONFIG_UPDATE_KEY`, then restart the gateway. The five-sentence consumer-configuration guide and its validation command are in [README.md](../README.md#setup-5-sentences).

## Restart and optional Redis mode

```bash
cd /opt/alfagen
docker compose restart gateway
docker compose logs --tail=100 gateway
```

The gateway restarts automatically and gets 35 seconds to shut down gracefully. On the available 4-CPU, 8-GB server, use single-worker MemoryVault as the baseline; restart drops its active mappings, and more than one worker is forbidden with that backend.

Use Redis only after a measured comparison shows that it helps. Set a retained base64 32-byte `PII_VAULT_KEY`, then set `PII_VAULT_BACKEND=redis`, `PII_WORKERS=2`, and `PII_VAULT_REDIS_URL=redis://redis:6379/0` in `.env`.

```bash
cd /opt/alfagen
docker compose --profile redis up -d redis
redis_ready=0
for attempt in $(seq 1 30); do
  if docker compose --profile redis exec -T redis redis-cli ping | grep -qx PONG; then
    redis_ready=1
    break
  fi
  sleep 1
done
test "$redis_ready" = 1 || { docker compose --profile redis logs --tail=100 redis; exit 1; }
docker compose --profile redis up -d --build gateway
```

The `redis-data` volume retains encrypted mappings across gateway restarts subject to TTL. Keep the Redis key stable and follow the deployment's approved data-retention process for any volume backup.
