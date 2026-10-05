"""Click-and-go window. Same jobs as the CLI. No extra packages required.

Prefers GTK 3 (already on this machine). If GTK is missing, opens a local
page in the browser instead.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .actions import (
    choose_hd_scale,
    default_work_dir,
    inspect_path,
    pack_hd,
    pull_pictures,
    save_model,
)
from .mesh import MeshExportError
from .pack import PackError


def _list_xpp(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(p for p in path.rglob("*.xpp") if p.is_file())
    return [path]


def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        os.spawnlp(os.P_NOWAIT, "open", "open", str(path))
        return
    os.spawnlp(os.P_NOWAIT, "xdg-open", "xdg-open", str(path))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    web = "--web" in argv
    paths = [Path(a) for a in argv if not a.startswith("-")]
    return run_ui(paths=paths, web=web)


def run_ui(*, paths: list[Path] | None = None, web: bool = False) -> int:
    if not web:
        try:
            return _run_gtk(paths or [])
        except Exception as exc:
            sys.stderr.write(f"window backend unavailable ({exc}); opening the local page instead\n")
    return _run_web(paths or [])


def _run_gtk(start_paths: list[Path]) -> int:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk, Pango

    css = b"""
    window { background: #141414; }
    label, button { color: #f2f2f2; font-size: 15px; }
    .title { font-size: 28px; font-weight: 700; }
    .sub { font-size: 15px; color: #c8c8c8; }
    .drop {
        background: #242424;
        border: 3px dashed #6a6a6a;
        padding: 28px;
        font-size: 20px;
        font-weight: 600;
    }
    .action {
        padding: 18px;
        font-size: 18px;
        font-weight: 700;
        background: #2f5d3a;
    }
    .action:disabled { background: #333; color: #777; }
    .secondary { background: #2a2a2a; padding: 12px; }
    textview, textview text { background: #0e0e0e; color: #d6d6d6; font-family: monospace; }
    """

    class App(Gtk.Window):
        def __init__(self) -> None:
            super().__init__(title="if1-tex")
            self.set_default_size(760, 720)
            self.paths: list[Path] = []
            self.busy = False

            provider = Gtk.CssProvider()
            provider.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            root.set_margin_top(18)
            root.set_margin_bottom(18)
            root.set_margin_start(18)
            root.set_margin_end(18)
            self.add(root)

            title = Gtk.Label(label="if1-tex")
            title.get_style_context().add_class("title")
            title.set_xalign(0)
            root.pack_start(title, False, False, 0)
            sub = Gtk.Label(
                label="Pick a game package. Push one big button. That is the whole tool."
            )
            sub.get_style_context().add_class("sub")
            sub.set_xalign(0)
            sub.set_line_wrap(True)
            root.pack_start(sub, False, False, 0)

            self.drop = Gtk.Button(label="CLICK HERE\nand pick an .xpp file\n(or a folder of them)")
            self.drop.get_style_context().add_class("drop")
            self.drop.set_size_request(-1, 140)
            self.drop.connect("clicked", self._pick)
            self.drop.drag_dest_set(
                Gtk.DestDefaults.ALL,
                [Gtk.TargetEntry.new("text/uri-list", 0, 0)],
                Gdk.DragAction.COPY,
            )
            self.drop.connect("drag-data-received", self._dropped)
            root.pack_start(self.drop, False, False, 0)

            self.status = Gtk.Label(label="No file yet.")
            self.status.set_xalign(0)
            self.status.set_line_wrap(True)
            root.pack_start(self.status, False, False, 0)

            hd_row = Gtk.Box(spacing=10)
            hd_row.pack_start(Gtk.Label(label="HD size:"), False, False, 0)
            self.hd = Gtk.ComboBoxText()
            self.hd.append("auto", "Auto (4× if the pictures are 512 or smaller, else 2×)")
            self.hd.append("2", "Always 2×")
            self.hd.append("4", "Always 4×")
            self.hd.set_active_id("auto")
            hd_row.pack_start(self.hd, True, True, 0)
            root.pack_start(hd_row, False, False, 0)

            asm_row = Gtk.Box(spacing=10)
            asm_row.pack_start(Gtk.Label(label="Assemble:"), False, False, 0)
            self.assemble = Gtk.ComboBoxText()
            self.assemble.append("unique-largest", "Unique largest (skip wreck copies)")
            self.assemble.append("unique-first", "Unique first (package order)")
            self.assemble.append("all", "All pieces (wrecked + intact stacked)")
            self.assemble.append("recipe", "Built-in recipe (heli intact)")
            self.assemble.set_active_id("unique-largest")
            asm_row.pack_start(self.assemble, True, True, 0)
            root.pack_start(asm_row, False, False, 0)

            self.pbr = Gtk.CheckButton(label="Remaster lighting (derived PBR — CPU, no GPU)")
            self.pbr.set_active(True)
            root.pack_start(self.pbr, False, False, 0)

            self.btn_pic = Gtk.Button(label="1.  GET THE PICTURES OUT")
            self.btn_hd = Gtk.Button(label="2.  MAKE THEM HD AND PACK A NEW GAME FILE")
            self.btn_mesh = Gtk.Button(label="3.  SAVE THE REMASTER MODEL (PBR)")
            self.btn_open = Gtk.Button(label="Open the folder I just made")
            for b, cls in (
                (self.btn_pic, "action"),
                (self.btn_hd, "action"),
                (self.btn_mesh, "action"),
                (self.btn_open, "secondary"),
            ):
                b.get_style_context().add_class(cls)
                b.set_sensitive(False)
                root.pack_start(b, False, False, 0)
            self.btn_pic.connect("clicked", lambda *_: self._go("pictures"))
            self.btn_hd.connect("clicked", lambda *_: self._go("hd"))
            self.btn_mesh.connect("clicked", lambda *_: self._go("model"))
            self.btn_open.connect("clicked", self._open_last)

            scroll = Gtk.ScrolledWindow()
            scroll.set_min_content_height(200)
            self.log = Gtk.TextView()
            self.log.set_editable(False)
            self.log.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            self.log.override_font(Pango.FontDescription("monospace 12"))
            scroll.add(self.log)
            root.pack_start(scroll, True, True, 0)

            self.last_dir: Path | None = None
            if start_paths:
                self._set_paths(start_paths)

        def _append(self, text: str) -> None:
            buf = self.log.get_buffer()
            buf.insert(buf.get_end_iter(), text.rstrip() + "\n")
            self.log.scroll_to_iter(buf.get_end_iter(), 0.0, False, 0, 0)

        def _set_paths(self, paths: list[Path]) -> None:
            found: list[Path] = []
            for p in paths:
                p = p.expanduser().resolve()
                if not p.exists():
                    self._append(f"not found: {p}")
                    continue
                found.extend(_list_xpp(p))
            self.paths = found
            if not found:
                self.status.set_text("I did not find any .xpp files there.")
                for b in (self.btn_pic, self.btn_hd, self.btn_mesh, self.btn_open):
                    b.set_sensitive(False)
                return
            self.drop.set_label(f"{len(found)} file(s) ready\nclick again to pick different ones")
            lines = []
            for p in found[:8]:
                try:
                    info = inspect_path(p)
                    lines.append(f"{p.name}: {info.summary()}")
                except Exception as exc:
                    lines.append(f"{p.name}: could not read ({exc})")
            extra = f"\n…and {len(found) - 8} more" if len(found) > 8 else ""
            self.status.set_text("\n".join(lines) + extra)
            for b in (self.btn_pic, self.btn_hd, self.btn_mesh):
                b.set_sensitive(True)
            self._append(f"loaded {len(found)} package(s)")

        def _pick(self, *_args) -> None:
            dlg = Gtk.FileChooserDialog(
                title="Pick an .xpp, or a folder of them",
                parent=self,
                action=Gtk.FileChooserAction.OPEN,
            )
            dlg.add_buttons(
                Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK
            )
            dlg.set_select_multiple(True)
            filt = Gtk.FileFilter()
            filt.set_name("inFAMOUS 1 packages")
            filt.add_pattern("*.xpp")
            filt.add_pattern("*.XPP")
            dlg.add_filter(filt)
            anyf = Gtk.FileFilter()
            anyf.set_name("Everything")
            anyf.add_pattern("*")
            dlg.add_filter(anyf)
            if dlg.run() == Gtk.ResponseType.OK:
                names = dlg.get_filenames()
                self._set_paths([Path(n) for n in names])
            dlg.destroy()

        def _dropped(self, _widget, _ctx, _x, _y, data, _info, _time) -> None:
            uris = data.get_uris()
            paths = []
            for uri in uris:
                parsed = urllib.parse.urlparse(uri)
                if parsed.scheme == "file":
                    paths.append(Path(urllib.parse.unquote(parsed.path)))
            if paths:
                self._set_paths(paths)

        def _scale(self) -> int | None:
            ident = self.hd.get_active_id()
            if ident == "2":
                return 2
            if ident == "4":
                return 4
            return None

        def _set_busy(self, busy: bool) -> None:
            self.busy = busy
            on = bool(self.paths) and not busy
            for b in (self.btn_pic, self.btn_hd, self.btn_mesh, self.drop):
                b.set_sensitive(on if b is not self.drop else not busy)
            self.btn_open.set_sensitive(self.last_dir is not None)

        def _go(self, job: str) -> None:
            if self.busy or not self.paths:
                return
            self._set_busy(True)
            scale = self._scale()
            assemble = self.assemble.get_active_id() or "unique-largest"
            remaster = bool(self.pbr.get_active())
            paths = list(self.paths)

            def work() -> None:
                try:
                    for src in paths:
                        dest_root = default_work_dir(src)
                        dest_root.mkdir(parents=True, exist_ok=True)
                        GLib.idle_add(self._append, f"\n=== {src.name} ===")
                        if job == "pictures":
                            info, _f, written, log = pull_pictures(src, dest_root / "pictures")
                            GLib.idle_add(self._append, info.summary())
                            GLib.idle_add(self._append, log)
                            GLib.idle_add(self._append, f"saved {written} picture(s) in {dest_root / 'pictures'}")
                        elif job == "hd":
                            used = scale if scale is not None else choose_hd_scale(inspect_path(src).max_2d_dim)
                            dest = dest_root / f"{src.stem}.hd{used}x.xpp"
                            info, used, msg = pack_hd(src, dest, scale=used)
                            GLib.idle_add(self._append, info.summary())
                            GLib.idle_add(self._append, msg)
                            GLib.idle_add(self._append, f"new game file: {dest}")
                        elif job == "model":
                            dest = dest_root / f"{src.stem}.remaster.glb"
                            try:
                                info, result = save_model(
                                    src, dest, remaster=remaster, assemble=assemble
                                )
                            except MeshExportError as exc:
                                GLib.idle_add(self._append, f"no 3D model here: {exc}")
                                continue
                            GLib.idle_add(self._append, info.summary())
                            GLib.idle_add(self._append, json.dumps(result, indent=2))
                            GLib.idle_add(self._append, f"saved model: {dest}")
                        self.last_dir = dest_root
                except PackError as exc:
                    GLib.idle_add(self._append, f"pack failed: {exc}")
                except Exception:
                    GLib.idle_add(self._append, traceback.format_exc())
                finally:
                    GLib.idle_add(self._set_busy, False)
                    GLib.idle_add(self._append, "done.")

            threading.Thread(target=work, daemon=True).start()

        def _open_last(self, *_args) -> None:
            if self.last_dir is not None:
                _open_folder(self.last_dir)

    win = App()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
    return 0


_WEB_HTML = """<!doctype html>
<meta charset="utf-8">
<title>if1-tex</title>
<style>
body{font-family:system-ui,sans-serif;background:#141414;color:#f2f2f2;max-width:720px;margin:40px auto;padding:0 16px}
h1{font-size:36px;margin:0 0 8px}
p{color:#c8c8c8}
.drop{border:3px dashed #6a6a6a;background:#242424;padding:40px;text-align:center;font-size:22px;font-weight:700;margin:20px 0}
button{display:block;width:100%;margin:10px 0;padding:18px;font-size:18px;font-weight:700;background:#2f5d3a;color:#fff;border:0;cursor:pointer}
button:disabled{background:#333;color:#777}
pre{background:#0e0e0e;padding:12px;white-space:pre-wrap;min-height:160px}
label{display:block;margin:12px 0}
</style>
<h1>if1-tex</h1>
<p>Pick a game package. Push one big button. That is the whole tool.</p>
<div class="drop" id="drop">CLICK HERE<br>and pick an .xpp file</div>
<input id="file" type="file" accept=".xpp,.XPP" hidden>
<label>Where to put the results<br>
<input id="outdir" style="width:100%;padding:8px;background:#222;color:#fff;border:1px solid #555" placeholder="leave blank = next to the file, or Downloads/if1-tex-out">
</label>
<label>HD size
<select id="scale" style="width:100%;padding:8px;background:#222;color:#fff">
<option value="auto">Auto (4× if 512 or smaller, else 2×)</option>
<option value="2">Always 2×</option>
<option value="4">Always 4×</option>
</select></label>
<label>Assemble
<select id="assemble" style="width:100%;padding:8px;background:#222;color:#fff">
<option value="unique-largest">Unique largest (skip wreck copies)</option>
<option value="unique-first">Unique first</option>
<option value="all">All pieces</option>
<option value="recipe">Built-in recipe (heli)</option>
</select></label>
<label><input id="pbr" type="checkbox" checked> Remaster lighting (derived PBR — CPU, no GPU)</label>
<button id="b1">1. GET THE PICTURES OUT</button>
<button id="b2">2. MAKE THEM HD AND PACK A NEW GAME FILE</button>
<button id="b3">3. SAVE THE REMASTER MODEL (PBR)</button>
<pre id="log">waiting…</pre>
<script>
const file=document.getElementById('file');
const drop=document.getElementById('drop');
const log=document.getElementById('log');
drop.onclick=()=>file.click();
drop.ondragover=e=>{e.preventDefault();};
drop.ondrop=e=>{e.preventDefault(); if(e.dataTransfer.files[0]) file.files=e.dataTransfer.files; drop.textContent=file.files[0].name;};
file.onchange=()=>{ if(file.files[0]) drop.textContent=file.files[0].name; };
async function go(job){
  if(!file.files[0]){ log.textContent='pick a file first'; return; }
  const body=new FormData();
  body.append('job', job);
  body.append('scale', document.getElementById('scale').value);
  body.append('assemble', document.getElementById('assemble').value);
  body.append('pbr', document.getElementById('pbr').checked ? '1' : '0');
  body.append('outdir', document.getElementById('outdir').value);
  body.append('file', file.files[0]);
  log.textContent='working…';
  const r=await fetch('/run', {method:'POST', body, headers:{'X-If1-Token':'__IF1_TOKEN__'}});
  const t=await r.text();
  log.textContent=t;
}
document.getElementById('b1').onclick=()=>go('pictures');
document.getElementById('b2').onclick=()=>go('hd');
document.getElementById('b3').onclick=()=>go('model');
</script>
"""


def _run_web(start_paths: list[Path]) -> int:
    upload_root = Path.home() / "if1-tex-out"
    upload_root.mkdir(parents=True, exist_ok=True)
    preload = start_paths[0] if start_paths else None
    # Per-launch secret: only the page this server served knows it, so another
    # site in the browser cannot drive the local server (CSRF).
    token = secrets.token_urlsafe(32)
    allowed_host: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def do_GET(self) -> None:
            if self.path != "/":
                self.send_error(404)
                return
            body = _WEB_HTML.replace("__IF1_TOKEN__", token).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/run":
                self.send_error(404)
                return
            if not _request_is_trusted(self.headers, token, allowed_host[0]):
                self.send_error(403)
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            ctype = self.headers.get("Content-Type", "")
            try:
                fields, files = _parse_multipart(ctype, raw)
                job = fields.get("job", "pictures")
                scale_s = fields.get("scale", "auto")
                assemble_s = fields.get("assemble", "unique-largest")
                remaster = fields.get("pbr", "1") != "0"
                outdir_s = fields.get("outdir", "").strip()
                if preload is not None and "file" not in files:
                    src = preload
                else:
                    name, blob = files["file"]
                    src = upload_root / Path(name).name
                    src.write_bytes(blob)
                dest_root = Path(outdir_s).expanduser() if outdir_s else default_work_dir(src)
                dest_root.mkdir(parents=True, exist_ok=True)
                info = inspect_path(src)
                lines = [info.summary(), f"file: {src}", f"out: {dest_root}"]
                if job == "pictures":
                    _i, _f, written, log = pull_pictures(src, dest_root / "pictures")
                    lines.append(log)
                    lines.append(f"saved {written} picture(s)")
                elif job == "hd":
                    used = None if scale_s == "auto" else int(scale_s)
                    used = used if used is not None else choose_hd_scale(info.max_2d_dim)
                    dest = dest_root / f"{src.stem}.hd{used}x.xpp"
                    _i, used, msg = pack_hd(src, dest, scale=used)
                    lines.append(msg)
                elif job == "model":
                    dest = dest_root / f"{src.stem}.remaster.glb"
                    _i, result = save_model(
                        src, dest, remaster=remaster, assemble=assemble_s
                    )
                    lines.append(json.dumps(result, indent=2))
                    lines.append(f"saved {dest}")
                else:
                    lines.append("unknown job")
                text = "\n".join(lines)
            except Exception:
                text = traceback.format_exc()
            body = text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{httpd.server_port}/"
    allowed_host.append(f"127.0.0.1:{httpd.server_port}")
    print(f"if1-tex {__version__}  {url}", flush=True)
    webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


def _request_is_trusted(headers, token: str, host: str) -> bool:
    """Accept a POST only from the page this server served: exact Host,
    same Origin when the browser sends one, and the per-launch token."""
    if headers.get("Host", "") != host:
        return False
    origin = headers.get("Origin")
    if origin is not None and origin != f"http://{host}":
        return False
    return hmac.compare_digest(headers.get("X-If1-Token", ""), token)


def _parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    if "boundary=" not in content_type:
        raise ValueError("expected multipart form")
    boundary = content_type.split("boundary=", 1)[1].strip().encode()
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for part in body.split(b"--" + boundary):
        if not part or part in (b"--\r\n", b"--"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        header_blob, _, data = part.partition(b"\r\n\r\n")
        headers = header_blob.decode("utf-8", "replace")
        if data.endswith(b"\r\n"):
            data = data[:-2]
        disp = ""
        for line in headers.split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                disp = line
        name = ""
        filename = ""
        for item in disp.split(";"):
            item = item.strip()
            if item.startswith("name="):
                name = item.split("=", 1)[1].strip('"')
            elif item.startswith("filename="):
                filename = item.split("=", 1)[1].strip('"')
        if not name:
            continue
        if filename:
            files[name] = (filename, data)
        else:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files
