-- Boxoffice schema. Safe to re-run: it resets seat counts but keeps reservations.
CREATE TABLE IF NOT EXISTS events (
  id         int PRIMARY KEY,
  name       text NOT NULL,
  seats_left int  NOT NULL
);

CREATE TABLE IF NOT EXISTS reservations (
  id          uuid PRIMARY KEY,
  event_id    int  NOT NULL REFERENCES events (id),
  seats       int  NOT NULL,
  price_cents int  NOT NULL,
  paid        boolean NOT NULL DEFAULT false,
  charge_id   text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Event 1 is the hot on-sale; it has enough seats that load tests never sell it out.
INSERT INTO events (id, name, seats_left) VALUES
  (1, 'Arctic Lights - Oslo Spektrum', 5000000),
  (2, 'The Harbour Quartet - Bergen', 800000),
  (3, 'Night Ferry - Tromso', 800000),
  (4, 'Glass Choir - Trondheim', 800000),
  (5, 'Static Bloom - Stavanger', 800000)
ON CONFLICT (id) DO UPDATE SET seats_left = EXCLUDED.seats_left;
