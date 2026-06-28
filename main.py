from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client
import resend
import os
from datetime import datetime, date, timedelta
from typing import Optional
import asyncio

# ── App setup ──
app = FastAPI(title="Coach Hala Booking API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace with your Vercel URL in production e.g. ["https://yoursite.vercel.app"]
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Clients ──
supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"]
)
resend.api_key = os.environ["RESEND_API_KEY"]

# ── Config ──
COACH_EMAIL    = "shahdaboelfotouh7@gmail.com"
FROM_EMAIL     = "bookings@coachhala.com"   # Must be verified on resend.com
SITE_URL       = os.environ.get("SITE_URL", "https://yoursite.vercel.app")
BACKEND_URL    = os.environ.get("BACKEND_URL", "https://yourbackend.railway.app")
ALL_TIMES      = [
    "9:00 AM","10:00 AM","11:00 AM","12:00 PM",
    "1:00 PM","2:00 PM","3:00 PM","4:00 PM","5:00 PM","6:00 PM"
]


# ── Models ──
class BookingRequest(BaseModel):
    client_name:    str
    client_email:   str
    client_phone:   str
    service:        str
    format:         str
    specialty:      str
    date:           str   # "Monday, Jan 1, 2025"
    date_key:       str   # "2025-01-01"
    time:           str   # "10:00 AM"
    amount:         int
    deposit:        bool
    remaining:      int   = 0
    pay_method:     str   = "InstaPay"
    instapay_ref:   str   = ""
    notes:          str   = ""
    emergency:      str   = ""


# ══════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════

@app.get("/")
def root():
    return {"status": "Coach Hala API is running ✅"}


# ── 1. Get available + taken slots for a date ──
@app.get("/slots/{date_key}")
def get_slots(date_key: str):
    """Returns available and taken time slots for a given date (YYYY-MM-DD)."""
    taken_rows = supabase.table("blocked_slots") \
        .select("time") \
        .eq("date", date_key) \
        .execute()

    taken = [row["time"] for row in taken_rows.data]
    available = [t for t in ALL_TIMES if t not in taken]

    return {"date": date_key, "available": available, "taken": taken}


# ── 2. Submit a new booking ──
@app.post("/book")
def create_booking(b: BookingRequest):
    """Client submits a booking request."""

    # Check slot isn't already taken
    existing = supabase.table("blocked_slots") \
        .select("id") \
        .eq("date", b.date_key) \
        .eq("time", b.time) \
        .execute()

    if existing.data:
        raise HTTPException(status_code=409, detail="This slot was just taken. Please choose another time.")

    # Save booking to database
    booking_data = {
        "client_name":    b.client_name,
        "client_email":   b.client_email,
        "client_phone":   b.client_phone,
        "service":        b.service,
        "format":         b.format,
        "specialty":      b.specialty,
        "date":           b.date_key,
        "date_display":   b.date,
        "time":           b.time,
        "amount":         b.amount,
        "deposit":        b.deposit,
        "remaining":      b.remaining,
        "pay_method":     b.pay_method,
        "instapay_ref":   b.instapay_ref,
        "notes":          b.notes,
        "emergency":      b.emergency,
        "status":         "pending",
        "created_at":     datetime.utcnow().isoformat()
    }

    result = supabase.table("bookings").insert(booking_data).execute()
    booking_id = result.data[0]["id"]

    # Block the slot immediately so nobody else can take it
    supabase.table("blocked_slots").insert({
        "date":   b.date_key,
        "time":   b.time,
        "reason": "booked",
        "booking_id": booking_id
    }).execute()

 # Send email to coach
    _send_coach_notification(b, booking_id)

    # Send "pending" email to client
    _send_client_pending(b)

    return {"success": True, "booking_id": booking_id, "message": "Booking request received!"}


# ── 3. Coach confirms booking (clicked from email) ──
@app.get("/confirm/{booking_id}")
def confirm_booking(booking_id: str, zoom_link: Optional[str] = None):
    """Hala clicks Confirm in her email — client gets confirmation email."""

    result = supabase.table("bookings") \
        .update({"status": "confirmed", "zoom_link": zoom_link or ""}) \
        .eq("id", booking_id) \
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Booking not found")

    b = result.data[0]
    _send_client_confirmation(b, zoom_link)

    return _html_response("✅ Booking Confirmed!",
        f"You confirmed <b>{b['client_name']}</b>'s session on {b['date_display']} at {b['time']}.<br>"
        f"A confirmation email has been sent to {b['client_email']}.")


# ── 4. Coach declines booking (clicked from email) ──
@app.get("/decline/{booking_id}")
def decline_booking(booking_id: str):
    """Hala clicks Decline — slot is freed, client is notified."""

    result = supabase.table("bookings") \
        .update({"status": "declined"}) \
        .eq("id", booking_id) \
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Booking not found")

    b = result.data[0]

    # Free the slot
    supabase.table("blocked_slots") \
        .delete() \
        .eq("date", b["date"]) \
        .eq("time", b["time"]) \
        .execute()

    # Notify client
    _send_client_declined(b)

    return _html_response("❌ Booking Declined",
        f"You declined <b>{b['client_name']}</b>'s session.<br>"
        f"The slot has been freed and the client has been notified.")


# ── 5. Get all bookings (coach dashboard data) ──
@app.get("/bookings")
def get_bookings(status: Optional[str] = None, secret: str = ""):
    """Returns all bookings. Protected by a secret key."""
    if secret != os.environ.get("ADMIN_SECRET", "hala-admin-2025"):
        raise HTTPException(status_code=401, detail="Unauthorized")

    query = supabase.table("bookings").select("*").order("created_at", desc=True)
    if status:
        query = query.eq("status", status)

    result = query.execute()
    return {"bookings": result.data, "total": len(result.data)}


# ── 6. Coach blocks a time slot manually ──
@app.post("/block")
def block_slot(date_key: str, time: str, secret: str = ""):
    """Coach manually blocks a slot (e.g. personal appointment)."""
    if secret != os.environ.get("ADMIN_SECRET", "hala-admin-2025"):
        raise HTTPException(status_code=401, detail="Unauthorized")

    supabase.table("blocked_slots").insert({
        "date": date_key, "time": time, "reason": "coach_blocked"
    }).execute()

    return {"success": True, "message": f"Slot {date_key} {time} blocked."}


# ── 7. Send deposit reminder ──
@app.get("/remind/{booking_id}")
def send_reminder(booking_id: str, secret: str = ""):
    """Manually trigger a deposit reminder email to a client."""
    if secret != os.environ.get("ADMIN_SECRET", "hala-admin-2025"):
        raise HTTPException(status_code=401, detail="Unauthorized")

    result = supabase.table("bookings").select("*").eq("id", booking_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Booking not found")

    b = result.data[0]
    if b["remaining"] <= 0:
        return {"message": "No remaining balance — no reminder needed."}

    _send_deposit_reminder(b)
    return {"success": True, "message": f"Reminder sent to {b['client_email']}"}


# ══════════════════════════════════════════
#  EMAIL HELPERS
# ══════════════════════════════════════════

def _send_coach_notification(b: BookingRequest, booking_id: str):
    """Email Hala with confirm/decline buttons."""
    confirm_url = f"{BACKEND_URL}/confirm/{booking_id}"
    decline_url = f"{BACKEND_URL}/decline/{booking_id}"
    payment_badge = "💳 Card" if b.pay_method == "Card" else "📱 InstaPay"
    deposit_label = "50% Deposit" if b.deposit else "Full Payment"

    resend.Emails.send({
        "from":    FROM_EMAIL,
        "to":      COACH_EMAIL,
        "subject": f"🗓 NEW BOOKING: {b.service} — {b.client_name}",
        "html": f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
          <div style="background:linear-gradient(135deg,#7B5EA7,#E8613A);padding:30px;border-radius:12px 12px 0 0;text-align:center">
            <h1 style="color:white;margin:0;font-size:24px">New Booking Request</h1>
            <p style="color:rgba(255,255,255,0.85);margin:8px 0 0">Action required — please confirm or decline</p>
          </div>

          <div style="background:#FFF8F5;padding:30px;border-radius:0 0 12px 12px;border:1px solid #EDD8CC">

            <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
              <tr><td style="padding:8px 0;color:#6B6070;width:140px">Client</td>
                  <td style="padding:8px 0;font-weight:600">{b.client_name}</td></tr>
              <tr><td style="padding:8px 0;color:#6B6070">Email</td>
                  <td style="padding:8px 0">{b.client_email}</td></tr>
              <tr><td style="padding:8px 0;color:#6B6070">Phone</td>
                  <td style="padding:8px 0">{b.client_phone}</td></tr>
              <tr><td colspan="2"><hr style="border:none;border-top:1px solid #EDD8CC;margin:8px 0"></td></tr>
              <tr><td style="padding:8px 0;color:#6B6070">Service</td>
                  <td style="padding:8px 0;font-weight:600">{b.service}</td></tr>
              <tr><td style="padding:8px 0;color:#6B6070">Format</td>
                  <td style="padding:8px 0">{b.format}</td></tr>
              <tr><td style="padding:8px 0;color:#6B6070">Specialty</td>
                  <td style="padding:8px 0">{b.specialty}</td></tr>
              <tr><td style="padding:8px 0;color:#6B6070">Date & Time</td>
                  <td style="padding:8px 0;font-weight:600;color:#E8613A">{b.date} at {b.time}</td></tr>
              <tr><td colspan="2"><hr style="border:none;border-top:1px solid #EDD8CC;margin:8px 0"></td></tr>
              <tr><td style="padding:8px 0;color:#6B6070">Payment</td>
                  <td style="padding:8px 0">{payment_badge} — {deposit_label}</td></tr>
              <tr><td style="padding:8px 0;color:#6B6070">Amount</td>
                  <td style="padding:8px 0;font-weight:600">EGP {b.amount:,}</td></tr>
              {"<tr><td style='padding:8px 0;color:#6B6070'>Remaining</td><td style='padding:8px 0;color:#7B5EA7;font-weight:600'>EGP " + str(b.remaining) + " (due 24h before)</td></tr>" if b.remaining > 0 else ""}
              {"<tr><td style='padding:8px 0;color:#6B6070'>InstaPay Ref</td><td style='padding:8px 0'>" + b.instapay_ref + "</td></tr>" if b.instapay_ref else ""}
              {"<tr><td style='padding:8px 0;color:#6B6070'>Client Notes</td><td style='padding:8px 0;font-style:italic'>" + b.notes + "</td></tr>" if b.notes else ""}
              {"<tr><td style='padding:8px 0;color:#6B6070'>Emergency</td><td style='padding:8px 0'>" + b.emergency + "</td></tr>" if b.emergency else ""}
            </table>

            <div style="text-align:center;margin-top:8px">
              <a href="{confirm_url}" style="display:inline-block;background:#4CB08A;color:white;padding:14px 32px;border-radius:100px;text-decoration:none;font-weight:700;font-size:16px;margin-right:12px">
                ✅ Confirm Booking
              </a>
              <a href="{decline_url}" style="display:inline-block;background:#C94E2A;color:white;padding:14px 32px;border-radius:100px;text-decoration:none;font-weight:700;font-size:16px">
                ❌ Decline
              </a>
            </div>

            <p style="color:#6B6070;font-size:12px;text-align:center;margin-top:20px">
              Clicking Confirm will automatically send {b.client_name} a confirmation email.
            </p>
          </div>
        </div>
        """
    })


def _send_client_pending(b: BookingRequest):
    """Tell the client their request was received and is pending."""
    resend.Emails.send({
        "from":    FROM_EMAIL,
        "to":      b.client_email,
        "subject": "⏳ Booking Request Received — Coach Hala",
        "html": f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
          <div style="background:linear-gradient(135deg,#7B5EA7,#E8613A);padding:30px;border-radius:12px 12px 0 0;text-align:center">
            <h1 style="color:white;margin:0">Request Received ⏳</h1>
          </div>
          <div style="background:#FFF8F5;padding:30px;border-radius:0 0 12px 12px;border:1px solid #EDD8CC">
            <p>Hi <b>{b.client_name}</b>,</p>
            <p>Your booking request has been sent to Coach Hala. She will review it and confirm within <b>24 hours</b>.</p>
            <div style="background:#FFF0E8;border-radius:10px;padding:16px;margin:20px 0">
              <p style="margin:4px 0"><b>Service:</b> {b.service}</p>
              <p style="margin:4px 0"><b>Date & Time:</b> {b.date} at {b.time}</p>
              <p style="margin:4px 0"><b>Format:</b> {b.format}</p>
              <p style="margin:4px 0"><b>Amount:</b> EGP {b.amount:,}</p>
            </div>
            <p style="background:#FFF3CD;padding:12px;border-radius:8px;border-left:4px solid #F5C842">
              ⚠️ <b>Your session is not confirmed yet.</b> You will receive another email once Coach Hala confirms.
            </p>
            <p>Questions? Reply to this email or WhatsApp: <b>01013996744</b></p>
            <p>Warm regards,<br><b>Coach Hala El Shahawy</b></p>
          </div>
        </div>
        """
    })


def _send_client_confirmation(b: dict, zoom_link: Optional[str]):
    """Send the client their confirmed session details."""
    format_detail = ""
    if b["format"] == "Zoom Call":
        if zoom_link:
            format_detail = f'<p><b>Zoom Link:</b> <a href="{zoom_link}">{zoom_link}</a></p>'
        else:
            format_detail = "<p><b>Format:</b> Zoom Call — link will be sent via WhatsApp before your session.</p>"
    else:
        format_detail = "<p><b>Format:</b> In-Person — location details will be sent via WhatsApp.</p>"

    remaining_note = ""
    if b.get("remaining", 0) > 0:
        remaining_note = f"""
        <p style="background:#FFF3CD;padding:12px;border-radius:8px;border-left:4px solid #F5C842">
          💳 <b>Remaining Balance:</b> EGP {b['remaining']:,} is due 24 hours before your session.
          You will receive a reminder email.
        </p>"""

    resend.Emails.send({
        "from":    FROM_EMAIL,
        "to":      b["client_email"],
        "subject": "✅ Your Session is Confirmed — Coach Hala",
        "html": f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
          <div style="background:linear-gradient(135deg,#4CB08A,#7B5EA7);padding:30px;border-radius:12px 12px 0 0;text-align:center">
            <h1 style="color:white;margin:0">Session Confirmed! ✅</h1>
          </div>
          <div style="background:#FFF8F5;padding:30px;border-radius:0 0 12px 12px;border:1px solid #EDD8CC">
            <p>Hi <b>{b['client_name']}</b>,</p>
            <p>Coach Hala has confirmed your session. See you soon!</p>
            <div style="background:#FFF0E8;border-radius:10px;padding:16px;margin:20px 0">
              <p style="margin:4px 0"><b>Service:</b> {b['service']}</p>
              <p style="margin:4px 0"><b>Specialty:</b> {b['specialty']}</p>
              <p style="margin:4px 0;color:#E8613A;font-size:18px"><b>📅 {b['date_display']} at {b['time']}</b></p>
              {format_detail}
            </div>
            {remaining_note}
            <div style="background:#FFF0E8;border-radius:10px;padding:14px;margin:16px 0;font-size:13px">
              <b>Cancellation Policy Reminder:</b><br>
              • More than 24hrs before: full refund or reschedule<br>
              • 2–24hrs before: reschedule only<br>
              • Within 30min or no-show: 50% fee retained
            </div>
            <p>Questions? WhatsApp: <b>01013996744</b></p>
            <p>Looking forward to our session!<br><b>Coach Hala El Shahawy</b></p>
          </div>
        </div>
        """
    })


def _send_client_declined(b: dict):
    """Tell the client their booking was declined."""
    resend.Emails.send({
        "from":    FROM_EMAIL,
        "to":      b["client_email"],
        "subject": "Re: Your Booking Request — Coach Hala",
        "html": f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
          <div style="background:linear-gradient(135deg,#7B5EA7,#E8613A);padding:30px;border-radius:12px 12px 0 0;text-align:center">
            <h1 style="color:white;margin:0">Booking Update</h1>
          </div>
          <div style="background:#FFF8F5;padding:30px;border-radius:0 0 12px 12px;border:1px solid #EDD8CC">
            <p>Hi <b>{b['client_name']}</b>,</p>
            <p>Unfortunately, Coach Hala is unable to confirm the slot you selected
            (<b>{b['date_display']} at {b['time']}</b>).</p>
            <p>Please visit the website to choose another available time — we'd love to find a slot that works!</p>
            <div style="text-align:center;margin:24px 0">
              <a href="{SITE_URL}" style="background:#E8613A;color:white;padding:14px 28px;border-radius:100px;text-decoration:none;font-weight:700">
                Choose Another Time →
              </a>
            </div>
            <p>Apologies for any inconvenience. Questions? WhatsApp: <b>01013996744</b></p>
            <p>Warm regards,<br><b>Coach Hala El Shahawy</b></p>
          </div>
        </div>
        """
    })


def _send_deposit_reminder(b: dict):
    """Remind client to pay remaining balance."""
    resend.Emails.send({
        "from":    FROM_EMAIL,
        "to":      b["client_email"],
        "subject": "⚠️ Payment Reminder — Balance Due — Coach Hala",
        "html": f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
          <div style="background:linear-gradient(135deg,#F5C842,#E8613A);padding:30px;border-radius:12px 12px 0 0;text-align:center">
            <h1 style="color:white;margin:0">Payment Reminder ⚠️</h1>
          </div>
          <div style="background:#FFF8F5;padding:30px;border-radius:0 0 12px 12px;border:1px solid #EDD8CC">
            <p>Hi <b>{b['client_name']}</b>,</p>
            <p>This is a reminder that your remaining session balance is due <b>24 hours before your session.</b></p>
            <div style="background:#FFF0E8;border-radius:10px;padding:16px;margin:20px 0">
              <p style="margin:4px 0"><b>Session:</b> {b['date_display']} at {b['time']}</p>
              <p style="margin:4px 0;font-size:20px;color:#E8613A"><b>Balance Due: EGP {b['remaining']:,}</b></p>
            </div>
            <p>Please send payment via InstaPay to: <b>01013996744</b><br>
            Then WhatsApp us the confirmation: <b>01013996744</b></p>
            <p>Warm regards,<br><b>Coach Hala El Shahawy</b></p>
          </div>
        </div>
        """
    })


def _html_response(title: str, body: str) -> dict:
    """Simple HTML page returned when coach clicks confirm/decline."""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=f"""
    <html><body style="font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#FFF8F5">
      <div style="text-align:center;max-width:480px;padding:40px;background:white;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,0.1)">
        <h2 style="color:#2C2424">{title}</h2>
        <p style="color:#6B6070;line-height:1.6">{body}</p>
        <p style="margin-top:24px;font-size:13px;color:#9CA3AF">Coach Hala El Shahawy Booking System</p>
      </div>
    </body></html>
    """)
