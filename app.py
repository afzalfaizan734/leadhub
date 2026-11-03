"""LeadHub entry point.

    python app.py                   start the server
    python app.py --resetpins       issue fresh PINs for anyone who needs one
    python app.py --reset-admin-pin set a NEW admin PIN and show it (locked out? use this)
    python app.py --reseed          import the seed CSV again (only if DB is empty)
"""
import socket
import sys

from leadhub import api, clock, config, database, security, services


def reset_admin_pin():
    """Set a fresh PIN for every admin account, show it ONCE, then exit WITHOUT
    starting the server - so the PIN stays on screen and cannot scroll away.
    Needs no internet; run this on the server PC any time you are locked out."""
    database.initialise()
    app = api.Container()
    app.accounts.bootstrap_defaults()        # make sure an Admin exists on a fresh DB
    admins = [u for u in app.users.all_active() if u["role"] == "admin"]
    if not admins:
        print("No admin account found - nothing to reset.")
        return
    issued = [(u["name"], security.random_pin(config.ADMIN_PIN_MIN)) for u in admins]
    for (name, pin), u in zip(issued, admins):
        app.users.set_pin(u["id"], pin)
    bar = "=" * 60
    print("\n" + bar)
    print("   NEW ADMIN PIN  -  copy it now, it is shown only once")
    print(bar)
    for name, pin in issued:
        print(f"      {name:<16}  PIN:  {pin}")
    print(bar)
    print("   Sign in with this PIN, then change it after login if you like.")
    print("   The server was NOT started - run Start-LeadHub again to go live.\n")


def main():
    if "--reset-admin-pin" in sys.argv:
        reset_admin_pin()
        return
    print("LeadHub starting...")
    database.initialise()

    app = api.Container()
    saved_tz = app.settings.get("business_tz")
    if saved_tz:
        clock.set_business_tz(saved_tz)
    clock.sync()                 # true time from the internet (falls back to PC clock)
    clock.start_background()     # keep it fresh
    if config.DEMO:
        services.seed_demo(app)
        print("  DEMO mode: sample data loaded")
    else:
        app.accounts.bootstrap_defaults()
        if "--resetpins" in sys.argv:
            for u in app.users.all_active():
                app.users.set_pin(u["id"], "")      # clears, forcing re-issue below
        app.accounts.issue_missing_pins()
        seeded = app.seed.seed_if_empty()
        if seeded:
            print(f"  seeded {seeded:,} leads")
    print(f"  leads in database: {app.leads.count():,}")

    server, port = api.create_server(app)
    ip, host = api.lan_ip(), socket.gethostname().lower()
    suffix = "" if port == 80 else f":{port}"

    alias = _alias_registered()
    print("\n" + "=" * 64)
    print("   LEADHUB IS RUNNING")
    print("=" * 64)
    print(f"   On this PC   :  http://localhost{suffix}")
    if alias:
        print(f"   Your team    :  http://{config.SITE_NAME}{suffix}        <-- share this")
        print(f"   Also works   :  http://{host}{suffix}")
    else:
        print(f"   Your team    :  http://{host}{suffix}")
        print("                   (run Setup-Name.bat as admin, then reboot once,")
        print(f"                    to shorten that to http://{config.SITE_NAME}{suffix})")
    print(f"   By IP        :  http://{ip}{suffix}")
    print("=" * 64)
    print("   The name follows this PC even when the router changes its IP.")
    print("   Leave this window OPEN - closing it stops the server.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopping...")


def _alias_registered():
    """True once this PC is actually named leadmanager.

    An SMB alias (LanmanServer OptionalNames) is NOT enough - that registers only
    the <20> file-server record, which browsers never consult. Only the real
    computer name registers the <00> record that hostname lookups use.
    """
    return socket.gethostname().lower() == config.SITE_NAME.lower()


if __name__ == "__main__":
    main()
