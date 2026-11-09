# sanctions-lists-fetcher

One command downloads 10 public sanctions lists straight from their publishers, counts the entries in each, hashes each file, writes a manifest, and prints what changed since the previous run.

Python 3.9+, standard library only. No account, no key, no dependency. Every request is a plain download from the publisher's public address; nothing is sent anywhere.

## Run it

```bash
python3 fetch_lists.py
```

The files land in `data/` (kept out of git). The manifest goes to `runs/<day>/manifest.json`: for each list, its address, download time, size, SHA-256 and entry count. If an earlier run exists in `runs/`, the script compares the two and prints which files changed and by how many entries.

To recount files you already have, without downloading:

```bash
python3 fetch_lists.py --from-dir path/to/files --day 2026-11-04
```

## Schedule it

The script exits with code 1 when any list fails, so a scheduler can raise an alert. A daily run at 06:00 with cron:

```
0 6 * * * cd /path/to/sanctions-lists-fetcher && python3 fetch_lists.py >> fetch.log 2>&1
```

## A real run

The run of 2026-11-09 (22:00 UTC), as it printed (`runs/2026-11-09/fetch.log`):

```
Fetching the 10 public lists: downloads only, nothing is sent.

  OFAC       attempt 1 failed (RemoteDisconnected), retrying in 15 s
  OFAC        19,563 entries · 28.2 MiB · sha256 329a9b9c6e55…
  OFAC-CONS      486 entries ·  1.0 MiB · sha256 e5d79f788291…
  CSL          6,213 entries · 16.2 MiB · sha256 32022b67eebc…
  UN           1,007 entries ·  2.1 MiB · sha256 a61ed5984d91…
  EU         attempt 1 failed (ConnectionResetError), retrying in 15 s
  EU         attempt 2 failed (ConnectionResetError), retrying in 45 s
  EU         FAILED · ConnectionResetError: [Errno 54] Connection reset by peer
  UK           6,420 entries · 21.1 MiB · sha256 b9edf715b3ea…
  EU-VESSELS     672 entries ·  9.9 MiB · sha256 2682ecf3442d…
  AU           4,015 entries ·  1.3 MiB · sha256 3cd81b966b78…
  CA           5,770 entries ·  2.8 MiB · sha256 d44c33444e0a…
  NZ           2,135 entries · 20.6 MiB · sha256 d5ae6f2c1816…

Manifest written to runs/2026-11-09/manifest.json

Compared with 2026-11-04:
  OFAC       file changed · 19,499 → 19,563 entries (+64)
  OFAC-CONS  file changed · 486 → 486 entries (+0)
  CSL        file changed · 6,213 → 6,213 entries (+0)
  UN         file changed · 1,007 → 1,007 entries (+0)
  EU         not comparable
  UK         unchanged
  EU-VESSELS unchanged
  AU         unchanged
  CA         file changed · 5,708 → 5,770 entries (+62)
  NZ         unchanged
  5 of 10 files changed

Done in 1374 s, 1 failed.
```

That night the EU server dropped the connection on every attempt (a plain `curl` of the same file was cut off too). The script recorded the failure, kept the other nine lists and exited with code 1, so a scheduler would have raised an alert.

The baseline in `runs/2026-11-04/` was computed by this script with `--from-dir`, from the files downloaded on 2026-11-04.

## The 10 lists and how entries are counted

| Code | Publisher and list | One entry is |
|---|---|---|
| OFAC | US Treasury, OFAC SDN list (XML) | an `<sdnEntry>` |
| OFAC-CONS | US Treasury, OFAC Consolidated (non-SDN) list (XML) | an `<sdnEntry>` |
| CSL | US Consolidated Screening List (CSV) | a row from a Commerce or State list (its Treasury rows are already counted under OFAC) |
| UN | UN Security Council Consolidated List (XML) | an `<INDIVIDUAL>` or an `<ENTITY>` |
| EU | EU Financial Sanctions Files, XML 1.1 | a `<sanctionEntity>` |
| UK | UK Sanctions List, FCDO (XML) | a `<Designation>` |
| EU-VESSELS | Annex XLII of Regulation (EU) No 833/2014, consolidated text on EUR-Lex (XHTML) | a distinct IMO number in the annex's table |
| AU | Australia, DFAT Consolidated List (XLSX) | a reference number (its "a", "b" rows are aliases of the same entry) |
| CA | Canada, Consolidated Autonomous Sanctions List (XML) | a `<record>` |
| NZ | New Zealand, MFAT Russia Sanctions Register (XLSX) | a row marked "sanctioned" on the register sheet, plus each listed ship not marked deleted |

The EU address carries `token=dG9rZW4tMjAxNw`: that is the public generic token the European Commission's own RSS feed publishes for these files, not a personal key.

This repository holds the script and the manifests only. The lists themselves are not copied here; download them from their publishers.
