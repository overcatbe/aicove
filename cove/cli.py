# The aicove command. Without arguments it opens the window; the rest is what the window
# runs behind its buttons, usable from a terminal too.

import sys

USAGE = """aicove                          open the window
aicove start NAME               load a launcher's model, or a persona on its model
aicove stop                     stop the model
aicove stop all                 stop the model and the tools marked for it
aicove status                   what is running
aicove list                     launchers and personas
aicove info                     this machine's launchers as JSON (what another machine asks)
aicove tools                    the tools of this machine and whether they run
aicove tools start|stop NAME    one of them
aicove passwords [sync]         names of the password entries | sync the store
aicove chat PERSONA [ID|last] ["message"]   chat from a terminal (no message: its chats)
aicove agent [help]             agents: run, schedule, reports (aicove agent help)
aicove check                    look at everything the settings point to
aicove fetch JOB.json           download helper behind "Add model" (resumes)"""


def start(args):
    from . import launchers, personas, server
    if len(args) != 1:
        print("usage: aicove start NAME")
        return 2
    launcher = launchers.get(args[0])
    if launcher is None:
        # Not a launcher: a persona is started on the launcher its file names.
        p = personas.load_all().get(args[0])
        if p is None:
            print(f"No launcher or persona called {args[0]}. See: aicove list")
            return 1
        launcher = launchers.get(p.launcher) if p.launcher else None
        if launcher is None:
            print(f"{p.name} has no model set (or its launcher is gone) — pick one in AIcove, Personas tab.")
            return 1
    return server.llama_start(launcher)


def show_list():
    from . import launchers, personas, server
    loaded = server.running()
    print("Launchers:")
    for l in launchers.find_launchers():
        mark = ">" if loaded and not l.error and l.model == loaded[0] else " "
        print(f" {mark} {l.name:28} {l.error or l.friendly}")
    print("Personas:")
    for p in personas.load_all().values():
        print(f"   {p.id:28} {p.name}" + (f"  (on {p.launcher})" if p.launcher else ""))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # The window shows our output line by line while a model loads.
    sys.stdout.reconfigure(line_buffering=True)
    from . import folder
    folder.move_in()
    if not argv:
        from . import gui
        return gui.main()
    cmd, args = argv[0], argv[1:]
    if cmd == "fetch":
        from . import fetch
        return fetch.main(args)
    if cmd == "chat":
        from . import chat
        return chat.main(args)
    if cmd == "agent":
        from . import agents
        return agents.main(args)
    from . import server, settings
    if cmd == "start":
        return start(args)
    if cmd == "stop" and args in ([], ["all"]):
        server.stop_all() if args else server.llama_stop()
        return 0
    if cmd == "status" and not args:
        server.status()
        return 0
    if cmd == "list" and not args:
        return show_list()
    if cmd == "info" and not args:
        import json
        from . import launchers
        print(json.dumps(launchers.info()))
        return 0
    if cmd == "passwords":
        from . import passwords
        return passwords.main(args)
    if cmd == "tools":
        from . import tools
        return tools.main(args)
    if cmd == "check" and not args:
        if not settings.S.exists:
            print(f"No {settings.CONFIG_FILE} yet — this is what was found on this machine:")
        for ok, text in settings.check():
            print({True: "  ok  ", False: " !!   ", None: "      "}[ok] + text)
        return 0
    print(USAGE)
    return 0 if cmd in ("help", "-h", "--help") else 2
