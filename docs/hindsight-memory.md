# Hindsight memory: making it work on a fresh machine

`memory.provider: hindsight` is a plugin client plus a **per-profile embedded daemon**.
Two things about that daemon are not obvious from Hermes' side, and both bite on a
machine that has never run it. Both are handled by this starter now; this file is why.

## 1. The server has to be fetched once (~2.8GB) and the plugin cannot do it

`local_embedded` runs `hindsight-api` as its own process in its own uv tool
environment — deliberately **not** a Hermes venv dependency (its tree cannot resolve
against Hermes' pins, and it is ~3GB of CUDA-wheel torch for the LLM stack). On a cold
machine the plugin fetches it itself via `uvx hindsight-api@<hindsight_embed.__version__>`.

That fetch does not fit the plugin's start budget on a slow link:

| | |
|---|---|
| plugin's start budget (`_DAEMON_START_TIMEOUT`, `plugins/hindsight/embedded.py`) | 900 s |
| payload (torch 528.9 + cudnn 527.5 + cublas 403.5 + cufft 204.2 + triton 236.5 + nccl 206.0 + cusolver 191.6 + cusparse 139.2 + cusparselt 162.3) | ≈ 2.8 GB |
| link measured here | 2.7 MB/s → **≈ 18 min** |

Every attempt therefore dies ~15% short, and **a killed uv download commits nothing**:
the partial bytes strand in `<cache>/.tmp*` and the tool env stays a venv skeleton, so
the next attempt starts from zero. Signature: `Failed to start the Hindsight daemon for
profile '...'` repeating every few minutes forever, orphaned `uvx hindsight-api`
processes, nothing listening on the daemon port, and a cache full of dead `.tmp*` dirs.

**The fix is `scripts/prime-hindsight-server.sh`**, run once per machine by `up.sh`:
the same install, with no window over it, holding the uv tools lock so the plugin's own
attempts queue instead of racing. It is idempotent (no-op once installed) and takes no
version argument — the version comes from the installed client, exactly as the plugin
derives its own spec, so nothing here goes stale when the plugin updates.

Expect the daemon to report `Failed to start the Hindsight daemon` on a cold boot until
the prime finishes - that is the plugin losing its own race, not a new fault, and it
stops for good once the tool env exists. Nothing is lost while it does: retain during
that window is a no-op and the messages are still in the session.

The tool environment lands on the **data volume** (`/opt/data/.local/share/uv/tools`)
because the service user (uid 1000) has `HOME=/opt/data` (`getent passwd hermes`), so
the primed server survives a container recreate. `/opt/hermes` (app + venv) is the
ephemeral overlay; its `site-packages` is owned by uid 10000 mode 755 while the service
runs as uid 1000, so nothing can be installed into the venv at runtime at all.

## 2. The daemon needs its LLM base URL, or it 401s against the wrong host

The plugin collapses provider `openrouter` onto the daemon's `openai` wire
(`settings.py::_OPENAI_WIRE_PROVIDERS`) and then relies on `HINDSIGHT_API_LLM_BASE_URL`.
Unset, the daemon sends the OpenRouter key to `api.openai.com`:

```
ValueError: Fact extraction failed ... AuthenticationError: 401 - Incorrect API key
provided: sk-or-v1...  You can find your API key at https://platform.openai.com/account/api-keys
```

while `/health` cheerfully reports `{"status":"healthy","database":"connected"}`. The
plugin's own setup wizard writes the value
(`plugins/hindsight/setup.py` → `https://openrouter.ai/api/v1`); when that branch never
ran, the config is simply left without it.

`scripts/ensure-hindsight-config.py` pins it, and runs from `apply-overlay.sh` so it
covers every profile including the default one — including profiles created later.
It only writes when the provider is `openrouter` and no base URL is set.

Two traps make a hand fix evaporate, which is why this is a reconciler and not a note:

* a plugin instance reads `config.json` **once**, in `initialize()` — editing the file
  does not reach an already-running session, and a fresh process must start the daemon
  before the change is visible;
* the daemon's start path reconciles the profile env file against the config it loaded
  at startup, so a line added by hand can be **overwritten** by the stale build.

## Shape of the profiles

`overlay/profiles/<name>/` is force-copied over the live profile on every reconcile,
and `apply-overlay.sh` is the container healthcheck — so anything declared there is
pinned. A declared `hindsight/config.json` that lacks `llm_base_url` would therefore
*revert* the fix on the next healthcheck; the base URL belongs in the overlay copy too.

The daemon is keyed by the config's `profile` (default `hermes`), not by the Hermes
profile name, so one daemon serves every Hermes profile and its env file is shared —
while `bank_id` is what actually isolates the data. Two profiles sharing one `bank_id`
write into one bank.

## Verifying, without trusting a banner

```bash
# 1. is the fetch done?
docker compose exec -T -u hermes hermes ls -l /opt/data/.local/share/uv/tools/hindsight-api/bin/hindsight-api

# 2. did the daemon get the base URL? (the live env, not the config)
for p in $(pgrep -f 'hindsight-ap[i]'); do tr '\0' '\n' < /proc/$p/environ \
    | grep -E '^HINDSIGHT_API_LLM_(BASE_URL|MODEL|PROVIDER)='; done

# 3. proof, not a banner: retain a distinctive fact, then recall it from a NEW process
hermes chat -q "Reply with exactly: ok" --oneshot     # note: -q/--query, not -Q
```

`/health` returning `healthy` proves only that the process is up — it says nothing about
whether the LLM behind it is reachable or correctly addressed, which is exactly the
failure mode above.
