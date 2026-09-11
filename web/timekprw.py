"""
timekprw: web front end for the timekpr daemon.

Serves the REST API of docs/web-api.md and the web UI, talking to the
daemon over D-Bus like timekpra does.  It must therefore run as root or
as a member of the timekpr group.

Listening: every listener ends up as a bound socket handed to the web
server, whether timekprw opened it itself (TCP or UNIX domain), inherited
it as a file descriptor, or received it from systemd socket activation.
"""
import argparse
import grp
import ipaddress
import os
import socket
import stat
import sys
from urllib.parse import urlsplit
# set up our python path
if "/usr/lib/python3/dist-packages" not in sys.path:
    sys.path.append("/usr/lib/python3/dist-packages")

# timekpr imports
from timekpr.common.constants import constants as cons

DEFAULT_LISTEN = "127.0.0.1:8463"
# group whose members may connect to UNIX sockets timekprw creates (the
# same group the daemon's D-Bus policy admits)
SOCKET_GROUP = "timekpr"
# the first file descriptor systemd passes (sd_listen_fds(3))
SD_LISTEN_FDS_START = 3


# ## listeners ##

def listen_tcp(spec):
    """Bind HOST:PORT or [IPV6]:PORT"""
    parts = urlsplit("//" + spec)
    if parts.hostname is None or parts.port is None:
        raise ValueError("%s is not HOST:PORT" % (spec))
    family, kind, proto, _canonname, sockaddr = socket.getaddrinfo(parts.hostname, parts.port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)[0]
    sock = socket.socket(family, kind, proto)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(sockaddr)
    sock.listen()
    return sock


def listen_unix(path):
    """Bind a UNIX domain socket, mode 0660, group timekpr when possible"""
    try:
        if stat.S_ISSOCK(os.stat(path).st_mode):
            os.unlink(path)
    except FileNotFoundError:
        pass
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    umask = os.umask(0o117)
    try:
        sock.bind(path)
    finally:
        os.umask(umask)
    try:
        os.chown(path, -1, grp.getgrnam(SOCKET_GROUP).gr_gid)
    except (KeyError, PermissionError):
        pass
    sock.listen()
    return sock


def inherit_fd(fd):
    """A socket somebody else bound and passed to us as a file descriptor"""
    return socket.socket(fileno=fd)


def systemd_sockets():
    """The sockets passed by systemd socket activation, if any (sd_listen_fds(3))"""
    if os.environ.get("LISTEN_PID") != str(os.getpid()):
        return []
    count = int(os.environ.get("LISTEN_FDS", "0"))
    for name in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        os.environ.pop(name, None)
    return [inherit_fd(SD_LISTEN_FDS_START + idx) for idx in range(count)]


def open_listener(spec):
    """HOST:PORT, unix:PATH or fd:N -> bound socket"""
    if spec.startswith("unix:"):
        return listen_unix(spec[len("unix:"):])
    if spec.startswith("fd:"):
        return inherit_fd(int(spec[len("fd:"):]))
    return listen_tcp(spec)


def describe(sock):
    name = sock.getsockname()
    return "unix:%s" % (name) if sock.family == socket.AF_UNIX else "%s:%s" % (name[0], name[1])


# ## configuration ##

def env_default(name, fallback):
    return os.environ.get("TIMEKPRW_" + name, fallback)


def token_file_candidates(explicit):
    """Where to look for the bearer token: the explicit path, a systemd
    credential named "token", then the default location"""
    if explicit is not None:
        return [explicit]
    candidates = []
    if "CREDENTIALS_DIRECTORY" in os.environ:
        candidates.append(os.path.join(os.environ["CREDENTIALS_DIRECTORY"], "token"))
    candidates.append(cons.TK_WEB_TOKEN_FILE)
    return candidates


def read_token(explicit):
    for path in token_file_candidates(explicit):
        if os.path.exists(path):
            with open(path, "r") as tokenFile:
                token = tokenFile.read().strip()
            if token == "":
                raise SystemExit("timekprw: token file %s is empty" % (path))
            return token
    return None


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="timekprw", description="web front end for timekpr (every option can also be given as an environment variable TIMEKPRW_<OPTION>)")
    parser.add_argument("--listen", action="append", metavar="SPEC", default=env_default("LISTEN", "").split() or None,
                        help="where to listen: HOST:PORT, [IPV6]:PORT, unix:PATH or fd:N; repeatable (default: %s unless systemd passes sockets)" % (DEFAULT_LISTEN))
    parser.add_argument("--token-file", default=env_default("TOKEN_FILE", None), help="file holding the bearer token clients on TCP must present (default: the systemd credential \"token\", then %s)" % (cons.TK_WEB_TOKEN_FILE))
    parser.add_argument("--no-auth", action="store_true", default=env_default("NO_AUTH", "") != "", help="serve TCP without authentication (only behind a reverse proxy that authenticates, or on loopback)")
    parser.add_argument("--static-dir", default=env_default("STATIC_DIR", cons.TK_WEB_DIR), help="directory with the web UI, or \"\" to serve the API only (default: %(default)s)")
    parser.add_argument("--root-path", default=env_default("ROOT_PATH", ""), help="path prefix a reverse proxy strips before forwarding (default: none)")
    return parser.parse_args(argv)


def serve(app, sockets, root_path=""):
    """Run the web server on already bound sockets (all listeners end up here)"""
    import uvicorn
    for sock in sockets:
        print("timekprw: listening on %s" % (describe(sock)), file=sys.stderr)
    config = uvicorn.Config(app, root_path=root_path, log_level="info")
    uvicorn.Server(config).run(sockets=sockets)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # the launcher in bin/ is a shebang line that runs this file, so the
    # launcher's own path arrives as the first argument (as for timekpra)
    if argv and "timekprw" in os.path.basename(argv[0]) and os.path.exists(argv[0]):
        argv = argv[1:]
    args = parse_args(argv)

    sockets = systemd_sockets()
    sockets += [open_listener(spec) for spec in (args.listen or ([] if sockets else [DEFAULT_LISTEN]))]

    # UNIX sockets are protected by their file permissions; TCP needs the token
    tcp = [sock for sock in sockets if sock.family != socket.AF_UNIX]
    token = None if args.no_auth else read_token(args.token_file)
    if tcp and token is None and not args.no_auth:
        raise SystemExit("timekprw: no token file found (looked at %s); create one, pass --no-auth, or listen on UNIX sockets only" % (", ".join(token_file_candidates(args.token_file))))
    if tcp and token is None and any(not ipaddress.ip_address(sock.getsockname()[0]).is_loopback for sock in tcp):
        print("timekprw: WARNING: serving without authentication on a non-loopback address", file=sys.stderr)

    # the web stack is imported late so that --help works without it
    from timekpr.web.app import create_app
    from timekpr.web.bridge import Bridge

    serve(create_app(Bridge(), static_dir=args.static_dir or None, token=token), sockets, args.root_path)


# main start
if __name__ == "__main__":
    main()
