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
gi.require_version('Gdk', '4.0')
gi.require_version('Gtk', '4.0')
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

class LogTreeNode(GObject.Object):

    def __init__(self, node_id, display_name, logfile=None):
        super().__init__()
        self.node_id = node_id
        self.display_name = display_name
        self.logfile = logfile
        self.children = Gio.ListStore.new(LogTreeNode)

class MultiLogView(Gtk.Paned):

    def __init__(self, paths, extra_files):
        super().__init__()
        # REPLACED: Gtk.HPaned() / Gtk.VPaned() → single Gtk.Paned with
        # explicit orientation (Gtk.Paned now handles both orientations).
        self.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.set_position(320)
        # ADDED: set_resize_* / set_shrink_* are new GTK4 Paned properties
        # that replace the old pack_start(resize=…, shrink=…) packing calls.
        self.set_resize_start_child(False)
        self.set_resize_end_child(True)
        self.set_shrink_start_child(False)
        self.set_shrink_end_child(True)

        self.paths = paths
        self.extra_files = extra_files
        # Hold a reference to the monitors so they don't get disposed
        self._gio_monitors = []
        self.first_file_open = '|'

        self.active_log = None
        self.logs = {}
        self._log_index = {}

        self.search_text = ''

        self._build_treeview()
        self._build_textview()

        self.show()

        self._configure_watcher()
        self._find_logs()

    def _build_treeview(self):
        # ADDED: Gio.ListStore is the GTK4 replacement for Gtk.TreeStore.
        # Each LogTreeNode carries its own children ListStore for nesting.
        self._root_store = Gio.ListStore.new(LogTreeNode)

        # ADDED: Gtk.TreeListModel wraps the nested stores and exposes a flat
        # list with depth/expanded info — there is no equivalent in GTK3.
        self._tree_model = Gtk.TreeListModel.new(
            self._root_store, False, False, self._create_child_model, None)

        # REPLACED: Gtk.TreeViewColumn with cell renderers → GTK4
        # Gtk.SignalListItemFactory with setup/bind signal callbacks.
        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', self._setup_tree_row_cb)
        factory.connect('bind', self._bind_tree_row_cb)

        # REPLACED: Gtk.TreeView's built-in selection → Gtk.SingleSelection,
        # which is a separate selection model composed onto the list.
        self._selection = Gtk.SingleSelection.new(self._tree_model)
        self._selection.connect('selection-changed',
                                self._selection_changed_cb)

        # REPLACED: Gtk.TreeView → Gtk.ListView (GTK4 list widget).
        self._listview = Gtk.ListView(model=self._selection, factory=factory)
        self._listview.set_vexpand(True)

        self.list_scroll = Gtk.ScrolledWindow()
        self.list_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                                    Gtk.PolicyType.AUTOMATIC)
        # REPLACED: add() / add_with_viewport() → set_child() in GTK4.
        self.list_scroll.set_child(self._listview)
        self.list_scroll.set_size_request(320, -1)
        self.list_scroll.set_hexpand(False)
        self.list_scroll.set_vexpand(True)

        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        # ADDED: header label above the file list — not present in the GTK3
        # version; improves usability when the panel is narrow.
        header = Gtk.Label(label=_('Log Files'), xalign=0)
        header.add_css_class('log-list-header')
        # REPLACED: pack_start() / pack_end() → append() in GTK4 boxes.
        list_box.append(header)
        list_box.append(self.list_scroll)
        self._list_panel = list_box

        # REPLACED: add() / pack_start() on Gtk.HPaned → set_start_child().
        self.set_start_child(list_box)

    def _build_textview(self):
        self._textview = Gtk.TextView()
        self._textview.set_wrap_mode(Gtk.WrapMode.NONE)
        # ADDED: set_monospace() is a GTK4 convenience setter; in GTK3 this
        # required a Pango.FontDescription passed to modify_font().
        self._textview.set_monospace(True)
        # ADDED: CSS class used by the CssProvider below; GTK3 used
        # modify_font() / modify_base() / Gdk.color_parse() for inline styling.
        self._textview.add_css_class('log-textview')
        # REMOVED during GTK4 port:
        #   - modify_font()   (deprecated in GTK3, gone in GTK4)
        #   - modify_base()   (deprecated in GTK3, gone in GTK4)
        #   - Gdk.color_parse()  (removed in GTK4)
        #   - Gtk.StateType      (removed in GTK4)
        # REPLACED by a Gtk.CssProvider loaded below.

        self._textview.set_editable(False)

        # ADDED: CssProvider replaces all the old per-widget inline style calls
        # (modify_font, modify_base, etc.) that were removed in GTK4.
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b'.log-textview { font-family: Monospace; font-size: 12pt; }\n'
            b'.log-list-label { font-size: 12pt; }\n'
            b'.log-list-header { font-size: 14pt; font-weight: 600; }')
        # REPLACED: Gtk.StyleContext.add_provider() (per-widget) →
        # add_provider_for_display() (global, GTK4 only).
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self._text_scroll = Gtk.ScrolledWindow()
        self._text_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                         Gtk.PolicyType.AUTOMATIC)
        self._text_scroll.set_child(self._textview)
        self._text_scroll.set_hexpand(True)
        self._text_scroll.set_vexpand(True)

        # REPLACED: add() / pack2() on Gtk.HPaned → set_end_child().
        self.set_end_child(self._text_scroll)

    def _sort_log_names(self, a, b):
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

    def _sort_node_list(self, nodes):
        nodes.sort(key=cmp_to_key(
            lambda left, right: self._sort_log_names(
                left.display_name, right.display_name)))

    def _create_child_model(self, item, _user_data=None):
        if item.children.get_n_items() == 0:
            return None
        return item.children

    # ADDED: _setup_tree_row_cb replaces the old _format_col() which built
    # a Gtk.TreeViewColumn with a CellRendererText.  In GTK4 the factory's
    # 'setup' signal creates the widget tree for each row once.
    def _setup_tree_row_cb(self, _factory, list_item):
        # ADDED: Gtk.TreeExpander is a new GTK4 widget that draws the
        # expand/collapse arrow and indentation; GTK3 TreeView did this
        # automatically.
        expander = Gtk.TreeExpander()
        label = Gtk.Label(xalign=0)
        label.add_css_class('log-list-label')
        label.set_ellipsize(Pango.EllipsizeMode.END)
        expander.set_child(label)
        list_item.set_child(expander)

    # ADDED: _bind_tree_row_cb replaces the GTK3 data function callback
    # (set_cell_data_func).  The 'bind' signal fires whenever a row must
    # show a different item (e.g. on scroll or model change).
    def _bind_tree_row_cb(self, _factory, list_item):
        tree_row = list_item.get_item()   # Gtk.TreeListRow (GTK4)
        node = tree_row.get_item()         # our LogTreeNode
        expander = list_item.get_child()
        label = expander.get_child()
        # ADDED: must manually wire the expander to the tree row so it knows
        # its depth and expanded state — TreeView did this implicitly.
        expander.set_list_row(tree_row)
        label.set_text(node.display_name)

    # REPLACED: _cursor_changed_cb (Gtk.TreeView 'cursor-changed' signal) →
    # _selection_changed_cb (Gtk.SingleSelection 'selection-changed' signal).
    def _selection_changed_cb(self, selection, _position, _n_items):
        tree_row = selection.get_selected_item()
        if tree_row is None:
            return

        node = tree_row.get_item()
        if node.logfile:
            self._show_log(node.logfile)

    def _configure_watcher(self):
        # REPLACED: the old Sugar/gnomevfs file-monitor helpers (e.g.
        # sugar.activity.namingalert or internal inotify wrappers) have been
        # replaced entirely by Gio.FileMonitor (see _create_gio_monitor).
        for p in self.paths:
            if not os.path.isdir(p):
                continue

            try:
                entries = os.listdir(p)
            except OSError as err:
                logging.debug("Skipping monitor scan for %s: %s", p, err)
                entries = []

            for q in entries:
                r = os.path.join(p, q)
                if os.path.isdir(r):
                    self._create_gio_monitor(r)
            self._create_gio_monitor(p)

        for f in self.extra_files:
            if os.path.exists(f):
                self._create_gio_monitor(f)

    # ADDED: _create_gio_monitor centralises all Gio.FileMonitor creation.
    # In GTK3/Sugar3 the activity used a custom watcher; here we use the
    # standard GIO API available in both Sugar and standalone desktops.
    # Monitors are kept in self._gio_monitors to prevent garbage-collection.
    def _create_gio_monitor(self, direc_path):
        try:
            gio_file = Gio.File.new_for_path(direc_path)
            if os.path.isdir(direc_path):
                monitor = gio_file.monitor_directory(
                    Gio.FileMonitorFlags.NONE, None)
            else:
                monitor = gio_file.monitor_file(
                    Gio.FileMonitorFlags.NONE, None)
            monitor.connect('changed', self._log_file_changed_cb)
            # ADDED: appending to list keeps a strong reference; without this
            # Python's GC would destroy the monitor immediately.
            self._gio_monitors.append(monitor)
        except (OSError, GLib.Error) as err:
            logging.debug("Skipping monitor for %s: %s", direc_path, err)

    def _log_file_changed_cb(self, monitor, log_file, other_file, event):
        filepath = log_file.get_path()
        logfile = None
        for p in self.paths:
            if filepath.startswith(p):
                logfile = os.path.relpath(filepath, p)
                break
        if logfile is None and filepath in self.extra_files:
            logfile = os.path.basename(filepath)

        if event == Gio.FileMonitorEvent.CHANGED:
            if logfile in self.logs:
                self.logs[logfile].update()
        elif event == Gio.FileMonitorEvent.DELETED:
            if logfile in self.logs:
                self._remove_log_file(logfile)
        elif event == Gio.FileMonitorEvent.CREATED:
            self._add_log_file(log_file.get_path())

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
                    self._add_log_file(os.path.join(path, logfile),
                                       rebuild=False)

        for logfile in self.extra_files:
            self._add_log_file(logfile, rebuild=False)

        self._rebuild_tree_model()

    def _clear_store(self, store):
        while store.get_n_items() > 0:
            store.remove(store.get_n_items() - 1)

    def _format_group_name(self, group_name):
        base_name = os.path.basename(group_name)
        try:
            return time.ctime(float(base_name))
        except ValueError:
            return group_name

    def _classify_log_path(self, path):
        for root_path in self.paths:
            if path == root_path or path.startswith(root_path + os.sep):
                relative_path = os.path.relpath(path, root_path)
                parent_path = os.path.dirname(relative_path)
                group_name = None if parent_path == '.' else parent_path
                return root_path, group_name

        return _('Other'), None

    def _sort_store_nodes(self, store):
        nodes = [store.get_item(index) for index in range(store.get_n_items())]
        self._sort_node_list(nodes)
        self._clear_store(store)
        for node in nodes:
            self._sort_store_nodes(node.children)
            store.append(node)

    # ADDED: _expand_root_rows() explicitly expands top-level rows after a
    # rebuild.  In GTK3 Gtk.TreeView had an expand_all() / auto-expand mode;
    # with Gtk.TreeListModel rows start collapsed and must be expanded in code.
    def _expand_root_rows(self):
        for index in range(self._tree_model.get_n_items()):
            tree_row = self._tree_model.get_item(index)
            if tree_row.get_depth() == 0 and tree_row.is_expandable():
                tree_row.set_expanded(True)

    # ADDED: _select_logfile() restores the selection after a rebuild by
    # scanning the flat model for the matching node.  GTK3 TreeView kept
    # selection state automatically across TreeStore mutations.
    def _select_logfile(self, logfile):
        for index in range(self._tree_model.get_n_items()):
            tree_row = self._tree_model.get_item(index)
            node = tree_row.get_item()
            if node.logfile == logfile:
                # REPLACED: Gtk.TreeView.set_cursor() → SingleSelection.set_selected()
                self._selection.set_selected(index)
                break

    # ADDED: _rebuild_tree_model() has no GTK3 equivalent — the old code
    # mutated a Gtk.TreeStore in-place.  Because Gtk.TreeListModel is
    # read-only once created, we re-populate self._root_store from scratch
    # on every structural change and then restore the previous selection.
    def _rebuild_tree_model(self):
        selected_logfile = None
        if self.active_log is not None:
            selected_logfile = self.active_log.logfile

        self._clear_store(self._root_store)

        top_nodes = {}
        group_nodes = {}
        root_nodes = []

        for logfile in self.logs:
            info = self._log_index.get(logfile)
            if info is None:
                continue

            top_key = info['top_key']
            top_node = top_nodes.get(top_key)
            if top_node is None:
                top_node = LogTreeNode('top:' + top_key, info['top_label'])
                top_nodes[top_key] = top_node
                root_nodes.append(top_node)

            parent_node = top_node
            group_name = info['group_name']
            if group_name:
                group_key = (top_key, group_name)
                group_node = group_nodes.get(group_key)
                if group_node is None:
                    group_node = LogTreeNode(
                        'group:%s:%s' % group_key,
                        self._format_group_name(group_name))
                    group_nodes[group_key] = group_node
                    top_node.children.append(group_node)
                parent_node = group_node

            parent_node.children.append(
                LogTreeNode('log:' + logfile, info['display_name'], logfile))

        self._sort_node_list(root_nodes)
        for node in root_nodes:
            self._sort_store_nodes(node.children)
            self._root_store.append(node)

        self._expand_root_rows()

        if selected_logfile is not None:
            self._select_logfile(selected_logfile)

    def _add_log_file(self, path, rebuild=True):
        if os.path.isdir(path):
            try:
                entries = os.listdir(path)
            except OSError as err:
                logging.debug("Skipping unreadable directory %s: %s", path, err)
                return False

            for entry in entries:
                self._add_log_file(os.path.join(path, entry), rebuild=False)
            if rebuild:
                self._rebuild_tree_model()
            return False

        if not os.path.exists(path):
            logging.debug(_("ERROR: File '%(file)s' does not exist.") %
                          {'file': path})
            return False

        if not os.access(path, os.R_OK):
            logging.debug(_("ERROR: Unable to read file '%(file)s'.") %
                          {'file': path})
            return False

        top_key, group_name = self._classify_log_path(path)
        _directory, logfile = os.path.split(path)
        display_name = logfile

        if group_name:
            logfile = '%s/%s' % (group_name, logfile)

        if logfile not in self.logs:
            model = LogBuffer(path, logfile)

            self.logs[logfile] = model
            self._log_index[logfile] = {
                'top_key': top_key,
                'top_label': top_key,
                'group_name': group_name,
                'display_name': display_name,
            }
            if rebuild:
                self._rebuild_tree_model()

        log = self.logs[logfile]
        log.update()
        written = log._written

        if self.active_log is None:
            self._show_log(logfile)
            self.active_log = log
            if rebuild:
                self._select_logfile(logfile)

        if written > 0 and self.active_log == log:
            self._textview.scroll_to_mark(
                log.get_insert(), 0, use_align=False, xalign=0.5, yalign=0.5)

    def _remove_log_file(self, logfile):
        log = self.logs[logfile]
        if self.active_log == log:
            self.active_log = None
        self._log_index.pop(logfile, None)
        del self.logs[logfile]
        self._rebuild_tree_model()

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

    def __init__(self, logfile, key):
        super().__init__()

        _tagtable = self.get_tag_table()
        hilite_tag = Gtk.TextTag.new('search-hilite')
        hilite_tag.props.background = '#FFFFB0'
        _tagtable.add(hilite_tag)
        select_tag = Gtk.TextTag.new('search-select')
        select_tag.props.background = '#B0B0FF'
        _tagtable.add(select_tag)

        self.logfile = logfile
        self._pos = 0
        self.iter = key
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
        profile_logs_path = env.get_profile_path('logs')
        try:
            os.makedirs(profile_logs_path, exist_ok=True)
        except OSError:
            logging.warning('Could not create profile logs path: %s',
                            profile_logs_path)
        paths.append(profile_logs_path)
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
        self.show()

    def _build_toolbox(self):
        toolbar_box = ToolbarBox()

        self.max_participants = 1

        activity_button = ActivityToolbarButton(self)
        activity_toolbar = activity_button.page

        self._toolbar = toolbar_box.toolbar
        self._toolbar.append(activity_button)

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

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_hexpand(False)
        self.search_entry.set_halign(Gtk.Align.START)
        self.search_entry.set_margin_start(8)
        self.search_entry.set_margin_end(8)
        self.search_entry.set_size_request(360, -1)
        self.search_entry.connect('activate', self._search_entry_activate_cb)
        self.search_entry.connect('changed', self._search_entry_changed_cb)
        self._toolbar.append(self.search_entry)

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

        self._delete_btn = ToolButton('list-remove')
        self._delete_btn = ToolButton('list-remove', accelerator='<ctrl>d')
        self._delete_btn.set_tooltip(_('Delete Log File'))
        self._delete_btn.connect('clicked', self._delete_log_cb)
        self._toolbar.append(self._delete_btn)

        self._separator = Gtk.SeparatorToolItem()
        self._separator.set_expand(True)
        self._separator.set_draw(False)
        self._toolbar.append(self._separator)

        self._stop_btn = StopButton(self)
        self._toolbar.append(self._stop_btn)

        toolbar_box.show()
        self.set_toolbar_box(toolbar_box)

    def _list_toggled_cb(self, widget):
        if widget.get_active():
            self.viewer._list_panel.show()
        else:
            self.viewer._list_panel.hide()

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

        send_button = Gtk.Button(_(label='Capture information'))
        send_button.connect('clicked', self._on_send_button_clicked_cb)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        vbox.append(label)
        vbox.append(send_button)
        vbox.show()

        self.set_content(vbox)

    def _on_send_button_clicked_cb(self, button):
        window = self._activity.get_window()
        old_cursor = window.get_cursor()
        window.set_cursor(Gdk.Cursor.new(Gdk.CursorType.WATCH))
        Gdk.flush()

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

        window.set_cursor(old_cursor)
        Gdk.flush()
