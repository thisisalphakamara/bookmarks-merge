# bookmarks-merge

[![GitHub stars](https://img.shields.io/github/stars/aamirtechs/bookmarks-merge?style=social)](https://github.com/aamirtechs/bookmarks-merge/stargazers) [![GitHub forks](https://img.shields.io/github/forks/aamirtechs/bookmarks-merge?style=social)](https://github.com/aamirtechs/bookmarks-merge/network/members) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/aamirtechs/bookmarks-merge/blob/main/LICENSE) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/aamirtechs/bookmarks-merge/issues)

Merge bookmark HTML exports from Chrome, Firefox, Safari and Edge into
one clean file, with duplicates removed and folders merged. Switching
browsers (or restoring an old export next to a new one) leaves
overlapping exports full of the same links saved twice and half-empty
folders. bookmarks-merge combines them into a single Netscape-format
HTML file that re-imports cleanly into any browser.

Zero dependencies. Python 3.9+.

## Why

Every browser exports the same Netscape bookmark HTML format, but none
of them dedupes on import. Import a Chrome export into Firefox, then a
Firefox export back into Chrome, and every shared link now exists
twice. bookmarks-merge fixes that before import: folders merge by
path, links dedupe by normalized URL, and the result is one file you
can import once and trust. It also doubles as a check: `--check`
exits nonzero when duplicates are present, so a backup routine can
flag a messy export set before it grows.

## Install

```bash
pip install bookmarks-merge
# or just run the single file, it has no dependencies:
python3 bookmarks_merge.py --help
```

## Quick example

Generate the synthetic samples (fake example.com links only, no real
browsing history), then merge them:

```bash
$ python3 examples/make_samples.py
wrote chrome_bookmarks.html, firefox_bookmarks.html

$ python3 bookmarks_merge.py examples/chrome_bookmarks.html examples/firefox_bookmarks.html
examples/chrome_bookmarks.html: 4 bookmark(s), 3 folder(s)
examples/firefox_bookmarks.html: 5 bookmark(s), 2 folder(s)
merged: 6 bookmark(s) in 4 folder(s), 3 duplicate(s) dropped
wrote merged_bookmarks.html

$ python3 bookmarks_merge.py examples/chrome_bookmarks.html examples/firefox_bookmarks.html --check
examples/chrome_bookmarks.html: 4 bookmark(s), 3 folder(s)
examples/firefox_bookmarks.html: 5 bookmark(s), 2 folder(s)
merged: 6 bookmark(s) in 4 folder(s), 3 duplicate(s) dropped
# exit code 1: duplicates were found

$ python3 bookmarks_merge.py examples/chrome_bookmarks.html examples/firefox_bookmarks.html --keep-newest --flatten --dry-run
examples/chrome_bookmarks.html: 4 bookmark(s), 3 folder(s)
examples/firefox_bookmarks.html: 5 bookmark(s), 2 folder(s)
merged: 6 bookmark(s) in 3 folder(s), 3 duplicate(s) dropped
flattened: 1 empty folder(s) removed
dry-run: would write merged_bookmarks.html
```

(All outputs above are captured from real runs on the synthetic samples
in `examples/`.)

The two samples share three links: `news.example.com` saved once with
and once without a trailing slash, a story link saved once with
`utm_source`/`utm_medium` tracking parameters, and a blog post saved
under two different titles. The merge keeps 6 unique links, merges
the `News` and `news` folders into one, and drops the empty folder
only when `--flatten` is given.

## How merging works

Folders merge by path. Folder names match case-insensitively, so
`News` and `news` become one folder, keeping the first-seen spelling
and the first-seen order of folders and bookmarks. Bookmarks dedupe
globally by normalized URL: the first occurrence wins, unless
`--keep-newest` is given, in which case a later duplicate with a newer
ADD_DATE replaces the kept title and date (the position stays where
the first occurrence was). A duplicate dropped from one folder is
still reported in `--json` output with its folder and both titles.

## URL normalization

These rules decide what counts as a duplicate:

| Rule | Example |
| --- | --- |
| Scheme and host lowercased | `HTTP://EXAMPLE.COM` matches `http://example.com` |
| Default ports dropped | `:80` for http, `:443` for https |
| Trailing slash dropped, including a bare root slash | `example.com/` matches `example.com` |
| Tracking parameters dropped | any `utm_*` parameter, plus `fbclid` and `gclid` |
| Remaining query parameters sorted | `?b=2&a=1` matches `?a=1&b=2` |
| Fragment kept | `#top` and `#bottom` stay distinct links |
| `www.` NOT stripped | `www.example.com` and `example.com` stay distinct |

`www.` is kept on purpose: `www.example.com` and `example.com` can
serve different content, so merging them could silently delete a
distinct bookmark. Relative links and non-HTTP schemes (such as
`javascript:`) are compared verbatim after trimming whitespace.

## Flags

| Flag | Meaning |
| --- | --- |
| `--out FILE` | merged output file (default: merged_bookmarks.html) |
| `--dedup` | dedupe by normalized URL (default: on) |
| `--no-dedup` | keep every bookmark, even exact duplicates |
| `--keep-newest` | on a title conflict, keep the duplicate with the latest ADD_DATE |
| `--flatten` | drop folders left empty after the merge |
| `--sort` | sort folders and bookmarks alphabetically (case-insensitive, folders before bookmarks) at every level |
| `--check` | report only, write nothing; exit 1 if duplicates are found or any input fails to parse, else exit 0 |
| `--dry-run` | show what would be written, write nothing |
| `--json` | print the full report as JSON (includes every dropped duplicate) |
| `--report PATH` | write the full report as JSON to a file |

## Limitations

- Parsing is deliberately tolerant: unclosed tags, stray text and
  missing DL wrappers are handled, but a file that is not bookmark
  HTML at all (for example a full web page) will parse to whatever
  links and H3 headings it happens to contain.
- ADD_DATE values that are missing or not numbers are treated as
  unknown; with `--keep-newest`, a duplicate with an unknown date
  never replaces a kept entry.
- Folder icons, toolbar flags and other browser-specific attributes
  are not preserved: the output carries HREF, title and ADD_DATE
  only, which is everything browsers need for a clean re-import.

## Development

```bash
python3 -m pytest tests/ -q
```

## License

MIT. See LICENSE.
