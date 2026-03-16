# LeadFlow Railway — Deploy Guide
## Your team picks an industry, hits Search, leads appear. No terminal ever.

---

## Project Structure
```
leadflow-railway/
├── backend/
│   ├── main.py           ← FastAPI server + scraper
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx       ← Full React UI with Lead Finder
│   │   └── main.jsx
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── nixpacks.toml         ← Railway build config
├── railway.toml          ← Railway deploy config
└── .gitignore
```

---

## Step 1 — Push to GitHub (5 min)

Open Terminal in the `leadflow-railway` folder:

```bash
git init
git add .
git commit -m "LeadFlow Railway"
```

Then:
1. Go to **github.com** → click **+** → **New repository**
2. Name it `leadflow-railway` → **Create repository** (keep it private)
3. Copy the commands GitHub shows you under "push an existing repository" and run them

---

## Step 2 — Deploy on Railway (5 min)

1. Go to **railway.app** → Sign up with GitHub
2. Click **New Project** → **Deploy from GitHub repo**
3. Select `leadflow-railway`
4. Railway detects the config and starts building automatically

---

## Step 3 — Set Environment Variables

In Railway dashboard → your service → **Variables** tab, add these:

| Variable | Value |
|----------|-------|
| `TEAM_PASSWORD` | `LeadFlow2024` |
| `SECRET_KEY` | `any-long-random-string-here` |
| `VITE_SUPABASE_URL` | `https://ucpwpjokyconwzwqvdad.supabase.co` |
| `VITE_SUPABASE_KEY` | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...` (your full anon key) |

After adding variables → click **Deploy** again to rebuild.

---

## Step 4 — Get Your URL

Railway gives you a URL like:
`https://leadflow-railway-production-xxxx.up.railway.app`

Share this with your team. Done.

---

## How Your Team Uses It

1. Open the URL → enter name + password `LeadFlow2024`
2. See the **Find Leads** panel at the top
3. Pick an industry from the dropdown (Healthcare, Accounting, IT Services, etc.)
4. Optionally filter by state
5. Drag the slider to choose how many leads (25–300)
6. Click **Find Leads →**
7. Leads appear in the list below, sorted by score — highest quality first
8. Click **📞 Log Call** after every call

That's it. No terminal, no CSV, no manual steps for your reps.

---

## Cost

Railway free tier: $5 credit/month — enough for light use.
Always-on: ~$5-7/month (Hobby plan).

Your Supabase database stays free (same one from before).

---

## Troubleshooting

**Build fails on Railway**
→ Check the build logs in Railway dashboard
→ Make sure all files are committed: `git status` should show nothing

**"Invalid password" on login**
→ Check `TEAM_PASSWORD` env var in Railway matches exactly

**Lead Finder shows no industries**
→ Check Railway logs — usually means the backend isn't starting
→ Verify `SECRET_KEY` and `TEAM_PASSWORD` are set in env vars

**Leads not saving**
→ Check `VITE_SUPABASE_URL` and `VITE_SUPABASE_KEY` are correct in env vars
