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
from urllib.parse import urlparse
import aptkit.simpleclient
import aptkit.enums

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


# Used for control flow/showing UIHelper.show_critical()
class AppError(Exception):
    def __init__(self, title, msg):
        super().__init__()
        self.title = title
        self.msg = msg


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

    def show_critical(self, title, msg, exit=True):
        dialog = Gtk.MessageDialog(parent=self.window,
                                    modal=True,
                                    message_type=Gtk.MessageType.ERROR,
                                    buttons=Gtk.ButtonsType.CLOSE)
        dialog.set_markup("<big><b>%s</b></big>\n\n%s" % (title, msg))
        dialog.run()
        dialog.destroy()
        if exit:
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
        self.added_list = Gtk.ListStore(GObject.TYPE_STRING)
        column = Gtk.TreeViewColumn("")
        render = Gtk.CellRendererText()
        column.pack_start(render, True)
        column.add_attribute(render, "markup", 0)
        self.ui_treeview_added.append_column(column)
        self.ui_treeview_added.set_model(self.added_list)

        self.removed_list = Gtk.ListStore(GObject.TYPE_STRING)
        column = Gtk.TreeViewColumn("")
        render = Gtk.CellRendererText()
        column.pack_start(render, True)
        column.add_attribute(render, "markup", 0)
        self.ui_treeview_removed.append_column(column)
        self.ui_treeview_removed.set_model(self.removed_list)

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
            # Initiate control values to empty strings
            # some .deb don't contain this information
            self.version = ""
            self.maintainer = ""
            self.description = ""
            self.installed_size = ""
            if "Version" in self.deb:
                self.version = self.deb["Version"]
            if "Maintainer" in self.deb:
                self.maintainer = self.deb["Maintainer"]
            if "Description" in self.deb:
                self.description = self.deb["Description"]
            if "Installed-Size" in self.deb:
                self.installed_size = self.round_size(self.deb["Installed-Size"])
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
        self.ui_headerbar.set_subtitle(self.version)
        self.ui_maintainer_label.set_text(self.maintainer)
        self.ui_size_label.set_text(self.installed_size)
        set_description(self.ui_textview_description, None, self.description)

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
        num_changes = len(self.remove) + len(self.install)
        if num_changes > 0:
            all_ok = False
            changes = gettext.ngettext('Requires changes in %d other package', 'Requires changes in %d other packages', num_changes) % num_changes
            self.set_package_status(Gtk.MessageType.WARNING, changes, _("_Install Package"))

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
            all_ok = False
            pkg = self.cache[self.deb.pkgname]
            res = self.deb.compare_to_version_in_cache(use_installed=True)
            if res == apt.debfile.DebPackage.VERSION_SAME and pkg.installed:
                self.set_package_status(Gtk.MessageType.INFO, _("The same version is already installed"), _("_Reinstall Package"))
            elif pkg.candidate and pkg.candidate.downloadable:
                msg = _("It is safer and recommended to install from the repositories instead.")
                self.uih.show_dialog(Gtk.MessageType.INFO, _("This package is available in the repositories"), msg)
        return all_ok


    def dpkg_action(self, widget, install):
        self.ui_window.set_sensitive(False)
        apt = aptkit.simpleclient.SimpleAPTClient(self.ui_window)
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
                if str(pkg.installed.version) == str(self.version):
                    self.ui_main_stack.set_visible_child_name("page_success")
                    pkg_str = "%s %s" % (self.deb.pkgname, self.version)
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
        self.added_list.clear()
        self.removed_list.clear()
        for inst in self.install:
            self.added_list.append([inst])
        for rm in self.remove:
            self.removed_list.append([rm])
        if len(self.install) <= 0:
            self.ui_added_label.hide()
            self.ui_added_scrolledwindow.hide()
        if len(self.remove) <= 0:
            self.ui_removed_label.hide()
            self.ui_removed_scrolledwindow.hide()
        self.ui_dialog_details.set_transient_for(self.ui_window)
        self.ui_dialog_details.run()
        self.ui_dialog_details.hide()

    def on_install_button_clicked(self, widget):
        self.dpkg_action(widget, True)


def set_description(textview, summary, description):
    buffer = textview.get_buffer()
    try:
        description = description.split("\n")
        if summary is None:
            summary = description[0]
            description[0] = ""
        else:
            summary = summary + "\n"

        text = "%s\n" % summary.title()

        # Parse new lines (this is necessary because APT introduces random new lines, multiple space
        # chars and . lines)
        for line in description:
            line = line.strip()
            if line == ".":
                text += "\n"
            else:
                text += line + "\n"
        text = re.compile(r'^(\s|\t)*(\*|0|-)',re.MULTILINE).sub('\n*', text)
        text = re.compile(r'\n', re.MULTILINE).sub(" ", text)
        text = re.compile(r'\s\s+', re.MULTILINE).sub("\n", text)

        buffer.set_text(text)

        # Make the summary bold
        tag = buffer.create_tag(None, weight=Pango.Weight.BOLD)
        iter = buffer.get_iter_at_offset(0)
        (start, end) = iter.forward_search("\n", Gtk.TextSearchFlags.TEXT_ONLY, None)
        buffer.apply_tag(tag , iter, end)
    except Exception as e:
        print(e)
        buffer.set_text("")

class URLApp():

    def __init__(self, url):
        self.uih = UIHelper(None)

        # parse URL
        parsed_url = urlparse(url)
        pkgs = parsed_url.path
        if not pkgs:
            pkgs = parsed_url.netloc
        self.pkgs_to_install = pkgs.split(",")

        # update the cache
        apt = aptkit.simpleclient.SimpleAPTClient(None)
        apt.set_finished_callback(self.on_update_finished)
        apt.update_cache()

    @_idle
    def on_update_finished(self, transaction=None, exit_state=None):
        if exit_state != aptkit.enums.EXIT_SUCCESS:
            Gtk.main_quit()

        # check the cache
        self.cache = apt.Cache()
        if self.cache._depcache.broken_count > 0:
            self.uih.show_critical(_("Broken dependencies"), _("To fix this run 'sudo apt-get install -f' in a terminal window."))

        # show the confirmation dialog
        self.settings = Gio.Settings(schema="org.x.captain")

        # Start installing the packages
        self.do_install()


    def do_install(self):
        """Runs the install process for the first package in self.pkgs_to_install.
        Function is recursively called by signal call-back functions."""

        # If there are no packages to install, exit the Gtk.main thread
        if not self.pkgs_to_install:
            Gtk.main_quit()
            return

        # Get the current package we will try to install
        self.pkgname = self.pkgs_to_install.pop(0)

        try:
            # check that package is in the repos, that it's installable and not already installed
            if self.pkgname not in self.cache:
                raise AppError(_("The package '%s' could not be installed") % self.pkgname, _("It was not found in the repositories."))
            self.pkg = self.cache[self.pkgname]
            if self.pkg.is_installed:
                raise AppError(_("The package '%s' could not be installed") % self.pkgname, _("It is already installed."))
            if not (self.pkg.candidate and self.pkg.candidate.downloadable):
                raise AppError(_("The package '%s' could not be installed") % self.pkgname, _("It is not installable."))
        except AppError as e:
            # An error occured from an above source, display the critial dialog
            self.uih.show_critical(e.title, e.msg, exit=False)
            # We're finished installing this package
            self.on_install_finished()
            return

        # Initialize the dialog
        self.builder = Gtk.Builder()
        self.builder.set_translation_domain(APP)
        self.builder.add_from_file("/usr/share/captain/apt_url.ui")
        self.builder.connect_signals(self)
        for widget in self.builder.get_objects():
            if issubclass(type(widget), Gtk.Buildable):
                name = "ui_%s" % Gtk.Buildable.get_name(widget)
                setattr(self, name, widget)

        # Update dialog messages
        self.ui_body_label.set_text(_("Do you want to install '%s'?") % self.pkgname)
        set_description(self.ui_textview, self.pkg.candidate.summary, self.pkg.candidate.description)

        # Show the dialog
        self.ui_window.show()

    @_idle
    def on_install_finished(self, transaction=None, exit_state=None):
        # We're done with this UI instance, destroy it
        # use getattr() to reference it since it may not even exist
        window = getattr(self, "ui_window", None)
        if window:
            window.destroy()
        # Install the next package
        self.do_install()

    #########################################
    # Callback functions used by the .ui file
    #########################################

    def on_window_deleted(self, widget, event):
        # We're "finished" installing this package, move onto the next one
        self.on_install_finished()

    def on_cancel_button_clicked(self, widget):
        self.ui_window.hide()

        # We're "finished" installing this package, move onto the next one
        self.on_install_finished()

    def on_install_button_clicked(self, widget):
        self.ui_window.hide()

        # install the package
        try:
            apt = aptkit.simpleclient.SimpleAPTClient(None)
            apt.set_finished_callback(self.on_install_finished)
            apt.install_packages([self.pkgname])
        except Exception as e:
            self.uih.show_critical(_("The package '%s' could not be installed") % self.pkgname, str(e))

if len(sys.argv) != 2:
    print("Usage: captain filename.deb")
    sys.exit(1)

argument = os.path.expanduser(sys.argv[1])

if argument.lower().startswith("apt:"):
    app = URLApp(argument)
else:
    if not os.path.exists(argument):
        print(f"File not found: {argument}")
        sys.exit(1)
    app = App(argument)

Gtk.main()
