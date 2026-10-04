class Source:
    """A data source. The server calls poll(now) ~5x per second while capturing.

    poll() returns a frame dict (see state.py) or None if nothing new arrived.
    """
    mode = "base"

    def start(self):
        pass

    def stop(self):
        pass

    def status(self, now):
        """(connection, human-readable detail) shown in the connection bar."""
        return "disconnected", ""

    def poll(self, now):
        return None
