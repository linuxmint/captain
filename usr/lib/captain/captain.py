#!/usr/bin/python3

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gio, GLib, Gdk, GLib, Pango, GObject
import gettext
import locale
import os
import setproctitle
import sys
import threading
import apt
import apt.debfile
import re
from mimetypes import guess_type
import mintcommon.aptdaemon
import aptdaemon.enums

# i18n
APP = "captain"
LOCALE_DIR = "/usr/share/locale"
locale.bindtextdomain(APP, LOCALE_DIR)
gettext.bindtextdomain(APP, LOCALE_DIR)
gettext.textdomain(APP)
_ = gettext.gettext

setproctitle.setproctitle("captain")

# Used as a decorator to run things in the background
def _async(func):
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=func, args=args, kwargs=kwargs)
        thread.daemon = True
        thread.start()
        return thread
    return wrapper

# Used as a decorator to run things in the main loop, from another thread
def _idle(func):
    def wrapper(*args):
        GLib.idle_add(func, *args)
    return wrapper


# A helper class to show dialog windows
class UIHelper():

    def __init__(self, window):
        self.window = window

    @_idle
    def show_dialog(self, dialog_type, title, msg):
        dialog = Gtk.MessageDialog(parent=self.window,
                                    modal=True,
                                    message_type=dialog_type,
                                    buttons=Gtk.ButtonsType.CLOSE)
        dialog.set_markup("<big><b>%s</b></big>\n\n%s" % (title, msg))
        dialog.run()
        dialog.destroy()

    def show_critical(self, title, msg):
        dialog = Gtk.MessageDialog(parent=self.window,
                                    modal=True,
                                    message_type=Gtk.MessageType.ERROR,
                                    buttons=Gtk.ButtonsType.CLOSE)
        dialog.set_markup("<big><b>%s</b></big>\n\n%s" % (title, msg))
        dialog.run()
        dialog.destroy()
        Gtk.main_quit()
        sys.exit()

class App():

    def __init__(self, path):
        self.absolute_path = os.path.abspath(path)
        self.filename = os.path.basename(path)
        self.settings = Gio.Settings(schema="org.x.captain")

        self.builder = Gtk.Builder()
        self.builder.set_translation_domain(APP)
        self.builder.add_from_file("/usr/share/captain/deb_package.ui")
        self.builder.connect_signals(self)
        for widget in self.builder.get_objects():
            if issubclass(type(widget), Gtk.Buildable):
                name = "ui_%s" % Gtk.Buildable.get_name(widget)
                setattr(self, name, widget)

        # setup the details treeview
        self.details_list = Gtk.ListStore(GObject.TYPE_STRING)
        column = Gtk.TreeViewColumn("")
        render = Gtk.CellRendererText()
        column.pack_start(render, True)
        column.add_attribute(render, "markup", 0)
        self.ui_treeview_details.append_column(column)
        self.ui_treeview_details.set_model(self.details_list)

        # setup the files treeview
        column = Gtk.TreeViewColumn("")
        render = Gtk.CellRendererText()
        column.pack_start(render, True)
        column.add_attribute(render, "text", 0)
        self.ui_treeview_files.append_column(column)

        self.ui_headerbar.set_title(self.filename)
        self.ui_path_label.set_text(self.absolute_path)

        self.ui_window.show()

        self.uih = UIHelper(self.ui_window)

        self.load_deb_file()

    @_async
    def load_deb_file(self):
        self.cache = apt.Cache()
        if self.cache._depcache.broken_count > 0:
            self.uih.show_critical(_("Broken dependencies"), _("To fix this run 'sudo apt-get install -f' in a terminal window."))
        try:
            self.deb = apt.debfile.DebPackage(self.absolute_path, self.cache)
            self.on_file_loaded()
        except (IOError, SystemError, ValueError) as e:
            print("Open failed with %s" % e)
            mimetype= guess_type(self.absolute_path)
            if (mimetype[0] != None and
                mimetype[0] != "application/vnd.debian.binary-package"):
                header = _("%s is not a valid package") % self.filename
                body = _("The MIME type of this file is '%s'.") % mimetype[0]
            else:
                header = _("Could not open %s") % self.filename
                body = _("The file might be corrupted or missing permissions.")
            self.uih.show_critical(header, body)

    @_idle
    def on_file_loaded(self):
        self.ui_main_stack.set_visible_child_name("main_page")
        self.ui_window.set_title(self.deb.pkgname)
        self.ui_headerbar.set_title(self.deb.pkgname)
        self.ui_headerbar.set_subtitle(self.deb["Version"])
        self.ui_maintainer_label.set_text(self.deb["Maintainer"])
        self.ui_size_label.set_text(self.round_size(self.deb["Installed-Size"]))

        # set description
        buf = self.ui_textview_description.get_buffer()
        try:
            long_desc = ""
            raw_desc = self.deb["Description"].split("\n")
            # append a newline to the summary in the first line
            summary = raw_desc[0]
            raw_desc[0] = ""
            long_desc = "%s\n" % summary
            for line in raw_desc:
                tmp = line.strip()
                if tmp == ".":
                    long_desc += "\n"
                else:
                    long_desc += tmp + "\n"
            #print long_desc
            # do some regular expression magic on the description
            # Add a newline before each bullet
            p = re.compile(r'^(\s|\t)*(\*|0|-)',re.MULTILINE)
            long_desc = p.sub('\n*', long_desc)
            # replace all newlines by spaces
            p = re.compile(r'\n', re.MULTILINE)
            long_desc = p.sub(" ", long_desc)
            # replace all multiple spaces by
            # newlines
            p = re.compile(r'\s\s+', re.MULTILINE)
            long_desc = p.sub("\n", long_desc)
            # write the descr string to the buffer
            buf.set_text(long_desc)
            # tag the first line with a bold font
            tag = buf.create_tag(None, weight=Pango.Weight.BOLD)
            iter = buf.get_iter_at_offset(0)
            (start, end) = iter.forward_search("\n", Gtk.TextSearchFlags.TEXT_ONLY, None)
            buf.apply_tag(tag , iter, end)
        except Exception as e:
            print("No description available", e)
            buf.set_text("")

        # set content
        store = Gtk.TreeStore(str)
        try:
            header = store.append(None, [_("Control files")])
            for name in self.deb.control_filelist:
                if not name.endswith("/"):
                    store.append(header, [name])
            header = store.append(None, [_("Data files")])
            for name in self.deb.filelist:
                if not name.endswith("/"):
                    store.append(header, [name])
        except Exception as e:
            print("Exception while reading the filelist", e)
            store.clear()
            store.append(None, [_("Error while reading the content")])
        self.ui_treeview_files.set_model(store)
        self.ui_treeview_files.expand_all()

        self.ui_install_button.get_style_context().remove_class("suggested-action")

        self.run_checks()

    @_idle
    def set_package_status(self, status_type, status, button_label):
        self.ui_infobar_label.set_markup(status)
        self.ui_infobar.set_message_type(status_type)
        self.ui_infobar.show()
        self.ui_install_button.set_label(button_label)
        if status_type == Gtk.MessageType.ERROR:
            self.ui_details_button.hide()
        elif status_type == Gtk.MessageType.INFO:
            self.ui_details_button.hide()
        elif status_type == Gtk.MessageType.WARNING:
            self.ui_details_button.show()

    @_async
    def run_checks(self):
        all_ok = True

        # check the deps
        if not self.deb.check():
            self.set_package_status(Gtk.MessageType.ERROR, _("Error: %s") % self.deb._failure_string, _("_Install Package"))
            return False

        # check broken provides
        provides = self.get_broken_provides()
        if provides:
            status = _("Error: No longer provides %s") % ", ".join(provides)
            self.set_package_status(Gtk.MessageType.ERROR,  status, _("_Install Package"))
            return False

        all_ok = self.compare_deb_with_cache()

        (self.install, self.remove, self.unauthenticated) = self.deb.required_changes
        if len(self.remove) >0 or len(self.install) > 0:
            all_ok = False
            deps = ""
            if len(self.remove) > 0:
                deps += _("Requires the removal of %s packages") % len(self.remove)
                deps += "\n"
            if len(self.install) > 0:
                deps += _("Requires the installation of %s packages") % len(self.install)
            self.set_package_status(Gtk.MessageType.WARNING, deps, _("_Install Package"))

        self.set_suggested_action(all_ok)

    @_idle
    def set_suggested_action(self, all_ok):
        if all_ok:
            self.ui_install_button.get_style_context().add_class("suggested-action")
            self.ui_install_button.grab_default()
        self.ui_install_button.set_sensitive(True)

    #########################################
    # APT functions
    #########################################

    def round_size(self, size):
        size = float(size)
        for unit in [_('KB'), _('MB'), _('GB')]:
            if size < 1024.0 or unit == _('GB'):
                break
            size /= 1024.0
        return f"{size:.0f} {unit}"

    def get_broken_provides(self):
        provides = set()
        broken_provides = set()
        try:
            pkg = self.cache[self.deb.pkgname].installed
        except (KeyError, TypeError):
            pkg = None
        if pkg:
            if pkg.provides:
                for p in self.deb.provides:
                    for i in p:
                        provides.add(i[0])
            provides = set(pkg.provides).difference(provides)
            if provides:
                for package in list(self.cache.keys()):
                    if self.cache[package].installed:
                        for dep in self.cache[package].installed.dependencies:
                            for d in dep.or_dependencies:
                                if d.name in provides:
                                    broken_provides.add(d.name)
            return broken_provides

    def compare_deb_with_cache(self):
        # check if the package is available in the repository
        all_ok = True
        if self.deb.pkgname in self.cache:
            pkg = self.cache[self.deb.pkgname]
            if pkg.candidate and pkg.candidate.downloadable:
                all_ok = False
                res = self.deb.compare_to_version_in_cache(use_installed=True)
                msg = _("It is safer and recommended to install from the repositories instead.")
                if res == apt.debfile.DebPackage.VERSION_SAME:
                    if pkg.installed:
                        self.set_package_status(Gtk.MessageType.INFO, _("The same version is already installed"), _("_Reinstall Package"))
                    else:
                        self.uih.show_dialog(Gtk.MessageType.INFO, _("This package is available in the repositories"), msg)
                elif res == apt.debfile.DebPackage.VERSION_NEWER:
                    self.uih.show_dialog(Gtk.MessageType.INFO, _("An older version is available in the repositories"), msg)
                elif res == apt.debfile.DebPackage.VERSION_OUTDATED:
                    self.uih.show_dialog(Gtk.MessageType.INFO, _("A newer version is available in the repositories"), msg)
        return all_ok


    def dpkg_action(self, widget, install):
        self.ui_window.set_sensitive(False)
        apt = mintcommon.aptdaemon.APT(self.ui_window)
        apt.set_finished_callback(self.on_install_finished)
        apt.set_cancelled_callback(self.on_install_cancelled)
        apt.install_file(self.deb.filename)

    #########################################
    # UI functions
    #########################################

    def show_busy_cursor(self, show_busy_cursor):
        win = self.ui_window.get_window()
        if win:
            if show_busy_cursor:
                win.set_cursor(Gdk.Cursor.new_for_display(Gdk.Display.get_default(), Gdk.CursorType.WATCH))
                while Gtk.events_pending():
                    Gtk.main_iteration()
            else:
                win.set_cursor(None)

    @_idle
    def on_install_finished(self, transaction=None, exit_state=None):
        self.show_busy_cursor(True)
        cache = apt.Cache()
        self.show_busy_cursor(False)
        if cache._depcache.broken_count > 0:
            self.uih.show_critical(_("Failed to install all dependencies"), _("To fix this run 'sudo apt-get install -f' in a terminal window."))
        if self.deb.pkgname in cache:
            pkg = cache[self.deb.pkgname]
            if pkg.is_installed:
                if str(pkg.installed.version) == str(self.deb["Version"]):
                    self.ui_main_stack.set_visible_child_name("page_success")
                    pkg_str = "%s %s" % (self.deb.pkgname, self.deb["Version"])
                    self.ui_success_label.set_text(_("%s is now installed.") % pkg_str)
                    self.ui_window.set_sensitive(True)

    @_idle
    def on_install_cancelled(self, transaction=None, exit_state=None):
        self.ui_window.set_sensitive(True)

    #########################################
    # Callback functions used by the .ui file
    #########################################

    def on_window_deleted(self, widget, event):
        Gtk.main_quit()

    def on_content_button_toggled(self, widget):
        if widget.get_active():
            self.ui_stack.set_visible_child_name("content_page")
        else:
            self.ui_stack.set_visible_child_name("description_page")

    def on_treeview_files_cursor_changed(self, treeview):
        model = treeview.get_model()
        (path, col) = treeview.get_cursor()
        if model and path and path.get_depth() >= 2:
            name = model[path][0]
            parent_path = path.get_indices()[0] # 0 for control, 1 for data
            data = _("The content could not be extracted")
            self.show_busy_cursor(True)
            try:
                if parent_path == 0:
                    data = self.deb.control_content(name)
                elif parent_path == 1:
                    data = self.deb.data_content(name)
            except Exception as e:
                print(e)
            buf = self.ui_textview_file_content.get_buffer().set_text(data)
            self.show_busy_cursor(False)

    def on_details_button_clicked(self, widget):
        if not self.deb:
          return
        self.details_list.clear()
        for rm in self.remove:
            self.details_list.append(["<b>%s</b>" % _("To be removed: %s") % rm])
        for inst in self.install:
            self.details_list.append([_("To be installed: %s") % inst])
        self.ui_dialog_details.set_transient_for(self.ui_window)
        self.ui_dialog_details.run()
        self.ui_dialog_details.hide()

    def on_install_button_clicked(self, widget):
        self.dpkg_action(widget, True)


class URLApp():

    def __init__(self, url):
        self.uih = UIHelper(None)

        # parse URL
        url = url.replace("apt://", "")
        self.pkgname = url.split("?")[0]

        # update the cache
        apt = mintcommon.aptdaemon.APT(None)
        apt.set_finished_callback(self.on_update_finished)
        apt.update_cache()

    @_idle
    def on_update_finished(self, transaction=None, exit_state=None):
        if exit_state != aptdaemon.enums.EXIT_SUCCESS:
            Gtk.main_quit()

        # check the cache
        self.cache = apt.Cache()
        if self.cache._depcache.broken_count > 0:
            self.uih.show_critical(_("Broken dependencies"), _("To fix this run 'sudo apt-get install -f' in a terminal window."))

        # check that package is in the repos, that it's installable and not already installed
        if self.pkgname not in self.cache:
            self.uih.show_critical("packge not in cache", "...")
        self.pkg = self.cache[self.pkgname]
        if self.pkg.is_installed:
            self.uih.show_critical("packge already installed", "...")
        if not (self.pkg.candidate and self.pkg.candidate.downloadable):
            self.uih.show_critical("packge not installable", "...")

        # show the confirmation dialog
        self.settings = Gio.Settings(schema="org.x.captain")

        self.builder = Gtk.Builder()
        self.builder.set_translation_domain(APP)
        self.builder.add_from_file("/usr/share/captain/apt_url.ui")
        self.builder.connect_signals(self)
        for widget in self.builder.get_objects():
            if issubclass(type(widget), Gtk.Buildable):
                name = "ui_%s" % Gtk.Buildable.get_name(widget)
                setattr(self, name, widget)

        self.ui_window.show()

    @_idle
    def on_install_finished(self, transaction=None, exit_state=None):
        Gtk.main_quit()


    #########################################
    # Callback functions used by the .ui file
    #########################################

    def on_window_deleted(self, widget, event):
        Gtk.main_quit()

    def on_cancel_button_clicked(self, widget):
        Gtk.main_quit()

    def on_install_button_clicked(self, widget):
        # install the package
        try:
            self.pkg.mark_install()
            changes = self.cache.get_changes()
            pkgs = []
            for pkg in changes:
                if pkg.marked_install:
                    pkgs.append(pkg.name)
            apt = mintcommon.aptdaemon.APT(None)
            apt.set_finished_callback(self.on_install_finished)
            apt.install_packages(pkgs)
        except Exception as e:
            self.uih.show_critical("can't install", str(e))

if len(sys.argv) != 2:
    print("Usage: captain filename.deb")
    sys.exit(1)

argument = os.path.expanduser(sys.argv[1])

if "apt://" in argument:
    app = URLApp(argument)
else:
    if not os.path.exists(argument):
        print(f"File not found: {argument}")
        sys.exit(1)
    app = App(argument)

Gtk.main()
