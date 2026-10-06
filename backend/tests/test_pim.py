from personal_os.pim import Pim, active_provider


class _G:
    def status(self): return {"connected": True, "who": "google"}
    def _me(self): return "g@example.com"
    def gmail_search(self, q, n): return ["g", q, n]
    def tasks_lists(self): return ["google-tasks"]
    def calendar_events(self, *a): return ["g-cal"]


class _M:
    def status(self): return {"connected": True, "who": "microsoft"}
    def _me(self): return "m@example.com"
    def gmail_search(self, q, n): return ["m", q, n]
    def calendar_events(self, *a): return ["m-cal"]


def test_pim_follows_setting_and_keeps_tasks_on_google():
    s = {"pimProvider": "google"}
    pim = Pim(_G(), _M(), lambda: s)
    assert pim.provider == "google" and pim.gmail_search("q", 1) == ["g", "q", 1]
    s["pimProvider"] = "microsoft"
    assert pim.provider == "microsoft"
    assert pim.gmail_search("q", 1) == ["m", "q", 1]
    assert pim.calendar_events(2) == ["m-cal"]
    assert pim.status()["who"] == "microsoft"
    assert pim.tasks_lists() == ["google-tasks"]  # no Microsoft equivalent in Cut 1
    assert active_provider({"pimProvider": "bogus"}) == "google"


def test_pim_forwards_the_identity_helper_to_the_active_client():
    s = {"pimProvider": "google"}
    pim = Pim(_G(), _M(), lambda: s)
    assert pim._me() == "g@example.com"  # the reply tracker asks the mail account who it is
    s["pimProvider"] = "microsoft"
    assert pim._me() == "m@example.com"
    assert not hasattr(pim, "__deepcopy__")
