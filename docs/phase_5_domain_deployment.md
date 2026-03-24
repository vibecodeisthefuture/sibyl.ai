# Phase 5: sybilai.live Domain Deployment Roadmap

**Status:** Planning
**Domain:** sybilai.live (purchased via Namecheap)
**Target Deployment:** After ASRock ROMED8-2T motherboard arrives
**Last Updated:** 2026-03-21

---

## Executive Summary

This document provides a step-by-step roadmap for deploying the Sibyl dashboard to the internet via the sybilai.live domain. The process involves five parallel work streams that must be sequenced carefully:

1. **DNS Configuration** — Point sybilai.live to your home network
2. **Reverse Proxy + SSL** — Encrypt traffic with HTTPS
3. **Authentication** — Lock down the dashboard to invite-only access
4. **Email Setup** — Get sybilai.live email working
5. **Kubernetes Integration** — Run the dashboard in your homelab cluster

**Key insight:** Most of this can be planned and partially tested now, but full deployment waits on the ASRock motherboard and a stable K8s cluster.

---

## Prerequisites & Current Status

### What You Have Now
- ✅ Namecheap domain registration (sybilai.live)
- ✅ FastAPI + React dashboard (runs on localhost:8088)
- ✅ Stellar hosting package from Namecheap (we'll likely ignore this)
- ✅ PositiveSSL certificate (included; can be used for HTTPS)
- ✅ Ubiquiti UDM Pro router (with port forwarding capability)
- ✅ ISP internet connection (need to verify port 80/443 access)

### What's Missing / Pending
- ⏳ **ASRock ROMED8-2T motherboard** — Blocks full K8s deployment
- ❓ **Static public IP** — Check if your ISP provides this; if not, we need Dynamic DNS

### Assumptions Made
- You'll run Traefik (K8s-native) as the reverse proxy
- Cloudflare (free tier) is optional but recommended to hide your home IP
- Let's Encrypt will auto-renew SSL certificates
- The dashboard runs in a private K8s cluster (not exposed to the internet directly)

---

## Overall Deployment Sequence

Here's the order you should tackle these phases:

```
┌─────────────────────────────────────────┐
│ NOW: Phase 5A & 5B Planning             │ ← You are here
│ (DNS strategy, SSL options, routing)    │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ Phase 5C: Auth Layer (design & test)    │ ← Can start soon
│ (Build login page, test locally)        │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ WAIT: ASRock motherboard arrives        │ ← Hard blocker
│ (Spin up K8s cluster)                   │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│ Phase 5A Execution: DNS + DDNS setup    │ ← Go live
│ Phase 5B Execution: Reverse proxy + SSL │
│ Phase 5D: Email configuration           │
│ Phase 5E: K8s integration               │
└─────────────────────────────────────────┘
```

---

## Phase 5A: DNS Configuration

### What DNS Does (Simple Explanation)

Think of DNS like the phone book of the internet. When someone visits `sybilai.live`, their computer asks a DNS server: "What's the IP address for sybilai.live?" The answer points them to your home network.

Currently, sybilai.live's DNS is managed by Namecheap. We need to tell Namecheap: "When someone visits sybilai.live, send them to MY home IP address."

### Core Challenge: Your IP Address Changes

Most home internet connections have a **dynamic IP**. Your ISP might assign you `203.0.113.42` today and `203.0.113.99` next month. DNS records don't update automatically, so visitors would see a broken site.

**Solution:** Use Dynamic DNS (DDNS) to automatically update the DNS record whenever your IP changes.

---

### Option A: Simple Static IP (Preferred, if available)

**Best case:** Your ISP gives you a static public IP that never changes.

**Action items:**
1. Contact your ISP and ask: "Do I have a static public IP?" (Usually a premium feature)
2. If yes, get the IP address (e.g., `203.0.113.42`)
3. Log into Namecheap DNS settings
4. Create an `A` record: `@ → 203.0.113.42`
5. Wait 10 minutes for propagation
6. Test: `ping sybilai.live` should resolve to your IP

**Pros:** Simple, no extra software needed
**Cons:** Might cost extra; not all ISPs offer it

---

### Option B: Dynamic DNS with DDNS Client (Recommended)

If your ISP doesn't provide a static IP, use DDNS:

#### Step 1: Enable DDNS in Namecheap

1. Log into Namecheap → Manage sybilai.live domain
2. Go to **Advanced DNS**
3. Under **Dynamic DNS**, enable it
4. Namecheap will show you a "DDNS password" (not your account password)
5. Save this password somewhere secure

#### Step 2: Run a DDNS Client in Your Homelab

A DDNS client is a small program that:
- Checks your current public IP every few minutes
- If it changed, tells Namecheap the new IP
- Namecheap updates the DNS record automatically

**Recommended DDNS client for your K8s cluster:**

Once your K8s cluster is running, deploy `ddns-updater` as a Kubernetes CronJob:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: namecheap-ddns
spec:
  schedule: "*/15 * * * *"  # Every 15 minutes
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: ddns-updater
            image: ddnsmonitor/ddns-updater:latest
            env:
            - name: PROVIDER
              value: "namecheap"
            - name: DOMAIN
              value: "sybilai.live"
            - name: NAMECHEAP_DDNS_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: namecheap-secret
                  key: password
          restartPolicy: OnFailure
```

**Before that:** Use a simpler DDNS client on a single machine:
- **Linux/Raspberry Pi:** `ddclient` (apt-get install ddclient)
- **Docker:** `ddns-updater` Docker image
- **Manual:** A cron job that calls curl to update Namecheap API

---

### Option C: Cloudflare as a DNS Proxy (Most Robust)

Cloudflare is a free service that acts as a middleman between visitors and your home IP.

**Why use Cloudflare?**
- **Hides your IP:** Visitors see Cloudflare's IP, not yours (better privacy/security)
- **DDoS protection:** Blocks attacks before they reach your homelab
- **Free SSL/TLS:** Built-in HTTPS support
- **Works with DDNS:** Cloudflare can auto-update your IP via API

**Setup Steps:**

1. Sign up for Cloudflare (free tier)
2. Add sybilai.live to Cloudflare
3. Cloudflare will give you new nameservers (e.g., `ns1.cloudflare.com`)
4. In Namecheap, change the nameservers to Cloudflare's
5. In Cloudflare dashboard, create an `A` record pointing to your home IP
6. Enable Cloudflare's DDNS API for automatic IP updates

**Cloudflare DDNS via API:**

Create a script that runs every 15 minutes:

```bash
#!/bin/bash
# Get your current public IP
CURRENT_IP=$(curl -s https://api.ipify.org)

# Call Cloudflare API to update the A record
curl -X PUT "https://api.cloudflare.com/client/v4/zones/ZONE_ID/dns_records/RECORD_ID" \
  -H "Authorization: Bearer YOUR_CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"A\",\"name\":\"sybilai.live\",\"content\":\"$CURRENT_IP\",\"ttl\":120}"
```

**Recommendation:** Use Cloudflare + DDNS. It gives you the best combination of privacy, security, and reliability.

---

### DNS Configuration Checklist

- [ ] Determine if ISP provides static IP (call and ask)
- [ ] If static: Get IP, create A record in Namecheap
- [ ] If dynamic: Choose DDNS (Namecheap + client) or Cloudflare
- [ ] Set up DDNS client (will do after K8s cluster is ready)
- [ ] Test DNS resolution: `nslookup sybilai.live` should return your IP
- [ ] Verify TTL is low (60-120 seconds) so updates propagate quickly

---

## Phase 5B: Reverse Proxy + SSL/TLS

### What a Reverse Proxy Does

Think of a reverse proxy as a **receptionist** in a building:

- Visitors arrive at the front door (port 80 for HTTP, port 443 for HTTPS)
- The receptionist (reverse proxy) checks the request and directs it to the right office (your dashboard running on port 8088)
- The receptionist also handles security: encryption, authentication, etc.

In your case, **Traefik** will be the receptionist running inside Kubernetes.

---

### Option A: Let's Encrypt (Recommended)

Let's Encrypt is a **free, automated SSL certificate authority**. Traefik can request a certificate automatically and renew it before expiration.

**How it works:**

1. You deploy Traefik in K8s with Let's Encrypt configured
2. Someone visits `https://sybilai.live`
3. Traefik automatically requests a free SSL certificate from Let's Encrypt
4. Let's Encrypt verifies you own the domain (via DNS or HTTP challenge)
5. Traefik gets the certificate and encrypts all traffic

**Why this is great:**
- ✅ Free
- ✅ Automatic renewal (no manual intervention)
- ✅ Works perfectly for Kubernetes

**Traefik + Let's Encrypt Helm Values:**

```yaml
traefik:
  ingressClass:
    enabled: true
    isDefaultClass: true

  ports:
    web:
      redirectTo: websecure  # Force HTTPS
    websecure:
      tls:
        enabled: true

  certificatesResolvers:
    letsencrypt:
      acme:
        email: admin@sybilai.live  # Your email (for cert renewal alerts)
        storage: /data/acme.json
        httpChallenge:
          entryPoint: web
        # OR use DNS challenge (more reliable for home networks):
        # dnsChallenge:
        #   provider: cloudflare
        #   resolvers: ["1.1.1.1:53"]
```

---

### Option B: Use PositiveSSL (From Namecheap)

Your Namecheap package includes a PositiveSSL certificate. This is a traditional certificate you install manually.

**Pros:**
- Already paid for
- Works immediately (no verification delay)

**Cons:**
- Manual renewal required every year
- Requires manual installation in Traefik
- More tedious for a homelab

**Only use this if Let's Encrypt fails for some reason.**

---

### Port Forwarding on UDM Pro

To expose your dashboard to the internet, the Ubiquiti UDM Pro router must forward ports 80 and 443 to your Traefik container.

**Steps:**

1. Log into UDM Pro web UI
2. Go to **Settings → Port Forwarding** (or **Firewall → Port Forwarding**)
3. Create two rules:
   - **HTTP:** External port 80 → Internal port 80 (to K8s worker node)
   - **HTTPS:** External port 443 → Internal port 443 (to K8s worker node)
4. The "internal" IP should be one of your K8s worker nodes (e.g., `192.168.1.100`)
5. Save and test

**Important:** Traefik will run on every K8s node, but only one needs to be reachable. If you have 3 Dell Optiplexes + 1 EPYC, pick one as the "ingress node" for port forwarding.

---

### ISP Port Blocking Check

Some ISPs block ports 80 and 443 to prevent home servers.

**Test before full deployment:**

```bash
# From outside your network, test if ports are reachable
nmap -p 80,443 YOUR_PUBLIC_IP
# Or use an online tool: https://www.canyouseeme.org/
```

If ports are blocked, you'll need to contact your ISP or use alternative ports (e.g., 8080 instead of 80). This complicates DNS setup, so resolve this early.

---

### Traefik Ingress for Sibyl Dashboard

Once Traefik is running and SSL is set up, create a Kubernetes Ingress to route sybilai.live to your dashboard:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: sibyl-dashboard
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"  # Use Let's Encrypt
spec:
  ingressClassName: traefik
  tls:
  - hosts:
    - sybilai.live
    secretName: sibyl-tls
  rules:
  - host: sybilai.live
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: sibyl-dashboard
            port:
              number: 8088
```

---

### SSL/TLS Configuration Checklist

- [ ] Verify ISP doesn't block ports 80/443
- [ ] Plan Traefik deployment in K8s (will do after cluster is ready)
- [ ] Decide: Let's Encrypt or PositiveSSL
- [ ] Configure UDM Pro port forwarding (80 → node, 443 → node)
- [ ] Plan Traefik Ingress route for sybilai.live
- [ ] Test HTTPS locally on localhost before going live

---

## Phase 5C: Authentication Layer (Invite-Only Access)

### Why You Need This

Right now, `localhost:8088` is open to anyone on your local network. Once it's on the internet at `sybilai.live`, it's exposed to the entire world. We need to lock it down so only invited users can access it.

### Approach 1: HTTP Basic Auth (Simplest)

**What it does:** Browser pops up a username/password dialog before showing the page.

**Pros:**
- Simple to set up
- Requires no code changes
- Works in Traefik middleware

**Cons:**
- Less user-friendly (modal popup every time)
- Only supports very basic credentials
- No logout option (user closes browser to sign out)

**Traefik BasicAuth Middleware:**

```yaml
apiVersion: traefik.containo.us/v1alpha1
kind: Middleware
metadata:
  name: sibyl-basicauth
spec:
  basicAuth:
    secret: sibyl-basicauth-secret
---
apiVersion: v1
kind: Secret
metadata:
  name: sibyl-basicauth-secret
type: Opaque
stringData:
  users: |
    admin:$apr1$zGlcQcEd$vNvwFMRO8SG/6OZfXMQpN0
    # ^ Use htpasswd to generate: htpasswd -c auth.txt admin
```

Then add to Ingress:

```yaml
metadata:
  annotations:
    traefik.ingress.kubernetes.io/middleware: sibyl-basicauth
```

---

### Approach 2: JWT-Based Auth Page (Better UX)

A lightweight custom login page that users see once, then get a session cookie.

**How it works:**

1. User visits `sybilai.live`
2. Middleware checks for a valid JWT cookie
3. If missing → redirect to `/login`
4. User enters password → receives JWT token in cookie
5. Dashboard is now accessible

**Setup:**

You'll need to:
1. Add a small auth service (can be a simple Python/Node service)
2. Modify the React frontend to check for auth
3. Add Traefik middleware to enforce auth

**Simple auth service (Flask example):**

```python
from flask import Flask, request, jsonify
import jwt
import datetime

app = Flask(__name__)
SECRET_KEY = "your-secret-key-here"
VALID_PASSWORD = "your-invite-password"

@app.route('/login', methods=['POST'])
def login():
    password = request.json.get('password')
    if password == VALID_PASSWORD:
        token = jwt.encode(
            {'exp': datetime.datetime.utcnow() + datetime.timedelta(days=30)},
            SECRET_KEY,
            algorithm='HS256'
        )
        return jsonify({'token': token})
    return jsonify({'error': 'Invalid password'}), 401

@app.route('/verify', methods=['GET'])
def verify():
    token = request.cookies.get('auth_token')
    try:
        jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        return jsonify({'valid': True})
    except:
        return jsonify({'valid': False}), 401
```

**React frontend changes:**

```jsx
import { useEffect, useState } from 'react';

export default function App() {
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Check if user is authenticated
    fetch('/api/verify')
      .then(res => res.ok ? setAuthenticated(true) : setAuthenticated(false))
      .catch(() => setAuthenticated(false))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p>Loading...</p>;

  if (!authenticated) {
    return <LoginPage onSuccess={() => setAuthenticated(true)} />;
  }

  return <Dashboard />;
}

function LoginPage({ onSuccess }) {
  const [password, setPassword] = useState('');

  const handleLogin = async (e) => {
    e.preventDefault();
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password })
    });
    if (res.ok) {
      const { token } = await res.json();
      document.cookie = `auth_token=${token}; max-age=${30*24*3600}; path=/`;
      onSuccess();
    } else {
      alert('Invalid password');
    }
  };

  return (
    <div style={{ padding: '50px', textAlign: 'center' }}>
      <h1>Sibyl.ai</h1>
      <p>Invite-only access. Enter your password:</p>
      <form onSubmit={handleLogin}>
        <input
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button type="submit">Access</button>
      </form>
    </div>
  );
}
```

---

### Landing Page Design

Whether you use Basic Auth or JWT, the page visitors see should communicate:

```
╔════════════════════════════════════════╗
║                                        ║
║           Sibyl.ai Dashboard           ║
║                                        ║
║    Currently in private development    ║
║                                        ║
║        Invite-only access required     ║
║                                        ║
║    [Enter Password]     [Submit]       ║
║                                        ║
╚════════════════════════════════════════╝
```

**Copy suggestion:**

> **Sibyl.ai** is currently in private development. This dashboard is invite-only. If you received an access code, enter it below. If you don't have one yet, contact the Sibyl team.

---

### Authentication Checklist

- [ ] Decide: Basic Auth (simple) vs JWT page (better UX)
- [ ] Generate Basic Auth secret if using basic auth
- [ ] Or design login page and auth service if using JWT
- [ ] Test locally: `localhost:8088` with auth enabled
- [ ] Create Traefik middleware for auth enforcement
- [ ] Plan: How will you distribute invite codes/passwords to users?

---

## Phase 5D: Email Setup

### What You're Setting Up

Your Namecheap package includes email hosting. We'll configure MX records so:
- `admin@sybilai.live` receives emails
- `alerts@sybilai.live` receives system notifications (digests, alerts)
- The Narrator can send email notifications (in addition to ntfy.sh)

---

### Step 1: Create Mailboxes in Namecheap

1. Log into Namecheap → Manage sybilai.live
2. Go to **Email** section
3. Create two mailboxes:
   - `admin@sybilai.live` (for you)
   - `alerts@sybilai.live` (for system notifications)
4. Set passwords (store securely)

**Suggested setup:**
- **Admin mailbox:** Forward to your personal email (e.g., your Gmail)
- **Alerts mailbox:** Archive only (don't forward; check periodically for digests)

---

### Step 2: Verify MX Records

Once mailboxes are created, Namecheap automatically adds MX records. Verify they exist:

```bash
nslookup -type=MX sybilai.live
# Should show something like:
# mail.sybilai.live MX preference 10
```

---

### Step 3: Wire Narrator to Email

Modify the Narrator (or your alert system) to send emails:

**Example: Python/FastAPI Narrator using `smtplib`:**

```python
import smtplib
from email.mime.text import MIMEText

def send_alert_email(subject: str, body: str):
    smtp_server = "mail.sybilai.live"
    smtp_port = 587
    sender = "alerts@sybilai.live"
    sender_password = "YOUR_MAILBOX_PASSWORD"  # Store in env var!
    recipient = "admin@sybilai.live"

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = recipient

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(sender, sender_password)
        server.send_message(msg)

# Call this in your alert logic:
send_alert_email("Sibyl Digest", "Here's today's summary...")
```

**Environment variables (in Kubernetes secret):**

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: email-credentials
type: Opaque
stringData:
  SMTP_HOST: mail.sybilai.live
  SMTP_PORT: "587"
  SMTP_USER: alerts@sybilai.live
  SMTP_PASSWORD: <your-password>
```

---

### Email Checklist

- [ ] Create admin@sybilai.live and alerts@sybilai.live in Namecheap
- [ ] Verify MX records are set
- [ ] Set up forwarding (admin → your personal email)
- [ ] Test: Send test email to admin@sybilai.live
- [ ] Store SMTP credentials in Kubernetes secrets
- [ ] Integrate email into Narrator/alert system
- [ ] Document which emails should notify whom

---

## Phase 5E: Kubernetes Integration

### Architecture Overview

Your deployment will look like this:

```
┌─────────────────────────────────────────────────────┐
│ Internet (sybilai.live)                             │
│         ↓ (HTTPS port 443)                          │
├─────────────────────────────────────────────────────┤
│ Ubiquiti UDM Pro Router                             │
│  (port forward 443 → K8s node 192.168.1.100:443)   │
├─────────────────────────────────────────────────────┤
│ Kubernetes Cluster (3x Dell Optiplex + EPYC)        │
│                                                      │
│  ┌──────────────────────────────────────────────┐  │
│  │ Traefik Ingress Controller                   │  │
│  │  (listens on :80 and :443)                   │  │
│  │  (enforces TLS, auth middleware)             │  │
│  └──────────────────────────────────────────────┘  │
│         ↓                                            │
│  ┌──────────────────────────────────────────────┐  │
│  │ Sibyl Dashboard Deployment                   │  │
│  │  (FastAPI backend + React frontend)          │  │
│  │  (3 replicas for HA)                         │  │
│  │  Service: port 8088 (internal)               │  │
│  └──────────────────────────────────────────────┘  │
│         ↓                                            │
│  ┌──────────────────────────────────────────────┐  │
│  │ Other Services (Narrator, etc.)              │  │
│  │  (communicate internally via K8s DNS)        │  │
│  └──────────────────────────────────────────────┘  │
│                                                      │
└─────────────────────────────────────────────────────┘
```

---

### Kubernetes Deployment Files

**1. Namespace (optional but recommended):**

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: sibyl
```

**2. ConfigMap for dashboard config:**

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: sibyl-config
  namespace: sibyl
data:
  api_url: "https://sybilai.live/api"
  environment: "production"
```

**3. Deployment:**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sibyl-dashboard
  namespace: sibyl
spec:
  replicas: 3  # High availability
  selector:
    matchLabels:
      app: sibyl-dashboard
  template:
    metadata:
      labels:
        app: sibyl-dashboard
    spec:
      containers:
      - name: dashboard
        image: registry.sybilai.live/sibyl-dashboard:latest  # Your image
        ports:
        - containerPort: 8088
          name: http
        env:
        - name: API_URL
          valueFrom:
            configMapKeyRef:
              name: sibyl-config
              key: api_url
        livenessProbe:
          httpGet:
            path: /health
            port: 8088
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /ready
            port: 8088
          initialDelaySeconds: 5
          periodSeconds: 10
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
```

**4. Service:**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: sibyl-dashboard
  namespace: sibyl
spec:
  selector:
    app: sibyl-dashboard
  ports:
  - port: 8088
    targetPort: 8088
    name: http
  type: ClusterIP  # Internal only; Traefik is the external gateway
```

**5. Ingress (connects to Traefik):**

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: sibyl-dashboard
  namespace: sibyl
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: websecure
    traefik.ingress.kubernetes.io/router.tls: "true"
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    traefik.ingress.kubernetes.io/middleware: sibyl-auth@kubernetescrd
spec:
  ingressClassName: traefik
  tls:
  - hosts:
    - sybilai.live
    secretName: sibyl-tls
  rules:
  - host: sybilai.live
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: sibyl-dashboard
            port:
              number: 8088
```

---

### Health Checks (Liveness & Readiness Probes)

The FastAPI backend must expose two endpoints:

```python
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
async def health():
    """Liveness probe: is the service running?"""
    return {"status": "alive"}

@app.get("/ready")
async def ready():
    """Readiness probe: is it ready to serve traffic?"""
    # Check database connectivity, etc.
    db_connected = check_database()
    return {"ready": db_connected}
```

Kubernetes uses these probes to:
- **Liveness:** Restart the pod if it stops responding
- **Readiness:** Remove from load balancer if not ready

---

### Staging vs. Production

Before deploying to `sybilai.live`, test everything at a staging URL:

```yaml
# Staging Ingress (on a different hostname)
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: sibyl-dashboard-staging
  namespace: sibyl
spec:
  ingressClassName: traefik
  rules:
  - host: staging-sibyl.home.local  # Or a test subdomain
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: sibyl-dashboard
            port:
              number: 8088
```

Test everything on staging before pointing sybilai.live at production.

---

### Kubernetes Checklist

- [ ] ASRock motherboard arrives and K8s cluster is operational
- [ ] Create namespace, ConfigMap, Deployment, Service
- [ ] Add health check endpoints to FastAPI
- [ ] Deploy Traefik with Let's Encrypt support
- [ ] Create Ingress routes (staging first, then production)
- [ ] Test on staging URL before sybilai.live goes live
- [ ] Set up monitoring (kubectl logs, pod status)
- [ ] Document how to scale (increase replicas if needed)

---

## Dependencies & Blockers

### Hard Blockers

| Blocker | Status | Impact | ETA |
|---------|--------|--------|-----|
| ASRock ROMED8-2T motherboard | Pending | Cannot deploy K8s cluster | TBD |
| ISP port 80/443 access | Unknown | Cannot expose dashboard to internet | Test ASAP |
| Static or stable dynamic IP | Unknown | DNS will break if IP changes too often | Test ASAP |

### Soft Dependencies

| Dependency | Status | Impact | Workaround |
|------------|--------|--------|------------|
| Nameheap DNS/DDNS | Ready | DNS won't resolve sybilai.live | Use Cloudflare |
| SSL certificate | Ready | HTTPS won't work | Use PositiveSSL instead of Let's Encrypt |
| Email service | Ready | Email notifications won't work | Use ntfy.sh only |

---

### Pre-Deployment Verification Tasks

**Do these NOW, don't wait for the motherboard:**

1. **Test ISP port access:**
   ```bash
   # From outside your network, check if ports are open
   nmap -p 80,443 YOUR_PUBLIC_IP
   # Or: https://www.canyouseeme.org/
   ```
   **Action:** If blocked, call your ISP and request port unblocking.

2. **Get your public IP:**
   ```bash
   curl https://api.ipify.org
   ```
   **Action:** Check if it changes. If stable → static IP. If it changes → need DDNS.

3. **Test Namecheap DNS access:**
   - Log into Namecheap
   - Verify you can access DNS management
   - Take screenshots for reference

4. **Test basic Docker/K8s concepts locally:**
   - Run Traefik + a test app in Docker Compose
   - Verify reverse proxy + SSL concepts locally
   - Test auth middleware

---

## Estimated Timeline

### Phase 5A: DNS Configuration
- **Planning:** Done
- **Cloudflare setup (if chosen):** 1-2 hours
- **DDNS client configuration:** 2-3 hours (after K8s cluster ready)
- **Testing:** 1 hour
- **Total:** 4-6 hours spread over 2-3 weeks

### Phase 5B: Reverse Proxy + SSL
- **Traefik in K8s:** 2-3 hours (after K8s cluster ready)
- **Let's Encrypt integration:** 1-2 hours
- **Port forwarding setup:** 1 hour
- **Testing:** 2-3 hours
- **Total:** 6-9 hours

### Phase 5C: Authentication
- **Basic Auth setup:** 1 hour
- **Or JWT + login page:** 4-6 hours (requires code changes)
- **Testing:** 1-2 hours
- **Total:** 2-8 hours (depends on approach)

### Phase 5D: Email Setup
- **Mailbox creation:** 30 minutes
- **Narrator integration:** 1-2 hours
- **Testing:** 30 minutes
- **Total:** 2-3 hours

### Phase 5E: Kubernetes Integration
- **K8s cluster setup:** ~2 days (but this is separate from Phase 5)
- **Deployment files:** 2-3 hours
- **Staging testing:** 2-3 hours
- **Production deployment:** 1-2 hours
- **Total:** 6-9 hours (K8s cluster not included)

### Overall Timeline

```
Week 1-2:  Phase 5A planning, DNS strategy, ISP verification
Week 3:    Phase 5C design (auth layer)
Week 4:    ASRock motherboard arrives, K8s cluster spinup begins
Week 5-6:  K8s cluster stabilizes, Phases 5B + 5E deployment
Week 7:    Email integration (Phase 5D), staging testing
Week 8:    Go live on sybilai.live
```

**Realistic full timeline: 6-8 weeks** (heavily dependent on K8s cluster stability)

---

## Stakeholder Action Items (Rafael)

### Immediate (This Week)

- [ ] **Verify ISP port access:** Test if ports 80/443 are blocked
  - Use `https://www.canyouseeme.org/?port=80`
  - If blocked, call ISP and request port unblocking

- [ ] **Check for static IP:** Contact ISP
  - Ask: "Do I have a static public IP?"
  - Get the IP address if yes
  - Determine if it changes monthly

- [ ] **Create Cloudflare account (optional but recommended)**
  - Go to `https://www.cloudflare.com/`
  - Sign up free tier
  - Have it ready for Phase 5A

### Before K8s Cluster Goes Live (Weeks 2-3)

- [ ] **Review DNS options:** Choose between static IP, DDNS, or Cloudflare
  - Decision: _____ (static / DDNS / Cloudflare)

- [ ] **Review auth approach:** Choose between Basic Auth or JWT
  - Decision: _____ (Basic Auth / JWT page)

- [ ] **Plan invite distribution:** How will you share access codes?
  - Via email? Direct messaging? In-person?

### After ASRock Motherboard Arrives (Week 4+)

- [ ] **Spin up K8s cluster** (detailed steps in separate doc)

- [ ] **Test dashboard locally on K8s**
  - Deploy to staging URL first
  - Verify HTTPS works
  - Test authentication

- [ ] **Deploy to sybilai.live**
  - Point DNS to your home IP (or Cloudflare)
  - Enable port forwarding on UDM Pro
  - Wait for DNS propagation (~10 minutes)

- [ ] **Smoke test:** Visit `https://sybilai.live` from your phone
  - Should see login page
  - Should be HTTPS (lock icon in browser)
  - Should ask for password

### Ongoing Maintenance

- [ ] **Monitor DDNS updates** (if using dynamic IP)
  - Check logs: DDNS client should update every 15 minutes
  - If stuck, troubleshoot

- [ ] **Monitor SSL certificate renewal** (if using Let's Encrypt)
  - Should auto-renew 30 days before expiration
  - Traefik handles this automatically

- [ ] **Review email notifications** (Phase 5D)
  - Check `alerts@sybilai.live` weekly for digest summaries

---

## Technical Reference

### Useful Commands

```bash
# Check DNS resolution
nslookup sybilai.live
dig sybilai.live
host sybilai.live

# Check SSL certificate
openssl s_client -connect sybilai.live:443

# Test HTTP → HTTPS redirect
curl -i http://sybilai.live  # Should 301 to https://

# Check if ports are open (from outside your network)
nmap -p 80,443 YOUR_PUBLIC_IP

# View Traefik logs in K8s
kubectl logs -n kube-system deployment/traefik -f

# Test DDNS (manual update)
curl "https://dynamicdns.park-your-domain.com/update?host=sybilai&domain=live&password=YOUR_DDNS_PASSWORD&ip=203.0.113.42"
```

### Configuration Files Location (K8s)

```
/home/admin/sibyl-k8s/
├── namespace.yaml
├── configmap.yaml
├── deployment.yaml
├── service.yaml
├── ingress.yaml
├── traefik-values.yaml
└── cert-manager-values.yaml
```

### Secrets Management (Kubernetes)

Store sensitive data in K8s Secrets, not ConfigMaps:

```bash
# Create a secret
kubectl create secret generic namecheap-ddns \
  --from-literal=password=YOUR_DDNS_PASSWORD \
  -n sibyl

# View secrets (base64 encoded)
kubectl get secret -n sibyl
kubectl describe secret namecheap-ddns -n sibyl

# Reference in Pod env var:
env:
- name: DDNS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: namecheap-ddns
      key: password
```

---

## FAQ

**Q: Why not just use Namecheap Stellar hosting?**
A: Stellar is a shared hosting platform. Your K8s cluster gives you full control, better security, and the ability to run multiple services. Stellar would be overkill for a private dashboard.

**Q: Can I use a subdomain instead of the root domain?**
A: Yes! You could use `dashboard.sybilai.live` instead of just `sybilai.live`. This lets you host other services on the root domain later.

**Q: What if my ISP blocks port 443?**
A: You can use a different port (e.g., 8443) and update DNS, but this complicates setup. Contact your ISP first.

**Q: Do I need a static IP?**
A: Not required if you use DDNS (Namecheap or Cloudflare). DDNS automatically updates DNS when your IP changes.

**Q: Is Let's Encrypt reliable for a homelab?**
A: Yes! It's the gold standard for free SSL. Auto-renewal in Traefik is transparent.

**Q: Can I test this locally first?**
A: Absolutely! Run Traefik + dashboard in Docker Compose locally to verify the architecture. Detailed local testing guide in Phase 5C.

**Q: What if someone DDoS attacks my home network?**
A: Cloudflare (free tier) blocks many DDoS attacks. For a homelab, it's good enough. ISP-level DDoS protection is premium.

---

## Next Steps

1. **This week:** Complete ISP verification tasks
2. **Week 2-3:** Make DNS/auth decisions, review with team
3. **Week 4+:** After K8s cluster is ready, execute Phases 5A-5E in order
4. **Week 8:** Go live on sybilai.live

---

**Document Version:** 1.0
**Last Updated:** 2026-03-21
**Owner:** Sibyl.ai Infrastructure Team
**Status:** Ready for Phase 5A planning
