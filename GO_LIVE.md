# LeadFlow — Ready to Deploy
## Everything is pre-configured. 3 steps to go live.

────────────────────────────────────────────────
STEP 1 — Run the Supabase SQL schema
────────────────────────────────────────────────
1. Go to https://supabase.com → sign in
2. Open your project → click "SQL Editor" in left sidebar
3. Click "+ New query"
4. Paste the entire contents of schema.sql
5. Click "Run"
   → You should see "Success. No rows returned"

────────────────────────────────────────────────
STEP 2 — Build the app
────────────────────────────────────────────────
Open Terminal in this folder and run:

  npm install
  npm run build

This creates a dist/ folder. Takes ~1 minute.

────────────────────────────────────────────────
STEP 3 — Deploy to Netlify
────────────────────────────────────────────────
1. Go to https://netlify.com → sign up free
2. Click "Add new site" → "Deploy manually"
3. Drag and drop the dist/ folder onto the page
4. Get your URL → share with your team

────────────────────────────────────────────────
TEAM LOGIN
────────────────────────────────────────────────
URL:      Your Netlify URL (e.g. https://xyz.netlify.app)
Password: LeadFlow#2024!

Each rep enters their own name + the shared password.
Their name gets attached to every call they log.

────────────────────────────────────────────────
AFTER GOING LIVE — Regenerate your Supabase key
────────────────────────────────────────────────
Since this key was shared in a chat:
1. Supabase Dashboard → Project Settings → API
2. Click "Regenerate" next to the anon key
3. Update the key in src/api.js
4. Run npm run build again → re-upload dist/ to Netlify
