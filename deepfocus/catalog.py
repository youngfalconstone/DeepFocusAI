"""Known distracting services: their domains, extra hostnames and process names.

`resolve(name)` turns what the user typed ("youtube", "Discord", "reddit.com")
into a reviewable list of hostnames. Unknown names fall back to a generic
expansion of name.com plus common subdomains.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .hostsfile import normalise_host

# Subdomain prefixes used when expanding a root domain into hostnames.
BASIC_PREFIXES = ["", "www.", "m."]
EXTENDED_PREFIXES = BASIC_PREFIXES + [
    "api.", "app.", "web.", "cdn.", "static.", "media.", "mobile.", "login.", "accounts.", "open.",
]


@dataclass
class Service:
    key: str
    name: str
    category: str
    roots: list[str]
    hosts: list[str] = field(default_factory=list)      # extra exact hostnames
    processes: list[str] = field(default_factory=list)  # desktop apps to close
    aliases: list[str] = field(default_factory=list)


SERVICES: list[Service] = [
    # --- Video ---
    Service("youtube", "YouTube", "Video", ["youtube.com", "youtu.be", "youtube-nocookie.com", "ytimg.com"],
            hosts=["youtubei.googleapis.com", "i.ytimg.com", "yt3.ggpht.com", "music.youtube.com", "studio.youtube.com",
                   "tv.youtube.com", "kids.youtube.com", "s.youtube.com", "yt.be"],
            aliases=["yt", "youtube music"]),
    Service("netflix", "Netflix", "Video", ["netflix.com", "nflxvideo.net", "nflximg.net", "nflxext.com", "nflxso.net"],
            processes=["Netflix.exe"]),
    Service("twitch", "Twitch", "Video", ["twitch.tv", "twitchcdn.net", "jtvnw.net", "ttvnw.net"],
            hosts=["gql.twitch.tv", "passport.twitch.tv", "usher.ttvnw.net", "clips.twitch.tv"]),
    Service("kick", "Kick", "Video", ["kick.com"]),
    Service("disneyplus", "Disney+", "Video", ["disneyplus.com", "dssott.com", "disney-plus.net", "bamgrid.com"],
            aliases=["disney plus", "disney"]),
    Service("primevideo", "Prime Video", "Video", ["primevideo.com", "aiv-cdn.net", "aiv-delivery.net"],
            aliases=["amazon prime video", "prime"]),
    Service("hulu", "Hulu", "Video", ["hulu.com", "hulustream.com"]),
    Service("crunchyroll", "Crunchyroll", "Video", ["crunchyroll.com", "vrv.co"]),
    Service("max", "Max / HBO", "Video", ["max.com", "hbomax.com", "hbo.com"], aliases=["hbo", "hbo max"]),
    Service("dailymotion", "Dailymotion", "Video", ["dailymotion.com", "dmcdn.net"]),
    Service("vimeo", "Vimeo", "Video", ["vimeo.com", "vimeocdn.com"]),

    # --- Social ---
    Service("discord", "Discord", "Social", ["discord.com", "discordapp.com", "discord.gg", "discordapp.net", "discord.media",
                                              "discord.co", "discord.new", "discordstatus.com"],
            hosts=["gateway.discord.gg", "cdn.discordapp.com", "media.discordapp.net", "status.discord.com", "ptb.discord.com",
                   "canary.discord.com", "support.discord.com"],
            processes=["Discord.exe", "DiscordPTB.exe", "DiscordCanary.exe"]),
    Service("reddit", "Reddit", "Social", ["reddit.com", "redd.it", "redditmedia.com", "redditstatic.com"],
            hosts=["old.reddit.com", "new.reddit.com", "oauth.reddit.com", "i.redd.it", "v.redd.it", "preview.redd.it",
                   "gateway.reddit.com", "sh.reddit.com"]),
    Service("twitter", "X / Twitter", "Social", ["twitter.com", "x.com", "t.co", "twimg.com", "twtrdns.net"],
            hosts=["api.x.com", "abs.twimg.com", "pbs.twimg.com", "video.twimg.com", "mobile.twitter.com"],
            aliases=["x", "twitter", "tweet"]),
    Service("instagram", "Instagram", "Social", ["instagram.com", "cdninstagram.com", "ig.me", "instagr.am"],
            hosts=["i.instagram.com", "graph.instagram.com", "scontent.cdninstagram.com"], aliases=["insta", "ig"]),
    Service("facebook", "Facebook", "Social", ["facebook.com", "fb.com", "fbcdn.net", "fb.me", "facebook.net", "fbsbx.com",
                                                "messenger.com"],
            hosts=["graph.facebook.com", "connect.facebook.net", "scontent.xx.fbcdn.net", "static.xx.fbcdn.net", "edge-chat.facebook.com"],
            aliases=["fb", "messenger", "meta"]),
    Service("threads", "Threads", "Social", ["threads.net", "threads.com"]),
    Service("tiktok", "TikTok", "Social", ["tiktok.com", "tiktokcdn.com", "tiktokv.com", "musical.ly", "tiktokcdn-us.com",
                                            "byteoversea.com", "ibytedtos.com", "tiktokv.us"],
            hosts=["webcast.tiktok.com", "us.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"], aliases=["tik tok"]),
    Service("snapchat", "Snapchat", "Social", ["snapchat.com", "sc-cdn.net", "snap.com", "snapkit.com", "snap-dev.net"],
            aliases=["snap"]),
    Service("pinterest", "Pinterest", "Social", ["pinterest.com", "pinimg.com", "pin.it", "pinterest.co.uk", "pinterest.ca"]),
    Service("tumblr", "Tumblr", "Social", ["tumblr.com", "tumblr.co", "txmblr.com"]),
    Service("linkedin", "LinkedIn", "Social", ["linkedin.com", "licdn.com", "lnkd.in"]),
    Service("bluesky", "Bluesky", "Social", ["bsky.app", "bsky.social", "bsky.network"], aliases=["bsky"]),
    Service("mastodon", "Mastodon", "Social", ["mastodon.social", "mastodon.online", "mas.to", "mstdn.social"]),
    Service("whatsapp", "WhatsApp Web", "Social", ["whatsapp.com", "whatsapp.net", "wa.me"],
            hosts=["web.whatsapp.com"], processes=["WhatsApp.exe"]),
    Service("telegram", "Telegram", "Social", ["telegram.org", "t.me", "telegram.me", "telegra.ph", "tdesktop.com"],
            hosts=["web.telegram.org"], processes=["Telegram.exe"]),
    Service("quora", "Quora", "Social", ["quora.com", "quoracdn.net"]),
    Service("9gag", "9GAG", "Social", ["9gag.com", "9cache.com"]),
    Service("imgur", "Imgur", "Social", ["imgur.com", "imgur.io"]),
    Service("4chan", "4chan", "Social", ["4chan.org", "4channel.org", "4cdn.org"]),

    # --- Gaming ---
    Service("roblox", "Roblox", "Gaming", ["roblox.com", "rbxcdn.com", "robloxlabs.com", "rbx.com"],
            hosts=["apis.roblox.com", "auth.roblox.com", "gamejoin.roblox.com", "clientsettings.roblox.com",
                   "clientsettingscdn.roblox.com", "setup.rbxcdn.com", "setup-aws.rbxcdn.com", "setup-cfly.rbxcdn.com",
                   "create.roblox.com", "devforum.roblox.com", "games.roblox.com", "presence.roblox.com"],
            processes=["RobloxPlayerBeta.exe", "RobloxPlayerLauncher.exe", "RobloxStudioBeta.exe", "Bloxstrap.exe"]),
    Service("steam", "Steam", "Gaming", ["steampowered.com", "steamcommunity.com", "steamstatic.com", "steamcontent.com",
                                          "steamusercontent.com", "steamgames.com", "steam-chat.com", "s.team"],
            hosts=["store.steampowered.com", "api.steampowered.com", "help.steampowered.com", "steamcdn-a.akamaihd.net"],
            processes=["steam.exe", "steamwebhelper.exe"]),
    Service("epicgames", "Epic Games / Fortnite", "Gaming", ["epicgames.com", "fortnite.com", "unrealengine.com", "epicgames.dev"],
            hosts=["launcher-public-service-prod06.ol.epicgames.com", "account-public-service-prod.ol.epicgames.com"],
            processes=["EpicGamesLauncher.exe", "FortniteClient-Win64-Shipping.exe", "FortniteLauncher.exe"],
            aliases=["epic", "fortnite", "epic games"]),
    Service("riot", "Riot / League of Legends / Valorant", "Gaming", ["riotgames.com", "leagueoflegends.com", "playvalorant.com",
                                                                        "riotcdn.net", "rdatasrv.net"],
            processes=["RiotClientServices.exe", "LeagueClient.exe", "VALORANT.exe", "VALORANT-Win64-Shipping.exe", "League of Legends.exe"],
            aliases=["league", "lol", "valorant", "league of legends"]),
    Service("battlenet", "Battle.net / Blizzard", "Gaming", ["battle.net", "blizzard.com", "bnet.168.com", "blzstatic.cn"],
            processes=["Battle.net.exe", "Battle.net Launcher.exe"], aliases=["blizzard", "battle net"]),
    Service("minecraft", "Minecraft", "Gaming", ["minecraft.net", "mojang.com", "minecraftservices.com"],
            processes=["Minecraft.Windows.exe", "MinecraftLauncher.exe", "javaw.exe"]),
    Service("xbox", "Xbox", "Gaming", ["xbox.com", "xboxlive.com", "xboxservices.com"], processes=["XboxApp.exe", "XboxPcApp.exe"]),
    Service("playstation", "PlayStation", "Gaming", ["playstation.com", "playstation.net", "sonyentertainmentnetwork.com"]),
    Service("eaapp", "EA App", "Gaming", ["ea.com", "origin.com"], processes=["EADesktop.exe", "Origin.exe"], aliases=["ea", "origin"]),
    Service("curseforge", "CurseForge", "Gaming", ["curseforge.com", "forgecdn.net", "overwolf.com"],
            processes=["CurseForge.exe", "Overwolf.exe"]),
    Service("itch", "itch.io", "Gaming", ["itch.io", "itch.zone"], processes=["itch.exe"]),
    Service("chess", "Chess.com", "Gaming", ["chess.com", "chesscomfiles.com"]),
    Service("lichess", "Lichess", "Gaming", ["lichess.org", "lichess1.org"]),

    # --- News ---
    Service("hackernews", "Hacker News", "News", ["ycombinator.com"], hosts=["news.ycombinator.com"], aliases=["hn", "hacker news"]),
    Service("cnn", "CNN", "News", ["cnn.com", "cnn.io"]),
    Service("foxnews", "Fox News", "News", ["foxnews.com"]),
    Service("nytimes", "New York Times", "News", ["nytimes.com", "nyt.com"], aliases=["nyt"]),
    Service("bbc", "BBC", "News", ["bbc.com", "bbc.co.uk", "bbci.co.uk"]),
    Service("dailymail", "Daily Mail", "News", ["dailymail.co.uk"]),
    Service("buzzfeed", "BuzzFeed", "News", ["buzzfeed.com", "buzzfeednews.com"]),
    Service("espn", "ESPN", "News", ["espn.com", "espncdn.com"]),
    Service("yahoo", "Yahoo", "News", ["yahoo.com", "yimg.com"]),
    Service("msn", "MSN", "News", ["msn.com"]),
    Service("medium", "Medium", "News", ["medium.com"]),
    Service("producthunt", "Product Hunt", "News", ["producthunt.com"]),

    # --- Shopping ---
    Service("amazon", "Amazon", "Shopping", ["amazon.com", "amazon.co.uk", "amazon.ca", "amazon.de", "amzn.to", "amzn.com",
                                              "media-amazon.com", "ssl-images-amazon.com"]),
    Service("ebay", "eBay", "Shopping", ["ebay.com", "ebaystatic.com", "ebayimg.com", "ebay.co.uk"]),
    Service("aliexpress", "AliExpress", "Shopping", ["aliexpress.com", "aliexpress.us", "alicdn.com"]),
    Service("temu", "Temu", "Shopping", ["temu.com", "kwcdn.com"]),
    Service("etsy", "Etsy", "Shopping", ["etsy.com", "etsystatic.com"]),

    # --- Music ---
    Service("spotify", "Spotify", "Music", ["spotify.com", "scdn.co", "spotifycdn.com", "spotify.net"],
            processes=["Spotify.exe"]),
    Service("soundcloud", "SoundCloud", "Music", ["soundcloud.com", "sndcdn.com"]),
]

PRESETS: dict[str, list[str]] = {
    "Social media": ["discord", "reddit", "twitter", "instagram", "facebook", "threads", "tiktok", "snapchat", "pinterest",
                     "tumblr", "bluesky", "whatsapp", "telegram", "9gag", "imgur"],
    "Video & streaming": ["youtube", "netflix", "twitch", "kick", "disneyplus", "primevideo", "hulu", "crunchyroll", "max",
                          "dailymotion", "vimeo"],
    "Gaming": ["roblox", "steam", "epicgames", "riot", "battlenet", "minecraft", "xbox", "playstation", "eaapp", "curseforge",
               "itch", "chess", "lichess"],
    "News & feeds": ["hackernews", "cnn", "foxnews", "nytimes", "bbc", "dailymail", "buzzfeed", "espn", "yahoo", "msn",
                     "medium", "producthunt"],
    "Shopping": ["amazon", "ebay", "aliexpress", "temu", "etsy"],
    "Deep work (everything)": [s.key for s in SERVICES],
}

_BY_KEY = {s.key: s for s in SERVICES}

AI_PREFIX = "ai:"


def ai_service(key: str) -> Service | None:
    """A saved AI block list as a Service ("ai:<slug>"), so plans, presets and sessions treat it like any other."""
    if not key.startswith(AI_PREFIX):
        return None
    from . import state  # local import: state knows nothing about the catalog
    rec = state.load_ai_blocks().get(key[len(AI_PREFIX):])
    if not rec:
        return None
    hosts = [d.get("host", "") for d in rec.get("domains", [])]
    # apex domains get the usual www./m. (and extended) prefixes; everything else is kept exact
    roots = [h for h in hosts if h.count(".") == 1]
    extra = [h for h in hosts if h.count(".") != 1]
    return Service(key, rec.get("name") or f"{rec.get('service', '?')} (AI)", "AI", roots, hosts=extra,
                   processes=[a.get("exe", "") for a in rec.get("apps", []) if a.get("exe")])


def ai_files(key: str) -> list[str]:
    """Files a saved AI list wants hidden (paths only; the caller re-checks the guard and existence)."""
    if not key.startswith(AI_PREFIX):
        return []
    from . import state
    rec = state.load_ai_blocks().get(key[len(AI_PREFIX):])
    return [f.get("path", "") for f in (rec or {}).get("files", []) if f.get("path")]


def get(key: str) -> Service | None:
    return _BY_KEY.get(key) or ai_service(key)


def categories() -> list[str]:
    seen: list[str] = []
    for s in SERVICES:
        if s.category not in seen:
            seen.append(s.category)
    return seen


def search(query: str) -> list[Service]:
    """Rank services by how well the query matches name, key, aliases or roots."""
    q = query.strip().lower()
    if not q:
        return []
    scored: list[tuple[int, Service]] = []
    for s in SERVICES:
        names = [s.key, s.name.lower(), *s.aliases] + [r.split(".")[0] for r in s.roots]
        best = 0
        for n in names:
            if q == n:
                best = max(best, 100)
            elif n.startswith(q):
                best = max(best, 80)
            elif q in n:
                best = max(best, 60)
            elif n in q and len(n) >= 3:
                best = max(best, 40)
        if best:
            scored.append((best, s))
    scored.sort(key=lambda t: (-t[0], t[1].name))
    return [s for _, s in scored]


def expand_roots(roots: list[str], extended: bool) -> list[str]:
    prefixes = EXTENDED_PREFIXES if extended else BASIC_PREFIXES
    out: list[str] = []
    for r in roots:
        n = normalise_host(r)
        if not n:
            continue
        for p in prefixes:
            h = p + n
            if h not in out:
                out.append(h)
    return out


def hostnames_for(service: Service, extended: bool) -> list[str]:
    out = expand_roots(service.roots, extended)
    for h in service.hosts:
        n = normalise_host(h)
        if n and n not in out:
            out.append(n)
    return out


def generic_hostnames(name: str, extended: bool) -> list[str]:
    """Best-effort expansion for a service we do not know: 'foo' -> foo.com family."""
    raw = name.strip().lower()
    n = normalise_host(raw)
    if n:  # user typed a domain
        return expand_roots([n], extended)
    slug = "".join(ch for ch in raw if ch.isalnum() or ch == "-")
    if not slug:
        return []
    roots = [f"{slug}.com"]
    if extended:
        roots += [f"{slug}.net", f"{slug}.io", f"{slug}.tv", f"{slug}.app", f"{slug}.gg"]
    return expand_roots(roots, extended)


def resolve(query: str, extended: bool) -> tuple[Service | None, list[str]]:
    """Return (matched_service_or_None, hostnames) for a typed name."""
    hits = search(query)
    if hits and (hits[0].name.lower() == query.strip().lower() or len(hits) == 1 or query.strip().lower() in
                 (hits[0].key, *hits[0].aliases)):
        return hits[0], hostnames_for(hits[0], extended)
    if hits:
        return hits[0], hostnames_for(hits[0], extended)
    return None, generic_hostnames(query, extended)
