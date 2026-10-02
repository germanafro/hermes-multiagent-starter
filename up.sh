#!/bin/bash

export HERMES_UID="$(id -u)"
export HERMES_GID="$(id -g)"
docker compose up -d --build
