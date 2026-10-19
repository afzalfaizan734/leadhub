"""LeadHub entry point.

    python app.py              start the server
    python app.py --resetpins  issue fresh PINs for anyone who needs one
    python app.py --reseed     import the seed CSV again (only if DB is empty)
"""
import socket
import sys

from leadhub import api, config, database, services


def main():
    print("LeadHub starting...")
    database.initialise()

    app = api.Container()
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
