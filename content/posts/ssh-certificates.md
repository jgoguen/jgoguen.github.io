---
title: SSH Certificates
date: 2022-12-07
description: /
  Controlling access to SSH servers using certificates
tags:
  - SSH
  - certificates
---

If you have a SSH server, you've probably heard that you should be using SSH key
authentication instead of passwords. There's a lot of benefits to key
authentication: better experience, smooth login, ability to log in via scripts,
service accounts become possible, and keys are significantly harder to crack
than a password. But if you deal with a lot of servers, or you manage a lot of
users, or both, there's a better way using SSH certificates instead of simple
keys.

When using simple key authentication, any user can generate their own SSH keys
and add the public portion to their `authorized_keys` file if you haven't taken
care to restrict access to it (or if they have broad `sudo`/`doas` privileges).
Depending on what other protections you have in place, this can mean users could
access servers from devices they shouldn't be allowed to use. In an enterprise
environment, for example, you may have Internet-exposed SSH servers you want to
restrict to access from managed devices only. Or you may want to allow some
users to log in as either themselves or as other users on the system, which
requires copying the public portion of a key to multiple `authorized_keys`
files. These keys also don't expire, and need to be manually removed when a user
should no longer have access.

You may also want to give users some way to trust that they're connecting to
a trusted server without making them compare fingerprints. Normally, you would
need to distribute a list of valid server fingerprints to everyone, and make
sure they get an updated list every time a new server is added, or an existing
server is reinstalled.

The solution to both problems is an SSH certificate. Functionally similar to
a TLS certificate, SSH certificates can be used for both identifying a server to
a user and identifying a user to a server. In this post, we'll explore how to
set up your SSH Certificate Authority (CA), how to issue certificates for both
users and hosts, and how to revoke SSH certificates when needed.

# Creating your SSH CA

The first thing you need to do is create your Certificate Authority keys. Unlike
TLS CAs, SSH CAs don't have the concept of intermediate issuers because you
don't actually have a CA certificat, you only have SSH keys trusted as signing
keys. This means your SSH CA is your root CA and can't be kept offline. If
possible, the private key should always be kept in secure hardware of some kind,
such as a Hardware Security Module (HSM) or local TPM. If you do, you'll need to
first create a private key on the secure storage using the PKCS#11 interface.

## Create a key in Secure Hardware

First you need to know the PKCS#11 URI of the secure hardware module you want to
use. You can find this with the `p11tool` command, part of the `gnutls-utils`
package on CentOS and similar distributions:

```
$ p11tool --list-tokens
Token 0:
        URL: pkcs11:model=SuperHSM;manufacturer=SuperSoft;serial=883197678b507ca7;token=puffy-hsm
        Label: puffy-hsm
        Type: Super Secure HSM
        Flags: RNG, Requires login
        Manufacturer: SuperSoft
        Model: SuperHSM
        Serial: 883197678b507ca7
        Module: /usr/lib64/pkcs11/libsuperhsm2.so
```

Make note of the URL and the Module, you will need both of these later.

Now generate a private key for your SSH CA. Since you're storing the private key
in secure hardware you could use one key for all your CAs, or you can use one
key per CA.

```
$ p11tool --generate-privkey=ecdsa --sec-param=high --login --label ssh-user-ca 'pkcs11:model=SuperHSM;manufacturer=SuperSoft;serial=883197678b507ca7;token=puffy-hsm'
warning: no --outfile was specified and the generated public key will be printed on screen.
Generating an EC/ECDSA key...
Token 'puffy-hsm' with URL 'pkcs11:model=SuperHSM;manufacturer=SuperSoft;serial=883197678b507ca7;token=puffy-hsm' requires user PIN
Enter PIN:
-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEmbX149h8eCyhg3Sx1DBPVjdW0jP+
gCeB33mr/I2vv9EOgr1Ec/lEablY2D/PR+AbZ7wII5aMAp9+9vdiaTVTxw==
-----END PUBLIC KEY-----
```

This is not the public key you want to save! You need the public key in the
standard SSH format. To get that, run `ssh-keygen -D libsuperhsm2.so -e >ssh-ca.pub`.
You will use this later when issuing SSH certificates.

## Create a key on disk

If you don't have secure hardware available, you will need to store your private
keys on disk. These will allow anyone with access to create new certificates
trusted by all your devices, so be sure to appropriately protect them!

Creating a private key on disk is a matter of a single command:

```
$ ssh-keygen -t ecdsa -b 521 -f ssh-ca -C 'SSH CA Certificate'
Generating public/private ecdsa key pair.
Enter passphrase (empty for no passphrase):
Enter same passphrase again:
Your identification has been saved in ssh-ca.
Your public key has been saved in ssh-ca.pub.
The key fingerprint is:
SHA256:B6Sms0weOZLIOz/TRV3xZj/vxznRxfC7eVyGY9y+5/c SSH CA Certificate
The key's randomart image is:
+---[ECDSA 521]---+
|        .  ..    |
|       o   .. .  |
|      o o .  + + |
|.. . + . o  o . +|
|..o B . S .  . =+|
|  .= = . .    =oB|
| o  = .      . *B|
|  oo .         *O|
|   .o          oE|
+----[SHA256]-----+
```

This will create two files: `ssh-ca` (the private key) and `ssh-ca.pub` (the
public key). You should create one set of keys for each SSH CA you intend to use;
this usually means creating two sets of keys, one for host certificates and one
for user certificates.

# Signing a Host Certificate

Now that you have your CA keys created, you're ready to issue SSH certificates.
The first type we'll look at is a host certificate, which identifies a SSH
server to a connecting client. All you need is your SSH server's host public key,
stored in `/etc/ssh/ssh_host_*_key.pub`, and the keys generated earlier.

## Signing with PKCS#11

To sign with a private key stored in secure hardware, use the `-D` option to
`ssh-keygen`:

```
$ ssh-keygen -s ssh-ca.pub -D libsuperhsm2.so -I dev1234.example.com -h -n dev1234.example.com,dev1234,10.20.0.134 -V +52w -z $(date +%s) ./ssh_host_ecdsa_key.pub
Enter PIN for 'puffy-hsm':
Signed host key ./ssh_host_ecdsa_key-cert.pub: id "dev1234.example.com" serial 1670459268 for dev1234.example.com,dev1234,10.20.0.134 valid from 2022-12-05T22:45:00 to 2023-12-04T22:46:02
```

Let's break that command down a bit. Each option means:

- `-s ssh-ca.pub`: Use `ssh-ca.pub` as the SSH public key
- `-D libsuperhsm2.so`: Look for the private key using the PKCS#11 library
  `libsuperhsm2.so`
- `-I dev1234.example.com`: The certificate's identity. This can be any
  alphanumeric string, but it's typically best to use the server's hostname.
- `-h`: Create a host certificate.
- `-n dev1234.example.com,dev1234,10.20.0.134`: A comma-separated list of
  principals the certificate is valid for. For a host certificate, these are all
  the hostnames or IP addresses valid for connecting to the server.
- `-V +52w`: The certificte is valid for the specified time, in this case for
  the next 52 weeks.
- `-z $(date +%s)`: Set the certificate serial number. `date +%s` prints the
  current time as seconds since the UNIX epoch, which causes the certificate
  serial number to be set to the current time. You don't need this option, but
  it can sometimes be helpful to have a serial number defined. If you don't set
  a serial number, 0 will be used.
- `./ssh_host_ecdsa_key.pub`: The path to the host public key to sign.

## Signing with on-disk keys

To sign with a private key stored on disk, leave out the `-D` option and pass
the private key to `-s`:

```
$ ssh-keygen -s ssh-ca -I dev1234.example.com -h -n dev1234.example.com,dev1234,10.20.0.134 -V +52w -z $(date +%s) ./ssh_host_ecdsa_key.pub
Signed host key ./ssh_host_ecdsa_key-cert.pub: id "dev1234.example.com" serial 1670459268 for dev1234.example.com,dev1234,10.20.0.134 valid from 2022-12-05T22:45:00 to 2023-12-04T22:46:02
```

## Using the host certificate

Once you've generated a host SSH certificate, you need to tell `sshd` to use it.
Add this line to `/etc/ssh/sshd_config` and restart `sshd`:

```
HostCertificate /etc/ssh/ssh_host_ecdsa_key-cert.pub
```

Now your SSH server is configured to present a host certificate, so the last
step is to tell clients to trust the certificate. Start by generating the
`known_hosts` format file:

```
$ printf '@cert-authority *.example.com %s' "$(cat ssh-ca.pub)" >ssh_known_hosts
```

Copy `ssh_known_hosts` to clients who should trust the SSH host certificates as
`/etc/ssh/ssh_known_hosts`. After that, you can remove any entries in the user's
`known_hosts` file for the server; they will automatically trust it based on the
SSH host certificate. And when you eventually reinstall a host, as long as you
sign a new SSH host certificate for the host users won't see the dreaded
"WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!" message.

# Signing a User Certificate

Once you have SSH host certificates deployed, the next step is to use SSH user
certificates to identify users to servers. The commands to do so are similar to
host certificates. You will also need the public portion of the user's SSH key.

## Signing with PKCS#11

To sign a user certificate with a key stored in secure hardware, we once again
use the `-D` option to `ssh-keygen`:

```
$ ssh-keygen -s ssh-ca.pub -D libsuperhsm2.so -I jgoguen@example.com -n jgoguen,deploybot -V +2w -z $(date +%s) ./user.pub
Enter PIN for 'puffy-hsm':
Signed user key ./user-cert.pub: id "jgoguen@example.com" serial 1670459268 for jgoguen,deploybot valid from 2022-12-06T23:06:00 to 2022-12-20T23:07:00
```

Let's break that command down. Each option means:

- `-s ssh-ca.pub`: Use `ssh-ca.pub` as the SSH public key
- `-D libsuperhsm2.so`: Look for the private key using the PKCS#11 library
  `libsuperhsm2.so`
- `-I jgoguen@example.com`: The certificate's identity. This can be any
  alphanumeric string, but it's typically best to use some unique identifier for
  the user. Their email address, username, or userPrincipalName are all good
  choices.
- `-n jgoguen,deploybot`: A comma-separated list of
  principals the certificate is valid for. This allows the user to log in to any
  host accepting the certificate as either `jgoguen` or `deploybot`.
- `-V +2w`: The certificte is valid for the specified time, in this case for
  the next 2 weeks.
- `-z $(date +%s)`: Set the certificate serial number. `date +%s` prints the
  current time as seconds since the UNIX epoch, which causes the certificate
  serial number to be set to the current time. You don't need this option, but
  it can sometimes be helpful to have a serial number defined. If you don't set
  a serial number, 0 will be used.
- `./jgoguen.pub`: The path to the user public key to sign.

## Signing with on-disk keys

Again, this is similar to how a host certificate is signed.

```
$ ssh-keygen -s ssh-ca -I jgoguen@example.com -n jgoguen,deploybot -V +2w -z $(date +%s) ./user.pub
Signed user key ./user-cert.pub: id "jgoguen@example.com" serial 1670459268 for jgoguen,deploybot valid from 2022-12-06T23:06:00 to 2022-12-20T23:07:00
```

Once again, the options are the same as for signing with a PKCS#11 key except
that the `-s` option points to the private key to use for signing.

## Using the user certificate

To use the user certificate, first all servers must be updated to trust the user
CA signing key. Copy the contents of the CA public key (in this example,
`ssh-ca.pub`) to all servers as `/etc/ssh/user-ca.pub`. Then add this line to
`/etc/sshd/sshd_config` on all servers that should accept SSH certificate
authentication and restart `sshd`:

```
TrustedUserCAKeys /etc/ssh/user-ca.pub
```

Next, deliver the file `user-cert.pub` to the user's client device. This can be
stored in their `~/.ssh` directory. The `ssh` command will expect to find three
files: `user` (unless the user's private key is stored in a PKCS#11 store),
`user.pub`, and `user-cert.pub`. In either `/etc/ssh/ssh_config` or
`~/.ssh/config`, set the `IdentityFile` directive to point to the private key
file or set the `PKCS11Provider` directive to point to the PKCS#11 library used
to store the private key.

# Trust But Verify

Verifying that SSH certificates are in use is as simple as watching the logs of
your SSH server. On a modern Linux distribution with `systemd`, that can be done
with `journalctl -f -u sshd.service`. For a successful login, you'll see
something like this in the SSH server logs:

```
sshd[52436]: Accepted publickey for deploybot from 10.20.30.40 port 9923 ssh2: ECDSA-CERT ID jgoguen@example.com (serial 1670459268) CA ECDSA SHA256:tltbnMalWg+skhm+VlGLd2xHiVPozyuOPl34WypdEO0
```

If the user tries to log in with a different username, you'll instead see
something like this:

```
sshd[52436]: error: key_cert_check_authority: invalid certificate
sshd[52436]: error: Certificate invalid: name is not a listed principal
```

And if the certificate is expired:

```
sshd[52436]: error: key_cert_check_authority: invalid certificate
sshd[52436]: error: Certificate invalid: expired
```

# Beyond The Basics

Now that you have SSH cetificates deployed and in use, there's a lot more you
can do. Here's a few ideas you might want to consider.

## Restrict SSH options

Maybe you don't want to allow users to forward ports through the SSH server, or
you don't want to allow X11 forwarding. If you check `man ssh-keygen` and look
for the `-O` flag, you'll see a number of different things you can enforce in
the certificate itself. For example, if you wanted to deny port forwarding and
X11 forwarding, you would add `-O no-x11-forwarding -O no-port-forwarding` to
the `ssh-keygen` command used to issue a user certificate:

```
$ ssh-keygen -s ssh-ca.pub -D libsuperhsm2.so -I jgoguen@example.com -n jgoguen,deploybot -V +2w -z $(date +%s) -O no-x11-forwarding -O no-port-forwarding ./user.pub
```

You can inspect the newly-issued certificate to verify these options are now set:

```
$ ssh-keygen -f ./user-cert.pub -L
./user-cert.pub:
        Type: ecdsa-sha2-nistp256-cert-v01@openssh.com user certificate
        Public key: ECDSA-CERT SHA256:yeYf7VNJMtYm1Dr+ZqqZHV3DlPpndqFgeOfNfYWXLGk
        Signing CA: ECDSA SHA256:B6Sms0weOZLIOz/TRV3xZj/vxznRxfC7eVyGY9y+5/c (using ecdsa-sha2-nistp521)
        Key ID: "jgoguen@example.com"
        Serial: 1670460599
        Valid: from 2022-12-07T19:49:00 to 2022-12-21T19:49:59
        Principals:
                jgoguen
                deploybot
        Critical Options: (none)
        Extensions:
                permit-agent-forwarding
                permit-pty
                permit-user-rc
```

The `permit-X11-forwarding` and `permit-port-forwarding` options, which are
enabled by default, are not present, so users will not be able to forward ports
or X11 connections.

## Enforce a Bastion Host

A good way to improve the security of your SSH servers is to simply not expose
them to the Internet in the first place and require all connections to pass
through a bastion host. A bastion host is an otherwise normal SSH server, but
it's the only SSH server exposed to the public Internet. All connections to
other SSH servers are required to originate from the bastion host, and a bastion
host should have a much more locked down configuration. Forcing all connections
through a bastion host requires only two simple `Host` stanzas in
`/etc/ssh/ssh_config` or `~/.ssh/config`. If using private keys stored on disk,
use these stanzas:

```
Host *.example.com
  ProxyJump ssh-gw.example.com
  IdentityFile ~/.ssh/user

Host ssh-gw.example.com
  ProxyJump none
  IdentityFile ~/.ssh/user
```

If you're using private keys stored in secure hardware, use these stanzas
instead:

```
Host *.example.com
  ProxyJump ssh-gw.example.com
  CertificateFile ~/.ssh/user-cert.pub
  # For a PKCS#11 URI, only a subset of path arguments are supported by OpenSSH
  IdentityFile pkcs11:object=ssh-user

Host ssh-gw.example.com
  ProxyJump none
  CertificateFile ~/.ssh/user-cert.pub
  # For a PKCS#11 URI, only a subset of path arguments are supported by OpenSSH
  IdentityFile pkcs11:object=ssh-user
```

If you add the SSH certificate and key to your local SSH agent, you can skip the
`CertificateFile` and `IdentityFile` parameters entirely.

## Enforce certificate authentication

If you previously had public key authentication enabled, using certificates
doesn't change that. Users can still log in with their SSH private keys, and
they can add their own SSH keys to their `authorized_keys` file to allow them to
log in without using their SSH certificate. Fortunately, putting a stop to this
is a one-line change. In `/etc/ssh/sshd_config` make sure this line is present
then restart `sshd`:

```
AuthorizedKeysFile none
```

This will prevent `sshd` from checking for any `authorized_keys` file anywhere,
effectively removing the ability to log in with standard SSH keys and enforcing
SSH certificates. If you want to go one step further and completely remove the
ability to log in with anything but certificates, also add this line to
`/etc/ssh/sshd_config` and restart `sshd`:

```
AuthenticationMethods publickey
```

If you do decide to completely remove any other authentication method, you
should keep a "break glass" user account active that can log in with a password
(and 2-factor authentication if possible).

## Allow  auternate user login on specific hosts

The examples given so far will allow logging in as the `deploybot` user on any
host where that user exists. You may want to restrict where a user can log in as
`deploybot` though, such as only allowing users to log in as `deploybot` on
build test hosts. To achieve this, start by removing `deploybot` as a principal
on the issued certificate. Then, on every host you want to allow `deploybot`
logins for, add this line to `/etc/ssh/sshd_config`:

```
AuthorizedPrincipalsFile /etc/ssh/auth_principals/auth_principals_%u
```

Then create the directory `/etc/ssh/auth_principals` and create the file
`/etc/ssh/auth_principals/auth_principals_deploybot`. In that file, add the
principals of users allowed to log in as `deploybot`, one per line. Restart
`sshd` when finished. Now a user with a certificate that has a principal in this
file can simply log in with `ssh deploybot@buildtest1234.example.com` but `ssh
deploybot@dev1234.example.com` would fail.

# Conclusion

If you manage large fleets of machines, or you manage many users, SSH
certificates give you a fairly easy way to improve the overall security of your
servers with easier and better access control compared to plain SSH keys or
passwords. One thing that isn't covered here is how to make this scale (doing
this manualy for more than just a few hosts and users will be painful), but even
a simple web service properly secured and authenticated will go a long way to
allowing you to scale at least to a small organization.
