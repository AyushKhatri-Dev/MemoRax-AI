# ⭐ MemoRax — WhatsApp AI Memory Assistant

> Your personal AI memory assistant on WhatsApp. Save memories, set reminders, store files, manage your calendar — all through natural conversation.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🧠 **Smart Memory** | Save anything in plain language — notes, ideas, tasks |
| 📅 **Calendar Events** | "Meeting with Rahul tomorrow 3pm" → auto-saved |
| ⏰ **Smart Reminders** | "Remind me to call doctor at 6pm" → WhatsApp alert |
| 📁 **File Vault** | Send images/PDFs → stored securely, retrieve on demand |
| 💬 **Natural Language** | No commands needed — just chat normally |
| 🔁 **Repeat Reminders** | Configurable repeat interval until seen/acknowledged |
| ✅ **Read Receipts** | Reminder auto-dismissed when you read it (blue tick) |
| 🖥️ **Web Dashboard** | View memories, reminders, files, activity timeline |
| 🌙 **Dark / Light Mode** | Dashboard supports both themes |

---

## 🚀 Quick Setup

### Step 1: Clone & Install

```bash
cd memoroe_ai

python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

pip install -r requirements.txt
```

### Step 2: Configure Environment

Create a `.env` file in the project root:

```env
# Django
SECRET_KEY=your-django-secret-key
DEBUG=True

# Twilio (WhatsApp API)
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your-twilio-auth-token
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886

# Groq AI (Free — https://console.groq.com)
GROQ_API_KEY=your-groq-api-key

# Ngrok public URL (for Twilio webhooks + file media URLs)
NGROK_URL=https://your-ngrok-url.ngrok-free.app
```

### Step 3: Initialize Database

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser   # optional, for admin panel
```

### Step 4: Run Server

```bash
python manage.py runserver
```

### Step 5: Expose with Ngrok

```bash
# New terminal:
ngrok http 8000

# Copy the HTTPS URL — e.g. https://abc123.ngrok-free.app
# Paste it into .env as NGROK_URL
```

### Step 6: Connect Twilio Webhook

1. Go to **Twilio Console** → Messaging → Try it out → Send a WhatsApp message
2. Click **Sandbox Settings**
3. Set **"When a message comes in"** → `https://abc123.ngrok-free.app/bot/webhook/`
4. Set **"Status Callback URL"** → `https://abc123.ngrok-free.app/bot/message-status/`
5. Method: **POST** → Save

### Step 7: Test It!

1. Send the sandbox join code to the Twilio WhatsApp number
2. Send **"hello"** → you'll get a welcome message with your dashboard link
3. Try: `"Save — meeting with client on 15 March 3pm"`
4. Try: `"Remind me to take medicine at 9pm"`
5. Try: `"Send me my prescription"` ← after uploading prescription image

---

## 💬 How to Use (Natural Language — No Commands!)

```
Save memory:        "save this — need to buy groceries"
Ask anything:       "what do i have saved about groceries?"
Set reminder:       "remind me to call Rahul at 6pm"
Calendar event:     "meeting with team on 20 march 2pm"
Check calendar:     "do i have any meeting on 20 march?"
Upload file:        [send image or PDF directly]
Retrieve file:      "send my prescription" / "bhejo meri report"
Dismiss reminder:   Reply "GOT IT" or just read the message (blue tick)
```

---

## 🖥️ Dashboard

Access your personal dashboard at:
```
http://localhost:8000/dash/<your-token>/
```

The token is sent in your WhatsApp welcome message.

**Dashboard sections:**
- **Overview** — stats summary (memories, reminders, files, events)
- **Calendar** — all saved calendar events
- **Memories** — browse all saved notes
- **Reminders** — pending & past reminders
- **File Vault** — uploaded images and documents
- **Activity** — interaction timeline with analytics

**Settings** (in dashboard):
- Toggle reminder repeat on/off
- Set repeat interval (minutes) — resends reminder until acknowledged

---

## 🏗️ Project Structure

```
memoroe_ai/
├── manage.py
├── requirements.txt
├── .env
│
├── memoroe_ai/              # Django project config
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
│
├── memoroe_bot/             # WhatsApp bot & webhook
│   ├── views.py             # /bot/webhook/ + /bot/message-status/
│   └── urls.py
│
├── memory_engine/           # AI brain
│   ├── brain.py             # Intent detection, Groq AI, response logic
│   ├── models.py            # BotUser, Memory, CalendarEvent, Reminder, SavedFile
│   ├── scheduler.py         # APScheduler — sends & repeats reminders
│   └── migrations/
│
├── dashboard/               # Web dashboard
│   ├── views.py
│   ├── urls.py
│   └── templates/dashboard/
│       ├── home.html        # Main dashboard (dark/light mode)
│       ├── calendar.html    # Calendar view
│       └── files.html       # File vault view
│
└── media/                   # Uploaded files (images, PDFs)
```

---

## 🔑 API Keys

### Twilio (WhatsApp)
- Console: [console.twilio.com](https://console.twilio.com)
- Account SID + Auth Token → Dashboard page

### Groq AI — FREE ⚡
- Signup: [console.groq.com](https://console.groq.com)
- Model used: **Llama 3.3 70B** (fast, free tier available)
- Free tier: generous — plenty for development and small deployments

---

## 💰 Cost Estimate

| Service | Free Tier | Paid |
|---------|-----------|------|
| Twilio WhatsApp | Sandbox (free testing) | ~$0.005/msg |
| Groq AI (Llama 3.3 70B) | ✅ Free tier | Pay-as-you-go |
| Ngrok | Free (dev) | $8/mo (production) |
| **Estimated for personal use** | **~$0/month** | — |

---

## 🚀 Deployment (Production)

### Railway.app
```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

Set all `.env` variables in Railway dashboard. Replace `NGROK_URL` with your production domain.

---

## 📈 Roadmap

- [x] Natural language memory saving
- [x] Smart calendar event detection
- [x] WhatsApp reminder delivery
- [x] Repeat reminders with configurable interval
- [x] Read receipt → auto-dismiss reminder
- [x] File Vault (images + PDFs)
- [x] Web dashboard with dark/light mode
- [x] Activity timeline & analytics
- [ ] Voice note transcription
- [ ] Multi-language support
- [ ] Razorpay Pro subscription
- [ ] Chrome extension
