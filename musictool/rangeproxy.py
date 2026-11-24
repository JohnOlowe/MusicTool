"""Fast `get --range`: a small local proxy that ffmpeg reads the video through.

A part of a video (--download-sections) is always fetched by ffmpeg, never by
yt-dlp's own downloader, and ffmpeg asks for it in ONE open-ended request --
which YouTube slows to about 2x real time (measured: 1:00 of 1080p in 30 s).
yt-dlp's own downloader, for a whole video, asks in 10 MB pieces and gets
17-40x. ffmpeg 8.1 can ask in pieces too (-request_size), but most ffmpegs
out there (Termux's, a Windows build from last year) are older.

So nothing about the download changes except where ffmpeg reads from. The
format links in yt-dlp's info are pointed at this proxy (127.0.0.1, a free
port), and yt-dlp is given that info (--load-info-json) instead of the video
link. When ffmpeg asks the proxy for "everything from byte N on", the proxy
asks YouTube for 10 MB pieces, two at a time, and hands them to ffmpeg in
order. ffmpeg still seeks, cuts and writes the file exactly as before (-c
copy, fragmented, so whatever came down plays), so every guarantee of the
part download holds as it was: carrying on after a drop, the 4 GB limit of
a FAT32 card, a full disk, fresh links after a 403, Stop and Force stop.

Kind to a limited connection:
  * each connection starts with a 1 MB piece and only grows (up to two 10
    MB pieces ahead) while ffmpeg actually reads -- ffmpeg looks at the
    start of the file and jumps around a little before it settles, and a
    jump costs at most about a megabyte;
  * the last ~48 MB fetched are kept in memory, so a jump back, or ffmpeg
    reconnecting after a drop, does not fetch the same bytes again;
  * what is fetched beyond the end of the part (ffmpeg stops reading, the
    pieces in flight are thrown away) is at most about 20 MB.

A drop: each piece is retried after 1, 2, 4, 8, 15 and 30 s (about a
minute), while ffmpeg simply waits on its connection -- the part goes on in
the same file, where it was. After that, or a refusal (403: the links ran
out), ffmpeg's stream is cut. ffmpeg takes a cut stream for the end of it and
says it is done, so the proxy remembers it (`broken`): cli._download_part
then measures how far the picture really got, names the file after that, and
carries on from there with fresh links, as it did after any drop.

Only YouTube's links are rewritten (HOSTS): its media links need no cookies,
so the proxy does not need yours.
"""
import collections
import http.client
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import cancel

PIECE = 10 * 1024 * 1024        # like yt-dlp's own downloader (download.CHUNK)
BLOCK = 1024 * 1024             # the first piece of a connection; cache unit
AHEAD = 2                       # pieces in flight per connection
CACHE = 48 * 1024 * 1024        # recently fetched bytes kept for a re-read
SLEEPS = (1, 2, 4, 8, 15, 30)   # a piece is retried after these (1 min)
HOSTS = ("googlevideo.com", "youtube.com")


class Refused(Exception):
    """The server said no (403, 404, 416 ...): not worth retrying here."""

    def __init__(self, code, reason=""):
        super().__init__(f"HTTP Error {code}: {reason}".strip())
        self.code, self.reason = code, reason or "Refused"


class Down(Exception):
    """The server could not be reached, even after the retries."""


class _Src:
    def __init__(self, n, url, headers):
        self.n, self.url, self.headers = n, url, dict(headers or {})
        self.size = None
        self.ctype = None
        self.lock = threading.Lock()


def _span(header):
    """'bytes=100-' -> (100, None); 'bytes=5-9' -> (5, 9); None -> (0, None).
    Anything else (a suffix range, several ranges) -> None."""
    if not header:
        return 0, None
    m = re.fullmatch(r"\s*bytes=(\d+)-(\d*)\s*", header)
    if not m:
        return None
    return int(m.group(1)), (int(m.group(2)) if m.group(2) else None)


class Proxy:
    """See the module docstring. start(), add(url, headers) -> a local link,
    stop(). Counters: fetched (bytes from the server), served (bytes to
    ffmpeg), requests (to the server); net_error: the last time the server
    could not be reached, cleared by the next piece that came; broken: why
    a stream to ffmpeg had to be cut short (None: never). on_wait(secs,
    error) is told before each wait for the network."""

    def __init__(self, opener=None, piece=PIECE, block=BLOCK, ahead=AHEAD,
                 cache=CACHE, sleeps=SLEEPS, timeout=30, on_wait=None):
        self.opener = opener or urllib.request.build_opener()
        self.piece = max(block, piece // block * block)
        self.block, self.ahead = block, max(1, ahead)
        self.cache_blocks = max(ahead * self.piece // block, cache // block)
        self.sleeps, self.timeout = tuple(sleeps), timeout
        self.fetched = self.served = self.requests = 0
        self.net_error = None
        self.broken = None
        self.on_wait = on_wait
        self._waited = 0.0
        self._srcs = {}
        self._by_url = {}
        self._cache = collections.OrderedDict()
        self._inflight = {}
        self._lock = threading.Lock()
        self._pool = None
        self._server = None
        self._closed = False

    # ---------------------------------------------------------- the server
    def start(self):
        if self._server:
            return self
        owner = self

        class Handler(_Handler):
            proxy = owner

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        srv.daemon_threads = True
        self._server = srv
        self._pool = ThreadPoolExecutor(max_workers=2 * self.ahead + 2,
                                        thread_name_prefix="rangeproxy")
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2},
                         daemon=True, name="rangeproxy").start()
        return self

    @property
    def port(self):
        return self._server.server_address[1] if self._server else None

    def add(self, url, headers=None):
        """A local link ffmpeg can read `url` through (the same link for the
        same url and headers)."""
        self.start()
        key = (url, tuple(sorted((headers or {}).items())))
        with self._lock:
            src = self._by_url.get(key)
            if src is None:
                src = _Src(len(self._srcs), url, headers)
                self._srcs[src.n] = src
                self._by_url[key] = src
        return f"http://127.0.0.1:{self.port}/p/{src.n}"

    def stop(self):
        self._closed = True
        srv, self._server = self._server, None
        if srv:
            srv.shutdown()
            srv.server_close()
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            self._cache.clear()

    def summary(self):
        mb = self.fetched / 1024 ** 2
        return (f"{mb:.0f} MB fetched in {self.requests} request"
                f"{'' if self.requests == 1 else 's'}")

    # ------------------------------------------------------- the fetching
    def _get(self, src, lo, hi, stream=False):
        """One request for bytes lo..hi-1, retried across a short drop.
        -> bytes. With stream=True a server that ignores ranges (200) gives
        back its open response instead, to be passed on as it is."""
        hdr = dict(src.headers)
        hdr["Range"] = f"bytes={lo}-{hi - 1}"
        hdr["Accept-Encoding"] = "identity"
        waits = list(self.sleeps)
        while True:
            try:
                r = self.opener.open(urllib.request.Request(src.url, headers=hdr),
                                     timeout=self.timeout)
                if r.status == 200:
                    if stream:
                        return r
                    r.close()
                    raise Refused(502, "the server stopped answering ranges")
                cr = re.match(r"bytes (\d+)-(\d+)/(\d+|\*)",
                              r.headers.get("Content-Range") or "")
                if r.status != 206 or not cr or int(cr.group(1)) != lo:
                    r.close()
                    raise Refused(502, "the server answered another range")
                with r:
                    data = r.read()
                total = int(cr.group(3)) if cr.group(3) != "*" else None
                want = min(hi, total) - lo if total else int(cr.group(2)) + 1 - lo
                if len(data) < want:
                    raise http.client.IncompleteRead(data, want - len(data))
                with self._lock:
                    if total and src.size is None:
                        src.size = total
                    src.ctype = src.ctype or r.headers.get("Content-Type")
                    self.fetched += len(data)
                    self.requests += 1
                    self.net_error = None
                return data
            except urllib.error.HTTPError as e:
                if e.code < 500 and e.code not in (408, 429):
                    raise Refused(e.code, e.reason) from None
                err = e
            except Refused:
                raise
            except (OSError, http.client.HTTPException, ValueError) as e:
                err = e
            if self._closed:
                raise Down("stopped")
            if not waits:
                self.net_error = f"{type(err).__name__}: {err}"
                raise Down(self.net_error)
            w = waits.pop(0)
            self._told(w, err)
            if not cancel.sleep(w):
                raise Down("cancelled")

    def _told(self, w, err):
        """on_wait, once per wait (two pieces in flight both wait)."""
        now = time.monotonic()
        with self._lock:
            if self.on_wait is None or now < self._waited:
                return
            self._waited = now + w * 0.9
        try:
            self.on_wait(w, err)
        except Exception:                                   # noqa: BLE001
            pass

    def _cache_get(self, src, i):
        with self._lock:
            v = self._cache.get((src.n, i))
            if v is not None:
                self._cache.move_to_end((src.n, i))
            return v

    def _cache_put(self, src, i, data):
        with self._lock:
            self._cache[(src.n, i)] = data
            self._cache.move_to_end((src.n, i))
            while len(self._cache) > self.cache_blocks:
                self._cache.popitem(last=False)

    def _keep(self, src, lo, data):
        """Cache the whole blocks in `data` (which starts at block-aligned lo)."""
        B = self.block
        for k in range(0, len(data), B):
            blk = data[k:k + B]
            if len(blk) == B or (src.size and lo + k + len(blk) == src.size):
                self._cache_put(src, (lo + k) // B, blk)

    def run(self, src, lo, hi):
        """Bytes lo..hi-1 (lo on a block boundary): from the cache where it
        has them, from a request already on its way for the blocks another
        connection is fetching (ffmpeg jumps back and forth at first, and
        each jump is a new connection), and the rest in one request."""
        B = self.block
        if src.size is not None:
            hi = min(hi, src.size)
        idx = range(lo // B, (hi + B - 1) // B)
        have = {i: self._cache_get(src, i) for i in idx}
        mine, theirs = [], []
        with self._lock:
            for i in idx:
                if have[i] is not None:
                    continue
                ev = self._inflight.get((src.n, i))
                if ev is None:
                    mine.append(i)
                else:
                    theirs.append((i, ev))
            if mine:
                ev = threading.Event()
                for i in range(mine[0], mine[-1] + 1):
                    self._inflight.setdefault((src.n, i), ev)
        if mine:
            m0, m1 = mine[0] * B, min((mine[-1] + 1) * B, hi)
            try:
                data = self._get(src, m0, m1)
                self._keep(src, m0, data)
                for i in range(mine[0], mine[-1] + 1):
                    have[i] = data[i * B - m0:(i + 1) * B - m0]
            finally:
                with self._lock:
                    for i in range(mine[0], mine[-1] + 1):
                        if self._inflight.get((src.n, i)) is ev:
                            del self._inflight[(src.n, i)]
                ev.set()
        for i, ev in theirs:
            if have[i] is not None:
                continue
            ev.wait(self.timeout * (len(self.sleeps) + 2))
            have[i] = self._cache_get(src, i)
            if have[i] is None:                  # it failed, or was dropped
                end = min((i + 1) * B, src.size or (i + 1) * B)
                have[i] = self._get(src, i * B, end)
                self._keep(src, i * B, have[i])
        return b"".join(have[i] for i in idx)[:hi - lo]


class _Handler(BaseHTTPRequestHandler):
    proxy = None
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):                  # not on the user's screen
        pass

    def do_GET(self):
        P = self.proxy
        m = re.fullmatch(r"/p/(\d+)", self.path)
        src = P._srcs.get(int(m.group(1))) if m else None
        if src is None:
            return self.send_error(404)
        span = _span(self.headers.get("Range"))
        if span is None:
            return self.send_error(416)
        start, end = span
        B = P.block
        lo = start // B * B
        try:
            with src.lock:                       # the size, once
                if src.size is None:
                    got = P._get(src, lo, lo + B, stream=True)
                    if not isinstance(got, (bytes, bytearray)):
                        return self._pass_on(got, start)
                    P._keep(src, lo, got)
            size = src.size
            if size is None:
                return self.send_error(502, "no size from the server")
            if start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                return self.end_headers()
            end = size - 1 if end is None or end >= size else end
            first = P.run(src, lo, min(lo + B, end + 1))
        except Refused as e:
            # passed on as it is: a 403 is how ffmpeg (and so yt-dlp and
            # cli._download_part) learns that the links ran out -- unless it
            # comes when ffmpeg reconnects or jumps, which it takes for the
            # end of the file: hence `broken`
            P.broken = P.broken or str(e)
            return self.send_error(e.code, e.reason)
        except Down as e:
            P.broken = P.broken or f"the server cannot be reached ({e})"
            return self.send_error(504, f"the server cannot be reached ({e})")
        ranged = self.headers.get("Range") is not None
        self.send_response(206 if ranged else 200)
        self.send_header("Content-Type", src.ctype or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if ranged:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Connection", "close")
        self.end_headers()
        self._stream(src, start, end, lo, first)

    def _stream(self, src, start, end, lo, first):
        """Bytes start..end to ffmpeg, in order, with pieces fetched ahead:
        more of them the more ffmpeg has read, and never more than
        ahead x piece bytes beyond what it has read (checked after every
        1 MB handed on) -- that is all that is lost when it stops."""
        P = self.proxy
        B = P.block
        st = {"pos": start, "nxt": lo + len(first), "read": 0}
        pending = collections.deque()
        stop_at = min((end // B + 1) * B, src.size)

        def ahead():
            window = min(P.ahead * P.piece, max(B, 2 * st["read"] + B))
            while (st["nxt"] < stop_at and len(pending) < P.ahead
                   and st["nxt"] - st["pos"] < window):
                nxt = st["nxt"]
                size = min(P.piece, max(B, (window - max(0, nxt - st["pos"]))
                                        // B * B))
                hi = min(nxt + size, stop_at)
                pending.append((nxt, P._pool.submit(P.run, src, nxt, hi)))
                st["nxt"] = hi

        cur = (lo, first)
        try:
            while st["pos"] <= end:
                if cur is None:
                    ahead()
                    if not pending:
                        break
                    at, fut = pending.popleft()
                    cur = (at, fut.result())
                at, data = cur
                cur = None
                view = memoryview(data)[st["pos"] - at:end + 1 - at]
                if not len(view):
                    break
                for k in range(0, len(view), B):
                    out = view[k:k + B]
                    self.wfile.write(out)
                    st["pos"] += len(out)
                    st["read"] += len(out)
                    with P._lock:
                        P.served += len(out)
                    ahead()
        except Refused as e:
            P.broken = P.broken or str(e)            # see do_GET
        except Down as e:
            P.broken = P.broken or f"the server cannot be reached ({e})"
        except (OSError, RuntimeError):
            # ffmpeg went away: it has what it needs, or it jumped
            pass
        finally:
            for _at, fut in pending:
                fut.cancel()

    def _pass_on(self, r, start):
        """A server that ignores ranges: hand its answer on as it is."""
        with r:
            n = r.headers.get("Content-Length")
            left = start
            while left > 0:
                skip = r.read(min(left, 1 << 20))
                if not skip:
                    break
                left -= len(skip)
            self.send_response(206 if start else 200)
            self.send_header("Content-Type",
                             r.headers.get("Content-Type") or
                             "application/octet-stream")
            if n and n.isdigit():
                total = int(n)
                self.send_header("Content-Length", str(max(0, total - start)))
                if start:
                    self.send_header("Content-Range",
                                     f"bytes {start}-{total - 1}/{total}")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while True:
                    buf = r.read(1 << 20)
                    if not buf:
                        break
                    self.wfile.write(buf)
            except OSError:
                pass


# ------------------------------------------------------------- yt-dlp side

def wanted(o):
    """Use the proxy for this part? Not with --proxy (the requests would go
    around it) or --limit-rate (ffmpeg ignores it; the proxy would make it
    worse), nor when it was turned off (--no-fast-range)."""
    return (o.get("fast_range", True) and not o.get("proxy")
            and not o.get("rate_limit") and not o.get("dry_run"))


def _ours(url, hosts):
    host = (urlsplit(url).hostname or "").lower()
    hosts = HOSTS if hosts is None else hosts
    return hosts is None or any(host == h or host.endswith("." + h)
                                for h in hosts)


def rewrite(info, proxy, hosts=None):
    """A copy of yt-dlp's `info` whose plain http(s) formats are read through
    `proxy`. -> (info, how many). HLS/DASH manifests are left alone (they
    are many small requests already), and so is every other site's link
    (hosts: HOSTS by default; HOSTS = None reads every link through it --
    the tests)."""
    if not isinstance(info, dict) or not info.get("formats"):
        return info, 0
    out = json.loads(json.dumps(info))
    n = 0
    top = out.get("http_headers") or {}

    def fix(f):
        nonlocal n
        url = str(f.get("url") or "")
        if (f.get("protocol") in ("https", "http") and url.startswith("http")
                and _ours(url, hosts)):
            f["url"] = proxy.add(url, f.get("http_headers") or top)
            f["protocol"] = "http"
            n += 1

    for f in out.get("formats") or []:
        fix(f)
    for f in out.get("requested_formats") or []:
        fix(f)
    if out.get("url") and out.get("protocol") in ("https", "http"):
        fix(out)
    if n:
        # When a download from loaded info fails, yt-dlp tries again from
        # webpage_url -- a fresh, slow ffmpeg that starts the part over and
        # overwrites (-y) what had come down. Without it, the failure comes
        # back to cli._download_part, which keeps what came down and carries
        # on from where it ends. The link still goes into the file's tags
        # (--embed-metadata takes meta_* as they are).
        page = out.pop("webpage_url", None)
        if page:
            out.setdefault("meta_purl", page)
            out.setdefault("meta_comment", page)
    return out, n


def write_info(info, proxy, hosts=None):
    """rewrite(), saved where yt-dlp can load it. -> the path, or None when
    there is nothing to read through the proxy. The caller removes it."""
    new, n = rewrite(info, proxy, hosts)
    if not n:
        return None
    fd, path = tempfile.mkstemp(prefix="musictool-part-", suffix=".info.json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(new, fh)
    return path
