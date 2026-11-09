#!/usr/bin/env python3
"""Download 10 public sanctions lists, count and hash each one, write a manifest,
and print what changed since the previous run.

Python 3.9+, standard library only. Nothing is sent anywhere: every request is a
plain download from the publisher's own public address.

usage:
  python3 fetch_lists.py                     # download now, compare with the latest run in runs/
  python3 fetch_lists.py --from-dir DIR      # recount files already on disk (no download)
"""
import argparse
import csv
import hashlib
import http.client
import io
import json
import re
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

# The EU address is the one the Commission's public RSS feed announces, with its public generic token.
EU_URL = ("https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content"
          "?token=dG9rZW4tMjAxNw")
CELEX_833 = "02014R0833-20260724"

LISTS = [
    {"code": "OFAC", "file": "ofac.xml", "kind": "ofac-xml",
     "title": "OFAC Specially Designated Nationals (SDN) list",
     "url": "https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/sdn.xml"},
    {"code": "OFAC-CONS", "file": "ofac-cons.xml", "kind": "ofac-xml",
     "title": "OFAC Consolidated Sanctions List (the non-SDN lists)",
     "url": "https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/consolidated.xml"},
    {"code": "CSL", "file": "csl.csv", "kind": "csl-csv",
     "title": "US Consolidated Screening List: its Commerce and State lists",
     "url": "https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.csv"},
    {"code": "UN", "file": "un.xml", "kind": "un-xml",
     "title": "UN Security Council Consolidated List",
     "url": "https://scsanctions.un.org/resources/xml/en/consolidated.xml"},
    {"code": "EU", "file": "eu.xml", "kind": "eu-xml",
     "title": "EU Financial Sanctions Files (FSF), XML 1.1",
     "url": EU_URL},
    {"code": "UK", "file": "uk.xml", "kind": "uk-xml",
     "title": "UK Sanctions List (FCDO)",
     "url": "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml"},
    {"code": "EU-VESSELS", "file": "eu-vessels.xhtml", "kind": "eu-vessels-xhtml",
     "title": "EU designated vessels: Annex XLII of Regulation (EU) No 833/2014",
     "url": f"https://publications.europa.eu/resource/celex/{CELEX_833}"},
    {"code": "AU", "file": "au.xlsx", "kind": "au-xlsx",
     "title": "Australian Sanctions Consolidated List (DFAT)",
     "url": "https://www.dfat.gov.au/sites/default/files/Australian_Sanctions_Consolidated_List.xlsx"},
    {"code": "CA", "file": "ca.xml", "kind": "ca-xml",
     "title": "Canada Consolidated Autonomous Sanctions List (SEMA)",
     "url": "https://www.international.gc.ca/world-monde/assets/office_docs/international_relations-relations_internationales/sanctions/sema-lmes.xml"},
    {"code": "NZ", "file": "nz.xlsx", "kind": "nz-xlsx",
     "title": "New Zealand Russia Sanctions Register (MFAT)",
     "url": "https://www.mfat.govt.nz/assets/Countries-and-Regions/Europe/Ukraine/Russia-Sanctions-Register.xlsx"},
]

# Some publishers refuse a client without a browser User-Agent (the UN does).
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
           "Accept": "application/xml, application/xhtml+xml, text/csv, */*"}
# EUR-Lex serves the consolidated text in several formats; ask for XHTML.
VESSEL_HEADERS = {**HEADERS, "Accept": "application/xhtml+xml", "Accept-Language": "en"}


def download(item, attempts=3):
    """A dropped connection is common on large files: try again after 15 s, then 45 s."""
    headers = VESSEL_HEADERS if item["kind"] == "eu-vessels-xhtml" else HEADERS
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(item["url"], headers=headers)
            with urllib.request.urlopen(req, timeout=300) as resp:
                return resp.read()
        except (OSError, http.client.HTTPException) as e:
            if attempt == attempts or getattr(e, "code", 500) < 500:
                raise
            wait = 15 * 3 ** (attempt - 1)
            print(f"  {item['code']:<10} attempt {attempt} failed ({type(e).__name__}), retrying in {wait} s")
            time.sleep(wait)


# ---- counting: one rule per format, written down in the README ----

def count_tags(data, pattern):
    return len(re.findall(pattern, data))


def count_csl(data):
    # The CSL repeats OFAC's lists (Treasury Department); those are counted under OFAC and OFAC-CONS.
    rows = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    return sum(1 for r in rows if "Treasury Department" not in (r.get("source") or ""))


def count_eu_vessels(data):
    text = data.decode("utf-8", "replace")
    start = re.search(r"ANNEX\s+XLII\b", text)
    if not start:
        return 0
    end = re.search(r"ANNEX\s+XLIII\b", text[start.start() + 10:])
    annex = text[start.start():] if not end else text[start.start():start.start() + 10 + end.start()]
    clean = lambda c: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
    imos = set()
    for row in re.finditer(r"<tr\b[^>]*>([\s\S]*?)</tr>", annex):
        cells = [clean(c) for c in re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", row.group(1))]
        # a vessel row: rank ("462." or "462"), name, seven-digit IMO number
        if len(cells) >= 3 and re.fullmatch(r"\d+\.?", cells[0]) and cells[1] and re.fullmatch(r"\d{7}", cells[2]):
            imos.add(cells[2])
    return len(imos)


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def read_sheets(data):
    """{sheet name: list of rows, each a list of cell strings} from an .xlsx file."""
    book = zipfile.ZipFile(io.BytesIO(data))
    shared = []
    if "xl/sharedStrings.xml" in book.namelist():
        for si in ElementTree.fromstring(book.read("xl/sharedStrings.xml")).findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    rels = {r.get("Id"): r.get("Target") for r in ElementTree.fromstring(book.read("xl/_rels/workbook.xml.rels"))}
    sheets = {}
    for s in ElementTree.fromstring(book.read("xl/workbook.xml")).find("m:sheets", NS):
        target = rels[s.get(f"{{{NS['r']}}}id")].lstrip("/")
        path = target if target.startswith("xl/") else "xl/" + target
        rows = []
        for row in ElementTree.fromstring(book.read(path)).iter(f"{{{NS['m']}}}row"):
            cells = {}
            for c in row.findall("m:c", NS):
                col = re.match(r"[A-Z]+", c.get("r")).group(0)
                idx = 0
                for ch in col:
                    idx = idx * 26 + ord(ch) - 64
                if c.get("t") == "inlineStr":
                    value = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
                else:
                    v = c.find("m:v", NS)
                    value = "" if v is None else (shared[int(v.text)] if c.get("t") == "s" else v.text or "")
                cells[idx - 1] = value.strip()
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
        sheets[s.get("name")] = rows
    return sheets


def table(rows, needed):
    """Rows as dicts, from the first row that holds every needed header."""
    for i, row in enumerate(rows):
        if all(h in row for h in needed):
            head = row
            return [dict(zip(head, r + [""] * (len(head) - len(r)))) for r in rows[i + 1:]]
    raise ValueError(f"no header row with {needed}")


def count_au(data):
    # one row per name: "8227" is the main name, "8227a", "8227b" its aliases; an entry is a reference number
    rows = table(next(iter(read_sheets(data).values())), ["Reference", "Name of Individual or Entity"])
    refs = set()
    for r in rows:
        m = re.match(r"\d+", r["Reference"])
        if m and r["Name of Individual or Entity"]:
            refs.add(m.group(0))
    return len(refs)


def count_nz(data):
    sheets = read_sheets(data)
    n = 0
    for r in table(sheets["Russia Sanctions Register"], ["Type", "Unique Identifier", "Sanction Status"]):
        if r["Type"] in ("Individual", "Entity", "Bank", "Asset") and r["Unique Identifier"] \
                and r["Sanction Status"].lower() == "sanctioned":
            name = r.get("Name of Asset") or r.get("First name") if r["Type"] == "Asset" else \
                " ".join(x for x in (r.get("First name"), r.get("Middle name(s)"), r.get("Last name")) if x)
            n += bool(name)
    for r in table(sheets["Ships"], ["Unique Identifier", "Name of Ship as of Date of Sanction"]):
        status = r.get("Sanction Status", "")
        if r["Unique Identifier"] and r["Name of Ship as of Date of Sanction"] \
                and r.get("Record Deleted Flag", "").lower() != "yes" and (not status or status.lower() == "sanctioned"):
            n += 1
    return n


def count(kind, data):
    if kind == "ofac-xml":
        return count_tags(data, rb"<sdnEntry>")
    if kind == "un-xml":
        return count_tags(data, rb"<INDIVIDUAL>") + count_tags(data, rb"<ENTITY>")
    if kind == "eu-xml":
        return count_tags(data, rb"<sanctionEntity[ >]")
    if kind == "uk-xml":
        return count_tags(data, rb"<Designation>")
    if kind == "ca-xml":
        return count_tags(data, rb"<record>")
    if kind == "csl-csv":
        return count_csl(data)
    if kind == "eu-vessels-xhtml":
        return count_eu_vessels(data)
    if kind == "au-xlsx":
        return count_au(data)
    if kind == "nz-xlsx":
        return count_nz(data)
    raise ValueError(kind)


# ---- run ----

def human(n):
    return f"{n / 1048576:.1f} MiB"


def latest_manifest(runs, exclude):
    found = sorted(p for p in runs.glob("*/manifest.json") if p.parent != exclude)
    return found[-1] if found else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from-dir", type=Path, help="recount files already downloaded to this folder")
    ap.add_argument("--data", type=Path, default=Path("data"), help="where downloads are kept (default: data/)")
    ap.add_argument("--runs", type=Path, default=Path("runs"), help="where manifests are written (default: runs/)")
    ap.add_argument("--day", help="name of this run's folder (default: today, UTC)")
    args = ap.parse_args()

    started = time.time()
    day = args.day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = args.runs / day
    out.mkdir(parents=True, exist_ok=True)
    args.data.mkdir(parents=True, exist_ok=True)
    verb = f"Recounting the 10 lists from {args.from_dir}" if args.from_dir else "Fetching the 10 public lists"
    print(f"{verb}: downloads only, nothing is sent.\n")

    lists, failed = [], 0
    for item in LISTS:
        entry = {"code": item["code"], "title": item["title"], "url": item["url"]}
        try:
            if args.from_dir:
                path = args.from_dir / item["file"]
                data = path.read_bytes()
                when = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            else:
                data = download(item)
                when = datetime.now(timezone.utc)
                (args.data / item["file"]).write_bytes(data)
            n = count(item["kind"], data)
            if n == 0:
                raise ValueError("no entry found: the file is not in the expected format")
            entry.update(ok=True, downloaded_at=when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), entries=n)
            print(f"  {item['code']:<10} {n:>7,} entries · {human(len(data)):>8} · sha256 {entry['sha256'][:12]}…")
        except Exception as e:  # one list failing must not hide the others
            failed += 1
            entry.update(ok=False, error=f"{type(e).__name__}: {e}")
            print(f"  {item['code']:<10} FAILED · {entry['error']}")
        lists.append(entry)

    manifest = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "lists": lists}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nManifest written to {out / 'manifest.json'}")

    prev_path = latest_manifest(args.runs, out)
    if prev_path:
        prev = {l["code"]: l for l in json.loads(prev_path.read_text())["lists"]}
        print(f"\nCompared with {prev_path.parent.name}:")
        changed = 0
        for l in lists:
            p = prev.get(l["code"])
            if not (l.get("ok") and p and p.get("ok")):
                print(f"  {l['code']:<10} not comparable")
                continue
            if l["sha256"] == p["sha256"]:
                print(f"  {l['code']:<10} unchanged")
                continue
            changed += 1
            delta = l["entries"] - p["entries"]
            print(f"  {l['code']:<10} file changed · {p['entries']:,} → {l['entries']:,} entries ({delta:+,})")
        print(f"  {changed} of {len(lists)} files changed")

    print(f"\nDone in {time.time() - started:.0f} s, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
