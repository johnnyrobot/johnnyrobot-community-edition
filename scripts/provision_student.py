"""
Provision a Student.

There is no self-registration: PocketBase keeps its users create rule locked
and a Deployment Operator creates every account (the reset-only demo profile). This script is that
act. Run it from the host with the Compose stack up:

    python -m scripts.provision_student --email alice@example.edu --password '...'
"""
import argparse
import asyncio
import sys

from api.config import get_settings
from api.database.pocketbase_client import PocketBaseClient


async def provision(email: str, password: str, name: str | None) -> int:
    settings = get_settings()
    client = PocketBaseClient(
        base_url=settings.pocketbase_url,
        superuser_email=settings.pocketbase_superuser_email,
        superuser_password=settings.pocketbase_superuser_password,
        timeout=settings.pocketbase_timeout_seconds,
    )
    try:
        response = await client.request(
            "POST",
            "/api/collections/users/records",
            json={
                "email": email,
                "password": password,
                "passwordConfirm": password,
                "name": name or email.split("@")[0],
                "emailVisibility": False,
                "verified": True,  # no SMTP is configured; the reset-only demo profile
            },
        )
        if response.status_code != 200:
            print(f"Could not provision {email}: {response.status_code} {response.text}")
            return 1
        print(f"Provisioned {email} as {response.json()['id']}")
        return 0
    finally:
        await client.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision a Student")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--name")
    args = parser.parse_args()
    return asyncio.run(provision(args.email, args.password, args.name))


if __name__ == "__main__":
    sys.exit(main())
