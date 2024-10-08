---
title: OpenBSD DNS Server with Unbound and Statistics
date: 2024-07-20
description: |-
  Using OpenBSD as a caching recursive DNS resolver with statistics and log
  collection.
tags:
  - OpenBSD
  - network
  - pf
  - unbound
  - dns
---

## DNS Servers

Your network's DNS is delegated to two public Internet resolvers. I've used
Google Public DNS here purely to illustrate how to configure the DNS servers
used by your LAN, but you could easily use quad9 or Cloudflare or any other
public DNS service. Some people may have various privacy concerns with these
companies, or with any public DNS service, and some people may have more
extensive home networking needs that justify internal DNS servers (like running
a [Jellyfin media server](https://jellyfin.org/)).

If this is you, you could run your own caching recursive DNS resolver! You can
do this on the gateway device, but it's best if you can spare at least two other
servers. They don't need to be nearly as powerful as the gateway device, I'm
running mine on two Raspberry Pi 4b units with 2GB RAM, and they're doing quite
well.

Once again, OpenBSD offers a great solution. The
[`unbound(8)`](https://man.openbsd.org/unbound) DNS server is provided with
OpenBSD, is fairly simple to configure, and can function as both an internal
name server and a caching recursive resolver for public Internet DNS. I'll also
enable DNSSEC validation.

Note that you could also use [`nsd(8)`](https://man.openbsd.org/nsd) as
the authoritative name server for your network. This works well for some people.
I've chosen not to do this because I want the same DNS names internally and
externally for accessing self-hosted services and that's much easier to do with
only [`unbound(8)`](https://man.openbsd.org/unbound).

### Why run your own DNS server?

It's reasonable to ask why would you want to run your own DNS server anyway. The
most immediate benefit you'll notice is query speed. Consider the flow for
resolving the IP address for `jgoguen.ca`:

- Your client sends a query to the locally configured DNS server asking for the
  IP address of `jgoguen.ca`.
- If the local DNS server has that IP address cached, the IP address is returned
  immediately. Otherwise, it has to go get that information.
- The local DNS server sends a query for the IP address for `jgoguen.ca` to one
  of the root name servers in `root.hints`.
- The root server replies with a referral to the TLD name servers for `.ca`.
- The local DNS server sends a query for the IP address for `jgoguen.ca` to one
  of the `.ca` TLD name servers.
- The TLD name server replies with a referral to the name servers authoritative
  for `jgoguen.ca`.
- The local DNS server sends a query for the IP address for `jgoguen.ca` to the
  authoritative name server.
- The authoritative name server replies with the IP address for `jgoguen.ca`.
- The local DNS server replies with the IP address for `jgoguen.ca`.

That's a fair bit of work just to resolve one domain name, and your devices will
send out many thousands of DNS queries during a normal day. Every single query
has to go out to the public Internet and back. It's reasonable to expect
a single packet to take tens, or possibly even hundreds, of milliseconds to
travel to a public Internet server, and a similar time for the reply to come
back. That's not considering the time taken to actually resolve the query, which
itself can be tens or hundreds of milliseconds. On your local network, it's
reasonable to expect packet travel times to be low single digit milliseconds
instead. Although it may not seem like much, saving a whole order of magnitude
on each query can actually make anything depending on DNS name resolution feel
faster. A local DNS server can spend the time to resolve queries once and serve
results from the cache in typically under a millisecond.

There's also a security benefit. With a local DNS server, you can intercept
queries for known malicious or advertising domains and reply with &quot;no such
server exists&quot;. Considering how many ad servers are repeatedly compromised to
serve malicious software, or are tricked into linking to malicious URLs, ad
blocking has become a critical part of basic Internet security.

Please keep in mind that advertising is how many sites pay their bills. Although
much less common these days, there are still some advertising companies that can
serve ads in a privacy-preserving way. Blocking ads by default is always the
best option, but if you find a site you enjoy using please do some research into
how they run their ads. If you're satisfied that they're serving ads in a manner
sufficiently private for you, and that they take adequate measures to prevent
and remove any malicious advertising, please consider excluding that site from
your ad blocking. For sites that you enjoy using invasive advertising or ad
servers that don't adequately protect against malicious ads, contact the
webmaster to encourage them to seek out a better alternative.

## DNS Server Preparation

Every good server begins with a good firewall configuration. A minimal
[`pf.conf(5)`](https://man.openbsd.org/pf.conf) suitable for a DNS server:

```pf
if_lan = "bse0"

set block-policy return
set skip on lo0
set reassemble yes no-df
set loginterface egress

match in all scrub (no-df random-id reassemble tcp)

# Port build user does not need network
block drop out log quick proto {tcp udp} user _pbuild

# Control inbound traffic, allow outbound by default
block return all
pass out modulate state

# Allow inbound DHCPv4
pass in on $if_lan inet proto udp from port bootps to port bootpc
pass out on $if_lan inet proto udp from port bootpc to port bootps

# Allow inbound ICMP
pass in on $if_lan inet proto icmp to ($if_lan)
pass in on $if_lan inet6 proto icmp6 to { ($if_lan) ff02::1/16 fe80::/10 }

# Allow inbound SSH
pass in on $if_lan proto tcp from ($if_lan:network) to ($if_lan) port 22 \
	modulate state

# Allow inbound DNS
pass in on $if_lan proto { tcp udp } from ($if_lan:network) to ($if_lan) \
	port 53 modulate state
pass in on $if_lan inet6 proto { tcp udp } from fe80::/10 to port 53 \
	modulate state
```

Note that this `pf.conf` allows all ICMP and ICMP6. According to RFC 4890
you are supposed to filter ICMP unless there's a good reason not to. For this
DNS server setup, it will be on a controlled network where ICMP is already being
properly filtered at the edge. To see what filtering is being done, or to copy
the full set of proper filtering rules to include here, see my
[home network gateway post]({{%relref "openbsd-home-gateway-att"%}}).

Validate and enable the new ruleset:

```sh
% doas pfctl -nf /etc/pf.conf
% doas pfctl -F rules -f /etc/pf.conf
```

### IP Address Setup

DNS servers, by their nature, require a static IP address. You can edit the
relevant [`hostname.if(5)`](https://man.openbsd.org/hostname.if) file to set a
static IP address for the DNS server, or edit
[`dhcpd.conf(5)`](https://man.openbsd.org/dhcpd.conf) on the DHCP server to set
a `fixed-address` for the DNS server's MAC address.

If you decide to use [`hostname.if(5)`](https://man.openbsd.org/hostname.if):

```sh
% cat /etc/hostname.bse0
inet 10.0.0.5 255.255.255.0 10.0.0.255
inet6 fd01:2345:6789:abcd::5 64
up
```

If you decide to use DHCP, add these lines (one block per DNS server) to the
`subnet` block in [`dhcpd.conf(5)`](https://man.openbsd.org/dhcpd.conf):

```conf
host dns1 {
  hardware ethernet 12:34:56:78:9a:bc;
  fixed-address 10.0.0.0.5;
}
```

Validate and restart [`dhcpd(8)`](https://man.openbsd.org/dhcpd):

```sh
% doas dhcpd -n && doas rcctl restart dhcpd
```

You will still need to set a static IPv6 address in the relevant
[`hostname.if(5)`](https://man.openbsd.org/hostname.if) file if you plan to
advertise the DNS servers over IPv6. Make sure your DNS servers are accessible
at their static addresses before proceeding!

## `unbound(8)` Configuration

Set up [`unbound.conf(5)`](https://man.openbsd.org/unbound.conf):

```sh
% cat /var/unbound/etc/unbound.conf
server:
  # Default is to deny queries. Use `access-control` to allow clients (defined
  # by CIDR or specific address) to send DNS queries.
  access-control: 192.168.0.0/16 allow
  access-control: 172.16.0.0/12 allow
  access-control: 10.0.0.0/8 allow
  access-control: fe80::/10 allow
  access-control: fc00::/7 allow
  access-control: 127.0.0.0/8 allow
  access-control: ::1/128 allow

  # Some extra privacy
  hide-identity: yes
  hide-version: yes
  qname-minimisation: yes

  # Listen on all interfaces/addresses
  interface: 0.0.0.0
  interface: ::

  # Allow replies for LAN zones to contain RFC1918 addresses.
  insecure-lan-zones: yes
  private-address: 10.0.0.0/8
  private-address: 172.16.0.0/12
  private-address: 192.168.0.0/16
  private-domain: example.lan

  # Threads should be no more than the number of available CPU cores
  num-threads: 4

  # Configure the DNS cache, to speed up requests for frequently requested
  # domains. Cache slabs should be a power of 2 and no more than the number of
  # threads.
  # rrset-cache-size should be about double msg-cache-size.
  cache-max-ttl: 604800
  cache-min-ttl: 1800
  infra-cache-numhosts: 100000
  infra-cache-slabs: 4
  key-cache-slabs: 4
  msg-cache-size: 128m
  msg-cache-slabs: 4
  rrset-cache-size: 256m
  rrset-cache-slabs: 4
  # Attempt to prefetch oft-requested names before their cache entry expires
  prefetch: yes

  # Configure port and socket options
  outgoing-range: 200
  # On a Linux system, you want so-reuseport set to yes. On OpenBSD, that will
  # actually prevent more than one thread from being used.
  so-reuseport: no
  so-rcvbuf: 2m
  so-sndbuf: 2m

  use-syslog: yes

# Allow using unbound-control(8)
remote-control:
  control-enable: yes
  control-interface: /var/run/unbound.sock
```

If you want to have DNS resolution for LAN servers, it is easiest to add them to
a separate file and include it. Add this line to the `server` section:

```conf
include: /var/unbound/etc/lan-hosts.conf
```

Add LAN client info:

```conf
cat /var/unbound/etc/lan-hosts.conf
# Change 'static' to 'typetransparent' if you want to use the same DNS names
# internally and externally, but not all DNS names will be given here.
local-zone: "example.lan." static
local-data: "gateway.example.lan IN A 10.0.0.1"
local-data: "dns1.example.lan. IN A 10.0.0.5"
local-data: "dns2.example.lan. IN A 10.0.0.6"
```

Validate, enable, and start [`unbound(8)`](https://man.openbsd.org/unbound):

```sh
% doas unbound-checkconf
% doas rcctl enable unbound
% doas rcctl start unbound
```

You should be able to query a public DNS name and get a response:

```sh
% dig www.google.com @::1
```

### Get the root hints file

To resolve DNS queries, unbound needs to know where the root name servers are.
Unbound does have a list in its code, but things can change so we want to keep
an updated list available. First, get the root hints file and put in in place:

```sh
% doas ftp -o /var/unbound/etc/root.hints https://www.internic.net/domain/named.root
```

Then add this line to the `server` section in
[`unbound.conf(5)`](https://man.openbsd.org/unbound.conf):

```conf
root-hints: "/var/unbound/etc/root.hints"
```

Validate and restart [`unbound(8)`](https://man.openbsd.org/unbound):

```sh
% doas unbound-checkconf
% doas rcctl restart unbound
```

### Enable DNSSEC

Now that you can resolve host names using your new DNS server, it's time to add
DNSSEC validation. This gives you confidence that, for domains that use DNSSEC,
the query results have not been tampered with. Few sites are using DNSSEC today,
but by configuring DNSSEC validation now you'll automatically take advantage of
it when more sites do start using it.

First, create `/var/unbound/etc/root.key` and allow the `_unbound` user to
write to it:

```sh
% doas install -o _unbound -g _unbound -m 0644 /var/unbound/etc/root.key
```

Edit the file to add this line:

```bind
. IN DS 38696 8 2 683D2D0ACB8C9B712A1948B27F741219298D0A450D612C483AF444A4C0FB2B16
```

This is the correct hash for the 2024-07-18 root key. Unbound will update this
file later with the right `DNSKEY` record. You can check
[`root-anchors.xml`](https://data.iana.org/root-anchors/root-anchors.xml) to get
the most recent hash and key tag. You only have to do this the first time you
set up DNSSEC, unbound will keep this file up to date as part of its normal
operation. Just tell unbound where to find it by adding this line to the
`server` section of [`unbound.conf(5)`](https://man.openbsd.org/unbound.conf):

```conf
auto-trust-anchor-file: "/var/unbound/etc/root.key"
```

Validate and restart [`unbound(8)`](https://man.openbsd.org/unbound):

```sh
% doas unbound-checkconf
% doas rcctl restart unbound
```

Now check if you can resolve a domain using DNSSEC and validate it. Using
[`dig(1)`](https://man.openbsd.org/dig), the `flags:` line will include flag
`ad` if DNSSEC is present and valid, and `+dnssec` will cause a `RRSIG` record
to be included in the reply:

```sh
% dig jgoguen.ca +dnssec @::1
; <<>> dig 9.10.8-P1 <<>> jgoguen.ca +dnssec @::1
;; global options: +cmd
;; Got answer:
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 65483
;; flags: qr rd ra ad; QUERY: 1, ANSWER: 5, AUTHORITY: 0, ADDITIONAL: 1

;; OPT PSEUDOSECTION:
; EDNS: version: 0, flags: do; udp: 1232
;; QUESTION SECTION:
;jgoguen.ca.                    IN      A

;; ANSWER SECTION:
jgoguen.ca.             1461    IN      A       185.199.110.153
jgoguen.ca.             1461    IN      A       185.199.111.153
jgoguen.ca.             1461    IN      A       185.199.109.153
jgoguen.ca.             1461    IN      A       185.199.108.153
jgoguen.ca.             1461    IN      RRSIG   A 13 2 300 20241008004809 20241005224809 34505 jgoguen.ca. m8ACnfzVqiOkyKb3ubRDhTqMIj/2TvrUxjgVKbCiCI/GY5tqrh8Daldq r5SUwrUd+qHQK4yyyJFfpaTdCsffag==

;; Query time: 0 msec
;; SERVER: ::1#53(::1)
;; WHEN: Sun Oct 06 19:53:48 EDT 2024
;; MSG SIZE  rcvd: 209
```

Note the last line, which contains the value of the `RRSIG` record. Compare to
a domain not using DNSSEC:

```sh
% dig google.com +dnssec @::1

; <<>> dig 9.10.8-P1 <<>> google.com +dnssec @::1
;; global options: +cmd
;; Got answer:
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 61465
;; flags: qr rd ra; QUERY: 1, ANSWER: 6, AUTHORITY: 0, ADDITIONAL: 1

;; OPT PSEUDOSECTION:
; EDNS: version: 0, flags: do; udp: 1232
;; QUESTION SECTION:
;google.com.                    IN      A

;; ANSWER SECTION:
google.com.             1800    IN      A       172.253.124.101
google.com.             1800    IN      A       172.253.124.139
google.com.             1800    IN      A       172.253.124.138
google.com.             1800    IN      A       172.253.124.100
google.com.             1800    IN      A       172.253.124.113
google.com.             1800    IN      A       172.253.124.102

;; Query time: 32 msec
;; SERVER: ::1#53(::1)
;; WHEN: Sun Oct 06 20:00:36 EDT 2024
;; MSG SIZE  rcvd: 135
```

## Make the rest of the LAN use unbound

To make LAN clients use your new DNS servers, edit
[`dhcpd.conf(5)`](https://man.openbsd.org/dhcpd.conf) and change the
`domain-name-servers` option to the IP addresses of your DNS servers. Restart
[`dhcpd(8)`](https://man.openbsd.org/dhcpd) after validating its configuration
file, keeping in mind that it could take up to the length of the existing DHCP
lease for clients to pick up the new DNS servers:

```sh
% doas dhcpd -n && doas rcctl restart dhcpd
```

If you're going to advertise the DNS servers over IPv6, also edit
[`rad.conf(5)`](https://man.openbsd.org/rad.conf) to add a `dns` block:

```conf
dns {
  nameserver {
    fd01:2345:6789:abcd::5
    fd01:2345:6789:abcd::6
  }
}
```

Validate and restart [`rad(8)`](https://man.openbsd.org/rad):

```sh
% doas rad -n && doas rcctl restart rad
```

## Add Monitoring

This is another point where you may be satisfied with what you have set up
already. If so, you can safely stop reading here, or carry on to see if you want
to continue. For this next section, we'll add monitoring of the DNS service.

### Pre-requisites: Monitoring software

This section assumes you have [Grafana](https://grafana.com/),
[InfluxDB 2](https://www.influxdata.com/), and
[Telegraf](https://www.influxdata.com/time-series-platform/telegraf/) already
installed and working. A few configuration details you'll want to include
(or have equivalents set up):

For InfluxDB, make sure you already have an organization, bucket, and a token
with write access to the bucket.

For Telegraf, a minimal `telegraf.conf`:

```conf
[[inputs.socket_listener]]
    service_address = "udp://:9456"
    data_format = "collectd"
    collectd_auth_file = "/etc/telegraf/collectd_auth"
    collectd_security_level = "encrypt"
    collectd_typesdb = ["/usr/share/collectd/types.db"]
    collectd_parse_multivalue = "split"

[[outputs.influxdb_v2]]
    urls = ["https://influxdb:8086"]
    token = "@{secrets:influxdb_dns_bucket_token}"
    organization = "myorg"
    bucket = "dns"
```

### `unbound.conf(5)` changes

Add these configuration items to the `server` block in your existing
[`unbound.conf(5)`](https://man.openbsd.org/unbound.conf):

```conf
log-local-actions: yes
log-queries: no
log-replies: yes
statistics-cumulative: yes
statistics-inhibit-zero: no
statistics-interval: 30
val-log-level: 2
```

Validate and restart [`unbound(8)`](https://man.openbsd.org/unbound):

```sh
% doas unbound-checkconf
% doas rcctl restart unbound
```

Unbound will now print statistics to the output log every 30 seconds.

### DNS Statistics

[`unbound(8)`](https://man.openbsd.org/unbound) is now recording statistics,
which are sent to `/var/log/daemon` every 30 seconds. To be useful, we need to
get those statistics to InfluxDB. To do so, we'll use a Go binary to listen to
syslog generated by unbound. On an OpenBSD host with a Go compiler (ideally not
a server, but if that's needed just remove the compiler later) fetch and compile
the binary, moving it into place after building:

```sh
% ftp -o unbound2influx.tar.gz https://vcs.jgoguen.ca/jgoguen/unbound2influx/archive/main.tar.gz
% tar zxf unbound2influx.tar.gz
% cd unbound2influx
% go build -o bin/unbound2influx
% doas install -o root -g _syslogd -m 0510 bin/unbound2influx /usr/local/bin/unbound2influx
```

You need to create `/etc/unbound2influx.json` to tell the program where to send
data. Because this file will contain a token, it needs to be adequately
protected. Create a file:

```sh
doas install -o root -g _syslogd -m 0640 /dev/null /etc/unbound2influx.json
```

Follow <https://vcs.jgoguen.ca/jgoguen/unbound2influx#configuration> to fill in
the file correctly. Once done, update
[`syslog.conf(5)`](https://man.openbsd.org/syslog.conf) to add these lines to
the bottom of the file:

```conf
!unbound
*.* |/usr/local/bin/unbound2influx
```

Restart [`syslogd(8)`](https://man.openbsd.org/syslogd):

```sh
% doas rcctl restart syslogd
```

DNS logs and statistics will be sent to InfluxDB as they're written to syslog.

## Malicious/Ad Host Blocking

The way the Internet is today, ad blocking is practically a fundamental security
requirement. Many third-party ad services (and some first-party ad services) are
regularly used to serve malicious ads or malicious content along with legitimate
ads. There's also a lot to be concerned about regarding privacy online the way
ad networks operate. You can't stop all ads everywhere, but let's set up
[`unbound(8)`](https://man.openbsd.org/unbound) to block what we can while
you're at home. Far from blocking only ads, the same techniques used to block
ads are used to prevent communication with known malicious domains.

To handle blocking malicious domains, we'll use a Go binary to periodically
fetch lists of known malicious and ad servers. A configuration file for
[`unbound(8)`](https://man.openbsd.org/unbound) will be updated as needed.

First, fetch and compile the `unbound-adhosts` binary:

```sh
% ftp -o unbound-adhosts.tar.gz https://vcs.jgoguen.ca/jgoguen/unbound-adhosts/archive/main.tar.gz
% tar zxf unbound-adhosts.tar.gz
% cd unbound-adhosts
% go build -o bin/unbound-adhosts
% doas install -o root -g _unbound -m 0510 bin/unbound-adhosts /usr/local/bin/unbound-adhosts
```

You need to create a cache directory, allowlist and blocklist files, and a
configuration file:

```sh
% doas install -o root -g _unbound -m 0770 -d /var/cache/unbound-adhosts
% doas install -o root -g _unbound -m 0640 /dev/null /etc/unbound-adhosts.json
% doas install -o root -g wheel -m 0664 /dev/null /var/unbound/allowlist.txt
% doas install -o root -g wheel -m 0664 /dev/null /var/unbound/blocklist.txt
```

`allowlist.txt` holds domain names, one per line, that are to be excluded from
blocking. Any domain and all subdomains will not be added to the final
blocking configuration. `blocklist.txt` holds domain names, also one per line,
that will always be added to the set of domains to block. In case of a conflict
at the same or higher domain level, `allowlist.txt` will prevail.

Follow <https://vcs.jgoguen.ca/jgoguen/unbound-adhosts#configuration> to fill in
the configuration file correctly. Four different filter formats are accepted, so
you can make use of almost any server list you find on the Internet. For
reference, or a starting point, my configuration has these entries for blocking
and allowlist:

```json
{
  "allowlist": {
    "adguard_filters": [],
    "bare_domain_filters": [
      "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/whitelist-referral.txt",
      "https://raw.githubusercontent.com/anudeepND/whitelist/master/domains/whitelist.txt",
      "https://raw.githubusercontent.com/anudeepND/whitelist/master/domains/referral-sites.txt"
    ],
    "hostfile_filters": [],
    "unbound_filters": []
  },
  "blocklist": {
    "adguard_filters": [
      "https://adguardteam.github.io/AdGuardSDNSFilter/Filters/filter.txt",
      "https://adguardteam.github.io/HostlistsRegistry/assets/filter_6.txt",
      "https://adguardteam.github.io/HostlistsRegistry/assets/filter_7.txt",
      "https://adguardteam.github.io/HostlistsRegistry/assets/filter_10.txt",
      "https://adguardteam.github.io/HostlistsRegistry/assets/filter_11.txt",
      "https://adguardteam.github.io/HostlistsRegistry/assets/filter_30.txt",
      "https://adguardteam.github.io/HostlistsRegistry/assets/filter_31.txt",
      "https://adguardteam.github.io/HostlistsRegistry/assets/filter_47.txt",
      "https://raw.githubusercontent.com/AdguardTeam/AdguardFilters/master/FrenchFilter/sections/adservers.txt",
      "https://raw.githubusercontent.com/AdguardTeam/AdGuardSDNSFilter/gh-pages/Filters/adguard_popup_filter.txt",
      "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/gambling.txt",
      "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/fake.txt",
      "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/spam-tlds-adblock.txt",
      "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/hoster.txt"
    ],
    "bare_domain_filters": [
      "https://www.stopforumspam.com/downloads/toxic_domains_whole.txt",
      "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/doh.txt",
      "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/tif.txt",
      "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/native.winoffice.txt",
      "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/native.amazon.txt",
      "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/native.apple.txt",
      "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/native.lgwebos.txt",
      "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/native.tiktok.txt",
      "https://raw.githubusercontent.com/JGeek00/adguard-home-lists/main/lists/lg-webos-ads.txt"
    ],
    "hostfile_filters": [
      "https://adaway.org/hosts.txt",
      "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
      "https://pgl.yoyo.org/adservers/serverlist.php?showintro=0;hostformat=hosts",
      "https://raw.githubusercontent.com/Sekhan/TheGreatWall/master/TheGreatWall.txt",
      "https://www.github.developerdan.com/hosts/lists/dating-services-extended.txt",
      "https://www.github.developerdan.com/hosts/lists/hate-and-junk-extended.txt",
      "https://raw.githubusercontent.com/davidonzo/Threat-Intel/master/lists/latestdomains.piHole.txt",
      "https://adguardteam.github.io/HostlistsRegistry/assets/filter_9.txt",
      "https://adguardteam.github.io/HostlistsRegistry/assets/filter_23.txt",
      "https://someonewhocares.org/hosts/hosts"
    ],
    "unbound_filters": [
      "https://malware-filter.gitlab.io/malware-filter/phishing-filter-unbound.conf",
      "https://malware-filter.gitlab.io/malware-filter/urlhaus-filter-unbound.conf"
    ]
  }
}
```

You can run it now to verify everything works. By default, the script will write
out the domain list at `/var/unbound/etc/unbound-adhosts.conf`.

```sh
% doas /usr/local/bin/unbound-adhosts update
```

Inspect `/var/unbound/etc/unbound-adhosts.conf` to see the final result. Once
you're satisfied, add a line to the end of the `server` section in
[`unbound.conf(5)`](https://man.openbsd.org/unbound.conf):

```conf
include: /var/unbound/etc/unbound-adhosts.conf
```

Validate and restart [`unbound(8)`](https://man.openbsd.org/unbound) to
verify everything works so far:

```sh
% doas unbounb-checkconfig
% doas -u _unbound unbound-control reload_keep_cache
% host ad-feeds.com ::1
Using domain server:
Name: ::1
Address: ::1#53
Aliases:

Host ad-feeds.com not found: 3(NXDOMAIN)
```

Finally, add an entry to `_unbound`'s crontab to run this periodically:

```sh
% doas -u _unbound crontab -e
```

Add this line:

```crontab
11~20	*/6	*	*	*	/usr/local/bin/unbound-adhosts update
```

Every 6 hours, somewhere between minutes 11-20, your block config will be
updated. If you want [`unbound(8)`](https://man.openbsd.org/unbound) reloaded
automatically, you can either add `--reload` to the cron entry or set `reload`
to `true` in `/etc/unbound-adhosts.json`.

This will also give you stats on blocked domains. Because blocked domains reply
with `NXDOMAIN`, the Grafana dashboard assumes that any `NXDOMAIN` is a blocked
request. While not strictly correct, the number of false positives is most
likely extremely low compared to the number of blocked requests and probably
won't impact the percentage values noticeably.

## Pretty Graphs

Now that you have DNS data going to InfluxDB, it's time to do something useful
with it. You can [download the JSON](/grafana/unbound-dashboard.json) for
a Grafana dashboard, or use the screenshots below for reference.

{{< figure src="/img/grafana/unbound-grafana-1.png" link="/img/grafana/unbound-grafana-1.png" alt="Grafana dashboard showing information about queries, response time, and cache status" width="33%" class="inline" target="_blank" >}}
{{< figure src="/img/grafana/unbound-grafana-2.png" link="/img/grafana/unbound-grafana-2.png" alt="Grafana dashboard showing details about top queried and blocked domains and query types" width="33%" class="inline" target="_blank" >}}

Having graphs for your data helps you see at a glance how your DNS servers are
performing and where any problems might be.

## What's Next?

At this point, take a look at
[Awesome Self-hosted](https://awesome-selfhosted.net/) and see if there are any
services you want to host on your LAN. You have internal DNS to reach them by
local IP, and your
[OpenBSD gateway](/posts/2024/07/16/openbsd-ipv6-home-internet-gateway-with-att-fibre/)
allows you to easily forward traffic to an internal web server (or you can run
[`relayd(8)`](https://man.openbsd.org/relayd) to forward to different places
based on different criteria). Just be careful about what you host and how you
configure the servers and services, your OpenBSD firewall can protect you from
a lot, but it can't protect you from a misconfiguration.

## Changelog

- 2024-10-08:
  + The Grafana dashboard has been updated to the latest version in use.
  + Script links have been replaced with links to Go source to compile binaries.
  + Added a section on why you might want to run your own DNS server.
  + Added directions to enable DNSSEC validation and initializing the root key.
  + Added directions for getting the root hints file from upstream.
  + [`pf.conf`](https://man.openbsd.org/pf.conf) has been tweaked slightly,
    mostly for display.
  + Minor wording updates for clarity throughout.
