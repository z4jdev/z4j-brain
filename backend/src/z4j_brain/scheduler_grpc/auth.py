"""mTLS + scheduler enrollment.

Brain mints client certificates for each enrolled scheduler
instance and validates incoming gRPC connections against the trust
store + the recognized cert fingerprints in the new ``schedulers``
table.

Phase 1 surface:

- ``mint_scheduler_cert(name: str) -> tuple[cert_pem, key_pem]``
  CLI-callable; brain operator runs ``z4j-brain mint-scheduler-cert
  --name scheduler-1`` to provision a new instance
- ``revoke_scheduler_cert(fingerprint: str) -> None``
- ``ServerInterceptor`` for grpc.aio that validates the client
  cert against the schedulers table on every RPC

Optional: cert-manager + Helm integration for k8s deployments
where cert minting is automated. Brain's mint helper still works
as a manual fallback.

Phase 1 implementation lands here.
"""

from __future__ import annotations

# Phase 1 implementation: cert minting helper + ServerInterceptor.
