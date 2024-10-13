---
title: OpenBSD IPv6 Home Internet Gateway with AT&T Fibre
date: 2024-07-20
description: |
  Using OpenBSD as a home Internet gateway with AT&T Fibre with IPv6
  support
tags:
  - OpenBSD
  - AT&T
  - network
  - pf
  - IPv6
---

When you sign up for Internet service, one thing you're going to get is a home
gateway. It's going to offer wired and wireless Internet connections plus
a bunch of other things you may not want and probably won't need. Unfortunately,
one of the things it's going to offer you is poor performance. The wireless
network on it may not cover your whole house, and if you're also limited in
where you can hook it up that could mean poor coverage in the most important
areas. It's also not built on the best hardware for the job, so when it counts
the most you may not get the connection speeds you expect. Depending on your
needs, you may find that the ISP hardware doesn't offer you enough flexibility.
And, perhaps most importantly, it's not all that secure, and the firewalls can
be difficult to configure properly.

## Before you start

Before doing anything, decide:

- Are you comfortable installing and configuring a computer (or more) that will
  become a critical part of your home Internet setup?
- Are you comfortable being technical support if something isn't working
  properly?
- Your ISP is unlikely to give you much support, can you troubleshoot enough to
  prove a problem isn't on your end?

## Hardware

Most ISP home gateways have a way to put them into bridge mode, where a single
device is directly exposed to the Internet. The AT&T Fibre
standard gateway, the BGW320, does not have a bridge mode. The closest it has is
"Passthrough", which works well enough for this.

For the gateway/router, I'm using a
[2-port Protectli vault](https://protectli.com/vault-2-port/) with 4GB RAM.
It's a good platform, quiet and fanless, fully supported by OpenBSD out of the
box. Throughout this article I'll assume you're also using a Protectli device;
substitute your own device where Protectli is mentioned, and be aware that
interface names may be different.

You'll most likely want a switch, and some wireless access points. These can be
anything you want; I'm using a
[UniFi 16-port POE+ switch](https://store.ui.com/us/en/collections/unifi-switching-utility-poe/products/usw-lite-16-poe)
with a few
[UniFi AP-6-Lite access points](https://store.ui.com/us/en/collections/unifi-wifi-flagship-compact/products/u6-lite).
If you also want to use UniFi equipment, look through
<https://store.ui.com/us/en/collections/unifi-switching-utility-poe> and
<https://store.ui.com/us/en/collections/unifi-wifi-flagship-compact> to see
what fits your needs.

## Installation

To begin, install the latest OpenBSD release. I'm using OpenBSD 7.5 for this
article. OpenBSD has an
[excellent installation guide](https://www.openbsd.org/faq/faq4.html) you can
follow if needed. The default automatic partition scheme is fine unless you have
some specific needs.

After installation is complete, reboot and configure
[`doas(1)`](https://man.openbsd.org/doas). Remember that, unlike `sudo`, all
users who may call [`doas(1)`](https://man.openbsd.org/doas) need to pass a
rule in [`doas.conf(5)`](https://man.openbsd.org/doas.conf):

```doas
# cat /etc/doas.conf
permit persist :wheel
permit nopass root
```

Make sure your user is added to the `wheel` group, or replace `:wheel` with your
username.

## Configuration

### WAN MAC Address

You will need the MAC address of the Protectli's WAN interface later on. The
BGW320 Passthrough mode will need it. Get that now and write it down for later:

```sh
% ifconfig em0 | grep lladdr | awk '{ print $2 }'
01:23:45:67:89:ab
```

### pf(5)

Before configuring anything else, set up
[`pf.conf(5)`](https://man.openbsd.org/pf.conf). `egress` will be the WAN
interface. This is technically a "minimal" configuration; ICMPv6 is such
a complicated beast it needs a lot of different rules. The comments I've added
also make this a lot longer than it really is.

```pf
% doas cat /etc/pf.conf
if_lan = em1

# List of martians created from all tables in RFC 6890 sections 2.2.2 and 2.2.3
# where the "Global" flag is FALSE. IPv4 bogons have also been mapped into the
# 6to4 and Teredo address spaces.
# Note that 192.0.0.0/24 and 2001::/23 may be given more specific assignments
# that are allowed in the future. As of October 2024, no more specific
# assignments with "Global" set to TRUE are defined.
table <martians> const { \
 # IPv4 bogons
 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 \
 172.16.0.0/12 192.0.0.0/24 192.0.2.0/24 192.168.0.0/16 198.18.0.0/15 \
 198.51.100.0/24 203.0.113.0/24 224.0.0.0/4 240.0.0.0/4 \
 # IPv6 bogons
 ::/128 ::1/128 ::ffff:0:0/96 100::/64 2001::/23 fc00::/7 fe80::/10 fec0::/10 \
 # IPv6-to-4 IPv4-mapped bogons (6-to-4 is all under 2002::/16)
 2002::/24 2002:a00::/24 2002:7f00::/24 2002:a9fe::/32 2002:ac10::/28 \
 2002:c000::/40 2002:c000:200::/40 2002:c0a8::/32 2002:c612::/31 \
 2002:c633:6400::/40 2002:cb00:7100::/40 2002:e000::/20 \
 2002:f000::/20 2002:ffff:ffff::/48 \
 # Teredo IPv4-mapped bogons (Teredo is all under 2001::/32)
 2001::/40 2001:0:a00::/40 2001:0:7f00::/40 2001:0:a9fe::/48 \
 2001:0:ac10::/44 2001:0:c000::/56 2001:0:c000:200::/56 \
 2001:0:c0a8::/48 2001:0:c612::/47 2001:0:c633:6400::/56 \
 2001:0:cb00:7100::/56 2001:0:e000::/36 2001:0:f000::/36 \
 2001:0:ffff:ffff::/64 \
}

# This will cause pf to reply to blocked traffic with a TCP RST packet (for TCP)
# or ICMP Unreachable (for other protocols) if nothing is specified on a block
# rule. Some tweaks you may consider depending on your own needs on specific
# rules:
# - return-rst: Applies only to TCP packets, send RST
# - return-icmp/return-icmp6: Reply with ICMP. By default this is ICMP
#   Unreachable but you can specify the ICMP type to use. return-icmp can accept
#   only an ICMPv4 type or both ICMPv4 and ICMPv6 (as `return-icmp (timex)` or
#   `return-icmp (timex, paramprob)`).
# - drop: Silently discard the packets.
# A return policy is better for quickly tearing down connections on the other
# side.
set block-policy return
# List interfaces where packet filtering should be skipped. It's almost never
# necessary to filter the loopback interface.
set skip on lo
# "yes" is default, which reassembled fragmented packets before passing them
# along. Adding "no-df" will also reassemble fragmented packets with the
# dont-fragment flag set instead of dropping them.
set reassemble yes no-df
# Enable collecting packet and byte count statistics; view with `pfctl -s info`
set loginterface egress

# The AT&T BGW320 is... not exactly what you would call a capable piece of
# hardware. It's limited to 8192 states, and honestly it would be great if it
# could actually handle nearly that many states.
# Also increase the number of table entries allowed. If you do bad-host
# filtering, the number of table entries could get quite large, especially if
# you don't coalesce IP ranges.
set limit { states 8192, table-entries 5000000 }

# Enabling syncookies causes pf to appear to accept a TCP connection even if it
# might be dropped later on. When receiving a SYN, pf will reply with SYN/ACK;
# when the ACK is received from the client, pf then evaluates the ruleset and
# takes whatever action the ruleset evaluates to. This can be useful for
# resiliance against synflood attacks that would exhaust the state table.
# Normally I would say this is useful to enable in adaptive mode, but it's
# unclear (unlikely) the BGW320 is that smart so there's not likely to be any
# benefit to enabling it here. If you want to, what I've done other places is to
# start using syncookies when the state table is 75% full and stop using it only
# when the state table falls to 20% full.
#set syncookies adaptive (start 75%, end 20%)

# Sanitize packets to reduce ambiguity:
# - no-df: Clear the dont-fragment bit. Helps with NFS implementations that like
#   to generate fragmented packets with dont-fragment set, but needs to be used
#   in combination with random-id because some operating systems also like to
#   generate dont-fragment packets with a zero IP ID field. If an upstream
#   router has to fragment the packet later, that will cause packets to end up
#   being dropped.
# - random-id: Replace the IPv4 ID field with a random identifier iff the packet
#   is not fragmented after optional reassembly. This helps when using no-df,
#   but also helps deal with predictable values generated by some hosts.
# - reassemble tcp: Statefully normalizes TCP connections. See "TRAFFIC
#   NORMALIZATION" in pf.conf(5) for all the details on what pf will do.
match in all scrub (no-df random-id reassemble tcp)

# NOTE: If you need to make game consoles work, add static-port to the end of
# this line.
match out on egress inet from !(egress:network) to any nat-to (egress:0)

# We are going to set up default-deny for incoming traffic, but there's a few
# things we want to take care of very early in the ruleset.

# Port build user does not need network. Though as a gateway/firewall, hopefully
# you aren't compiling anything at all here!
block drop out log quick proto {tcp udp} user _pbuild

# Allow traffic to the AT&T gateway device. The BGW320 IP address is in the
# list of martians but traffic will need to traverse the WAN interface.
pass in quick on $if_lan from ($if_lan:network) to 192.168.1.254 modulate state
pass out quick on egress from any to 192.168.1.254 modulate state

# Allow DHCPv4, DHCPv6 from WAN, and SLAAC
# NOTE: fe80::/10 is an IPv6 bogon, but it must be accepted on egress to allow
# for DHCPv6 and ICMPv6.
pass in quick on egress inet proto udp from port bootps to port bootpc
pass out quick on egress inet proto udp from port bootpc to port bootps
pass in quick on $if_lan inet proto udp from port bootpc to port bootps
pass out quick on $if_lan inet proto udp from port bootps to port bootpc
pass in quick on egress inet6 proto udp from fe80::/10 port dhcpv6-server \
  to fe80::/10 port dhcpv6-client no state
pass out quick on egress inet6 proto udp from fe80::/10 port dhcpv6-client \
  to fe80::/10 port dhcpv6-server no state
# fe80::/10 is link-local and ff02::/16 is multicast, make sure we accept ICMP
# network management packets on those ranges.
pass in quick on egress inet6 proto icmp6 from any \
  to { (egress), ff02::/16, fe80::/10 } \
  icmp6-type { routeradv, neighbradv, neighbrsol }
pass out quick on egress inet6 proto icmp6 from any \
  to { ff02::/16, fe80::/10 } icmp6-type { routersol, neighbradv, neighbrsol }
pass in quick on $if_lan inet6 proto icmp6 from any \
  to { ($if_lan), ff02::/16, fe80::/10 } \
  icmp6-type { routersol, neighbradv, neighbrsol }
pass in quick on $if_lan inet6 proto icmp6 from any \
  to { ff02::/16, fe80::/10 } icmp6-type { routeradv, neighbradv, neighbrsol }

# The WAN interface has no business sending traffic not covered above to or
# from addresses not routable on the global public Internet or with no return
# path.
block drop in quick on egress from { <martians>, no-route, urpf-failed } to any
block drop out quick on egress from any to { <martians>, no-route }

# Default deny all incoming traffic, default allow all outgoing traffic.
block return all
pass out modulate state
# Allow LAN outbound to the public Internet
pass in on $if_lan from any to !<martians> modulate state

# ICMP is critical for IPv6 networks. On IPv4 networks there's rarely a
# good reason to restrict it like people do, especially on a home network.
# However, there is also no good reason not to and the relevant standards say
# ICMP should be restricted without a good reason.
# See icmp(4) and icmp6(4) and RFC4890 for more detail.
block drop in inet proto icmp
block drop in inet6 proto icmp6

# ICMPv4 has no strictly required types, but a few that are good to allow anyway
# are:
# - echoreq - Ping is helpful for quick connectivity checks. There are
#   other ways to check if your host is accessible if you block it though, so
#   don't count on blocking echoreq to keep your hosts hidden.
#   echorep is allowed back by the created state from echoreq.
# - unreach with code needfrag - This is used in Path MTU Discovery, if the DF
#   flag is set and unreach with code needfrag comes back your system knows it
#   needs to send smaller packets.
# - timex - Code 0 is returned by routers if the packet TTL has reached zero
#   before reaching the destination. Code 1 is returned by hosts if the packet
#   couldn't be fully assembled within its time limit.
# ICMPv4, unlike ICMPv6, is entirely optional so you can simply remove any of
# these rules you don't want. You may also need to adjust the maximum packet
# rates if these limits are too low (expressed in num_packets/num_seconds).
# LAN gets a much higher rate since it's reasonable to expect multiple
# devices might be sending ICMP at the same time.
pass in on egress inet proto icmp from any to (egress:0) icmp-type echoreq \
  max-pkt-rate 100/15
pass in on $if_lan inet proto icmp from ($if_lan:network) to any \
  icmp-type echoreq max-pkt-rate 100/5
pass in on egress inet proto icmp from any to (egress:0) icmp-type unreach \
  code needfrag max-pkt-rate 10/10
pass in on $if_lan inet proto icmp from ($if_lan:network) to any \
  icmp-type unreach code needfrag max-pkt-rate 10/5
pass in on egress inet proto icmp from any to (egress:0) icmp-type timex \
  max-pkt-rate 1/2
pass in on $if_lan inet proto icmp from ($if_lan:network) to any \
  icmp-type timex max-pkt-rate 1/1

# Unlike ICMPv4, ICMPv6 is critical to a network's operation. There are
# different considerations for ICMP destined for the firewall and for ICMP
# destined for something on the other side of the firewall.
# Note: ($if_lan:network) is used here for packets coming in egress because the
# routable IPv6 addresses for internal hosts are chosen from the delegated
# prefix assigned to the LAN interface. Technically this does also include the
# private addresses, but further up the ruleset is a 'block quick' rule
# preventing egress from sending packets to or receiving packets from private
# address space.
# Transit ICMPv6 - must not be dropped!
# Note: echorep is allowed implicitly by the state opened by an echoreq.
pass in on egress inet6 proto icmp6 from any to ($if_lan:network) \
  icmp6-type { unreach, toobig, echoreq } max-pkt-rate 10/10
pass in on $if_lan inet6 proto icmp6 from ($if_lan:network) to \
  !($if_lan:network) icmp6-type { unreach, toobig, echoreq } max-pkt-rate 10/1

pass in on egress inet6 proto icmp6 from any to ($if_lan:network) \
  icmp6-type timex code transit max-pkt-rate 5/5
pass in on $if_lan inet6 proto icmp6 from ($if_lan:network) to \
  !($if_lan:network) icmp6-type timex code transit max-pkt-rate 5/1
pass in on egress inet6 proto icmp6 from any to ($if_lan:network) \
  icmp6-type { paramprob code nxthdr, paramprob code 2 } max-pkt-rate 5/5
pass in on $if_lan inet6 proto icmp6 from ($if_lan:network) to \
  !($if_lan:network) icmp6-type { paramprob code nxthdr, paramprob code 2 } \
	max-pkt-rate 5/1

# Transit ICMPv6 - normally should not be dropped
pass in on egress inet6 proto icmp6 from any to ($if_lan:network) \
  icmp6-type timex code reassemb max-pkt-rate 5/5
pass in on $if_lan inet6 proto icmp6 from ($if_lan:network) to \
  !($if_lan:network) icmp6-type timex code reassemb max-pkt-rate 5/1
pass in on egress inet6 proto icmp6 from any to ($if_lan:network) \
  icmp6-type paramprob code badhead max-pkt-rate 5/5
pass in on $if_lan inet6 proto icmp6 from ($if_lan:network) to \
  !($if_lan:network) icmp6-type paramprob code badhead max-pkt-rate 5/1
# These handle ICMPv6 messages for mobile IP. If you happen to need this (which
# is highly unlikely for a personal home network) uncomment these rules
#pass in on egress inet6 proto icmp6 from any to ($if_lan:network) \
#  icmp6-type { 144, 145, 146, 147 }
#pass in on $if_lan inet6 proto icmp6 from ($if_lan:network) to \
#  !($if_lan:network) icmp6-type { 144, 145, 146, 147 }

# Local ICMPv6 - must not be dropped
# Note that the desired behaviour may differ between the WAN and LAN interfaces.
pass in on egress inet6 proto icmp6 from any to (egress) \
  icmp6-type { echoreq, unreach, toobig, listqry, listenrep, listendone, \
  143, 148, 149, 151, 152, 153 } \
  max-pkt-rate 10/10
pass in on $if_lan inet6 proto icmp6 from ($if_lan:network) to ($if_lan) \
  icmp6-type { echoreq, unreach, toobig, listqry, listenrep, listendone, 143 } \
  max-pkt-rate 10/1
pass in on egress inet6 proto icmp6 from any to (egress) icmp6-type timex \
  code transit max-pkt-rate 5/5
pass in on $if_lan inet6 proto icmp6 from ($if_lan:network) to ($if_lan) \
  icmp6-type timex max-pkt-rate 5/1
pass in on egress inet6 proto icmp6 from any to (egress) \
	icmp6-type { paramprob code nxthdr, paramprob code 2 } max-pkt-rate 5/5
pass in on $if_lan inet6 proto icmp6 from ($if_lan:network) to ($if_lan) \
  icmp6-type { paramprob code nxthdr, paramprob code 2 } max-pkt-rate 5/1

# {router,neighbr}{adv,sol} are not included here because they've been handled
# further up with 'pass quick' rules.
pass in on egress inet6 proto icmp6 from any to (egress) \
  icmp6-type { 141, 142 }
pass in on $if_lan inet6 proto icmp6 from ($if_lan:network) to ($if_lan) \
  icmp6-type { 141, 142 }

# Local ICMPv6 - normally should not be dropped
pass in on egress inet6 proto icmp6 from any to (egress) \
	icmp6-type timex code reassemb max-pkt-rate 5/5
pass in on $if_lan inet6 proto icmp6 from ($if_lan:network) to \
  !($if_lan:network) icmp6-type timex code reassemb max-pkt-rate 5/1
pass in on egress inet6 proto icmp6 from any to (egress) \
  icmp6-type paramprob code badhead max-pkt-rate 5/5
pass in on $if_lan inet6 proto icmp6 from ($if_lan:network) to \
  !($if_lan:network) icmp6-type paramprob code badhead max-pkt-rate 5/1

# Allow SSH in from LAN for later configuration and maintenance.
pass in on $if_lan proto tcp from ($if_lan:network) to ($if_lan) port 22 \
  modulate state
```

Always verify your [`pf.conf(5)`](https://man.openbsd.org/pf.conf) changes, then
apply the new rules:

```sh
% doas pfctl -nf /etc/pf.conf
% doas pfctl -F rules -f /etc/pf.conf
```

### Enable forwarding packets

Normally, packets can't be forwarded between interfaces. We need to do exactly
that to use the Protectli as a gateway device though. Add these two lines to
[`sysctl.conf(5)`](https://man.openbsd.org/sysctl.conf) to enable packet
forwarding:

```conf
net.inet.ip.forwarding=1
net.inet6.ip6.forwarding=1
```

This will take effect after the next reboot. To apply the changes immediately,
run:

```sh
% doas sysctl net.inet.ip.forwarding=1 net.inet6.ip6.forwarding=1
```

### Network Interfaces

Configure your Ethernet interfaces using the
[`hostname.if(5)`](https://man.openbsd.org/hostname.if) files. The WAN interface
is `em0`, the LAN interface is `em1`. `/etc/hostname.em0` requires only two easy
lines:

```text
% cat /etc/hostname.em0
inet autoconf
up
```

You might be tempted to include `inet6 autoconf` here, but don't. IPv6 will be
configured later, in a way that allows requesting an IPv6 prefix via Prefix
Delegation and advertising that IPv6 prefix on the internal network.

`/etc/hostname.em1` is similarly easy. This time, although IPv6 will be
configured later, an `inet6 autoconf` line is required this time so
[`slaacd(8)`](https://man.openbsd.org/slaacd) will advertise on the interface.
I also include an alias for the IPv6 Unique Local Addresses prefix I advertise
on my LAN (pick anything within fd00::/8):

```text
% cat /etc/hostname.em1
inet 10.0.0.1 255.255.255.0 10.0.0.255
inet6 autoconf
inet6 alias fd01:2345:6789:abcd::1 64
up
```

Change the `inet` and `inet6 alias` lines to match your own preferred network
addressing.

### Internal IPv4 addressing with `dhcpd(8)`

Next, set up [`dhcpd(8)`](https://man.openbsd.org/dhcpd) so clients on the LAN
can get IPv4 addresses. A minimal
[`dhcpd.conf(5)`](https://man.openbsd.org/dhcpd.conf):

```dhcpd
% cat /etc/dhcpd.conf
option domain-name-servers 8.8.8.8 8.8.4.4;
default-lease-time 86400;
max-lease-time 86400;

subnet 10.0.0.0 netmask 255.255.255.0 {
  option routers 10.0.0.1;
  range 10.0.0.100 10.0.0.199;
}
```

This will allow up to 100 clients to request an IPv4 address, and leave room for
any static IP address assignment you might need. Enable and start
[`dhcpd(8)`](https://man.openbsd.org/dhcpd):

```sh
% doas rcctl enable dhcpd
% doas rcctl start dhcpd
```

### Configure the BGW320

If you've previously disabled them, you must first enable
[DHCP](http://192.168.1.254/cgi-bin/dhcpserver.ha) and
[DHCPv6](http://192.168.1.254/cgi-bin/ip6lan.ha)
(all options).

With a minimal [`pf.conf(5)`](https://man.openbsd.org/pf.conf) and internal DHCP
working, it's time to connect to the BGW320. From a computer on the BGW320's
network, go to <http://192.168.1.254/cgi-bin/ippass.ha>. You will need the
device passcode, which is printed on the back of the gateway. Make the following
changes, remembering that some changes may reload the page to enable the next
set:

- Change "Allocation Mode" to "Passthrough"
- Change "Passthrough Mode" to "DHCPS-fixed"
- For "Passthrough Fixed MAC Address", enter the MAC address of the WAN
  interface you recorded earlier in "Manual Entry".

Click "Save" to make changes. At this point, you can disconnect all devices from
the BGW320. Plug the Protectli's WAN port in to the BGW320's LAN port 1 and
power it on. Plug your switch into the Protectli's LAN port and your computer
into the switch. Log in to the Protectli via SSH. You should, at this point, see
a public IPv4 address assigned to `em0` and be able to ping something external
from both the Protectli gateway and your computer. This is why we set up
[`pf.conf(5)`](https://man.openbsd.org/pf.conf) so early, you do not want to
have something exposed to the public Internet without proper protection!

### External IPv6 addresses with `dhcpcd`

`dhcpcd` is what will handle IPv6 on the Internet side. It must first be
installed:

```sh
% doas pkg_add dhcpcd
```

Then create `/etc/dhcpcd.conf`. What has worked for me is:

```conf
% cat /etc/dhcpcd.conf
nooption domain_name_servers
nooption domain_name
nooption domain_search
nooption host_name

ipv6only
noipv6rs
duid
persistent

option rapid_commit
option interface_mtu
require dhcp_server_identifier

# slaac private

script /usr/bin/true

vendorclassid

allowinterfaces em0 em1

interface em0
 ipv6rs
 ia_na 1
 ia_pd 2 em1/0
```

Finally, enable and start `dhcpcd`:

```sh
% doas rcctl enable dhcpcd
% doas rcctl start dhcpcd
```

[`slaacd(8)`](https://man.openbsd.org/slaacd) is also needed. AT&T hands out
addresses using DHCPv6, which is handled by `dhcpcd`, but routing information
comes from Router Advertisements, which requires
[`slaacd(8)`](https://man.openbsd.org/slaacd):

```sh
% doas rcctl enable slaacd
% doas rcctl start slaacd
```

At this point, you should see at least one public IPv6 address on `em0` and
`em1` should have both a public IPv6 address plus the address you defined in
`hostname.em1`. You should be able to verify IPv6 connectivity from the OpenBSD
gateway with `ping6 www.google.com`.

### Internal IPv6 addresses with `rad(8)`

Now that you have an IPv6 prefix delegated, you need to advertise that prefix on
your internal network. [`rad(8)`](https://man.openbsd.org/rad) allows clients on
your internal network to request IPv6 addresses using SLAAC. A minimal
[`rad.conf(5)`](https://man.openbsd.org/rad.conf):

```conf
% cat /etc/rad.conf
other configuration no
interface em1
```

Enable and start [`rad(8)`](https://man.openbsd.org/rad):

```sh
% doas rcctl enable rad
% doas rcctl start rad
```

[`rad(8)`](https://man.openbsd.org/rad) will automatically advertise all
prefixes on the interface it's listening on. Since you have a prefix from the
public IPv6 Prefix Delegation as well as a ULA prefix, all clients requesting
IPv6 addresses using SLAAC will get at least two addresses, one from each
prefix. IPv6 clients should pick up their addresses automatically shortly after
starting [`rad(8)`](https://man.openbsd.org/rad).

## Trust, but Verify

At this point, you should be able to:

- Run `ifconfig em0` on your new gateway and see one IPv4 address and at least
  two IPv6 addresses.
- Run `ifconfig em1` on your new gateway and see a different IPv4 address and
  at least two different IPv6 addresses.
- Run `ping6 www.google.com` from both your new gateway and a LAN client and see
  responses.
- Check the IP addresses assigned to any internal LAN client and see both an
  IPv4 address and at least one IPv6 address in the same range as the address
  from the output of `ifconfig em1`.

## What's Next?

At this point you have taken some load off your ISP's Internet gateway device
and put your LAN behind a secure and trustworthy firewall. This is already
a significant improvement in the security and flexibility of your home network,
and is a fine place to stop if you're satisfied already. If so, great! If you
want to try one more thing, my
[next post]({{<relref "openbsd-unbound-dns-server">}}) will show how to configure
[`unbound(8)`](https://man.openbsd.org/unbound) as a local caching recursive DNS
resolver.

## Changelog

- 2024-08-02: A helpful reader pointed out that I missed a PF rule to allow
  outbound traffic from the LAN to the public Internet. That's been added.
- 2024-10-06: Another helpful reader pointed out that I was incorrectly allowing
  all ICMP types. [RFC4890](https://www.rfc-editor.org/rfc/rfc4890.txt)
  specifies that certain ICMPv6 types SHOULD be blocked, and according to the
  definition of SHOULD in RFCs that means it is to be done without a good reason
  not to. I do not have a good reason not to. The sample `pf.conf` has been
  updated to only allow ICMP types needed for daily network operation.
