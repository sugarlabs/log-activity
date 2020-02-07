What is this?
=============

The Log Activity allows you to troubleshoot problems with your computer. You can view the log files of Sugar and Activities.

How to use?
===========

Log is part of the Sugar desktop.

Log is used when looking for why an activity or Sugar is not working properly.

For an activity, start the activity, make the activity do something wrong, then stop the activity.  Check that the activity is stopped by opening the Frame (F6).  Start the Log activity (F3, log, enter).  Click on the log file for the activity; it will be named according to the activity bundle_id.  Compare the log against what used to happen, or what happens when the activity does not do something wrong.  Write to the maintainer of the activity or create an issue on GitHub in the activity repository.  Explain how you made the activity do something wrong.  Include the log as quoted text.  Include the activity version.

For Sugar, make Sugar do something wrong, then stop Sugar by logging out.  Log back in.  Start the Log activity (F3, log, enter).  Click on the date and time for when you logged out.  Click on the `shell.log` or `datastore.log` files.  Compare the log against what used to happen, or what happens when Sugar does not do something wrong.  Write to the Sugar mailing list or create an issue on GitHub in the [sugar repository](https://github.com/sugarlabs/sugar).  Explain how you made Sugar do something wrong.  Include the log as quoted text.  Include the Sugar version.

Please refer to;

* [How to Get Sugar on sugarlabs.org](https://sugarlabs.org/),
* [How to use Sugar](https://help.sugarlabs.org/),
* [How to use Log](https://help.sugarlabs.org/log.html).

How to integrate?
=================

On Debian and Ubuntu systems;

```
apt install sugar-log-activity
```

On Fedora systems;

```
dnf install sugar-log
```

Log depends on Python, [Sugar
Toolkit](https://github.com/sugarlabs/sugar-toolkit-gtk3), D-Bus,
GTK+ 3, Pango, Python urllib.  Log also runs several Linux utilities; ifconfig, route, df, ps, free, and top.

Log is started by [Sugar](https://github.com/sugarlabs/sugar).

Log is packaged by Linux distributions;
* [Debian package sugar-log-activity](https://packages.debian.org/sugar-log-activity),
* [Ubuntu package sugar-log-activity](https://packages.ubuntu.com/sugar-log-activity), and;
* [Fedora package sugar-log](https://src.fedoraproject.org/).

How to develop?
===============

* setup a development environment for Sugar desktop,
* clone this repository,
* edit source files,
* test in Terminal by typing `sugar-activity3`

APIs
====

Code inside Log depends on several APIs, including;

* [PyGObject](https://lazka.github.io/pgi-docs/), and;
* [Sugar Toolkit](https://developer.sugarlabs.org/sugar3).

Branch master
=============

The `master` branch targets an environment with latest stable release
of [Sugar](https://github.com/sugarlabs/sugar), with dependencies on
latest stable release of Fedora and Debian distributions.

Release tags are v41 and higher.

Branch python2
==============

The `python2` branch is a backport of features and bug fixes from the
`master` branch for ongoing maintenance of the activity on Fedora 18,
Ubuntu 16.04 and Ubuntu 18.04 systems which don't have a Python 3
capable release of Sugar.

Release tags are v40.1 and higher, but lower than v41.
