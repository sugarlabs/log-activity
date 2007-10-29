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
            return '??'
                
        return self.__read_file('/ofw/mfg-data/'+item)
            
    def laptop_serial_number(self):
        return self._mfg_data('SN')

    def laptop_motherboard_number(self):
        return self._mfg_data('B#')

    def laptop_board_revision(self):
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
        if not os.path.exists('/sys/class/power_supply/olpc-battery/'+item):
            return '??'
        
        return self.__read_file('/sys/class/power_supply/olpc-battery/'+item).strip()

    def battery_serial_number(self):
        return self._battery_info('serial_number')
    
    def battery_capacity(self):
        return self._battery_info('capacity') + ' ' + self._battery_info('capacity_level')

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
        

class LogCollect:
    """Collect XO logfiles and machine metadata for reporting to OLPC

    """
    def __init__(self):
        self._mp = MachineProperties()


    def make_report(self, target='stdout'):
        """Create the report

        Arguments:
            target - where to save the logs, a path or stdout            

        """

        li = self.laptop_info()
        for k, v in li.iteritems():
            print k + ': ' +v
            
        print self._mp.battery_info()

    def read_file(self, filename):
        """Read the entire contents of a file and return it as a string"""

        data = ''

        f = open(filename)
        try:
            data = f.read()
        finally:
            f.close()

        return data

    def laptop_info(self):
        """Return a string with laptop serial, battery type, build, memory info, etc."""

        d=dict()
        d['laptop-info-version'] = '0.1'
        d['memfree'] = self._mp.memfree()
        d['disksize'] = '%d MB' % ( self._mp.disksize('/') / (1024*1024) ) 
        d['diskfree'] = '%d MB' % ( self._mp.diskfree('/') / (1024*1024) ) 
        d['olpc_build'] = self._mp.olpc_build()
        d['kernel_version'] = self._mp.kernel_version()
        d['uptime'] = self._mp.uptime()
        d['serial-number'] = self._mp.laptop_serial_number()
        d['motherboard-number'] = self._mp.laptop_motherboard_number()
        d['board-revision'] = self._mp.laptop_board_revision()
        d['uuid'] = self._mp.laptop_uuid()
        d['keyboard'] = self._mp.laptop_keyboard()
        d['wireless_mac'] = self._mp.laptop_wireless_mac()
        d['bios-version'] = self._mp.laptop_bios_version()
        d['country'] = self._mp.laptop_country()
        d['localization'] = self._mp.laptop_localization()
        #d['battery-serial-number'] = self._mp.battery_serial_number()
        #d['battery-capacity'] = self._mp.battery_capacity()       
    
        return d

lc = LogCollect()

lc.make_report()


