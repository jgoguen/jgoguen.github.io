---
title: If you Understand TCP, you Understand Consent
date: 2022-12-12
description: /
  People act like consent is a hard thing. It's actually quite simple. If you
  can understand how a TCP connection works, you can understand how consent
  works.
tags:
  - consent
  - TCP
  - people
---

If you can understand TCP, you can understand consent. It's really quite simple.
Here we'll look at how TCP flags (the original six plus `ECE` and `CWR`) map to
a conversation between two people.

Let's say you want to start talking to someone. In TCP, you start with the
three-way handshake:

```
You ---  SYN  --> Them
You <--SYN/ACK--- Them
You ---  ACK  --> Them
```

In human terms, a `SYN` (synchronize) expresses that you're asking to begin
a conversation, and an `ACK` (acknowledge) is how you acknowledge what the other
person has said. Note that both sides must explicitly begin the conversation and
both sides must acknowledge the other. If it doesn't happen like this, there is
no conversation.

At this point, a conversation has been started with explicit consent from both
people. It is highly important to note there is no way to force a conversation.
If someone doesn't want to talk, they cannot be forced to. The normal flow of
a conversation is simple. You talk to them:

```
You ---Talk--> Them
You <-- ACK--- Them
```

Or they talk to you:

```
You <--Talk--- Them
You ---ACK --> Them
```

However, sometimes something urgent comes up. There's two relevant TCP flags
here: `URG` (urgent) and `PSH` (push). `URG` tells the recipient (the other
person in our conversation) that what follows is urgent and must be processed
before anything else. `PSH` is similar, the recipient is asked to process each
chunk of information immediately as it arrives instead of gathering it up and
dealing with everything at once. A comparable occurrence in a conversation might
be that you have something they need to know right away, or they need you to do
something right away and ask questions later.

People sometimes get overwhelmed and ask you to slow down. In TCP, this is
signaled in the packet, and the receiver is expected to use the `ECE` (ECN-Echo,
where "ECN" means "Explicit Congestion Notification") flag to acknowledge the
"I'm getting overwhelmed" signal. That acknowledgment happens in TCP by setting
the `CWR` (Congestion Window Reduced) flag and actually slowing down as much as
needed until the recipient tells you they're feeling better.

Finally, consent is not consent unless it can be withdrawn for any or no reason
at all. For a conversation, that means either person can simply say "this
conversation is over" and walk away. In TCP, that's done with the `RST` (reset)
flag. When the `RST` flag is seen, both sides are to consider the conversation
abruptly over.

```
You ---Talk--> Them
You <-- RST--- Them
(Nothing else may follow)
```

Of course, hopefully it doesn't come to that. It's always better when the
conversation ends on a more polite note. Yet again, TCP shows us how it's done:

```
You ---  FIN  --> Them
You <--FIN/ACK--- Them
You ---  ACK  --> Them
```

The `FIN` (finished) flag means you have nothing more to say. You are signalling
that you want to gracefully end the conversation. They agree to gracefully end
the conversation and acknowledge you, finished by you acknowledging them.

Now, I've used a conversation as the real-world example, but anything involving
two people can be modeled with a TCP connection: relationships, friendships,
a date, going out to dinner, etc. If it involves two (or more!) people actively
engaging, the TCP model of consent applies. You may notice that there's nothing
here where one person forces or coerces another to listen. That's intentional!
It can't happen in TCP, one side cannot force a connection to be opened or to
remain open. It also must not happen in real life, you must never force or
coerce someone to engage with you in any way.

And there you have it, basic consent modeled by a simple technical process. If
you can understand how a TCP connection is set up, you have no excuse for not
understanding how consent works.
