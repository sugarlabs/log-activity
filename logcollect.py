# Copyright (C) 2007, Pascal Scheffers <pascal@scheffers.net>
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation
# files (the "Software"), to deal in the Software without
# restriction, including without limitation the rights to use,
# copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following
# conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
# OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.
#
# log-collect for OLPC
#
# Compile a report containing:
#  * Basic system information:
#  ** Serial number
#  ** Battery type
#  ** Build number
#  ** Uptime
#  ** disk free space
#  ** ...
#  * Installed packages list
#  * All relevant log files (all of them, at first)
#
# The report is output as a tarfile
#
# This file has two modes:
# 1. It is a stand-alone python script, when invoked as 'log-collect'
# 2. It is a python module.

import os
import zipfile
import glob
import sys
import time

# The next couple are used by LogSend
import http.client
import mimetypes
import urllib.parse

MFG_DATA_PATHS = ['/ofw/mfg-data/', '/proc/device-tree/mfg-data/']


class MachineProperties:
    """Various machine properties in easy to access chunks.
    """

    def __read_file(self, filename):
        """Read the entire contents of a file and return it as a string"""

        data = ''

        f = open(filename)
        try:
            data = f.read()
        finally:
            f.close()

        return data

    def olpc_build(self):
        """Buildnumber, from /etc/issue"""
        # Is there a better place to get the build number?
        if not os.path.exists('/etc/issue'):
            return '#/etc/issue not found'

        # Needed, because we want to default to the first non blank line:
        first_line = ''

        for line in self.__read_file('/etc/issue').splitlines():
            if line.lower().find('olpc build') > -1:
                return line
            if first_line == '':
                first_line = line

        return first_line

    def uptime(self):
        for line in self.__read_file('/proc/uptime').splitlines():
            if line != '':
                return line
        return ''

    def loadavg(self):
        for line in self.__read_file('/proc/loadavg').splitlines():
            if line != '':
                return line
        return ''

    def kernel_version(self):
        for line in self.__read_file('/proc/version').splitlines():
            if line != '':
                return line
        return ''

    def memfree(self):
        line = ''

        for line in self.__read_file('/proc/meminfo').splitlines():
            if line.find('MemFree:') > -1:
                return line[8:].strip()

    def _trim_null(self, v):
        if v != '' and ord(v[len(v) - 1]) == 0:
            v = v[:len(v) - 1]
        return v

    def _mfg_data(self, item):
        """Return mfg data item from mfg-data directory"""

        mfg_path = None
        for test_path in MFG_DATA_PATHS:
            if os.path.exists(test_path + item):
                mfg_path = test_path + item
                break
        if mfg_path is None:
            return ''

        v = self._trim_null(self.__read_file(mfg_path))
        return v

    def laptop_serial_number(self):
        return self._mfg_data('SN')

    def laptop_motherboard_number(self):
        return self._mfg_data('B#')

    def laptop_board_revision(self):
        s = self._mfg_data('SG')[0:1]
        if s == '':
            return ''

        return '%02X' % ord(self._mfg_data('SG')[0:1])

    def laptop_uuid(self):
        return self._mfg_data('U#')

    def laptop_keyboard(self):
        kb = self._mfg_data('KM') + '-'
        kb += self._mfg_data('KL') + '-'
        kb += self._mfg_data('KV')
        return kb

    def laptop_wireless_mac(self):
        return self._mfg_data('WM')

    def laptop_bios_version(self):
        try:
            d = open('/proc/device-tree/openprom/model', 'r').read()
            v = self._trim_null(d)
            return v
        except:
            pass

        try:
            d = open('/ofw/openprom/model', 'r').read()
            v = self._trim_null(d)
            return v
        except:
            pass

        try:
            d = open('/sys/class/dmi/id/bios_version', 'r').read()
            v = self._trim_null(d)
            return v
        except:
            pass

        return ''

    def laptop_country(self):
        return self._mfg_data('LA')

    def laptop_localization(self):
        return self._mfg_data('LO')

    def _battery_info(self, item):
        root = '/sys/class/power_supply/olpc-battery/'
        if not os.path.exists(root + item):
            return ''

        return self.__read_file(root + item).strip()

    def battery_serial_number(self):
        return self._battery_info('serial_number')

    def battery_capacity(self):
        return self._battery_info('capacity') + ' ' + \
            self._battery_info('capacity_level')

    def battery_info(self):
        # Should be just:
        # return self._battery_info('uevent')
        # But because of a bug in the kernel, that has trash, lets filter:
        bi = ''
        for line in self._battery_info('uevent').splitlines():
            if line.startswith('POWER_'):
                bi += line + '\n'

        return bi

    def disksize(self, path):
        return os.statvfs(path).f_bsize * os.statvfs(path).f_blocks

    def diskfree(self, path):
        return os.statvfs(path).f_bsize * os.statvfs(path).f_bavail

    def _read_popen(self, cmd):
        p = os.popen(cmd)
        s = ''
        try:
            for line in p:
                s += line
        finally:
            p.close()

        return s

    def ifconfig(self):
        return self._read_popen('/sbin/ifconfig')

    def route_n(self):
        return self._read_popen('/sbin/route -n')

    def df_a(self):
        return self._read_popen('/bin/df -a')

    def ps_auxfwww(self):
        return self._read_popen('/bin/ps auxfwww')

    def usr_bin_free(self):
        return self._read_popen('/usr/bin/free')

    def top(self):
        return self._read_popen('/usr/bin/top -bn2')

    def installed_activities(self):
        s = ''
        for path in glob.glob('/usr/share/sugar/activities/*.activity'):
            s += os.path.basename(path) + '\n'

        home = os.path.expanduser('~')
        for path in glob.glob(os.path.join(home, 'Activities', '*')):
            s += '~' + os.path.basename(path) + '\n'

        return s


class LogCollect:
    """Collect XO logfiles and machine metadata for reporting to OLPC

    """
    def __init__(self):
        self._mp = MachineProperties()

    def write_logs(self, archive='', logbytes=15360):
        """Write a zipfile containing the tails of the logfiles and
        machine info of the XO

        Arguments:
            archive -   Specifies the location where to store the data
                        defaults to /dev/shm/logs-<xo-serial>.zip

            logbytes -  Maximum number of bytes to read from each log file.
                        0 means complete logfiles, not just the tail
                        -1 means only save machine info, no logs
        """
        # This function is crammed with try...except to make sure we
        # get as much data as possible, if anything fails.

        if archive == '':
            archive = '/dev/shm/logs.zip'
            try:
                # With serial number is more convenient, but might
                # fail for some reason...
                archive = '/dev/shm/logs-%s.zip' % \
                    self._mp.laptop_serial_number()
            except Exception:
                pass

        z = zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED)

        try:
            try:
                z.writestr('info.txt', self.laptop_info())
            except Exception as e:
                z.writestr('info.txt',
                           "logcollect: could not add info.txt: %s" % e)

            if logbytes > -1:
                # Include some log files from /var/log.
                for fn in ['dmesg', 'messages', 'cron', 'maillog', 'rpmpkgs',
                           'Xorg.0.log', 'spooler']:
                    try:
                        if os.access('/var/log/' + fn, os.F_OK):
                            if logbytes == 0:
                                z.write('/var/log/' + fn, 'var-log/' + fn)
                            else:
                                z.writestr('var-log/' + fn,
                                           self.file_tail('/var/log/' + fn,
                                                          logbytes))
                    except Exception as e:
                        z.writestr('var-log/' + fn,
                                   "logcollect: could not add %s: %s" %
                                   (fn, e))

                home = os.path.expanduser('~')
                here = os.path.join(home, '.sugar/default/logs/*.log')
                for path in glob.glob(here):
                    if os.access(path, os.F_OK):
                        pref = 'sugar-logs/'
                        name = os.path.join(pref, os.path.basename(path))
                        try:
                            if logbytes == 0:
                                z.write(path, name)
                            else:
                                z.writestr(name,
                                           self.file_tail(path, logbytes))
                        except Exception as e:
                            z.writestr(name,
                                       "logcollect: could not add %s: %s" %
                                       (name, e))
                here = os.path.join(home, '.sugar/default/logs/*/*.log')
                for path in glob.glob(here):
                    if os.access(path, os.F_OK):
                        when = os.path.basename(os.path.dirname(path))
                        pref = 'sugar-logs-%s/' % when
                        name = os.path.join(pref, os.path.basename(path))
                        try:
                            if logbytes == 0:
                                z.write(path, name)
                            else:
                                z.writestr(name,
                                           self.file_tail(path, logbytes))
                        except Exception as e:
                            z.writestr(name,
                                       "logcollect: could not add %s: %s" %
                                       (name, e))
                try:
                    z.write('/etc/resolv.conf')
                except Exception as e:
                    z.writestr('/etc/resolv.conf',
                               "logcollect: could not add resolv.conf: %s" % e)

        except Exception as e:
            print('While creating zip archive: %s' % e)

        z.close()

        return archive

    def file_tail(self, filename, tailbytes):
        """Read the tail (end) of the file

        Arguments:
            filename    The name of the file to read
            tailbytes   Number of bytes to include or 0 for entire file
        """

        data = ''

        f = open(filename)
        try:
            fsize = os.stat(filename).st_size

            if tailbytes > 0 and fsize > tailbytes:
                f.seek(-tailbytes, 2)

            data = f.read()
        finally:
            f.close()

        return data

    def make_report(self, target='stdout'):
        """Create the report

        Arguments:
            target - where to save the logs, a path or stdout

        """

        li = self.laptop_info()
        for k, v in li.items():
            print(k + ': ' + v)

        print(self._mp.battery_info())

    def laptop_info(self):
        """Return a string with laptop serial, battery type, build,
        memory info, etc."""

        s = ''
        try:
            # Do not include UUID!
            s += 'laptop-info-version: 1.0\n'
            s += 'clock: %f\n' % time.clock()
            s += 'date: %s\n' % time.strftime("%a, %d %b %Y %H:%M:%S +0000",
                                              time.gmtime())
            s += 'memfree: %s\n' % self._mp.memfree()
            s += 'disksize: %s MB\n' % (self._mp.disksize('/') / (1024 * 1024))
            s += 'diskfree: %s MB\n' % (self._mp.diskfree('/') / (1024 * 1024))
            s += 'olpc_build: %s\n' % self._mp.olpc_build()
            s += 'kernel_version: %s\n' % self._mp.kernel_version()
            s += 'uptime: %s\n' % self._mp.uptime()
            s += 'loadavg: %s\n' % self._mp.loadavg()
            s += 'serial-number: %s\n' % self._mp.laptop_serial_number()
            s += 'motherboard-number: %s\n' % \
                 self._mp.laptop_motherboard_number()
            s += 'board-revision: %s\n' % self._mp.laptop_board_revision()
            s += 'keyboard: %s\n' % self._mp.laptop_keyboard()
            s += 'wireless_mac: %s\n' % self._mp.laptop_wireless_mac()
            s += 'firmware: %s\n' % self._mp.laptop_bios_version()
            s += 'country: %s\n' % self._mp.laptop_country()
            s += 'localization: %s\n' % self._mp.laptop_localization()

            s += self._mp.battery_info()

            s += "\n[/sbin/ifconfig]\n%s\n" % self._mp.ifconfig()
            s += "\n[/sbin/route -n]\n%s\n" % self._mp.route_n()

            s += '\n[Installed Activities]\n%s\n' % \
                 self._mp.installed_activities()

            s += '\n[df -a]\n%s\n' % self._mp.df_a()
            s += '\n[ps auxwww]\n%s\n' % self._mp.ps_auxfwww()
            s += '\n[free]\n%s\n' % self._mp.usr_bin_free()
            s += '\n[top -bn2]\n%s\n' % self._mp.top()
        except Exception as e:
            s += '\nException while building info:\n%s\n' % e

        return s


class LogSend:

    # post_multipart and encode_multipart_formdata have been taken from
    #  http://aspn.activestate.com/ASPN/Cookbook/Python/Recipe/146306
    def post_multipart(self, host, selector, fields, files):
        """
        Post fields and files to an http host as multipart/form-data.
        fields is a sequence of (name, value) elements for regular
        form fields.  files is a sequence of (name, filename, value)
        elements for data to be uploaded as files Return the server's
        response page.
        """
        content_type, body = self.encode_multipart_formdata(fields, files)
        h = http.client.HTTP(host)
        h.putrequest('POST', selector)
        h.putheader('content-type', content_type)
        h.putheader('content-length', str(len(body)))
        h.putheader('Host', host)
        h.endheaders()
        h.send(body)
        errcode, errmsg, headers = h.getreply()
        return h.file.read()

    def encode_multipart_formdata(self, fields, files):
        """
        fields is a sequence of (name, value) elements for regular
        form fields.  files is a sequence of (name, filename, value)
        elements for data to be uploaded as files Return
        (content_type, body) ready for httplib.HTTP instance
        """
        BOUNDARY = '----------ThIs_Is_tHe_bouNdaRY_$'
        CRLF = '\r\n'
        L = []
        for (key, value) in fields:
            L.append('--' + BOUNDARY)
            L.append('Content-Disposition: form-data; name="%s"' % key)
            L.append('')
            L.append(value)
        for (key, filename, value) in files:
            L.append('--' + BOUNDARY)
            L.append('Content-Disposition: form-data; '
                     'name="%s"; filename="%s"' % (key, filename))
            L.append('Content-Type: %s' % self.get_content_type(filename))
            L.append('')
            L.append(value)
        L.append('--' + BOUNDARY + '--')
        L.append('')
        body = CRLF.join(L)
        content_type = 'multipart/form-data; boundary=%s' % BOUNDARY
        return content_type, body

    def read_file(self, filename):
        """Read the entire contents of a file and return it as a string"""

        data = ''

        f = open(filename)
        try:
            data = f.read()
        finally:
            f.close()

        return data

    def get_content_type(self, filename):
        return mimetypes.guess_type(filename)[0] or 'application/octet-stream'

    def http_post_logs(self, url, archive):
        # host, selector, fields, files
        files = ('logs', os.path.basename(archive), self.read_file(archive)),

        # Client= olpc will make the server return just "OK" or "FAIL"
        fields = ('client', 'xo'),
        urlparts = urllib.parse.urlsplit(url)
        print("Sending logs to %s" % url)
        r = self.post_multipart(urlparts[1], urlparts[2], fields, files)
        print(r)
        return (r == 'OK')


# This script is dual-mode, it can be used as a command line tool and as
# a library.
if sys.argv[0].endswith('logcollect.py') or \
        sys.argv[0].endswith('logcollect'):
    print('log-collect utility 1.0')

    lc = LogCollect()
    ls = LogSend()

    logs = ''
    mode = 'http'

    if len(sys.argv) == 1:
        print("""logcollect.py - send your XO logs to OLPC

Usage:
    logcollect.py http://server.name/submit.php
                         - submit logs to a server

    logcollect.py file:/media/xxxx-yyyy/mylog.zip
                         - save the zip file on a USB device or SD card

    logcollect.py all file:/media/xxxx-yyyy/mylog.zip
                        - Save to zip file and include ALL logs

    logcollect.py none http
                        - Just send info.txt, but no logs via http.

    logcollect.py none file
                        - Just save info.txt in /dev/shm/logs-SN123.zip

    If you specify 'all' or 'none' you must specify http or file as well.
        """)
        sys.exit()

    logbytes = 15360
    if len(sys.argv) > 1:
        mode = sys.argv[len(sys.argv) - 1]
        if sys.argv[1] == 'all':
            logbytes = 0
        if sys.argv[1] == 'none':
            logbytes = -1

    if mode.startswith('file'):
        # file://
        logs = mode[5:]

    logs = lc.write_logs(logs, logbytes)
    print('Logs saved in %s' % logs)

    sent_ok = False
    if len(sys.argv) > 1:
        mode = sys.argv[len(sys.argv) - 1]

    if mode.startswith('http'):
        print("Trying to send the logs using HTTP (web)")
        if len(mode) == 4:
            print("No default log destination, aborting")
            sys.exit(1)
        else:
            url = mode

        if ls.http_post_logs(url, logs):
            print("Logs were sent.")
            sent_ok = True
        else:
            print("FAILED to send logs.")

    if sent_ok:
        os.remove(logs)
        print("Logs were sent, tempfile deleted.")
