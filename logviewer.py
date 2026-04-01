# Copyright (C) 2006-2007, Eduardo Silva <edsiper@gmail.com>
# Copyright (C) 2009 Simon Schampijer
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
import time
import logging
from gettext import gettext as _

import re

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Gio
from gi.repository import Gdk
from gi.repository import Gtk
from gi.repository import Pango

from sugar4.activity import activity
from sugar4.activity.widgets import ActivityToolbarButton
from sugar4 import env
from sugar4.graphics import iconentry
from sugar4.graphics.toolbutton import ToolButton
from sugar4.graphics.toggletoolbutton import ToggleToolButton
from sugar4.graphics.palette import Palette
from sugar4.graphics.alert import NotifyAlert
from logcollect import LogCollect
from sugar4.graphics.toolbarbox import ToolbarBox
from sugar4.graphics.toolbarbox import ToolbarButton
from sugar4.activity.widgets import CopyButton, StopButton
from sugar4.datastore import datastore


_AUTOSEARCH_TIMEOUT = 1000


# Should be builtin to sugar.graphics.alert.NotifyAlert...
def _notify_response_cb(notify, response, activity):
    activity.remove_alert(notify)


class MultiLogView(Gtk.Paned):

    def __init__(self, paths, extra_files):
        GObject.GObject.__init__(self)
        self.set_orientation(Gtk.Orientation.HORIZONTAL)

        self.paths = paths
        self.extra_files = extra_files
        # Hold a reference to the monitors so they don't get disposed
        self._gio_monitors = []
        self.first_file_open = '|'

        self.active_log = None
        self.logs = {}

        self.search_text = ''

        self._build_treeview()
        self._build_textview()

        self._configure_watcher()
        self._find_logs()

    def _format_col(self, col, cell, model, iterator, user_data):
        treestore, text_iter = self._treeview.get_selection().get_selected()
        direc, filename = self.first_file_open.split('|')
        if text_iter is None:
            if filename == model.get_value(iterator, 0):
                parent_iter = model.iter_parent(iterator)
                if parent_iter is None:
                    cell.props.background_rgba = Gdk.RGBA(1.0, 1.0, 1.0, 1)
                else:
                    if direc == model.get_value(parent_iter, 0):
                        cell.props.background_rgba = \
                            Gdk.RGBA(0.75, 0.75, 0.75, 1)
                    else:
                        cell.props.background_rgba = Gdk.RGBA(1.0, 1.0, 1.0, 1)
            else:
                cell.props.background_rgba = Gdk.RGBA(1.0, 1.0, 1.0, 1)
        else:
            cell.props.background_rgba = Gdk.RGBA(1.0, 1.0, 1.0, 1)

    def _build_treeview(self):
        self._treeview = Gtk.TreeView()

        self._treeview.connect('cursor-changed', self._cursor_changed_cb)
        self._treeview.set_enable_search(False)

        self._treemodel = Gtk.TreeStore(GObject.TYPE_STRING,
                                        GObject.TYPE_STRING)

        sorted = Gtk.TreeModelSort.new_with_model(self._treemodel)

        sorted.set_sort_column_id(0, Gtk.SortType.ASCENDING)
        sorted.set_sort_func(0, self._sort_logfile)
        self._treeview.set_model(sorted)

        renderer = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(_('Log Files'), renderer, text=0)
        col.set_cell_data_func(renderer, self._format_col, 0)
        self._treeview.append_column(col)

        renderer = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn('', renderer, text=1)
        self._treeview.append_column(col)
        col.set_visible(False)

        self.path_iter = {}
        for p in self.paths:
            self.path_iter[p] = self._treemodel.append(None, [p, ''])

        if len(self.extra_files):
            self.extra_iter = self._treemodel.append(None, [_('Other'), ''])

        self.list_scroll = Gtk.ScrolledWindow()
        self.list_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                                    Gtk.PolicyType.AUTOMATIC)
        self.list_scroll.set_child(self._treeview)
        self.list_scroll.set_size_request(300, -1)

        self.set_start_child(self.list_scroll)

    def _build_textview(self):
        self._textview = Gtk.TextView()
        self._textview.set_wrap_mode(Gtk.WrapMode.NONE)

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"textview { font-family: Monospace; background-color: #FFFFFF; }")
        
        context = self._textview.get_style_context()
        context.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self._textview.set_editable(False)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self._textview)

        self.set_end_child(scroll)

    def _sort_logfile(self, treemodel, itera, iterb, user_data=None):
        a = treemodel.get_value(itera, 0)
        b = treemodel.get_value(iterb, 0)
        if a is None or b is None:
            return 0
        a = a.lower()
        b = b.lower()

        # Filenames are parased as xxxx-YYY.log
        # Sort first by xxxx, then numerically by YYY.
        logre = re.compile(r'(.*)-(\d+)\.log', re.IGNORECASE)
        ma = logre.match(a)
        mb = logre.match(b)
        if ma and mb:
            if ma.group(1) > mb.group(1):
                return 1
            if ma.group(1) < mb.group(1):
                return -1
            if int(ma.group(2)) > int(mb.group(2)):
                return 1
            if int(ma.group(2)) < int(mb.group(2)):
                return -1
            return 0
        else:
            # Put first the files and later the directories
            if a.endswith('.log') and not b.endswith('.log'):
                return -1
            if b.endswith('.log') and not a.endswith('.log'):
                return 1

            if a > b:
                return 1
            if a < b:
                return -1
            return 0

    def _configure_watcher(self):
        for p in self.paths:
            for q in os.listdir(p):
                r = os.path.join(p, q)
                if os.path.isdir(r):
                    self._create_gio_monitor(r)
            self._create_gio_monitor(p)

        for f in self.extra_files:
            self._create_gio_monitor(f)

    def _create_gio_monitor(self, direc_path):
        monitor = Gio.File.new_for_path(direc_path)\
            .monitor_directory(Gio.FileMonitorFlags.NONE, None)
        monitor.connect('changed', self._log_file_changed_cb)
        self._gio_monitors.append(monitor)

    def _log_file_changed_cb(self, monitor, log_file, other_file, event):
        filepath = log_file.get_path()
        logfile = None
        for p in self.paths:
            if filepath.startswith(p):
                logfile = os.path.relpath(filepath, p)
                break

        if event == Gio.FileMonitorEvent.CHANGED:
            if logfile in self.logs:
                self.logs[logfile].update()
        elif event == Gio.FileMonitorEvent.DELETED:
            if logfile in self.logs:
                self._remove_log_file(logfile)
        elif event == Gio.FileMonitorEvent.CREATED:
            self._add_log_file(log_file.get_path())

    def _cursor_changed_cb(self, treeview):
        selection = self._treeview.get_selection()
        if selection is not None:
            treestore, text_iter = selection.get_selected()
            if text_iter is not None:
                self._show_log(treestore.get_value(text_iter, 1))
                if treestore.iter_has_child(text_iter):
                    path = treestore.get_path(text_iter)
                    if treeview.row_expanded(path):
                        treeview.collapse_row(path)
                    else:
                        treeview.expand_row(path, False)

    def _show_log(self, logfile):
        if logfile in self.logs:
            if self.active_log is None:
                try:
                    direc, filename = os.path.split(logfile)
                    self.first_file_open = \
                        time.ctime(float(direc)) + '|' + filename
                except ValueError:
                    self.first_file_open = \
                        env.get_profile_path('logs') + '|' + logfile
            log = self.logs[logfile]
            self._textview.set_buffer(log)
            self._textview.scroll_to_mark(
                log.get_insert(), 0, use_align=False, xalign=0.5, yalign=0.5)
            self.active_log = log

    def _find_logs(self):
        for path in self.paths:
            try:
                files = os.listdir(path)
            except BaseException:
                logging.debug(
                    _("ERROR: Failed to look for files in '%(path)s'.") %
                    {'path': path})
            else:
                for logfile in files:
                    self._add_log_file(os.path.join(path, logfile))

        for logfile in self.extra_files:
            self._add_log_file(logfile)

        self._treeview.expand_all()

    def _add_log_file(self, path, parent=None, _dir=None):
        if os.path.isdir(path):
            pdir, _dir = os.path.split(path)
            if pdir == self.paths[0]:
                self._add_old_logs_dir(pdir, _dir)

            return False

        if not os.path.exists(path):
            logging.debug(_("ERROR: File '%(file)s' does not exist.") %
                          {'file': path})
            return False

        if not os.access(path, os.R_OK):
            logging.debug(_("ERROR: Unable to read file '%(file)s'.") %
                          {'file': path})
            return False

        directory, logfile = os.path.split(path)
        name = logfile

        if _dir:
            logfile = '%s/%s' % (_dir, logfile)

        if logfile not in self.logs or _dir:
            if not parent:
                parent = self.extra_iter
                if directory in self.path_iter:
                    parent = self.path_iter[directory]
            tree_iter = self._treemodel.append(parent, [name, logfile])

            model = LogBuffer(path, tree_iter)

            self.logs[logfile] = model

        log = self.logs[logfile]
        log.update()
        written = log._written

        if self.active_log is None:
            self._show_log(logfile)
            self.active_log = log
            success, log_iter = \
                self._treeview.get_model().convert_child_iter_to_iter(log.iter)
            self._treeview.get_selection().select_iter(log_iter)

        if written > 0 and self.active_log == log:
            self._textview.scroll_to_mark(
                log.get_insert(), 0, use_align=False, xalign=0.5, yalign=0.5)

    def _add_old_logs_dir(self, path, _dir):
        # Add a directory with their respective logs
        complete = os.path.join(path, _dir)
        name = time.ctime(float(_dir))
        parent = self._treemodel.append(self.path_iter[path], [name, ''])
        for p in os.listdir(complete):
            self._add_log_file(os.path.join(complete, p), parent, _dir)

        return parent

    def _remove_log_file(self, logfile):
        log = self.logs[logfile]
        self._treemodel.remove(log.iter)
        if self.active_log == log:
            self.active_log = None
        del self.logs[logfile]

    def set_search_text(self, text):
        self.search_text = text

        _buffer = self._textview.get_buffer()

        start, end = _buffer.get_bounds()
        _buffer.remove_tag_by_name('search-hilite', start, end)
        _buffer.remove_tag_by_name('search-select', start, end)

        text_iter = _buffer.get_start_iter()

        while True:
            next_found = text_iter.forward_search(text, 0, None)
            if next_found is None:
                break
            start, end = next_found
            _buffer.apply_tag_by_name('search-hilite', start, end)
            text_iter = end

        if self.get_next_result('current'):
            self.search_next('current')
        elif self.get_next_result('backward'):
            self.search_next('backward')

    def get_next_result(self, direction):
        _buffer = self._textview.get_buffer()

        if direction == 'forward':
            text_iter = _buffer.get_iter_at_mark(_buffer.get_insert())
            text_iter.forward_char()
        else:
            text_iter = _buffer.get_iter_at_mark(_buffer.get_insert())

        if direction == 'backward':
            return text_iter.backward_search(self.search_text, 0, None)
        else:
            return text_iter.forward_search(self.search_text, 0, None)

    def search_next(self, direction):
        next_found = self.get_next_result(direction)
        if next_found:
            _buffer = self._textview.get_buffer()

            start, end = _buffer.get_bounds()
            _buffer.remove_tag_by_name('search-select', start, end)

            start, end = next_found
            _buffer.apply_tag_by_name('search-select', start, end)

            _buffer.place_cursor(start)

            self._textview.scroll_to_iter(start, 0.1, use_align=False,
                                          xalign=0.5, yalign=0.5)
            self._textview.scroll_to_iter(end, 0.1, use_align=False,
                                          xalign=0.5, yalign=0.5)


class LogBuffer(Gtk.TextBuffer):

    def __init__(self, logfile, iterator):
        GObject.GObject.__init__(self)

        _tagtable = self.get_tag_table()
        hilite_tag = Gtk.TextTag.new('search-hilite')
        hilite_tag.props.background = '#FFFFB0'
        _tagtable.add(hilite_tag)
        select_tag = Gtk.TextTag.new('search-select')
        select_tag.props.background = '#B0B0FF'
        _tagtable.add(select_tag)

        self.logfile = logfile
        self._pos = 0
        self.iter = iterator
        self.update()

    def append_formatted_text(self, text):
        # Remove ANSI escape codes.
        # todo- Handle a subset of them.
        strip_ansi = re.compile(r'\033\[[\d;]*m')
        text = strip_ansi.sub('', text)
        self.insert(self.get_end_iter(), text)

    def update(self):
        try:
            f = open(self.logfile, 'r')
            init_pos = self._pos

            f.seek(self._pos)
            self.append_formatted_text(f.read())
            self._pos = f.tell()
            f.close()

            self._written = (self._pos - init_pos)
        except BaseException:
            self.insert(self.get_end_iter(),
                        _("Error: Can't open file '%s'\n") % self.logfile)
            self._written = 0


class LogActivity(activity.Activity):
    def __init__(self, handle):
        activity.Activity.__init__(self, handle)

        self._autosearch_timer = None

        # Paths to watch: ~/.sugar/someuser/logs, /var/log
        paths = []
        paths.append(env.get_profile_path('logs'))
        paths.append('/var/log')

        # Additional misc files.
        ext_files = []
        ext_files.append(os.path.expanduser('~/.bash_history'))

        self.viewer = MultiLogView(paths, ext_files)
        self.set_canvas(self.viewer)
        self.viewer.grab_focus()

        self._build_toolbox()

        # Get Sugar's clipboard
        self.clipboard = Gdk.Display.get_default().get_clipboard()

        self._configure_cb(None)

        self.connect('notify::default-width', self._configure_cb)

    def _build_toolbox(self):
        toolbar_box = ToolbarBox()

        self.max_participants = 1

        activity_button = ActivityToolbarButton(self)
        activity_toolbar = activity_button.page

        self._toolbar = toolbar_box.toolbar
        self._toolbar.append(activity_button)

        self._secondary_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._secondary_toolbar_button = ToolbarButton(
            page=self._secondary_toolbar,
            icon_name='system-search')
        self._secondary_toolbar.show()
        self._toolbar.append(self._secondary_toolbar_button)
        self._secondary_toolbar_button.hide()

        show_list = ToggleToolButton('view-list')
        show_list.set_active(True)
        show_list.set_tooltip(_('Show list of files'))
        show_list.connect('toggled', self._list_toggled_cb)
        self._toolbar.append(show_list)
        show_list.show()

        copy = CopyButton()
        copy.connect('clicked', self.__copy_clicked_cb)
        self._toolbar.append(copy)

        wrap_btn = ToggleToolButton("format-wrap")
        wrap_btn.set_tooltip(_('Word Wrap'))
        wrap_btn.connect('clicked', self._wrap_cb)
        self._toolbar.append(wrap_btn)

        self.search_entry = iconentry.IconEntry()
        self.search_entry.set_size_request(300, -1)
        self.search_entry.set_icon_from_name(
            iconentry.ICON_ENTRY_PRIMARY, 'entry-search')
        self.search_entry.add_clear_button()
        self.search_entry.connect('activate', self._search_entry_activate_cb)
        self.search_entry.connect('changed', self._search_entry_changed_cb)
        self._search_item = Gtk.Box()
        self._search_item.append(self.search_entry)
        self._toolbar.append(self._search_item)

        self._search_prev = ToolButton('go-previous-paired')
        self._search_prev.set_tooltip(_('Previous'))
        self._search_prev.connect('clicked', self._search_prev_cb)
        self._toolbar.append(self._search_prev)

        self._search_next = ToolButton('go-next-paired')
        self._search_next.set_tooltip(_('Next'))
        self._search_next.connect('clicked', self._search_next_cb)
        self._toolbar.append(self._search_next)

        self._update_search_buttons()

        self.collector_palette = CollectorPalette(self)
        collector_btn = ToolButton('log-export')
        collector_btn.set_palette(self.collector_palette)
        collector_btn.connect('clicked', self._logviewer_cb)
        collector_btn.show()
        activity_toolbar.append(collector_btn)

        self._delete_btn = ToolButton('list-remove', accelerator='<ctrl>d')
        self._delete_btn.set_tooltip(_('Delete Log File'))
        self._delete_btn.connect('clicked', self._delete_log_cb)
        self._toolbar.append(self._delete_btn)

        self._separator = Gtk.Separator()
        self._separator.set_hexpand(True)
        self._toolbar.append(self._separator)

        self._stop_btn = StopButton(self)
        self._toolbar.append(self._stop_btn)

        self.set_toolbar_box(toolbar_box)

    def _configure_cb(self, event=None):
        for control in [self._stop_btn, self._separator, self._delete_btn]:
            if control.get_parent() == self._toolbar:
                self._toolbar.remove(control)

        if self.get_width() < self.get_height():
            self._secondary_toolbar_button.show()
            self._secondary_toolbar_button.set_expanded(True)
            self._remove_controls(self._toolbar)
            self._add_controls(self._secondary_toolbar)
        else:
            self._secondary_toolbar_button.set_expanded(False)
            self._secondary_toolbar_button.hide()
            self._remove_controls(self._secondary_toolbar)
            self._add_controls(self._toolbar)

        for control in [self._delete_btn, self._separator, self._stop_btn]:
            if control.get_parent() != self._toolbar:
                self._toolbar.append(control)

    def _remove_controls(self, toolbar):
        for control in [self._search_item, self._search_prev,
                        self._search_next]:
            if control.get_parent() == toolbar:
                toolbar.remove(control)

    def _add_controls(self, toolbar):
        for control in [self._search_item, self._search_prev,
                        self._search_next]:
            if control.get_parent() != toolbar:
                toolbar.append(control)
                control.show()

    def _list_toggled_cb(self, widget):
        if widget.get_active():
            self.viewer.list_scroll.show()
        else:
            self.viewer.list_scroll.hide()

    def __copy_clicked_cb(self, button):
        if self.viewer.active_log:
            self.viewer.active_log.copy_clipboard(self.clipboard)

    def _wrap_cb(self, button):
        if button.get_active():
            self.viewer._textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        else:
            self.viewer._textview.set_wrap_mode(Gtk.WrapMode.NONE)

    def _search_entry_activate_cb(self, entry):
        if self._autosearch_timer:
            GLib.source_remove(self._autosearch_timer)
            self._autosearch_timer = None
        self.viewer.set_search_text(entry.props.text)
        self._update_search_buttons()

    def _search_entry_changed_cb(self, entry):
        if self._autosearch_timer:
            GLib.source_remove(self._autosearch_timer)
        self._autosearch_timer = GLib.timeout_add(_AUTOSEARCH_TIMEOUT,
                                                  self.__autosearch_cb)

    def __autosearch_cb(self):
        self._autosearch_timer = None
        self.search_entry.activate()
        return False

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
            prev_result = self.viewer.get_next_result('backward')
            next_result = self.viewer.get_next_result('forward')
            self._search_prev.props.sensitive = prev_result is not None
            self._search_next.props.sensitive = next_result is not None

    def _delete_log_cb(self, widget):
        if self.viewer.active_log:
            logfile = self.viewer.active_log.logfile
            try:
                os.remove(logfile)
            except OSError as err:
                notify = NotifyAlert()
                notify.props.title = _('Error')
                notify.props.msg = _('%(error)s when deleting %(file)s') % \
                    {'error': err.strerror, 'file': logfile}
                notify.connect('response', _notify_response_cb, self)
                self.add_alert(notify)

    def _logviewer_cb(self, widget):
        self.collector_palette.popup(True)


class CollectorPalette(Palette):
    def __init__(self, activity):
        Palette.__init__(self, _('Log Collector: Capture information'))

        self._activity = activity

        self._collector = LogCollect()

        trans = _('This captures information about the system\n'
                  'and running processes to a journal entry.\n'
                  'Use this to improve a problem report.')
        label = Gtk.Label(label=trans)
        label.set_vexpand(True)

        send_button = Gtk.Button(label=_('Capture information'))
        send_button.connect('clicked', self._on_send_button_clicked_cb)
        send_button.set_vexpand(True)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        vbox.append(label)
        vbox.append(send_button)

        self.set_content(vbox)

    def _on_send_button_clicked_cb(self, button):
        window = self._activity.get_window()
        
        wait_cursor = Gdk.Cursor.new_from_name("wait", None)
        self._activity.set_cursor(wait_cursor)

        identifier = str(int(time.time()))
        filename = '%s.zip' % identifier
        filepath = os.path.join(activity.get_activity_root(), filename)
        success = True
        # FIXME: subprocess or thread
        try:
            self._collector.write_logs(archive=filepath, logbytes=0)
        except BaseException:
            success = False

        self.popdown(True)

        if not success:
            title = _('Logs not captured')
            msg = _('The logs could not be captured.')

            notify = NotifyAlert()
            notify.props.title = title
            notify.props.msg = msg
            notify.connect('response', _notify_response_cb, self._activity)
            self._activity.add_alert(notify)

        jobject = datastore.create()
        metadata = {
            'title': _('log-%s') % filename,
            'title_set_by_user': '0',
            'suggested_filename': filename,
            'mime_type': 'application/zip',
        }
        for k, v in list(metadata.items()):
            jobject.metadata[k] = v
        jobject.file_path = filepath
        datastore.write(jobject)
        self._last_log = jobject.object_id
        jobject.destroy()
        activity.show_object_in_journal(self._last_log)
        os.remove(filepath)

        self._activity.set_cursor(None)
