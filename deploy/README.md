# Deploying Concord

The repo ships with two ready-to-go deploy paths. Pick whichever fits.

## Option A. Hugging Face Spaces (recommended for a free portfolio demo)

Free, 16 GB RAM, no cold-start within active hours. The URL looks like
`https://huggingface.co/spaces/<your-handle>/concord`.

### 1. Create the Space

1. Sign in at https://huggingface.co.
2. https://huggingface.co/new-space
3. Settings:
   - **Owner:** your handle
   - **Space name:** `concord`
   - **License:** MIT
   - **SDK:** Docker
   - **Hardware:** CPU basic (free)
   - **Public:** yes

### 2. Push the code

The Space is a git repo. Clone it, copy your Concord source in, push.

```bash
# Inside your concord checkout:
git remote add hfspace https://huggingface.co/spaces/<your-handle>/concord

# Use the Space-specific README (has the frontmatter HF needs):
cp deploy/huggingface/README.md README-hf.md
git checkout -b hfspace
git mv README.md README-github.md
git mv README-hf.md README.md
git commit -am "huggingface: switch to Space README for deploy"
git push hfspace hfspace:main
```

Or simpler if you don't mind two READMEs in two branches: maintain a
dedicated `hfspace` branch on your fork and push that to the Space remote.
HF Spaces watches `main` of the Space repo, not your GitHub main.

### 3. Set the API key

In the Space settings, `Variables and secrets → New secret`:

```
ANTHROPIC_API_KEY = sk-ant-...
```

### 4. Wait for the build

First build is 3-4 minutes (pip install, model download). Subsequent rebuilds
on push are about a minute since the layers are cached.

The Space is now live at `https://huggingface.co/spaces/<your-handle>/concord`.

---

## Option B. Render (recommended if you want a custom domain)

Free tier works for casual demos (sleeps after 15 min, 30 s cold start).
Starter plan ($7/mo) is always-on with a custom domain.

### 1. Connect the repo

1. https://dashboard.render.com → **New → Blueprint**.
2. Connect your GitHub account, pick the `concord` repo.
3. Render reads `render.yaml` and proposes the service. Click **Apply**.

### 2. Set the API key

Render → your service → **Environment** → add:

```
ANTHROPIC_API_KEY = sk-ant-...
```

(Marked as `sync: false` in the blueprint, so Render won't auto-populate it.)

### 3. Wait for the build

First build is 4-5 minutes (Docker build, install, indexing on startup).
The service is now live at `https://concord-<hash>.onrender.com`.

For a custom domain (`concord.yourdomain.com`):
Render → your service → **Settings → Custom Domains** → add → follow DNS
instructions.

---

## After deploying, sanity-check

```bash
# Should return {"status": "ok"}
curl https://<your-url>/healthz

# Should return the six demo customers
curl https://<your-url>/customers

# Open the demo UI
open https://<your-url>/
```

---

## Cost notes

Concord makes real Anthropic API calls. Every visitor to your demo spends
your API credit. For a portfolio demo this is usually fine (a single
end-to-end request is ~$0.01-0.03 across all the LLM calls). If your demo
goes viral or someone scripts it, consider:

- Adding IP-based rate limiting (e.g., `slowapi` middleware on `/support`).
- Switching all tiers to `model_fast` only in production via env override:
  `CONCORD_MODEL_STANDARD=claude-haiku-4-5`, `CONCORD_MODEL_HIGH=claude-haiku-4-5`.
  Cheaper, slightly worse quality, perfectly fine for showcase.
- Setting a daily Anthropic spend cap in your Anthropic account.

## State notes

Both blueprints use ephemeral storage. That means:

- The Chroma index re-builds on each cold start (5 s, fine).
- The SQLite audit log and trace store reset on restart.

For production you'd attach a persistent disk (Render: "Disks" tab, $1/mo
for 1 GB; HF Spaces: persistent storage, $5/mo for 20 GB) and point
`CONCORD_CHROMA_PATH` and `CONCORD_DB_URL` at it.
