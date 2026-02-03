from __future__ import annotations

from govgraph.webhooks import sign_body


def test_sign_body_is_stable() -> None:
    sig1 = sign_body("secret", b'{"a":1}')
    sig2 = sign_body("secret", b'{"a":1}')
    assert sig1 == sig2
    assert sig1.startswith("sha256=")

