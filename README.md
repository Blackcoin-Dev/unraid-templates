# Blackcoin Unraid template — historical image warning

## Current release and this template are different artifacts

**September 12, 2026:** the current immutable Blackcoin-Dev Core release is
[v30.1.5.1](https://github.com/Blackcoin-Dev/Blackcoin/releases/tag/v30.1.5.1),
at source commit `bd5dad9aef27aff1f1bff37d6e5464b8305cd3ca`.

This repository's template still references
`qqblackcoin/blackcoin-v4-gui:latest`. Docker Hub reported that tag was last pushed
on **July 6, 2026**, with digest
`sha256:647bf0310aeac2b8acbf572174914b3120b15211e53287496a28fa235f0928b8`.
It is **not a verified v30.1.5.1 distribution path**. A tag named `latest` is not
proof that it contains the latest Core release. The reported digest is a dated
observation, not a recommendation to install or downgrade to that image.

Do not use the historical template as the current-release installation guide.
Use the [published release and verification records](https://github.com/Blackcoin-Dev/Blackcoin/releases/tag/v30.1.5.1)
and the [current setup/upgrade guide](https://projectblackcoin.org/upgrade).
For project support, use the [verified community directory](https://projectblackcoin.org/community).

This notice does not change the template's image reference, ports, volume paths,
runtime privileges or a running installation. It does not establish which binary
is installed inside a particular user's container. Existing operators should
identify their actual version and make verified wallet/data backups before
planning an upgrade. Never run two clients against the same data directory.

## Replacement-image acceptance gate

A future current-release template should not be published until its exact image
has been built and tested. Required evidence includes:

- immutable Core release and source identity, archive checksums and provenance;
- an immutable image digest and reviewed wrapper source;
- verified binary version and native-architecture runtime tests;
- isolated first start, synchronization, clean shutdown and normal restart;
- persistent-data, backup/restore and upgrade behavior;
- private authenticated RPC and documented protection for the browser UI;
- explicit staking/claim-signing authority, without automatic wallet unlocking;
- documented support contact and rollback limits.

No replacement image, new Unraid Community Applications acceptance, or
compatibility certification is claimed here. A historical template or Docker
pull is not evidence of an independently operating current-release node.

## Release and protocol boundaries

Blackcoin-Dev is independently maintained; this is not a CoinBlack release or an
endorsement by historical maintainers. Source/tag signing is not an independent
audit or a platform publisher signature. Read the exact release's signing and
verification notices before running it.

Base blocks use Proof of Stake. Gold Rush uses transaction-carried Argon2id claim
work, not PoW base blocks. No reward, completion time, value, exchange listing,
liquidity or third-party compatibility is guaranteed.
