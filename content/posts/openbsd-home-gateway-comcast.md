---
title: OpenBSD IPv6 Home Internet Gateway with Xfinity/Comcast
date: 2022-06-08
description: |
  Using OpenBSD as a home Internet gateway with Xfinity/Comcast with IPv6
  support
tags:
  - OpenBSD
  - Xfinity
  - network
  - IPv6
---

When you sign up for Internet service, one thing you're going to get is a home
gateway. It's going to offer wired and wireless Internet connections plus
a bunch of other things you may not want and probably won't need. Unfortunately,
one of the things it's going to offer you is poor performance. The wireless
network on it probably won't cover your whole house, and if you're also limited
in where you can hook it up that could mean poor coverage in the most important
areas. It's also not built on the best hardware for the job, so when it counts
the most you may not get the connection speeds you expect. And, perhaps most
importantly, it's not all that secure.

## Before you start

Before doing anything, decide:

- Are you comfortable installing and configuring a computer that will become
  a critical part of your home Internet setup?
- Are you comfortable being technical support if something isn't working
  properly?
- Your ISP is unlikely to give you much support, can you troubleshoot enough to
  prove a problem isn't on your end?

## Hardware

Most ISP home gateways have a way to put them into bridge mode, where a single
device is directly exposed to the Internet. If you don't want to deal with
buying your own modem and getting it registered with Xfinity, this may be
a viable option. However, considering the home gateway is an additional charge
you should get a modem. Removing the home gateway rental fee will pay for the
modem over time. I use the
[Arris SURFboard SB8200 gigabit modem](https://www.amazon.com/dp/B07DY16W2Z). No
setup is needed on the modem itself, simply call Xfinity customer support and
ask them to register the modem on your account.

For the gateway/router, I'm using a
[2-port Protectli vault](https://protectli.com/vault-2-port/) with 4GB RAM. It's
a good platform, quiet and fanless, fully supported by OpenBSD out of the box.

You'll most likely want a switch, and some wireless access points. These can be
anything you want; I'm using a UniFi 8-port 150W POE+ switch, and a few UniFi
AC-Pro access points, all of which are old enough to be discontinued. If you
also want to use UniFi equipment, look through
https://store.ui.com/collections/unifi-network-switching and
https://store.ui.com/collections/unifi-network-wireless to see what fits your
needs.

## Installation

To begin, install the latest OpenBSD release. OpenBSD has an
[excellent installation guide](https://www.openbsd.org/faq/faq4.html) you can
follow if needed. The default automatic partition scheme is fine unless you have
some specific needs.

After installation is complete, reboot and configure
[`doas(1)`](https://man.openbsd.org/doas). Remember that, unlike `sudo`, all
users who may call `doas(1)` need to pass a rule in
[`doas.conf(5)`](https://man.openbsd.org/doas.conf):

```
# cat /etc/doas.conf
permit persist :wheel
permit nopass root
```

Make sure your user is added to the `wheel` group, or replace `:wheel` with your
username.

## Configuration

### `pf(4)`

Before anything else, set up a minimal
[`pf.conf(5)`](https://man.openbsd.org/pf.conf). `egress` will be the WAN
interface.

```
% doas cat /etc/pf.conf
table <martians> const { \
        0.0.0.0/8 10.0.0.0/8 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 \
        192.0.2.0/24 192.88.99.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 \
        203.0.113.0/24 224.0.0.0/3 \
        ::/128 ::/96 ::1/128 ::ffff:0:0/96 100::/64 2001:10::/28 2001:2::/48 \
        2001:db8::/32 3ffe::/16 fec0::/10 fc00::/7 \
}

set block-policy return
set skip on lo
set reassemble yes no-df
set loginterface egress

match in all scrub (no-df random-id reassemble tcp)

# NOTE: If you need to make game consoles work, add static-port to the end of
# this line
match out on egress inet from !(egress:network) to any nat-to (egress:0)
# Port build user does not need network
block drop out log quick proto {tcp udp} user _pbuild

block drop in quick on egress from <martians> to any
block drop out quick on egress from any to <martians>

# Default deny all incoming traffic, default allow all outgoing traffic
block return all
pass out modulate state

pass in inet proto icmp all icmp-type echoreq
pass in inet6 proto icmp6 all icmp6-type { echoreq neighbrsol neighbradv }

pass in on egress inet6 proto icmp6 all icmp6-type routeradv
pass in on !egress inet6 proto icmp6 all icmp6-type routersol

pass in on egress inet proto udp from port bootps to port bootpc
pass in on !egress inet proto udp from port bootpc to port bootps
pass in on egress inet6 proto udp from fe80::/10 port dhcpv6-server to fe80::/10 port dhcpv6-client no state

pass in on $if_lan proto tcp from ($if_lan:network) to ($if_lan) port 22 modulate state
```

Always verify your `pf.conf(5)` changes, then apply the new rules:

```
% doas pfctl -nf /etc/pf.conf
% doas pfctl -F rules -f /etc/pf.conf
```

### Network Interfaces

First, configure your Ethernet interfaces using the
[`hostname.if(5)`](http://man.openbsd.org/hostname.if) files. The WAN interface
is `em0`, the LAN interface is `em1`. `/etc/hostname.em0` requires only two easy
lines:

```
% cat /etc/hostname.em0
inet autoconf
up
```

You might be tempted to include `inet6 autoconf` here, but don't. IPv6 will be
configured later, in a way that allows requesting an IPv6 previx via Prefix
Delegation and advertising that IPv6 prefix on the internal network.

`/etc/hostname.em1` is similarly easy. Again, don't add an `inet6` line, IPv6
will be handled later.

```
% cat /etc/hostname.em1
inet 192.168.0.1 255.255.255.0 192.168.0.255
up
```

Change the `inet` line to match your own preferred network addressing.

### Internal IPv4 addressing with `dhcpd(8)`

Next, set up [`dhcpd(8)`](https://man.openbsd.org/dhcpd) so clients on the LAN
can get IPv4 addresses. A minimal
[`dhcpd.conf(5)`](https://man.openbsd.org/dhcpd.conf):

```
% cat /etc/dhcpd.conf
option domain-name-servers 9.9.9.9 8.8.8.8;
default-lease-time 86400;
max-lease-time 86400;

subnet 192.168.0.0 netmask 255.255.255.0 {
  option routers 192.168.0.1;
  range 192.168.0.100 192.168.0.199;
}
```

This will allow up to 100 clients to request an IPv4 address, and leave room for
any static IP address assignment you might need. Enable and start `dhcpd`:

```
% doas rcctl enable dhcpd
% doas rcctl start dhcpd
```

### External IPv6 addresses with `dhcpcd`

`dhcpcd` is what will handle IPv6 on the Internet side. It must first be
installed:

```
% doas pkg_add dhcpcd
```

Then create `/etc/dhcpcd.conf`. What has worked for me is:

```
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

script /usr/bin/true

vendorclassid

allowinterfaces em0 em1

interface em0
        ipv6rs
        ia_na 1
        ia_pd 1/::/60 em1/1/64
```

Finally, enable and start `dhcpcd`:

```
% doas rcctl enable dhcpcd
% doas rcctl start dhcpcd
```

[`slaacd(8)`](https://man.openbsd.org/slaacd) is also needed. Xfinity hands out
addresses using DHCPv6, which is handled by `dhcpcd`, but routing information
comes from Router Advertisements, which requires `slaacd(8)`:

```
% doas rcctl enable slaacd
% doas rcctl start slaacd
```

At this point, you should be able to verify IPv6 connectivity from the OpenBSD
gateway with `ping6 www.google.com`.

### Internal IPv6 addresses with `rad(8)`

Now that you have an IPv6 prefix delegated, you need to advertise that prefix on
your internal network. [`rad(8)`](https://man.openbsd.org/rad) allows clients on
your internal network to request IPv6 addresses using SLAAC. A minimal
[`rad.conf(5)`](https://man.openbsd.org/rad.conf):

```
% cat /etc/rad.conf
other configuration no
interface em1
```

Enable and start `rad(8)`:

```
% doas rcctl enable rad
% doas rcctl start rad
```

### Enable forwarding packets

The last step is to allow forwarding packets. Add these two lines to
`/etc/sysctl.conf`:

```
net.inet.ip.forwarding=1
net.inet6.ip6.forwarding=1
```

To apply the changes immediately, run:

```
% doas sysctl net.inet.ip.forwarding=1 net.inet6.ip6.forwarding=1
```

## Trust, but Verify

At this point, you should be able to:

- Run `ifconfig em0` on your new gateway and see one IPv4 address and two IPv6
  addresses.
- Run `ifconfig em1` on your new gateway and see a different IPv4 address and
  two different IPv6 addresses.
- Run `ping6 www.google.com` from both your new gateway and a LAN client and see
  responses.
- Check the IP addresses assigned to any internal LAN client and see both an
  IPv4 address and at least one IPv6 address in the same range as the address
  from the output of `ifconfig em1`.
