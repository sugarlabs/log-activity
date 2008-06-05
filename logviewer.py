#!/usr/bin/env python

# Copyright (C) 2006-2007, Eduardo Silva <edsiper@gmail.com>
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

import os
import logging
from gettext import gettext as _

import re

import gtk
import dbus
import pango
import pygtk
import gobject
import gnomevfs

from sugar.activity import activity
from sugar import env
from sugar.graphics import iconentry
from sugar.graphics.toolbutton import ToolButton
from sugar.graphics.toggletoolbutton import ToggleToolButton
from sugar.graphics.palette import Palette
from sugar.graphics.alert import NotifyAlert
from logcollect import LogCollect, LogSend

# Should be builtin to sugar.graphics.alert.NotifyAlert...
def _notify_response_cb(notify, response, activity):
    activity.remove_alert(notify)

class MultiLogView(gtk.HBox):
    def __init__(self, paths, extra_files):
        gtk.HBox.__init__(self, False, 3)
        
        self.paths = paths
        self.extra_files = extra_files

        self.active_log = None
        self.logs = {}

        self.search_text = ''
        
        self._build_treeview()
        self._build_textview()
        
        self.show_all()

        self._configure_watcher()
        self._find_logs()

    def _build_treeview(self):
        self._treeview = gtk.TreeView()

        self._treeview.set_rules_hint(True)
        self._treeview.connect('cursor-changed', self._cursor_changed_cb)

        self._treemodel = gtk.TreeStore(gobject.TYPE_STRING)

        sorted = gtk.TreeModelSort(self._treemodel)
        sorted.set_sort_column_id(0, gtk.SORT_ASCENDING)
        sorted.set_sort_func(0, self._sort_logfile)
        self._treeview.set_model(sorted)

        renderer = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_('Log Files'), renderer, text=0)
        self._treeview.append_column(col)

        self.path_iter = {}
        for p in self.paths:
            self.path_iter[p] = self._treemodel.append(None, [p])

        if len(self.extra_files):
            self.extra_iter = self._treemodel.append(None, [_('Other')])

        scroll = gtk.ScrolledWindow()
        scroll.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        scroll.add(self._treeview)

        scroll.set_size_request(gtk.gdk.screen_width()*30/100, 0)
        self.pack_start(scroll, True, True, 0)

    def _build_textview(self):
        self._textview = gtk.TextView()
        self._textview.set_wrap_mode(gtk.WRAP_NONE)
        
        pangoFont = pango.FontDescription('Courier 8')
        self._textview.modify_font(pangoFont)

        bgcolor = gtk.gdk.color_parse('#FFFFFF')
        self._textview.modify_base(gtk.STATE_NORMAL, bgcolor)

        self._textview.set_editable(False)

        self._tagtable = gtk.TextTagTable()
        hilite_tag = gtk.TextTag('search-hilite')
        hilite_tag.props.background = '#FFFFB0'
        self._tagtable.add(hilite_tag)
        select_tag = gtk.TextTag('search-select')
        select_tag.props.background = '#B0B0FF'
        self._tagtable.add(select_tag)
        
        scroll = gtk.ScrolledWindow()
        scroll.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        scroll.add(self._textview)

        scroll.set_size_request(gtk.gdk.screen_width()*70/100, 0)
        self.pack_start(scroll, True, True, 0)
        
    def _sort_logfile(self, treemodel, itera, iterb):
        a = treemodel.get_value(itera, 0)
        b = treemodel.get_value(iterb, 0)
        if a == None or b == None:
            return 0
        a = a.lower()
        b = b.lower()
        
        # Filenames are parased as xxxx-YYY.log
        # Sort first by xxxx, then numerically by YYY.
        logre = re.compile(r'(.*)-(\d+)\.log', re.IGNORECASE)
        ma = logre.match(a)
        mb = logre.match(b)
        if ma and mb:
            if ma.group(1) > mb.group(1): return 1
            if ma.group(1) < mb.group(1): return -1
            if int(ma.group(2)) > int(mb.group(2)): return 1
            if int(ma.group(2)) < int(mb.group(2)): return -1
            return 0
        else:
            if a > b: return 1
            if a < b: return -1
            return 0

    def _configure_watcher(self):
        # Setting where gnomeVFS will be watching
        for p in self.paths:
            gnomevfs.monitor_add('file://' + p,
                                 gnomevfs.MONITOR_DIRECTORY,
                                 self._log_file_changed_cb)

        for f in self.extra_files:
            gnomevfs.monitor_add('file://' + f,
                             gnomevfs.MONITOR_FILE,
                             self._log_file_changed_cb)

    def _log_file_changed_cb(self, monitor_uri, info_uri, event):
        path = info_uri.split('file://')[-1]
        dir, logfile = os.path.split(path)

        if event == gnomevfs.MONITOR_EVENT_CHANGED:
            if self.logs.has_key(logfile):
                self.logs[logfile].update()
        elif event == gnomevfs.MONITOR_EVENT_DELETED:
            if self.logs.has_key(logfile):
                self._remove_log_file(logfile)
        elif event == gnomevfs.MONITOR_EVENT_CREATED:
            self._add_log_file(path)

    def _cursor_changed_cb(self, treeview):
        treestore, iter = self._treeview.get_selection().get_selected()
        self._show_log(treestore.get_value(iter, 0))

    def _show_log(self, logfile):
        if self.logs.has_key(logfile):
            log = self.logs[logfile]
            self._textview.set_buffer(log)
            self._textview.scroll_to_mark(log.get_insert(), 0)
            self.active_log = log

    def _find_logs(self):
        for path in self.paths:
            try:
                files = os.listdir(path)
            except:
                logging.debug(_("ERROR: Failed to look for files in '%(path)s'.") % {'path': path})
            else:
                for logfile in files:
                    self._add_log_file(os.path.join(path, logfile))

        for logfile in self.extra_files:
            self._add_log_file(logfile)

        self._treeview.expand_all()

    def _add_log_file(self, path):
        if os.path.isdir(path):
            return False

        if not os.path.exists(path):
            logging.debug(_("ERROR: File '%(file)s' does not exist.") % {'file': path})
            return False

        if not os.access(path, os.R_OK): 
            logging.debug(_("ERROR: Unable to read file '%(file)s'.") % {'file': path})
            return False

        dir, logfile = os.path.split(path)

        if not self.logs.has_key(logfile):
            parent = self.extra_iter
            if self.path_iter.has_key(dir):
                parent = self.path_iter[dir]
            iter = self._treemodel.append(parent, [logfile])
            
            model = LogBuffer(self._tagtable, path, iter)
            self.logs[logfile] = model

        log = self.logs[logfile]
        log.update()
        written = log._written

        if self.active_log == None:
            self.active_log = log
            self._show_log(logfile)
            iter = self._treeview.get_model().convert_child_iter_to_iter(None, log.iter)
            self._treeview.get_selection().select_iter(iter)

        if written > 0 and self.active_log == log:
            self._textview.scroll_to_mark(log.get_insert(), 0)

    def _remove_log_file(self, logfile):
        log = self.logs[logfile]
        self._treemodel.remove(log.iter)
        if self.active_log == log:
            self.active_log = None
        del self.logs[logfile]

    def set_search_text(self, text):
        self.search_text = text
        
        buffer = self._textview.get_buffer()
        
        start, end = buffer.get_bounds()
        buffer.remove_tag_by_name('search-hilite', start, end)
        buffer.remove_tag_by_name('search-select', start, end)
        
        iter = buffer.get_start_iter()
        while True:
            next = iter.forward_search(text, 0)
            if next is None: break
            start, end = next
            buffer.apply_tag_by_name('search-hilite', start, end)
            iter = end

        if self.get_next_result('current'):
            self.search_next('current')
        elif self.get_next_result('backward'):
            self.search_next('backward')

    def get_next_result(self, dir):
        buffer = self._textview.get_buffer()
        
        if dir == 'forward':
            iter = buffer.get_iter_at_mark(buffer.get_insert())
            iter.forward_char()
        else:
            iter = buffer.get_iter_at_mark(buffer.get_insert())
            
        if dir == 'backward':
            return iter.backward_search(self.search_text, 0)
        else:
            return iter.forward_search(self.search_text, 0)

    def search_next(self, dir):        
        next = self.get_next_result(dir)
        if next:
            buffer = self._textview.get_buffer()

            start, end = buffer.get_bounds()
            buffer.remove_tag_by_name('search-select', start, end)

            start, end = next
            buffer.apply_tag_by_name('search-select', start, end)
            
            buffer.place_cursor(start)
            
            self._textview.scroll_to_iter(start, 0.1)
            self._textview.scroll_to_iter(end, 0.1)

class LogBuffer(gtk.TextBuffer):
    def __init__(self, tagtable, logfile, iter):
        gtk.TextBuffer.__init__(self, tagtable)

        self.logfile = logfile
        self._pos = 0
        self.iter = iter
        self.update()

    def append_formatted_text(self, text):
        # Remove ANSI escape codes.
        # todo- Handle a subset of them.
        strip_ansi = re.compile(r'\033\[[\d;]*m')
        text = strip_ansi.sub('', text)
        self.insert(self.get_end_iter(), text.encode('utf8'))

    def update(self):
        try:
            f = open(self.logfile, 'r')
            init_pos = self._pos
    
            f.seek(self._pos)
            self.append_formatted_text(f.read())
            self._pos = f.tell()
            f.close()
    
            self._written = (self._pos - init_pos)
        except:
            self.insert(self.get_end_iter(), _("Error: Can't open file '%s'\n") % self.logfile)
            self._written = 0

class LogActivity(activity.Activity):
    def __init__(self, handle):
        activity.Activity.__init__(self, handle)
        self.set_title(_('Log'))

        # Paths to watch: ~/.sugar/someuser/logs, /var/log
        paths = []
        paths.append(env.get_profile_path('logs'))
        paths.append('/var/log')

        # Additional misc files.
        ext_files = []
        ext_files.append(os.path.expanduser('~/.bash_history'))

        self.viewer = MultiLogView(paths, ext_files)
        self.set_canvas(self.viewer)

        self._build_toolbox()
        
        self.show()

    def _build_toolbox(self):
        toolbox = activity.ActivityToolbox(self)

        edit_toolbar = activity.EditToolbar()
        
        edit_toolbar.paste.props.visible = False
        edit_toolbar.undo.props.visible = False
        edit_toolbar.redo.props.visible = False
        edit_toolbar.separator.props.visible = False
        edit_toolbar.copy.connect('clicked', self._copy_cb)
        
        wrap_btn = ToggleToolButton('format-justify-left')
        wrap_btn.set_tooltip(_('Word Wrap'))
        wrap_btn.connect('clicked', self._wrap_cb)
        wrap_btn.show()
        edit_toolbar.insert(wrap_btn, -1)
        
        separator = gtk.SeparatorToolItem()
        separator.set_draw(False)
        separator.set_expand(True)
        edit_toolbar.insert(separator, -1)
        separator.show()

        search_item = gtk.ToolItem()

        self._search_entry = iconentry.IconEntry()
        self._search_entry.set_icon_from_name(iconentry.ICON_ENTRY_PRIMARY,
                                                'system-search')
        self._search_entry.add_clear_button()
        self._search_entry.connect('activate', self._search_entry_activate_cb)
        self._search_entry.connect('changed', self._search_entry_changed_cb)

        width = int(gtk.gdk.screen_width() / 3)
        self._search_entry.set_size_request(width, -1)
        self._search_entry.show()
        search_item.add(self._search_entry)

        search_item.show()
        edit_toolbar.insert(search_item, -1)

        self._search_prev = ToolButton('go-previous-paired')
        self._search_prev.set_tooltip(_('Previous'))
        #self._search_prev.props.sensitive = False
        self._search_prev.connect('clicked', self._search_prev_cb)
        self._search_prev.show()
        edit_toolbar.insert(self._search_prev, -1)

        self._search_next = ToolButton('go-next-paired')
        self._search_next.set_tooltip(_('Next'))
        #self._search_next.props.sensitive = False
        self._search_next.connect('clicked', self._search_next_cb)
        self._search_next.show()
        edit_toolbar.insert(self._search_next, -1)
        
        self._update_search_buttons()
        
        edit_toolbar.show()
        toolbox.add_toolbar(_('Edit'), edit_toolbar)

        tools_toolbar = gtk.Toolbar()

        delete_btn = ToolButton('list-remove')
        delete_btn.set_tooltip(_('Delete Log File'))
        delete_btn.connect('clicked', self._delete_log_cb)
        delete_btn.show()
        tools_toolbar.insert(delete_btn, -1)

        separator = gtk.SeparatorToolItem()
        separator.set_expand(True)
        separator.set_draw(False)
        separator.show()
        tools_toolbar.insert(separator, -1)
        
        self.collector_palette = CollectorPalette(self)
        collector_btn = ToolButton('zoom-best-fit')
        collector_btn.set_palette(self.collector_palette)
        collector_btn.connect('clicked', self._logviewer_cb)
        collector_btn.show()
        tools_toolbar.insert(collector_btn, -1)

        tools_toolbar.show()
        toolbox.add_toolbar(_('Tools'), tools_toolbar)
        
        toolbox.show()
        self.set_toolbox(toolbox)

        # Hide unsupported Activity tools.
        toolbar = toolbox.get_activity_toolbar()
        toolbar.share.hide()
        toolbar.keep.hide()
        
    def _copy_cb(self, button):
        if self.viewer.active_log:
            self.viewer.active_log.copy_clipboard(gtk.clipboard_get())

    def _wrap_cb(self, button):
        if button.get_active():
            self.viewer._textview.set_wrap_mode(gtk.WRAP_WORD_CHAR)
        else:
            self.viewer._textview.set_wrap_mode(gtk.WRAP_NONE)

    def _search_entry_activate_cb(self, entry):
        self.viewer.set_search_text(entry.props.text)
        self._update_search_buttons()

    def _search_entry_changed_cb(self, entry):
        self.viewer.set_search_text(entry.props.text)
        self._update_search_buttons()
    
    def _search_prev_cb(self, button):
        self.viewer.search_next('backward')
        self._update_search_buttons()
    
    def _search_next_cb(self, button):
        self.viewer.search_next('forward')
        self._update_search_buttons()
    
    def _update_search_buttons(self,):
        if len(self.viewer.search_text) == 0:
            self._search_prev.props.sensitive = False
            self._search_next.props.sensitive = False
        else:
            prev = self.viewer.get_next_result('backward')
            next = self.viewer.get_next_result('forward')
            self._search_prev.props.sensitive = prev != None
            self._search_next.props.sensitive = next != None
    
    def _delete_log_cb(self, widget):
        if self.viewer.active_log:
            logfile = self.viewer.active_log.logfile
            try:
                os.remove(logfile)
            except OSError, err:
                notify = NotifyAlert()
                notify.props.title = _('Error')
                notify.props.msg = _('%(error)s when deleting %(file)s') % \
                    { 'error': err.strerror, 'file': logfile }
                notify.connect('response', _notify_response_cb, self)
                self.add_alert(notify)

    def _logviewer_cb(self, widget):
        self.collector_palette.popup(True)

class CollectorPalette(Palette):
    _DEFAULT_SERVER = 'http://olpc.scheffers.net/olpc/submit.tcl'

    def __init__(self, handler):
        Palette.__init__(self, _('Log Collector: Send XO information'))

        self._handler = handler
        
        self._collector = LogCollect()
        
        label = gtk.Label(
            _('Log collector sends information about the system\n'\
              'and running processes to a central server.  Use\n'\
              'this option if you want to report a problem.'))
        
        send_button = gtk.Button(_('Send information'))
        send_button.connect('clicked', self._on_send_button_clicked_cb)

        vbox = gtk.VBox(False, 5)
        vbox.pack_start(label)
        vbox.pack_start(send_button)
        vbox.show_all()

        self.set_content(vbox)

    def _on_send_button_clicked_cb(self, button):
        success = True
        try:
            data = self._collector.write_logs()
            sender = LogSend()
            success = sender.http_post_logs(self._DEFAULT_SERVER, data)
        except:
            success = False

        os.remove(data)
        self.popdown(True)

        title = ''
        msg = ''
        if success:
            title = _('Logs sent')
            msg = _('The logs were uploaded to the server.')
        else:
            title = _('Logs not sent')
            msg = _('The logs could not be uploaded to the server. '\
                    'Please check your network connection.')

        notify = NotifyAlert()
        notify.props.title = title
        notify.props.msg = msg
        notify.connect('response', _notify_response_cb, self._handler)
        self._handler.add_alert(notify)

