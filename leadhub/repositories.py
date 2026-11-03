"""Data access. Each repository is the Information Expert for one table.

Services talk to repositories; nothing else writes SQL against these tables.
Lead age and bucket are always derived here from authority_date, so they are
correct on the day they are read rather than the day they were imported.
"""
from . import clock, config, database, security


def now() -> str:
    return clock.now_str()


def _age():
    """AGE expression using the accurate business-timezone 'now'."""
    return config.age_sql(clock.now_str())


def _lead_select():
    a = _age()
    return f"l.*, u.name AS agent, ({a}) AS age, ({config.bucket_case_sql(a)}) AS bucket"


class UserRepository:
    def all_active(self):
        with database.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT id,name,role FROM users WHERE active=1 ORDER BY role DESC,name")]

    def by_name(self, name):
        with database.connect() as c:
            return c.execute("SELECT * FROM users WHERE name=? AND active=1",
                             (name,)).fetchone()

    def by_id(self, uid):
        with database.connect() as c:
            return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

    def ensure(self, name, role):
        with database.connect() as c:
            c.execute("INSERT OR IGNORE INTO users(name,role,created_at) VALUES(?,?,?)",
                      (name, role, now()))
            c.commit()
            return c.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()["id"]

    def set_pin(self, uid, pin):
        salt, digest = security.new_pin_material(pin)
        with database.connect() as c:
            c.execute("UPDATE users SET pin_salt=?,pin_hash=? WHERE id=?", (salt, digest, uid))
            c.commit()

    def without_pin(self):
        with database.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT id,name,role FROM users WHERE active=1 AND "
                "(pin_hash IS NULL OR pin_hash='')")]

    def touch_login(self, uid):
        with database.connect() as c:
            c.execute("UPDATE users SET last_login=? WHERE id=?", (now(), uid))
            c.commit()

    def delete(self, uid):
        """Hard-delete a user. Callers must first return that user's leads to the
        pool (activity/call_log keep the person's name as free text, so history
        is preserved)."""
        with database.connect() as c:
            c.execute("DELETE FROM users WHERE id=?", (uid,))
            c.commit()


class ActivityRepository:
    def add(self, conn, lead_id, who, action, detail=""):
        conn.execute("INSERT INTO activity(lead_id,who,action,detail,ts) VALUES(?,?,?,?,?)",
                     (lead_id, who, action, detail, now()))

    def recent(self, limit=60):
        with database.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT a.*,l.legal_name FROM activity a "
                "LEFT JOIN leads l ON l.id=a.lead_id ORDER BY a.id DESC LIMIT ?", (limit,))]

    def recent_for(self, who, limit=12):
        with database.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT a.ts,a.action,a.detail,l.legal_name FROM activity a "
                "LEFT JOIN leads l ON l.id=a.lead_id WHERE a.who=? AND a.action='update' "
                "ORDER BY a.id DESC LIMIT ?", (who, limit))]


class CallLogRepository:
    """Append-only call history. Nothing here ever updates or deletes a row."""

    def add(self, lead_id, who, outcome, note):
        with database.write_lock, database.connect() as c:
            c.execute("INSERT INTO call_log(lead_id,who,outcome,note,ts) "
                      "VALUES(?,?,?,?,?)", (lead_id, who, outcome or "", note or "", now()))
            c.commit()
            return dict(c.execute("SELECT * FROM call_log WHERE lead_id=? "
                                  "ORDER BY id DESC LIMIT 1", (lead_id,)).fetchone())

    def for_lead(self, lead_id, limit=50):
        with database.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM call_log WHERE lead_id=? ORDER BY id DESC LIMIT ?",
                (lead_id, limit))]

    def count_for(self, lead_id):
        with database.connect() as c:
            return c.execute("SELECT COUNT(*) FROM call_log WHERE lead_id=?",
                             (lead_id,)).fetchone()[0]

    def logged_today(self, who):
        with database.connect() as c:
            return c.execute(
                "SELECT COUNT(*) FROM call_log WHERE who=? "
                f"AND substr(ts,1,10)='{clock.today_str()}'", (who,)).fetchone()[0]


class LeadRepository:
    """Owns every query against `leads`."""

    # ---------------- reading
    def _filters(self, *, scope=None, user_id=None, status=None, state=None,
                 bucket=None, text=None, fresh=False, day=None, month=None,
                 number_format=None, followup=None):
        where, args = [], []
        if scope == "mine":
            where.append("l.assigned_to=?")
            args.append(user_id)
        elif scope == "unassigned":
            where.append("l.assigned_to IS NULL")
        elif scope and scope.startswith("agent:"):
            where.append("l.assigned_to=?")
            args.append(int(scope.split(":")[1]))
        if status:
            where.append("l.status=?")
            args.append(status)
        if state:
            where.append("l.state=?")
            args.append(state.upper())
        if number_format:
            where.append("UPPER(IFNULL(l.number_format,''))=?")
            args.append(str(number_format).upper())
        if bucket:
            frag = config.bucket_where_sql(bucket, _age())
            if frag:
                where.append(frag)
        if followup == "due":
            # dated, and that date has arrived or passed
            where.append("IFNULL(l.next_followup,'')<>'' "
                         f"AND l.next_followup <= '{clock.today_str()}'")
        elif followup == "any":
            where.append("IFNULL(l.next_followup,'')<>''")
        if day:
            where.append("substr(l.assigned_at,1,10)=?")
            args.append(day)
        if month:
            where.append("substr(l.assigned_at,1,7)=?")
            args.append(month)
        if fresh:
            where.append(f"({_age()}) <= 7")
        if text:
            like = f"%{text}%"
            where.append("(l.legal_name LIKE ? OR l.usdot LIKE ? OR l.mc_number LIKE ? "
                         "OR l.officer LIKE ? OR l.phone LIKE ? OR l.email LIKE ? "
                         "OR l.city LIKE ?)")
            args += [like] * 7
        return (("WHERE " + " AND ".join(where)) if where else ""), args

    def search(self, *, limit=300, offset=0, **kw):
        # Follow-up lists read oldest-first; everything else keeps its existing order.
        _a = _age()
        order = ("l.next_followup ASC, l.legal_name"
                 if kw.get("followup") else
                 f"(({_a}) IS NULL), ({_a}), l.legal_name")
        w, args = self._filters(**kw)
        with database.connect() as c:
            total = c.execute(f"SELECT COUNT(*) FROM leads l {w}", args).fetchone()[0]
            rows = [dict(r) for r in c.execute(
                f"""SELECT {_lead_select()} FROM leads l
                    LEFT JOIN users u ON u.id=l.assigned_to {w}
                    ORDER BY {order}
                    LIMIT ? OFFSET ?""", args + [limit, offset])]
        return {"total": total, "rows": rows, "limit": limit, "offset": offset}

    def followup_counts(self, user_id=None):
        """Badge numbers: overdue, due today, and everything still scheduled."""
        where, args = ["IFNULL(next_followup,'')<>''"], []
        if user_id:
            where.append("assigned_to=?")
            args.append(user_id)
        w = "WHERE " + " AND ".join(where)
        td = clock.today_str()
        with database.connect() as c:
            r = c.execute(f"""
                SELECT
                  SUM(CASE WHEN next_followup <  '{td}' THEN 1 ELSE 0 END) overdue,
                  SUM(CASE WHEN next_followup =  '{td}' THEN 1 ELSE 0 END) today,
                  SUM(CASE WHEN next_followup >  '{td}' THEN 1 ELSE 0 END) upcoming,
                  COUNT(*) total
                FROM leads {w}""", args).fetchone()
        d = {k: (r[k] or 0) for k in ("overdue", "today", "upcoming", "total")}
        d["due"] = d["overdue"] + d["today"]
        return d

    def by_id(self, lead_id):
        with database.connect() as c:
            return c.execute(
                f"SELECT {_lead_select()} FROM leads l LEFT JOIN users u ON u.id=l.assigned_to "
                f"WHERE l.id=?", (lead_id,)).fetchone()

    def existing_usdots(self):
        with database.connect() as c:
            return {r[0] for r in c.execute("SELECT usdot FROM leads")}

    def count(self):
        with database.connect() as c:
            return c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]

    # ---------------- aggregates
    def status_totals(self):
        with database.connect() as c:
            return {r[0]: r[1] for r in c.execute(
                "SELECT status,COUNT(*) FROM leads GROUP BY status")}

    def unassigned_count(self):
        with database.connect() as c:
            return c.execute(
                "SELECT COUNT(*) FROM leads WHERE assigned_to IS NULL").fetchone()[0]

    def agent_summary(self):
        with database.connect() as c:
            return [dict(r) for r in c.execute("""
                SELECT u.id,u.name,u.role,
                  COUNT(l.id) AS assigned,
                  SUM(CASE WHEN l.status='New' THEN 1 ELSE 0 END) AS untouched,
                  SUM(CASE WHEN l.status IN ('Contacted','Interested') THEN 1 ELSE 0 END) AS working,
                  SUM(CASE WHEN l.status='Interested' THEN 1 ELSE 0 END) AS interested,
                  SUM(CASE WHEN l.status='Closed' THEN 1 ELSE 0 END) AS closed,
                  IFNULL(SUM(l.call_attempts),0) AS calls
                FROM users u LEFT JOIN leads l ON l.assigned_to=u.id
                WHERE u.active=1 AND u.role='agent'
                GROUP BY u.id ORDER BY u.name""")]

    def status_matrix(self):
        with database.connect() as c:
            return [dict(r) for r in c.execute(
                """SELECT IFNULL(u.name,'(unassigned)') owner, l.status st, COUNT(*) n
                   FROM leads l LEFT JOIN users u ON u.id=l.assigned_to
                   GROUP BY owner, st""")]

    def bucket_matrix(self):
        """Live counts per age bucket, split into unassigned vs each agent."""
        with database.connect() as c:
            return [dict(r) for r in c.execute(f"""
                SELECT ({config.bucket_case_sql(_age())}) AS bucket,
                       IFNULL(u.name,'(unassigned)') AS owner,
                       COUNT(*) AS n,
                       SUM(CASE WHEN l.assigned_to IS NULL THEN 1 ELSE 0 END) AS free
                FROM leads l LEFT JOIN users u ON u.id=l.assigned_to
                GROUP BY bucket, owner""")]

    def bucket_detail(self, bucket_key):
        """Contactability + state spread for one bucket - used by the Buckets tab."""
        frag = config.bucket_where_sql(bucket_key, _age())
        if not frag:
            return {}
        with database.connect() as c:
            row = c.execute(f"""
                SELECT COUNT(*) total,
                  SUM(CASE WHEN l.assigned_to IS NULL THEN 1 ELSE 0 END) unassigned,
                  SUM(CASE WHEN IFNULL(l.phone,'')<>'' THEN 1 ELSE 0 END) with_phone,
                  SUM(CASE WHEN IFNULL(l.email,'')<>'' THEN 1 ELSE 0 END) with_email,
                  SUM(CASE WHEN IFNULL(l.cell,'')<>'' THEN 1 ELSE 0 END) with_cell,
                  SUM(CASE WHEN l.insurance_status='ON_FILE' THEN 1 ELSE 0 END) insured
                FROM leads l WHERE {frag}""").fetchone()
            states = [dict(r) for r in c.execute(
                f"""SELECT l.state, COUNT(*) n FROM leads l WHERE {frag}
                    GROUP BY l.state ORDER BY n DESC LIMIT 8""")]
            fmt = [dict(r) for r in c.execute(
                f"""SELECT UPPER(IFNULL(l.number_format,'')) AS fmt, COUNT(*) n,
                           SUM(CASE WHEN l.assigned_to IS NULL THEN 1 ELSE 0 END) free
                    FROM leads l WHERE {frag} GROUP BY fmt ORDER BY n DESC""")]
        d = dict(row)
        d["states"] = states
        d["formats"] = fmt
        return d

    def format_totals(self):
        """MOTUS vs LEGACY across the whole table, and how many are free."""
        with database.connect() as c:
            return [dict(r) for r in c.execute(
                """SELECT UPPER(IFNULL(number_format,'')) AS fmt, COUNT(*) n,
                          SUM(CASE WHEN assigned_to IS NULL THEN 1 ELSE 0 END) free
                   FROM leads GROUP BY fmt ORDER BY n DESC""")]

    def agent_report(self, uid, name):
        frag = "assigned_to=?"
        with database.connect() as c:
            one = lambda s: c.execute(s, (uid,)).fetchone()[0]
            rep = {
                "assigned": one(f"SELECT COUNT(*) FROM leads WHERE {frag}"),
                "calls": one(f"SELECT IFNULL(SUM(call_attempts),0) FROM leads WHERE {frag}"),
                "touched": one(f"SELECT COUNT(*) FROM leads WHERE {frag} AND status<>'New'"),
                "with_notes": one(f"SELECT COUNT(*) FROM leads WHERE {frag} AND IFNULL(notes,'')<>''"),
                "followups": one(f"SELECT COUNT(*) FROM leads WHERE {frag} AND IFNULL(next_followup,'')<>''"),
                "by_status": {r[0]: r[1] for r in c.execute(
                    f"SELECT status,COUNT(*) FROM leads WHERE {frag} GROUP BY status", (uid,))},
                "by_disposition": {(r[0] or "(none)"): r[1] for r in c.execute(
                    f"SELECT disposition,COUNT(*) FROM leads WHERE {frag} "
                    f"GROUP BY disposition ORDER BY COUNT(*) DESC LIMIT 8", (uid,))},
                "by_bucket": {r[0]: r[1] for r in c.execute(
                    f"SELECT ({config.bucket_case_sql(_age())}) b,COUNT(*) FROM leads l WHERE l.{frag} GROUP BY b", (uid,))},
                "top_states": [{"state": r[0], "n": r[1]} for r in c.execute(
                    f"SELECT state,COUNT(*) FROM leads WHERE {frag} "
                    f"GROUP BY state ORDER BY COUNT(*) DESC LIMIT 6", (uid,))],
                "due": [dict(r) for r in c.execute(
                    f"SELECT legal_name,next_followup,status,phone FROM leads WHERE {frag} "
                    f"AND IFNULL(next_followup,'')<>'' ORDER BY next_followup LIMIT 10", (uid,))],
            }
        return rep

    # ---------------- writing
    def update_fields(self, lead_id, changes, who, activity: ActivityRepository):
        sets, args = [], []
        for k, v in changes.items():
            if k in config.EDITABLE_LEAD_FIELDS:
                sets.append(f"{k}=?")
                args.append(v)
        if not sets:
            return None
        sets += ["updated_at=?", "updated_by=?"]
        args += [now(), who, lead_id]
        with database.write_lock, database.connect() as c:
            c.execute(f"UPDATE leads SET {','.join(sets)} WHERE id=?", args)
            activity.add(c, lead_id, who, "update",
                         ", ".join(f"{k}={v}" for k, v in changes.items()
                                   if k in config.EDITABLE_LEAD_FIELDS))
            c.commit()
        return self.by_id(lead_id)

    def pick_unassigned(self, count, *, bucket=None, state=None, fresh=False,
                        number_format=None):
        where, args = ["l.assigned_to IS NULL"], []
        if bucket:
            frag = config.bucket_where_sql(bucket, _age())
            if frag:
                where.append(frag)
        if state:
            where.append("l.state=?")
            args.append(state.upper())
        if number_format:
            where.append("UPPER(IFNULL(l.number_format,''))=?")
            args.append(str(number_format).upper())
        if fresh:
            where.append(f"({_age()}) <= 7")
        _a = _age()
        with database.connect() as c:
            return [r[0] for r in c.execute(
                f"""SELECT l.id FROM leads l WHERE {' AND '.join(where)}
                    ORDER BY (({_a}) IS NULL), ({_a}) LIMIT ?""", args + [count])]

    def assign(self, lead_ids, agent_id, agent_name, actor, activity: ActivityRepository):
        """Stamps assigned_at, which is what the day/month folders group on.
        Re-assigning later the same day keeps the lead in that same day."""
        stamp = now()
        with database.write_lock, database.connect() as c:
            c.executemany(
                "UPDATE leads SET assigned_to=?,assigned_at=?,updated_at=?,updated_by=? "
                "WHERE id=?", [(agent_id, stamp, stamp, actor, i) for i in lead_ids])
            for i in lead_ids:
                activity.add(c, i, actor, "assign", f"-> {agent_name}")
            c.commit()
        return len(lead_ids)

    def unassign(self, lead_ids, actor, activity: ActivityRepository):
        stamp = now()
        with database.write_lock, database.connect() as c:
            c.executemany("UPDATE leads SET assigned_to=NULL,assigned_at=NULL,"
                          "updated_at=?,updated_by=? WHERE id=?",
                          [(stamp, actor, i) for i in lead_ids])
            for i in lead_ids:
                activity.add(c, i, actor, "unassign", "")
            c.commit()
        return len(lead_ids)

    def ids_for_agent(self, uid):
        with database.connect() as c:
            return [r[0] for r in c.execute(
                "SELECT id FROM leads WHERE assigned_to=?", (uid,))]

    # ---------------- calendar (month / day folders)
    def months(self, agent_id=None):
        """Months that actually contain assignments. Nothing is pre-created."""
        where, args = ["assigned_at IS NOT NULL", "assigned_at<>''"], []
        if agent_id:
            where.append("assigned_to=?")
            args.append(agent_id)
        with database.connect() as c:
            return [dict(r) for r in c.execute(
                f"""SELECT substr(assigned_at,1,7) AS month, COUNT(*) AS leads,
                           COUNT(DISTINCT substr(assigned_at,1,10)) AS days,
                           COUNT(DISTINCT assigned_to) AS agents,
                           SUM(CASE WHEN status<>'New' THEN 1 ELSE 0 END) AS worked,
                           IFNULL(SUM(call_attempts),0) AS calls
                    FROM leads WHERE {' AND '.join(where)}
                    GROUP BY month ORDER BY month DESC""", args)]

    def days(self, month, agent_id=None):
        where, args = ["substr(assigned_at,1,7)=?"], [month]
        if agent_id:
            where.append("assigned_to=?")
            args.append(agent_id)
        with database.connect() as c:
            return [dict(r) for r in c.execute(
                f"""SELECT substr(l.assigned_at,1,10) AS day,
                           IFNULL(u.name,'(unassigned)') AS agent,
                           l.assigned_to AS agent_id,
                           COUNT(*) AS leads,
                           SUM(CASE WHEN l.status<>'New' THEN 1 ELSE 0 END) AS worked,
                           SUM(CASE WHEN l.status='Interested' THEN 1 ELSE 0 END) AS interested,
                           SUM(CASE WHEN l.status='Closed' THEN 1 ELSE 0 END) AS closed,
                           SUM(CASE WHEN l.status='DNC' THEN 1 ELSE 0 END) AS dnc,
                           IFNULL(SUM(l.call_attempts),0) AS calls
                    FROM leads l LEFT JOIN users u ON u.id=l.assigned_to
                    WHERE {' AND '.join(where)}
                    GROUP BY day, agent, agent_id ORDER BY day DESC, agent""", args)]

    def day_status_split(self, day, agent_id=None):
        where, args = ["substr(assigned_at,1,10)=?"], [day]
        if agent_id:
            where.append("assigned_to=?")
            args.append(agent_id)
        with database.connect() as c:
            return {r[0]: r[1] for r in c.execute(
                f"SELECT status,COUNT(*) FROM leads WHERE {' AND '.join(where)} "
                f"GROUP BY status", args)}

    def bulk_insert(self, rows):
        """rows: tuples matching INSERT_COLS. Ignores USDOTs already present."""
        if not rows:
            return 0
        with database.write_lock, database.connect() as c:
            before = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            c.executemany(f"""INSERT OR IGNORE INTO leads ({','.join(INSERT_COLS)})
                              VALUES({','.join('?' * len(INSERT_COLS))})""", rows)
            c.commit()
            after = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        return after - before


INSERT_COLS = ["usdot", "docket", "mc_number", "number_format", "legal_name", "dba_name",
               "officer", "phone", "cell", "email", "street", "city", "state", "zip",
               "power_units", "drivers", "authority_date", "days_since",
               "insurance_company", "coverage", "insurance_status", "created_at"]


class SettingsRepository:
    """Tiny key/value store for admin settings (e.g. the business timezone)."""

    def get(self, key, default=None):
        with database.connect() as c:
            r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return r[0] if r else default

    def set(self, key, value):
        with database.write_lock, database.connect() as c:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, str(value)))
            c.commit()
