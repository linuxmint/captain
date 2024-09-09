# Captain

captain is a suite of tools related to APT.

It contains the following tools:

- an installer which is launched when you open .deb files
- an installer which is launched when you an apt://pkgname URL

Captain replaces gdebi (which wasn't actively maintained upstream) and apturl (which wasn't actively maintained and was specific to Ubuntu).

# Usage

To install a .deb file:

`captain file.deb` or double-click the .deb file.

To install a package from the repositories:

`xdg-open apt://pkgname`

# TODO

- make captain compatible with Debian by switching away from mint-common.aptdaemon*

* This requires aptdaemon to be forked and ported to Debian.
