-- ══════════════════════════════════════════════════
--  Coach Hala Booking System — Supabase SQL Schema
--  Run this in: supabase.com → your project → SQL Editor
-- ══════════════════════════════════════════════════

-- 1. Bookings table — stores every booking request
CREATE TABLE IF NOT EXISTS bookings (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_name     TEXT NOT NULL,
  client_email    TEXT NOT NULL,
  client_phone    TEXT NOT NULL,
  service         TEXT NOT NULL,
  format          TEXT NOT NULL,       -- 'Zoom Call' or 'In-Person'
  specialty       TEXT NOT NULL,
  date            DATE NOT NULL,       -- stored as YYYY-MM-DD
  date_display    TEXT NOT NULL,       -- human readable e.g. "Monday, Jan 6, 2025"
  time            TEXT NOT NULL,       -- e.g. "10:00 AM"
  amount          INTEGER NOT NULL,    -- in EGP
  deposit         BOOLEAN DEFAULT FALSE,
  remaining       INTEGER DEFAULT 0,   -- remaining balance in EGP
  pay_method      TEXT DEFAULT 'InstaPay',
  instapay_ref    TEXT DEFAULT '',
  zoom_link       TEXT DEFAULT '',
  notes           TEXT DEFAULT '',
  emergency       TEXT DEFAULT '',
  status          TEXT DEFAULT 'pending'
                  CHECK (status IN ('pending','confirmed','declined','cancelled')),
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Blocked slots table — tracks taken/blocked time slots
CREATE TABLE IF NOT EXISTS blocked_slots (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  date        DATE NOT NULL,
  time        TEXT NOT NULL,
  reason      TEXT DEFAULT 'booked'
              CHECK (reason IN ('booked','coach_blocked','buffer')),
  booking_id  UUID REFERENCES bookings(id) ON DELETE CASCADE,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(date, time)   -- prevents duplicate slot entries
);

-- 3. Auto-update updated_at on bookings
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER bookings_updated_at
  BEFORE UPDATE ON bookings
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- 4. Indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_bookings_date    ON bookings(date);
CREATE INDEX IF NOT EXISTS idx_bookings_status  ON bookings(status);
CREATE INDEX IF NOT EXISTS idx_bookings_email   ON bookings(client_email);
CREATE INDEX IF NOT EXISTS idx_blocked_date     ON blocked_slots(date);

-- ══════════════════════════════════════════════════
--  SAMPLE DATA (optional — delete before going live)
-- ══════════════════════════════════════════════════

-- Block some slots as examples
-- INSERT INTO blocked_slots (date, time, reason) VALUES
--   ('2025-07-15', '10:00 AM', 'booked'),
--   ('2025-07-15', '2:00 PM',  'booked'),
--   ('2025-07-18', '11:00 AM', 'coach_blocked');
