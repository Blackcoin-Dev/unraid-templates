# Public GUI image publisher

This wrapper preserves the legacy Unraid paths and ports. It packages only the
six executables from the newest published stable Blackcoin-Dev GitHub release,
after checking the signed tag/commit, source marker, API SHA-256 digests and
release checksum manifest. It never builds unreleased Core master.

The Ubuntu 22.04 amd64 base is pinned by manifest digest in `Dockerfile`.
Distribution packages are installed from Ubuntu repositories at image build
time; their inventory is included at `/usr/share/doc/blackcoin-gui/`. This is
not a claim that separate image builds are byte-for-byte reproducible.

## Runtime compatibility and security

- Architecture: Linux x86-64 only; runtime UID/GID 1000.
- Data: `/home/blackcoin/.blackcoin`, unchanged. The launcher never recursively
  changes ownership. Existing host data must be writable by UID/GID 1000.
- Browser GUI: port 8080, `/vnc.html`; P2P: TCP 15714. No published RPC port.
- Stop timeout: **150 seconds**, allowing Core to flush data before GUI cleanup.
- Staking, staking autostart and claim mining are disabled at startup. No wallet
  is created, unlocked, funded or recovered by the launcher or publisher.
- The legacy browser GUI has **no authentication or TLS**. Anyone who can reach
  it can control the wallet interface. Keep it on a trusted private LAN or behind
  an authenticated VPN/proxy. Never expose port 8080/9595 to the public Internet.
  RPC being disabled does not secure the GUI.
- `BLACKCOIN_NETWORK=regtest` and `BLACKCOIN_TEST_RPC=1` are isolated-test options,
  not production configuration. Test RPC is loopback-only and cookie-authenticated.

Keep verified wallet backups. Stop the old client before upgrading, never mount
one datadir in two running clients, and do not assume newer data can be safely
opened by an older release. Image publication does not redeploy existing nodes.

## One publisher, fixed reviewed source

The native publisher installation is
`/mnt/user/appdata/blackcoin-public-publisher/source`, checked out at a reviewed,
signed public source commit. It does not pull or execute new wrapper source
automatically. An operator upgrades that source explicitly.

Run the same command for initial publication and recurring reconciliation:

```sh
/bin/bash /mnt/user/appdata/blackcoin-public-publisher/source/tools/reconcile-unraid.sh
```

The host launcher holds `flock` for the whole operation, with a 45-minute bound.
It uses a digest-pinned Python helper and the host Docker CLI/buildx. It mounts
only the public publisher installation, Docker socket, and existing Docker
configuration in place (configuration read-only). Docker socket access gives
the helper Docker-administrator authority; this is publisher infrastructure,
not the public GUI container. No wallet/fleet datadir is mounted.

Two final logs are retained (128 KiB each); the active helper's Docker log is
capped at two 2 MiB files. Failures produce a bounded syslog notice. Inspect an
active helper using the `org.blackcoin.public-publisher=true` Docker label and
`docker logs --tail 80` on its exact container ID. Cleanup uses a unique per-run
CID file, never adopts or stops another same-name container.

The coordinator installs the separate native 15-minute cron entry; GitHub CI
does not duplicate that scheduler. Standard public CI runs deterministic unit
tests. Native release qualification is run once per exact image ID, not once per
timer tick. No-change reconciliation performs no build, push or fleet restart.

## Build and promotion contract

`tools/reconcile.py` resolves the release, checks existing published identity,
reuses an exact candidate when available, and builds only a missing candidate.
It caches verified binaries and retains a successful GUI receipt for retrying
publication without repeating qualification. Tests use an internal Docker
network, a fresh disposable volume and no host ports. They check exact Core
version/source, required GUI processes, noVNC, no wallet creation, keyless
regtest blocks, clean stop/restart and persistence. They do not claim mainnet
synchronization, existing-wallet upgrade or third-party platform certification.

Promotion publishes a unique `<version>-<Core source12>-<wrapper source12>` tag
and a version tag, then rechecks the current stable release immediately before
advancing `latest`. Unique tags are never overwritten. A version tag already
containing the same verified Core release is retained across wrapper updates.
Registry manifest digest and actual binary identity are checked. The only
unlabelled bootstrap exception is the explicitly pinned historical public image
digest; automatic version decreases are refused.

Publication receipts are retained under `state/builds/` and the last successful
receipt is `state/last-publication.json`. These files contain provenance and
digests, not credentials. An unsuccessful run does not authorize advancing latest.
