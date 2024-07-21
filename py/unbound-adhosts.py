#!/usr/bin/env python3
# vim: set expandtab sw=4 ts=4 sts=4 foldmethod=indent filetype=python:

# unbound-adhosts.py
# Copyright 2024 Joel Goguen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from typing import cast

import requests

BUF_SIZE = 65536
ADGUARD_FILTERS = [
    "https://adguardteam.github.io/AdGuardSDNSFilter/Filters/filter.txt",
    "https://raw.githubusercontent.com/AdguardTeam/AdguardFilters/master/FrenchFilter/sections/adservers.txt",
    # Review and make sure there aren't too many hits in valid replies
    # "https://github.com/AdguardTeam/AdGuardSDNSFilter/blob/gh-pages/Filters/adguard_popup_filter.txt",
]
BARE_DOMAIN_FILTERS = [
    "https://www.stopforumspam.com/downloads/toxic_domains_whole.txt",
]
HOSTFILE_FILTERS = [
    "https://adaway.org/hosts.txt",
    "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
    "https://pgl.yoyo.org/adservers/serverlist.php?showintro=0;hostformat=hosts",
    "https://raw.githubusercontent.com/Sekhan/TheGreatWall/master/TheGreatWall.txt",
]
UNBOUND_FILTERS = [
    "https://malware-filter.gitlab.io/malware-filter/urlhaus-filter-unbound.conf",
]


class Args(argparse.Namespace):
    def __init__(self) -> None:
        super(Args, self).__init__()

        self.output = "/var/unbound/etc/unbound-adhosts.conf"
        self.allowlist = f"{os.path.dirname(sys.argv[0])}/allowlist.txt"
        self.blocklist = f"{os.path.dirname(sys.argv[0])}/blocklist.txt"
        self.dry_run = False
        self.verbose = False


def fetch_url(url: str) -> requests.Response | None:
    resp = requests.get(url, allow_redirects=True, stream=True)
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        print(f"Error fetching {url}: {e}")
        return None
    return resp


def parse_adguard_filters() -> set[str]:
    domains: set[str] = set()

    # Lines matched:
    # - ||example.com^
    # - .example.com^
    # - ||.example.com^
    # - example.com^
    domain_re = re.compile(r"^(?:\|{2})?\.?([\w\d_\-\.]+)\^(?:\$dnsrewrite=ad\-block\.dns\.adguard\.com)?$")
    for url in ADGUARD_FILTERS:
        resp = fetch_url(url)
        if resp is None:
            continue
        for line in resp.iter_lines(decode_unicode=True):  # type:ignore[reportAny]
            line = cast(str, line).strip()
            match = domain_re.match(line)
            if match is not None:
                domains.add(match.group(1).rstrip("."))

    return domains


def parse_bare_domain_filters() -> set[str]:
    domains: set[str] = set()

    for url in BARE_DOMAIN_FILTERS:
        resp = fetch_url(url)
        if resp is None:
            continue
        for line in resp.iter_lines(decode_unicode=True):  # type: ignore[reportAny]
            line = cast(str, line).strip()
            domains.add(line.rstrip("."))

    return domains


def parse_hostfile_filters() -> set[str]:
    domains: set[str] = set()

    hostfile_re = re.compile(r"^(?:127|0\.|::)[^\s]+\s+([^\s]+).*$")
    for url in HOSTFILE_FILTERS:
        resp = fetch_url(url)
        if resp is None:
            continue
        for line in resp.iter_lines(decode_unicode=True):  # type: ignore[reportAny]
            line = cast(str, line).strip()
            match = hostfile_re.match(line)
            if match is not None:
                domains.add(match.group(1).rstrip("."))

    return domains


def parse_unbound_filters() -> set[str]:
    domains: set[str] = set()

    unbound_re = re.compile(r'^local\-zone:\s+"([^"]+)"\s+')
    for url in UNBOUND_FILTERS:
        resp = fetch_url(url)
        if resp is None:
            continue
        for line in resp.iter_lines(decode_unicode=True):  # type: ignore[reportAny]
            line = cast(str, line).strip()
            match = unbound_re.match(line)
            if match is not None:
                domains.add(match.group(1).rstrip("."))

    return domains


def parse_args() -> Args:
    args = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ns = Args()

    _ = args.add_argument(
        "-o",
        "--output",
        type=str,
        default=ns.output,
        help="Unbound conf file to replace with the current adhosts domains",
    )
    _ = args.add_argument(
        "-a",
        "--allowlist",
        type=str,
        default=ns.allowlist,
        help="File containing one allowlisted domain per line",
    )
    _ = args.add_argument(
        "-b",
        "--blocklist",
        type=str,
        default=ns.blocklist,
        help="File containing one domain to block per line",
    )
    _ = args.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        default=ns.dry_run,
        help="Do not write the destination file, only report any changes",
    )
    _ = args.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=ns.verbose,
        help="Print messages to indicate progress",
    )

    return args.parse_args(namespace=Args())


def include_domain(needle: str, haystack: Iterable[str]) -> bool:
    # I'd love to know how this gets onto some lists...
    if '"(t.co)"' in needle:
        return False

    # Some entries, for some reason, are in a list and end with '\"'.
    if needle.endswith('"'):
        return False

    for entry in haystack:
        if needle == entry:
            return False

        entry_parts = list(reversed(entry.split(".")))
        needle_parts = list(reversed(needle.split(".")))
        # If the entry is longer than the needle, it can't be a match
        if len(entry_parts) > len(needle_parts):
            continue

        # We know at this point that the needle has at least as many parts as the
        # current haystack entry. Now we can get the sub-list of needle the same length
        # as the current entry parts list (which has been reversed so we're comparing
        # parts starting from the TLD and moving to successive sub-domains); if the
        # lists are equal, the domain should be excluded.
        needle_sublist = needle_parts[:len(entry_parts)]
        if needle_sublist == entry_parts:
            return False

    return True


def main() -> int:
    args = parse_args()

    allowlist: set[str] = set()
    if os.path.exists(args.allowlist):
        if args.verbose:
            print(f"Parsing allowlist {args.allowlist}")
        with open(args.allowlist, "r") as f:
            for line in f:
                if line.startswith("#") or line.strip() == "":
                    continue
                allowlist.add(line.strip().rstrip("."))
        if args.verbose:
            print(f"Allowlist has {len(allowlist)} entries")

    candidate_domains: set[str] = set()
    if os.path.exists(args.blocklist):
        if args.verbose:
            print(f"Parsing blocklist at {args.blocklist}")
        with open(args.blocklist, "r") as f:
            for line in f:
                if line.startswith("#") or line.strip() == "":
                    continue
                candidate_domains.add(line.strip().rstrip("."))
        if args.verbose:
            print(f"Seeding blocklist with {len(candidate_domains)} entries")

    futures: list[Future[set[str]]] = []
    with ProcessPoolExecutor() as executor:
        futures.append(executor.submit(parse_unbound_filters))
        futures.append(executor.submit(parse_adguard_filters))
        futures.append(executor.submit(parse_hostfile_filters))
        futures.append(executor.submit(parse_bare_domain_filters))

    for future in as_completed(futures):
        candidate_domains.update(future.result())

    if args.verbose:
        print(f"Inital adhost list of {len(candidate_domains)} domains")

    ad_domains = sorted(
        {
            domain.rstrip(".")
            for domain in candidate_domains
            if include_domain(domain.rstrip("."), allowlist)
        }
    )

    # It's a lot of ugly-as-sin processing up front, but it's

    if args.verbose:
        print(f"Filtered adhost list of {len(ad_domains)} domains")

    with tempfile.TemporaryDirectory() as d:
        adhost_file = os.path.join(d, "adhosts.txt")
        with open(adhost_file, "w") as f:
            for domain in ad_domains:
                _ = f.write(f'local-zone: "{domain}." always_nxdomain\n')

        new_shasum = hashlib.sha512()
        with open(adhost_file, "r") as f:
            while True:
                data = f.read(BUF_SIZE)
                if not data:
                    break
                new_shasum.update(data.encode())

        old_shasum = hashlib.sha512()
        if os.path.isfile(args.output):
            with open(args.output, "r") as f:
                while True:
                    data = f.read(BUF_SIZE)
                    if not data:
                        break
                    old_shasum.update(data.encode())

        if old_shasum.digest() != new_shasum.digest():
            if not args.dry_run:
                if args.verbose:
                    print(f"Moving {adhost_file} to {args.output}")
                shutil.move(adhost_file, args.output)
                check = subprocess.run(
                    ["doas", "-u", "_unbound", "/usr/sbin/unbound-checkconf"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                if check.returncode != 0:
                    print("unbound config not valid:", file=sys.stderr)
                    print(check.stderr, file=sys.stderr)
                    sys.exit(check.returncode)
                
                reload = subprocess.run(
                    ["doas", "-u", "_unbound", "unbound-control", "reload"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                if reload.returncode != 0:
                    print("failed to restart unbound:", file=sys.stderr)
                    print(reload.stderr, file=sys.stderr)
            else:
                print(f"Dry-run mode: Would update {args.output}, new ad domains found")

    return 0


if __name__ == "__main__":
    sys.exit(main())
