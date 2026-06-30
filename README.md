# Coach Hala El Shahawy — Booking Backend

FastAPI backend with Supabase database and Resend email service.

---

## What this does

- Stores all bookings permanently in a real database (Supabase)
- Blocks slots in real time so no double booking
- Emails Coach Hala with ✅ Confirm / ❌ Decline buttons
- Sends clients a pending email immediately
- Sends clients a beautiful confirmation email when Hala confirms
- Sends clients a declined email with link to rebook
- Supports deposit reminders
 
---

## Setup — Step by Step

### Step 1: Supabase (database)

1. Go to **supabase.com** → Sign up free with Google
2. Click **New Project** → name it `coach-hala` → set a password → create
3. Wait ~2 minutes for it to set up
4. Go to **SQL Editor** (left sidebar)
5. Paste the entire contents of `schema.sql` → click **Run**
6. Go to **Settings → API** → copy:
   - `Project URL` → paste into `.env` as `SUPABASE_URL`
   - `anon public` key → paste into `.env` as `SUPABASE_KEY`

### Step 2: Resend (emails)

1. Go to **resend.com** → Sign up free
2. Click **Add Domain** → enter `coachhala.com` (or whatever domain you have)
   - If you don't have a domain yet, use their free `@resend.dev` address for testing
3. Go to **API Keys** → create a new key → copy it → paste into `.env` as `RESEND_API_KEY`
4. In `main.py` line 19, update `FROM_EMAIL` if needed

### Step 3: Deploy to Railway

1. Go to **github.com** → create a new repo called `coach-hala-backend`
2. Upload all these files to it (drag and drop in the GitHub web interface)
3. Go to **railway.app** → sign up with GitHub → New Project → Deploy from GitHub repo
4. Select your `coach-hala-backend` repo
5. Once deployed, go to **Variables** tab → add all your `.env` values:
   - `SUPABASE_URL`
   - `SUPABASE_KEY`
   - `RESEND_API_KEY`
   - `SITE_URL` (your Vercel frontend URL)
   - `BACKEND_URL` (your Railway URL — find it in Railway under Domains)
   - `ADMIN_SECRET` (make up a secret password)
6. Go to **Settings → Domains** → copy your Railway URL (e.g. `coach-hala-backend.railway.app`)
7. Paste that URL back into Railway Variables as `BACKEND_URL`

### Step 4: Update your frontend

In `coach-hala-website.html`, find this line:
```
const FORMSPREE_BOOKING_ID = 'mjgqrwla';
```

Replace the entire `completePayment` fetch block with:
```javascript
const res = await fetch('https://YOUR-RAILWAY-URL.railway.app/book', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    client_name:  name,
    client_email: email,
    client_phone: phone,
    service:      booking.serviceName,
    format:       booking.formatName,
    specialty:    booking.specialtyName,
    date:         booking.date,
    date_key:     booking.dateKey,
    time:         booking.time,
    amount:       booking.priceRaw,
    deposit:      booking.deposit === 'half',
    remaining:    booking.deposit === 'half' ? Math.ceil(booking.priceRaw / 2) : 0,
    pay_method:   payMethod,
    instapay_ref: ipaRef,
    notes:        notes,
    emergency:    emergency
  })
});
```

Also replace the calendar slot loading with real data:
```javascript
async function selectDate(date, el) {
  // ... existing code to set booking.date and booking.dateKey ...
  
  // Load real slots from backend
  const res = await fetch(`https://YOUR-RAILWAY-URL.railway.app/slots/${booking.dateKey}`);
  const data = await res.json();
  renderTimeSlots(data.available, data.taken);
}
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Health check |
| GET | `/slots/{date}` | Get available slots for a date |
| POST | `/book` | Submit a booking request |
| GET | `/confirm/{id}` | Coach confirms booking (from email link) |
| GET | `/decline/{id}` | Coach declines booking (from email link) |
| GET | `/bookings?secret=xxx` | Get all bookings (admin) |
| POST | `/block?secret=xxx` | Block a slot manually (admin) |
| GET | `/remind/{id}?secret=xxx` | Send deposit reminder |

---

## Testing locally

```bash
# Install dependencies
pip install -r requirements.txt

# Copy env file and fill in your values
cp .env.example .env

# Run the server
uvicorn main:app --reload

# Test in browser
open http://localhost:8000
open http://localhost:8000/docs   # Interactive API docs
```

---

## File Structure

```
coach-hala-backend/
├── main.py           # All backend logic
├── requirements.txt  # Python dependencies
├── Procfile          # Railway deployment config
├── schema.sql        # Supabase database setup
├── .env.example      # Environment variables template
└── README.md         # This file
```
