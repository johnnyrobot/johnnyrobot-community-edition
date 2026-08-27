#!/bin/sh
# Create the superuser before serving.
#
# PocketBase has no way to create the first superuser from the environment:
# `serve` on a fresh pb_data leaves no superuser at all and prints a browser
# install URL instead. FastAPI's data client then fails superuser auth with a
# 400, every record operation becomes a 503, and scripts/provision_student.py
# cannot bootstrap out of it because it authenticates through the same client.
# Without this bootstrap step, the first Student could never be created.
#
# `upsert` is idempotent: restarts are safe, and changing the environment
# variable rotates the password on the next start.
set -eu

# Compose's ${VAR:?} interpolation already refuses to start when either
# variable is absent *or* empty, which is what acceptance criterion 13 asks
# for, so under docker compose these two lines never fire. They cover the image
# run directly with `docker run`, where no interpolation stands in the way and
# PocketBase would otherwise be asked to upsert an empty identity.
: "${POCKETBASE_SUPERUSER_EMAIL:?is empty; refusing to start without a superuser credential}"
: "${POCKETBASE_SUPERUSER_PASSWORD:?is empty; refusing to start without a superuser credential}"

# --automigrate defaults on, so this also applies the mounted pb_migrations to
# a fresh volume. Both directories are named explicitly to match the serve
# command in CMD rather than relying on the build's defaults.
/pb/pocketbase superuser upsert \
	"$POCKETBASE_SUPERUSER_EMAIL" \
	"$POCKETBASE_SUPERUSER_PASSWORD" \
	--dir=/pb/pb_data \
	--migrationsDir=/pb/pb_migrations

# exec so PocketBase stays PID 1 and keeps receiving Docker's signals.
exec "$@"
