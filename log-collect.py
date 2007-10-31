#!/usr/bin/env python
# Copyright (C) 2006-2007, Pascal Scheffers <pascal@scheffers.net> 
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

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
import httplib
import mimetypes

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
                first_line=line

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

    def _mfg_data(self, item):
        """Return mfg data item from /ofw/mfg-data/"""
        
        if not os.path.exists('/ofw/mfg-data/'+item):
            return ''
        
        v = self.__read_file('/ofw/mfg-data/'+item)
        # Remove trailing 0 character, if any:
        if v != '' and ord(v[len(v)-1]) == 0:
            v = v[:len(v)-1]
        
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
        return self._mfg_data('BV')

    def laptop_country(self):
        return self._mfg_data('LA')

    def laptop_localization(self):
        return self._mfg_data('LO')
    
    def _battery_info(self, item):
        """ from  /sys/class/power-supply/olpc-battery/ """
        root = '/sys/class/power_supply/olpc-battery/'
        if not os.path.exists(root+item):
            return ''
        
        return self.__read_file(root+item).strip()

    def battery_serial_number(self):
        return self._battery_info('serial_number')
    
    def battery_capacity(self):
        return self._battery_info('capacity') + ' ' + \
                    self._battery_info('capacity_level')

    def battery_info(self):
        #Should be just:
        #return self._battery_info('uevent')
        
        #But because of a bug in the kernel, that has trash, lets filter:
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
    
    def installed_activities(self):
        
        s = ''
        
        for path in glob.glob('/usr/share/activities/*.activity'):
            s += os.path.basename(path) + '\n'

        for path in glob.glob('/home/olpc/Activities/*'):
            s += '~' + os.path.basename(path) + '\n'
            
        return s
        
        

class LogCollect:
    """Collect XO logfiles and machine metadata for reporting to OLPC

    """
    def __init__(self):
        self._mp = MachineProperties()

    def write_logs(self, archive='', logbytes=15360):
        """Write a zipfile containing the tails of the logfiles and machine info of the XO
        
        Arguments:
            archive -   Specifies the location where to store the data
                        defaults to /dev/shm/logs-<xo-serial>.zip
                        
            logbytes -  Maximum number of bytes to read from each log file.
                        0 means complete logfiles, not just the tail
                        -1 means only save machine info, no logs
        """
        
        if archive=='':            
            archive = '/dev/shm/logs-%s.zip' % self._mp.laptop_serial_number()
            # Oops - null character in serialno!
            #archive = '/dev/shm/logs.zip'
            
        z = zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED)
        
        try:         
            z.writestr('info.txt', self.laptop_info())
            
            if logbytes > -1:            
                # Include some log files from /var/log.
                for fn in ['dmesg', 'messages', 'cron', 'maillog','rpmpkgs',
                           'Xorg.0.log', 'spooler']:
                    if os.access('/var/log/'+fn, os.F_OK):
                        if logbytes == 0:
                            z.write('/var/log/'+fn, 'var-log/'+fn)
                        else:
                            z.writestr('var-log/'+fn,
                                       self.file_tail('/var/log/'+fn, logbytes))
                    
                # Include all current ones from sugar/logs
                for path in glob.glob('/home/olpc/.sugar/default/logs/*.log'):
                    if os.access(path, os.F_OK):
                        if logbytes == 0:
                            z.write(path, 'sugar-logs/'+os.path.basename(path))
                        else:
                            z.writestr('sugar-logs/'+os.path.basename(path),
                                       self.file_tail(path, logbytes))
                 
                z.write('/etc/resolv.conf')
        except Exception, e:
            print 'While creating zip archive: %s' % e            
        
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
        for k, v in li.iteritems():
            print k + ': ' +v
            
        print self._mp.battery_info()

    def laptop_info(self):
        """Return a string with laptop serial, battery type, build, memory info, etc."""

        s = ''        

        # Do not include UUID!
        s += 'laptop-info-version: 1.0\n'
        s += 'clock: %f\n' % time.clock()
        s += 'date: %s' % time.strftime("%a, %d %b %Y %H:%M:%S +0000",
                                        time.gmtime())
        s += 'memfree: %s\n' % self._mp.memfree()
        s += 'disksize: %s MB\n' % ( self._mp.disksize('/') / (1024*1024) ) 
        s += 'diskfree: %s MB\n' % ( self._mp.diskfree('/') / (1024*1024) ) 
        s += 'olpc_build: %s\n' % self._mp.olpc_build()
        s += 'kernel_version: %s\n' % self._mp.kernel_version()
        s += 'uptime: %s\n' % self._mp.uptime()
        s += 'loadavg: %s\n' % self._mp.loadavg()        
        s += 'serial-number: %s\n' % self._mp.laptop_serial_number()
        s += 'motherboard-number: %s\n' % self._mp.laptop_motherboard_number()
        s += 'board-revision: %s\n' %  self._mp.laptop_board_revision()
        s += 'keyboard: %s\n' %  self._mp.laptop_keyboard()
        s += 'wireless_mac: %s\n' %  self._mp.laptop_wireless_mac()
        s += 'firmware: %s\n' %  self._mp.laptop_bios_version()
        s += 'country: %s\n' % self._mp.laptop_country()
        s += 'localization: %s\n' % self._mp.laptop_localization()
            
        s += self._mp.battery_info()
        
        s += "\n[/sbin/ifconfig]\n%s\n" % self._mp.ifconfig()
        s += "\n[/sbin/route -n]\n%s\n" % self._mp.route_n()
        
        s += '\n[Installed Activities]\n%s\n' % self._mp.installed_activities()
        
        return s

class LogSend:
    
    # post_multipart and encode_multipart_formdata have been taken from
    #  http://aspn.activestate.com/ASPN/Cookbook/Python/Recipe/146306
    def post_multipart(self, host, selector, fields, files):
        """
        Post fields and files to an http host as multipart/form-data.
        fields is a sequence of (name, value) elements for regular form fields.
        files is a sequence of (name, filename, value) elements for data to be uploaded as files
        Return the server's response page.        
        """
        content_type, body = self.encode_multipart_formdata(fields, files)
        h = httplib.HTTP(host)
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
        fields is a sequence of (name, value) elements for regular form fields.
        files is a sequence of (name, filename, value) elements for data to be uploaded as files
        Return (content_type, body) ready for httplib.HTTP instance
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
            L.append('Content-Disposition: form-data; name="%s"; filename="%s"' % (key, filename))
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
        
    def http_post_logs(self, hostname, url, archive):
        #host, selector, fields, files
        files = ('logs', os.path.basename(archive), self.read_file(archive)),
        
        # Client= olpc will make the server return just "OK" or "FAIL"
        fields = ('client', 'xo'),
                
        r = self.post_multipart(hostname, url, fields, files)
        print r
        return (r == 'OK')


# This script is dual-mode, it can be used as a command line tool and as
# a library. 
if sys.argv[0].endswith('log-collect.py') or \
        sys.argv[0].endswith('log-collect'):
    print 'log-collect utility 1.0'
        
    lc = LogCollect()
    ls = LogSend()
    
    logs = lc.write_logs()
    print 'Logs saved in %s' % logs

    if sys.argv[len(sys.argv)-1] == 'http':
        print "Trying to send the logs using HTTP (web)"
        if ls.http_post_logs('pascal.scheffers.net', '/olpc/submit.tcl', logs):
            print "Logs were sent."
        else:
            print "FAILED to send logs."
       



