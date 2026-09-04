#!/usr/bin/env python3
"""
Google Flow video generator — reusable template (via useapi.net bridge)
=======================================================================
Copy this file into any agent, set TOKEN + EMAIL (or pass --token/--email),
and run:

    python3 flow_video_template.py --image photo.jpg --prompt "drone orbit of a home" --out video.mp4
    python3 flow_video_template.py --prompt "cartoon puppy in a meadow" --out kids.mp4   (text-to-video, no image)
    python3 flow_video_template.py --probe                                               (health check only)

REQUIREMENTS
------------
1. A useapi.net subscription ($15/mo, Stripe). Sign up -> they issue a token
   shaped like:  user:3048-xxxxxxxxxxxx
2. A Google account WITH Flow access linked inside useapi
   (the --email below must match the Google email you linked at app.useapi.net)

THE 8 PITFALLS (each one cost real debugging time — do not remove the workarounds)
----------------------------------------------------------------------------------
P1. CLOUDFLARE BLOCKS BARE REQUESTS.
    Every call MUST send browser headers (User-Agent + Accept + Referer) or you
    get 403 / error 1010 ("banned browser signature") — NOT a token problem.
    If everything suddenly 403s: it is this, not your subscription.

P2. UPLOAD RESPONSE NESTS THE REFERENCE ID.
    POST /assets/{email} returns:
        {"mediaGenerationId": {"mediaGenerationId": "user:3048-email:...-image:..."}}
    The usable reference string is the NESTED one. The bare UUID in
    media.name will fail video submission with "incorrect format".

P3. CREATE endpoint is /videos — NOT /jobs.
    POST /v1/google-flow/jobs        -> 405 Method Not Allowed
    POST /v1/google-flow/videos      -> correct
    GET  /v1/google-flow/jobs/{id}   -> polling only

P4. The reference parameter is "referenceImage_1" — NOT "startImage".
    "startImage" -> 400 "Parameter startImage has incorrect format".

P5. duration MUST be 8. 5 -> 400 Bad Request. 8 is the minimum.

P6. aspectRatio accepts "portrait" or "landscape" — NOT "9:16" / "16:9".

P7. Upload Content-Type MUST be image/jpeg (convert other formats first,
    e.g. png/webp -> jpg, or the upload 400s).

P8. INTERIORS MORPH if you animate a raw room photo directly.
    For rooms (kitchen/bedroom/living): generate a still first, QC it, then
    animate with a near-static camera. Exteriors/drone shots are fine direct.
"""
import argparse, json, sys, time, urllib.request, urllib.error

API = "https://api.useapi.net/v1/google-flow"

# ---------------------------------------------------------------- client ----
def make_client(token: str, email: str):
    """All requests go through here — browser headers are MANDATORY (P1)."""
    HEADERS = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://app.useapi.net/",
    }

    def call(method, url, data=None, raw=False, timeout=60):
        body, headers = None, dict(HEADERS)
        if data is not None:
            if isinstance(data, bytes):
                body = data
                headers["Content-Type"] = "image/jpeg"          # P7
            else:
                body = json.dumps(data).encode()
                headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            b = r.read()
            return b if raw else json.loads(b)

    return call, email


# ----------------------------------------------------------------- steps ----
def upload_reference(call, email, image_path):
    """Upload an image, return the reference string (handles the nested ID, P2)."""
    with open(image_path, "rb") as f:
        img = f.read()
    j = call("POST", f"{API}/assets/{email}", img, raw=True, timeout=90)
    j = json.loads(j) if isinstance(j, (bytes, str)) else j
    ref = j.get("mediaGenerationId")
    if isinstance(ref, dict):                      # nested shape (P2)
        ref = ref.get("mediaGenerationId")
    if not isinstance(ref, str) or "image:" not in ref:
        raise RuntimeError(f"upload returned no usable reference: {str(j)[:200]}")
    return ref


def submit_video(call, email, prompt, ref=None, duration=8, aspect="portrait",
                 seed=7, resolution="720p"):
    """Submit an Omni Flash job. duration must be >= 8 (P5)."""
    payload = {
        "model": "omni-flash",
        "email": email,
        "prompt": prompt,
        "aspectRatio": aspect,                     # portrait|landscape (P6)
        "resolution": resolution,
        "duration": duration,                      # 8 minimum (P5)
        "seed": seed,
        "async": True,
    }
    if ref:
        payload["referenceImage_1"] = ref          # NOT startImage (P4)
    j = call("POST", f"{API}/videos", payload, timeout=60)   # /videos not /jobs (P3)
    return j.get("jobid") or j.get("jobId") or j.get("job_id")


def poll_job(call, job_id, max_wait_s=540, quiet=False):
    """Poll until completed; return the video URL."""
    t0 = time.time()
    while time.time() - t0 < max_wait_s:
        r = call("GET", f"{API}/jobs/{job_id}", timeout=40)
        status = r.get("status", "?")
        if not quiet:
            print(f"  {int(time.time()-t0)}s: {status}")
        if status == "completed":
            url = ((r.get("response") or {}).get("media") or [{}])[0].get("videoUrl", "")
            return url
        if status in ("failed", "error"):
            raise RuntimeError(f"job failed: {str(r)[:300]}")
        time.sleep(10)
    raise TimeoutError(f"job {job_id} did not finish in {max_wait_s}s")


def download(url, out_path):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Chrome/125.0.0.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(out_path, "wb") as f:
        f.write(r.read())
    return out_path


# ------------------------------------------------------------------ main ----
def main():
    ap = argparse.ArgumentParser(description="Google Flow video via useapi bridge")
    ap.add_argument("--token", required=True, help="useapi token  user:3048-...")
    ap.add_argument("--email", required=True, help="linked Google email")
    ap.add_argument("--prompt", help="video prompt")
    ap.add_argument("--image", help="optional reference image (jpg)")
    ap.add_argument("--out", default="flow_video.mp4")
    ap.add_argument("--duration", type=int, default=8)
    ap.add_argument("--aspect", default="portrait", choices=["portrait", "landscape"])
    ap.add_argument("--probe", action="store_true", help="health check only")
    args = ap.parse_args()

    call, email = make_client(args.token, args.email)

    if args.probe:
        r = call("GET", f"{API}/accounts", timeout=30)
        accts = [a.get("email") for a in (r if isinstance(r, list) else r.get("accounts", []))]
        print("API OK. linked accounts:", accts or r)
        return

    if not args.prompt:
        ap.error("--prompt is required (or use --probe)")

    ref = None
    if args.image:
        print(f"Uploading reference {args.image} ...")
        ref = upload_reference(call, email, args.image)
        print(f"  ref: {ref[:60]}...")

    print("Submitting video job ...")
    job = submit_video(call, email, args.prompt, ref=ref,
                       duration=args.duration, aspect=args.aspect)
    print(f"  job: {job}")

    print("Polling ...")
    url = poll_job(call, job)
    download(url, args.out)
    import os
    print(f"SAVED: {args.out} ({os.path.getsize(args.out)} bytes)")


if __name__ == "__main__":
    main()
