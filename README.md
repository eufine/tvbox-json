# TVBox config without netdisk sources

[![Update filtered TVBox config](https://github.com/eufine/tvbox-json/actions/workflows/update.yml/badge.svg)](https://github.com/eufine/tvbox-json/actions/workflows/update.yml)

This repository fetches the 饭太硬 TVBox configuration, removes netdisk
(网盘 / 云盘) sources, and publishes the cleaned JSON. GitHub Actions refreshes
it once a day, so there is no manual download, cleanup, or re-upload step.

## Use it in TVBox

Public raw URL:

```text
https://raw.githubusercontent.com/eufine/tvbox-json/main/tvbox.json
```

If `raw.githubusercontent.com` is slow or blocked on your network, put a GitHub
proxy in front of it, for example:

```text
https://ghproxy.net/https://raw.githubusercontent.com/eufine/tvbox-json/main/tvbox.json
```

Optional GitHub Pages URL (enable Pages once, see below):

```text
https://eufine.github.io/tvbox-json/tvbox.json
```

Paste one of these URLs into the TVBox / 影视仓 / FongMi configuration field.
Use the **raw** URL, not the GitHub HTML page URL.

## What gets removed

The filter removes netdisk-based sources plus an explicit list of unused
sources, including:

- netdisk keys such as `MDrive`, `玩偶`, `seed`, `ZPan`, `JPan`, `YpanSo`,
  `BpanSo`, `抠搜`, `UC`, `push_agent`
- unused keys such as `Aid`, `Bili`, `Biliych`, `dr_兔小贝`, `少儿教育`,
  `小学课堂`, `初中课堂`, `高中教育`
- site names containing `网盘`, `云盘`, `盘搜`, `盘她`, `盘他`, `四盘`,
  `夸克`, `阿里云盘`, etc.
- spider/API names such as `*Quark*`, `*AliPan*`, `*UCDrive*`, `*PikPak*`,
  `*Ypan*`, `*Bpan*`, `*S_zps*`, `*KkSs*`, `*UuSs*`, etc.
- sites using the `Cloud-drive` extension

`filter-report.json` lists exactly what was removed on the last run, including
the removal reason. Everything else (live TV, regular VOD sites, parsers,
rules, hosts) is preserved.

The explicit key list is in [`config/exclude.txt`](config/exclude.txt), so it is
easy to add or remove keys without editing the filter JSON.

### Excluding more sources

Edit [`config/exclude.txt`](config/exclude.txt) and add one site key per line.
Blank lines and lines starting with `#` are ignored. This file is the
authoritative explicit-exclusion list, so you can add or remove keys without
touching the Python code or the JSON settings.

Example:

```text
# Remove these sources
SomeSiteKey
AnotherSiteKey
```

### Customizing

Edit [`config/filter.json`](config/filter.json):

| Key | Meaning |
| --- | --- |
| `source_urls` | Upstream config URLs, tried in order. |
| `priority_keys` | Keys moved to just after the first site, in this order. |
| `name_keywords` | Substrings matched against site/live names. |
| `api_patterns` | Regexes matched against `sites[].api`. |
| `keep_sites` | Sites that should stay even if they use `Cloud-drive`. |
| `strip_cloud_drive_from_kept` | Remove the `Cloud-drive` key from kept sites. |
| `filter_lives` | Also apply name keywords to the `lives` array. |

`priority_keys` currently moves `文采` and `奶酪` to positions 2 and 3,
immediately after the first site (`点我切源`), so they appear first in search.

After editing, push to `main`; the workflow runs automatically and commits the
new `tvbox.json` and `filter-report.json`.

## How it works

The upstream 饭太硬 endpoint returns an HTML page to browsers, but returns a
JPEG/BMP image to TVBox's OkHttp client. The actual JSON is Base64-encoded and
appended to that image. `scripts/filter_tvbox.py`:

1. requests the endpoint with an OkHttp User-Agent;
2. detects plain JSON, image + Base64 JSON, or plain Base64;
3. tolerates `//` comments and trailing commas used by TVBox configs;
4. filters `sites` (and optionally `lives`) and applies priority ordering;
5. writes valid, pretty-printed `tvbox.json`.

If the upstream is temporarily unreachable or changes format, the workflow job
fails and the last successfully generated `tvbox.json` stays in place.

## Run locally

No third-party Python packages are required.

```powershell
# Fetch from the default upstream and write tvbox.json
python scripts/filter_tvbox.py

# Test against a saved response
python scripts/filter_tvbox.py --input .\saved-response.bmp --output tvbox.json

# Override the source URL
python scripts/filter_tvbox.py --source "https://example.com/tvbox.json"
```

## Enable GitHub Pages (optional)

1. Open **Settings → Pages**.
2. Under **Build and deployment**, choose **Deploy from a branch**.
3. Select branch `main` and folder `/ (root)`, then save.
4. The JSON becomes available at
   `https://eufine.github.io/tvbox-json/tvbox.json`.

The repository contains a `.nojekyll` file, so Pages serves the JSON directly.

## Notes

- The repository must be **public** for TVBox to fetch the raw URL without a
  token.
- The upstream configuration is maintained by a third party. Its address or
  encoding can change without notice; update `config/filter.json` if that
  happens.
- GitHub automatically disables scheduled workflows after 60 days without
  repository activity. If the config has not changed for a long time, open the
  **Actions** tab and re-enable the workflow (or run it manually).
- This project only filters the source list. It does not host, proxy, or
  endorse the upstream content.
