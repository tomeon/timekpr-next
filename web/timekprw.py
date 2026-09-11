"""
timekprw: web front end for the timekpr daemon.

Serves the REST API of docs/web-api.md and the web UI, talking to the
daemon over D-Bus like timekpra does.  It must therefore run as root or
as a member of the timekpr group.
"""
import argparse
import ipaddress
import os
import sys
# set up our python path
if "/usr/lib/python3/dist-packages" not in sys.path:
    sys.path.append("/usr/lib/python3/dist-packages")

# timekpr imports
from timekpr.common.constants import constants as cons

DEFAULT_PORT = 8463


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
    parser.add_argument("--host", default=env_default("HOST", "127.0.0.1"), help="address to listen on (default: %(default)s)")
    parser.add_argument("--port", type=int, default=int(env_default("PORT", DEFAULT_PORT)), help="port to listen on (default: %(default)s)")
    parser.add_argument("--token-file", default=env_default("TOKEN_FILE", None), help="file holding the bearer token clients must present (default: the systemd credential \"token\", then %s)" % (cons.TK_WEB_TOKEN_FILE))
    parser.add_argument("--no-auth", action="store_true", default=env_default("NO_AUTH", "") != "", help="serve without authentication (only behind a reverse proxy that authenticates, or on loopback)")
    parser.add_argument("--static-dir", default=env_default("STATIC_DIR", cons.TK_WEB_DIR), help="directory with the web UI, or \"\" to serve the API only (default: %(default)s)")
    parser.add_argument("--root-path", default=env_default("ROOT_PATH", ""), help="path prefix a reverse proxy strips before forwarding (default: none)")
    return parser.parse_args(argv)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # the launcher in bin/ is a shebang line that runs this file, so the
    # launcher's own path arrives as the first argument (as for timekpra)
    if argv and "timekprw" in os.path.basename(argv[0]) and os.path.exists(argv[0]):
        argv = argv[1:]
    args = parse_args(argv)
    token = None if args.no_auth else read_token(args.token_file)
    if token is None and not args.no_auth:
        raise SystemExit("timekprw: no token file found (looked at %s); create one or pass --no-auth" % (", ".join(token_file_candidates(args.token_file))))
    if token is None and not ipaddress.ip_address(args.host).is_loopback:
        print("timekprw: WARNING: serving without authentication on a non-loopback address", file=sys.stderr)

    # the web stack is imported late so that --help works without it
    import uvicorn
    from timekpr.web.app import create_app
    from timekpr.web.bridge import Bridge

    app = create_app(Bridge(), static_dir=args.static_dir or None, token=token)
    uvicorn.run(app, host=args.host, port=args.port, root_path=args.root_path, log_level="info")


# main start
if __name__ == "__main__":
    main()
