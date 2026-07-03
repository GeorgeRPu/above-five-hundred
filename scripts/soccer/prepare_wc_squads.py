#!/usr/bin/env python3
"""Build per-nation World Cup squads with each player's club and minutes.

This is the player bridge for the club-SPI roster prior (538's
roster-based SPI: club_SPI x (0.75 + 0.25 x minutes_fraction), composited
over a national squad). We take it from salimt/football-datasets, a
Transfermarkt dump:

  * player_profiles.csv      -> player_id -> name, citizenship, position
  * player_market_value.csv  -> player_id, date -> value (squad selection)
  * player_performances.csv  -> player_id, season -> club + minutes (157 MB,
                                git-LFS, streamed)

For the historical World Cups (2014, 2018, 2022) squads are the REAL
rosters from openfootball/worldcup (names + birthdates; the 2014 files
also carry each player's club), joined to Transfermarkt by birthdate and
normalized name to recover each player's club and minutes that season.
For the live 2026 edition, whose roster files don't exist yet, we fall
back to each nation's 26 most valuable citizens by Transfermarkt market
value. Each player's primary club and minutes fraction become the 538
credit weight. The compact result is written to
data/soccer/wc_squads.json; club names stay in source form and are
matched to club-SPI at render time via above500.club_names.

    uv run python scripts/soccer/prepare_wc_squads.py
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
import urllib.request

from above500.soccer.wc_spi import DATA as INTL, FIFA_CODES  # 2026 field, our naming

from above500.soccer.club_roster import SQUADS_FILE as OUT
BASE = "https://raw.githubusercontent.com/salimt/football-datasets/main/datalake/transfermarkt"
PROFILES = f"{BASE}/player_profiles/player_profiles.csv"
MARKET = f"{BASE}/player_market_value/player_market_value.csv"
PERF = ("https://media.githubusercontent.com/media/salimt/football-datasets/main"
        "/datalake/transfermarkt/player_performances/player_performances.csv")

# World Cup year -> (Transfermarkt season, market-value cutoff date)
WC_SEASON = {2014: "13/14", 2018: "17/18", 2022: "21/22", 2026: "25/26"}
WC_CUTOFF = {2014: "2014-06-12", 2018: "2018-06-14",
             2022: "2022-11-20", 2026: "2026-06-11"}
LIVE_YEAR = 2026   # season data is incomplete; prefer profiles' current club
SQUAD_SIZE = 26
FULL_SEASON_MIN = 2800   # ~31 league matches; regulars saturate near 1.0

# Transfermarkt citizenship -> our team naming, where they differ.
NATION_FIXUPS = {
    "Korea, South": "South Korea", "South Korea": "South Korea",
    "USA": "United States", "United States": "United States",
    "Cote d'Ivoire": "Ivory Coast", "Côte d'Ivoire": "Ivory Coast",
    "DR Congo": "DR Congo", "Congo DR": "DR Congo",
    "Cape Verde": "Cape Verde", "Cabo Verde": "Cape Verde",
    "Czech Republic": "Czech Republic", "Czechia": "Czech Republic",
    "Turkey": "Turkey", "Türkiye": "Turkey",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Iran": "Iran", "IR Iran": "Iran",
    "Curacao": "Curaçao", "Curaçao": "Curaçao",
}


OF_RAW = "https://raw.githubusercontent.com/openfootball/worldcup/master"
# 2014 per-team squad files (fixed list — the GitHub tree API rate-limits)
OF14_FILES = [
    "ar-argentina", "au-australia", "ba-bosnia-herzegovina", "be-belgium",
    "br-brazil", "ch-switzerland", "ci-cote-d-ivoire", "cl-chile",
    "cm-cameroon", "co-colombia", "cr-costa-rica", "de-deutschland",
    "dz-algeria", "ec-ecuador", "en-england", "es-espana", "fr-france",
    "gh-ghana", "gr-greece", "hn-honduras", "hr-croatia", "ir-iran",
    "it-italy", "jp-japan", "kr-south-korea", "mx-mexico", "ng-nigeria",
    "nl-netherlands", "pt-portugal", "ru-russia", "us-united-states",
    "uy-uruguay",
]
OF14_NATION_FIXUPS = {"Deutschland": "Germany", "Espana": "Spain",
                      "Cote D Ivoire": "Ivory Coast",
                      "Bosnia Herzegovina": "Bosnia and Herzegovina"}

# openfootball squad-file line formats:
#  2014 (per-team files):  " (1)  GK  Sergio Romero   ##  45, AS Monaco (FRA)"
_OF14_RE = re.compile(
    r"^\s*\(\d+\)\s+\w{2}\s+(.+?)\s+##\s+\d+,\s+(.+?)(?:\s+\(\w{3}\))?\s*$")
#  2018/2022 (one combined file): "  1, Seny DIENG,   GK,  b. 1994/11/23"
_OF_LINE_RE = re.compile(
    r"^\s*\d+,\s+(.+?),\s+(GK|DF|MF|FW),\s+b\.\s+(\d{4}/\d{2}/\d{2})")


def _strip_id(name: str) -> str:
    # profiles store "Lionel Messi (28003)"; drop the trailing id
    i = name.rfind(" (")
    return name[:i] if i != -1 and name[i + 2:-1].isdigit() else name


def _norm_name(name: str) -> str:
    """Accent-folded lowercase name for cross-source player matching."""
    import unicodedata
    s = unicodedata.normalize("NFKD", name)
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z ]", " ", s).split())


def _get(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=90) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception:
        return None


def _openfootball_squads() -> dict[int, dict[str, list[dict]]]:
    """Real rosters: {year: {nation: [{name, dob?, club?}]}}."""
    out: dict[int, dict[str, list[dict]]] = {}

    # 2014: per-team files with clubs, no birthdates
    nations14 = {}
    for slug in OF14_FILES:                          # "ar-argentina"
        nation = slug.split("-", 1)[1].replace("-", " ").title()
        nation = OF14_NATION_FIXUPS.get(nation, NATION_FIXUPS.get(nation, nation))
        text = _get(f"{OF_RAW}/2014--brazil/squads/{slug}.txt") or ""
        squad = []
        for line in text.splitlines():
            m = _OF14_RE.match(line)
            if m:
                squad.append({"name": m.group(1).strip(),
                              "club": m.group(2).strip(), "dob": None})
        if squad:
            nations14[nation] = squad
    out[2014] = nations14

    # 2018/2022: one combined file, names + birthdates, no clubs
    for year in (2018, 2022):
        text = _get(f"{OF_RAW}/more/{year}_squads.txt") or ""
        nations: dict[str, list[dict]] = {}
        current = None
        for line in text.splitlines():
            if line.startswith("=="):
                name = line.lstrip("= ").split("#")[0].strip()
                current = NATION_FIXUPS.get(name, name)
                nations[current] = []
            else:
                m = _OF_LINE_RE.match(line)
                if m and current:
                    nations[current].append({
                        "name": m.group(1).strip(),
                        "dob": m.group(3).replace("/", "-"),
                        "club": None,
                    })
        out[year] = {n: sq for n, sq in nations.items() if sq}

    return out


def _wc_participants() -> dict[int, set[str]]:
    """Nations per World Cup, from the committed international results."""
    years = {y: set() for y in WC_SEASON}
    with gzip.open(INTL, "rt", newline="") as f:
        for r in csv.DictReader(f):
            if r["tournament"] != "FIFA World Cup":
                continue
            y = int(r["date"][:4])
            if y in years:
                years[y].add(r["home_team"]); years[y].add(r["away_team"])
    # 2026 hasn't been played; use the known 48-team field
    years[2026] = set(FIFA_CODES)
    return years


def _load_profiles(needed_nations: set[str]) -> dict[str, dict]:
    print("  loading player profiles …")
    with urllib.request.urlopen(PROFILES, timeout=180) as resp:
        text = resp.read().decode("utf-8", "replace")
    players = {}
    for r in csv.DictReader(io.StringIO(text)):
        # dual-nationals store "France  Cameroon" (double-space separated);
        # assign the first listed nationality that is in the WC field.
        nation = None
        for c in re.split(r"\s{2,}", r["citizenship"].strip()):
            mapped = NATION_FIXUPS.get(c, c)
            if mapped in needed_nations:
                nation = mapped
                break
        if nation is None:
            continue
        players[r["player_id"]] = {
            "name": _strip_id(r["player_name"]),
            "nation": nation,
            "dob": r.get("date_of_birth", ""),
            "position": r.get("main_position", ""),
            "current_club": r.get("current_club_name", ""),
        }
    print(f"  kept {len(players):,} players from WC nations")
    return players


def _load_market_values(players: dict) -> dict[int, dict[str, float]]:
    """{wc_year: {player_id: market value as of that WC's cutoff date}}."""
    print("  loading market values …")
    with urllib.request.urlopen(MARKET, timeout=180) as resp:
        text = resp.read().decode("utf-8", "replace")
    by_year: dict[int, dict[str, float]] = {y: {} for y in WC_CUTOFF}
    # rows are per player over time; keep the latest value at/under each cutoff
    seen: dict[int, dict[str, str]] = {y: {} for y in WC_CUTOFF}
    for r in csv.DictReader(io.StringIO(text)):
        pid, date = r["player_id"], r["date_unix"]
        if pid not in players:
            continue
        try:
            value = float(r["value"] or 0)
        except ValueError:
            continue
        for year, cutoff in WC_CUTOFF.items():
            if date <= cutoff and date >= seen[year].get(pid, ""):
                seen[year][pid] = date
                by_year[year][pid] = value
    return by_year


def _stream_minutes(players: dict, seasons: set[str]) -> dict:
    """player_id -> season -> {team_name: minutes} for the target seasons."""
    print("  streaming performances (157 MB) …")
    req = urllib.request.Request(PERF, headers={"Accept-Encoding": "identity"})
    acc: dict = {}
    with urllib.request.urlopen(req, timeout=600) as resp:
        reader = csv.DictReader(io.TextIOWrapper(resp, encoding="utf-8", errors="replace"))
        for r in reader:
            if r["season_name"] not in seasons or r["player_id"] not in players:
                continue
            try:
                mins = int(float(r["minutes_played"] or 0))
            except ValueError:
                continue
            if mins <= 0:
                continue
            by_season = acc.setdefault(r["player_id"], {})
            teams = by_season.setdefault(r["season_name"], {})
            teams[r["team_name"]] = teams.get(r["team_name"], 0) + mins
    print(f"  collected minutes for {len(acc):,} players")
    return acc


def _build_tm_index(players: dict) -> tuple[dict, dict]:
    """(by (dob, normalized surname), by (nation, normalized full name))."""
    by_dob: dict[tuple[str, str], list[str]] = {}
    by_name: dict[tuple[str, str], list[str]] = {}
    for pid, p in players.items():
        norm = _norm_name(p["name"])
        if not norm:
            continue
        surname = norm.split()[-1]
        if p["dob"]:
            by_dob.setdefault((p["dob"], surname), []).append(pid)
        by_name.setdefault((p["nation"], norm), []).append(pid)
    return by_dob, by_name


def _match_player(entry: dict, nation: str, by_dob: dict, by_name: dict,
                  players: dict) -> str | None:
    """Find the Transfermarkt player_id for a squad-file entry."""
    norm = _norm_name(entry["name"])
    if not norm:
        return None
    surname = norm.split()[-1]
    if entry.get("dob"):
        cands = by_dob.get((entry["dob"], surname), [])
        if len(cands) == 1:
            return cands[0]
        # same dob+surname collision: prefer matching nation
        for pid in cands:
            if players[pid]["nation"] == nation:
                return pid
    cands = by_name.get((nation, norm), [])
    return cands[0] if len(cands) == 1 else None


def main() -> None:
    participants = _wc_participants()
    needed = set().union(*participants.values())
    players = _load_profiles(needed)
    values = _load_market_values(players)
    minutes = _stream_minutes(players, set(WC_SEASON.values()))
    real = _openfootball_squads()
    by_dob, by_name = _build_tm_index(players)

    squads: dict[str, dict] = {}
    for year, season in WC_SEASON.items():
        out_nations = {}

        if year in real:
            # real rosters, joined to Transfermarkt for club + minutes
            matched = total = 0
            for nation, squad in real[year].items():
                if nation not in participants[year]:
                    print(f"    !! {year}: unmapped nation {nation!r}")
                    continue
                roster = []
                for entry in squad:
                    total += 1
                    pid = _match_player(entry, nation, by_dob, by_name, players)
                    teams = minutes.get(pid, {}).get(season) if pid else None
                    perf_club, mins = (max(teams.items(), key=lambda kv: kv[1])
                                       if teams else (None, 0))
                    club = entry["club"] or perf_club   # 2014 files carry clubs
                    if pid:
                        matched += 1
                    roster.append({
                        "player": entry["name"],
                        "club": club,
                        "minutes_fraction": round(min(1.0, mins / FULL_SEASON_MIN), 3),
                        "position": players[pid]["position"] if pid else "",
                    })
                if roster:
                    out_nations[nation] = roster
            print(f"  {year}: real rosters, TM match {matched}/{total} players")
        else:
            # live edition: 26 most valuable citizens as of the tournament
            by_nation: dict[str, list] = {}
            for pid, value in values[year].items():
                by_nation.setdefault(players[pid]["nation"], []).append((value, pid))
            for nation in participants[year]:
                ranked = sorted(by_nation.get(nation, []), reverse=True)[:SQUAD_SIZE]
                roster = []
                for _, pid in ranked:
                    teams = minutes.get(pid, {}).get(season) or {}
                    perf_club, mins = (max(teams.items(), key=lambda kv: kv[1])
                                       if teams else (None, 0))
                    club = players[pid]["current_club"] or perf_club
                    minf = min(1.0, mins / FULL_SEASON_MIN) if mins else 0.8
                    roster.append({
                        "player": players[pid]["name"],
                        "club": club,
                        "minutes_fraction": round(minf, 3),
                        "position": players[pid]["position"],
                    })
                if roster:
                    out_nations[nation] = roster
        squads[str(year)] = out_nations
        print(f"  {year}: squads for {len(out_nations)}/{len(participants[year])} nations")

    OUT.write_text(json.dumps({
        "source": "salimt/football-datasets (Transfermarkt)",
        "metric": "best ~26 citizens by club minutes; primary club + minutes fraction",
        "squads": squads,
    }, indent=2) + "\n")
    print(f"\nWrote {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
