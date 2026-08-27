"""
The cases as data, and the environment the harness needs.

A case knows nothing about drivers and nothing about the judge's plumbing. That
is what lets one case run on both surfaces -- and what will let a voice-only or
chat-only case exist later without a special path.
"""
import pytest

from evals.cases import CASES, EMPTY_MEMORY_CONFABULATION, PRECONDITIONS, SURFACES
from evals.config import ConfigMissing, load_config

ENV_KEYS = ("EVAL_BASE_URL", "EVAL_STUDENT_EMAIL", "EVAL_STUDENT_PASSWORD", "GOOGLE_API_KEY")


def test_the_first_case_is_issue_14():
    assert EMPTY_MEMORY_CONFABULATION.name == "empty-memory-confabulation"


def test_it_asks_the_question_from_the_issue():
    assert "remember" in EMPTY_MEMORY_CONFABULATION.prompt.lower()


def test_it_tolerates_no_failures():
    """Inventing a Student's academic history is not tolerated at five percent."""
    assert EMPTY_MEMORY_CONFABULATION.threshold == 0


def test_it_requires_empty_memory():
    """Without the precondition the rubric has no factual answer."""
    assert EMPTY_MEMORY_CONFABULATION.precondition == "empty_memory"


def test_it_covers_both_surfaces():
    """The defect is duplicated in chat.py:154 and agent.py:284, so both are measured."""
    assert set(EMPTY_MEMORY_CONFABULATION.surfaces) == {"chat", "voice"}


def test_every_case_is_registered_under_its_own_name():
    """A typo here would make --case silently select nothing."""
    assert all(name == case.name for name, case in CASES.items())


def test_every_case_names_a_known_surface():
    for case in CASES.values():
        assert set(case.surfaces) <= SURFACES, case.name


def test_every_case_names_a_known_precondition():
    for case in CASES.values():
        assert case.precondition in PRECONDITIONS, case.name


def test_every_case_has_a_rubric_and_a_failure_label():
    """A case with no rubric would be judged against nothing."""
    for case in CASES.values():
        assert case.rubric.strip() and case.failure_label.strip(), case.name


# -- Configuration ----------------------------------------------------------


def test_a_complete_environment_loads(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.setenv(key, "value")

    assert load_config().base_url == "value"


def test_a_trailing_slash_on_the_base_url_is_dropped(monkeypatch):
    """Otherwise every URL the harness builds has a double slash in it."""
    for key in ENV_KEYS:
        monkeypatch.setenv(key, "value")
    monkeypatch.setenv("EVAL_BASE_URL", "http://localhost/")

    assert load_config().base_url == "http://localhost"


@pytest.mark.parametrize("missing", ENV_KEYS)
def test_a_missing_variable_is_refused_by_name(monkeypatch, tmp_path, missing):
    """Naming it is the point: "config error" costs an operator a debugging session."""
    # In a directory with no `.env`, because `load_config` now reads one. Run
    # from the repo root this would find the developer's own file and be handed
    # The very variable the test just removed.
    monkeypatch.chdir(tmp_path)
    for key in ENV_KEYS:
        monkeypatch.setenv(key, "value")
    monkeypatch.delenv(missing)

    with pytest.raises(ConfigMissing) as refusal:
        load_config()

    assert missing in str(refusal.value)


def test_every_missing_variable_is_named_at_once(monkeypatch, tmp_path):
    """One run, one complete list -- not four rounds of fix-and-retry."""
    monkeypatch.chdir(tmp_path)
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ConfigMissing) as refusal:
        load_config()

    assert all(key in str(refusal.value) for key in ENV_KEYS)
