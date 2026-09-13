# Blackcoin Unraid GUI

The public `qqblackcoin/blackcoin-v4-gui:latest` image now packages verified stable
**Blackcoin v30.1.5.1**. Publication completed September 13, 2026. Core source:
`bd5dad9aef27aff1f1bff37d6e5464b8305cd3ca`. Tested GUI wrapper:
`191280022ceb4692518aa7f0411553ab1085a4c7`.

These three public tags resolve to the same verified manifest digest:

- `qqblackcoin/blackcoin-v4-gui:latest`
- `qqblackcoin/blackcoin-v4-gui:30.1.5.1`
- `qqblackcoin/blackcoin-v4-gui:30.1.5.1-bd5dad9aef27-191280022ceb`

Digest: `sha256:05e219908d50f8b7317e91a0419feac7e40f60b2f02b4fb7d3f0f3c84e9defdd`.
This replaces the historical July 6 image behind `latest`. The latest tag is
mutable; pin the full digest or unique source/version/wrapper tag for these bytes.

The [publication record](verification/30.1.5.1-191280022ceb.json) binds the image
to [released Core binaries](https://github.com/Blackcoin-Dev/Blackcoin/releases/tag/v30.1.5.1)
and [tested wrapper source](https://github.com/Blackcoin-Dev/unraid-templates/tree/191280022ceb4692518aa7f0411553ab1085a4c7).
All 13 isolated native amd64 GUI checks passed: actual version/source, noVNC and
GUI process health, unprivileged execution, fresh wallet-free data, keyless
regtest blocks, clean shutdown/restart, persistence and fixture cleanup.
All 44 offline tests passed in [GitHub CI](https://github.com/Blackcoin-Dev/unraid-templates/actions/runs/34790946506).
This does not claim existing-wallet migration, mainnet synchronization,
independent audit, or Unraid Community Applications certification.

## Install or update safely

- Linux x86-64 only. Runtime UID/GID remains **1000:1000**.
- Preserve `/home/blackcoin/.blackcoin` and its host mapping. Back up wallets/data
  before upgrading; never run two clients against the same datadir.
- Browser GUI remains container port **8080**, path **`/vnc.html`**, default host
  port **9595**. P2P remains **15714/tcp**. No RPC port is published.
- **The browser GUI has no authentication or TLS. Anyone reaching it can control
  the wallet interface.** Use a trusted private LAN or authenticated VPN/proxy.
  Never expose its port directly to the Internet.
- Set `--stop-timeout 150` in Extra Parameters, including existing containers,
  to allow clean shutdown. The updated template includes this setting.
- The launcher does not change ownership, create/unlock a wallet, or automatically
  enable staking/claim mining. Those startup activities are disabled.

In Unraid, check for image updates and update/recreate the stopped container
with its existing data mapping. Publication does not automatically update or
restart installed containers. Do not assume older releases can open newer data.
See the [setup/upgrade guide](https://projectblackcoin.org/upgrade) and
[verified support directory](https://projectblackcoin.org/community).

## Stable-release reconciliation

The publisher verifies official stable releases, source identities and binary
checksums. It excludes drafts, prereleases and unreleased Core master. It builds
only missing candidates and reuses completed image/test results after a push
failure. Version downgrades and conflicting immutable tags are refused.

The separate native 15-minute scheduler uses one full-operation lock. No-change
runs perform no build, push or node restart. Wrapper source is pinned and needs
an explicit reviewed update; new Git source is not automatically executed.
See [PUBLISHING.md](PUBLISHING.md) for the command, boundaries and receipts.

## Release and protocol boundaries

Blackcoin-Dev is independently maintained; this is not a CoinBlack release or
an endorsement by historical maintainers. Source/tag signing is not an
independent audit or platform publisher signature. Read the release's exact
signing and verification notices before running it.

Base blocks use Proof of Stake. Gold Rush uses transaction-carried Argon2id claim
work, not PoW base blocks. No reward, completion time, value, exchange listing,
liquidity or third-party compatibility is guaranteed.
