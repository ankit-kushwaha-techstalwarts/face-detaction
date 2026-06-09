# FaceAttend — Trusting the Self-Signed SSL Certificate

FaceAttend runs on **HTTPS (port 5443)** so that Safari and Chrome on iPhones, iPads, and
Macs can access the webcam/camera (`getUserMedia` API is blocked on plain HTTP by Apple).

Because the certificate is self-signed (not from a public CA), each device needs to be told
to trust it once. Follow the steps for your device below.

---

## 1. Find Your Server's IP Address

On the server machine, run:

```bash
# macOS / Linux
hostname -I        # or: ifconfig | grep "inet "

# Windows
ipconfig           # look for "IPv4 Address"
```

The URL you will use on every device is:  `https://<SERVER-IP>:5443`

Example: `https://192.168.1.42:5443`

---

## 2. macOS (Chrome / Firefox)

These browsers use their own certificate store, so a one-time browser bypass is enough.

1. Open `https://<SERVER-IP>:5443` in Chrome or Firefox.
2. Click **Advanced** → **Proceed to … (unsafe)**.
3. Done — the camera will now work.

**To permanently trust (optional — removes the warning):**

1. Download the certificate:
   ```
   https://<SERVER-IP>:5443/cert.pem
   ```
   (Add a route for this — see note below, or copy `cert.pem` via USB/AirDrop.)
2. Double-click the downloaded `cert.pem` file → Keychain Access opens.
3. Find **faceattend.local** in the login keychain.
4. Double-click it → expand **Trust** → set **"When using this certificate"** to **Always Trust**.
5. Close and enter your Mac password to save.

> **Safari note**: Safari uses the macOS system keychain. You must complete the "permanently
> trust" steps above (or use Chrome/Firefox with the bypass) because Safari does not have
> an "Advanced → Proceed" option for self-signed certs.

---

## 3. iPhone / iPad (iOS / iPadOS 14+)

iOS requires the certificate to be **installed as a profile** AND **enabled for full trust**.
Two steps are mandatory.

### Step A — Install the certificate profile

**Option 1: Open directly from Safari on the device**

1. On the iPhone/iPad, open **Safari** and go to:
   ```
   https://<SERVER-IP>:5443/cert.pem
   ```
   (Safari will say "This website is trying to download a configuration profile.")
2. Tap **Allow** → tap **Close**.
3. Go to **Settings → General → VPN & Device Management**.
4. Under "Downloaded Profile", tap **faceattend.local**.
5. Tap **Install** (top right) → enter your passcode → tap **Install** again → **Done**.

**Option 2: Send via AirDrop / Email**

1. Copy `cert.pem` from the server folder to your Mac.
2. AirDrop or email it to the iPhone.
3. Tap the attachment → tap **Allow** → follow the same Settings steps above.

### Step B — Enable full trust for the certificate

After installing, you MUST do this second step or HTTPS will still fail:

1. Go to **Settings → General → About → Certificate Trust Settings**.
2. Under "Enable Full Trust For Root Certificates", toggle ON the switch next to
   **faceattend.local**.
3. Tap **Continue** on the warning dialog.

Now open Safari and go to `https://<SERVER-IP>:5443` — the camera will work.

---

## 4. Windows (Chrome / Edge)

1. Open `https://<SERVER-IP>:5443` in Chrome or Edge.
2. Click **Advanced** → **Proceed to … (unsafe)** — camera will work immediately.

**To permanently trust (removes the warning):**

1. Copy `cert.pem` to the Windows machine (USB, network share, etc.).
2. Rename it to `cert.crt` (Windows needs the `.crt` extension).
3. Double-click `cert.crt` → click **Install Certificate**.
4. Select **Local Machine** → click **Next**.
5. Choose **"Place all certificates in the following store"** → click **Browse**.
6. Select **Trusted Root Certification Authorities** → **OK** → **Next** → **Finish**.
7. Restart Chrome/Edge.

---

## 5. Android (Chrome)

Android does not allow trusting user certificates for HTTPS in normal Chrome without
device-level management (MDM). The recommended approach:

1. Open Chrome and go to `https://<SERVER-IP>:5443`.
2. Tap **Advanced** → **Proceed to site (unsafe)** — camera will then be available.

> If "Proceed" is not shown, Chrome's policy may be blocking it on your Android version.
> In that case, the best option for production use is to set up a proper reverse proxy
> with a real certificate (see **Production / nginx** section below).

---

## 6. Expose the Certificate for Download (optional)

Add this route to `app.py` so devices can download the cert directly over the network:

```python
@app.route('/cert.pem')
def download_cert():
    import os
    cert_path = os.path.join(os.path.dirname(__file__), 'cert.pem')
    return send_from_directory(os.path.dirname(__file__), 'cert.pem',
                               as_attachment=True,
                               mimetype='application/x-pem-file')
```

Then on any device visit `https://<SERVER-IP>:5443/cert.pem` to download.

---

## 7. Production Setup — nginx Reverse Proxy (recommended for permanent deployment)

For a real deployment, use a proper CA certificate (Let's Encrypt) behind nginx:

```nginx
# /etc/nginx/sites-available/faceattend
server {
    listen 443 ssl;
    server_name faceattend.yourdomain.gov.in;

    ssl_certificate     /etc/letsencrypt/live/faceattend.yourdomain.gov.in/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/faceattend.yourdomain.gov.in/privkey.pem;

    # Proxy to Flask (run on HTTP internally — no self-signed cert needed)
    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }

    # MJPEG camera feeds need longer timeouts
    location /video_feed/ {
        proxy_pass         http://127.0.0.1:5000;
        proxy_read_timeout 3600;
        proxy_buffering    off;
    }
}

server {
    listen 80;
    server_name faceattend.yourdomain.gov.in;
    return 301 https://$host$request_uri;
}
```

With a real domain + Let's Encrypt certificate, no manual cert trust is needed on any device.

---

## Quick Reference

| Device | Quick bypass | Permanent trust |
|--------|-------------|-----------------|
| macOS Chrome/Firefox | Advanced → Proceed | Keychain → Always Trust |
| macOS Safari | ❌ Must trust in Keychain | Keychain → Always Trust |
| iPhone / iPad | Must install profile + enable full trust | Settings → Certificate Trust Settings |
| Windows Chrome/Edge | Advanced → Proceed | certmgr → Trusted Root CA |
| Android Chrome | Advanced → Proceed | Not fully supported without MDM |
