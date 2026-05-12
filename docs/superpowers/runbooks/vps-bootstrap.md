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
