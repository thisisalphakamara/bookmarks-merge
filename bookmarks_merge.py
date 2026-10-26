#!/usr/bin/env python3
"""bookmarks-merge: merge cross-browser bookmark HTML exports into one file.

Every major browser exports bookmarks as Netscape-format HTML, so moving
between browsers (or restoring an old export alongside a new one) leaves
overlapping files full of duplicate links and half-empty folders. This
tool merges any number of those exports into a single Netscape HTML file
that re-imports cleanly into Chrome, Firefox, Safari and Edge.

Zero dependencies, Python 3.9+. Parsing uses html.parser only, so
malformed exports (unclosed tags, stray text, missing DL wrappers) are
handled gracefully instead of crashing the merge.

Merge rules, in order:

  Folders merge by path. Folder names match case-insensitively, so
  "News" and "news" become one folder, keeping the first-seen spelling
  and the first-seen order of folders and bookmarks.
  Bookmarks dedupe by normalized URL (see normalize_url). The first
  occurrence wins, unless --keep-newest is given, in which case a later
  duplicate with a newer ADD_DATE replaces the kept title and date
  (the position stays where the first occurrence was).
  --flatten drops folders left empty after the merge.
  --sort sorts folders and bookmarks alphabetically (case-insensitive,
  folders before bookmarks) at every level after the merge.
  --check validates the exports and reports duplicate counts. It exits
  1 when duplicates are found or when any input fails to parse, and
  writes no output file.

URL normalization rules (documented, because they decide what counts
as a duplicate):

  Scheme and host are lowercased.
  Default ports are dropped (80 for http, 443 for https).
  A trailing slash on the path is dropped, including a bare root slash,
  so https://example.com/ and https://example.com are the same link.
  Tracking query parameters are dropped: any parameter whose name
  starts with "utm_" plus "fbclid" and "gclid". Remaining parameters
  are sorted, so reordered query strings still match.
  The fragment is kept: #section links point at different content.
  "www." is NOT stripped: www.example.com and example.com can serve
  different content, so they stay distinct bookmarks.
"""

import argparse
import html
import json
import sys
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlparse, urlunparse, urlencode

VERSION = "1.0.0"

TRACKING_PARAMS = ("fbclid", "gclid")


class Bookmark:
    """One bookmark: a URL, a title, and an optional ADD_DATE."""

    def __init__(self, url, title="", add_date=None):
        self.url = url
        self.title = title
        self.add_date = add_date

    def __repr__(self):
        return "Bookmark(%r, %r)" % (self.url, self.title)


class Folder:
    """A folder node. The root folder has name None."""

    def __init__(self, name=None, add_date=None):
        self.name = name
        self.add_date = add_date
        self.children = []

    def __repr__(self):
        return "Folder(%r, %d children)" % (self.name, len(self.children))


def normalize_url(url):
    """Normalize a URL for duplicate detection.

    Returns the normalized URL string, or the stripped input unchanged
    when it cannot be parsed as an absolute URL (relative links and
    non-HTTP schemes such as javascript: are compared verbatim, after
    stripping surrounding whitespace).
    """
    raw = url.strip()
    try:
        parts = urlparse(raw)
    except ValueError:
        return raw
    if not parts.scheme or not parts.netloc:
        return raw
    scheme = parts.scheme.lower()
    host = parts.hostname.lower() if parts.hostname else ""
    port = parts.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = host
    if port:
        netloc = "%s:%d" % (host, port)
    if parts.username:
        auth = parts.username
        if parts.password:
            auth += ":" + parts.password
        netloc = auth + "@" + netloc
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    elif path == "/":
        path = ""
    kept = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lower = key.lower()
        if lower.startswith("utm_") or lower in TRACKING_PARAMS:
            continue
        kept.append((key, value))
    kept.sort()
    query = urlencode(kept)
    return urlunparse((scheme, netloc, path, parts.params, query, parts.fragment))


def _parse_add_date(value):
    """Parse an ADD_DATE attribute to int, or None when absent/invalid."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class _BookmarkParser(HTMLParser):
    """Tolerant Netscape bookmark HTML parser.

    Netscape exports are loose HTML: <p> tags after DL/DT, unclosed
    anchors, folders without a following DL. The parser never raises
    on bad markup; it records what it could not understand in
    self.errors and keeps the partial tree.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Folder()
        self.stack = [self.root]
        self.pending_folder = None
        self.collecting = None
        self.errors = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "dl":
            if self.pending_folder is not None:
                self.stack.append(self.pending_folder)
                self.pending_folder = None
        elif tag == "h3":
            folder = Folder(name="", add_date=_parse_add_date(attrs.get("add_date")))
            self.stack[-1].children.append(folder)
            self.pending_folder = folder
            self.collecting = folder
        elif tag == "a":
            href = attrs.get("href")
            if href is None:
                self.errors.append("A tag without HREF ignored")
                self.collecting = None
                return
            bookmark = Bookmark(
                url=href, add_date=_parse_add_date(attrs.get("add_date"))
            )
            self.stack[-1].children.append(bookmark)
            self.collecting = bookmark
        # dt, p and everything else carry no structure we need.

    def handle_endtag(self, tag):
        if tag == "dl":
            if len(self.stack) > 1:
                self.stack.pop()
        elif tag in ("h3", "a"):
            self.collecting = None

    def handle_data(self, data):
        if self.collecting is None:
            return
        text = data.strip()
        if not text:
            return
        if isinstance(self.collecting, Folder):
            self.collecting.name = (self.collecting.name + " " + text).strip()
        else:
            self.collecting.title = (self.collecting.title + " " + text).strip()

    def error(self, message):
        self.errors.append(message)


def parse_bookmarks(text):
    """Parse Netscape bookmark HTML text into a Folder tree.

    Returns (root, errors). Never raises on malformed input.
    """
    parser = _BookmarkParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:  # HTMLParser is forgiving; stay safe anyway.
        parser.errors.append("parse failed: %s" % exc)
    return parser.root, parser.errors


def count_tree(folder):
    """Return (bookmark_count, folder_count) for a parsed tree."""
    bookmarks = 0
    folders = 0
    for child in folder.children:
        if isinstance(child, Folder):
            folders += 1
            sub_b, sub_f = count_tree(child)
            bookmarks += sub_b
            folders += sub_f
        else:
            bookmarks += 1
    return bookmarks, folders


def _find_folder(parent, name):
    """Find a child folder by case-insensitive name, or None."""
    wanted = name.strip().lower()
    for child in parent.children:
        if isinstance(child, Folder) and child.name.strip().lower() == wanted:
            return child
    return None


def merge_trees(trees, dedup=True, keep_newest=False):
    """Merge parsed trees into one root folder.

    trees is a list of (source_label, root) pairs in input order.
    Returns (merged_root, duplicates) where duplicates is a list of
    dicts describing every dropped (or, with --keep-newest, replaced)
    bookmark.
    """
    merged = Folder()
    seen = {}
    duplicates = []

    def merge_folder(src, dst, path):
        for child in src.children:
            if isinstance(child, Folder):
                existing = _find_folder(dst, child.name)
                if existing is None:
                    existing = Folder(name=child.name, add_date=child.add_date)
                    dst.children.append(existing)
                merge_folder(child, existing, path + (child.name.strip(),))
            else:
                key = normalize_url(child.url) if dedup else None
                if key is not None and key in seen:
                    kept = seen[key]
                    record = {
                        "url": child.url,
                        "normalized_url": key,
                        "folder": "/".join(path) if path else "(root)",
                        "dropped_title": child.title,
                        "kept_title": kept.title,
                    }
                    if (
                        keep_newest
                        and child.add_date is not None
                        and (kept.add_date is None or child.add_date > kept.add_date)
                    ):
                        kept.title = child.title
                        kept.add_date = child.add_date
                        record["kept_updated"] = True
                    duplicates.append(record)
                else:
                    kept = Bookmark(
                        url=child.url, title=child.title, add_date=child.add_date
                    )
                    dst.children.append(kept)
                    if key is not None:
                        seen[key] = kept

    for _label, root in trees:
        merge_folder(root, merged, ())
    return merged, duplicates


def flatten_empty_folders(folder):
    """Drop folders left empty after the merge, recursively.

    Returns the number of folders removed.
    """
    removed = 0
    kept = []
    for child in folder.children:
        if isinstance(child, Folder):
            removed += flatten_empty_folders(child)
            if not child.children:
                removed += 1
                continue
        kept.append(child)
    folder.children = kept
    return removed


def sort_tree(folder):
    """Sort folders and bookmarks alphabetically at every level.

    Case-insensitive. Folders come before bookmarks. Within each group
    the original relative order of equal names is preserved (stable sort).
    Does not change which bookmarks were kept or dropped by the merge.
    """
    for child in folder.children:
        if isinstance(child, Folder):
            sort_tree(child)

    def sort_key(item):
        if isinstance(item, Folder):
            name = (item.name or "").strip().lower()
            return (0, name)
        title = (item.title or "").strip().lower()
        return (1, title)

    folder.children.sort(key=sort_key)


def _escape(text):
    return html.escape(text, quote=True)


def render_html(root):
    """Render a merged tree as Netscape bookmark HTML.

    The output re-imports cleanly: DOCTYPE, a top-level DL, H3 folder
    headers with ADD_DATE, and A entries with HREF and ADD_DATE.
    """
    lines = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        "<!-- This is an automatically generated file.",
        "     It will be read and overwritten.",
        "     DO NOT EDIT! -->",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>Bookmarks</TITLE>",
        "<H1>Bookmarks</H1>",
        "<DL><p>",
    ]

    def render_folder(folder, indent):
        pad = "    " * indent
        for child in folder.children:
            if isinstance(child, Folder):
                date = (
                    ' ADD_DATE="%d"' % child.add_date
                    if child.add_date is not None
                    else ""
                )
                lines.append(
                    "%s<DT><H3%s>%s</H3>" % (pad, date, _escape(child.name))
                )
                lines.append("%s<DL><p>" % pad)
                render_folder(child, indent + 1)
                lines.append("%s</DL><p>" % pad)
            else:
                date = (
                    ' ADD_DATE="%d"' % child.add_date
                    if child.add_date is not None
                    else ""
                )
                lines.append(
                    '%s<DT><A HREF="%s"%s>%s</A>'
                    % (pad, _escape(child.url), date, _escape(child.title))
                )

    render_folder(root, 1)
    lines.append("</DL><p>")
    return "\n".join(lines) + "\n"


def build_report(file_infos, merged, duplicates, flattened):
    """Assemble the report dict shared by --json, --report and text output."""
    merged_b, merged_f = count_tree(merged)
    return {
        "inputs": file_infos,
        "total_input_bookmarks": sum(f["bookmarks"] for f in file_infos),
        "total_input_folders": sum(f["folders"] for f in file_infos),
        "merged_bookmarks": merged_b,
        "merged_folders": merged_f,
        "duplicates_dropped": len(duplicates),
        "duplicates": duplicates,
        "empty_folders_removed": flattened,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="bookmarks-merge",
        description="Merge Netscape bookmark HTML exports into one file.",
    )
    parser.add_argument("inputs", nargs="*", help="bookmark HTML export files")
    parser.add_argument(
        "--out",
        default="merged_bookmarks.html",
        help="merged output file (default: merged_bookmarks.html)",
    )
    parser.add_argument(
        "--dedup",
        action="store_true",
        default=True,
        help="dedupe bookmarks by normalized URL (default: on)",
    )
    parser.add_argument(
        "--no-dedup",
        dest="dedup",
        action="store_false",
        help="keep every bookmark, even exact duplicates",
    )
    parser.add_argument(
        "--keep-newest",
        action="store_true",
        help="when duplicates differ in title, keep the one with the "
        "latest ADD_DATE instead of the first occurrence",
    )
    parser.add_argument(
        "--flatten",
        action="store_true",
        help="drop folders left empty after the merge",
    )
    parser.add_argument(
        "--sort",
        action="store_true",
        help="sort folders and bookmarks alphabetically (case-insensitive, "
        "folders before bookmarks) at every level after the merge",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the exports and report duplicate counts only, "
        "write nothing; exit 1 when duplicates are found or any input "
        "fails to parse",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be written, write nothing",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--report", help="write the report as JSON to this path")
    args = parser.parse_args(argv)

    if not args.inputs:
        parser.print_usage(sys.stderr)
        print("bookmarks-merge: at least one input file is required", file=sys.stderr)
        return 2

    trees = []
    file_infos = []
    parse_failed = False
    for path in args.inputs:
        try:
            text = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError as exc:
            print("bookmarks-merge: cannot read %s: %s" % (path, exc), file=sys.stderr)
            return 2
        root, errors = parse_bookmarks(text)
        bookmarks, folders = count_tree(root)
        if errors:
            parse_failed = True
        trees.append((path, root))
        file_infos.append(
            {
                "path": path,
                "bookmarks": bookmarks,
                "folders": folders,
                "errors": errors,
            }
        )

    merged, duplicates = merge_trees(
        trees, dedup=args.dedup, keep_newest=args.keep_newest
    )
    flattened = flatten_empty_folders(merged) if args.flatten else 0
    if args.sort:
        sort_tree(merged)
    report = build_report(file_infos, merged, duplicates, flattened)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for info in file_infos:
            line = "%s: %d bookmark(s), %d folder(s)" % (
                info["path"],
                info["bookmarks"],
                info["folders"],
            )
            if info["errors"]:
                line += " [%d parse note(s)]" % len(info["errors"])
            print(line)
        print(
            "merged: %d bookmark(s) in %d folder(s), %d duplicate(s) dropped"
            % (
                report["merged_bookmarks"],
                report["merged_folders"],
                report["duplicates_dropped"],
            )
        )
        if flattened:
            print("flattened: %d empty folder(s) removed" % flattened)

    if args.report:
        try:
            with open(args.report, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2)
                handle.write("\n")
        except OSError as exc:
            print(
                "bookmarks-merge: cannot write report %s: %s" % (args.report, exc),
                file=sys.stderr,
            )
            return 2

    if args.check:
        if duplicates or parse_failed:
            return 1
        return 0

    if args.dry_run:
        if not args.json:
            print("dry-run: would write %s" % args.out)
        return 0

    try:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(render_html(merged))
    except OSError as exc:
        print("bookmarks-merge: cannot write %s: %s" % (args.out, exc), file=sys.stderr)
        return 2
    if not args.json:
        print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
