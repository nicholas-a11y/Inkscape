#!/usr/bin/env python3
"""Send to LightBurn - Inkscape 1.4 extension.

Cleans up a copy of the drawing (optionally selection only, text/shapes to
paths, ungroup, snap stroke colours to LightBurn's layer palette) and then
either opens it in LightBurn or saves it as a LightBurn-friendly SVG.
The open document is never modified.
"""
import hashlib
import hmac
import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import warnings

import inkex
from inkex import Color
from inkex.command import inkscape
from lxml import etree

# LightBurn layer colours C00-C29 (from LightBurn's colour palette table)
LB_LAYERS = [
    (0, 0, 0), (0, 0, 255), (255, 0, 0), (0, 224, 0), (208, 208, 0),
    (255, 128, 0), (0, 224, 224), (255, 0, 255), (180, 180, 180), (0, 0, 160),
    (160, 0, 0), (0, 160, 0), (160, 160, 0), (192, 128, 0), (0, 160, 255),
    (160, 0, 160), (128, 128, 128), (125, 135, 185), (187, 119, 132),
    (74, 111, 227), (211, 63, 106), (140, 215, 140), (240, 185, 141),
    (246, 196, 225), (250, 158, 212), (80, 10, 120), (180, 90, 0),
    (0, 71, 84), (134, 250, 136), (255, 219, 102),
]
# Tool layers T1/T2 - left alone if already used, never snapped to
LB_TOOLS = [(243, 105, 38), (12, 150, 217)]

KEEP_TAGS = {"defs", "namedview", "metadata", "style", "script", "title", "desc"}


# ---------------------------------------------------------------- REST API
# LightBurn REST API: https://github.com/LightBurnSoftware/LightBurn-REST
# Requests go to 127.0.0.1, not "localhost": on Windows "localhost" resolves to IPv6 ::1 first,
# and the failed attempt costs ~2 s per connection. Keys stay filed under the old
# "localhost" name so existing pairings keep working.
API_BASE = "http://127.0.0.1:19520"
SECRET_KEY = "http://localhost:19520"
APP_NAME = "Inkscape (Send to LightBurn)"
CAPABILITIES = ["upload"]


class LBError(Exception):
    """Something the user needs to read and act on."""


class Unreachable(LBError):
    """Nothing is listening on the LightBurn port."""


def _secret_file():
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif system == "Darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "inkscape-send-to-lightburn", "secret.json")


def load_secret():
    try:
        with open(_secret_file(), "r", encoding="utf-8") as fh:
            return json.load(fh).get(SECRET_KEY, "")
    except (OSError, ValueError):
        return ""


def save_secret(secret):
    path = _secret_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            store = json.load(fh)
        if not isinstance(store, dict):
            store = {}
    except (OSError, ValueError):
        store = {}
    store[SECRET_KEY] = secret
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(store, fh)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def forget_secret():
    """Forget the key for the current port only."""
    path = _secret_file()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            store = json.load(fh)
        store.pop(SECRET_KEY, None)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(store, fh)
    except (OSError, ValueError, AttributeError):
        pass


def bearer(secret):
    msg = str(int(time.time()) // 60).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def api_alive(timeout=2):
    try:
        with urllib.request.urlopen(API_BASE + "/", timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True  # something answered
    except (urllib.error.URLError, OSError):
        return False


def pair():
    body = json.dumps({"application_name": APP_NAME, "capabilities": CAPABILITIES}).encode()
    req = urllib.request.Request(API_BASE + "/api/connect", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:  # 30 s consent dialog
            secret = json.loads(resp.read()).get("secret", "")
    except urllib.error.HTTPError as err:
        if err.code == 403:
            raise LBError("Pairing was declined or timed out. Run again and click "
                          "Allow in the LightBurn dialog.")
        raise LBError("Pairing failed: HTTP %s %s" % (err.code, err.reason))
    except (urllib.error.URLError, OSError) as err:
        raise Unreachable("Can't reach LightBurn (%s)" % getattr(err, "reason", err))
    if not secret:
        raise LBError("LightBurn approved pairing but returned no secret.")
    save_secret(secret)
    return secret


def send_file(path, filename, replace, log=lambda msg: None):
    """POST the file to LightBurn. Pairs first if needed; re-pairs once on a 401.
    Everything LightBurn sends back is passed to log (never the secret or token)."""
    with open(path, "rb") as fh:
        data = fh.read()
    endpoint = "/api/file/open" if replace else "/api/file/upload"
    secret = load_secret() or pair()
    for attempt in (1, 2):
        log("POST %s%s  X-Filename=%s  %d bytes" % (API_BASE, endpoint, filename, len(data)))
        req = urllib.request.Request(
            API_BASE + endpoint, data=data, method="POST",
            headers={"Authorization": "Bearer " + bearer(secret),
                     "Content-Type": "application/octet-stream",
                     "X-Filename": filename})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", "replace").strip()
                log("LightBurn replied: HTTP %s %s  %s" % (resp.status, resp.reason, body[:500]))
                return
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", "replace").strip()
            log("LightBurn replied: HTTP %s %s  %s" % (err.code, err.reason, body[:500]))
            if err.code in (401, 403) and attempt == 1:
                log("authorization rejected; pairing again")
                forget_secret()
                secret = pair()
                continue
            raise LBError("LightBurn rejected the file: HTTP %s %s %s"
                          % (err.code, err.reason, body[:200]))
        except (urllib.error.URLError, OSError) as err:
            raise Unreachable("Can't reach LightBurn (%s)" % getattr(err, "reason", err))


# ------------------------------------------------- legacy UDP interface
# Older LightBurn (before the REST API) listens for UDP commands on
# 127.0.0.1:19840 and answers on 19841. PING = is it running, LOADFILE:<path>
# opens a file (File > Open, so it replaces the project and may prompt to save).
UDP_OUT_PORT = 19840
UDP_IN_PORT = 19841


def udp_send(message, timeout=1.0):
    """Send one UDP command. Returns LightBurn's reply, or None if no reply."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", UDP_IN_PORT))
        except OSError:
            raise LBError("UDP port %d is in use by another program, so I can't "
                          "hear LightBurn's reply." % UDP_IN_PORT)
        sock.settimeout(timeout)
        try:
            sock.sendto(message.encode("utf-8"), ("127.0.0.1", UDP_OUT_PORT))
            data, _ = sock.recvfrom(4096)
            return data.decode("utf-8", "replace").strip()
        except OSError:  # timeout, or ICMP "port unreachable" on Windows
            return None
    finally:
        sock.close()


def find_lightburn_installs(system):
    """Return [(is_prerelease, path)] for installed LightBurn builds, release first.
    Early Access builds install beside the release one with a _PreRelease suffix."""
    import glob
    found = []
    if system == "Windows":
        bases = []
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            b = os.environ.get(env)
            if b and b not in bases:
                bases.append(b)
        for b in bases:
            for d in sorted(glob.glob(os.path.join(b, "LightBurn*"))):
                exes = [os.path.join(d, "LightBurn.exe")]
                exes += sorted(glob.glob(os.path.join(d, "LightBurn*.exe")))
                for exe in exes:
                    name = os.path.basename(exe).lower()
                    if os.path.isfile(exe) and "udp" not in name and "uninst" not in name:
                        found.append(("prerelease" in d.lower(), exe))
                        break
    elif system == "Darwin":
        for app in sorted(glob.glob("/Applications/LightBurn*.app")):
            found.append(("prerelease" in app.lower(), app))
    found.sort(key=lambda t: t[0])  # stable sort: release builds first
    return found


def _folder_key(path):
    """Lower-case install folder or app name, e.g. ...\\LightBurn_PreRelease\\LightBurn.exe
    -> lightburn_prerelease. Splits on both separators so it works for any platform's paths."""
    import re
    parts = [x for x in re.split(r"[\\/]", path) if x]
    if path.lower().endswith(".app"):
        return parts[-1][:-4].lower()
    return parts[-2].lower() if len(parts) >= 2 else ""


def choose_install(installs, build):
    """build is 'release' (folder LightBurn) or 'prerelease' (folder LightBurn_PreRelease).
    Other folders, such as old archived versions, are ignored. Returns a path or None."""
    wanted = "lightburn_prerelease" if build == "prerelease" else "lightburn"
    for _flag, path in installs:
        if _folder_key(path) == wanted:
            return path
    return None


def debug_key_held():
    """Is Alt (Option on Mac) down right now? Lets the user ask for the debug log by holding it on Apply.
    Windows and macOS only; anywhere else (or on any error) this is simply False."""
    try:
        system = platform.system()
        if system == "Windows":
            import ctypes
            return bool(ctypes.windll.user32.GetAsyncKeyState(0x12) & 0x8000)  # VK_MENU (Alt)
        if system == "Darwin":
            import ctypes
            lib = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/"
                              "ApplicationServices")
            lib.CGEventSourceFlagsState.restype = ctypes.c_uint64
            lib.CGEventSourceFlagsState.argtypes = [ctypes.c_int32]
            return bool(lib.CGEventSourceFlagsState(0) & 0x80000)  # Option mask
    except Exception:  # noqa: BLE001
        pass
    return False


def nearest_layer_colour(rgb):
    if tuple(rgb) in LB_TOOLS:
        return tuple(rgb)
    return min(LB_LAYERS, key=lambda c: sum((a - b) ** 2 for a, b in zip(c, rgb)))


class SendToLightBurn(inkex.EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--mode", default="open")
        pars.add_argument("--selection_only", type=inkex.Boolean, default=False)
        pars.add_argument("--text_to_paths", type=inkex.Boolean, default=True)
        pars.add_argument("--ungroup", type=inkex.Boolean, default=False)
        pars.add_argument("--remap_colors", type=inkex.Boolean, default=False)
        pars.add_argument("--open_method", default="merge")
        pars.add_argument("--lb_build", default="release")
        pars.add_argument("--connection", default="auto")
        pars.add_argument("--save_path", default="")

    # ------------------------------------------------------------------ main
    def trace(self, msg):
        """Record a decision; written to last_run.log and optionally shown at the end."""
        if not hasattr(self, "_trace"):
            self._trace = []
            self._t0 = time.time()
        self._trace.append("[+%.2fs] %s" % (time.time() - self._t0, msg))
        try:
            d = os.path.join(tempfile.gettempdir(), "inkscape_to_lightburn")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "last_run.log"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(self._trace) + "\n")
        except OSError:
            pass

    def effect(self):
        o = self.options
        self._show_log = debug_key_held()  # sampled once, at the start
        self.trace("mode=%s connection=%s lb_build=%s platform=%s"
                   % (o.mode, o.connection, o.lb_build, platform.system()))
        work = tempfile.mkdtemp(prefix="lb_ink_")
        try:
            src = os.path.join(work, "src.svg")
            self.document.write(src, encoding="utf-8", xml_declaration=True)

            if o.selection_only:
                ids = [e.get_id() for e in self.svg.selection.values()]
                if not ids:
                    raise inkex.AbortExtension(
                        "'Selected objects only' is ticked but nothing is selected.")
                doc = inkex.load_svg(src)
                self.prune_to(doc.getroot(), ids)
                doc.write(src, encoding="utf-8", xml_declaration=True)

            final = src
            if o.text_to_paths or o.ungroup:
                self.trace("cleanup (text to paths / ungroup) starting")
                final = self.run_inkscape_cleanup(src, work)
                self.trace("cleanup finished")
            if o.remap_colors:
                self.remap(final)

            stem = self.doc_stem()
            if o.mode == "save":
                self.deliver_save(final, stem)
            else:
                self.deliver_open(final, stem)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            if o.mode != "save" and self._show_log:
                self.msg("\n".join(getattr(self, "_trace", [])))

    # --------------------------------------------------------------- helpers
    @staticmethod
    def prune_to(root, ids):
        """Delete everything except the selected objects, their ancestors
        (so inherited transforms survive) and their descendants."""
        for i in ids:
            el = root.getElementById(i)
            if el is None:
                continue
            el.set("data-lb", "sel")
            for d in el.descendants():
                d.set("data-lb", "sel")
            for a in el.ancestors():
                if a.get("data-lb") is None:
                    a.set("data-lb", "anc")

        def walk(parent):
            for child in list(parent):
                if not isinstance(child.tag, str):
                    continue
                if etree.QName(child).localname in KEEP_TAGS:
                    continue
                mark = child.get("data-lb")
                if mark == "sel":
                    continue
                if mark == "anc":
                    walk(child)
                else:
                    parent.remove(child)

        walk(root)
        for el in root.iter():
            if isinstance(el.tag, str):
                el.attrib.pop("data-lb", None)

    def run_inkscape_cleanup(self, src, work):
        o = self.options
        acts = []
        if o.text_to_paths:
            acts += ["select-all:all", "object-to-path"]
        if o.ungroup:
            acts += ["select-all:all", "selection-ungroup"] * 8
        out = os.path.join(work, "out.svg")
        acts += ["export-type:svg", "export-plain-svg",
                 "export-filename:" + out.replace("\\", "/"), "export-do"]
        try:
            inkscape(src, actions=";".join(acts))
        except Exception as err:  # noqa: BLE001
            raise inkex.AbortExtension("Inkscape cleanup step failed: %s" % err)
        if not os.path.isfile(out):
            raise inkex.AbortExtension("Inkscape cleanup step produced no file.")
        return out

    def remap(self, path):
        doc = inkex.load_svg(path)
        for el in doc.getroot().iter():
            if not isinstance(el.tag, str):
                continue
            style = el.get("style") or ""
            parts = [p for p in style.split(";") if p.strip()]
            changed = False
            for n, p in enumerate(parts):
                k, _, v = p.partition(":")
                if k.strip() == "stroke":
                    new = self._snap(v.strip())
                    if new:
                        parts[n] = "stroke:" + new
                        changed = True
            if changed:
                el.set("style", ";".join(parts))
            attr = el.get("stroke")
            if attr:
                new = self._snap(attr)
                if new:
                    el.set("stroke", new)
        doc.write(path, encoding="utf-8", xml_declaration=True)

    @staticmethod
    def _snap(value):
        if not value or value in ("none", "inherit", "currentColor") or value.startswith("url("):
            return None
        try:
            c = Color(value).to_rgb()
            r, g, b = int(c[0]), int(c[1]), int(c[2])
        except Exception:  # noqa: BLE001
            return None
        return "#%02x%02x%02x" % nearest_layer_colour((r, g, b))

    def doc_stem(self):
        name = self.svg.get("sodipodi:docname") or "drawing"
        return os.path.splitext(os.path.basename(name))[0] or "drawing"

    def doc_dir(self):
        try:
            p = self.document_path()
        except Exception:  # noqa: BLE001
            p = ""
        if p and os.path.isfile(p):
            d = os.path.dirname(p)
            # Unsaved docs live in Inkscape's temp area - don't save there
            if not d.startswith(tempfile.gettempdir()):
                return d
        return os.path.join(os.path.expanduser("~"), "Documents") \
            if os.path.isdir(os.path.join(os.path.expanduser("~"), "Documents")) \
            else os.path.expanduser("~")

    # ------------------------------------------------------------- delivery
    def deliver_save(self, final, stem):
        target = self.options.save_path.strip()
        if not target:
            target = os.path.join(self.doc_dir(), stem + "_lightburn.svg")
        elif os.path.isdir(target):
            target = os.path.join(target, stem + "_lightburn.svg")
        elif not target.lower().endswith(".svg"):
            target += ".svg"
        try:
            shutil.copyfile(final, target)
        except OSError as err:
            raise inkex.AbortExtension("Could not save to %s: %s" % (target, err))
        self.msg("Saved to:\n%s" % target)

    def deliver_open(self, final, stem):
        o = self.options
        outdir = os.path.join(tempfile.gettempdir(), "inkscape_to_lightburn")
        os.makedirs(outdir, exist_ok=True)
        target = os.path.join(outdir, "%s_%d.svg" % (stem, int(time.time())))
        shutil.copyfile(final, target)

        try:
            self.open_in_lightburn(target, stem)
        except LBError as err:
            raise inkex.AbortExtension("%s\nThe cleaned-up file is at:\n%s" % (err, target))

    def open_in_lightburn(self, target, stem):
        """Deliver the file according to the chosen connection method.
        auto: REST API, then legacy UDP, then start LightBurn with the file as an argument.
        udp: legacy UDP, then start LightBurn the same way. rest (not offered in the dialog
        at present): REST API only, never starts LightBurn."""
        o = self.options
        method = o.connection
        if method in ("auto", "rest"):
            alive = api_alive()
            self.trace("REST API answering on %s: %s" % (API_BASE, alive))
            if alive:
                send_file(target, stem + ".svg", o.open_method == "replace", self.trace)
                self.trace("uploaded via REST")
                return
            if method != "auto":
                raise LBError("Nothing answered on the REST API port 19520. Check that LightBurn "
                              "is running and that its API is enabled.")
        ping = udp_send("PING", timeout=0.5)
        self.trace("UDP PING sent to 127.0.0.1:%d, listening on %d; reply: %r"
                   % (UDP_OUT_PORT, UDP_IN_PORT, ping))
        if ping is not None:
            reply = udp_send("LOADFILE:" + target, timeout=15)
            self.trace("UDP LOADFILE sent to 127.0.0.1:%d; reply: %r" % (UDP_OUT_PORT, reply))
            if reply != "OK":
                raise LBError("LightBurn didn't confirm loading the file (reply: %r)." % reply)
            return
        self.trace("LightBurn not reachable; starting it")
        if self.launch(target, quiet=True) is None:
            if method == "udp":
                raise LBError("LightBurn didn't answer on the older UDP interface (port %d), and "
                              "I couldn't find the %s build to start. If LightBurn is open, check "
                              "that its UDP interface is enabled." % (UDP_OUT_PORT, self.options.lb_build))
            raise LBError("LightBurn isn't answering on port 19520, and I couldn't find the %s "
                          "build to start. If LightBurn is open, check that its API is enabled."
                          % self.options.lb_build)

    def launch(self, target, quiet=False):
        cmd = self.lightburn_command(target)
        self.trace("launch command: %r" % (cmd,))
        if cmd is None:
            if quiet:
                return None
            raise LBError("Couldn't find the LightBurn build you chose (%s). Choose the other "
                          "build in the dialog, or install it in the default folder."
                          % self.options.lb_build)
        kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL}
        if platform.system() == "Windows":
            kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
        else:
            kw["start_new_session"] = True
        try:
            # Popen's destructor warns if the child is still running when we exit;
            # that is exactly what we want here, and Inkscape shows stderr as an error.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ResourceWarning)
                proc = subprocess.Popen(cmd, **kw)
                time.sleep(0.75)  # did it die straight away?
                rc = proc.poll()
                self.trace("process after 0.75 s: %s" % ("still running" if rc is None else "exited with code %s" % rc))
                if rc not in (None, 0):
                    raise LBError("LightBurn exited immediately with code %s (command: %s)" % (rc, cmd[0]))
                proc.returncode = 0
                return proc
        except OSError as err:
            raise LBError("Couldn't launch LightBurn: %s" % err)

    def lightburn_command(self, target):
        system = platform.system()
        installs = find_lightburn_installs(system)
        if installs:
            path = choose_install(installs, self.options.lb_build)
            self.trace("build wanted: %s -> %s" % (self.options.lb_build, path))
            if path:
                return ["open", "-a", path, target] if system == "Darwin" else [path, target]
            return None
        if system not in ("Windows", "Darwin"):
            for name in ("LightBurn", "lightburn"):
                w = shutil.which(name)
                if w:
                    return [w, target]
            home = os.path.expanduser("~")
            for c in (os.path.join(home, "LightBurn", "LightBurn"), "/opt/LightBurn/LightBurn"):
                if os.path.isfile(c):
                    return [c, target]
        return None


if __name__ == "__main__":
    SendToLightBurn().run()
