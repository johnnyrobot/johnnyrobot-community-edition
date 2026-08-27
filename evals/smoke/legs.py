"""
The four legs, and what each one concludes.

Each leg returns a verdict rather than raising, so a broken upload cannot stop
The voice leg from running. That is the whole point of the shape: twenty
minutes before a demo, "the upload is broken" is a much worse answer than "the
upload is broken and voice is fine".

Timeouts are generous rather than tight. The stated environment is a live demo
on hostile venue wifi, and a slow-but-successful session is still a working
session -- the same reasoning `evals/drivers/chat_http.py` already applies.
"""
from pathlib import Path

from evals.config import SmokeConfig
from evals.smoke.browser import Page
from evals.smoke.results import LegResult, Verdict

# Sign-in is one POST behind Caddy; anything slower than this is a fault.
LOGIN_TIMEOUT = 30.0

EMAIL = 'input[type="email"]'
PASSWORD = 'input[type="password"]'
SUBMIT = 'button[type="submit"]'


async def auth_leg(page: Page, config: SmokeConfig) -> LegResult:
    """Sign in through the form, because the form is what this measures.

    `evals/stack.py` signs in over HTTP and cannot stand in for this. A
    deployment whose API accepts the credentials while the browser cannot reach
    The dashboard is exactly the failure worth catching, and an HTTP sign-in
    would report it green.
    """
    await page.goto("/login")
    await page.fill(EMAIL, config.student_email)
    await page.fill(PASSWORD, config.student_password)
    await page.click(SUBMIT)

    if await page.wait_for_url("/dashboard", LOGIN_TIMEOUT):
        return LegResult(name="auth", verdict=Verdict.PASS)

    # State only what was observed. The form was submitted; /dashboard never
    # arrived. The old wording -- "signed in but never reached /dashboard" --
    # asserted a sign-in this leg never actually witnessed, then took it back
    # in the same breath.
    return LegResult(
        name="auth",
        verdict=Verdict.FAIL,
        detail=(
            "submitted the sign-in form but never reached /dashboard — the "
            "credentials were refused, or the API never answered"
        ),
    )


# Import runs through Gemini File Search and is the slowest thing here.
UPLOAD_TIMEOUT = 60.0
IMPORT_TIMEOUT = 180.0
REMOVE_TIMEOUT = 60.0

# More than a handful of stale fixture rows means something is wrong that this
# leg should not paper over by looping. Bounded so a fake -- or a deployment
# that accepts the click and never removes the row -- cannot spin here.
MAX_STALE_ROWS = 5

FIXTURE_PATH = str(Path(__file__).parent / "fixture" / "smoke-material.txt")

DOCUMENT_TITLE = '[data-testid="document-title-input"]'
DOCUMENT_UPLOAD = '[data-testid="document-upload-input"]'
DOCUMENT_SUBMIT = '[data-testid="document-upload-submit"]'
DOCUMENT_ROW = '[data-testid="document-row"]'

# `DocumentsPage.tsx` is a two-step form, not a dropzone: the title input is
# `required` and `handleUpload` refuses to call the mutation at all unless
# both a file *and* a title are present. A first live run picked the file and
# stopped there -- the submit button was never clicked, the mutation never
# fired, and the row never appeared ("0 textbooks" on screen). Fixed here, not
# in the app: a two-step upload form is a legitimate UI choice, and the
# harness's job is to drive the form that exists.
#
# The title is not free-form. FIXTURE_ROW below scopes on the literal text
# "smoke-material", which is the row's rendered title (`tb.title`) -- so
# whatever this leg types into the title field IS the string the row-lookup
# has to match. Anything else and the row this leg waits for would never be
# The row this leg created.
FIXTURE_TITLE = "smoke-material"

# Scoped to our own upload by title, so a Student's real materials are never
# counted and never deleted. Playwright's selector engine resolves :has-text.
FIXTURE_ROW = f'{DOCUMENT_ROW}:has-text("smoke-material")'

# The row carries a badge only when something is wrong or unfinished. A clean
# import renders no badge at all, so "ready" is asserted by absence.
PROCESSING = 'text="Processing"'
IMPORT_FAILED = 'text="Failed — not searchable"'

# Scoped inside FIXTURE_ROW the same way FIXTURE_ROW itself is scoped off the
# whole page (see the comment above it). A page-wide PROCESSING/IMPORT_FAILED
# check reads every Course Material in the Library, not just this leg's own
# upload: another Student's material stuck Processing would block this leg for
# The full IMPORT_TIMEOUT and then FAIL it, and another material already
# Failed would make this leg confidently -- and wrongly -- report OUR upload
# as "Failed — not searchable". `>>` chains a second selector to resolve
# inside the first's match, the same Playwright scoping mechanism
# `wait_for_count`'s docstring in browser.py already relies on.
ROW_PROCESSING = f"{FIXTURE_ROW} >> {PROCESSING}"
ROW_IMPORT_FAILED = f"{FIXTURE_ROW} >> {IMPORT_FAILED}"


async def documents_leg(page: Page, fixture_path: str = FIXTURE_PATH) -> LegResult:
    """Upload one small file, wait for it to settle, then remove it.

    Removal is part of the leg, not tidiness. the reset-only demo profile makes the demo
    deployment reset-only, so an upload survives the run, and a harness that
    grows the Library on every invocation changes what the next run's chat and
    voice legs are working against.
    """
    await page.goto("/documents")

    # A previous run that crashed mid-leg would leave a fixture row behind and
    # make "it appeared" trivially true. Clear it first, bounded rather than
    # unbounded: a click that never actually removes the row (a fake that does
    # not mutate state, or a deployment that silently ignores the click) must
    # not spin this leg forever.
    for _ in range(MAX_STALE_ROWS):
        if not await page.count(FIXTURE_ROW):
            break
        await page.click(f"{FIXTURE_ROW} button")
        if not await page.wait_for_count(FIXTURE_ROW, 0, REMOVE_TIMEOUT):
            break

    # The loop above can exit two ways: the top check found no row left (the
    # clean case), or the bounded loop ran out -- via the inner `break` on a
    # removal that never stuck, or by exhausting MAX_STALE_ROWS attempts --
    # with the row still sitting there. Without this check, the next line
    # (`wait_for(FIXTURE_ROW, ...)`) would find that STALE row and report "it
    # appeared", so a leg that never even established a clean starting point
    # would go on to grade a previous run's leftover material as its own.
    if await page.count(FIXTURE_ROW):
        return LegResult(
            name="documents",
            verdict=Verdict.FAIL,
            detail=(
                "a leftover material from a previous run could not be "
                "cleared, so this run could not be trusted to be measuring "
                "its own upload"
            ),
        )

    # Two steps, in the order the form actually validates them: the title is
    # `required` same as the file, and only the click on submit fires
    # `handleUpload` -- selecting a file alone leaves the mutation unsent.
    await page.fill(DOCUMENT_TITLE, FIXTURE_TITLE)
    await page.upload(DOCUMENT_UPLOAD, fixture_path)
    await page.click(DOCUMENT_SUBMIT)

    if not await page.wait_for(FIXTURE_ROW, UPLOAD_TIMEOUT):
        return LegResult(
            name="documents",
            verdict=Verdict.FAIL,
            detail="the uploaded material never appeared in the list",
        )

    # Fast path: already Failed by the time the row first appeared. Scoped to
    # this leg's own row (ROW_IMPORT_FAILED) rather than the page, so someone
    # else's Failed material can never be reported as ours.
    if await page.count(ROW_IMPORT_FAILED):
        return LegResult(
            name="documents",
            verdict=Verdict.FAIL,
            detail=(
                "the material imported as 'Failed — not searchable', so a tutor "
                "would never find it"
            ),
        )

    if not await page.wait_for_gone(ROW_PROCESSING, IMPORT_TIMEOUT):
        return LegResult(
            name="documents",
            verdict=Verdict.FAIL,
            detail="the material was still marked Processing when the wait ran out",
        )

    # `wait_for_gone` returning True proves only that the Processing badge is
    # gone, never that the import succeeded -- a Course Material moves from
    # `processing` straight to `failed`, never back through Processing on the
    # way to success or failure. The IMPORT_FAILED check above ran before the
    # badge could possibly have flipped yet, so without checking again here, a
    # row that started Processing and only later failed would sail past both
    # checks, get removed below, and report PASS on a failed import -- the
    # single most important FAIL this leg exists to catch.
    if await page.count(ROW_IMPORT_FAILED):
        return LegResult(
            name="documents",
            verdict=Verdict.FAIL,
            detail=(
                "the material imported as 'Failed — not searchable', so a tutor "
                "would never find it"
            ),
        )

    await page.click(f"{FIXTURE_ROW} button")

    if not await page.wait_for_count(FIXTURE_ROW, 0, REMOVE_TIMEOUT):
        return LegResult(
            name="documents",
            verdict=Verdict.FAIL,
            detail="the material uploaded but would not remove, so it is still there",
        )

    return LegResult(name="documents", verdict=Verdict.PASS)


# The route calls Mem0 twice and Gemini once, all serially.
CHAT_TIMEOUT = 120.0

# Deliberately trivial. This leg asserts that a reply arrived, never that it
# was good -- `evals/` measures that, with a rubric and a calibrated judge
# behind it -- so the prompt should provoke nothing worth judging.
CHAT_PROMPT = "Hello"

CHAT_INPUT = '[data-testid="chat-input"]'
CHAT_SEND = '[data-testid="chat-send"]'
CHAT_REPLY = '[data-testid="chat-message-assistant"]'


async def chat_leg(page: Page) -> LegResult:
    """Send one message and require a non-empty reply to render.

    A reply on screen means the route, the auth header, the Gemini call and the
    React state all worked. Whether it is a *good* reply is a question with an
    instrument already pointed at it.
    """
    await page.goto("/chat")
    await page.fill(CHAT_INPUT, CHAT_PROMPT)
    await page.click(CHAT_SEND)

    if not await page.wait_for(CHAT_REPLY, CHAT_TIMEOUT):
        return LegResult(
            name="chat",
            verdict=Verdict.FAIL,
            detail=f"no reply rendered within {CHAT_TIMEOUT:.0f}s",
        )

    if not (await page.text(CHAT_REPLY)).strip():
        return LegResult(
            name="chat",
            verdict=Verdict.FAIL,
            detail="a reply bubble rendered with nothing in it",
        )

    return LegResult(name="chat", verdict=Verdict.PASS)


# The agent has to join a room and start a realtime model. Generous, because a
# slow tutor is still a working tutor and this runs on venue wifi.
VOICE_TIMEOUT = 90.0

AGENT_AUDIO = '[data-testid="agent-audio"]'

# Read off `frontend/src/lib/voiceStatus.ts`, character for character. Both use
# U+2026, not three dots. These are asserted as visible words rather than by a
# testid on purpose: that label is the contract the voice-status contract established, and a
# testid would let it regress to reading "Connected" while this stayed green.
NOT_CONNECTED = 'text="Not connected"'
NO_TUTOR = 'text="Waiting for the tutor to join…"'

# Transitional, not terminal: the pill is still on its way to one of the two
# states above (or to a live one) when the wait runs out. Neither proves the
# room was rejected nor that a tutor joined -- it proves only that the session
# had not settled yet, so it earns its own branch rather than being folded
# into NOT_CONNECTED or, worse, into the "tutor joined" branch below.
CONNECTING = 'text="Connecting…"'
RECONNECTING = 'text="Reconnecting…"'

# `voiceStatus.ts`'s four `live: true` labels -- the only DOM states that
# prove an agent joined and is driving the pill. `AGENT_AUDIO` needs the
# agent to have actually spoken first; these do not (see `voice_leg`'s
# docstring for why that gap matters), so PASS is either one.
READY = 'text="Ready"'
LISTENING = 'text="Listening…"'
THINKING = 'text="Thinking…"'
SPEAKING = 'text="Speaking…"'
LIVE_LABELS = (READY, LISTENING, THINKING, SPEAKING)


async def voice_leg(page: Page) -> LegResult:
    """Require that a tutor joined the room -- shown by audio or by the pill.

    PASS is `AGENT_AUDIO` (the agent published a track) OR any of
    `LIVE_LABELS` (per `voiceStatus.ts`, a `live: true` label requires the
    room connected AND an agent present). Audio alone used to be the whole
    test, and a second live run broke it on a fully healthy deployment: a
    green "Listening…" pill, a live microphone, an End Session control -- and
    no `agent-audio`, because `agent.py` calls `session.start()` but never
    `generate_reply()`, so the tutor never speaks first. A smoke run that
    never speaks to the agent can never make it speak either, so `agent-audio`
    could never appear no matter how healthy the session was -- the leg was
    asserting a property (spoke first) the deployment was never asked to
    have. The label is accepted as equally sufficient proof not because it is
    a weaker stand-in for audio, but because it is gated on the same fact
    this leg exists to check: a tutor joined.

    Read from the DOM rather than from LiveKit, because this is a UI harness.
    If the room is healthy and the page fails to reflect it, a Student sees a
    broken session -- the voice-status contract exactly, where a green pill and a live mic sat
    on top of a connection LiveKit had already rejected. Asserting on LiveKit
    internals would have passed while a Student stared at a lying pill.

    A live run once hit a stale deployment: no `agent-audio` element, and none
    of the text labels below either, because the build predated all of them.
    The old fallback branch reported "a tutor joined but published no audio"
    unconditionally whenever none of the three recognised states matched --
    which is exactly what a `Connecting…`/`Reconnecting…` pill still on screen
    at the timeout looks like, and exactly what a page that failed to render
    at all looks like too. That fallback asserted two things it never
    observed (a connection, and a tutor), and it sent an operator to debug a
    perfectly healthy agent. A confidently wrong diagnosis is worse than an
    honest "cannot tell": it burns the operator's time on the wrong system
    while the real fault goes unnoticed. So every remaining branch below is
    gated on having actually observed the state it names, and the branch that
    cannot name a cause says so plainly instead of guessing one.
    """
    await page.goto("/session")

    if await page.wait_for_any([AGENT_AUDIO, *LIVE_LABELS], VOICE_TIMEOUT):
        return LegResult(name="voice", verdict=Verdict.PASS)

    # Neither audio nor a live label ever showed up -- `wait_for_any` above
    # already ruled out every `live: true` state, so nothing past this point
    # may claim a tutor joined. Which of the known non-live states decides
    # where an operator goes next, so say it rather than reporting a bare
    # failure.
    if await page.count(NOT_CONNECTED):
        return LegResult(
            name="voice",
            verdict=Verdict.FAIL,
            detail=(
                "the room never connected — check LIVEKIT_URL, the API key, and "
                "the network"
            ),
        )

    if await page.count(NO_TUTOR):
        return LegResult(
            name="voice",
            verdict=Verdict.FAIL,
            detail=(
                "the room connected but no tutor joined — the agent is down. "
                "A retired model has caused exactly this before"
            ),
        )

    # Still on its way to connecting when the timeout hit. This is not a
    # rejected connection (NOT_CONNECTED is terminal; this is not) and it is
    # certainly not a tutor having joined -- it is a timeout on a connection
    # that was still in flight, so say exactly that and nothing more.
    if await page.count(CONNECTING):
        return LegResult(
            name="voice",
            verdict=Verdict.FAIL,
            detail=(
                'the pill still read "Connecting…" when the wait expired — the '
                "session never finished connecting within the timeout"
            ),
        )

    if await page.count(RECONNECTING):
        return LegResult(
            name="voice",
            verdict=Verdict.FAIL,
            detail=(
                'the pill still read "Reconnecting…" when the wait expired — the '
                "session never finished connecting within the timeout"
            ),
        )

    # None of the above, and `wait_for_any` already ruled out `AGENT_AUDIO`
    # and every `LIVE_LABELS` entry -- not NOT_CONNECTED, not NO_TUTOR, not
    # transitional, not a live state -- the page showed nothing this harness
    # knows how to read at all. (There used to be a branch here for "a tutor
    # joined but published no audio", reached whenever a `LIVE_LABELS` text
    # matched instead of `AGENT_AUDIO` -- exactly what a live run's healthy
    # "Listening…" session with no proactive greeting looked like, wrongly
    # reported as FAIL. Now that those labels are a PASS condition above,
    # that state can no longer reach this point, so the branch was removed
    # rather than left as dead code nothing could ever execute.) This
    # fallback is for the *other* live failure -- a stale deployment whose
    # build predated `agent-audio` and every text label -- and it must report
    # plainly that the state is unrecognised, offering the two likeliest
    # causes as a suggestion, not a diagnosis. Guessing a specific cause here
    # (e.g. "the agent is down") is exactly the mistake that sent an operator
    # chasing a healthy agent during the run this branch exists to prevent.
    return LegResult(
        name="voice",
        verdict=Verdict.FAIL,
        detail=(
            "the voice page showed no state this harness recognises — it may "
            "not have rendered, or the deployed build may not match this "
            "harness"
        ),
    )
