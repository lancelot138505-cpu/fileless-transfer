"""
node.py
=======

1. TLS

   Protects the transport channel.

   Session IDs, capabilities, commands and payloads are not
   sent over a plaintext TCP channel.

2. Endpoint codec

   The payload is transformed locally before entering the
   Session.

3. Blind Session

   The Session stores only the opaque payload blob.

   It never calls encode() or decode().


IMPORTANT
---------

The automatic certificate generation below is intended only
for local development and demonstration.

It is NOT a production certificate-management system.
"""


import json
import os
import socket
import ssl
import subprocess
import sys

from session import Session
from codec import encode, decode


DEFAULT_PORT = 9000

CERT_FILE = "cert.pem"
KEY_FILE = "key.pem"


# ============================================================
# Development certificate
# ============================================================

def ensure_certificate():
    """
    Generate a local self-signed certificate if one does not
    already exist.

    This is ONLY for local experimentation.

    Production systems should use a proper certificate
    authority / certificate-management process.
    """

    if (
        os.path.exists(CERT_FILE)
        and os.path.exists(KEY_FILE)
    ):
        return

    print(
        "[tls] generating development certificate..."
    )

    command = [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-keyout",
        KEY_FILE,
        "-out",
        CERT_FILE,
        "-days",
        "365",
        "-nodes",
        "-subj",
        "/CN=fileless-transfer"
    ]

    try:

        subprocess.run(
            command,
            check=True
        )

    except FileNotFoundError:

        raise RuntimeError(
            "OpenSSL is required to generate the "
            "development certificate."
        )


# ============================================================
# JSON transport
# ============================================================

def send_message(sock, message):
    """
    Send one length-prefixed JSON message.

    TLS provides transport confidentiality and integrity.

    This function only performs message framing.
    """

    data = json.dumps(
        message
    ).encode("utf-8")

    header = len(data).to_bytes(
        4,
        "big"
    )

    sock.sendall(header)
    sock.sendall(data)


def recv_exact(sock, size):
    """
    Receive exactly `size` bytes.
    """

    data = bytearray()

    while len(data) < size:

        chunk = sock.recv(
            size - len(data)
        )

        if not chunk:
            raise ConnectionError(
                "connection closed"
            )

        data.extend(chunk)

    return bytes(data)


def recv_message(sock):
    """
    Receive one length-prefixed JSON message.
    """

    header = recv_exact(
        sock,
        4
    )

    size = int.from_bytes(
        header,
        "big"
    )

    data = recv_exact(
        sock,
        size
    )

    return json.loads(
        data.decode("utf-8")
    )


# ============================================================
# Node
# ============================================================

class Node:

    def __init__(
        self,
        host,
        port
    ):

        self.host = host
        self.port = port

        # Active ephemeral Sessions.
        self.sessions = {}

    # ========================================================
    # Server
    # ========================================================

    def listen(self):

        ensure_certificate()

        context = ssl.SSLContext(
            ssl.PROTOCOL_TLS_SERVER
        )

        context.load_cert_chain(
            CERT_FILE,
            KEY_FILE
        )

        with socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM
        ) as server:

            server.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_REUSEADDR,
                1
            )

            server.bind(
                (
                    self.host,
                    self.port
                )
            )

            server.listen(5)

            print(
                f"[node] listening on "
                f"{self.host}:{self.port}"
            )

            print(
                "[tls] TLS enabled"
            )

            while True:

                raw_conn, address = (
                    server.accept()
                )

                print(
                    f"[node] connection from "
                    f"{address}"
                )

                try:

                    with context.wrap_socket(
                        raw_conn,
                        server_side=True
                    ) as conn:

                        print(
                            "[tls] secure channel established"
                        )

                        self.handle_connection(
                            conn
                        )

                except Exception as exc:

                    print(
                        f"[error] {exc}"
                    )

                finally:

                    try:
                        raw_conn.close()
                    except Exception:
                        pass

    # ========================================================
    # Server-side connection
    # ========================================================

    def handle_connection(
        self,
        conn
    ):

        # ----------------------------------------------------
        # 1. Application handshake
        # ----------------------------------------------------

        request = recv_message(
            conn
        )

        if request.get(
            "type"
        ) != "handshake":

            send_message(
                conn,
                {
                    "type":
                        "error",
                    "message":
                        "handshake required"
                }
            )

            return

        send_message(
            conn,
            {
                "type":
                    "handshake_ok"
            }
        )

        print(
            "[node] handshake completed"
        )

        # ----------------------------------------------------
        # 2. Session request
        # ----------------------------------------------------

        request = recv_message(
            conn
        )

        if request.get(
            "type"
        ) != "session_request":

            send_message(
                conn,
                {
                    "type":
                        "error",
                    "message":
                        "session request required"
                }
            )

            return

        # ----------------------------------------------------
        # 3. Create ephemeral Session
        # ----------------------------------------------------

        session = Session()

        self.sessions[
            session.id
        ] = session

        print(
            f"[session] created: "
            f"{session.id}"
        )

        send_message(
            conn,
            {
                "type":
                    "session_created",

                "session":
                    session.id,

                "token":
                    session.token
            }
        )

        # ====================================================
        # 4. Session loop
        # ====================================================

        while session.active:

            request = recv_message(
                conn
            )

            session_id = request.get(
                "session"
            )

            token = request.get(
                "token"
            )

            command = request.get(
                "command"
            )

            # ------------------------------------------------
            # Verify Session
            # ------------------------------------------------

            if session_id != session.id:

                send_message(
                    conn,
                    {
                        "type":
                            "error",
                        "message":
                            "invalid session"
                    }
                )

                continue

            # ------------------------------------------------
            # Verify capability
            # ------------------------------------------------

            if not session.authorize(
                token
            ):

                send_message(
                    conn,
                    {
                        "type":
                            "error",
                        "message":
                            "access denied"
                    }
                )

                continue

            # =================================================
            # PUT
            # =================================================

            if command == "put":

                name = request[
                    "name"
                ]

                # IMPORTANT:
                #
                # This value is already an encoded/encrypted
                # opaque blob.
                #
                # The server does NOT decode it.
                #
                # The Session receives it unchanged.

                encoded_blob = request[
                    "payload"
                ]

                session.put(
                    name,
                    encoded_blob
                )

                print(
                    f"[session] stored "
                    f"{name} "
                    f"({len(encoded_blob)} "
                    f"encoded chars)"
                )

                send_message(
                    conn,
                    {
                        "type":
                            "ok",

                        "command":
                            "put",

                        "name":
                            name,

                        "size":
                            len(encoded_blob)
                    }
                )

            # =================================================
            # GET
            # =================================================

            elif command == "get":

                name = request[
                    "name"
                ]

                # The Session returns the exact opaque blob.

                encoded_blob = session.get(
                    name
                )

                if encoded_blob is None:

                    send_message(
                        conn,
                        {
                            "type":
                                "error",
                            "message":
                                "object not found"
                        }
                    )

                    continue

                # IMPORTANT:
                #
                # No encode().
                # No decode().
                #
                # The server simply forwards the blob.

                send_message(
                    conn,
                    {
                        "type":
                            "data",

                        "name":
                            name,

                        "payload":
                            encoded_blob
                    }
                )

            # =================================================
            # LIST
            # =================================================

            elif command == "list":

                objects = (
                    session.list_objects()
                )

                send_message(
                    conn,
                    {
                        "type":
                            "list",

                        "objects":
                            objects
                    }
                )

            # =================================================
            # CLOSE
            # =================================================

            elif command == "close":

                print(
                    f"[session] closing: "
                    f"{session.id}"
                )

                send_message(
                    conn,
                    {
                        "type":
                            "closing"
                    }
                )

                self.destroy_session(
                    session.id
                )

                break

            else:

                send_message(
                    conn,
                    {
                        "type":
                            "error",

                        "message":
                            "unknown command"
                    }
                )

    # ========================================================
    # Session destruction
    # ========================================================

    def destroy_session(
        self,
        session_id
    ):

        session = self.sessions.pop(
            session_id,
            None
        )

        if session is None:
            return

        session.destroy()

        print(
            f"[session] destroyed: "
            f"{session_id}"
        )

    # ========================================================
    # Client
    # ========================================================

    def connect(
        self,
        remote_host,
        remote_port
    ):

        # ----------------------------------------------------
        # TLS client context
        #
        # For this local prototype we disable certificate
        # verification because the server certificate is
        # self-signed.
        #
        # DO NOT copy this setting into production code.
        # ----------------------------------------------------

        context = ssl.create_default_context()

        context.check_hostname = False
        context.verify_mode = (
            ssl.CERT_NONE
        )

        with socket.create_connection(
            (
                remote_host,
                remote_port
            )
        ) as raw_sock:

            with context.wrap_socket(
                raw_sock,
                server_hostname=remote_host
            ) as sock:

                print(
                    "[tls] secure channel established"
                )

                # --------------------------------------------
                # 1. Handshake
                # --------------------------------------------

                send_message(
                    sock,
                    {
                        "type":
                            "handshake"
                    }
                )

                response = recv_message(
                    sock
                )

                if response.get(
                    "type"
                ) != "handshake_ok":

                    raise RuntimeError(
                        "handshake failed"
                    )

                # --------------------------------------------
                # 2. Request Session
                # --------------------------------------------

                send_message(
                    sock,
                    {
                        "type":
                            "session_request"
                    }
                )

                response = recv_message(
                    sock
                )

                if response.get(
                    "type"
                ) != "session_created":

                    raise RuntimeError(
                        "session creation failed"
                    )

                session_id = response[
                    "session"
                ]

                token = response[
                    "token"
                ]

                print(
                    f"[session] created: "
                    f"{session_id}"
                )

                # ============================================
                # Session lifetime
                # ============================================

                self.put(
                    sock,
                    session_id,
                    token,
                    "hello.txt",
                    b"Hello from "
                    b"fileless-transfer."
                )

                self.put(
                    sock,
                    session_id,
                    token,
                    "second.txt",
                    b"Another object."
                )

                self.list(
                    sock,
                    session_id,
                    token
                )

                self.get(
                    sock,
                    session_id,
                    token,
                    "hello.txt"
                )

                self.get(
                    sock,
                    session_id,
                    token,
                    "second.txt"
                )

                # --------------------------------------------
                # End Session
                # --------------------------------------------

                self.close(
                    sock,
                    session_id,
                    token
                )

    # ========================================================
    # Client PUT
    # ========================================================

    def put(
        self,
        sock,
        session,
        token,
        name,
        data
    ):

        # IMPORTANT:
        #
        # Encoding happens locally at the endpoint.
        #
        # The resulting blob is what enters the network
        # protocol and Session.

        encoded_blob = encode(
            data
        )

        send_message(
            sock,
            {
                "session":
                    session,

                "token":
                    token,

                "command":
                    "put",

                "name":
                    name,

                "payload":
                    encoded_blob
            }
        )

        response = recv_message(
            sock
        )

        print(
            "[put]",
            response
        )

    # ========================================================
    # Client GET
    # ========================================================

    def get(
        self,
        sock,
        session,
        token,
        name
    ):

        send_message(
            sock,
            {
                "session":
                    session,

                "token":
                    token,

                "command":
                    "get",

                "name":
                    name
            }
        )

        response = recv_message(
            sock
        )

        if response.get(
            "type"
        ) != "data":

            print(
                "[get]",
                response
            )

            return

        # IMPORTANT:
        #
        # The server returned the opaque blob unchanged.
        #
        # Decoding happens locally at the endpoint.

        encoded_blob = response[
            "payload"
        ]

        data = decode(
            encoded_blob
        )

        print(
            f"[get] {name}: "
            f"{data!r}"
        )

    # ========================================================
    # Client LIST
    # ========================================================

    def list(
        self,
        sock,
        session,
        token
    ):

        send_message(
            sock,
            {
                "session":
                    session,

                "token":
                    token,

                "command":
                    "list"
            }
        )

        response = recv_message(
            sock
        )

        print(
            "[list]",
            response
        )

    # ========================================================
    # Client CLOSE
    # ========================================================

    def close(
        self,
        sock,
        session,
        token
    ):

        send_message(
            sock,
            {
                "session":
                    session,

                "token":
                    token,

                "command":
                    "close"
            }
        )

        response = recv_message(
            sock
        )

        print(
            "[session]",
            response
        )


# ============================================================
# CLI
# ============================================================

def main():

    if len(sys.argv) < 2:

        print()
        print(
            "fileless-transfer"
        )
        print()
        print(
            "Usage:"
        )
        print()
        print(
            "  python node.py server [port]"
        )
        print(
            "  python node.py "
            "connect <host> [port]"
        )
        print()

        return

    mode = sys.argv[1]

    # --------------------------------------------------------
    # Server
    # --------------------------------------------------------

    if mode == "server":

        port = (
            int(sys.argv[2])
            if len(sys.argv) > 2
            else DEFAULT_PORT
        )

        Node(
            "0.0.0.0",
            port
        ).listen()

    # --------------------------------------------------------
    # Client
    # --------------------------------------------------------

    elif mode == "connect":

        if len(sys.argv) < 3:

            print(
                "missing host"
            )

            return

        host = sys.argv[2]

        port = (
            int(sys.argv[3])
            if len(sys.argv) > 3
            else DEFAULT_PORT
        )

        Node(
            "0.0.0.0",
            0
        ).connect(
            host,
            port
        )

    else:

        print(
            f"unknown mode: {mode}"
        )


if __name__ == "__main__":
    main()
