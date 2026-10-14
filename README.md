# Team Rice Instagram Bot

Automatic posting and comment replies for **@teamrice.realestate**. It runs free on GitHub Actions, and you don't need a server.

Every 15 minutes, GitHub runs the bot. It:

1. **Publishes** any post in `posts.csv` whose time has arrived (photo, carousel or Reel).
2. **Replies to comments** that match a keyword in `reply_rules.json`. For example, someone comments "price?" and the bot replies publicly, then sends them a DM with details.
3. Once a week, **renews the access token** so the connection doesn't expire.

Nothing goes live until you finish the setup below. The two example posts are marked `draft`, and comment replies start out turned off.

---

## What's in this repo

| File | What it's for |
|---|---|
| `posts.csv` | Your content calendar. One row per post. |
| `images/` | Put your photos and videos here. |
| `reply_rules.json` | Keywords to watch for, and what to reply. |
| `config.json` | Time zone and other settings. |
| `bot/instagram_bot.py` | The bot itself. You don't need to edit it. |
| `.github/workflows/instagram.yml` | The schedule that runs the bot. |
| `state/` | The bot's memory, so it never posts or replies twice. |

---

## One-time setup (about 30 minutes)

### Step 1: Make sure the Instagram account is Professional
In the Instagram app, go to **Settings → Account type and tools → Switch to professional account**, and choose **Business**. Skip this if the account is already Business or Creator.

### Step 2: Create a Meta developer app
1. Go to **developers.facebook.com** and log in with Facebook.
2. Click **My Apps → Create App**.
3. When it asks for a use case, pick **"Manage messaging & content on Instagram."** Give the app a name like *Team Rice Bot*.
4. In the app dashboard, open **Instagram → API setup with Instagram business login**.

### Step 3: Get the access token
1. In that same API setup page, under **Generate access tokens**, click **Add account** and log in as @teamrice.realestate.
2. Approve the permissions it asks for (basic, content publish, manage comments, manage messages).
3. Click **Generate token** and copy the long string it shows you. It's good for about 60 days, and the bot renews it automatically.

> Treat this token like a password. Never paste it into a file in this repo. It only goes in the secret in Step 4.

### Step 4: Give the token to GitHub (as a secret)
1. In this repo on GitHub, click **Settings → Secrets and variables → Actions**.
2. Click **New repository secret**.
3. Name: `IG_ACCESS_TOKEN`. Secret: paste the token. Click **Add secret**.

### Step 5: Test the connection
1. Click the **Actions** tab. If GitHub asks, click the button to enable workflows.
2. Click **Instagram bot → Run workflow**. Leave task on **check** and click the green **Run workflow** button.
3. After about 30 seconds, click the run. You should see `Connected to Instagram as @teamrice.realestate`.

If you get an error instead, the message will usually say what's wrong. Most often the token was copied incompletely, or a permission wasn't approved in Step 3.

### Step 6 (recommended): Let the bot save renewed tokens
Sometimes Instagram hands back a brand-new token when the bot renews it. GitHub won't let the bot update its own secrets without a special key, so:

1. Click your profile picture (top right) → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Repository access: **Only select repositories →** this repo.
3. Permissions → Repository permissions → **Secrets: Read and write**.
4. Generate it, copy it, and add it as another repo secret named `GH_PAT` (same steps as Step 4).

If you skip this, the Monday token job will fail with a clear message on any week Instagram issues a new token. When that happens, repeat Step 3 and paste the new token into `IG_ACCESS_TOKEN`.

---

## Scheduling posts

1. **Upload your photos:** open the `images` folder → **Add file → Upload files**. Use **.jpg** for photos (Instagram's API rejects PNG) and **.mp4** for Reels.
2. **Edit `posts.csv`:** click it, then click the pencil icon. Or edit it in Excel and save as **CSV UTF-8**.

| Column | What to put |
|---|---|
| `id` | Any unique short name, like `lakehouse-oct20` |
| `publish_at` | `YYYY-MM-DD HH:MM` in **Mountain Time** (Columbia Valley time), like `2026-10-20 09:30` |
| `media` | One file name for a single photo/Reel, or several separated by `\|` for a carousel (max 10). Full `https://` links also work. |
| `caption` | Your caption. Type `\n` where you want a line break. Wrap it in quotes if it contains commas. |
| `status` | Leave **blank** to schedule it. Put `draft` to hold it back. |

Posts go out within about 15 minutes of their time; GitHub's timer sometimes runs a little late. If a post is more than 12 hours overdue, for example because the bot was paused, it's skipped rather than posted late. You can change that in `config.json`.

**To re-post something:** give it a new `id`.

---

## Auto-replying to comments

1. Open `reply_rules.json` and edit the rules. Each rule has:
   - `keywords`: words that trigger it (not case-sensitive; "price" won't match "priceless")
   - `public_reply`: reply posted under the comment. `{username}` becomes their handle.
   - `private_reply` (optional): a DM sent to the commenter
2. Change `"enabled": false` to `"enabled": true` and commit.

The bot is built not to come across as spammy:
- It only answers comments that arrive **after** you turn it on, never old ones.
- It replies to the same person **at most once per post**.
- It never replies to your own comments.
- It sends at most 20 replies per run.

**DMs people send you directly** (not comment replies) aren't handled by this bot, because instant DM replies need an always-on server. The free option is Instagram's own **Automated responses / Instant replies** in Meta Business Suite → Inbox → Automations. That works fine alongside this bot.

---

## Pause, test, and troubleshoot

- **Practice run:** Actions → Instagram bot → Run workflow, pick a task, and leave **Dry run** checked. It shows what would happen without posting.
- **Pause everything (dry-run mode):** Settings → Secrets and variables → Actions → **Variables** tab → add `DRY_RUN` = `true`. Delete it to resume.
- **Stop completely:** Actions → Instagram bot → **⋯ → Disable workflow**.
- **See what happened:** every run is logged in the Actions tab. Lines starting with `+` succeeded, and `!` means something failed. Failed posts are retried up to 3 times.
- **Heads-up:** this repo is public, so anything in `images/` and `posts.csv` is visible to anyone with the link before it's posted. The token itself is safe in Secrets.
- GitHub turns off scheduled workflows in repos that have had no activity for 60 days. The bot's own memory updates usually count as activity, but if the schedule stops, re-enable it from the Actions tab.
