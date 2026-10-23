"""Lyrics providers.

Order matters. Each returns Candidate objects; the orchestrator verifies a
candidate actually matches the query before accepting it -- EXCEPT YouTube
Music, which is looked up by the exact videoId of the file you downloaded, so
there is no ambiguity to check.

Sources, best first for YouTube-sourced libraries:

  ytmusic   YouTube Music's own timed lyrics (MusixMatch-licensed). Needs the
            videoId. Timings match YOUR file exactly. Three unauthenticated
            calls; only the ANDROID_MUSIC client returns timings.
  lrclib    Free, open, keyless community DB. Synced + plain. The safe default.
  netease   NetEase Cloud Music. Public, keyless, large synced corpus
            (especially strong for CJK, but covers plenty of Western tracks).

Deliberately NOT implemented: MusixMatch direct (needs an API key and the free
tier only returns 30% of each lyric), and Genius/AZLyrics/Megalobiz (plain text
only, and aggressive bot detection -- that is what kept blocking ytmdl).
"""

import os

from .util import (similarity, title_variants, artist_variants, timed_to_lrc,
                   norm_key)

DEFAULT_ORDER = ("ytmusic", "lrclib", "netease")

# Overridable for mirrors and for testing.
YTM_BASE = os.environ.get("MUSICTOOL_YTM_BASE", "https://music.youtube.com/youtubei/v1")
YTM_NEXT = YTM_BASE + "/next"
YTM_BROWSE = YTM_BASE + "/browse"
NETEASE_BASE = os.environ.get("MUSICTOOL_NETEASE_BASE", "https://music.163.com/api")
NETEASE_SEARCH = NETEASE_BASE + "/search/get/web"
NETEASE_LYRIC = NETEASE_BASE + "/song/lyric"
NETEASE_HEADERS = {"Referer": "https://music.163.com/",
                   "User-Agent": "Mozilla/5.0 (musictool)"}

MIN_TITLE = 0.72
MIN_ARTIST = 0.50


class Query:
    def __init__(self, artist="", title="", album="", duration=0.0,
                 video_id="", path=""):
        self.artist = artist
        self.title = title
        self.album = album
        self.duration = float(duration or 0)
        self.video_id = video_id
        self.path = path

    def artists(self, fname_artist=""):
        return artist_variants(self.artist, fname_artist)

    def titles(self):
        return title_variants(self.title)


class Candidate:
    def __init__(self, provider, title, artist, album="", duration=0.0,
                 lyrics="", synced=False, trusted=False, note=""):
        self.provider = provider
        self.title = title
        self.artist = artist
        self.album = album
        self.duration = float(duration or 0)
        self.lyrics = lyrics
        self.synced = synced
        self.trusted = trusted          # looked up by exact id: skip similarity
        self.note = note
        self.score = 0.0

    def describe(self):
        return (f"{self.provider}: {self.artist} / {self.title}"
                f" [{self.album}]"
                f"{' synced' if self.synced else ' UNSYNCED'}"
                + (f" ({self.note})" if self.note else ""))


class Provider:
    name = "?"
    needs_video = False
    blurb = ""

    def available(self):
        return True, "ok"

    def fetch(self, q, http):
        raise NotImplementedError


# --------------------------------------------------------------------------
class YTMusic(Provider):
    name = "ytmusic"
    needs_video = True
    blurb = ("YouTube Music timed lyrics, looked up by videoId. "
             "No key. Best timing match for YouTube downloads.")

    def available(self):
        return True, "needs the videoId recorded at download time"

    def _lyrics_browse_id(self, video_id, http):
        body = {"context": {"client": {"clientName": "WEB_REMIX",
                                       "clientVersion": "1.20240101.01.00"}},
                "videoId": video_id}
        data = http.json(YTM_NEXT, headers={}, method="POST", body=body)
        if not data:
            return None
        try:
            tabs = (data["contents"]["singleColumnMusicWatchNextResultsRenderer"]
                    ["tabbedRenderer"]["watchNextTabbedResultsRenderer"]["tabs"])
        except (KeyError, TypeError):
            return None
        for tab in tabs:
            ep = (tab.get("tabRenderer", {}).get("endpoint", {})
                  .get("browseEndpoint", {}))
            cfg = (ep.get("browseEndpointContextSupportedConfigs", {})
                   .get("browseEndpointContextMusicConfig", {}))
            if cfg.get("pageType") == "MUSIC_PAGE_TYPE_TRACK_LYRICS":
                return ep.get("browseId")
        return None

    def fetch(self, q, http):
        if not q.video_id:
            return []
        browse_id = self._lyrics_browse_id(q.video_id, http)
        if not browse_id:
            return []
        # ANDROID_MUSIC is the client that returns timed cues; WEB_REMIX does not.
        body = {"context": {"client": {"clientName": "ANDROID_MUSIC",
                                       "clientVersion": "7.03.52"}},
                "browseId": browse_id}
        data = http.json(YTM_BROWSE, headers={}, method="POST", body=body)
        if not data:
            return []
        try:
            model = (data["contents"]["elementRenderer"]["newElement"]["type"]
                     ["componentType"]["model"]["timedLyricsModel"]["lyricsData"])
            rows = model["timedLyricsData"]
        except (KeyError, TypeError):
            return []
        cues = []
        for row in rows:
            try:
                ms = int(row["cueRange"]["startTimeMilliseconds"])
            except (KeyError, TypeError, ValueError):
                continue
            cues.append((ms, row.get("lyricLine", "").strip()))
        if not cues:
            return []
        return [Candidate(self.name, q.title, q.artist, album="", duration=q.duration,
                          lyrics=timed_to_lrc(cues), synced=True, trusted=True,
                          note=f"videoId {q.video_id}, {len(cues)} cues")]


# --------------------------------------------------------------------------
class LrcLib(Provider):
    name = "lrclib"
    blurb = "Free, open, keyless community DB. Synced + plain. Requires a User-Agent."

    base = os.environ.get("MUSICTOOL_LRCLIB_BASE", "https://lrclib.net")

    def _get(self, http, path, params):
        data = http.json(self.base + path, params=params)
        return data if data else None

    def fetch(self, q, http):
        out = []
        seen = set()

        def add(rec):
            if not rec or rec.get("id") in seen:
                return
            seen.add(rec.get("id"))
            out.append(Candidate(
                self.name, rec.get("trackName") or "", rec.get("artistName") or "",
                rec.get("albumName") or "", rec.get("duration") or 0,
                rec.get("syncedLyrics") or rec.get("plainLyrics") or "",
                synced=bool(rec.get("syncedLyrics")), note=f"id {rec.get('id')}"))

        titles, artists = q.titles(), q.artists()
        # exact signature, most specific first
        for title in titles:
            for artist in artists:
                for params in ({"album_name": q.album} if q.album else {}, {}):
                    p = {"artist_name": artist, "track_name": title}
                    p.update(params)
                    if q.duration:
                        p["duration"] = int(round(q.duration))
                    add(self._get(http, "/api/get", p))
                    if q.duration:
                        p2 = dict(p)
                        p2.pop("album_name", None)
                        add(self._get(http, "/api/get", p2))
        for title in titles[:2]:
            for artist in artists[:2]:
                add(self._get(http, "/api/get",
                              {"artist_name": artist, "track_name": title}))
        for title in titles[:2]:
            for artist in artists[:2]:
                for rec in (self._get(http, "/api/search",
                                      {"q": f"{artist} {title}"}) or []):
                    add(rec)
        for rec in (self._get(http, "/api/search", {"q": titles[0]}) or []):
            add(rec)
        return out


# --------------------------------------------------------------------------
class NetEase(Provider):
    name = "netease"
    blurb = "NetEase Cloud Music. Public, keyless, large synced corpus."

    NO_LYRIC_MARKERS = ("纯音乐", "暂无歌词", "instrumental", "no lyrics")

    def fetch(self, q, http):
        titles = q.titles()
        artists = q.artists()
        query = f"{artists[0]} {titles[0]}" if artists else titles[0]
        data = http.json(NETEASE_SEARCH,
                         params={"s": query, "type": 1, "limit": 10, "offset": 0},
                         headers=NETEASE_HEADERS)
        songs = ((data or {}).get("result") or {}).get("songs") or []
        out = []
        for s in songs[:6]:
            sid = s.get("id")
            if not sid:
                continue
            name = s.get("name") or ""
            art = ", ".join(a.get("name", "") for a in (s.get("artists") or []))
            album = ((s.get("album") or {}).get("name")) or ""
            dur = (s.get("duration") or 0) / 1000.0
            lyr = http.json(NETEASE_LYRIC,
                            params={"id": sid, "lv": -1, "kv": 1, "tv": -1},
                            headers=NETEASE_HEADERS)
            text = ((lyr or {}).get("lrc") or {}).get("lyric") or ""
            if not text.strip() or any(m in text for m in self.NO_LYRIC_MARKERS):
                continue
            from .util import classify
            out.append(Candidate(self.name, name, art, album, dur, text,
                                 synced=classify(text) == "synced",
                                 note=f"id {sid}"))
        return out


# --------------------------------------------------------------------------
REGISTRY = {"ytmusic": YTMusic, "lrclib": LrcLib, "netease": NetEase}


def build(order=DEFAULT_ORDER):
    return [REGISTRY[n]() for n in order if n in REGISTRY]


def score(cand, q, titles, artists):
    """Returns (accepted, score). Verifies a candidate is the right song."""
    if cand.trusted:
        return True, 1.0
    tsim = max((similarity(cand.title, t) for t in titles), default=0.0)
    asim = max((similarity(cand.artist, a) for a in artists), default=0.0)
    if tsim < MIN_TITLE or asim < MIN_ARTIST:
        return False, 0.0
    dscore = 0.0
    if q.duration and cand.duration:
        diff = abs(cand.duration - q.duration)
        dscore = 1.0 if diff <= 2 else max(0.0, 1.0 - diff / 60.0)
    return True, 0.55 * tsim + 0.25 * asim + 0.20 * dscore


def find_best(q, providers, http, allow_unsynced=False, log=None,
              fname_artist=""):
    """Try each provider in order; return (candidate, tried_log)."""
    titles, artists = q.titles(), q.artists(fname_artist)
    tried = []
    best_synced = best_plain = None

    for prov in providers:
        if prov.needs_video and not q.video_id:
            tried.append(f"{prov.name}: skipped (no videoId for this file)")
            continue
        try:
            cands = prov.fetch(q, http)
        except Exception as e:
            tried.append(f"{prov.name}: error {e}")
            continue
        if not cands:
            tried.append(f"{prov.name}: nothing")
            continue
        kept = 0
        for c in cands:
            ok, s = score(c, q, titles, artists)
            if not ok:
                continue
            c.score = s
            kept += 1
            if c.synced:
                if best_synced is None or s > best_synced.score:
                    best_synced = c
            elif best_plain is None or s > best_plain.score:
                best_plain = c
        tried.append(f"{prov.name}: {len(cands)} result(s), {kept} passed matching")
        # A trusted, synced hit (YouTube by videoId) is as good as it gets.
        if best_synced is not None and best_synced.trusted:
            break
        if log:
            log(tried[-1])

    if best_synced:
        return best_synced, tried
    if best_plain and allow_unsynced:
        return best_plain, tried
    if best_plain:
        tried.append("only unsynced lyrics available (use --allow-unsynced)")
    else:
        tried.append("no candidate passed the similarity threshold")
    return None, tried
