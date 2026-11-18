"""The web shell: a terminal in the browser, connected to a process in one of an instance's containers.

    policy.py       who may open which shell where (permissions, production, root), and which container it reaches
    tickets.py      one-time tickets: the authenticated POST that opens the way for one WebSocket
    sessions.py     open shells, their limits, and the bridge between the WebSocket and the process
    docker_exec.py  the process itself, through the Docker Engine API (asyncio), and ending it
    recordings.py   encrypted recordings of what was shown and typed, and the list of past shells

The HTTP side is web/v1/shell.py (tickets, open and past shells, recordings); the WebSocket is web/v1/shell_socket.py,
served on the same asyncio loop as the live events (gunicorn's threads cannot hold WebSockets). docs/SECURITY.md has
the threat model; docs/ADMIN_GUIDE.md the settings (SHELL_*).
"""
