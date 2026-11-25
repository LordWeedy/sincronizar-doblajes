#!/usr/bin/env python3
"""
probar_actualizador.py - Pruebas del actualizador de Sync Master con instalaciones, servidor HTTPS, claves y
certificados DE PRUEBA, todo en una carpeta temporal (no toca la carpeta del programa ni sus ajustes).

Se fabrican versiones con publicar_version.py a partir de una copia de esta carpeta (con los servidores, la clave
y el certificado de prueba puestos en esa copia), se publican en un servidor HTTPS local con fallos a voluntad
(lento, se para, se corta, error 500, contenido alterado, certificado no válido, caído) y se instalan desde el
paquete .zip como lo haría cualquiera; después se actualiza y se comprueba el resultado archivo por archivo.

Uso:  python probar_actualizador.py [--solo prueba1,prueba2] [--conservar]
Necesita openssl (el de Git para Windows vale) para los certificados de prueba. Termina con código 1 si algo falla.
"""

import argparse
import base64
import ctypes
import hashlib
import http.server
import importlib.util
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
# el programa: la carpeta de arriba (lo de siempre: esta es su «publicacion») o, desde la 1.4.18, el src/ del clon del
# repositorio, con esta carpeta en su herramientas/ (como en publicar_version.py)
_ARRIBA = os.path.dirname(AQUI)
CLON = (_ARRIBA if not os.path.isfile(os.path.join(_ARRIBA, "actualizador.py"))
        and os.path.isfile(os.path.join(_ARRIBA, "src", "actualizador.py")) else None)
PROGRAMA = os.path.join(CLON, "src") if CLON else _ARRIBA
EN_CLON = CLON is not None
PY = sys.executable
V1, V2, V3, V_GUI_ROTA, V_MOTOR_ROTO = "1.0", "1.1", "1.2", "1.3", "1.4"
# la numeración de antes (por fecha): la última publicada con el código de entonces y el paso a la numerada
V_VIEJA, V_PUENTE = "2026.10.25", "2026.10.26"
COPIAR = ("Sincronizar_Doblaje_Gui.py", "sincronizar_doblaje.py", "limpiar_vhs.py", "forzados_vo.py",
          "actualizador.py", "actualizador_ventana.py", "probar_sincronizador.py", "Iniciar Sincronizar Doblaje.bat",
          "Sync_Master.ico",
          "publicacion/publicar_version.py", "publicacion/archivos_publicados.txt")
# el LEEME del paquete, en las copias de desarrollo de prueba (las de siempre: con «publicacion» dentro): donde lo pone
# archivos_publicados.txt (desde la 1.4.18 va en el programa, src/LEEME.txt)
LEEME = "LEEME.txt" if EN_CLON else "publicacion/LEEME.txt"
COPIAR += (LEEME,)


def origen(r):
    """Dónde está, en esta instalación de desarrollo, lo que va en r de una copia de desarrollo de prueba."""
    if EN_CLON and r.startswith("publicacion/"):
        return os.path.join(AQUI, r[len("publicacion/"):])
    return os.path.join(PROGRAMA, r)
SIN_CONSOLA = 0x08000000
AJUSTES_J = "Sincronizar_Doblaje_ajustes.json"


def sha(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def cargar(ruta, nombre):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def ejecutar(orden, cwd=None, env=None, timeout=300):
    r = subprocess.run(orden, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=SIN_CONSOLA)
    return r.returncode, r.stdout + r.stderr


# ---------------------------------------------------------------------------------------------------------------
# Servidor HTTPS de prueba con fallos a voluntad

class Control:
    def __init__(self):
        self.lock = threading.Lock()
        self.reglas = {}            # sufijo de la ruta -> dict(modo, ...)
        self.peticiones = []        # (ruta, cabecera Range, bytes enviados)

    def regla(self, ruta):
        with self.lock:
            hallados = [(len(k), k, r) for k, r in self.reglas.items() if k in ruta and r.get("veces", 1) > 0]
        if not hallados:
            return None, None
        _, k, r = max(hallados)
        return k, r

    def gastar(self, sufijo):
        with self.lock:
            if sufijo in self.reglas:
                self.reglas[sufijo]["veces"] = self.reglas[sufijo].get("veces", 1) - 1

    def limpiar(self):
        with self.lock:
            self.reglas.clear()
            self.peticiones.clear()

    def bytes_de(self, trozo):
        with self.lock:
            return sum(n for r, _, n in self.peticiones if trozo in r)


class Manejador(http.server.BaseHTTPRequestHandler):
    raiz = None
    control = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        ruta = self.path.split("?")[0]
        local = os.path.normpath(os.path.join(self.raiz, *[p for p in ruta.split("/") if p and p != ".."]))
        sufijo, regla = self.control.regla(ruta)
        modo = regla["modo"] if regla else None
        if modo == "error_500":
            self.control.gastar(sufijo)
            return self._error(500)
        if modo == "redirigir_http":
            self.control.gastar(sufijo)
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/x")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not os.path.isfile(local):
            return self._error(404)
        datos = open(local, "rb").read()
        if modo == "alterar":
            datos = datos[:-1] + bytes([datos[-1] ^ 1])
        if modo == "truncar_json":
            self.control.gastar(sufijo)
            datos = datos[:len(datos) // 2]
        inicio = 0
        rango = self.headers.get("Range")
        if rango and rango.startswith("bytes=") and modo != "sin_rangos":
            inicio = int(rango[6:].split("-")[0])
        cuerpo = datos[inicio:]
        self.send_response(206 if inicio else 200)
        if inicio:
            self.send_header("Content-Range", f"bytes {inicio}-{len(datos) - 1}/{len(datos)}")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        enviado = 0
        try:
            if modo in ("parar_tras", "cortar_tras"):
                self.control.gastar(sufijo)
                n = min(regla["bytes"], len(cuerpo))
                self.wfile.write(cuerpo[:n])
                self.wfile.flush()
                enviado = n
                if modo == "parar_tras":
                    time.sleep(regla.get("segundos", 30))
                self.close_connection = True
                return
            if modo == "lento":
                paso = max(1024, regla["bps"] // 20)
                for i in range(0, len(cuerpo), paso):
                    self.wfile.write(cuerpo[i:i + paso])
                    self.wfile.flush()
                    enviado += len(cuerpo[i:i + paso])
                    time.sleep(paso / regla["bps"])
                return
            self.wfile.write(cuerpo)
            enviado = len(cuerpo)
        except (ConnectionError, OSError):
            pass
        finally:
            with self.control.lock:
                self.control.peticiones.append((ruta, rango, enviado))

    def _error(self, codigo):
        self.send_response(codigo)
        self.send_header("Content-Length", "0")
        self.end_headers()


class Servidor:
    def __init__(self, raiz, cert, clave):
        self.control = Control()
        manejador = type("M", (Manejador,), {"raiz": raiz, "control": self.control})
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), manejador)
        self.httpd.daemon_threads = True
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, clave)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.puerto = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, carpeta):
        return f"https://localhost:{self.puerto}/{carpeta}/"

    def parar(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def buscar_openssl():
    for c in (shutil.which("openssl"), r"C:\Program Files\Git\mingw64\bin\openssl.exe",
              r"C:\Program Files\Git\usr\bin\openssl.exe"):
        if c and os.path.exists(c):
            return c
    raise RuntimeError("no se encuentra openssl (hace falta para los certificados de prueba)")


def certificados(carpeta, nombre="ca"):
    """CA de prueba y certificado de localhost firmado por ella. -> (ca.pem, srv.pem, srv.key)"""
    o = buscar_openssl()
    ca, ca_k = os.path.join(carpeta, f"{nombre}.pem"), os.path.join(carpeta, f"{nombre}.key")
    srv, srv_k = os.path.join(carpeta, f"{nombre}_srv.pem"), os.path.join(carpeta, f"{nombre}_srv.key")
    csr, ext = os.path.join(carpeta, f"{nombre}_srv.csr"), os.path.join(carpeta, f"{nombre}_ext.cnf")
    with open(ext, "w") as f:
        f.write("subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=CA:FALSE\n"
                "keyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nauthorityKeyIdentifier=keyid\n"
                "subjectKeyIdentifier=hash\n")
    for orden in (
            [o, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-keyout", ca_k,
             "-out", ca, "-days", "2", "-subj", f"/CN=Prueba {nombre}", "-addext", "basicConstraints=critical,CA:TRUE",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-addext", "subjectKeyIdentifier=hash"],
            [o, "req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-keyout", srv_k, "-out",
             csr, "-subj", "/CN=localhost"],
            [o, "x509", "-req", "-in", csr, "-CA", ca, "-CAkey", ca_k, "-CAcreateserial", "-out", srv, "-days", "2",
             "-extfile", ext]):
        codigo, salida = ejecutar(orden)
        if codigo:
            raise RuntimeError(f"openssl: {salida[-400:]}")
    return ca, srv, srv_k


# ---------------------------------------------------------------------------------------------------------------
# Entorno: copia de desarrollo, versiones publicadas e instalaciones

class Entorno:
    def __init__(self, carpeta):
        self.t = carpeta
        self.web = os.path.join(carpeta, "web")
        os.makedirs(self.web)
        self.ca, srv, srv_k = certificados(carpeta, "ca")
        self.servidor = Servidor(self.web, srv, srv_k)
        _, srv2, srv2_k = certificados(carpeta, "ajena")         # firmado por una CA en la que no se confía
        self.servidor_ajeno = Servidor(self.web, srv2, srv2_k)
        self.secreta = os.urandom(32)
        sys.path.insert(0, PROGRAMA)
        self.act = cargar(os.path.join(PROGRAMA, "actualizador.py"), "act_prueba")
        self.publica = self.act.ed25519_publica(self.secreta)
        self.kid = self.act.id_de_clave(self.publica)
        self.ruta_clave = os.path.join(carpeta, "clave_prueba.txt")
        with open(self.ruta_clave, "w") as f:
            f.write(self.secreta.hex() + "\n")
        self.claves = {self.kid: self.publica.hex()}
        self.n_inst = 0
        self.dev_vieja = self.dev_puente = None
        self.escribir_auxiliares()

    def url(self, destino="pub"):
        return self.servidor.url(destino)

    def dev(self, nombre, servidores=None):
        """Copia de esta carpeta (la de desarrollo) con los servidores, la clave y la CA de prueba."""
        d = os.path.join(self.t, nombre)
        for r in COPIAR:
            os.makedirs(os.path.dirname(os.path.join(d, r)), exist_ok=True)
            shutil.copyfile(origen(r), os.path.join(d, r))
        with open(os.path.join(d, "herramienta_grande.bin"), "wb") as f:
            f.write(hashlib.sha256(b"semilla").digest() * (12 * 1024 * 1024 // 32))       # 12 MB
        with open(os.path.join(d, "publicacion", "archivos_publicados.txt"), "a", encoding="utf-8") as f:
            f.write("herramienta_grande.bin\n")
        pv = cargar(os.path.join(d, "publicacion", "publicar_version.py"), f"pv_{nombre}")
        self.para_pruebas(pv, os.path.join(d, "actualizador.py"), servidores)
        return d

    def para_pruebas(self, pv, a, servidores=None):
        """Servidores, clave, CA y tiempos de prueba en un actualizador.py."""
        pv.fijar_constante(a, "SERVIDORES", repr(servidores or [self.url("pub")]))
        pv.fijar_constante(a, "CLAVES_PUBLICAS", repr(self.claves))
        pv.fijar_constante(a, "CA_EXTRA", repr(self.ca))
        for nombre_c, valor in (("T_SALUD", "25"), ("T_CIERRE", "20"), ("T_CONEXION", "4"), ("ESPERA_BASE", "0.2"),
                                ("INTENTOS", "3")):
            pv.fijar_constante(a, nombre_c, valor)

    def dev_viejo(self, nombre, version):
        """Copia de desarrollo (con la herramienta de publicación de ahora) que publica el programa TAL COMO SE
        PUBLICÓ en `version` (sus archivos, de las versiones preparadas de esta carpeta o, si no están, como en un
        clon recién bajado o en GitHub, de las publicadas en el repositorio): para probar el paso desde las copias que
        ya hay instaladas. None si esa versión no está en ninguna de las dos."""
        origen = next((o for o in [os.path.join(AQUI, "versiones", version)]
                       + ([os.path.join(CLON, "versiones", version)] if CLON else [])
                       if os.path.isfile(os.path.join(o, "manifiesto.json"))), None)
        if not origen:
            return None
        d = self.dev(nombre)
        sobre = json.loads(open(os.path.join(origen, "manifiesto.json"), "rb").read())
        lineas = ["herramienta_grande.bin"]
        for x in json.loads(base64.b64decode(sobre["manifiesto"]))["archivos"]:
            destino = os.path.join(d, "viejo", *x["ruta"].split("/"))
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            shutil.copyfile(os.path.join(origen, "archivos", x["sha256"]), destino)
            lineas.append(f"viejo/{x['ruta']} -> {x['ruta']}")
        with open(os.path.join(d, "publicacion", "archivos_publicados.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lineas) + "\n")
        pv = cargar(os.path.join(d, "publicacion", "publicar_version.py"), f"pv_{nombre}_viejo")
        self.para_pruebas(pv, os.path.join(d, "viejo", "actualizador.py"))
        return d

    def preparar(self, dev, version, notas="Notas de prueba.", extra=()):
        notas_txt = os.path.join(self.t, f"notas_{version}.txt")
        with open(notas_txt, "w", encoding="utf-8") as f:
            f.write(notas)
        codigo, salida = ejecutar([PY, os.path.join(dev, "publicacion", "publicar_version.py"), "preparar", version,
                                   "--notas", notas_txt, "--clave", self.ruta_clave, "--pruebas", *extra])
        if codigo:
            raise RuntimeError(f"preparar {version}: {salida[-1500:]}")
        return salida

    def publicar(self, dev, version, destino="pub"):
        codigo, salida = ejecutar([PY, os.path.join(dev, "publicacion", "publicar_version.py"), "publicar", version,
                                   "--destino", os.path.join(self.web, destino)])
        if codigo:
            raise RuntimeError(f"publicar {version}: {salida[-1500:]}")
        return salida

    def instalar(self, dev, version):
        """Instala desde el paquete .zip (como cualquiera) y añade datos del usuario y archivos ajenos."""
        self.n_inst += 1
        inst = os.path.join(self.t, f"inst{self.n_inst}")
        with zipfile.ZipFile(os.path.join(dev, "publicacion", "versiones", version, "paquete",
                                          f"Sincronizar_Doblaje_{version}.zip")) as z:
            z.extractall(inst)
        inst = os.path.join(inst, "Sincronizar Doblaje")
        datos = {"Sincronizar_Doblaje_ajustes.json": json.dumps({"plantilla": "Serie {ep}", "formato": "aac"}),
                 ".venv/pyvenv.cfg": "home = falso\n", "ffmpeg/ffmpeg.exe": "no es ffmpeg",
                 "Serie 1x01_[es-ES]_SYNC.mka": "audio del usuario", "Serie 1x01_[es-ES]_SYNC_informe.txt": "informe",
                 "Otra_Herramienta_Gui.py": "# otra herramienta del usuario\n", "mkvmerge.exe": "ajeno",
                 "notas mías.txt": "lo que sea"}
        for r, texto in datos.items():
            os.makedirs(os.path.dirname(os.path.join(inst, r)), exist_ok=True)
            with open(os.path.join(inst, r), "w", encoding="utf-8") as f:
                f.write(texto)
        return inst

    def manifiesto(self, dev, version):
        sobre = open(os.path.join(dev, "publicacion", "versiones", version, "manifiesto.json"), "rb").read()
        return self.act.abrir_sobre(sobre, self.claves)

    def escribir_auxiliares(self):
        """gui_falsa.py (hace de GUI en las pruebas del ayudante) y envoltorio_gui.py (abre la GUI de verdad,
        oculta, y la maneja como lo haría alguien con el ratón)."""
        with open(os.path.join(self.t, "gui_falsa.py"), "w", encoding="utf-8") as f:
            f.write(GUI_FALSA)
        with open(os.path.join(self.t, "envoltorio_gui.py"), "w", encoding="utf-8") as f:
            f.write(ENVOLTORIO)
        with open(os.path.join(self.t, "arranque_falso.py"), "w", encoding="utf-8") as f:
            f.write(ARRANQUE_FALSO)

    def parar(self):
        self.servidor.parar()
        self.servidor_ajeno.parar()


GUI_FALSA = r'''
import json, os, sys, time, importlib
inst, modo = sys.argv[1], sys.argv[2]
sys.path.insert(0, inst)
import actualizador as a
p = a.pendiente_de_salud(inst)
if p and modo == "falla":
    raise SystemExit("la versión nueva revienta al arrancar (prueba)")
if p and modo == "cuelga":
    time.sleep(600)
v = {}
for n in ("sincronizar_doblaje", "limpiar_vhs", "forzados_vo", "actualizador", "actualizador_ventana"):
    v[n] = importlib.import_module(n).VERSION
v["Sincronizar_Doblaje_Gui"] = a._version_de(os.path.join(inst, "Sincronizar_Doblaje_Gui.py"))
if p and modo == "version_mala":
    v["sincronizar_doblaje"] = "0.0.1"
if p:
    a.escribir_salud(inst, p[0], v)
with open(os.path.join(inst, "..", "gui_falsa_arranco.txt"), "a") as f:
    f.write(json.dumps({"pid": os.getpid(), "env": os.environ.get("SINCRONIZAR_ACTUALIZACION"), "v": v}) + "\n")
'''

ARRANQUE_FALSO = r'''
import os, sys
inst = sys.argv[1]
sys.path.insert(0, inst)
import actualizador
r = actualizador.al_arrancar(inst, actualizador._version_de(os.path.join(inst, "Sincronizar_Doblaje_Gui.py")))
print("AL_ARRANCAR", r)
'''

ENVOLTORIO = r'''
import json, os, sys, threading, time, traceback
gui = os.environ["PRUEBA_GUI"]
inst = os.path.dirname(gui)
informe = os.environ["PRUEBA_INFORME"]
import tkinter as tk
_tk, _top = tk.Tk.__init__, tk.Toplevel.__init__
def _tk_oculta(self, *a, **k):
    _tk(self, *a, **k)
    self.withdraw()
def _top_oculta(self, *a, **k):
    _top(self, *a, **k)
    self.withdraw()
tk.Tk.__init__, tk.Toplevel.__init__ = _tk_oculta, _top_oculta
sys.path.insert(0, inst)
import actualizador_ventana as av

def apuntar(**k):
    with open(informe, "a", encoding="utf-8") as f:
        f.write(json.dumps(k, ensure_ascii=False) + "\n")

def esperar(root, cond, luego, limite=60, t0=None):
    t0 = t0 or time.time()
    try:
        ok = cond()
    except Exception as e:
        ok = False
    if ok:
        luego()
    elif time.time() - t0 > limite:
        apuntar(fase="tiempo_agotado", cond=getattr(cond, "__name__", "?"))
        root.destroy()
    else:
        root.after(200, lambda: esperar(root, cond, luego, limite, t0))

def conducir(integ):
    app, root = integ.app, integ.app.master
    modo = os.environ.get("PRUEBA_MODO", "")
    primera = not os.path.exists(informe) or os.environ.get("SINCRONIZAR_ACTUALIZACION") is None
    def estado_barra():
        return {"version": integ.etiqueta.cget("text"), "boton": integ.boton.cget("text") if integ.boton.winfo_manager() else None,
                "estado": integ.estado.cget("text"), "filas": len(app.trabajos), "salida": app.output_folder.get(),
                "titulo": root.title(), "dialogo": bool(integ.dialogo is not None and integ.dialogo.vivo())}
    if not primera:
        # la ventana que ha abierto el ayudante tras actualizar (o tras volver atrás)
        def informar():
            apuntar(fase="reabierta", pid=os.getpid(), **estado_barra())
            root.after(3000, root.destroy)
        # con el resultado a la vista y la comprobación de versión hecha (para ver si la ventana se abre sola)
        esperar(root, lambda: integ.estado.cget("text")[:1] in ("✔", "⚠"),
                lambda: esperar(root, lambda: integ.comprobacion is not None, informar, 30), 60)
        return
    if modo == "sin_conexion":
        integ.comprobar_ya()
        def fin():
            apuntar(fase="sin_conexion", **estado_barra())
            root.destroy()
        esperar(root, lambda: not integ.comprobando, fin, 40)
        return
    carpeta = os.path.dirname(informe)
    archivos = [os.path.join(carpeta, n) for n in ("Serie 1x01.mka", "Serie 1x01.mkv", "Serie 1x02.mka")]
    for a in archivos:
        open(a, "w").close()
    app._add_paths(archivos)
    app.output_folder.set(carpeta)
    integ.comprobar_ya()
    def con_comprobacion():
        apuntar(fase="comprobado", **estado_barra())
        if modo == "al_dia":
            root.destroy()
            return
        integ.abrir_dialogo()
        dlg = integ.dialogo
        # una «tanda» en marcha mientras se descarga: no se puede instalar hasta que termine
        fin_tanda = threading.Event()
        app.worker_thread = threading.Thread(target=fin_tanda.wait, args=(120,), daemon=True)
        app.worker_thread.start()
        dlg._principal()
        def descargado():
            def cuando_libre():
                apuntar(fase="libre", boton=str(dlg.principal.cget("state")), error=dlg.error.get())
                dlg._principal()      # «Instalar y volver a abrir»: la ventana se cierra
            apuntar(fase="descargado", boton=str(dlg.principal.cget("state")), error=dlg.error.get(),
                    fase_texto=dlg.fase.get(), cifras=dlg.cifras.get())
            fin_tanda.set()
            esperar(root, lambda: str(dlg.principal.cget("state")) == "normal", cuando_libre, 30)
        esperar(root, lambda: dlg.estado in ("listo", "error"), lambda: (descargado() if dlg.estado == "listo" else
                (apuntar(fase="error_descarga", error=dlg.error.get()), root.destroy())), 90)
    esperar(root, lambda: integ.comprobacion is not None, con_comprobacion, 40)

_empezar = av.Integracion.empezar
def empezar(self):
    _empezar(self)
    self.app.master.after(600, lambda: conducir(self))
av.Integracion.empezar = empezar
sys.argv = [os.path.abspath(__file__)]
g = {"__name__": "__main__", "__file__": gui}
try:
    exec(compile(open(gui, encoding="utf-8").read(), gui, "exec"), g)
except SystemExit:
    raise
except BaseException:
    apuntar(fase="excepcion", texto=traceback.format_exc()[-1500:])
    raise
'''


# ---------------------------------------------------------------------------------------------------------------
# Utilidades de comprobación

def foto_datos(inst, gestionados):
    """{ruta: sha} de todo lo que NO es del programa (datos del usuario, ajenos)."""
    out = {}
    g = {x.lower() for x in gestionados}
    for base, carpetas, archivos in os.walk(inst):
        carpetas[:] = [c for c in carpetas if c not in (".actualizador", "__pycache__")]
        for a in archivos:
            r = os.path.relpath(os.path.join(base, a), inst).replace("\\", "/")
            if r.lower() not in g:
                out[r] = sha(os.path.join(base, a))
    return out


def como_manifiesto(inst, pub):
    """[] si la instalación tiene exactamente los archivos del manifiesto; si no, lo que difiere."""
    fallos = []
    for a in pub.manifiesto["archivos"]:
        r = os.path.join(inst, *a["ruta"].split("/"))
        if not os.path.isfile(r):
            fallos.append(f"falta {a['ruta']}")
        elif sha(r) != a["sha256"]:
            fallos.append(f"{a['ruta']} no es el de la {pub.version}")
    return fallos


def esperar_ayudante(inst, tiempo=120):
    r = os.path.join(inst, ".actualizador")
    fin = time.time() + tiempo
    time.sleep(0.5)
    while time.time() < fin:
        libre = True
        c = os.path.join(r, "actualizando.lock")
        if os.path.exists(c):
            try:
                fd = os.open(c, os.O_RDWR)
                import msvcrt
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    libre = False
                os.close(fd)
            except OSError:
                pass
        if libre and not os.path.exists(os.path.join(r, "plan.json")):
            return json.load(open(os.path.join(r, "resultado.json"), encoding="utf-8")) if os.path.exists(
                os.path.join(r, "resultado.json")) else {}
        time.sleep(0.3)
    raise RuntimeError("el ayudante no ha terminado a tiempo")


def red_de(ent, act, servidores=None, **kw):
    kw.setdefault("t_conexion", 3)
    kw.setdefault("espera_base", 0.1)
    kw.setdefault("intentos", 3)
    return act.Red(servidores or [ent.url("pub")], ca_extra=ent.ca, **kw)


def preparar_y_lanzar(ent, inst, act, relanzar_modo="bien", servidores=None, plan_mod=None, esperar=True):
    """Lo que hace la GUI al pulsar Actualizar, con una GUI falsa para el arranque: comprobar, descargar y lanzar el
    ayudante; espera a que termine y devuelve (resultado, plan)."""
    red = red_de(ent, act, servidores)
    pub, servidor = act.obtener_publicacion(red, ent.claves)
    version = act._version_de(os.path.join(inst, "Sincronizar_Doblaje_Gui.py"))
    plan = act.calcular_plan(inst, pub, servidor, version)
    carpeta = act.preparar(inst, plan, red, claves=ent.claves)
    if plan_mod:
        plan_mod(plan)
    act.lanzar_ayudante(inst, plan, carpeta, [PY, os.path.join(ent.t, "gui_falsa.py"), inst, relanzar_modo],
                        ventana=False)
    return (esperar_ayudante(inst) if esperar else None), plan


def abrir_sin_compartir(ruta, leer=False):
    """Abre un archivo como un programa que lo tiene bloqueado: nadie más puede cambiarlo ni sustituirlo (con
    leer=True, sí leerlo)."""
    k = ctypes.windll.kernel32
    k.CreateFileW.restype = ctypes.c_void_p
    h = k.CreateFileW(ruta, 0x80000000, 1 if leer else 0, None, 3, 0, None)
    if h == -1 or h == ctypes.c_void_p(-1).value:
        raise OSError("no se pudo abrir sin compartir")
    return h


# ---------------------------------------------------------------------------------------------------------------
# Las pruebas: cada una devuelve una lista de fallos (vacía = bien) y, si quiere, una nota

PRUEBAS = []


def prueba(nombre, que):
    def deco(f):
        PRUEBAS.append((nombre, que, f))
        return f
    return deco


@prueba("firma", "Ed25519: vectores del RFC 8032, cruce con la librería cryptography y firmas alteradas")
def p_firma(ent):
    a = ent.act
    fallos = []
    vectores = [("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60", "",
                 "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25b"
                 "f5f0595bbe24655141438e7a100b"),
                ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb", "72",
                 "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2e"
                 "aeb4302aeeb00d291612bb0c00")]
    for sk, m, firma in vectores:
        sk, m, firma = bytes.fromhex(sk), bytes.fromhex(m), bytes.fromhex(firma)
        if a.ed25519_firmar(sk, m) != firma or not a.ed25519_verificar(a.ed25519_publica(sk), m, firma):
            fallos.append("no cuadra un vector del RFC 8032")
    nota = ""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
        from cryptography.hazmat.primitives import serialization
        for i in range(30):
            k = Ed25519PrivateKey.generate()
            sk = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                 serialization.NoEncryption())
            m = os.urandom(i * 11)
            if a.ed25519_firmar(sk, m) != k.sign(m):
                fallos.append("firma distinta de la de cryptography")
            Ed25519PublicKey.from_public_bytes(a.ed25519_publica(sk)).verify(a.ed25519_firmar(sk, m), m)
        nota = "30 cruces con cryptography"
    except ImportError:
        nota = "sin cryptography en este Python: solo los vectores del RFC"
    pub = a.ed25519_publica(ent.secreta)
    f = a.ed25519_firmar(ent.secreta, b"hola")
    for i in range(64):
        mala = bytearray(f)
        mala[i] ^= 0x10
        if a.ed25519_verificar(pub, b"hola", bytes(mala)):
            fallos.append(f"acepta una firma alterada (byte {i})")
    return fallos, nota


@prueba("normal_gui", "actualización normal con la GUI de verdad (oculta): botón, novedades, progreso, tanda en "
                      "marcha que bloquea, cierre, ayudante, arranque comprobado, lista de episodios conservada")
def p_normal_gui(ent):
    fallos = []
    inst = ent.instalar(ent.dev1, V1)
    pub2 = ent.manifiesto(ent.dev1, V3)
    gestionados = [a["ruta"] for a in pub2.manifiesto["archivos"]] + [
        a["ruta"] for a in ent.manifiesto(ent.dev1, V1).manifiesto["archivos"]]
    antes = foto_datos(inst, gestionados)
    ent.servidor.control.limpiar()
    informe = os.path.join(ent.t, "informe_normal.jsonl")
    env = dict(os.environ, PRUEBA_GUI=os.path.join(inst, "Sincronizar_Doblaje_Gui.py"), PRUEBA_INFORME=informe,
               PRUEBA_MODO="actualizar", SINCRONIZAR_ACTUALIZADOR_SIN_VENTANA="1")
    env.pop("SINCRONIZAR_ACTUALIZACION", None)
    t0 = time.time()
    p = subprocess.Popen([PY, os.path.join(ent.t, "envoltorio_gui.py")], env=env, creationflags=SIN_CONSOLA,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p.wait(180)
    res = esperar_ayudante(inst, 150)
    fin = time.time() + 30
    lineas = []
    while time.time() < fin:
        lineas = [json.loads(x) for x in open(informe, encoding="utf-8")] if os.path.exists(informe) else []
        if any(x["fase"] in ("reabierta", "excepcion", "tiempo_agotado") for x in lineas):
            break
        time.sleep(0.5)
    time.sleep(4)
    fases = {x["fase"]: x for x in lineas}
    c = fases.get("comprobado", {})
    if c.get("boton") != f"Actualizar a la {V3}":
        fallos.append(f"botón: {c.get('boton')!r}")
    if c.get("version") != f"Versión {V1}":
        fallos.append(f"versión mostrada: {c.get('version')!r}")
    if not c.get("dialogo"):
        fallos.append("la ventana de la actualización no se ha abierto sola")
    d = fases.get("descargado", {})
    if d.get("boton") != "disabled" or "tanda en marcha" not in d.get("error", ""):
        fallos.append(f"con la tanda en marcha se podía instalar: {d}")
    if "de" not in d.get("cifras", "") and "descargados" not in d.get("cifras", ""):
        fallos.append(f"sin cifras de progreso: {d.get('cifras')!r}")
    if fases.get("libre", {}).get("boton") != "normal":
        fallos.append("al acabar la tanda no se pudo instalar")
    r = fases.get("reabierta", {})
    if r.get("version") != f"Versión {V3}":
        fallos.append(f"la ventana reabierta no es la {V3}: {r}")
    if "Actualizado" not in r.get("estado", ""):
        fallos.append(f"la ventana reabierta no dice que se ha actualizado: {r.get('estado')!r}")
    if r.get("filas") != 2 or r.get("salida") != ent.t:
        fallos.append(f"no se ha conservado la lista o la carpeta de salida: {r.get('filas')} filas, {r.get('salida')!r}")
    if "excepcion" in fases:
        fallos.append("excepción en la GUI: " + fases["excepcion"]["texto"])
    registro = open(os.path.join(inst, ".actualizador", "registro.txt"), encoding="utf-8").read()
    if f"Actualizado a la {V3} y comprobado que arranca" not in registro or res.get("ok") is False:
        fallos.append(f"el ayudante no confirmó la actualización: {res}")
    if os.path.exists(os.path.join(inst, ".actualizador", "resultado.json")):
        fallos.append("la ventana nueva no ha recogido el resultado")
    fallos += como_manifiesto(inst, pub2)
    if os.path.exists(os.path.join(inst, "probar_sincronizador.py")):
        fallos.append("no se ha retirado probar_sincronizador.py (ya no está en la versión nueva)")
    despues = foto_datos(inst, gestionados)
    antes.pop(AJUSTES_J, None)
    despues.pop(AJUSTES_J, None)
    if despues != antes:
        fallos.append(f"han cambiado datos del usuario o archivos ajenos: {sorted(set(antes.items()) ^ set(despues.items()))}")
    a = json.load(open(os.path.join(inst, AJUSTES_J), encoding="utf-8"))
    if a.get("plantilla") != "Serie {ep}" or a.get("formato") != "aac":
        fallos.append(f"los ajustes del usuario no se han conservado: {a}")
    grande = ent.servidor.control.bytes_de(next(a["sha256"] for a in pub2.manifiesto["archivos"]
                                                if a["ruta"] == "herramienta_grande.bin"))
    if grande:
        fallos.append(f"se ha descargado el archivo grande que no cambiaba ({grande} bytes)")
    if os.path.exists(os.path.join(inst, ".actualizador", "preparacion", V3)):
        fallos.append("queda la carpeta de preparación")
    if not ent.act.respaldos(inst):
        fallos.append("no hay respaldo de la versión anterior")
    return fallos, f"{time.time() - t0:.0f} s; descargados {sum(n for _, _, n in ent.servidor.control.peticiones)} bytes"


@prueba("al_dia_gui", "sin versión nueva: la GUI no enseña el botón")
def p_al_dia(ent):
    inst = ent.instalar(ent.dev1, V3)
    informe = os.path.join(ent.t, "informe_al_dia.jsonl")
    env = dict(os.environ, PRUEBA_GUI=os.path.join(inst, "Sincronizar_Doblaje_Gui.py"), PRUEBA_INFORME=informe,
               PRUEBA_MODO="al_dia", SINCRONIZAR_ACTUALIZADOR_SIN_VENTANA="1")
    env.pop("SINCRONIZAR_ACTUALIZACION", None)
    subprocess.run([PY, os.path.join(ent.t, "envoltorio_gui.py")], env=env, timeout=120, creationflags=SIN_CONSOLA)
    lineas = [json.loads(x) for x in open(informe, encoding="utf-8")] if os.path.exists(informe) else []
    c = next((x for x in lineas if x["fase"] == "comprobado"), None)
    if not c:
        return [f"la GUI no llegó a comprobar: {lineas}"]
    return ([] if c["boton"] is None else [f"enseña el botón: {c['boton']!r}"]) + (
        ["se abre la ventana de la actualización sin haber versión nueva"] if c.get("dialogo") else [])


@prueba("sin_conexion_gui", "servidor caído: la GUI sigue normal, sin botón ni avisos")
def p_sin_conexion(ent):
    dev = ent.dev("dev_caido", servidores=["https://localhost:1/pub/"])
    ent.preparar(dev, V1)
    inst = ent.instalar(dev, V1)
    informe = os.path.join(ent.t, "informe_caido.jsonl")
    env = dict(os.environ, PRUEBA_GUI=os.path.join(inst, "Sincronizar_Doblaje_Gui.py"), PRUEBA_INFORME=informe,
               PRUEBA_MODO="sin_conexion", SINCRONIZAR_ACTUALIZADOR_SIN_VENTANA="1")
    env.pop("SINCRONIZAR_ACTUALIZACION", None)
    subprocess.run([PY, os.path.join(ent.t, "envoltorio_gui.py")], env=env, timeout=120, creationflags=SIN_CONSOLA)
    lineas = [json.loads(x) for x in open(informe, encoding="utf-8")] if os.path.exists(informe) else []
    c = next((x for x in lineas if x["fase"] == "sin_conexion"), None)
    if not c:
        return [f"la GUI no terminó la comprobación: {lineas}"]
    return [] if c["boton"] is None and not c["estado"] else [f"{c}"]


@prueba("danado", "un archivo dañado en la instalación se repara al actualizar y con «reparar»")
def p_danado(ent):
    fallos = []
    inst = ent.instalar(ent.dev1, V1)
    grande = os.path.join(inst, "herramienta_grande.bin")
    with open(grande, "r+b") as f:
        f.seek(1000)
        f.write(b"ROTO")
    act = cargar(os.path.join(inst, "actualizador.py"), f"act_{os.path.basename(os.path.dirname(inst))}")
    red = red_de(ent, act)
    pub, s = act.obtener_publicacion(red, ent.claves)
    plan = act.calcular_plan(inst, pub, s, V1)
    if not any(o["ruta"] == "herramienta_grande.bin" for o in plan.descargas):
        fallos.append("el archivo dañado no está en lo que se descarga")
    res, _ = preparar_y_lanzar(ent, inst, act)
    if not res.get("ok"):
        fallos.append(f"no se actualizó: {res}")
    fallos += como_manifiesto(inst, ent.manifiesto(ent.dev1, V3))
    # «reparar»: misma versión, un archivo tocado
    leeme = os.path.join(inst, "LEEME.txt")
    with open(leeme, "a", encoding="utf-8") as f:
        f.write("cambio\n")
    codigo, salida = ejecutar([PY, os.path.join(inst, "actualizador.py"), "reparar"], cwd=inst)
    if codigo or "LEEME.txt" not in salida:
        fallos.append(f"reparar: {salida[-600:]}")
    fallos += como_manifiesto(inst, ent.manifiesto(ent.dev1, V3))
    return fallos


@prueba("red_lenta", "red lenta (300 KB/s): termina, con progreso real y creciente")
def p_lenta(ent):
    inst = ent.instalar(ent.dev1, V1)
    act = cargar(os.path.join(inst, "actualizador.py"), "act_lenta")
    ent.servidor.control.limpiar()
    pub = ent.manifiesto(ent.dev1, V3)
    motor = next(a for a in pub.manifiesto["archivos"] if a["ruta"] == "sincronizar_doblaje.py")
    ent.servidor.control.reglas[motor["sha256"]] = {"modo": "lento", "bps": 300 * 1024, "veces": 99}
    red = red_de(ent, act)
    plan = act.calcular_plan(inst, *act.obtener_publicacion(red, ent.claves), V1)
    eventos = []
    t0 = time.time()
    act.preparar(inst, plan, red, progreso=lambda **k: eventos.append(dict(k)), claves=ent.claves)
    t = time.time() - t0
    del ent.servidor.control.reglas[motor["sha256"]]
    del_motor = [e["hecho"] for e in eventos if e.get("archivo") == "sincronizar_doblaje.py" and e.get("hecho")]
    fallos = []
    if len(del_motor) < 5 or del_motor != sorted(del_motor) or del_motor[-1] != motor["tamano"]:
        fallos.append(f"progreso del archivo lento raro: {del_motor[:5]}…{del_motor[-2:]}")
    totales = [e["hecho_total"] for e in eventos if "hecho_total" in e]
    if totales != sorted(totales) or totales[-1] != plan.bytes:
        fallos.append("el total no crece hasta lo que hay que descargar")
    if t < motor["tamano"] / (300 * 1024) * 0.7:
        fallos.append(f"ha ido demasiado rápido para estar limitado ({t:.1f} s)")
    return fallos, f"{t:.1f} s para {plan.bytes} bytes; {len(eventos)} avisos de progreso"


@prueba("parada_y_corte", "descarga que se para y conexión cortada: reintento con espera y se aprovecha lo bajado "
                          "(Range) solo si es el mismo archivo")
def p_parada(ent):
    fallos = []
    inst = ent.instalar(ent.dev1, V1)
    act = cargar(os.path.join(inst, "actualizador.py"), "act_parada")
    pub = ent.manifiesto(ent.dev1, V3)
    motor = next(a for a in pub.manifiesto["archivos"] if a["ruta"] == "sincronizar_doblaje.py")
    gui = next(a for a in pub.manifiesto["archivos"] if a["ruta"] == "Sincronizar_Doblaje_Gui.py")
    c = ent.servidor.control
    c.limpiar()
    c.reglas[motor["sha256"]] = {"modo": "parar_tras", "bytes": 100000, "segundos": 20, "veces": 1}
    c.reglas[gui["sha256"]] = {"modo": "cortar_tras", "bytes": 50000, "veces": 1}
    red = red_de(ent, act)
    plan = act.calcular_plan(inst, *act.obtener_publicacion(red, ent.claves), V1)
    reintentos = []
    t0 = time.time()
    act.preparar(inst, plan, red, progreso=lambda **k: reintentos.append(k) if k.get("fase") == "reintento" else None,
                 claves=ent.claves)
    t = time.time() - t0
    rangos = {r: rg for r, rg, _ in c.peticiones if rg}
    if not any(motor["sha256"] in r and rg == "bytes=100000-" for r, rg in rangos.items()):
        fallos.append(f"no se reanudó la descarga parada desde el byte 100000: {rangos}")
    if not any(gui["sha256"] in r and rg == "bytes=50000-" for r, rg in rangos.items()):
        fallos.append(f"no se reanudó la descarga cortada desde el byte 50000: {rangos}")
    if len(reintentos) < 2:
        fallos.append(f"no se avisó de los reintentos: {reintentos}")
    # lo descargado a medias de OTRO archivo (registro con otro SHA-256) no se aprovecha
    carpeta = os.path.join(inst, ".actualizador", "preparacion", "prueba")
    os.makedirs(carpeta, exist_ok=True)
    parcial = os.path.join(carpeta, "x.parcial")
    with open(parcial, "wb") as f:
        f.write(b"basura" * 1000)
    act.escribir_json(parcial + ".json", {"sha256": "0" * 64, "tamano": motor["tamano"]})
    c.limpiar()
    red.descargar(f"{ent.url('pub')}versiones/{V3}/archivos/{motor['sha256']}", parcial, motor["tamano"],
                  motor["sha256"])
    if any(rg for _, rg, _ in c.peticiones):
        fallos.append("aprovechó una descarga a medias de otro archivo")
    if sha(parcial) != motor["sha256"]:
        fallos.append("la descarga no quedó bien")
    return fallos, f"{t:.1f} s con una parada (detectada a los {red.t_conexion} s) y un corte; {len(reintentos)} reintentos"


@prueba("alternativo", "servidor principal caído o con errores: se usa el alternativo, solo si sirve la misma "
                       "publicación firmada")
def p_alternativo(ent):
    fallos = []
    inst = ent.instalar(ent.dev1, V1)
    act = cargar(os.path.join(inst, "actualizador.py"), "act_alt")
    # 1) principal caído (puerto cerrado): todo del alternativo
    red = red_de(ent, act, ["https://localhost:1/pub/", ent.url("alt")])
    pub, s = act.obtener_publicacion(red, ent.claves)
    if s != ent.url("alt"):
        fallos.append(f"no usó el alternativo: {s}")
    act.preparar(inst, act.calcular_plan(inst, pub, s, V1), red, claves=ent.claves)
    shutil.rmtree(os.path.join(inst, ".actualizador", "preparacion"))
    # 2) manifiesto del principal, pero sus archivos dan error 500: se bajan del alternativo
    c = ent.servidor.control
    c.limpiar()
    c.reglas["/pub/versiones/"] = {"modo": "error_500", "veces": 999}
    c.reglas["/pub/versiones/" + V3 + "/manifiesto.json"] = {"modo": None, "veces": 999}
    red = red_de(ent, act, [ent.url("pub"), ent.url("alt")])
    pub, s = act.obtener_publicacion(red, ent.claves)
    act.preparar(inst, act.calcular_plan(inst, pub, s, V1), red, claves=ent.claves)
    if not c.bytes_de("/alt/versiones/"):
        fallos.append("no descargó los archivos del alternativo")
    shutil.rmtree(os.path.join(inst, ".actualizador", "preparacion"))
    # 3) el principal sigue fallando y el alternativo sirve OTRA publicación con el mismo número: no se usa
    red = red_de(ent, act, [ent.url("pub"), ent.url("otra")])
    pub, s = act.obtener_publicacion(red, ent.claves)
    try:
        act.preparar(inst, act.calcular_plan(inst, pub, s, V1), red, claves=ent.claves)
        fallos.append("descargó de un alternativo que sirve otra publicación")
    except act.ErrorActualizacion:
        pass
    if c.bytes_de("/otra/versiones/" + V3 + "/archivos/"):
        fallos.append("bajó archivos del servidor con otra publicación")
    c.limpiar()
    return fallos


@prueba("seguridad_red", "certificado no válido, HTTP sin cifrar y redirección a HTTP: no se descarga nada")
def p_seguridad_red(ent):
    fallos = []
    act = ent.act
    red = act.Red([f"https://localhost:{ent.servidor_ajeno.puerto}/pub/"], ca_extra=ent.ca, t_conexion=3,
                  espera_base=0.1, intentos=2)
    try:
        act.obtener_publicacion(red, ent.claves)
        fallos.append("aceptó un certificado de una CA que no es de confianza")
    except act.SinConexion as e:
        if "certificado" not in e.mensaje:
            fallos.append(f"mensaje poco claro: {e.mensaje}")
    try:
        act.Red(["http://localhost/pub/"])
        fallos.append("aceptó un servidor HTTP")
    except act.ErrorActualizacion:
        pass
    ent.servidor.control.reglas[f"/redir/{act.CANAL}.json"] = {"modo": "redirigir_http", "veces": 99}
    try:
        act.obtener_publicacion(red_de(ent, act, [ent.url("redir")]), ent.claves)
        fallos.append("siguió una redirección a HTTP")
    except act.ErrorActualizacion:
        pass
    ent.servidor.control.limpiar()
    return fallos


@prueba("manifiestos_malos", "firma inválida, clave desconocida, manifiesto incompleto o truncado, hash incorrecto: "
                             "se rechaza y la instalación no cambia")
def p_manifiestos(ent):
    fallos = []
    act = ent.act
    sobre = open(os.path.join(ent.web, "pub", f"{act.CANAL}.json"), "rb").read()
    s = json.loads(sobre)
    datos = base64.b64decode(s["manifiesto"])
    alterado = dict(s, manifiesto=base64.b64encode(datos.replace(b'"notas": "', b'"notas": "X', 1)).decode())
    for nombre, malo, clase in (
            ("firma alterada", json.dumps(alterado).encode(), act.FirmaInvalida),
            ("firmado con otra clave", act.crear_sobre(datos, os.urandom(32), ent.kid), act.FirmaInvalida),
            ("clave desconocida", act.crear_sobre(datos, ent.secreta, "0123456789abcdef"), act.FirmaInvalida),
            ("sin firma", json.dumps({k: v for k, v in s.items() if k != "firma"}).encode(), act.ManifiestoInvalido),
            ("truncado", sobre[:len(sobre) // 2], act.ManifiestoInvalido)):
        try:
            act.abrir_sobre(malo, ent.claves)
            fallos.append(f"{nombre}: aceptado")
        except clase:
            pass
        except Exception as e:
            fallos.append(f"{nombre}: error inesperado {type(e).__name__}: {e}")
    for nombre, cambio in (("sin archivos", lambda x: x.pop("archivos")), ("sin versión", lambda x: x.pop("version")),
                           ("sin la GUI", lambda x: x.update(archivos=[a for a in x["archivos"]
                                                                       if a["ruta"] != "Sincronizar_Doblaje_Gui.py"])),
                           ("sha malo", lambda x: x["archivos"][0].update(sha256="xyz")),
                           ("otro programa", lambda x: x.update(programa="otro"))):
        x = json.loads(datos)
        cambio(x)
        try:
            act.abrir_sobre(act.crear_sobre(json.dumps(x).encode(), ent.secreta, ent.kid), ent.claves)
            fallos.append(f"firmado pero {nombre}: aceptado")
        except act.ManifiestoInvalido:
            pass
    # manifiesto truncado servido por el servidor
    ent.servidor.control.reglas[f"/pub/{act.CANAL}.json"] = {"modo": "truncar_json", "veces": 99}
    try:
        act.obtener_publicacion(red_de(ent, act), ent.claves)
        fallos.append("aceptó un manifiesto truncado del servidor")
    except act.ManifiestoInvalido:
        pass
    ent.servidor.control.limpiar()
    # un archivo alterado en el servidor: no pasa de la preparación y la instalación sigue igual
    inst = ent.instalar(ent.dev1, V1)
    a2 = cargar(os.path.join(inst, "actualizador.py"), "act_malos")
    pub = ent.manifiesto(ent.dev1, V3)
    motor = next(a for a in pub.manifiesto["archivos"] if a["ruta"] == "sincronizar_doblaje.py")
    ent.servidor.control.reglas[motor["sha256"]] = {"modo": "alterar", "veces": 99}
    antes = {r: sha(os.path.join(inst, r)) for r in ("sincronizar_doblaje.py", "Sincronizar_Doblaje_Gui.py")}
    try:
        red = red_de(ent, a2)
        a2.preparar(inst, a2.calcular_plan(inst, *a2.obtener_publicacion(red, ent.claves), V1), red,
                    claves=ent.claves)
        fallos.append("aceptó un archivo con otro SHA-256")
    except a2.ManifiestoInvalido:
        pass
    ent.servidor.control.limpiar()
    if {r: sha(os.path.join(inst, r)) for r in antes} != antes:
        fallos.append("la instalación ha cambiado")
    if [n for n in os.listdir(os.path.join(inst, ".actualizador", "preparacion", V3)) if n.startswith(motor["sha256"])]:
        fallos.append("queda el archivo alterado en la preparación")
    return fallos


@prueba("rutas_maliciosas", "rutas absolutas, «..», unidades, flujos NTFS, nombres reservados, datos del usuario y "
                            "puntos de unión que salen de la carpeta: rechazados, nada escrito fuera")
def p_rutas(ent):
    act = ent.act
    fallos = []
    malas = ["../fuera.py", "a/../../fuera.py", "/abs.py", "C:/Windows/x.py", "C:x.py", "\\\\srv\\c\\x.py",
             "a\\b.py", "x.py:flujo", "CON", "nul.txt", "com1.py", "a/./b.py", "a//b.py", "carpeta/", "fin.",
             " espacio.py", "SINCRO~1.PY", ".actualizador/diario.json", ".venv/Scripts/python.exe",
             "ffmpeg/ffmpeg.exe", "Sincronizar_Doblaje_ajustes.json", "__pycache__/x.pyc", "", "a" * 300]
    for r in malas:
        try:
            act.validar_ruta(r)
            fallos.append(f"acepta {r!r}")
        except act.ManifiestoInvalido:
            pass
    m = json.loads(base64.b64decode(json.loads(open(os.path.join(ent.web, "pub", f"{act.CANAL}.json"), "rb").read())
                                    ["manifiesto"]))
    m["archivos"].append({"ruta": "../../fuera.py", "tamano": 1, "sha256": "0" * 64})
    try:
        act.abrir_sobre(act.crear_sobre(json.dumps(m).encode(), ent.secreta, ent.kid), ent.claves)
        fallos.append("acepta un manifiesto firmado con «../../fuera.py»")
    except act.ManifiestoInvalido:
        pass
    inst = ent.instalar(ent.dev1, V1)
    fuera = os.path.join(ent.t, "fuera_de_la_instalacion")
    os.makedirs(fuera)
    codigo, salida = ejecutar(["cmd", "/c", "mklink", "/J", os.path.join(inst, "extra"), fuera])
    if codigo:
        return fallos + [f"no se pudo crear el punto de unión: {salida}"]
    try:
        act.destino_seguro(inst, "extra/nuevo.txt")
        fallos.append("deja escribir a través de un punto de unión")
    except act.SinPermisos:
        pass
    nota = "punto de unión (mklink /J) probado"
    try:
        os.symlink(fuera, os.path.join(inst, "enlace"), target_is_directory=True)
        try:
            act.destino_seguro(inst, "enlace/x.txt")
            fallos.append("deja escribir a través de un enlace simbólico")
        except act.SinPermisos:
            pass
        nota += "; enlace simbólico probado"
    except OSError:
        nota += "; enlace simbólico NO probado (Windows no deja crearlos sin permisos de administrador)"
    if os.listdir(fuera):
        fallos.append("se ha escrito fuera de la instalación")
    os.rmdir(os.path.join(inst, "extra"))
    return fallos, nota


@prueba("permisos", "archivo de solo lectura y carpeta sin permiso de escritura: error claro antes de tocar nada")
def p_permisos(ent):
    fallos = []
    inst = ent.instalar(ent.dev1, V1)
    act = cargar(os.path.join(inst, "actualizador.py"), "act_permisos")
    red = red_de(ent, act)
    plan = act.calcular_plan(inst, *act.obtener_publicacion(red, ent.claves), V1)
    carpeta = act.preparar(inst, plan, red, claves=ent.claves)
    datos = {"id": "prueba-ro", "desde": V1, "hacia": V3, "manifiesto": plan.pub.manifiesto,
             "operaciones": plan.operaciones, "preparacion": carpeta}
    motor = os.path.join(inst, "sincronizar_doblaje.py")
    os.chmod(motor, 0o444)
    antes = sha(motor)
    try:
        act.aplicar(inst, datos)
        fallos.append("sustituyó un archivo de solo lectura")
    except act.SinPermisos:
        pass
    finally:
        os.chmod(motor, 0o666)
    if sha(motor) != antes or os.path.exists(os.path.join(inst, ".actualizador", "diario.json")):
        fallos.append("tocó algo con el archivo de solo lectura")
    usuario = os.environ.get("USERNAME")
    extra = os.path.join(inst, "carpeta_cerrada")
    os.makedirs(extra)
    shutil.copyfile(motor, os.path.join(extra, "x.py"))
    codigo, salida = ejecutar(["icacls", extra, "/deny", f"{usuario}:(OI)(CI)(WD,AD)"])
    try:
        if codigo:
            fallos.append(f"icacls: {salida}")
        else:
            try:
                act.comprobar_escritura(inst, [{"ruta": "carpeta_cerrada/x.py", "tipo": "sustituir"}])
                fallos.append("no detecta una carpeta sin permiso de escritura")
            except act.SinPermisos as e:
                if "permisos" not in e.mensaje:
                    fallos.append(f"mensaje poco claro: {e.mensaje}")
    finally:
        ejecutar(["icacls", extra, "/remove:d", usuario])
    return fallos


@prueba("bloqueado", "archivo abierto por otro programa: no se puede sustituir, se deshace todo y se dice cuál")
def p_bloqueado(ent):
    fallos = []
    inst = ent.instalar(ent.dev1, V1)
    act = cargar(os.path.join(inst, "actualizador.py"), "act_bloq")
    pub1 = ent.manifiesto(ent.dev1, V1)
    antes = {a["ruta"]: sha(os.path.join(inst, *a["ruta"].split("/"))) for a in pub1.manifiesto["archivos"]}
    asas = []
    try:
        res, _ = preparar_y_lanzar(ent, inst, act, plan_mod=lambda plan: asas.append(
            abrir_sin_compartir(os.path.join(inst, "limpiar_vhs.py"), leer=True)))
    finally:
        for h in asas:
            ctypes.windll.kernel32.CloseHandle(h)
    if res.get("ok") or "limpiar_vhs.py" not in res.get("mensaje", ""):
        fallos.append(f"resultado: {res}")
    fallos += como_manifiesto(inst, pub1)
    if {r: sha(os.path.join(inst, *r.split("/"))) for r in antes} != antes:
        fallos.append("la instalación no ha quedado como antes")
    return fallos


@prueba("dos_a_la_vez", "dos actualizaciones a la vez: la segunda no hace nada")
def p_dos(ent):
    fallos = []
    inst = ent.instalar(ent.dev1, V1)
    act = cargar(os.path.join(inst, "actualizador.py"), "act_dos")
    c = act.Cerrojo(act.Rutas(inst).cerrojo)
    c.tomar()
    try:
        red = red_de(ent, act)
        pub, s = act.obtener_publicacion(red, ent.claves)
        plan = act.calcular_plan(inst, pub, s, V1)
        carpeta = act.preparar(inst, plan, red, claves=ent.claves)
        act.lanzar_ayudante(inst, plan, carpeta, [PY, os.path.join(ent.t, "gui_falsa.py"), inst, "bien"], ventana=False)
        time.sleep(14)
        if como_manifiesto(inst, ent.manifiesto(ent.dev1, V1)):
            fallos.append("se ha actualizado con otra actualización en marcha")
    finally:
        c.soltar()
    # dos ayudantes lanzados a la vez sobre el mismo plan: uno actualiza, el otro no hace nada
    for _ in range(2):
        act._lanzar([act.python_ventana(), "-I", act.copiar_ayudante(inst), "ayudante", "--instalacion", inst,
                     "--sin-ventana"], cwd=inst)
    try:
        res = esperar_ayudante(inst)
    except RuntimeError as e:
        return fallos + [str(e)]
    if os.path.exists(act.Rutas(inst).plan):
        fallos.append("queda el plan")
    registro = open(act.Rutas(inst).registro, encoding="utf-8").read()
    if "otra actualización en marcha" not in registro and "No hay ninguna actualización preparada" not in registro:
        fallos.append("el segundo ayudante no dijo nada")
    return fallos, f"resultado: {res.get('mensaje', '')}"


@prueba("trabajo_activo", "un análisis en marcha (aquí o en otro equipo con la carpeta compartida): no se actualiza")
def p_trabajo(ent):
    fallos = []
    inst = ent.instalar(ent.dev1, V1)
    act = cargar(os.path.join(inst, "actualizador.py"), "act_trab")
    motor = subprocess.Popen([PY, "-c", "import sys, time; sys.path.insert(0, sys.argv[1]); import actualizador as a; "
                                        "i = a.registrar_instancia(sys.argv[1], 'motor', True); time.sleep(60)", inst],
                             creationflags=SIN_CONSOLA)
    try:
        time.sleep(1.5)
        if not act.ocupadas(inst):
            fallos.append("no se ve el análisis en marcha")
        res, _ = preparar_y_lanzar(ent, inst, act)
        if res.get("ok") or "análisis" not in res.get("mensaje", ""):
            fallos.append(f"resultado: {res}")
        fallos += como_manifiesto(inst, ent.manifiesto(ent.dev1, V1))
    finally:
        motor.kill()
    return fallos


@prueba("fallo_al_sustituir", "fallo a mitad de la sustitución: se restaura la versión anterior")
def p_fallo(ent):
    fallos = []
    inst = ent.instalar(ent.dev1, V1)
    act = cargar(os.path.join(inst, "actualizador.py"), "act_fallo")
    red = red_de(ent, act)
    plan = act.calcular_plan(inst, *act.obtener_publicacion(red, ent.claves), V1)
    carpeta = act.preparar(inst, plan, red, claves=ent.claves)
    datos = {"id": "prueba-fallo", "desde": V1, "hacia": V3, "manifiesto": plan.pub.manifiesto,
             "operaciones": plan.operaciones, "preparacion": carpeta}

    def fallo(i, o):
        if i == 3:
            raise OSError("fallo provocado")
    try:
        act.aplicar(inst, datos, fallo=fallo)
        fallos.append("no falló")
    except OSError:
        pass
    fallos += como_manifiesto(inst, ent.manifiesto(ent.dev1, V1))
    if os.path.exists(act.Rutas(inst).diario):
        fallos.append("queda el diario abierto")
    return fallos, f"{len(plan.operaciones)} cambios, fallo en el 4.º"


@prueba("interrupcion", "corte de luz a mitad (proceso muerto sin limpiar): al volver a abrir se deshace solo")
def p_interrupcion(ent):
    fallos = []
    for momento in ("aplicando", "respaldando"):
        inst = ent.instalar(ent.dev1, V1)
        act = cargar(os.path.join(inst, "actualizador.py"), f"act_int_{momento}")
        red = red_de(ent, act)
        plan = act.calcular_plan(inst, *act.obtener_publicacion(red, ent.claves), V1)
        carpeta = act.preparar(inst, plan, red, claves=ent.claves)
        datos = {"id": "prueba-corte", "desde": V1, "hacia": V3, "manifiesto": plan.pub.manifiesto,
                 "operaciones": plan.operaciones, "preparacion": carpeta}
        ruta_plan = os.path.join(ent.t, f"plan_corte_{momento}.json")
        act.escribir_json(ruta_plan, datos)
        guion = ("import sys, json, os; sys.path.insert(0, sys.argv[1]); import actualizador as a\n"
                 "d = json.load(open(sys.argv[2], encoding='utf-8'))\n"
                 + ("a.aplicar(sys.argv[1], d, fallo=lambda i, o: os._exit(9) if i == 2 else None)\n"
                    if momento == "aplicando" else
                    "orig = a._copiar_comprobado\n"
                    "n = [0]\n"
                    "def corte(*x):\n"
                    "    n[0] += 1\n"
                    "    if n[0] == 2: os._exit(9)\n"
                    "    return orig(*x)\n"
                    "a._copiar_comprobado = corte\n"
                    "a.aplicar(sys.argv[1], d)\n"))
        codigo, salida = ejecutar([PY, "-c", guion, inst, ruta_plan])
        d = act.leer_json(act.Rutas(inst).diario) or {}
        if codigo != 9 or d.get("estado") != momento:
            fallos.append(f"{momento}: el corte no dejó el diario a medias ({codigo}, {d.get('estado')}) {salida[-300:]}")
            continue
        if momento == "aplicando" and not como_manifiesto(inst, ent.manifiesto(ent.dev1, V1)):
            fallos.append("aplicando: el corte no dejó la instalación mezclada (la prueba no prueba nada)")
        codigo, salida = ejecutar([PY, os.path.join(ent.t, "arranque_falso.py"), inst])
        time.sleep(1)
        fallos += [f"{momento}: {x}" for x in como_manifiesto(inst, ent.manifiesto(ent.dev1, V1))]
        res = act.leer_json(act.Rutas(inst).resultado) or {}
        if "interrumpió" not in res.get("mensaje", ""):
            fallos.append(f"{momento}: no se dice que se interrumpió: {res}")
        if os.path.exists(act.Rutas(inst).diario):
            fallos.append(f"{momento}: queda el diario")
    return fallos


@prueba("arranque_fallido", "la versión nueva no arranca (revienta, se cuelga, carga otra versión de un componente, "
                            "el motor falla al importar): se vuelve a la anterior y se abre esa")
def p_arranque(ent):
    fallos = []
    notas = []
    for modo in ("falla", "cuelga", "version_mala"):
        inst = ent.instalar(ent.dev1, V1)
        act = cargar(os.path.join(inst, "actualizador.py"), f"act_arr_{modo}")
        t0 = time.time()
        res, _ = preparar_y_lanzar(ent, inst, act, relanzar_modo=modo)
        notas.append(f"{modo} {time.time() - t0:.0f} s")
        if res.get("ok"):
            fallos.append(f"{modo}: se dio por buena")
        fallos += [f"{modo}: {x}" for x in como_manifiesto(inst, ent.manifiesto(ent.dev1, V1))]
        marca = os.path.join(os.path.dirname(inst), "gui_falsa_arranco.txt")
        arranques = open(marca).read() if os.path.exists(marca) else ""
        if modo == "version_mala" and "0.0.1" not in arranques:
            fallos.append("version_mala: la GUI falsa no llegó a arrancar")
    # motor que revienta al importarlo (sintaxis buena): ni se llega a abrir la ventana nueva
    inst = ent.instalar(ent.dev1, V1)
    act = cargar(os.path.join(inst, "actualizador.py"), "act_motor_roto")
    red = red_de(ent, act, [ent.url("mala_motor")])
    pub, s = act.obtener_publicacion(red, ent.claves)
    plan = act.calcular_plan(inst, pub, s, V1)
    carpeta = act.preparar(inst, plan, red, claves=ent.claves)
    act.lanzar_ayudante(inst, plan, carpeta, [PY, os.path.join(ent.t, "gui_falsa.py"), inst, "bien"], ventana=False)
    res = esperar_ayudante(inst)
    if res.get("ok") or "no carga" not in res.get("mensaje", ""):
        fallos.append(f"motor roto: {res}")
    fallos += [f"motor roto: {x}" for x in como_manifiesto(inst, ent.manifiesto(ent.dev1, V1))]
    return fallos, "; ".join(notas)


@prueba("arranque_fallido_gui", "la GUI nueva revienta al abrir (con la GUI de verdad): vuelta a la anterior, que "
                                "se abre y dice por qué")
def p_arranque_gui(ent):
    fallos = []
    inst = ent.instalar(ent.dev1, V1)
    pv = cargar(os.path.join(ent.dev1, "publicacion", "publicar_version.py"), "pv_mala")
    pv.fijar_constante(os.path.join(inst, "actualizador.py"), "SERVIDORES", repr([ent.url("mala_gui")]))
    pub1 = ent.manifiesto(ent.dev1, V1)
    antes = {a["ruta"]: sha(os.path.join(inst, *a["ruta"].split("/"))) for a in pub1.manifiesto["archivos"]}
    informe = os.path.join(ent.t, "informe_gui_rota.jsonl")
    env = dict(os.environ, PRUEBA_GUI=os.path.join(inst, "Sincronizar_Doblaje_Gui.py"), PRUEBA_INFORME=informe,
               PRUEBA_MODO="actualizar", SINCRONIZAR_ACTUALIZADOR_SIN_VENTANA="1")
    env.pop("SINCRONIZAR_ACTUALIZACION", None)
    subprocess.Popen([PY, os.path.join(ent.t, "envoltorio_gui.py")], env=env, creationflags=SIN_CONSOLA).wait(180)
    res = esperar_ayudante(inst, 150)
    fin = time.time() + 40
    lineas = []
    while time.time() < fin:
        lineas = [json.loads(x) for x in open(informe, encoding="utf-8")] if os.path.exists(informe) else []
        if any(x["fase"] == "reabierta" for x in lineas):
            break
        time.sleep(0.5)
    time.sleep(4)
    r = next((x for x in lineas if x["fase"] == "reabierta"), {})
    if res.get("ok"):
        fallos.append("se dio por buena")
    if r.get("version") != f"Versión {V1}" or "No se ha podido actualizar" not in r.get("estado", ""):
        fallos.append(f"la ventana que se abre después: {r}")
    if r.get("filas") != 2:
        fallos.append(f"no se conservó la lista al volver: {r.get('filas')}")
    if r.get("dialogo"):
        fallos.append("tras fallar, la ventana de la actualización se vuelve a abrir sola")
    if r.get("boton") != f"Actualizar a la {V_GUI_ROTA}":
        fallos.append(f"tras fallar no queda el botón para reintentarlo: {r.get('boton')!r}")
    if "revienta" not in res.get("detalle", "") and "RuntimeError" not in res.get("detalle", ""):
        fallos.append(f"el detalle no trae el error de la versión nueva: {res.get('detalle', '')[-300:]!r}")
    despues = {r: sha(os.path.join(inst, *r.split("/"))) if os.path.exists(os.path.join(inst, *r.split("/")))
               else None for r in antes}
    if despues != antes:
        fallos.append(f"no ha quedado como antes: {[r for r in antes if antes[r] != despues[r]]}")
    return fallos


@prueba("versiones", "desde la más antigua (salta versiones), «actualizable_desde», sin bajadas involuntarias y "
                     "bajada explícita con restaurar-version")
def p_versiones(ent):
    fallos = []
    inst = ent.instalar(ent.dev1, V1)
    act = cargar(os.path.join(inst, "actualizador.py"), "act_vers")
    red = red_de(ent, act)
    c = act.comprobar(inst, V1, red, ent.claves)
    if c.estado != "disponible" or c.pub.version != V3:
        fallos.append(f"desde la {V1}: {c.estado} {c.pub.version}")
    m = dict(c.pub.manifiesto, actualizable_desde=V2)
    if act.compatible(m, V1) is None:
        fallos.append("no respeta «actualizable_desde»")
    if act.compatible(dict(m, requisitos={"python_min": "9.0"}), V2) is None:
        fallos.append("no respeta «python_min»")
    c = act.comprobar(inst, "9.9", red, ent.claves)
    if c.estado != "al_dia":
        fallos.append("ofrece bajar de versión")
    # las de fecha (las de antes de la 1.0) son anteriores a todas las numeradas, aunque su número sea mayor
    c = act.comprobar(inst, V_PUENTE, red, ent.claves)
    if c.estado != "disponible" or c.pub.version != V3:
        fallos.append(f"desde la {V_PUENTE} (por fecha) no ofrece la {V3}: {c.estado}")
    orden = sorted(["1.10", "2026.10.26", "1.2", "1.1.1", "2.0", "2026.10.25.2", "1.0"], key=act.orden_version)
    if orden != ["2026.10.25.2", "2026.10.26", "1.0", "1.1.1", "1.2", "1.10", "2.0"]:
        fallos.append(f"orden de versiones: {orden}")
    res, _ = preparar_y_lanzar(ent, inst, act)
    if not res.get("ok"):
        fallos.append(f"{V1} -> {V3}: {res}")
    fallos += como_manifiesto(inst, ent.manifiesto(ent.dev1, V3))
    codigo, salida = ejecutar([PY, os.path.join(inst, "actualizador.py"), "restaurar-version", V1], cwd=inst)
    if codigo == 0:
        fallos.append("restaurar-version sin --confirmo ha hecho algo")
    codigo, salida = ejecutar([PY, os.path.join(inst, "actualizador.py"), "restaurar-version", V1, "--confirmo"],
                              cwd=inst)
    if codigo:
        fallos.append(f"restaurar-version: {salida[-500:]}")
    fallos += [f"tras restaurar: {x}" for x in como_manifiesto(inst, ent.manifiesto(ent.dev1, V1))]
    codigo, salida = ejecutar([PY, os.path.join(inst, "actualizador.py"), "deshacer"], cwd=inst)
    if codigo:
        fallos.append(f"deshacer: {salida[-500:]}")
    fallos += [f"tras deshacer: {x}" for x in como_manifiesto(inst, ent.manifiesto(ent.dev1, V3))]
    return fallos


@prueba("ajustes", "migración de ajustes respaldada y deshecha si la versión nueva no arranca; datos intactos")
def p_ajustes(ent):
    fallos = []
    inst = ent.instalar(ent.dev1, V1)
    act = cargar(os.path.join(inst, "actualizador.py"), "act_ajustes")
    ajustes = os.path.join(inst, "Sincronizar_Doblaje_ajustes.json")
    original = open(ajustes, "rb").read()
    red = red_de(ent, act)
    plan = act.calcular_plan(inst, *act.obtener_publicacion(red, ent.claves), V1)
    carpeta = act.preparar(inst, plan, red, claves=ent.claves)
    datos = {"id": "prueba-ajustes", "desde": V1, "hacia": V3, "manifiesto": plan.pub.manifiesto,
             "operaciones": plan.operaciones, "preparacion": carpeta}
    act.aplicar(inst, datos)
    r = act.migrar_ajustes(inst, ajustes, migraciones={2: lambda a: dict(a, formato_nuevo=a.pop("formato"))}, esquema=2)
    if r != 2 or "formato_nuevo" not in json.load(open(ajustes, encoding="utf-8")):
        fallos.append("no migró")
    act.revertir(inst)
    if open(ajustes, "rb").read() != original:
        fallos.append("al deshacer, los ajustes no vuelven a ser los de antes")
    if act.leer_estado(inst).get("esquema_ajustes", 1) != 1:
        fallos.append("al deshacer, el esquema de los ajustes no vuelve al de antes")
    fallos += como_manifiesto(inst, ent.manifiesto(ent.dev1, V1))
    return fallos


@prueba("registro", "el registro no guarda contraseñas ni tokens")
def p_registro(ent):
    act = ent.act
    t = act.sin_secretos("https://usuario:secreta@servidor/x?token=abc123&b=2 Authorization: Bearer xyz token=qwe")
    return [] if all(x not in t for x in ("secreta", "abc123", "xyz", "qwe")) else [t]


@prueba("publicacion", "publicar: lista explícita, sin datos ni ajustes, versiones inmutables, activación atómica "
                       "con la anterior guardada, verificación por HTTPS y restaurar")
def p_publicacion(ent):
    fallos = []
    dev = ent.dev("dev_publi")
    pv = os.path.join(dev, "publicacion", "publicar_version.py")
    lista = os.path.join(dev, "publicacion", "archivos_publicados.txt")
    texto = open(lista, encoding="utf-8").read()
    with open(os.path.join(dev, "Sincronizar_Doblaje_ajustes.json"), "w") as f:
        f.write("{}")
    for malo in ("Sincronizar_Doblaje_ajustes.json", ".venv/pyvenv.cfg", "x_informe.txt", "clave_firma.txt"):
        with open(lista, "w", encoding="utf-8") as f:
            f.write(texto + malo + "\n")
        try:
            ent.preparar(dev, V1)
            fallos.append(f"publica {malo}")
        except RuntimeError:
            pass
    with open(lista, "w", encoding="utf-8") as f:
        f.write(texto)
    # números: 1.0, 1.1... (1.1.1 para un arreglo); por fecha, solo pidiéndolo
    for malo in ("2026.11.02", "0.9", "01.0", "1", "1.0.0.1", "1.0a", "v1.1"):
        try:
            ent.preparar(dev, malo)
            fallos.append(f"prepara la versión «{malo}»")
        except RuntimeError:
            pass
    ent.preparar(dev, V1)
    zip_ = os.path.join(dev, "publicacion", "versiones", V1, "paquete", f"Sincronizar_Doblaje_{V1}.zip")
    nombres = zipfile.ZipFile(zip_).namelist()
    if any("ajustes" in n or ".venv" in n or "publicar_version" in n for n in nombres):
        fallos.append(f"el paquete lleva lo que no debe: {nombres}")
    try:
        ent.preparar(dev, V1)
        fallos.append("deja preparar dos veces la misma versión")
    except RuntimeError:
        pass
    destino = os.path.join(ent.web, "publi")
    ent.publicar(dev, V1, "publi")
    act = ent.act
    puntero = os.path.join(destino, f"{act.CANAL}.json")
    if act.abrir_sobre(open(puntero, "rb").read(), ent.claves).version != V1:
        fallos.append("no se anuncia la versión")
    if os.path.exists(os.path.join(destino, f"{act.CANAL_FECHAS}.json")):
        fallos.append(f"una numerada se anuncia también en {act.CANAL_FECHAS}.json (el de las copias de antes)")
    ent.preparar(dev, V2)
    codigo, salida = ejecutar([PY, pv, "subir", V2, "--destino", destino])
    if act.abrir_sobre(open(puntero, "rb").read(), ent.claves).version != V1:
        fallos.append("subir ya la anuncia (tienen que ser pasos separados)")
    codigo, salida = ejecutar([PY, pv, "verificar", "--url", ent.url("publi"), "--version", V2, "--ca", ent.ca])
    if codigo:
        fallos.append(f"verificar por HTTPS: {salida[-400:]}")
    codigo, salida = ejecutar([PY, pv, "activar", V2, "--destino", destino])
    if codigo or not os.listdir(os.path.join(destino, "historial")):
        fallos.append(f"activar: {salida[-300:]}")
    # alguien cambia un archivo publicado: verificar lo ve
    archivo = os.path.join(destino, "versiones", V2, "archivos", os.listdir(os.path.join(destino, "versiones", V2,
                                                                                         "archivos"))[0])
    with open(archivo, "ab") as f:
        f.write(b"x")
    codigo, salida = ejecutar([PY, pv, "verificar", "--url", ent.url("publi"), "--ca", ent.ca])
    if codigo == 0:
        fallos.append("verificar no ve un archivo publicado alterado")
    codigo, salida = ejecutar([PY, pv, "subir", V2, "--destino", destino])
    if codigo == 0:
        fallos.append("subir pisa una versión publicada distinta")
    codigo, salida = ejecutar([PY, pv, "restaurar", "--destino", destino])
    if codigo or act.abrir_sobre(open(puntero, "rb").read(), ent.claves).version != V1:
        fallos.append(f"restaurar: {salida[-300:]}")
    return fallos


def gui_actualizar(ent, inst, nombre):
    """Abre la GUI de verdad de la instalación (oculta) y actualiza con ella como en «normal_gui»; espera a que la
    ventana que abre el ayudante diga cómo ha ido. -> ({fase: datos}, resultado del ayudante)"""
    informe = os.path.join(ent.t, nombre)
    env = dict(os.environ, PRUEBA_GUI=os.path.join(inst, "Sincronizar_Doblaje_Gui.py"), PRUEBA_INFORME=informe,
               PRUEBA_MODO="actualizar", SINCRONIZAR_ACTUALIZADOR_SIN_VENTANA="1")
    env.pop("SINCRONIZAR_ACTUALIZACION", None)
    subprocess.Popen([PY, os.path.join(ent.t, "envoltorio_gui.py")], env=env, creationflags=SIN_CONSOLA,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).wait(180)
    res = esperar_ayudante(inst, 150)
    fin = time.time() + 40
    lineas = []
    while time.time() < fin:
        lineas = [json.loads(x) for x in open(informe, encoding="utf-8")] if os.path.exists(informe) else []
        if any(x["fase"] in ("reabierta", "excepcion", "tiempo_agotado", "error_descarga") for x in lineas):
            break
        time.sleep(0.5)
    time.sleep(4)
    return {x["fase"]: x for x in lineas}, res


@prueba("de_fechas_a_numeradas", "una copia con la 2026.10.25 tal como se publicó pasa sola a la numeración nueva, "
                                 "con la GUI de verdad: «Actualizar a la 2026.10.26» (el paso) y luego «Actualizar "
                                 "a la 1.2»; datos intactos")
def p_de_fechas(ent):
    if not ent.dev_vieja:
        return [], f"no se puede probar: la {V_VIEJA} no está en las versiones preparadas ni en las del repositorio"
    fallos = []
    act = ent.act
    web = os.path.join(ent.web, "pub")
    pub_vieja = ent.manifiesto(ent.dev_vieja, V_VIEJA)
    pub_puente = ent.manifiesto(ent.dev_puente, V_PUENTE)
    pub3 = ent.manifiesto(ent.dev1, V3)
    # lo publicado: el paso, en el puntero que leen las copias de antes; la numerada, en el suyo y en el enlace fijo
    for canal, v in ((act.CANAL_FECHAS, V_PUENTE), (act.CANAL, V3)):
        hay = act.abrir_sobre(open(os.path.join(web, f"{canal}.json"), "rb").read(), ent.claves).version
        if hay != v:
            fallos.append(f"{canal}.json anuncia la {hay}, no la {v}")
    if sha(os.path.join(web, "Sincronizar_Doblaje.zip")) != sha(os.path.join(
            ent.dev1, "publicacion", "versiones", V3, "paquete", f"Sincronizar_Doblaje_{V3}.zip")):
        fallos.append("el enlace fijo al paquete no es el de la numerada")
    inst = ent.instalar(ent.dev_vieja, V_VIEJA)
    if act._version_de(os.path.join(inst, "actualizador.py")) != V_VIEJA or "CANAL_FECHAS" in open(
            os.path.join(inst, "actualizador.py"), encoding="utf-8").read():
        return fallos + ["la instalación de partida no tiene el código de la 2026.10.25"]
    gestionados = [a["ruta"] for p in (pub_vieja, pub_puente, pub3) for a in p.manifiesto["archivos"]]
    antes = foto_datos(inst, gestionados)
    t0 = time.time()
    # 1) con la GUI y el actualizador de la 2026.10.25
    f1, res1 = gui_actualizar(ent, inst, "informe_fechas_1.jsonl")
    c, r = f1.get("comprobado", {}), f1.get("reabierta", {})
    if c.get("version") != f"Versión {V_VIEJA}" or c.get("boton") != f"Actualizar a la {V_PUENTE}":
        fallos.append(f"la {V_VIEJA} no ofrece el paso: {c}")
    if r.get("version") != f"Versión {V_PUENTE}" or "Actualizado" not in r.get("estado", "") or res1.get("ok") is False:
        fallos.append(f"el paso no se instaló bien: {r} {res1}")
    fallos += [f"tras el paso: {x}" for x in como_manifiesto(inst, pub_puente)]
    for fase in ("excepcion", "tiempo_agotado", "error_descarga"):
        if fase in f1:
            fallos.append(f"paso: {fase} {f1[fase]}")
    # 2) con la GUI del paso, que ya lee el puntero de las numeradas
    f2, res2 = gui_actualizar(ent, inst, "informe_fechas_2.jsonl")
    c, r = f2.get("comprobado", {}), f2.get("reabierta", {})
    if c.get("version") != f"Versión {V_PUENTE}" or c.get("boton") != f"Actualizar a la {V3}":
        fallos.append(f"el paso no ofrece la {V3}: {c}")
    if r.get("version") != f"Versión {V3}" or "Actualizado" not in r.get("estado", "") or res2.get("ok") is False:
        fallos.append(f"la {V3} no se instaló bien: {r} {res2}")
    if V3 not in r.get("titulo", ""):
        fallos.append(f"el título no dice la versión: {r.get('titulo')!r}")
    fallos += como_manifiesto(inst, pub3)
    for fase in ("excepcion", "tiempo_agotado", "error_descarga"):
        if fase in f2:
            fallos.append(f"numerada: {fase} {f2[fase]}")
    despues = foto_datos(inst, gestionados)
    antes.pop(AJUSTES_J, None)
    despues.pop(AJUSTES_J, None)
    if despues != antes:
        fallos.append(f"han cambiado datos del usuario o archivos ajenos: {sorted(set(antes.items()) ^ set(despues.items()))}")
    # 3) ya numerada, no vuelve a ofrecer nada (ni el paso)
    a = cargar(os.path.join(inst, "actualizador.py"), "act_fechas_fin")
    c = a.comprobar(inst, V3, red_de(ent, a), ent.claves)
    if c.estado != "al_dia":
        fallos.append(f"la {V3} ofrece la {c.pub.version}")
    return fallos, f"{time.time() - t0:.0f} s las dos actualizaciones"


@prueba("github", "orden «github» con un repositorio git local: sube, anuncia, pone la etiqueta vX.Y en el commit que "
                  "la anuncia; repetirla no duplica nada; la Release, sin GitHub de verdad, falla con un mensaje claro")
def p_github(ent):
    fallos = []
    remoto = os.path.join(ent.t, "remoto.git")
    sitio = os.path.join(ent.web, "gh")

    def git(*orden, carpeta=sitio):
        codigo, salida = ejecutar(["git", "-C", carpeta, *orden])
        if codigo:
            raise RuntimeError(f"git {' '.join(orden)}: {salida[-300:]}")
        return salida
    ejecutar(["git", "init", "-q", "--bare", remoto])
    ejecutar(["git", "init", "-q", sitio])
    for k, v in (("user.name", "Prueba"), ("user.email", "prueba@example.invalid"), ("core.autocrlf", "false")):
        git("config", k, v)
    with open(os.path.join(sitio, ".gitattributes"), "w") as f:
        f.write("* -text\n")
    git("add", "-A")
    git("commit", "-q", "-m", "Inicio")
    git("branch", "-M", "main")
    git("remote", "add", "origin", remoto)
    git("push", "-q", "-u", "origin", "main")
    dev = ent.dev("dev_gh", servidores=[ent.url("gh")])
    ent.preparar(dev, V1, "Primera numerada.")
    pv = os.path.join(dev, "publicacion", "publicar_version.py")
    codigo, salida = ejecutar([PY, pv, "github", V1, "--sitio", sitio, "--si"], timeout=900)
    if f"Publicada la {V1}" not in salida or f"Etiqueta v{V1}" not in salida:
        fallos.append(f"github: {salida[-800:]}")
    if codigo == 0 or "no es de GitHub" not in salida:
        fallos.append(f"la Release sin GitHub de verdad no avisa: {codigo} {salida[-300:]}")
    if ent.act.abrir_sobre(open(os.path.join(sitio, f"{ent.act.CANAL}.json"), "rb").read(),
                           ent.claves).version != V1:
        fallos.append("no se anuncia en el sitio")
    commits = git("log", "--format=%H %s").splitlines()
    anuncia = next((x.split()[0] for x in commits if x.endswith(f"Anuncia la versión {V1}")), None)
    etiquetada = git("rev-list", "-n", "1", f"v{V1}", carpeta=remoto).strip()
    if not anuncia or etiquetada != anuncia:
        fallos.append(f"la etiqueta v{V1} no está en el remoto o no va en el commit que la anuncia: {etiquetada}")
    codigo, salida = ejecutar([PY, pv, "github", V1, "--sitio", sitio, "--si"], timeout=900)
    if "ya estaba anunciada" not in salida or len(git("log", "--format=%H").splitlines()) != len(commits):
        fallos.append(f"repetirla no se salta lo hecho: {salida[-500:]}")
    codigo, salida = ejecutar([PY, pv, "release", V_PUENTE, "--sitio", sitio])
    if codigo == 0 or "por fecha" not in salida:
        fallos.append(f"release de una versión por fecha: {salida[-300:]}")
    return fallos


@prueba("github_clon", "con el programa en el src/ de un clon (1.4.18): la lista antes de cada commit, sin confirmar no "
                       "sube nada, lo ajeno lo para (lo que ignora .gitignore no), cada commit solo con lo suyo y src/ "
                       "idéntico al manifiesto; src/ cambiado después de preparar no se publica; las herramientas, en "
                       "herramientas/ del propio clon, y lo que se queda en el equipo (versiones preparadas, paquete, "
                       "guías) ni se sube ni para nada")
def p_github_clon(ent):
    fallos = []
    raw = os.path.join(ent.web, "raw")              # (servida por HTTPS: el clon es la carpeta del sitio)
    clon = os.path.join(raw, "sincronizar-doblajes")
    herr = os.path.join(clon, "herramientas")
    remoto = os.path.join(ent.t, "remoto_clon.git")

    def git(*orden):
        codigo, salida = ejecutar(["git", "-C", clon, *orden])
        if codigo:
            raise RuntimeError(f"git {' '.join(orden)}: {salida[-300:]}")
        return salida

    def en_commit(asunto):
        h = next(x.split()[0] for x in git("log", "--format=%H %s").splitlines() if x.endswith(asunto))
        return sorted(x for x in git("show", "--name-only", "--format=", h).splitlines() if x)
    ejecutar(["git", "init", "-q", "--bare", remoto])
    ejecutar(["git", "init", "-q", clon])
    for k, v in (("user.name", "Prueba"), ("user.email", "prueba@example.invalid"), ("core.autocrlf", "false")):
        git("config", k, v)
    with open(os.path.join(clon, ".gitattributes"), "w") as f:
        f.write("* -text\n")
    # de herramientas/ solo se suben las herramientas (lo demás es del equipo del autor), como en el repositorio
    with open(os.path.join(clon, ".gitignore"), "w") as f:
        f.write("src/Sincronizar_Doblaje_ajustes.json\nsrc/.actualizador/\n__pycache__/\nherramientas/*\n"
                "!herramientas/publicar_version.py\n!herramientas/archivos_publicados.txt\n")
    # las herramientas, ya en el repositorio (una sola copia: la del clon)
    os.makedirs(herr)
    programa_ = [r for r in COPIAR if not r.startswith("publicacion/")]
    shutil.copyfile(origen("publicacion/publicar_version.py"), os.path.join(herr, "publicar_version.py"))
    with open(os.path.join(herr, "archivos_publicados.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(dict.fromkeys(programa_ + ["LEEME.txt"])) + "\n")
    git("add", "-A")
    git("commit", "-q", "-m", "Inicio")
    git("branch", "-M", "main")
    git("remote", "add", "origin", remoto)
    git("push", "-q", "-u", "origin", "main")
    # el programa en src/, aún sin subir (como antes de la primera versión desde src/)
    os.makedirs(os.path.join(clon, "src"))
    for r in programa_:
        shutil.copyfile(origen(r), os.path.join(clon, "src", r))
    shutil.copyfile(origen(LEEME), os.path.join(clon, "src", "LEEME.txt"))
    pv = cargar(os.path.join(herr, "publicar_version.py"), "pv_clon")
    ent.para_pruebas(pv, os.path.join(clon, "src", "actualizador.py"), [ent.url("raw/sincronizar-doblajes")])
    pvr = os.path.join(herr, "publicar_version.py")
    notas = os.path.join(ent.t, "notas_clon.txt")
    with open(notas, "w", encoding="utf-8") as f:
        f.write("Primera desde src/.")
    codigo, salida = ejecutar([PY, pvr, "preparar", V1, "--notas", notas, "--clave", ent.ruta_clave, "--pruebas",
                               "--zip-compartir"])
    if codigo:
        return [f"preparar desde src/: {salida[-800:]}"]
    if not os.path.isfile(os.path.join(herr, "versiones", V1, "manifiesto.json")) or \
            not os.path.isfile(os.path.join(herr, "Sincronizar_Doblaje_compartir.zip")) or \
            os.path.exists(os.path.join(clon, "src", "publicacion")) or \
            os.path.exists(os.path.join(clon, "src", "Sincronizar_Doblaje_compartir.zip")):
        fallos.append("la versión preparada y el paquete para compartir no quedan en la carpeta de herramientas")
    n = len(git("log", "--format=%H").splitlines())
    codigo, salida = ejecutar([PY, pvr, "github", V1, "--solo-lista"])
    if codigo or "src/actualizador.py" not in salida or f"versiones/{V1}/manifiesto.json" not in salida or \
            "actual.json" not in salida or len(git("log", "--format=%H").splitlines()) != n:
        fallos.append(f"--solo-lista: {salida[-600:]}")
    r = subprocess.run([PY, pvr, "github", V1], input="n\n", capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, creationflags=SIN_CONSOLA)
    if r.returncode == 0 or "no confirmado" not in r.stdout + r.stderr or "src/actualizador.py" not in r.stdout or \
            len(git("log", "--format=%H").splitlines()) != n or git("diff", "--cached", "--name-only").strip():
        fallos.append(f"sin confirmar sube algo (o no enseña la lista): {(r.stdout + r.stderr)[-500:]}")
    with open(os.path.join(clon, "notas_mias.txt"), "w") as f:
        f.write("algo ajeno\n")
    with open(os.path.join(clon, "src", "Sincronizar_Doblaje_ajustes.json"), "w") as f:
        f.write("{}\n")
    codigo, salida = ejecutar([PY, pvr, "github", V1, "--si"])
    if codigo == 0 or "notas_mias.txt" not in salida or len(git("log", "--format=%H").splitlines()) != n:
        fallos.append(f"lo ajeno no lo para: {salida[-400:]}")
    os.remove(os.path.join(clon, "notas_mias.txt"))
    with open(os.path.join(herr, "ACTUALIZACIONES.txt"), "w", encoding="utf-8") as f:
        f.write("guía local del autor (no se sube)\n")
    codigo, salida = ejecutar([PY, pvr, "github", V1, "--si"], timeout=900)
    if f"Publicada la {V1}" not in salida or "no es de GitHub" not in salida:
        fallos.append(f"github desde src/ (con lo que dejó el «no» de antes y los ajustes ignorados): {salida[-800:]}")
    else:
        sobre = json.loads(open(os.path.join(herr, "versiones", V1, "manifiesto.json"), "rb").read())
        m = json.loads(base64.b64decode(sobre["manifiesto"]))
        esperado = sorted([f"versiones/{V1}/manifiesto.json", f"versiones/{V1}/paquete/Sincronizar_Doblaje_{V1}.zip"]
                          + [f"versiones/{V1}/archivos/{h}" for h in {a["sha256"] for a in m["archivos"]}]
                          + [f"src/{a['ruta']}" for a in m["archivos"]])
        if en_commit(f"Sube la versión {V1} (sin anunciar)") != esperado:
            fallos.append(f"el commit de la versión no lleva justo lo suyo: {en_commit(f'Sube la versión {V1} (sin anunciar)')}")
        anuncio = en_commit(f"Anuncia la versión {V1}")
        if ent.act.CANAL + ".json" not in anuncio or any(
                x not in (ent.act.CANAL + ".json", "Sincronizar_Doblaje.zip") and not x.startswith("historial/")
                for x in anuncio):
            fallos.append(f"el commit del anuncio no lleva justo lo suyo: {anuncio}")
        for a in m["archivos"]:
            if ent.act.sha256_archivo(os.path.join(clon, "src", a["ruta"])) != a["sha256"]:
                fallos.append(f"src/{a['ruta']} no es el del manifiesto")
        if "Sincronizar_Doblaje_ajustes.json" in git("ls-files"):
            fallos.append("se ha subido un archivo ignorado (los ajustes)")
        de_herr = sorted(x for x in git("ls-files", "herramientas").splitlines() if x)
        if de_herr != ["herramientas/archivos_publicados.txt", "herramientas/publicar_version.py"]:
            fallos.append(f"de herramientas/ se ha subido algo más que las herramientas: {de_herr}")
    # src/ cambiado después de preparar: no se publica
    with open(notas, "w", encoding="utf-8") as f:
        f.write("Segunda.")
    codigo, salida = ejecutar([PY, pvr, "preparar", V2, "--notas", notas, "--clave", ent.ruta_clave, "--pruebas"])
    with open(os.path.join(clon, "src", "sincronizar_doblaje.py"), "a", encoding="utf-8") as f:
        f.write("\n# cambio después de preparar\n")
    n = len(git("log", "--format=%H").splitlines())
    codigo, salida = ejecutar([PY, pvr, "github", V2, "--si"])
    if codigo == 0 or "No son los de la" not in salida or len(git("log", "--format=%H").splitlines()) != n:
        fallos.append(f"src/ cambiado después de preparar se publica: {salida[-400:]}")
    return fallos


@prueba("clave_sin_defecto", "la clave de firma solo con --clave/--guardar o la variable SYNC_MASTER_CLAVE: sin ninguna, "
                             "error claro y nada preparado ni creado; con la variable, como con --clave; nunca dentro "
                             "del programa; ninguna ruta de clave en el código")
def p_clave_sin_defecto(ent):
    fallos = []
    dev = ent.dev("dev_clave")
    pv = os.path.join(dev, "publicacion", "publicar_version.py")
    notas = os.path.join(ent.t, "notas_clave.txt")
    with open(notas, "w", encoding="utf-8") as f:
        f.write("Prueba de la clave.")
    preparada = os.path.join(dev, "publicacion", "versiones", V1, "manifiesto.json")
    sin = {k: v for k, v in os.environ.items() if k.upper() != "SYNC_MASTER_CLAVE"}
    codigo, salida = ejecutar([PY, pv, "preparar", V1, "--notas", notas, "--pruebas"], env=sin)
    if codigo == 0 or "SYNC_MASTER_CLAVE" not in salida or os.path.exists(os.path.dirname(preparada)):
        fallos.append(f"preparar sin clave: {salida[-400:]}")
    antes = set(os.listdir(ent.t))
    codigo, salida = ejecutar([PY, pv, "claves"], env=sin)
    if codigo == 0 or "SYNC_MASTER_CLAVE" not in salida or set(os.listdir(ent.t)) != antes:
        fallos.append(f"claves sin decir dónde: {salida[-400:]}")
    codigo, salida = ejecutar([PY, pv, "claves", "--guardar", os.path.join(dev, "clave_nueva.txt")], env=sin)
    if codigo == 0 or os.path.exists(os.path.join(dev, "clave_nueva.txt")):
        fallos.append(f"guarda la clave dentro del programa: {salida[-400:]}")
    codigo, salida = ejecutar([PY, pv, "preparar", V1, "--notas", notas, "--pruebas"],
                              env=dict(sin, SYNC_MASTER_CLAVE=ent.ruta_clave))
    if codigo or not os.path.isfile(preparada):
        fallos.append(f"preparar con SYNC_MASTER_CLAVE: {salida[-400:]}")
    texto = open(pv, encoding="utf-8").read()
    if any(x in texto for x in ("expanduser", "CLAVE_DEFECTO", "clave_firma.txt", "USERPROFILE")):
        fallos.append("publicar_version.py aún lleva una ruta de clave por defecto")
    return fallos


# ---------------------------------------------------------------------------------------------------------------

def montar(ent):
    """Versiones de prueba: V1 (la más antigua con actualizador), V2, V3, una con la GUI rota, otra con el motor roto
    y otra V3 distinta (para el servidor alternativo que sirve otra cosa)."""
    ent.dev1 = d = ent.dev("dev1")
    ent.preparar(d, V1, "Primera versión con actualizador.")
    ent.publicar(d, V1)
    with open(os.path.join(d, "sincronizar_doblaje.py"), "a", encoding="utf-8") as f:
        f.write("\n# cambio de prueba de la V2\n")
    with open(os.path.join(d, *LEEME.split("/")), "a", encoding="utf-8") as f:
        f.write("\nNovedad de la V2.\n")
    ent.preparar(d, V2, "Novedades de la V2:\n- Un cambio en el motor.")
    ent.publicar(d, V2)
    with open(os.path.join(d, "extra_nuevo.txt"), "w", encoding="utf-8") as f:
        f.write("archivo nuevo de la V3\n")
    texto = open(os.path.join(d, "publicacion", "archivos_publicados.txt"), encoding="utf-8").read()
    with open(os.path.join(d, "publicacion", "archivos_publicados.txt"), "w", encoding="utf-8") as f:
        f.write(texto.replace("probar_sincronizador.py\n", "") + "extra_nuevo.txt -> extra/nuevo.txt\n")
    ent.preparar(d, V3, "Novedades de la V3:\n- Archivo nuevo en una subcarpeta.\n- Se retira la batería.")
    ent.publicar(d, V3)
    shutil.copytree(os.path.join(ent.web, "pub"), os.path.join(ent.web, "alt"))
    gui = os.path.join(d, "Sincronizar_Doblaje_Gui.py")
    bueno = open(gui, encoding="utf-8").read()
    with open(gui, "w", encoding="utf-8") as f:
        f.write(bueno.replace("        self.master = master\n", "        self.master = master\n"
                              "        raise RuntimeError('la GUI nueva revienta al abrir (prueba)')\n", 1))
    ent.preparar(d, V_GUI_ROTA, "GUI rota a propósito.")
    ent.publicar(d, V_GUI_ROTA, "mala_gui")
    with open(gui, "w", encoding="utf-8") as f:
        f.write(bueno)
    motor = os.path.join(d, "sincronizar_doblaje.py")
    bueno_m = open(motor, encoding="utf-8").read()
    with open(motor, "w", encoding="utf-8") as f:
        f.write(bueno_m.replace("\nAR = 8000", "\nraise RuntimeError('motor roto (prueba)')\nAR = 8000", 1))
    ent.preparar(d, V_MOTOR_ROTO, "Motor roto a propósito.")
    ent.publicar(d, V_MOTOR_ROTO, "mala_motor")
    with open(motor, "w", encoding="utf-8") as f:
        f.write(bueno_m)
    # otra publicación de la V3 (otro contenido, misma firma de confianza) en «otra»
    d2 = ent.dev("dev_otra")
    with open(os.path.join(d2, *LEEME.split("/")), "a", encoding="utf-8") as f:
        f.write("\nOtra V3.\n")
    ent.preparar(d2, V3, "Otra V3.")
    ent.publicar(d2, V3, "otra")
    # la numeración de antes: la 2026.10.25 tal como se publicó y el paso a la numerada, los dos en el puntero de
    # las copias de antes (lo único que leen); la numerada sigue en el suyo
    ent.dev_vieja = ent.dev_viejo("dev_vieja", V_VIEJA)
    if ent.dev_vieja:
        ent.preparar(ent.dev_vieja, V_VIEJA, "La de antes de la numeración.", extra=("--por-fecha",))
        ent.publicar(ent.dev_vieja, V_VIEJA)
        ent.dev_puente = ent.dev("dev_puente")
        ent.preparar(ent.dev_puente, V_PUENTE, "Paso a la numeración nueva (1.0, 1.1...).", extra=("--por-fecha",))
        ent.publicar(ent.dev_puente, V_PUENTE)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--solo", default="")
    ap.add_argument("--conservar", action="store_true")
    ap.add_argument("--carpeta", help="carpeta de trabajo (por defecto, una temporal)")
    args = ap.parse_args()
    solo = {x.strip() for x in args.solo.split(",") if x.strip()}
    t = tempfile.mkdtemp(prefix="pruebas_actualizador_", dir=args.carpeta)
    t0 = time.time()
    resultados = []
    ent = None
    try:
        ent = Entorno(t)
        montar(ent)
        print(f"Versiones de prueba listas en {time.time() - t0:.0f} s", flush=True)
        for nombre, que, f in PRUEBAS:
            if solo and nombre not in solo:
                continue
            t1 = time.time()
            nota = ""
            try:
                r = f(ent)
                fallos, nota = r if isinstance(r, tuple) else (r, "")
            except Exception:
                fallos = ["no se pudo probar: " + traceback.format_exc()[-1200:]]
            resultados.append((nombre, fallos))
            print(f"{'OK   ' if not fallos else 'FALLO'} {nombre:21s} {time.time() - t1:5.0f} s  {que}"
                  + (f"\n        ({nota})" if nota else "") + "".join(f"\n        - {x}" for x in fallos), flush=True)
    finally:
        if ent:
            ent.parar()
        if args.conservar:
            print(f"Archivos de las pruebas: {t}")
        else:
            # los objetos del repositorio git de la prueba «github» son de solo lectura (sin esto se quedaban), y las
            # ventanas que abren las pruebas tardan unos segundos en cerrarse y soltar sus archivos
            def escribible(funcion, ruta, _):
                try:
                    os.chmod(ruta, 0o666)
                    funcion(ruta)
                except OSError:
                    pass
            for _ in range(30):
                shutil.rmtree(t, onerror=escribible)
                if not os.path.exists(t):
                    break
                time.sleep(2)
            if os.path.exists(t):
                print(f"No se ha podido borrar del todo {t}: bórrala a mano.")
    malos = [n for n, f in resultados if f]
    print(f"\n{len(resultados) - len(malos)} de {len(resultados)} pruebas bien"
          + (f"; FALLAN: {', '.join(malos)}" if malos else "") + f" ({time.time() - t0:.0f} s)")
    print("No aplica: interfaz web remota (el programa no tiene).")
    return 1 if malos else 0


if __name__ == "__main__":
    sys.exit(main())
