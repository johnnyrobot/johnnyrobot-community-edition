"""
Where the harness reads its credentials from.

`evals/config.py` read `os.environ` and nothing else, while every other entry
point in this repo loads `.env` first. So `python -m evals` refused to start on
a machine that was configured correctly -- the four variables were in `.env`,
which is where this project puts them -- and the operator had to discover that
this one tool wanted them exported by hand.

An exported variable still wins. A shell that deliberately points the harness at
a different deployment must not be silently overruled by a file.
"""
import pytest

from evals.config import ConfigMissing, load_config, load_judge_config, load_smoke_config

ENV_VARS = (
    "EVAL_BASE_URL",
    "EVAL_STUDENT_EMAIL",
    "EVAL_STUDENT_PASSWORD",
    "GOOGLE_API_KEY",
)

DOTENV = "\n".join(
    (
        "EVAL_BASE_URL=https://from-the-file.example",
        "EVAL_STUDENT_EMAIL=file@example.com",
        "EVAL_STUDENT_PASSWORD=file-password",
        "GOOGLE_API_KEY=file-google-key",
    )
)


@pytest.fixture
def bare_environment(monkeypatch, tmp_path):
    """A directory holding a `.env`, and a shell that exports none of it."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(DOTENV + "\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_harness_reads_dotenv(bare_environment):
    """The failure this exists to prevent: configured correctly, refused anyway."""
    config = load_config()

    assert config.base_url == "https://from-the-file.example"
    assert config.student_email == "file@example.com"


def test_an_exported_variable_beats_the_file(bare_environment, monkeypatch):
    """A shell aiming the harness somewhere else is not overruled by a file."""
    monkeypatch.setenv("EVAL_BASE_URL", "https://from-the-shell.example")

    assert load_config().base_url == "https://from-the-shell.example"


def test_a_missing_variable_is_still_refused_by_name(monkeypatch, tmp_path):
    """No `.env` and no exports: the harness must still say what is absent."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigMissing) as refusal:
        load_config()

    for name in ENV_VARS:
        assert name in str(refusal.value)


# The judge spends one variable. Grading it against a fixture of fixed text
# contacts no deployment, so demanding a deployment be *named* before that can
# run is a requirement with nothing behind it -- and the machine most likely to
# want it is the one with no deployment configured at all.

DEPLOYMENT_VARS = ("EVAL_BASE_URL", "EVAL_STUDENT_EMAIL", "EVAL_STUDENT_PASSWORD")


@pytest.fixture
def only_a_google_key(monkeypatch, tmp_path):
    """A machine with a Gemini key and no deployment, and no `.env` to hide it."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "a-google-key")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_judge_needs_only_a_google_key(only_a_google_key):
    assert load_judge_config().google_api_key == "a-google-key"


def test_the_judge_config_reads_dotenv_too(bare_environment):
    """Same file, same rule -- this is a narrower read, not a separate mechanism."""
    assert load_judge_config().google_api_key == "file-google-key"


def test_an_exported_key_beats_the_file_here_as_well(bare_environment, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "from-the-shell")

    assert load_judge_config().google_api_key == "from-the-shell"


def test_a_missing_google_key_is_refused_by_name(monkeypatch, tmp_path):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigMissing) as refusal:
        load_judge_config()

    assert "GOOGLE_API_KEY" in str(refusal.value)


@pytest.mark.parametrize("name", DEPLOYMENT_VARS)
def test_the_judge_refusal_never_names_a_variable_it_does_not_want(monkeypatch, tmp_path, name):
    """Naming a variable it will not read would send an operator to configure nothing.

    The refusal message is the whole interface of a ConfigMissing, so listing
    something spurious there is the same defect as requiring it.
    """
    for missing in ENV_VARS:
        monkeypatch.delenv(missing, raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigMissing) as refusal:
        load_judge_config()

    assert name not in str(refusal.value)


def test_the_full_config_still_wants_everything(only_a_google_key):
    """Narrowing the judge's read must not narrow the run that drives a deployment."""
    with pytest.raises(ConfigMissing):
        load_config()


def test_the_smoke_run_needs_no_google_key(monkeypatch, tmp_path):
    """There is no judge in a smoke run, so it must not demand a judge's key."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in (
        ("EVAL_BASE_URL", "https://smoke.example"),
        ("EVAL_STUDENT_EMAIL", "smoke@example.com"),
        ("EVAL_STUDENT_PASSWORD", "smoke-password"),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(tmp_path)

    assert load_smoke_config().base_url == "https://smoke.example"


def test_the_smoke_refusal_never_names_the_google_key(monkeypatch, tmp_path):
    """Naming a variable it will not read would send an operator to configure nothing."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigMissing) as refusal:
        load_smoke_config()

    assert "GOOGLE_API_KEY" not in str(refusal.value)
    assert "EVAL_BASE_URL" in str(refusal.value)


def test_the_smoke_base_url_loses_a_trailing_slash(monkeypatch, tmp_path):
    """A trailing slash would put a double slash in every URL the browser opens."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in (
        ("EVAL_BASE_URL", "https://smoke.example/"),
        ("EVAL_STUDENT_EMAIL", "smoke@example.com"),
        ("EVAL_STUDENT_PASSWORD", "smoke-password"),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(tmp_path)

    assert load_smoke_config().base_url == "https://smoke.example"
