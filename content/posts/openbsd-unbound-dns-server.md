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

Note that you could also use [`nsd(8)`](https://man.openbsd.org/unbound) as the
authoritative nameserver for your network. This works well for some people. I've
chosen not to do this because I want the same DNS names internally and
externally for accessing self-hosted services and that's much easier to do
with only [`unbound(8)`](https://man.openbsd.org/unbound).

### DNS Server Preparation

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

# Allow inbound DHCP
pass in on $if_lan inet proto udp from port bootps to port bootpc
pass out on $if_lan inet proto udp from port bootpc to port bootps
pass in on $if_lan inet6 proto udp from fe80::/10 port dhcpv6-server to fe80::/10 port dhcpv6-client no state
pass out on $if_lan inet6 proto udp from fe80::/10 port dhcpv6-client to fe80::/10 port dhcpv6-server no state

# Allow inbound ICMP
pass in on $if_lan inet proto icmp to ($if_lan)
pass in on $if_lan inet6 proto icmp6 to { ($if_lan) ff02::1/16 fe80::/10 }

# Allow inbound SSH
pass in on $if_lan proto tcp from $if_lan:network to ($if_lan) port 22 modulate state

# Allow inbound DNS
pass in on $if_lan proto { tcp udp } from $if_lan:network to ($if_lan) port 53 modulate state
pass in on $if_lan inet6 proto { tcp udp } from fe80::/10 to port 53 modulate state
```

Validate and enable the new ruleset:

```sh
% doas pfctl -nf /etc/pf.conf
% doas pfctl -F rules -f /etc/pf.conf
```

### IP Address Setup

DNS servers, by their nature, require a static IP address. You can edit the
relevant [`hostname.if(5)`](https://man.openbsd.org/hostname.if) file to set a
static IP address for the DNS server, or edit
[`dhcpd.conf(5)`](https://man.openbsd.org/dhcpd.conf) to set a `fixed-address`
for the DNS server's MAC address.

If you use [`hostname.if(5)`](https://man.openbsd.org/hostname.if):

```sh
% cat /etc/hostname.bse0
inet 10.0.0.5 255.255.255.0 10.0.0.255
inet6 fd01:2345:6789:abcd::5 64
up
```

If you use DHCP, add these lines (one block per DNS server) to the `subnet`
block in [`dhcpd.conf(5)`](https://man.openbsd.org/dhcpd.conf):

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

### `unbound(8)` Configuration

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
  # domains. Cache slabs should be no more than the number of threads.
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
  so-reuseport: yes
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

### Make the rest of the LAN use it

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
[InfluxxDB 2](https://www.influxdata.com/), and
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
extended-statistics: yes
log-local-actions: yes
log-queries: no
log-replies: yes
statistics-cumulative: yes
val-log-level: 2
```

Validate and restart [`unbound(8)`](https://man.openbsd.org/unbound):

```sh
% doas unbound-checkconf && doas rcctl restart unbound
```

### DNS Statistics

[`unbound(8)`](https://man.openbsd.org/unbound) is now recording statistics,
which you can view at any time:

```sh
% doas unbound-control stats_noreset
```

To be useful, we need to get those statistics to InfluxDB. To do so, we'll use
`collectd`. Install it first:

```sh
% doas pkg_add collectd
```

A minimal `collectd.conf`:

```conf
Hostname    "dns2"
LoadPlugin syslog
<Plugin syslog>
  LogLevel info
</Plugin>
LoadPlugin exec
LoadPlugin network
<Plugin exec>
  Exec _collectd "/usr/local/bin/collectd-unbound"
</Plugin>
<Plugin network>
  Server "10.0.0.4" "9456"
</Plugin>
```

To allow `collectd` to run `unbound-control` to get statistics, add this line to
[`doas.conf(5)`](https://man.openbsd.org/doas.conf):

```doas
permit nopass _collectd as _unbound cmd unbound-control args stats_noreset
```

Finally, copy this script to `/usr/local/bin/collectd-unbound`:

```sh
#!/bin/sh

PATH="/bin:/sbin:/usr/bin:/usr/sbin"

HOSTNAME="${COLLECTD_HOSTNAME:-$(hostname -s)}"
INTERVAL="${COLLECTD_INTERVAL:-30}"

while sleep "$INTERVAL"; do
  doas -u _unbound unbound-control stats_noreset | egrep -v "^(histogram\.|time\.now|time\.elapsed)" | sed -re "s;^([^=]+)=([0-9\.]+);PUTVAL $HOSTNAME/exec-unbound/gauge-\1 interval=$INTERVAL N:\2;"

  awk -v h=$HOSTNAME -v i=$INTERVAL 'END { print "PUTVAL " h "/exec-unbound/gauge-num.adhosts interval=" i " N:" FNR }' /var/unbound/etc/unbound-adhosts.conf
  awk -v h=$HOSTNAME -v i=$INTERVAL '!/^($|[:space:]*#)/ { hosts++ } END { print "PUTVAL " h "/exec-unbound/gauge-num.allowlist interval=" i " N:" hosts+0 }' /var/unbound/allowlist.txt
  awk -v h=$HOSTNAME -v i=$INTERVAL '!/^($|[:space:]*#)/ { hosts++ } END { print "PUTVAL " h "/exec-unbound/gauge-num.manual-blocks interval=" i " N:" hosts+0 }' /var/unbound/blocklist.txt
done

exit 0
```

Make sure the script is owned by `root:_collectd` and give it permissions
`0750`.

Enable and start `collectd`, it will shortly start sending statistics to
InfluxDB:

```sh
% doas rcctl enable collectd
% doas rcctl start collectd
```

DNS queries are being logged to [`syslogd(8)`](https://man.openbsd.org/syslogd).
We need to send them to an external program for processing and forwarding to
InfluxDB. I wrote a simple Go program to read, queue, process, and send the
logs. Download [unbound2influx.go](/static/go/unbound2influx.go) and build it:

```sh
% go build -o unbound2influx unbound2influx.go
```

Set permissions and move it into place:

```sh
% doas chown root:_syslogd unbound2influx
% doas chmod 0750 unbound2influx
% doas mv unbound2influx /usr/local/bin/unbound2influx
```

You also need to create `/etc/unbound2influx.cfg` and fill in details. `hosts`
points to the file you use to define LAN addresses, if you haven't done that
give it a path to an empty file:

```sh
% doas cat /etc/unbound2influx.cfg
token = <API token from InfluxDB>
bucket = <bucket name from InfluxDB>
org = <org name from InfluxDB>
hosts = /var/unbound/etc/lan-hosts.conf
influx_host = http://10.0.0.4:8086
```

Edit [`syslog.conf(5)`](https://man.openbsd.org/syslog.conf) to add these lines
to the bottom of the file:

```conf
!unbound
*.* |/usr/local/bin/unbound2influx
```

Restart [`syslogd(8)`](https://man.openbsd.org/syslogd) and logs will start
streaming to InfluxDB as DNS requests are made:

```sh
% doas rcctl restart syslogd
```

### Ad Blocking

The way the Internet is today, ad blocking is practically a fundamental security
requirement. Many third-party ad services (and some first-party ad services) are
regularly used to serve malicious ads or malicious content along with legitimate
ads. There's also a lot to be concerned about regarding privacy online the way
ad networks operate. You can't stop all ads everywhere, but let's set up
[`unbound(8)`](https://man.openbsd.org/unbound) to block what we can while
you're at home.

Start by creating two empty files, `allowlist.txt` and `blocklist.txt`. They can
be anywhere, but the script I wrote defaults to `allowlist.txt` and
`blocklist.txt` in the same directory as the script. The script, which I've
installed at `/var/unbound/unbound-adhosts.py` can be
[downloaded here](/py/unbound-adhosts.py). Set permissions on the files:

```sh
% doas chown root:_unbound unbound-adhosts.py allowlist.txt blocklist.txt
% doas chmod 0755 unbound-adhosts.py
```

You can run it now to verify everything works. By default the script will write
out the domain list at `/var/unbound/unbound-adhosts.conf`.

```sh
% doas /var/unbound/unbound-adhosts.py -v
```

Add a line to the end of the `server` section in
[`unbound(8)`](https://man.openbsd.org/unbound):

```conf
include: /var/unbound/etc/unbound-adhosts.conf
```

You can validate and restart [`unbound(8)`](https://man.openbsd.org/unbound) to
verify everything works so far:

```sh
% doas unbound-control reload_keep_cache
% host ad-feeds.com ::1
Using domain server:
Name: ::1
Address: ::1#53
Aliases:

Host ad-feeds.com not found: 3(NXDOMAIN)
```

Finally, add an entry to `root`'s crontab to run this periodically:

```sh
% doas crontab -e
```

Add this line:

```crontab
11~20	*/6	*	*	*	/var/unbound/unbound-adhosts.py
```

Every 6 hours, somewhere between minutes 11-20, your adblock list will be
updated and [`unbound(8)`](https://man.openbsd.org/unbound) reloaded.

### Pretty Graphs

Now that you have DNS data going to InfluxDB, it's time to do something useful
with it. You can [download the JSON](/grafana/unbound-dashboard.json) for
a Grafana dashboard, or use the screenshots below for reference.

{{< figure src="/img/grafana/unbound-grafana-1.png" link="/img/grafana/unbound-grafana-1.png" alt="Grafana dashboard showing information about queries, response time, and cache status" width="33%" class="inline" target="_blank" >}}
{{< figure src="/img/grafana/unbound-grafana-2.png" link="/img/grafana/unbound-grafana-2.png" alt="Grafana dashboard showing details about top queried and blocked domains and query types" width="33%" class="inline" target="_blank" >}}
{{< figure src="/img/grafana/unbound-grafana-3.png" link="/img/grafana/unbound-grafana-3.png" alt="Grafana dashboard showing a log of recent queries and top clients by query count" width="33%" class="inline" target="_blank" >}}

## What's Next?

At this point, take a look at
[Awesome Self-hosted](https://awesome-selfhosted.net/) and see if there's any
services you want to host on your LAN. You have internal DNS to reach them by
local IP, and your
[OpenBSD gateway](/posts/2024/07/16/openbsd-ipv6-home-internet-gateway-with-att-fibre/)
allows you to easily forward traffic to an internal web server (or you can run
[`relayd(8)`](https://man.openbsd.org/relayd) to forward to different places
based on different criteria). Just be careful about what you host and how you
configure the servers and services, your OpenBSD firewall can protect you from
a lot but it can't protect you from a misconfiguration.
