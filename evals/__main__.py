"""
`python -m evals` -- run the behavior evals against a running deployment.

    export EVAL_BASE_URL=http://localhost
    export EVAL_STUDENT_EMAIL=evals@example.com
    export EVAL_STUDENT_PASSWORD=...
    export GOOGLE_API_KEY=...
    .venv/bin/python -m evals --n 20

Deliberately not a pytest test. It is slow, it costs money, and it needs the
stack up; pytest.ini already excludes the live suites for the same reasons, and
a runner that prints a rate table is a better fit for this than an assertion.

`--calibrate-judge` measures the judge instead of the tutor, against handwritten
specimens whose ground truth is known. No deployment is contacted -- there is no
tutor in it -- and `GOOGLE_API_KEY` is the only variable it spends:

    .venv/bin/python -m evals --calibrate-judge

It reads only `GOOGLE_API_KEY`, so it runs on a machine that has a Gemini key
and no deployment configured -- which is the machine most likely to want it.

`--smoke` drives a real browser at the deployment and reports whether login,
upload, chat, and voice each work -- not whether the tutor answered well, just
whether the pages do:

    .venv/bin/python -m evals --smoke

Add `--headed` to watch the browser instead of running it headless. It reads
`EVAL_BASE_URL`, `EVAL_STUDENT_EMAIL`, and `EVAL_STUDENT_PASSWORD` but not
`GOOGLE_API_KEY` -- there is no judge in a smoke run, so nothing bills a model.

`load_dotenv` runs inside `_read`, so none of these forms need an export.
"""
import argparse
import asyncio
import logging
import os
import sys
import tempfile

import httpx

from evals import calibration
from evals.cases import CASES
from evals.config import ConfigMissing, load_config, load_judge_config, load_smoke_config
from evals.drivers.chat_http import ChatHttpDriver
from evals.drivers.voice_room import VoiceRoomDriver
from evals.report import exit_code, render
from evals.runner import run_case
from evals.smoke.browser import BrowserMissing, open_page
from evals.smoke.report import exit_code as smoke_exit_code
from evals.smoke.report import render as smoke_render
from evals.smoke.runner import run_smoke
from evals.stack import sign_in

# Twenty runs of a case against a live deployment; three judgings of a fixed
# specimen. The two differ by an order of magnitude because they cost different
# things -- an eval run drives a voice session, a calibration judging is two
# short completions -- so `--n` carries whichever default fits the mode it ran
# in rather than forcing one number to be wrong for both.
DEFAULT_RUNS = 20
DEFAULT_CALIBRATION_RUNS = 3

# Screenshots go somewhere an operator can find them and nothing tracks.
SCREENSHOT_DIR = os.path.join(tempfile.gettempdir(), "johnnyrobot-smoke")


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="evals", description=__doc__)
    parser.add_argument("--case", action="append", choices=sorted(CASES), help="default: all")
    parser.add_argument("--surface", action="append", choices=["chat", "voice"], help="default: all")
    parser.add_argument(
        "--calibrate-judge",
        action="store_true",
        help="grade the judge against handwritten specimens instead of running cases",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="drive a browser at the deployment instead of running cases",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="show the browser during --smoke instead of running headless",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=None,
        help=f"runs per case per surface (default {DEFAULT_RUNS}), "
        f"or judgings per specimen when calibrating (default {DEFAULT_CALIBRATION_RUNS})",
    )
    return parser.parse_args(argv)


async def _calibrate(args) -> int:
    """Measure the instrument rather than the tutor.

    No client, no sign-in, no drivers: the specimens are fixed text, so there is
    nothing to drive and no precondition to establish.

    `load_judge_config` runs first, and running it *here* rather than letting
    `judge._generate` reach it lazily is the whole point. `judge()` catches every
    exception and returns VOID, so a missing key discovered mid-run would print a
    full table of voids -- a result-shaped object produced by a harness that never
    ran. Up front it is one line naming what is absent.

    It is the narrow read, so a machine with a Gemini key and no deployment can
    grade the judge. Nothing in this path opens a base URL, and a refusal that
    named one would send an operator to configure something never read.
    """
    load_judge_config()

    result = await calibration.calibrate(
        calibration.SPECIMENS, n=args.n or DEFAULT_CALIBRATION_RUNS
    )

    print(calibration.render(result))
    return calibration.exit_code(result)


async def _smoke(args) -> int:
    """Drive a browser at the deployment and report what works.

    `load_smoke_config` rather than `load_config`: there is no judge in a
    smoke run, so demanding `GOOGLE_API_KEY` would refuse by naming a
    variable the run never reads.
    """
    config = load_smoke_config()

    async with open_page(config.base_url, headed=args.headed) as page:
        result = await run_smoke(page, config, SCREENSHOT_DIR)

    print(smoke_render(result, config.base_url))
    return smoke_exit_code(result)


async def _run(args) -> int:
    config = load_config()
    selected = [CASES[name] for name in (args.case or sorted(CASES))]

    async with httpx.AsyncClient(base_url=config.base_url) as client:
        student = await sign_in(client, config)

        drivers = {
            "chat": ChatHttpDriver(client, config.base_url),
            "voice": VoiceRoomDriver(client, config.base_url),
        }

        results = []
        for case in selected:
            for surface in case.surfaces:
                if args.surface and surface not in args.surface:
                    continue
                results.append(
                    await run_case(
                        case, drivers[surface], client, student, n=args.n or DEFAULT_RUNS
                    )
                )

    print(render(results))
    return exit_code(results)


def main(argv=None) -> int:
    # Scoped to the evals logger, not the root logger: basicConfig with
    # level=INFO on root would also switch on httpx's per-request logging,
    # and evals/stack.py is careful never to quote a response body (it may
    # carry a token or credential) -- httpx's own logging would undo that.
    logging.basicConfig(format="%(message)s")
    logging.getLogger("evals").setLevel(logging.INFO)
    args = _parse_args(argv)
    try:
        if args.smoke:
            return asyncio.run(_smoke(args))
        return asyncio.run(_calibrate(args) if args.calibrate_judge else _run(args))
    except ConfigMissing as missing:
        # Not a failing case: the harness never ran. Say so plainly rather than
        # letting an operator read a traceback as a result.
        print(f"{missing}", file=sys.stderr)
        return 2
    except BrowserMissing as no_browser:
        # Not a failing deployment: nothing ran. Say so where a table would
        # otherwise look like a result. Caught ahead of the generic
        # RuntimeError handler below -- BrowserMissing is a RuntimeError
        # subclass, and that handler's message would misname this as an
        # eval run failing to start.
        print(f"{no_browser}", file=sys.stderr)
        return 2
    except RuntimeError as unreachable:
        print(f"The eval run could not start: {unreachable}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
