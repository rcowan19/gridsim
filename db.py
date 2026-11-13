"""Accounts, saved leagues and subscription state. SQLite locally (data/gridsim.db), Postgres in production (DATABASE_URL, e.g. Supabase)."""
import datetime as dt
import os

import sqlalchemy as sa

HERE = os.path.dirname(os.path.abspath(__file__))
md = sa.MetaData()
users = sa.Table("users", md, sa.Column("id", sa.String(64), primary_key=True), sa.Column("email", sa.String(320)),
                 sa.Column("created_at", sa.DateTime, default=lambda: now()))
leagues = sa.Table("leagues", md, sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
                   sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False),
                   sa.Column("platform", sa.String(16), nullable=False), sa.Column("league_ref", sa.String(64), nullable=False),
                   sa.Column("season", sa.Integer, nullable=False), sa.Column("name", sa.String(200)), sa.Column("my_team", sa.String(64)),
                   sa.Column("creds_enc", sa.Text), sa.Column("created_at", sa.DateTime, default=lambda: now()),
                   sa.UniqueConstraint("user_id", "platform", "league_ref", "season", name="uq_user_league"))
subs = sa.Table("subscriptions", md, sa.Column("user_id", sa.String(64), primary_key=True),
                sa.Column("stripe_customer_id", sa.String(64), index=True), sa.Column("stripe_subscription_id", sa.String(64)),
                sa.Column("status", sa.String(24)), sa.Column("current_period_end", sa.DateTime), sa.Column("season_pass_until", sa.DateTime),
                sa.Column("last_event_ts", sa.Integer), sa.Column("updated_at", sa.DateTime))
events = sa.Table("stripe_events", md, sa.Column("id", sa.String(80), primary_key=True), sa.Column("created_at", sa.DateTime, default=lambda: now()))

_engine = None
PAST_DUE_GRACE = dt.timedelta(days=3)


def url():
    u = os.environ.get("DATABASE_URL") or "sqlite:///" + os.path.join(HERE, "data", "gridsim.db")
    if u.startswith("postgres://"):
        u = "postgresql://" + u[len("postgres://"):]
    if u.startswith("postgresql://"):
        u = "postgresql+psycopg://" + u[len("postgresql://"):]
    return u


def engine():
    global _engine
    if _engine is None:
        u = url()
        if u.startswith("sqlite"):
            os.makedirs(os.path.dirname(u.replace("sqlite:///", "")) or ".", exist_ok=True)
        _engine = sa.create_engine(u, pool_pre_ping=True, future=True)
        md.create_all(_engine)
    return _engine


def reset(new_url=None):
    """Tests: drop the cached engine (optionally pointing at a new database)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
    if new_url:
        os.environ["DATABASE_URL"] = new_url


def now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)      # naive UTC, as stored


def upsert_user(uid, email):
    with engine().begin() as c:
        row = c.execute(sa.select(users.c.email).where(users.c.id == uid)).first()
        if row is None:
            c.execute(users.insert().values(id=uid, email=email))
        elif email and row[0] != email:
            c.execute(users.update().where(users.c.id == uid).values(email=email))


def _league_dict(r):
    return {"id": r.id, "platform": r.platform, "league": r.league_ref, "season": r.season, "name": r.name, "my_team": r.my_team, "has_creds": bool(r.creds_enc)}


def list_leagues(uid):
    with engine().connect() as c:
        return [_league_dict(r) for r in c.execute(sa.select(leagues).where(leagues.c.user_id == uid).order_by(leagues.c.id))]


def get_league(uid, lid, with_creds=False):
    with engine().connect() as c:
        r = c.execute(sa.select(leagues).where(leagues.c.user_id == uid, leagues.c.id == lid)).first()
    if r is None:
        return None
    d = _league_dict(r)
    if with_creds:
        d["creds_enc"] = r.creds_enc
    return d


def save_league(uid, platform, ref, season, name=None, my_team=None, creds_enc=None):
    """Insert or update (same platform + league + season). Credentials are only replaced when new ones are given."""
    with engine().begin() as c:
        cur = c.execute(sa.select(leagues.c.id).where(leagues.c.user_id == uid, leagues.c.platform == platform,
                                                       leagues.c.league_ref == ref, leagues.c.season == season)).first()
        vals = {k: v for k, v in dict(name=name, my_team=my_team, creds_enc=creds_enc).items() if v is not None}
        if cur:
            if vals:
                c.execute(leagues.update().where(leagues.c.id == cur[0]).values(**vals))
            return cur[0]
        return c.execute(leagues.insert().values(user_id=uid, platform=platform, league_ref=ref, season=season, **vals)).inserted_primary_key[0]


def update_league(uid, lid, **vals):
    with engine().begin() as c:
        return c.execute(leagues.update().where(leagues.c.user_id == uid, leagues.c.id == lid).values(**vals)).rowcount


def delete_league(uid, lid):
    with engine().begin() as c:
        return c.execute(leagues.delete().where(leagues.c.user_id == uid, leagues.c.id == lid)).rowcount


# ---- subscriptions ----
def get_sub(uid):
    with engine().connect() as c:
        return c.execute(sa.select(subs).where(subs.c.user_id == uid)).first()


def uid_for_customer(cust):
    with engine().connect() as c:
        r = c.execute(sa.select(subs.c.user_id).where(subs.c.stripe_customer_id == cust)).first()
    return r[0] if r else None


def set_sub(uid, **vals):
    vals["updated_at"] = now()
    with engine().begin() as c:
        if c.execute(sa.select(subs.c.user_id).where(subs.c.user_id == uid)).first():
            c.execute(subs.update().where(subs.c.user_id == uid).values(**vals))
        else:
            c.execute(subs.insert().values(user_id=uid, **vals))


def event_seen(eid):
    with engine().connect() as c:
        return c.execute(sa.select(events.c.id).where(events.c.id == eid)).first() is not None


def mark_event(eid):
    """Record a Stripe event as processed (webhooks are retried and can arrive twice). Called after the handler succeeds."""
    try:
        with engine().begin() as c:
            c.execute(events.insert().values(id=eid))
    except sa.exc.IntegrityError:
        pass


def entitlement(uid, at=None):
    at = at or now()
    r = get_sub(uid)
    out = {"active": False, "plan": None, "status": (r.status if r else None) or "none", "until": None}
    if r is None:
        return out
    if r.season_pass_until and r.season_pass_until > at:
        out.update(active=True, plan="season", until=r.season_pass_until.isoformat())
    end = r.current_period_end
    if r.status in ("active", "trialing") or (r.status == "past_due" and end and at < end + PAST_DUE_GRACE):
        if not out["active"]:
            out.update(active=True, plan="monthly", until=end.isoformat() if end else None)
    out["customer"] = bool(r.stripe_customer_id)
    return out


def delete_user_data(uid):
    with engine().begin() as c:
        c.execute(leagues.delete().where(leagues.c.user_id == uid))
        c.execute(users.delete().where(users.c.id == uid))
