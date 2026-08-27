"""
A `Page` that answers from a dict instead of a browser.

Selectors are opaque strings here: the fake never parses them, it just looks
them up. That is enough to test every leg's decision-making, which is the part
that can be wrong in a way a human would not notice.
"""


class FakePage:
    """A `Page` implementation against a dict, never raising.

    The action methods (`goto`, `fill`, `click`, `upload`, `screenshot`) are
    deliberately more permissive than the real browser: they never raise for an
    unknown selector. An offline-green leg is not by itself proof it will survive
    a real browser—the live run is what exercises that.
    """
    def __init__(self, present=(), text=None, url="/login", appears=(), goes=()):
        # Selectors currently matching, and how many of each.
        self.present = dict.fromkeys(present, 1) if not isinstance(present, dict) else dict(present)
        self.texts = dict(text or {})
        self.current_url = url
        # Selectors `wait_for` should report as arriving, and ones
        # `wait_for_gone` should report as leaving.
        self.appears = set(appears)
        self.goes = set(goes)
        self.actions: list[tuple[str, ...]] = []

    async def goto(self, path):
        self.current_url = path
        self.actions.append(("goto", path))

    async def fill(self, selector, value):
        self.actions.append(("fill", selector, value))

    async def click(self, selector):
        self.actions.append(("click", selector))

    async def upload(self, selector, file_path):
        self.actions.append(("upload", selector, file_path))

    async def count(self, selector):
        return self.present.get(selector, 0)

    async def text(self, selector):
        return self.texts.get(selector, "")

    async def wait_for(self, selector, timeout):
        return selector in self.appears or self.present.get(selector, 0) > 0

    async def wait_for_gone(self, selector, timeout):
        return selector in self.goes or self.present.get(selector, 0) == 0

    async def wait_for_count(self, selector, expected, timeout):
        return self.present.get(selector, 0) == expected

    async def wait_for_any(self, selectors, timeout):
        # Same honouring of `appears`/`present` as `wait_for` above, just
        # checked across a list and returning which one matched (or "").
        for selector in selectors:
            if selector in self.appears or self.present.get(selector, 0) > 0:
                return selector
        return ""

    async def wait_for_url(self, fragment, timeout):
        return fragment in self.current_url

    async def screenshot(self, file_path):
        self.actions.append(("screenshot", file_path))
