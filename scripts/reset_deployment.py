"""
Reset this ResetOnly deployment.

Resetting means clearing PocketBase state and purging the Gemini File Search
store together. External providers do not reset when PocketBase does, so a
reset that omits the provider store leaves files whose owner metadata matches
no living Student permanently resident in the one place Student Library
isolation is enforced (the reset-only demo profile).

Earlier state is never restored. That is the declaration, not a limitation to
work around.

    python -m scripts.reset_deployment --confirm
"""
import argparse
import json
import os
import subprocess
import sys

from api.config import get_settings

COMPOSE = ["docker", "compose", "-f", "docker-compose.prod.yml"]
# The deployed stack deliberately publishes no PocketBase host port
# by design. `docker-compose.contract.yml` republishes it to 127.0.0.1 only;
# `reprovision_operator_accounts()` below adds it only while provisioning.
COMPOSE_WITH_CONTRACT = [*COMPOSE, "-f", "docker-compose.contract.yml"]


def purge_provider_store() -> None:
    """Delete every file in the File Search store, then the store itself."""
    from google import genai

    settings = get_settings()
    if not settings.google_api_key:
        raise RuntimeError("GOOGLE_API_KEY is unset; the provider store cannot be purged")

    client = genai.Client(api_key=settings.google_api_key)

    deleted = 0
    for file in client.files.list():
        client.files.delete(name=file.name)
        deleted += 1
    print(f"Deleted {deleted} provider files")

    for store in client.file_search_stores.list():
        client.file_search_stores.delete(name=store.name, config={"force": True})
        print(f"Deleted File Search store {store.display_name}")


def clear_pocketbase_state() -> None:
    """Drop PocketBase's data volume, then bring PocketBase back up empty.

    The volume name is read out of Compose's own resolved config, never
    spelled out here and never reconstructed from a naming convention.
    Compose builds it as `<project>_pb_data` today, and the project name
    comes from the checkout directory's basename (or `COMPOSE_PROJECT_NAME`),
    so a hardcoded name is wrong for any checkout not named after the
    repository -- a worktree, a rename, a second clone. Asking Compose what
    it actually calls the volume survives an explicit `name:` on the volume
    and any future change to that convention as well.

    Getting this wrong is silent and badly ordered: `main()` purges the
    provider store *first*, so a volume removal that matches nothing leaves
    every Course Material pointing at a provider file that is genuinely gone,
    under a script that still prints "Reset complete."

    Hence no `-f` and `check=True`. `--force` tells the daemon to return
    success for a volume that does not exist, which is exactly the silence
    this function must not produce. This script resets an existing,
    provisioned deployment; an absent `pb_data` means something is already
    wrong -- wrong project, a deployment that never ran -- and the operator
    needs to see it. `check=True` also stops the reset when the volume is
    still held by a running container.
    """
    subprocess.run([*COMPOSE, "down"], check=True)

    resolved = subprocess.run(
        [*COMPOSE, "config", "--format", "json"],
        capture_output=True, text=True, check=True,
    )
    volume_name = json.loads(resolved.stdout)["volumes"]["pb_data"]["name"]
    subprocess.run(["docker", "volume", "rm", volume_name], check=True)

    subprocess.run([*COMPOSE, "up", "-d", "pocketbase"], check=True)


def reprovision_operator_accounts() -> None:
    """Re-create the operator-held demo accounts.

    Edit this list to match the accounts your deployment hands out. It is
    deliberately explicit: a reset that quietly restores nothing leaves the
    deployment looking broken rather than reset.

    `provision_student` reaches PocketBase over HTTP from the host, but the
    deployed stack publishes no PocketBase port, so this briefly republishes it
    to 127.0.0.1 only via `docker-compose.contract.yml`, for exactly as
    long as provisioning every account takes. The `finally` block always
    reconciles PocketBase back to the unpublished base config, even if a
    provisioning call raises partway through the loop. Published-vs-not is a
    container-creation-time property in Docker, so both the publish and the
    reconcile genuinely recreate the container; `pb_data` is a named volume
    and survives recreation either way.
    """
    accounts = [
        ("demo@example.com", "REPLACE_WITH_THE_DEMO_PASSWORD", "Demo Student"),
    ]

    try:
        subprocess.run([*COMPOSE_WITH_CONTRACT, "up", "-d", "pocketbase"], check=True)
        env = {**os.environ, "POCKETBASE_URL": "http://127.0.0.1:8090"}
        for email, password, name in accounts:
            subprocess.run(
                [sys.executable, "-m", "scripts.provision_student",
                 "--email", email, "--password", password, "--name", name],
                check=True,
                env=env,
            )
    finally:
        # Reconcile even if the publish itself only partly succeeded, or a
        # provisioning call raised partway through the loop above. If this
        # reconcile itself raises, the loopback publish may still be live;
        # re-run `docker compose -f docker-compose.prod.yml up -d pocketbase`
        # by hand to remove it.
        subprocess.run([*COMPOSE, "up", "-d", "pocketbase"], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset this ResetOnly deployment")
    parser.add_argument("--confirm", action="store_true", help="required; this destroys all state")
    args = parser.parse_args()

    settings = get_settings()
    if settings.deployment_profile != "ResetOnly":
        print(
            f"Refusing to run: DEPLOYMENT_PROFILE is {settings.deployment_profile!r}, "
            "not 'ResetOnly'. This script destroys all state (the reset-only demo profile)."
        )
        return 1

    if not args.confirm:
        print("Refusing to run without --confirm. This destroys all state.")
        return 1

    print("Purging the Gemini File Search store...")
    purge_provider_store()

    print("Clearing PocketBase state...")
    clear_pocketbase_state()

    print("Re-provisioning operator accounts...")
    # The rest of the stack comes back up whatever happens to provisioning. A
    # single account failing (a duplicate email, say) must not leave a
    # deployment dark with only PocketBase running, at the exact moment its
    # provider store and its records have both just been destroyed. The
    # original exception still propagates after this `finally` runs, so
    # "Reset complete." is printed only on a genuine success.
    try:
        reprovision_operator_accounts()
    finally:
        subprocess.run([*COMPOSE, "up", "-d"], check=True)

    print("Reset complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
