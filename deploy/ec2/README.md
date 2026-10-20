# Deploying the backend on a single EC2 instance

The app runs in Docker on one Ubuntu instance; the database stays on Supabase:

```
Internet ──► :80/:443  Caddy ──► api (FastAPI :8000)
                                   │
                worker (Celery) ◄──┼──► redis
                                   └──► Supabase Postgres (pooler)
Uploads (audio, documents) ──► Cloudinary or S3
```

Recommended: **t3.small (2 GB)** to start, **t3.medium (4 GB)** if the worker runs out of memory, **Ubuntu 22.04/24.04**, **30 GB gp3** disk.

---

## 1. AWS console setup (one time)

### 1.1 Elastic IP
EC2 → **Elastic IPs** → *Allocate* → *Associate* with your instance.
A plain public IP changes on every stop/start; the frontend and DNS need a fixed one.

### 1.2 Security group (instance → Security → security group → *Edit inbound rules*)

| Type  | Port | Source      | Why                         |
|-------|------|-------------|-----------------------------|
| SSH   | 22   | **My IP**   | your SSH access only        |
| HTTP  | 80   | 0.0.0.0/0   | API (and Let's Encrypt)     |
| HTTPS | 443  | 0.0.0.0/0   | API over HTTPS              |

Do **not** open 8000, 5432 or 6379. Those stay inside Docker.

### 1.3 S3 bucket for uploads
S3 → *Create bucket*, in the same region as the instance (e.g. `ap-southeast-2`).
Keep **Block all public access ON**: the app serves files through presigned URLs.

### 1.4 IAM user for the app
IAM → Users → *Create user* `classecho-app` (no console access) →
*Add permissions → Create inline policy → JSON* → paste [`s3-policy.json`](s3-policy.json)
with `YOUR-BUCKET-NAME` replaced → save.
Then *Security credentials → Create access key* (use case "Application running on AWS compute")
and keep the key ID and secret for step 3.

---

## 2. Connect and get the code onto the server

From PowerShell on Windows (fix the key's permissions once, or ssh refuses it):

```powershell
icacls "C:\Work\Smart Class\First Key.pem" /inheritance:r /grant:r "$($env:USERNAME):(R)"
ssh -i "C:\Work\Smart Class\First Key.pem" ubuntu@<ELASTIC-IP>
```

On the server, clone the backend repo. It's private, so add a read-only **deploy key**:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub
```
Paste that key in GitHub → `resonatingmind/Class-Echo-Backend` → Settings → Deploy keys → *Add* (read-only). Then:

```bash
printf 'Host github.com\n  IdentityFile ~/.ssh/github_deploy\n' >> ~/.ssh/config
git clone git@github.com:resonatingmind/Class-Echo-Backend.git ~/backend
```

---

## 3. Install and configure

```bash
cd ~/backend/deploy/ec2
sudo bash bootstrap.sh     # Docker, 4 GB swap, .env with generated secrets, nightly backup cron
exit                       # log out/in so docker works without sudo
```

Reconnect, then fill in the blanks:

```bash
nano ~/backend/deploy/ec2/.env
```

At minimum, set these:
- `DATABASE_URL` (Supabase transaction pooler, :6543) and `MIGRATION_DATABASE_URL` (session pooler, :5432)
- `SECRET_KEY`: the one from the current deployment, or existing users are logged out
- `GEMINI_API_KEY`
- `ADMIN_EMAIL`, `ADMIN_PASSWORD`
- storage: `CLOUDINARY_URL`, **or** `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_STORAGE_BUCKET_NAME`
- `FRONTEND_URL`, `ALLOWED_ORIGINS`
- one email provider (e.g. `RESEND_API_KEY` + `RESEND_FROM_EMAIL`)

Cloudinary takes priority over S3 whenever any `CLOUDINARY_*` variable is set.

---

## 4. Start it

```bash
bash ~/backend/deploy/ec2/deploy.sh
```

The first build takes a few minutes. Then, from your own machine:

```
http://<ELASTIC-IP>/health            → {"status": "ok", ...}
http://<ELASTIC-IP>/health/analysis   → "broker":"reachable","workers":1,"storage":"s3"
```

If `storage` isn't what you configured (`"cloudinary"` or `"s3"`), re-check `.env`.

---

## 5. HTTPS (needed by the web frontend)

A frontend served over HTTPS (Vercel, CloudFront) **cannot call an `http://` API**: browsers block it
as mixed content. Mobile apps also need HTTPS unless cleartext is allowed.

- **With a domain:** add a DNS **A record** `api.yourdomain.com → <ELASTIC-IP>`, then in `.env` set
  `SITE_ADDRESS=api.yourdomain.com` and run `deploy.sh`. Caddy gets and renews the certificate itself.
- **No domain yet:** use a free wildcard-DNS name for the IP. For IP `13.233.10.20`, set
  `SITE_ADDRESS=13-233-10-20.sslip.io`. Caddy can usually get a real certificate for it.
  Swap to your own domain later.

Then point the frontend at it:
- **Web:** set `EXPO_PUBLIC_API_URL=https://<SITE_ADDRESS>` and rebuild.
- **Mobile:** set the same value in `eas.json` and rebuild.

---

## Day-to-day operations (run in `~/backend/deploy/ec2`)

| Task | Command |
|---|---|
| Deploy latest `main` | `bash deploy.sh` |
| Status | `docker compose ps` |
| Logs | `docker compose logs -f api` / `docker compose logs -f worker` |
| Restart worker only | `docker compose restart worker` |

**Backups:** the database lives on Supabase, so back it up there (Supabase → Database → Backups).
Nothing on this instance needs backing up except `.env`.

**Memory:**
- `CELERY_CONCURRENCY=1` suits t3.small (2 GB). Use 2 on 4 GB, 3–4 on 8 GB.
- If the worker gets OOM-killed, `docker compose logs worker` shows `SIGKILL`.

**Never** set `RESET_DB_ON_STARTUP=true` or `ENVIRONMENT=test`: it wipes the Supabase database. `deploy.sh` refuses to run if either is set.
