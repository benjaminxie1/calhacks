"""Sponsor integrations without the network: the dispute room is faked at the
HTTP boundary, so these check what we send and how we read rulings back."""
from cam import run as cam_run
from cam.room import Room


class FakeRoom(Room):
    def __init__(self, rulings_rows=()):
        self.calls, self.rows = [], list(rulings_rows)

    def call(self, reducer, *args):
        self.calls.append((reducer, *args))

    def sql(self, query):
        return self.rows


def run_with(room):
    return cam_run.run(cam_run.SAMPLE / "lease.md", cam_run.SAMPLE / "statement.yaml", "428-0061-012-03", "Alameda",
                       "regex", "template", {}, None, cam_run.SAMPLE / "assessor_cache.json", room=room)


def test_room_gets_audit_trail_and_findings():
    room = FakeRoom()
    r = run_with(room)
    names = [c[0] for c in room.calls]
    assert names[0] == "open_audit" and names[-1] == "close_audit"
    assert names.count("log_event") == len(r["trail"])
    assert names.count("add_finding") == len(r["result"].findings)
    assert room.calls[-1][-1] == "needs_owner"  # L9 is still open
    assert ("L9" in [c[2] for c in room.calls if c[0] == "add_finding"])


def test_owner_ruling_in_room_reaches_the_auditor():
    room = FakeRoom([{"rule": "L9", "ruling": [0, "exclude_capital"]}, {"rule": "share:", "ruling": [1, []]}])
    assert room.rulings("x") == {"L9": "exclude_capital"}
    r = run_with(room)
    assert not any(f.needs_owner for f in r["result"].findings)
    assert room.calls[-1][-1] == "drafted"
