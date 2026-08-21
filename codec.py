"""
codec.py
========

Endpoint-side payload codec.

IMPORTANT
---------
The current implementation uses hexadecimal encoding ONLY as
a visible placeholder.

Hexadecimal encoding is NOT encryption.

The architectural purpose of this module is to demonstrate
that payload transformation happens at the endpoint and
outside the Session.

The Session must never import this module.
"""


def encode(data: bytes) -> str:
    """
    Endpoint-side encoding.

    Placeholder only.
    Replace with authenticated encryption in a real
    implementation.
    """

    return data.hex()


def decode(blob: str) -> bytes:
    """
    Endpoint-side decoding.

    This function is intentionally called only by the
    client endpoint.
    """

    return bytes.fromhex(blob)
