from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse, Response
from pydantic import BaseModel
from supabase import create_client, Client
import resend
import os
from datetime import datetime
from typing import Optional

# ── App setup ──
app = FastAPI(title="Coach Hala Booking API", version="1.0.0")

# ── CORS — manual middleware so it cannot be blocked ──
@app.middleware("http")
async def cors_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        response = Response(status_code=200)
    else:
        try:
            response = await call_next(request)
        except Exception as e:
            response = JSONResponse({"error": str(e)}, status_code=500)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Expose-Headers"] = "*"
    return response

# ── Clients ──
supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"]
)
resend.api_key = os.environ["RESEND_API_KEY"]

# ── Config ──
COACH_EMAIL = "shahdaboelfotouh7@gmail.com"
FROM_EMAIL  = "onboarding@resend.dev"  # Free Resend test address — works without domain
SITE_URL    = os.environ.get("SITE_URL", "https://halaelshahawy.vercel.app")
BACKEND_URL = os.environ.get("BACKEND_URL", "https://web-production-43aee.up.railway.app")
ALL_TIMES   = [
    "9:00 AM","10:00 AM","11:00 AM","12:00 PM",
    "1:00 PM","2:00 PM","3:00 PM","4:00 PM","5:00 PM","6:00 PM"
]

# ── Models ──
class BookingRequest(BaseModel):
    client_name:  str
    client_email: str
    client_phone: str
    service:      str
    format:       str
    specialty:    str
    date:         str
    date_key:     str
    time:         str
    amount:       int
    deposit:      bool
    remaining:    int  = 0
    pay_method:   str  = "InstaPay"
    instapay_ref: str  = ""
    notes:        str  = ""
    emergency:    str  = ""

# ══════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════

@app.get("/")
def root():
    return {"status": "Coach Hala API is running ✅"}


@app.get("/slots/{date_key}")
def get_slots(date_key: str):
    taken_rows = supabase.table("blocked_slots") \
        .select("time").eq("date", date_key).execute()
    taken = [row["time"] for row in taken_rows.data]
    available = [t for t in ALL_TIMES if t not in taken]
    return {"date": date_key, "available": available, "taken": taken}


@app.post("/book")
def create_booking(b: BookingRequest):
    # Check slot isn't taken
    existing = supabase.table("blocked_slots") \
        .select("id").eq("date", b.date_key).eq("time", b.time).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="This slot was just taken. Please choose another time.")

    # Save booking
    booking_data = {
        "client_name":  b.client_name,
        "client_email": b.client_email,
        "client_phone": b.client_phone,
        "service":      b.service,
        "format":       b.format,
        "specialty":    b.specialty,
        "date":         b.date_key,
        "date_display": b.date,
        "time":         b.time,
        "amount":       b.amount,
        "deposit":      b.deposit,
        "remaining":    b.remaining,
        "pay_method":   b.pay_method,
        "instapay_ref": b.instapay_ref,
        "notes":        b.notes,
        "emergency":    b.emergency,
        "status":       "pending",
        "created_at":   datetime.utcnow().isoformat()
    }
    result = supabase.table("bookings").insert(booking_data).execute()
    booking_id = result.data[0]["id"]

    # Block the slot
    supabase.table("blocked_slots").insert({
        "date": b.date_key, "time": b.time,
        "reason": "booked", "booking_id": booking_id
    }).execute()

    # Send emails (non-blocking)
    try:
        _send_coach_notification(b, booking_id)
    except Exception as e:
        print(f"Coach email failed: {e}")
    try:
        _send_client_pending(b)
    except Exception as e:
        print(f"Client email failed: {e}")

    return {"success": True, "booking_id": booking_id, "message": "Booking request received!"}


@app.get("/confirm/{booking_id}")
def confirm_booking(booking_id: str, zoom_link: Optional[str] = None):
    result = supabase.table("bookings") \
        .update({"status": "confirmed", "zoom_link": zoom_link or ""}) \
        .eq("id", booking_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Booking not found")
    b = result.data[0]
    try:
        _send_client_confirmation(b, zoom_link)
    except Exception as e:
        print(f"Confirmation email failed: {e}")
    return HTMLResponse(content=f"""
    <html><body style="font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#FFF8F5">
      <div style="text-align:center;max-width:480px;padding:40px;background:white;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,0.1)">
        <h2 style="color:#4CB08A">✅ Booking Confirmed!</h2>
        <p style="color:#6B6070">You confirmed <b>{b['client_name']}</b>'s session on {b['date_display']} at {b['time']}.<br>A confirmation email has been sent to {b['client_email']}.</p>
      </div>
    </body></html>
    """)


@app.get("/decline/{booking_id}")
def decline_booking(booking_id: str):
    result = supabase.table("bookings") \
        .update({"status": "declined"}).eq("id", booking_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Booking not found")
    b = result.data[0]
    supabase.table("blocked_slots") \
        .delete().eq("date", b["date"]).eq("time", b["time"]).execute()
    try:
        _send_client_declined(b)
    except Exception as e:
        print(f"Decline email failed: {e}")
    return HTMLResponse(content=f"""
    <html><body style="font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#FFF8F5">
      <div style="text-align:center;max-width:480px;padding:40px;background:white;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,0.1)">
        <h2 style="color:#C94E2A">❌ Booking Declined</h2>
        <p style="color:#6B6070">You declined <b>{b['client_name']}</b>'s session. The slot has been freed and the client notified.</p>
      </div>
    </body></html>
    """)


@app.get("/bookings")
def get_bookings(status: Optional[str] = None, secret: str = ""):
    if secret != os.environ.get("ADMIN_SECRET", "hala2025secret"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    query = supabase.table("bookings").select("*").order("created_at", desc=True)
    if status:
        query = query.eq("status", status)
    result = query.execute()
    return {"bookings": result.data, "total": len(result.data)}


# ══════════════════════════════════════════
#  EMAIL HELPERS
# ══════════════════════════════════════════

def _send_coach_notification(b: BookingRequest, booking_id: str):
    confirm_url = f"{BACKEND_URL}/confirm/{booking_id}"
    decline_url = f"{BACKEND_URL}/decline/{booking_id}"
    resend.Emails.send({
        "from":    FROM_EMAIL,
        "to":      COACH_EMAIL,
        "subject": f"🗓 NEW BOOKING: {b.service} — {b.client_name}",
        "html": f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
          <div style="background:linear-gradient(135deg,#7B5EA7,#E8613A);padding:30px;border-radius:12px 12px 0 0;text-align:center">
            <h1 style="color:white;margin:0">New Booking Request</h1>
          </div>
          <div style="background:#FFF8F5;padding:30px;border-radius:0 0 12px 12px;border:1px solid #EDD8CC">
            <p><b>Client:</b> {b.client_name}</p>
            <p><b>Email:</b> {b.client_email}</p>
            <p><b>Phone:</b> {b.client_phone}</p>
            <p><b>Service:</b> {b.service}</p>
            <p><b>Format:</b> {b.format}</p>
            <p><b>Specialty:</b> {b.specialty}</p>
            <p><b>Date & Time:</b> {b.date} at {b.time}</p>
            <p><b>Payment:</b> {b.pay_method} — {'50% Deposit' if b.deposit else 'Full'} — EGP {b.amount:,}</p>
            <p><b>InstaPay Ref:</b> {b.instapay_ref or 'N/A'}</p>
            <p><b>Notes:</b> {b.notes or 'None'}</p>
            <div style="text-align:center;margin-top:24px">
              <a href="{confirm_url}" style="background:#4CB08A;color:white;padding:14px 32px;border-radius:100px;text-decoration:none;font-weight:700;margin-right:12px">✅ Confirm</a>
              <a href="{decline_url}" style="background:#C94E2A;color:white;padding:14px 32px;border-radius:100px;text-decoration:none;font-weight:700">❌ Decline</a>
            </div>
          </div>
        </div>"""
    })


def _send_client_pending(b: BookingRequest):
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
            <p>Your booking request has been sent to Coach Hala. She will confirm within <b>24 hours</b>.</p>
            <p><b>Service:</b> {b.service}</p>
            <p><b>Date & Time:</b> {b.date} at {b.time}</p>
            <p><b>Format:</b> {b.format}</p>
            <p style="background:#FFF3CD;padding:12px;border-radius:8px;border-left:4px solid #F5C842">
              ⚠️ <b>Your session is not confirmed yet.</b> You will receive another email once Coach Hala confirms.
            </p>
            <p>Questions? WhatsApp: <b>01013996744</b></p>
            <p>Warm regards,<br><b>Coach Hala El Shahawy</b></p>
          </div>
        </div>"""
    })


def _send_client_confirmation(b: dict, zoom_link: Optional[str]):
    format_detail = f'<p><b>Zoom Link:</b> <a href="{zoom_link}">{zoom_link}</a></p>' if zoom_link else "<p>Zoom link or location will be sent via WhatsApp before your session.</p>"
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
            <p>Coach Hala has confirmed your session!</p>
            <p><b>Service:</b> {b['service']}</p>
            <p><b>Date & Time:</b> {b['date_display']} at {b['time']}</p>
            {format_detail}
            <p>Questions? WhatsApp: <b>01013996744</b></p>
            <p>See you soon!<br><b>Coach Hala El Shahawy</b></p>
          </div>
        </div>"""
    })


def _send_client_declined(b: dict):
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
            <p>Unfortunately Coach Hala is unable to confirm this slot. Please visit the website to choose another time.</p>
            <div style="text-align:center;margin:24px 0">
              <a href="{SITE_URL}" style="background:#E8613A;color:white;padding:14px 28px;border-radius:100px;text-decoration:none;font-weight:700">Choose Another Time →</a>
            </div>
            <p>Warm regards,<br><b>Coach Hala El Shahawy</b></p>
          </div>
        </div>"""
    })
