"""
What the harness needs from its environment.

Deliberately not `api.config.Settings`. That class is the *application's*
configuration, read by every process the deployment runs; eval credentials are
not application configuration and would be one more thing an Operator sees and
wonders whether they must set. The harness is a separate tool and reads its own
environment.

A missing variable is refused by name, and every missing variable is named at
once. "Configuration error" costs an operator a debugging session; four rounds
of fix-and-retry costs four.
"""
import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv


class ConfigMissing(RuntimeError):
    """The environment does not carry what the harness needs to run."""


@dataclass(frozen=True)
class EvalConfig:
    base_url: str
    student_email: str
    student_password: str
    google_api_key: str


@dataclass(frozen=True)
class JudgeConfig:
    """What it takes to grade text, as opposed to drive a deployment.

    Judging is pure text in, verdict out. Nothing in it dials a deployment, so
    The judge asks for the one variable it spends and no more. That is what lets
    `--calibrate-judge` run on a machine that has a Gemini key and nothing else
    -- which is exactly the machine most likely to want it, since grading the
    instrument is the one thing here that needs no stack up.
    """

    google_api_key: str


@dataclass(frozen=True)
class SmokeConfig:
    """What it takes to drive a browser at a deployment.

    No `google_api_key`: the smoke harness asserts that pages work, never that
    a tutor answered well, so there is no judge in it and nothing to bill.
    """

    base_url: str
    student_email: str
    student_password: str


_SMOKE_FIELDS = {
    "base_url": "EVAL_BASE_URL",
    "student_email": "EVAL_STUDENT_EMAIL",
    "student_password": "EVAL_STUDENT_PASSWORD",
}

# The environment variable behind each field. GOOGLE_API_KEY rather than
# GEMINI_API_KEY on purpose: it is what the application itself reads, and a
# shell that exports only GEMINI_API_KEY is why a live suite errors instead of
# skipping.
_JUDGE_FIELDS = {
    "google_api_key": "GOOGLE_API_KEY",
}

# The full read is exactly the union of the two narrow ones. Spelling it that
# way rather than relisting the variables means a field added to either subset
# cannot go missing here.
_FIELDS = {**_SMOKE_FIELDS, **_JUDGE_FIELDS}


def _read(fields: dict[str, str]) -> dict[str, str]:
    """Load `.env`, take the named variables, refuse if any is absent.

    Every missing variable is named at once: "configuration error" costs an
    operator a debugging session, and four rounds of fix-and-retry costs four.
    Only the requested variables are named, so a refusal never sends someone to
    configure something this call would not have read.
    """
    # `.env` is where this project keeps these, and every other entry point
    # loads it. Reading only `os.environ` meant `python -m evals` refused to
    # start on a correctly configured machine, naming four variables that were
    # sitting in the file it had not looked at.
    #
    # `usecwd=True` searches upward from the working directory rather than from
    # this file, which is what `api.config` already does via pydantic-settings'
    # cwd-relative `env_file`. Without it the search starts in `evals/` and the
    # harness would read a different `.env` than the application does.
    #
    # No `override`: an exported variable wins. A shell deliberately aiming the
    # harness at another deployment must not be quietly overruled by a file.
    load_dotenv(find_dotenv(usecwd=True))

    values = {field: os.environ.get(key, "") for field, key in fields.items()}

    missing = [fields[field] for field, value in values.items() if not value.strip()]
    if missing:
        raise ConfigMissing(
            "The eval harness needs these environment variables and they are "
            f"unset or empty: {', '.join(sorted(missing))}"
        )
    return values


def load_config() -> EvalConfig:
    """Read the eval environment, or refuse and say exactly what is absent."""
    values = _read(_FIELDS)

    # A trailing slash would put a double slash in every URL the harness builds.
    values["base_url"] = values["base_url"].rstrip("/")
    return EvalConfig(**values)


def load_judge_config() -> JudgeConfig:
    """Read only what judging spends.

    A strict subset of `load_config`, not a second mechanism: same file, same
    precedence, same refusal. The difference is the list.
    """
    return JudgeConfig(**_read(_JUDGE_FIELDS))


def load_smoke_config() -> SmokeConfig:
    """Read only what driving a browser spends."""
    values = _read(_SMOKE_FIELDS)

    # A trailing slash would put a double slash in every URL the browser opens.
    values["base_url"] = values["base_url"].rstrip("/")
    return SmokeConfig(**values)
