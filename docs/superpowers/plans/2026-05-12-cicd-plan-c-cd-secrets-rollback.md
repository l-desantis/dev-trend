# CI/CD Plan C — Continuous Deploy: SSH, SOPS secrets, health-gated rollback, Telegram

> **For agentic workers:** REQUIRED SUB-SKILL — use `superpowers:executing-plans` (or `superpowers:subagent-driven-development`) to work through this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

## Context

Plans A and B produced a container and a CI pipeline that pushes images to `ghcr.io/l-desantis/dev-trend`. This plan completes CI/CD: every push to `main` is **deployed** to a Hetzner CX22 VPS via SSH, with automatic rollback on health-check failure and Telegram notifications.

Secrets are managed with **SOPS + age**. An age public key lives in `.sops.yaml` in the repo; the matching private key sits at `/etc/devtrend/age.key` on the VPS. The encrypted env file (`secrets.enc.env`) is committed; the VPS decrypts it at deploy time. GitHub Actions never sees decrypted secrets — it only holds an SSH deploy key.

See the design doc: `docs/superpowers/specs/2026-05-12-cicd-infrastructure-design.md`.

**Goal:** A push to `main` is automatically deployed to the VPS within ~5 minutes of CI green. If `/health` fails to come up after deploy, the previous image is automatically restored and a Telegram message reports the rollback.

**Architecture:** A `deploy.yml` workflow triggered by `workflow_run` after `Build and Push`. The workflow opens an SSH session to the VPS, runs `git pull`, decrypts SOPS secrets, sets `IMAGE_TAG=sha-<new>`, runs `docker compose pull && up -d`, then polls `/health`. On failure it opens a second SSH session that pins `IMAGE_TAG` to the previous value and brings the stack back up. A final step posts to Telegram regardless of outcome.

**Tech Stack:** SOPS, age, OpenSSH, `webfactory/ssh-agent`, `appleboy/ssh-action`, Telegram Bot API.

**Environment note:** This plan involves one-shot manual VPS bootstrap (Task 4) that the operator runs over SSH. The assistant cannot run those commands directly — present them clearly and wait for confirmation between tasks.

---

## File Structure

| File | Status | Responsibility |
| --- | --- | --- |
| `.sops.yaml` | Create | Pins the age recipient (public key) used to encrypt `secrets.enc.env`. |
| `secrets.enc.env` | Create | SOPS-encrypted env file committed to the repo. |
| `.gitignore` | Modify | Ensure decrypted `.env` is never committed. |
| `docs/superpowers/runbooks/vps-bootstrap.md` | Create | One-shot manual VPS setup steps. |
| `.github/workflows/deploy.yml` | Create | Auto-deploy on `Build and Push` success. SSH to VPS → pull → up → health-gate → rollback on failure → Telegram. |
| `.github/workflows/rollback.yml` | Create | `workflow_dispatch` to roll back to any sha tag still on ghcr.io. |
| `README.md` | Modify | Add a "Production deploy & secrets" section. |

---

## Task 1: Decide where the age key lives and generate it

The age private key is the root of trust for all production secrets. It must:
- Exist on the VPS at a known path (`/etc/devtrend/age.key`).
- Be readable by the `deploy` user (or by `sops` running as that user) at deploy time.
- **Never** leave the VPS or be committed anywhere.

There are two reasonable patterns:
1. Generate the key locally, copy it to the VPS, delete the local copy.
2. Generate the key directly on the VPS via SSH.

Pattern 2 is safer (key never touches the laptop's filesystem). We use pattern 2.

This task is informational — no files are created here. Task 4 generates the key on the VPS.

- [ ] **Step 1: Confirm `age` is installed locally**

Ask the operator:

```
! age --version
```

If missing on Ubuntu/WSL: `! sudo apt install age`. On macOS: `! brew install age`. We need the local `age` only to compute the **public** key after Task 4 generates the private one on the VPS — actually `age` running on the VPS will print the public key during generation, so even this is optional. Move on either way.

---

## Task 2: Update `.gitignore` to prevent decrypted secrets leaking

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Read the current `.gitignore`**

Verify `.env` is already ignored. The existing file likely has it; if not, add it.

- [ ] **Step 2: Append a new section to `.gitignore`**

```gitignore

# SOPS / deploy
.env
.env.local
.env.production
# Never commit decrypted secrets or the age key.
age.key
*.age.key
```

(If `.env` is already in the file, only add the SOPS-specific lines — keep `.gitignore` clean and don't duplicate.)

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore decrypted env files and age keys"
```

---

## Task 3: Create `.sops.yaml` with a placeholder recipient

We can't fill in the real public key until Task 4 generates the private key on the VPS. We commit the file structure now with a clearly-labelled placeholder so Task 4 only has to substitute one value.

**Files:**
- Create: `.sops.yaml`

- [ ] **Step 1: Write `.sops.yaml`**

```yaml
# SOPS config — pins the age recipient(s) used to encrypt secrets.enc.env.
# The matching private key lives on the VPS at /etc/devtrend/age.key.
#
# To add a contributor:
#   1. Have them generate their own age key (`age-keygen -o ~/.config/sops/age/keys.txt`).
#   2. Append their public key to the `age:` list below.
#   3. Re-encrypt: `sops updatekeys secrets.enc.env`.
creation_rules:
  - path_regex: secrets\.enc\.env$
    encrypted_regex: ".*"
    age: >-
      REPLACE_WITH_VPS_AGE_PUBLIC_KEY
```

- [ ] **Step 2: Commit (placeholder only; do not encrypt yet)**

```bash
git add .sops.yaml
git commit -m "chore: scaffold .sops.yaml (recipient TBD by VPS bootstrap)"
```

---

## Task 4: VPS bootstrap runbook + one-shot manual setup

This is the one and only manual step in the entire CI/CD work. The operator runs it once on a fresh Hetzner CX22.

**Files:**
- Create: `docs/superpowers/runbooks/vps-bootstrap.md`
- (Operator action) Run the runbook on the VPS.

- [ ] **Step 1: Write the runbook**

```bash
mkdir -p docs/superpowers/runbooks
```

Then create `docs/superpowers/runbooks/vps-bootstrap.md` with:

````markdown
# VPS bootstrap — Hetzner CX22 (Ubuntu 24.04) for DevTrend

One-shot manual setup. Run as `root` (or with `sudo`) on the freshly-provisioned VPS.

## 1. Base packages

```bash
apt update && apt -y upgrade
apt -y install ca-certificates curl gnupg git age
```

## 2. Docker engine + compose plugin

```bash
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt update
apt -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Verify:

```bash
docker --version
docker compose version
```

## 3. Install `sops`

```bash
SOPS_VERSION=v3.9.1
curl -fsSL "https://github.com/getsops/sops/releases/download/${SOPS_VERSION}/sops-${SOPS_VERSION}.linux.amd64" \
  -o /usr/local/bin/sops
chmod +x /usr/local/bin/sops
sops --version
```

## 4. Create the `deploy` user

```bash
adduser --disabled-password --gecos "" deploy
usermod -aG docker deploy
mkdir -p /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chown deploy:deploy /home/deploy/.ssh
```

## 5. Generate the age key

```bash
mkdir -p /etc/devtrend
age-keygen -o /etc/devtrend/age.key
chmod 0400 /etc/devtrend/age.key
chown root:deploy /etc/devtrend/age.key
chmod 0440 /etc/devtrend/age.key
```

`age-keygen` prints the **public key** to stdout. Copy it — looks like:

```
# created: 2026-05-12T...
# public key: age1abcd...xyz
```

Save the `age1...` line. You will paste it into `.sops.yaml` on your laptop.

## 6. Generate the GHA → VPS SSH key (on your laptop, not the VPS)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/devtrend_deploy -N "" -C "gha-deploy@devtrend"
```

Output:
- `~/.ssh/devtrend_deploy` — **private key**; paste this into the `DEPLOY_SSH_KEY` GitHub Secret (Task 5).
- `~/.ssh/devtrend_deploy.pub` — copy this onto the VPS.

On the VPS:

```bash
echo "<paste the .pub contents here>" >> /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
```

Test from laptop:

```bash
ssh -i ~/.ssh/devtrend_deploy deploy@<VPS_IP> "docker ps"
```

Expected: empty docker ps output, no password prompt.

## 7. Clone the repo to `/opt/dev-trend`

```bash
sudo mkdir -p /opt/dev-trend
sudo chown deploy:deploy /opt/dev-trend
sudo -u deploy git clone https://github.com/l-desantis/dev-trend.git /opt/dev-trend
```

(Use HTTPS for the clone; deploys pull via the same URL, no SSH credentials needed for the repo itself since it's public.)

## 8. Log in to ghcr.io as the `deploy` user

Create a GitHub Personal Access Token with `read:packages` scope only (your account → Settings → Developer settings → Personal access tokens → Tokens (classic)). Then on the VPS:

```bash
sudo -u deploy bash -c 'echo "<PAT>" | docker login ghcr.io -u l-desantis --password-stdin'
```

The credential is stored at `/home/deploy/.docker/config.json`.

## 9. First-time bootstrap of the running stack

After Tasks 5 and 6 of Plan C are done (encrypted `secrets.enc.env` committed, `deploy.yml` workflow exists), you can either:
- **Wait** for the next push to `main` to trigger the first deploy.
- **Manually** bootstrap by SSHing in and running the deploy commands once (mirrors the GHA flow):

```bash
sudo -u deploy bash -c '
  cd /opt/dev-trend
  git pull --ff-only
  SOPS_AGE_KEY_FILE=/etc/devtrend/age.key sops -d secrets.enc.env > .env
  chmod 600 .env
  export IMAGE_TAG=latest
  docker compose pull
  docker compose up -d
'
```

## 10. Firewall (optional but recommended)

CX22's default firewall allows everything. The app exposes `127.0.0.1:8000` (loopback only) so no inbound rule is needed; just confirm SSH (port 22) is open and consider rate-limiting it via `ufw`:

```bash
ufw allow 22/tcp
ufw --force enable
ufw limit ssh
```
````

- [ ] **Step 2: Commit the runbook**

```bash
git add docs/superpowers/runbooks/vps-bootstrap.md
git commit -m "doc: add VPS bootstrap runbook for production deploy"
```

- [ ] **Step 3: Operator runs the runbook on the VPS**

The operator works through the runbook end-to-end. At the end they have:
- A working `deploy` user that can `docker ps` over SSH from their laptop.
- An age public key (looks like `age1...`).
- An SSH private key (`~/.ssh/devtrend_deploy` on their laptop).

Wait for the operator to confirm these three items before proceeding.

---

## Task 5: Encode the age public key, store secrets

Now we close the loop: paste the VPS's age public key into `.sops.yaml`, encrypt the env file.

**Files:**
- Modify: `.sops.yaml`
- Create: `secrets.enc.env`

- [ ] **Step 1: Replace the placeholder in `.sops.yaml`**

Operator opens `.sops.yaml` and replaces `REPLACE_WITH_VPS_AGE_PUBLIC_KEY` with the actual `age1...` string from VPS bootstrap.

Final file looks like:

```yaml
creation_rules:
  - path_regex: secrets\.enc\.env$
    encrypted_regex: ".*"
    age: >-
      age1abcd...xyz
```

- [ ] **Step 2: Operator installs `sops` locally (for editing)**

```
! curl -fsSL https://github.com/getsops/sops/releases/download/v3.9.1/sops-v3.9.1.linux.amd64 -o /tmp/sops && sudo install /tmp/sops /usr/local/bin/sops
! sops --version
```

- [ ] **Step 3: Operator creates the plaintext env file (do NOT commit)**

Build a plaintext `secrets.plain.env` by **copying `.env`** (which is gitignored) and trimming non-secret lines. Keep only the secret-bearing keys:

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_ALLOWED_CHAT_IDS=...
NIM_API_KEY=...
OPENAI_API_KEY=...
GITHUB_TOKEN=...
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=...
```

Plus any **non-default** runtime config that differs between prod and dev (`LLM_PROVIDER=nim`, `EMBEDDING_PROVIDER=nim`, etc.). Non-secret runtime config can also stay here for convenience — there's no harm in encrypting public values.

- [ ] **Step 4: Encrypt with SOPS**

```
! sops --encrypt --input-type dotenv --output-type dotenv secrets.plain.env > secrets.enc.env
! shred -u secrets.plain.env
```

Expected: `secrets.enc.env` exists; its contents are base64-looking ciphertext with a `sops_age__list_0__map_recipient` block at the bottom. The plaintext file is gone.

- [ ] **Step 5: Verify decryption round-trips**

```
! sops --decrypt secrets.enc.env | head -5
```

(This requires `SOPS_AGE_KEY_FILE` pointed at a key that's listed as a recipient. The operator does **not** have the private key locally — by design. So this step should **fail** with "no matching keys" on the laptop. That's the correct state.)

The real verification is in Task 7 (Step 4) when the VPS decrypts during deploy.

- [ ] **Step 6: Commit**

```bash
git add .sops.yaml secrets.enc.env
git commit -m "feat(deploy): SOPS-encrypted secrets.enc.env keyed to VPS age recipient"
```

---

## Task 6: Add GitHub Secrets

`deploy.yml` needs three secrets:

1. **`DEPLOY_SSH_KEY`** — the ed25519 private key generated in Task 4 Step 6.
2. **`DEPLOY_SSH_HOST`** — the VPS IP or hostname.
3. **`DEPLOY_TELEGRAM_BOT_TOKEN`** — same as `TELEGRAM_BOT_TOKEN` in `.env`; duplicated here for the deploy-time notification step (which runs in GHA, not on the VPS).
4. **`DEPLOY_TELEGRAM_CHAT_ID`** — same as `TELEGRAM_CHAT_ID`.

- [ ] **Step 1: Operator adds the secrets**

Visit `https://github.com/l-desantis/dev-trend/settings/secrets/actions` → New repository secret.

Add each of:
- `DEPLOY_SSH_KEY` = full contents of `~/.ssh/devtrend_deploy` (including the BEGIN/END lines).
- `DEPLOY_SSH_HOST` = e.g. `1.2.3.4` or `devtrend.example.com`.
- `DEPLOY_TELEGRAM_BOT_TOKEN` = your bot token.
- `DEPLOY_TELEGRAM_CHAT_ID` = your chat id.

- [ ] **Step 2: Confirm the four secrets appear in the Secrets list**

---

## Task 7: Create `deploy.yml`

**Files:**
- Create: `.github/workflows/deploy.yml`

- [ ] **Step 1: Write `.github/workflows/deploy.yml`**

```yaml
name: Deploy

on:
  workflow_run:
    workflows: ["Build and Push"]
    types: [completed]
    branches: [main]
  workflow_dispatch:
    inputs:
      sha:
        description: "Short SHA tag to deploy (defaults to the latest main HEAD)"
        required: false

concurrency:
  group: deploy-prod
  cancel-in-progress: false

permissions:
  contents: read

jobs:
  deploy:
    if: >
      github.event_name == 'workflow_dispatch' ||
      (github.event.workflow_run.conclusion == 'success' &&
       github.event.workflow_run.head_branch == 'main')
    runs-on: ubuntu-24.04
    steps:
      - name: Checkout (for sha lookup)
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.workflow_run.head_sha || github.sha }}

      - name: Compute deploy SHA
        id: meta
        run: |
          if [ -n "${{ inputs.sha }}" ]; then
            echo "short_sha=${{ inputs.sha }}" >> "$GITHUB_OUTPUT"
          else
            echo "short_sha=$(git rev-parse --short=7 HEAD)" >> "$GITHUB_OUTPUT"
          fi
          echo "commit_subject=$(git log -1 --pretty=%s)" >> "$GITHUB_OUTPUT"

      - name: Set up SSH agent
        uses: webfactory/ssh-agent@v0.9.0
        with:
          ssh-private-key: ${{ secrets.DEPLOY_SSH_KEY }}

      - name: Add VPS host key
        run: |
          mkdir -p ~/.ssh
          ssh-keyscan -H ${{ secrets.DEPLOY_SSH_HOST }} >> ~/.ssh/known_hosts

      - name: Deploy
        id: deploy
        continue-on-error: true
        env:
          NEW_SHA: ${{ steps.meta.outputs.short_sha }}
        run: |
          ssh deploy@${{ secrets.DEPLOY_SSH_HOST }} bash -se <<'EOF'
            set -euo pipefail
            cd /opt/dev-trend
            mkdir -p .deploy
            if [ -f .deploy/current_tag ]; then
              cp .deploy/current_tag .deploy/previous_tag
            fi
            echo "sha-${NEW_SHA}" > .deploy/current_tag
            git fetch --quiet origin main
            git reset --hard origin/main
            SOPS_AGE_KEY_FILE=/etc/devtrend/age.key sops -d secrets.enc.env > .env
            chmod 600 .env
            export IMAGE_TAG="sha-${NEW_SHA}"
            docker compose pull
            docker compose up -d --remove-orphans
            # Health gate — 60s budget.
            for i in $(seq 1 30); do
              if curl -fsS http://127.0.0.1:8000/health > /dev/null; then
                echo "healthy after ${i} attempts"
                exit 0
              fi
              sleep 2
            done
            echo "health check failed after 60s"
            exit 1
          EOF

      - name: Rollback on failure
        if: steps.deploy.outcome == 'failure'
        id: rollback
        run: |
          ssh deploy@${{ secrets.DEPLOY_SSH_HOST }} bash -se <<'EOF'
            set -euo pipefail
            cd /opt/dev-trend
            if [ ! -f .deploy/previous_tag ]; then
              echo "no previous_tag file — cannot roll back"
              exit 1
            fi
            PREV=$(cat .deploy/previous_tag)
            export IMAGE_TAG="${PREV}"
            docker compose up -d --remove-orphans
            for i in $(seq 1 30); do
              if curl -fsS http://127.0.0.1:8000/health > /dev/null; then
                echo "rolled back to ${PREV} successfully"
                exit 0
              fi
              sleep 2
            done
            echo "rollback also failed — manual intervention required"
            exit 1
          EOF

      - name: Telegram notify (success)
        if: steps.deploy.outcome == 'success'
        env:
          BOT: ${{ secrets.DEPLOY_TELEGRAM_BOT_TOKEN }}
          CHAT: ${{ secrets.DEPLOY_TELEGRAM_CHAT_ID }}
          SHA: ${{ steps.meta.outputs.short_sha }}
          SUBJECT: ${{ steps.meta.outputs.commit_subject }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          curl -fsS -X POST "https://api.telegram.org/bot${BOT}/sendMessage" \
            --data-urlencode "chat_id=${CHAT}" \
            --data-urlencode "parse_mode=Markdown" \
            --data-urlencode "text=*DevTrend deployed* ✅%0A%60sha-${SHA}%60 — ${SUBJECT}%0A[run](${RUN_URL})"

      - name: Telegram notify (rollback)
        if: steps.deploy.outcome == 'failure' && steps.rollback.outcome == 'success'
        env:
          BOT: ${{ secrets.DEPLOY_TELEGRAM_BOT_TOKEN }}
          CHAT: ${{ secrets.DEPLOY_TELEGRAM_CHAT_ID }}
          SHA: ${{ steps.meta.outputs.short_sha }}
          SUBJECT: ${{ steps.meta.outputs.commit_subject }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          curl -fsS -X POST "https://api.telegram.org/bot${BOT}/sendMessage" \
            --data-urlencode "chat_id=${CHAT}" \
            --data-urlencode "parse_mode=Markdown" \
            --data-urlencode "text=*DevTrend deploy rolled back* ⚠️%0A%60sha-${SHA}%60 failed health check — restored previous image.%0A${SUBJECT}%0A[run](${RUN_URL})"

      - name: Telegram notify (total failure)
        if: steps.deploy.outcome == 'failure' && steps.rollback.outcome != 'success'
        env:
          BOT: ${{ secrets.DEPLOY_TELEGRAM_BOT_TOKEN }}
          CHAT: ${{ secrets.DEPLOY_TELEGRAM_CHAT_ID }}
          SHA: ${{ steps.meta.outputs.short_sha }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          curl -fsS -X POST "https://api.telegram.org/bot${BOT}/sendMessage" \
            --data-urlencode "chat_id=${CHAT}" \
            --data-urlencode "parse_mode=Markdown" \
            --data-urlencode "text=*DevTrend deploy FAILED — manual intervention required* ❌%0A%60sha-${SHA}%60 failed health check and rollback also failed.%0A[run](${RUN_URL})"

      - name: Fail the job if deploy failed
        if: steps.deploy.outcome == 'failure'
        run: exit 1
```

- [ ] **Step 2: Commit and push on a feature branch**

```bash
git checkout -b ci/add-deploy
git add .github/workflows/deploy.yml
git commit -m "feat(deploy): add health-gated deploy workflow with auto-rollback"
git push -u origin ci/add-deploy
```

- [ ] **Step 3: Open and merge the PR**

CI runs on the PR (lint/typecheck/test). Once green, merge to `main`. The merge-commit CI run triggers `Build and Push`, which on success triggers `Deploy`.

- [ ] **Step 4: Watch the first real deploy**

GitHub → Actions → "Deploy". Expected sequence:
1. Job starts, SSH agent + host key set up.
2. `Deploy` step runs ~30-90 s. Last log line: `healthy after N attempts`.
3. `Telegram notify (success)` runs and posts.

Verify in your Telegram chat: a message arrived saying *"DevTrend deployed ✅"*.

- [ ] **Step 5: Operator verifies the running container on the VPS**

```
! ssh deploy@<VPS_IP> "cd /opt/dev-trend && docker compose ps && cat .deploy/current_tag"
```

Expected: container `running (healthy)`, `current_tag` = `sha-<short>` matching the just-deployed commit.

---

## Task 8: Smoke-test auto-rollback

We deliberately break `/health` to confirm the rollback path actually fires.

**Files:**
- Modify (temporarily): `app/api/routes_health.py`

- [ ] **Step 1: Create a deliberately-broken health endpoint on a branch**

```bash
git checkout main && git pull
git checkout -b chaos/break-health
```

Edit `app/api/routes_health.py`:

```python
from fastapi import APIRouter

from app.config import get_settings
from app.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    raise RuntimeError("intentional chaos: testing rollback")
```

- [ ] **Step 2: Push, open and merge the chaos PR**

```bash
git add app/api/routes_health.py
git commit -m "chaos: deliberately break /health to verify rollback"
git push -u origin chaos/break-health
```

Open and merge the PR. (Unit tests for the health route, if any, will likely fail — that's fine for this controlled experiment; if CI blocks the merge, temporarily bypass with admin override **only for this test commit**, or move the chaos into a place CI doesn't cover. The cleanest variant: change the health route's response model to a value that won't validate, so CI's unit tests still pass but the live endpoint 500s.)

- [ ] **Step 3: Watch the deploy fail and roll back**

GitHub → Actions → "Deploy" for the chaos commit. Expected:
1. `Deploy` step **fails** after 60 s of health-check polling.
2. `Rollback on failure` step **succeeds**.
3. `Telegram notify (rollback)` posts the rollback message.

Verify in Telegram: a message saying *"DevTrend deploy rolled back ⚠️"*.

- [ ] **Step 4: Operator confirms VPS is on the previous image**

```
! ssh deploy@<VPS_IP> "cd /opt/dev-trend && cat .deploy/current_tag .deploy/previous_tag && docker compose ps"
```

Expected: `.deploy/previous_tag` is the old sha; the running container's image tag matches it.

- [ ] **Step 5: Revert the chaos commit**

```bash
git checkout main && git pull
git revert <chaos_commit_sha>
git push
```

This triggers a normal forward deploy back to good code. Verify success Telegram message.

---

## Task 9: Manual rollback workflow

For cases where a bad deploy passed `/health` but is misbehaving in subtler ways, we want a one-click way to roll back to any prior sha tag still on ghcr.io.

**Files:**
- Create: `.github/workflows/rollback.yml`

- [ ] **Step 1: Write `.github/workflows/rollback.yml`**

```yaml
name: Manual Rollback

on:
  workflow_dispatch:
    inputs:
      target_sha:
        description: "Short SHA tag to roll back to (e.g. abc1234)"
        required: true

concurrency:
  group: deploy-prod
  cancel-in-progress: false

permissions:
  contents: read

jobs:
  rollback:
    runs-on: ubuntu-24.04
    steps:
      - name: Set up SSH agent
        uses: webfactory/ssh-agent@v0.9.0
        with:
          ssh-private-key: ${{ secrets.DEPLOY_SSH_KEY }}

      - name: Add VPS host key
        run: |
          mkdir -p ~/.ssh
          ssh-keyscan -H ${{ secrets.DEPLOY_SSH_HOST }} >> ~/.ssh/known_hosts

      - name: Roll back
        id: rollback
        env:
          TARGET: ${{ inputs.target_sha }}
        run: |
          ssh deploy@${{ secrets.DEPLOY_SSH_HOST }} bash -se <<'EOF'
            set -euo pipefail
            cd /opt/dev-trend
            mkdir -p .deploy
            if [ -f .deploy/current_tag ]; then
              cp .deploy/current_tag .deploy/previous_tag
            fi
            echo "sha-${TARGET}" > .deploy/current_tag
            export IMAGE_TAG="sha-${TARGET}"
            docker compose pull
            docker compose up -d --remove-orphans
            for i in $(seq 1 30); do
              if curl -fsS http://127.0.0.1:8000/health > /dev/null; then
                echo "rolled back to sha-${TARGET}"
                exit 0
              fi
              sleep 2
            done
            echo "target image failed health check"
            exit 1
          EOF

      - name: Telegram notify
        if: always()
        env:
          BOT: ${{ secrets.DEPLOY_TELEGRAM_BOT_TOKEN }}
          CHAT: ${{ secrets.DEPLOY_TELEGRAM_CHAT_ID }}
          TARGET: ${{ inputs.target_sha }}
          STATUS: ${{ steps.rollback.outcome }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          if [ "${STATUS}" = "success" ]; then EMOJI="✅"; else EMOJI="❌"; fi
          curl -fsS -X POST "https://api.telegram.org/bot${BOT}/sendMessage" \
            --data-urlencode "chat_id=${CHAT}" \
            --data-urlencode "parse_mode=Markdown" \
            --data-urlencode "text=*Manual rollback to sha-${TARGET}* ${EMOJI}%0A[run](${RUN_URL})"
```

- [ ] **Step 2: Commit and merge via PR**

```bash
git checkout -b ci/add-rollback
git add .github/workflows/rollback.yml
git commit -m "ci: add manual rollback workflow"
git push -u origin ci/add-rollback
```

Open & merge PR.

- [ ] **Step 3: Test the manual rollback**

GitHub → Actions → "Manual Rollback" → "Run workflow" → enter a recent `sha-*` (not the current one, e.g. the previous deploy's sha) → run.

Expected: workflow succeeds, Telegram posts a manual-rollback success message. Verify on the VPS that `current_tag` is now the target.

Then run "Manual Rollback" again with the **latest** sha to restore normal state.

---

## Task 10: README — "Production deploy & secrets" section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append a new section to README**

```markdown
## Production deploy & secrets

DevTrend deploys automatically to a single Hetzner CX22 VPS on every push to `main`. The deploy is health-gated: if `/health` fails to come up within 60 s, the previous image is restored automatically and a Telegram message reports the rollback.

### Secrets (SOPS + age)

Production secrets are SOPS-encrypted at `secrets.enc.env` in this repo. The matching age private key lives on the VPS at `/etc/devtrend/age.key`. To edit secrets:

```bash
sops secrets.enc.env
```

This opens `$EDITOR` with the decrypted content; saving re-encrypts on close. Commit the updated `secrets.enc.env` and push — the next deploy will pick up the new values.

To add a contributor with edit access: append their age public key to `.sops.yaml`, then run `sops updatekeys secrets.enc.env`.

### Manual operations

- **Roll back to a previous build:** GitHub → Actions → "Manual Rollback" → enter the target short SHA (any `sha-*` tag still on `ghcr.io/l-desantis/dev-trend`).
- **Re-deploy a SHA:** same workflow.
- **First-time VPS setup:** see `docs/superpowers/runbooks/vps-bootstrap.md`.

### Image retention

The most recent 10 `sha-*` builds plus `latest` are kept on ghcr.io. Older builds are pruned weekly by `prune-ghcr.yml`.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "doc: README — production deploy & secrets section"
```

---

## Verification (run by operator — see CLAUDE.md)

End-to-end after all tasks:

1. **Forward deploy:**
   - Push a small no-op change to `main`.
   - Expected: within ~5 min — CI green → Build and Push green → Deploy green → Telegram success message. VPS `current_tag` matches the new short SHA.

2. **Rollback:**
   - Already covered by Task 8 if performed. Outcome: a deliberately bad `/health` triggered auto-rollback to the previous image and a Telegram alert.

3. **Manual rollback:**
   - Already covered by Task 9 Step 3.

4. **Secret rotation smoke:**
   - `! sops secrets.enc.env` (on a contributor laptop with their age key listed in `.sops.yaml`) — decrypt + re-encrypt cleanly.
   - On the VPS: confirm `! sops -d secrets.enc.env | head -1` succeeds.

5. **Concurrency:**
   - Push two commits to `main` within ~30s of each other.
   - Expected: two `Deploy` runs queue, never overlap (`concurrency: deploy-prod`, `cancel-in-progress: false`).

---

## Out of scope (explicit)

- **Postgres / Alembic.** Plan D. SQLite is still the runtime DB until then.
- **DB backups.** Deferred — recommended path (nightly `pg_dump` → Hetzner Storage Box via restic) documented in the design doc.
- **Zero-downtime deploys (blue/green).** Out of scope for single-VPS.
- **Monitoring / metrics (Prometheus, Sentry).** Deferred. Structured JSON logs + Telegram alerts are the safety net for now.
- **Log shipping.** Deferred. `docker compose logs` is enough today.
- **PR preview environments.** Out of scope.
