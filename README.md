# Captain

captain is a suite of tools related to APT.

It contains the following tools:

- an installer which is launched when you open .deb files
- an installer which is launched when you an apt://pkgname URL

Captain replaces gdebi (which wasn't actively maintained upstream) and apturl (which wasn't actively maintained and was specific to Ubuntu).

# TODO

- improve the UI which lists removals and additions
- make captain compatible with Debian by switching away from mint-common.aptdaemon*


* This requires aptdaemon to be forked and ported to Debian.
