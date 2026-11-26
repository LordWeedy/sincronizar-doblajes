#!/usr/bin/env python3
"""
publicar_version.py - Prepara, publica y verifica versiones de Sync Master para su actualizador.

Tres pasos separados (ver ACTUALIZACIONES.txt):
  1. preparar VERSION --notas NOTAS.txt     versión nueva en publicacion\\versiones\\VERSION: solo los archivos de
                                             archivos_publicados.txt, con la VERSION puesta en todos los .py, el
                                             manifiesto (tamaños, SHA-256, notas), su firma y el paquete .zip
  2. subir VERSION --destino CARPETA        copia la versión, inmutable, a la carpeta que sirve el servidor HTTPS
     activar VERSION --destino CARPETA      la anuncia (cambia estable.json de una vez), solo si está completa;
                                             la anterior queda en historial\\
     publicar VERSION --destino CARPETA     subir + verificar + activar + verificar
  3. verificar --url https://...            descarga por HTTPS lo publicado y comprueba firma, tamaños y SHA-256

Con el repositorio de GitHub (el clon, si esta herramienta está en su herramientas/; si no, publicacion\\sitio), todo de
una vez:  github VERSION
  (sube, comprueba, anuncia y crea la etiqueta vVERSION y la Release con las notas y el paquete; repetirlo termina
  lo que faltara, p. ej. la Release si no había GitHub CLI; «release VERSION» hace solo esto último)
Otras: claves (crea la clave de firma), configurar --servidor URL (pone los servidores en actualizador.py),
estado --destino, restaurar --destino [--version] (vuelve a anunciar una versión anterior).
Las versiones van numeradas: 1.0, 1.1, 1.2... (1.1.1 para arreglar una ya publicada). Las de antes de la 1.0 eran
por fecha (AAAA.MM.DD) y se anuncian aparte, en estable.json, que es lo único que leen esas copias.
La clave privada no sale de este equipo ni va en el código: se indica con --clave RUTA (o --guardar RUTA al crearla) o
con la variable de entorno SYNC_MASTER_CLAVE, sin ruta por defecto; se guarda fuera del programa y del repositorio y no
se publica nunca.
"""

import argparse
import ast
import base64
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
# Dónde está el programa que se publica. Lo de siempre: esta carpeta («publicacion») dentro de la del programa. Desde la
# 1.4.18 (el código en src/ del repositorio, para recibir cambios con pull requests): esta carpeta es herramientas/ del
# clon del repositorio (CLON, la de arriba; una sola copia de las herramientas, la del repositorio) y el programa está en
# su src/. Las versiones preparadas, las notas y el paquete para compartir se quedan aquí, en el equipo: el .gitignore
# del repositorio solo deja subir de herramientas/ las herramientas que van en él (herramientas()).
_ARRIBA = os.path.dirname(AQUI)
CLON = (_ARRIBA if not os.path.isfile(os.path.join(_ARRIBA, "actualizador.py"))
        and os.path.isfile(os.path.join(_ARRIBA, "src", "actualizador.py")) else None)
PROGRAMA = os.path.join(CLON, "src") if CLON else _ARRIBA
SITIO = CLON or os.path.join(AQUI, "sitio")
sys.path.insert(0, PROGRAMA)
import actualizador as act       # noqa: E402

LISTA = "archivos_publicados.txt"
VARIABLE_CLAVE = "SYNC_MASTER_CLAVE"    # la ruta de la clave privada, si no se da con --clave/--guardar (no hay otra)
ACTUALIZABLE_DESDE = "2026.10.24"      # la primera versión con actualizador: desde ella se puede ir a cualquiera
PYTHON_MIN = "3.9"
NUMERADA = re.compile(r"[1-9]\d{0,2}\.\d{1,3}(\.\d{1,3})?")     # 1.0, 1.1... y 1.1.1 para un arreglo
# nunca se publican aunque alguien los ponga en la lista
EXCLUIDOS = re.compile(r"(?i)(^|/)(\.actualizador|\.venv|ffmpeg|__pycache__|\.git|respaldos|versiones)(/|$)|"
                       r"_ajustes\.json$|_informe\.txt$|\.log$|\.zip$|\.key$|\.pem$|clave|credencial|token|"
                       r"_SYNC|\.(mkv|mka|mp4|avi|srt|ass|flac|wav)$")


class Error(Exception):
    pass


def herramientas(programa):
    """La carpeta con la lista de lo que se publica, las versiones preparadas y las notas: «publicacion» dentro de la del
    programa (lo de siempre) o, con el programa en el src/ de un clon, esta misma (AQUI: herramientas/ del clon)."""
    dentro = os.path.join(programa, "publicacion")
    return dentro if os.path.isdir(dentro) else AQUI


def sha256(ruta):
    return act.sha256_archivo(ruta)


def leer_lista(programa):
    """[(origen absoluto, ruta en la instalación)] de archivos_publicados.txt."""
    ruta = os.path.join(herramientas(programa), LISTA)
    if not os.path.exists(ruta):
        raise Error(f"Falta {ruta}")
    out, vistos = [], set()
    for n, linea in enumerate(open(ruta, encoding="utf-8").read().splitlines(), 1):
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        origen, _, destino = (x.strip() for x in linea.partition("->"))
        destino = destino or origen
        try:
            act.validar_ruta(destino)
        except act.ErrorActualizacion as e:
            raise Error(f"{LISTA}, línea {n}: {e.mensaje}")
        if EXCLUIDOS.search(destino) or EXCLUIDOS.search(origen.replace("\\", "/").split("/")[-1]):
            raise Error(f"{LISTA}, línea {n}: «{linea}» no se puede publicar (ajustes, datos, registros, claves o "
                        "archivos de trabajo).")
        absoluto = os.path.normpath(os.path.join(programa, origen))
        if not act.dentro(programa, absoluto) or not os.path.isfile(absoluto) or act.es_enlace(absoluto):
            raise Error(f"{LISTA}, línea {n}: no existe (o no es un archivo normal de la carpeta) {origen}")
        if destino.lower() in vistos:
            raise Error(f"{LISTA}, línea {n}: {destino} está repetido")
        vistos.add(destino.lower())
        out.append((absoluto, destino))
    # los componentes que tiene que llevar: los del actualizador.py que se publica (una versión de antes, publicada
    # otra vez tal como era, no trae los que se añadieron después: forzados_vo.py, desde la 1.4.13)
    act_py = next((o for o, d in out if d.lower() == "actualizador.py"), None)
    for c in (leer_constante(act_py, "COMPONENTES") if act_py else None) or act.COMPONENTES:
        if c.lower() not in vistos:
            raise Error(f"{LISTA}: falta {c}")
    return out


def version_de(ruta):
    return act._version_de(ruta)


def fijar_constante(ruta, nombre, valor):
    """Cambia la línea «NOMBRE = ...» (una sola, a nivel de módulo y en una línea) de un .py. Devuelve si cambió."""
    texto = open(ruta, encoding="utf-8").read()
    patron = re.compile(rf"^{nombre} = .*$", re.M)
    hallados = patron.findall(texto)
    if len(hallados) != 1:
        raise Error(f"{os.path.basename(ruta)}: tiene que haber una sola línea «{nombre} = ...» (hay {len(hallados)})")
    nuevo = patron.sub(lambda m: f"{nombre} = {valor}", texto)
    if nuevo == texto:
        return False
    ast.parse(nuevo)
    tmp = ruta + ".fijando"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(nuevo)
    os.replace(tmp, ruta)
    return True


def leer_constante(ruta, nombre):
    for x in ast.parse(open(ruta, encoding="utf-8").read()).body:
        if isinstance(x, ast.Assign) and any(getattr(t, "id", "") == nombre for t in x.targets):
            return ast.literal_eval(x.value)
    return None


def canal_de(v):
    """El puntero que anuncia la versión: las numeradas, CANAL; las por fecha, el de las copias de antes de la 1.0."""
    return act.CANAL_FECHAS if act.por_fecha(v) else act.CANAL


def ruta_clave(valor, opcion):
    """La ruta de la clave privada: la de la opción o, si no, la de la variable de entorno. Nunca una por defecto, y
    nunca dentro del programa ni del repositorio (donde podría acabar subida)."""
    ruta = (valor or os.environ.get(VARIABLE_CLAVE, "")).strip()
    if not ruta:
        raise Error(f"Falta la ruta de la clave de firma: {opcion} RUTA o la variable de entorno {VARIABLE_CLAVE} (la "
                    "clave no va en el código ni en el repositorio).")
    ruta = os.path.abspath(ruta)
    for carpeta, que in ((PROGRAMA, "la carpeta del programa"), (CLON, "el repositorio")):
        if carpeta and (act.dentro(carpeta, ruta)
                        or os.path.normcase(os.path.dirname(ruta)) == os.path.normcase(carpeta)):
            raise Error(f"La clave privada no puede estar dentro de {que} ({ruta}).")
    return ruta


def leer_clave(ruta):
    if not os.path.exists(ruta):
        raise Error(f"No está la clave de firma en {ruta}. Créala con «publicar_version.py claves».")
    m = re.search(r"\b[0-9a-f]{64}\b", open(ruta, encoding="utf-8").read())
    if not m:
        raise Error(f"{ruta} no tiene una clave válida")
    secreta = bytes.fromhex(m.group(0))
    publica = act.ed25519_publica(secreta)
    return secreta, publica, act.id_de_clave(publica)


def nombres_sin_definir(ruta):
    sys.path.insert(0, PROGRAMA)
    try:
        import probar_sincronizador
        return probar_sincronizador.nombres_sin_definir(ruta)
    except ImportError:
        return []


# ---------------------------------------------------------------------------------------------------------------

def cmd_claves(args):
    destino = ruta_clave(args.guardar, "--guardar")
    if os.path.exists(destino):
        raise Error(f"Ya existe {destino}; no se sobrescribe (perderías la clave con la que se firmó lo publicado).")
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    secreta = os.urandom(32)
    publica = act.ed25519_publica(secreta)
    kid = act.id_de_clave(publica)
    with open(destino, "x", encoding="utf-8") as f:
        f.write(f"# Clave privada de firma de Sync Master ({kid}). NO la compartas ni la publiques.\n"
                f"# Haz una copia de seguridad en un sitio seguro: sin ella no se pueden publicar más versiones\n"
                f"# para quienes ya tienen el programa.\n{secreta.hex()}\n")
    if os.name == "nt":
        usuario = os.environ.get("USERNAME", "")
        subprocess.run(["icacls", destino, "/inheritance:r", "/grant:r", f"{usuario}:F"], capture_output=True)
    print(f"Clave creada en {destino}\nId: {kid}\nPública: {publica.hex()}")
    if args.instalar:
        ruta = os.path.join(args.programa, "actualizador.py")
        claves = dict(leer_constante(ruta, "CLAVES_PUBLICAS") or {})
        claves[kid] = publica.hex()
        fijar_constante(ruta, "CLAVES_PUBLICAS", repr(claves))
        print(f"Puesta en {ruta} (CLAVES_PUBLICAS). Las instalaciones la tendrán con la próxima versión.")
    return 0


def cmd_configurar(args):
    ruta = os.path.join(args.programa, "actualizador.py")
    for s in args.servidor:
        if not s.lower().startswith("https://"):
            raise Error(f"{s}: solo HTTPS")
    servidores = [s if s.endswith("/") else s + "/" for s in args.servidor]
    if fijar_constante(ruta, "SERVIDORES", repr(servidores)):
        print(f"SERVIDORES = {servidores} en {ruta}")
    else:
        print("Ya estaban así.")
    return 0


def cmd_preparar(args):
    programa = args.programa
    v = args.version
    try:
        if act.por_fecha(v):
            if not args.por_fecha:
                raise Error(f"{v} es de la numeración por fecha de antes de la 1.0; las versiones van numeradas "
                            "(1.0, 1.1, 1.2...). Por fecha solo se prepara con --por-fecha (para las copias de antes).")
        elif not NUMERADA.fullmatch(v):
            raise Error(f"{v}: las versiones son «mayor.menor» (1.0, 1.1, 1.2...) o, para arreglar una ya publicada, "
                        "«mayor.menor.arreglo» (1.1.1).")
    except ValueError as e:
        raise Error(str(e))
    carpeta_versiones = os.path.join(herramientas(programa), "versiones")
    destino = os.path.join(carpeta_versiones, v)
    if os.path.exists(destino):
        raise Error(f"La {v} ya está preparada ({destino}) y las versiones no se cambian: usa otro número.")
    hechas = [d for d in (os.listdir(carpeta_versiones) if os.path.isdir(carpeta_versiones) else [])
              if re.fullmatch(r"\d+(\.\d+){0,3}", d)]
    for d in hechas:
        if act.orden_version(d) >= act.orden_version(v):
            raise Error(f"Ya hay una versión igual o posterior preparada ({d}).")
    notas = open(args.notas, encoding="utf-8").read().strip() if args.notas else ""
    if not notas:
        raise Error("Faltan las notas de la versión (--notas archivo.txt): son las novedades que verá quien actualiza.")
    secreta, publica, kid = leer_clave(ruta_clave(args.clave, "--clave"))
    lista = leer_lista(programa)
    # 1) la VERSION en todos los .py y comprobaciones de coherencia
    for origen, ruta in lista:
        if ruta.lower().endswith(".py"):
            if fijar_constante(origen, "VERSION", repr(v)):
                print(f"VERSION = {v!r} en {ruta}")
    problemas = []
    for origen, ruta in lista:
        if ruta.lower().endswith(".py"):
            try:
                compile(open(origen, "rb").read(), ruta, "exec", dont_inherit=True)
            except SyntaxError as e:
                problemas.append(f"{ruta}: error de sintaxis en la línea {e.lineno}")
                continue
            if version_de(origen) != v:
                problemas.append(f"{ruta}: VERSION es {version_de(origen)!r}, no {v!r}")
            for ln, func, nombre in nombres_sin_definir(origen):
                problemas.append(f"{ruta}, línea {ln} ({func}): «{nombre}» no existe")
    act_py = next(o for o, r in lista if r == "actualizador.py")
    claves = leer_constante(act_py, "CLAVES_PUBLICAS") or {}
    if claves.get(kid) != publica.hex():
        problemas.append(f"actualizador.py no confía en la clave con la que firmas ({kid}): ponla con «claves "
                         "--instalar» o firma con la que tiene.")
    servidores = leer_constante(act_py, "SERVIDORES") or []
    if not servidores and not args.pruebas:
        problemas.append("actualizador.py no tiene SERVIDORES: las instalaciones no sabrán dónde buscar "
                         "(«configurar --servidor https://...»).")
    if leer_constante(act_py, "CA_EXTRA") is not None and not args.pruebas:
        problemas.append("actualizador.py tiene CA_EXTRA (solo para pruebas): déjalo en None.")
    if problemas:
        raise Error("No se prepara la versión:\n  - " + "\n  - ".join(problemas))
    # 2) carpeta nueva con los archivos, el manifiesto firmado y el paquete
    tmp = tempfile.mkdtemp(prefix=f"preparando-{v}-", dir=os.path.dirname(destino) if os.path.isdir(
        os.path.dirname(destino)) else None)
    try:
        os.makedirs(os.path.join(tmp, "archivos"))
        archivos = []
        for origen, ruta in lista:
            h, n = sha256(origen), os.path.getsize(origen)
            copia = os.path.join(tmp, "archivos", h)
            if not os.path.exists(copia):
                shutil.copyfile(origen, copia)
            if sha256(copia) != h:
                raise Error(f"La copia de {ruta} no coincide")
            archivos.append({"ruta": ruta, "tamano": n, "sha256": h})
        manifiesto = {"formato": act.FORMATO, "programa": act.PROGRAMA, "version": v,
                      "fecha": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "canal": canal_de(v), "notas": notas,
                      "requisitos": {"plataforma": "win", "python_min": args.python_min},
                      "actualizable_desde": args.actualizable_desde, "archivos": archivos}
        datos = json.dumps(manifiesto, ensure_ascii=False, indent=1).encode("utf-8")
        sobre = act.crear_sobre(datos, secreta, kid)
        pub = act.abrir_sobre(sobre, {kid: claves[kid]})      # comprobado con la clave que llevará el programa
        with open(os.path.join(tmp, "manifiesto.json"), "wb") as f:
            f.write(sobre)
        with open(os.path.join(tmp, "notas.txt"), "w", encoding="utf-8") as f:
            f.write(notas + "\n")
        os.makedirs(os.path.join(tmp, "paquete"))
        zip_ = os.path.join(tmp, "paquete", f"Sincronizar_Doblaje_{v}.zip")
        # con la lista de lo que es del programa: así la primera actualización sabe qué puede retirar
        estado = {"version": v, "archivos": {a["ruta"]: {"sha256": a["sha256"], "tamano": a["tamano"]}
                                             for a in archivos}, "instalado": "paquete .zip"}
        with zipfile.ZipFile(zip_, "w", zipfile.ZIP_DEFLATED) as z:
            for a in archivos:
                z.write(os.path.join(tmp, "archivos", a["sha256"]), "Sincronizar Doblaje/" + a["ruta"])
            z.writestr("Sincronizar Doblaje/.actualizador/estado.json",
                       json.dumps(estado, ensure_ascii=False, indent=1))
        with zipfile.ZipFile(zip_) as z:
            if z.testzip() is not None or len(z.namelist()) != len(archivos) + 1:
                raise Error("El paquete .zip no ha salido bien")
        os.makedirs(carpeta_versiones, exist_ok=True)
        os.replace(tmp, destino)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    print(f"Preparada la {v} en {destino}: {len(archivos)} archivos, {sum(a['tamano'] for a in archivos)} bytes, "
          f"manifiesto {pub.huella[:16]}…, firmado con {kid}.")
    if args.zip_compartir:
        # (junto al programa, como siempre; con el programa en el src/ de un clon, aquí, en herramientas/: el .gitignore
        # no lo deja subir)
        en_clon = not os.path.isdir(os.path.join(programa, "publicacion"))
        compartir = os.path.join(AQUI if en_clon else programa, "Sincronizar_Doblaje_compartir.zip")
        shutil.copyfile(os.path.join(destino, "paquete", f"Sincronizar_Doblaje_{v}.zip"), compartir + ".nuevo")
        os.replace(compartir + ".nuevo", compartir)
        print(f"Paquete para compartir: {compartir}")
    return 0


def _claves_de(programa, version=None):
    """Las claves de confianza: las de la versión preparada (si se indica y existe) o las de la carpeta."""
    if version:
        d = os.path.join(herramientas(programa), "versiones", version)
        m = json.loads(open(os.path.join(d, "manifiesto.json"), "rb").read())
        datos = base64.b64decode(m["manifiesto"])
        for a in json.loads(datos)["archivos"]:
            if a["ruta"] == "actualizador.py":
                return leer_constante(os.path.join(d, "archivos", a["sha256"]), "CLAVES_PUBLICAS") or {}
    return leer_constante(os.path.join(programa, "actualizador.py"), "CLAVES_PUBLICAS") or {}


def comprobar_carpeta_version(carpeta, claves, version):
    """Comprueba una versión en una carpeta (preparada o publicada): firma, manifiesto y cada archivo."""
    sobre = open(os.path.join(carpeta, "manifiesto.json"), "rb").read()
    pub = act.abrir_sobre(sobre, claves)
    if pub.version != version:
        raise Error(f"{carpeta}: el manifiesto es de la {pub.version}, no de la {version}")
    for a in pub.manifiesto["archivos"]:
        ruta = os.path.join(carpeta, "archivos", a["sha256"])
        if not os.path.isfile(ruta):
            raise Error(f"Falta {a['ruta']} ({ruta})")
        if os.path.getsize(ruta) != a["tamano"] or sha256(ruta) != a["sha256"]:
            raise Error(f"{a['ruta']} no coincide con el manifiesto ({ruta})")
    return pub


def cmd_subir(args):
    v = args.version
    origen = os.path.join(herramientas(args.programa), "versiones", v)
    if not os.path.isdir(origen):
        raise Error(f"La {v} no está preparada ({origen}).")
    claves = _claves_de(args.programa, v)
    pub = comprobar_carpeta_version(origen, claves, v)
    destino = os.path.join(args.destino, "versiones", v)
    if os.path.isdir(destino):
        try:
            otra = comprobar_carpeta_version(destino, claves, v)
        except (Error, act.ErrorActualizacion) as e:
            raise Error(f"En el destino ya hay una {v} incompleta o distinta ({e}); no se toca: revísala a mano.")
        if otra.huella != pub.huella:
            raise Error(f"En el destino ya hay OTRA publicación de la {v}; las versiones no se cambian.")
        print(f"La {v} ya estaba subida e igual.")
        return 0
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    tmp = destino + f".subiendo-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, "archivos"))
    shutil.copyfile(os.path.join(origen, "manifiesto.json"), os.path.join(tmp, "manifiesto.json"))
    for a in pub.manifiesto["archivos"]:
        shutil.copyfile(os.path.join(origen, "archivos", a["sha256"]), os.path.join(tmp, "archivos", a["sha256"]))
    paquete = os.path.join(origen, "paquete", f"Sincronizar_Doblaje_{v}.zip")
    if os.path.isfile(paquete):         # el paquete completo, para instalarlo por primera vez
        os.makedirs(os.path.join(tmp, "paquete"))
        shutil.copyfile(paquete, os.path.join(tmp, "paquete", os.path.basename(paquete)))
    comprobar_carpeta_version(tmp, claves, v)
    os.replace(tmp, destino)
    print(f"Subida la {v} a {destino} (todavía sin anunciar).")
    return 0


def _puntero(destino, canal=act.CANAL):
    return os.path.join(destino, f"{canal}.json")


def anunciada_en(destino, canal, claves):
    """La Publicacion que anuncia ese puntero del destino, o None."""
    puntero = _puntero(destino, canal)
    return act.abrir_sobre(open(puntero, "rb").read(), claves) if os.path.exists(puntero) else None


def cmd_activar(args, permitir_anterior=False):
    v = args.version
    canal = canal_de(v)
    claves = _claves_de(args.programa, v if os.path.isdir(os.path.join(herramientas(args.programa), "versiones", v))
                        else None)
    carpeta = os.path.join(args.destino, "versiones", v)
    pub = comprobar_carpeta_version(carpeta, claves, v)
    puntero = _puntero(args.destino, canal)
    actual = anunciada_en(args.destino, canal, claves)
    if actual:
        if act.orden_version(actual.version) >= act.orden_version(v) and not permitir_anterior:
            raise Error(f"Ya se anuncia la {actual.version}; para volver a anunciar una anterior usa «restaurar».")
        historial = os.path.join(args.destino, "historial")
        os.makedirs(historial, exist_ok=True)
        shutil.copyfile(puntero, os.path.join(historial, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                                              + f"_{canal}_{actual.version}.json"))
    tmp = puntero + f".{os.getpid()}.tmp"
    shutil.copyfile(os.path.join(carpeta, "manifiesto.json"), tmp)
    if act.abrir_sobre(open(tmp, "rb").read(), claves).huella != pub.huella:
        os.remove(tmp)
        raise Error("La copia del manifiesto no coincide")
    os.replace(tmp, puntero)
    paquete = os.path.join(carpeta, "paquete", f"Sincronizar_Doblaje_{v}.zip")
    if os.path.isfile(paquete) and canal == act.CANAL:   # enlace fijo al paquete completo de la versión anunciada
        fijo = os.path.join(args.destino, "Sincronizar_Doblaje.zip")
        shutil.copyfile(paquete, fijo + ".nuevo")
        os.replace(fijo + ".nuevo", fijo)
    print(f"Anunciada la {v} ({puntero}).")
    return 0


def cmd_publicar(args):
    cmd_subir(args)
    cmd_verificar(argparse.Namespace(programa=args.programa, destino=args.destino, url=None, version=args.version,
                                     ca=None))
    cmd_activar(args)
    cmd_verificar(argparse.Namespace(programa=args.programa, destino=args.destino, url=None, version=None, ca=None))
    return 0


def cmd_restaurar(args):
    historial = os.path.join(args.destino, "historial")
    v = args.version
    if not v:
        previos = sorted(x for x in (os.listdir(historial) if os.path.isdir(historial) else [])
                         if f"_{act.CANAL}_" in x)
        if not previos:
            raise Error("No hay ninguna versión anunciada antes; indica --version.")
        v = previos[-1].rsplit("_", 1)[1][:-5]
    print(f"Se vuelve a anunciar la {v}. OJO: quien ya tenga una posterior NO baja de versión sola (el programa no "
          "acepta bajadas involuntarias); para arreglar una versión mala para todos, prepara una NUEVA (número mayor) "
          "con los archivos buenos.")
    return cmd_activar(argparse.Namespace(programa=args.programa, destino=args.destino, version=v),
                       permitir_anterior=True)


def verificar_url(programa, url, version=None, ca=None, detalle=True, canal=act.CANAL):
    """Descarga por HTTPS el manifiesto (el anunciado en `canal` o el de `version`) y todos sus archivos, y lo
    comprueba todo. Devuelve la versión verificada."""
    claves = _claves_de(programa, version if version and os.path.isdir(
        os.path.join(herramientas(programa), "versiones", version)) else None)
    red = act.Red([url], ca_extra=ca, registro=lambda t: print("  " + t))
    pub, servidor = act.obtener_publicacion(red, claves, version, canal)
    with tempfile.TemporaryDirectory(prefix="verificando-") as d:
        for a in pub.manifiesto["archivos"]:
            red.descargar(f"{servidor}versiones/{pub.version}/archivos/{a['sha256']}",
                          os.path.join(d, a["sha256"] + ".parcial"), a["tamano"], a["sha256"])
            if detalle:
                print(f"  bien: {a['ruta']} ({a['tamano']} bytes)")
    print(f"Verificada por HTTPS la {pub.version} en {url}: firma, manifiesto y {len(pub.manifiesto['archivos'])}"
          " archivos.")
    return pub.version


def _git(carpeta, *orden):
    r = subprocess.run(["git", "-C", carpeta, *orden], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode:
        raise Error(f"git {' '.join(orden)}: {(r.stderr or r.stdout).strip()[-600:]}")
    return r.stdout


def _rel(sitio, ruta):
    return os.path.relpath(ruta, sitio).replace("\\", "/")


def _pendientes(sitio):
    """Lo que hay en el repositorio sin commit (cambiado, nuevo o borrado; sin lo que ignora .gitignore): sus rutas."""
    partes, out, i = _git(sitio, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0"), [], 0
    while i < len(partes):
        if len(partes[i]) > 3:
            out.append(partes[i][3:])
            if partes[i][0] in "RC":            # (renombrado o copiado: detrás va la ruta de antes)
                i += 1
                out.append(partes[i])
        i += 1
    return out


def _confirma(pregunta):
    try:
        return input(pregunta).strip().lower() in ("s", "si", "sí", "y", "yes")
    except EOFError:
        return False


def _subir_a_github(sitio, mensaje, rutas, si=False):
    """Commit con SOLO `rutas` (archivos o carpetas, relativas al repositorio) y push; nada de «git add -A». Antes enseña
    la lista de archivos y, sin `si`, pide que se confirme (si no, no se sube nada). Si se colara algo de fuera de
    esas rutas, no se hace."""
    rutas = sorted({r.replace("\\", "/").rstrip("/") for r in rutas})
    existen = [r for r in rutas if os.path.exists(os.path.join(sitio, r))]
    if existen:
        _git(sitio, "add", "--", *existen)
    lista = [x for x in _git(sitio, "diff", "--cached", "--name-only", "-z").split("\0") if x]
    fuera = [x for x in lista if not any(x == r or x.startswith(r + "/") for r in rutas)]
    if fuera:
        _git(sitio, "reset", "-q")
        raise Error("No se sube: en el commit iría algo que no es de la publicación:\n  " + "\n  ".join(fuera))
    if lista:
        print(f"Commit «{mensaje}» en {sitio} ({len(lista)} archivo{'s' if len(lista) != 1 else ''}):")
        for x in lista:
            print(f"  {x}")
        if not si and not _confirma("¿Hago el commit y lo subo a GitHub? (s/n): "):
            _git(sitio, "reset", "-q")
            raise Error("No se ha subido nada (no confirmado). Para confirmarlo de antemano, tras ver la lista "
                        "(--solo-lista): --si")
        _git(sitio, "commit", "-q", "-m", mensaje)
    _git(sitio, "push", "-q")
    print(f"Subido a GitHub: {mensaje}")


def _rutas_version(destino, sitio, pub, v):
    """Lo que sube cmd_subir de la versión v, relativo al repositorio."""
    base = f"{_rel(sitio, os.path.join(destino, 'versiones', v))}/"
    return ([base + "manifiesto.json"] + sorted({base + "archivos/" + a["sha256"] for a in pub.manifiesto["archivos"]})
            + [base + f"paquete/Sincronizar_Doblaje_{v}.zip"])


def _rutas_anuncio(destino, sitio, canal):
    """Lo que cambia cmd_activar: el puntero del canal, su copia en historial/ y, en el canal de las numeradas, el
    enlace fijo al paquete."""
    return ([_rel(sitio, _puntero(destino, canal)), _rel(sitio, os.path.join(destino, "historial"))]
            + ([_rel(sitio, os.path.join(destino, "Sincronizar_Doblaje.zip"))] if canal == act.CANAL else []))


def _sitio(args):
    # (resuelta como la del programa, en main(): con una abspath y la otra realpath, en Windows una podía quedar con el
    # nombre corto —C:\Users\RUNNER~1, la carpeta temporal de GitHub Actions— y la otra con el largo, y todo src/ salía
    # como «cambios que no son de esta publicación»)
    sitio = os.path.realpath(args.sitio)
    if not os.path.isdir(os.path.join(sitio, ".git")):
        raise Error(f"{sitio} no es la copia del repositorio de GitHub.")
    return sitio


def cmd_github(args):
    """La publicación entera en el repositorio de GitHub: subir, comprobar por HTTPS, anunciar, comprobar que se
    anuncia (GitHub puede tardar hasta 5 minutos en servir el puntero nuevo) y, si es numerada, la etiqueta y la
    Release. Lo ya hecho de una vez anterior se salta, así que repetirlo termina lo que faltara."""
    v = args.version
    canal = canal_de(v)
    sitio = _sitio(args)
    destino = os.path.join(sitio, args.carpeta) if args.carpeta else sitio
    # con el programa en el src/ del repositorio, sus archivos publicados van en el commit de la versión, y tienen que
    # ser los de la versión preparada (byte a byte: los del manifiesto); lo demás, nada sin subir
    lista = leer_lista(args.programa) if act.dentro(sitio, args.programa) else []
    fuentes = [_rel(sitio, o) for o, _ in lista]
    claves = _claves_de(args.programa, v)
    preparada = comprobar_carpeta_version(os.path.join(herramientas(args.programa), "versiones", v), claves, v)
    rutas_2 = _rutas_anuncio(destino, sitio, canal)
    # (lo de una vez anterior de esta misma publicación que no se llegó a subir —se dijo que no a un commit—, se admite:
    # su carpeta de la versión, que cmd_subir comprueba igual, y el anuncio, que se rehace)
    propias = [f"{_rel(sitio, os.path.join(destino, 'versiones', v))}/"] + [r + "/" for r in rutas_2] + rutas_2
    otros = [x for x in _pendientes(sitio) if x not in fuentes and not any(x == r or x.startswith(r) for r in propias)]
    if otros:
        raise Error(f"En {sitio} hay cambios que no son de esta publicación; súbelos (o deshazlos) antes:\n  "
                    + "\n  ".join(otros) + f"\n(el programa: {args.programa}; sus archivos en el repositorio: "
                    + (", ".join(fuentes[:3]) + "..." if fuentes else "ninguno") + ")")
    subida = bool(_git(sitio, "ls-files", "--", _rel(sitio, os.path.join(destino, "versiones", v,
                                                                          "manifiesto.json"))).strip())
    if lista and not subida:
        por_ruta = {a["ruta"]: a["sha256"] for a in preparada.manifiesto["archivos"]}
        distintos = [_rel(sitio, o) for o, r in lista if sha256(o) != por_ruta.get(r)]
        if distintos:
            raise Error(f"No son los de la {v} preparada (¿cambiados después de prepararla?):\n  "
                        + "\n  ".join(distintos))
    rutas_1 = _rutas_version(destino, sitio, preparada, v) + ([] if subida else fuentes)
    if args.solo_lista:
        cambian = set(_pendientes(sitio))
        print(f"1) «Sube la versión {v} (sin anunciar)»" + (" (ya hecho)" if subida else "") + ":")
        for r in rutas_1:
            if r not in fuentes or r in cambian:
                print(f"  {r}")
        print(f"2) «Anuncia la versión {v}»:")
        for r in rutas_2:
            print(f"  {r}" + ("/<fecha>_" + canal + "_<la que se anunciaba>.json" if r.endswith("historial") else ""))
        print("No se ha hecho nada. Para publicarla así: github " + v + " --si")
        return 0
    url = args.url or (leer_constante(os.path.join(args.programa, "actualizador.py"), "SERVIDORES") or [None])[0]
    if not url:
        raise Error("No hay dirección: indica --url o pon SERVIDORES en actualizador.py.")
    cmd_subir(argparse.Namespace(programa=args.programa, destino=destino, version=v))
    _subir_a_github(sitio, f"Sube la versión {v} (sin anunciar)", rutas_1, args.si)
    _con_espera(lambda: verificar_url(args.programa, url, v, detalle=False), 180, f"que GitHub sirva la {v}")
    ya = anunciada_en(destino, canal, claves)
    if ya and ya.version == v:
        print(f"La {v} ya estaba anunciada en {canal}.json.")
    else:
        cmd_activar(argparse.Namespace(programa=args.programa, destino=destino, version=v))
    # (también si ya estaba anunciada: lo que de una vez anterior no se llegó a subir; si no hay nada, solo el push)
    _subir_a_github(sitio, f"Anuncia la versión {v}", rutas_2, args.si)

    def anunciada():                    # solo el manifiesto anunciado; la descarga entera, una vez al final
        red = act.Red([url], registro=lambda t: None)
        hay = act.obtener_publicacion(red, _claves_de(args.programa), canal=canal)[0].version
        if hay != v:
            raise Error(f"GitHub aún anuncia la {hay} (caché)")
    _con_espera(anunciada, 480, f"que GitHub anuncie la {v}")
    verificar_url(args.programa, url, None, detalle=False, canal=canal)
    print(f"Publicada la {v}: las instalaciones la verán en su próxima comprobación (al abrir o cada 6 horas).")
    if act.por_fecha(v):
        print("Es por fecha (para las copias de antes de la 1.0): no lleva etiqueta ni Release.")
        return 0
    return cmd_release(args)


# --- Etiqueta y Release de GitHub (lo que se ve en la página del repositorio) ---

def _gh():
    """gh.exe: en el PATH o donde lo deja su instalador (un PATH viejo no lo ve hasta abrir otra consola)."""
    for c in [shutil.which("gh")] + [os.path.join(os.environ.get(v, ""), "GitHub CLI", "gh.exe")
                                     for v in ("ProgramW6432", "ProgramFiles")] + [
            r"C:\Program Files\GitHub CLI\gh.exe",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "GitHub CLI", "gh.exe")]:
        if c and os.path.isfile(c):
            return c
    return None


def _ejecutar_gh(gh, *orden, entrada=None):
    return subprocess.run([gh, *orden], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          input=entrada, env=dict(os.environ, GH_PROMPT_DISABLED="1", NO_COLOR="1"))


def _repo_de(sitio):
    url = _git(sitio, "remote", "get-url", "origin").strip()
    m = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    if not m:
        raise Error(f"El repositorio de {sitio} no es de GitHub ({url}).")
    return m.group(1)


def _commit_que_anuncia(sitio, v):
    """El commit «Anuncia la versión V» (la etiqueta va ahí); si no está, el último."""
    for linea in _git(sitio, "log", "--format=%H%x09%s", "-n", "500").splitlines():
        h, _, asunto = linea.partition("\t")
        if asunto == f"Anuncia la versión {v}":
            return h
    return _git(sitio, "rev-parse", "HEAD").strip()


def texto_release(v, notas):
    return (f"{notas}\n\n---\n**Instalarlo por primera vez:** descarga `Sincronizar_Doblaje_{v}.zip` (abajo, en "
            "*Assets*), descomprímelo donde quieras y abre «Iniciar Sincronizar Doblaje.bat»; lo que falte (Python, "
            "ffmpeg) lo descarga él la primera vez.\n**Si ya lo tienes:** no hace falta descargar nada; el programa "
            f"lo avisa solo con el botón «Actualizar a la {v}» junto a su número de versión.")


def cmd_release(args):
    """La etiqueta vV en el commit que la anuncia y la Release de GitHub con las notas y el paquete .zip."""
    v = args.version
    if act.por_fecha(v):
        raise Error("Las versiones por fecha no llevan Release (solo las numeradas, de la 1.0 en adelante).")
    sitio = _sitio(args)
    destino = os.path.join(sitio, args.carpeta) if getattr(args, "carpeta", "") else sitio
    claves = _claves_de(args.programa, v)
    ya = anunciada_en(destino, canal_de(v), claves)
    if not ya or act.orden_version(ya.version) < act.orden_version(v):
        raise Error(f"La {v} no está anunciada en {sitio}: publícala antes («github {v}»).")
    carpeta = os.path.join(herramientas(args.programa), "versiones", v)
    comprobar_carpeta_version(os.path.join(destino, "versiones", v), claves, v)
    notas = open(os.path.join(carpeta, "notas.txt"), encoding="utf-8").read().strip()
    paquete = os.path.join(carpeta, "paquete", f"Sincronizar_Doblaje_{v}.zip")
    if not os.path.isfile(paquete):
        raise Error(f"Falta el paquete {paquete}")
    etiqueta = f"v{v}"
    # 1) la etiqueta, con git (sale en GitHub aunque no haya GitHub CLI)
    if not _git(sitio, "tag", "--list", etiqueta).strip():
        _git(sitio, "tag", "-a", etiqueta, "-m", f"Versión {v}", _commit_que_anuncia(sitio, v))
    _git(sitio, "push", "-q", "origin", f"refs/tags/{etiqueta}")
    print(f"Etiqueta {etiqueta} en GitHub.")
    # 2) la Release, con GitHub CLI (la API de GitHub pide una sesión: «gh auth login», una vez)
    repo = _repo_de(sitio)
    gh = _gh()
    if not gh:
        raise Error(f"Publicada la {v} con su etiqueta, pero para la Release hace falta GitHub CLI: instálala "
                    "(«winget install --id GitHub.cli»), entra una vez con «gh auth login» y repite "
                    f"«publicar_version.py release {v}».")
    if _ejecutar_gh(gh, "auth", "status", "--hostname", "github.com").returncode:
        raise Error(f"Publicada la {v} con su etiqueta, pero GitHub CLI no tiene la sesión abierta: entra una vez con "
                    f"«gh auth login» y repite «publicar_version.py release {v}».")
    if _ejecutar_gh(gh, "release", "view", etiqueta, "--repo", repo).returncode == 0:
        print(f"La Release {etiqueta} ya existía: https://github.com/{repo}/releases/tag/{etiqueta}")
        return 0
    with tempfile.TemporaryDirectory(prefix="release-") as d:
        cuerpo = os.path.join(d, "notas.md")
        with open(cuerpo, "w", encoding="utf-8") as f:
            f.write(texto_release(v, notas) + "\n")
        r = _ejecutar_gh(gh, "release", "create", etiqueta, f"{paquete}#Sincronizar_Doblaje_{v}.zip", "--repo", repo,
                         "--verify-tag", "--title", f"Sync Master {v}", "--notes-file", cuerpo, "--latest")
    if r.returncode:
        raise Error(f"No se ha creado la Release {etiqueta}: {(r.stderr or r.stdout).strip()[-600:]}")
    print(f"Release creada: https://github.com/{repo}/releases/tag/{etiqueta}")
    return 0


def _con_espera(funcion, segundos, que):
    fin = time.time() + segundos
    while True:
        try:
            return funcion()
        except (Error, act.ErrorActualizacion) as e:
            if time.time() >= fin:
                raise Error(f"No se ha podido comprobar {que}: {getattr(e, 'mensaje', None) or e}")
            print(f"  Esperando {que}: {getattr(e, 'mensaje', None) or e}")
            time.sleep(20)


def cmd_verificar(args):
    if args.url:
        verificar_url(args.programa, args.url, args.version, args.ca)
        return 0
    claves = _claves_de(args.programa, args.version if args.version and os.path.isdir(
        os.path.join(herramientas(args.programa), "versiones", args.version)) else None)
    if args.version:
        pub = comprobar_carpeta_version(os.path.join(args.destino, "versiones", args.version), claves, args.version)
    else:
        punteros = [c for c in (act.CANAL, act.CANAL_FECHAS) if os.path.exists(_puntero(args.destino, c))]
        if not punteros:
            raise Error(f"En {args.destino} no se anuncia ninguna versión.")
        for canal in punteros:
            pub = anunciada_en(args.destino, canal, claves)
            otra = comprobar_carpeta_version(os.path.join(args.destino, "versiones", pub.version), claves, pub.version)
            if otra.huella != pub.huella:
                raise Error(f"{canal}.json no coincide con el manifiesto de su versión")
            print(f"Verificada en {args.destino} la {pub.version} ({canal}.json): firma, manifiesto y "
                  f"{len(pub.manifiesto['archivos'])} archivos.")
        return 0
    print(f"Verificada en {args.destino} la {pub.version}: firma, manifiesto y {len(pub.manifiesto['archivos'])} "
          "archivos.")
    return 0


def cmd_estado(args):
    claves = _claves_de(args.programa)
    for canal, para in ((act.CANAL, ""), (act.CANAL_FECHAS, " para las copias de antes de la 1.0")):
        pub = anunciada_en(args.destino, canal, claves)
        print(f"Anunciada{para} ({canal}.json): {pub.version if pub else 'ninguna'}")
    carpeta = os.path.join(args.destino, "versiones")
    for v in sorted((x for x in (os.listdir(carpeta) if os.path.isdir(carpeta) else [])
                     if re.fullmatch(r"\d+(\.\d+){0,3}", x)), key=act.orden_version):
        print(f"Subida: {v}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--programa", default=PROGRAMA, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="orden", required=True)
    p = sub.add_parser("claves", help="crea la clave de firma (una sola vez)")
    p.add_argument("--guardar", help=f"dónde guardarla (o la variable de entorno {VARIABLE_CLAVE}); fuera del programa "
                                     "y del repositorio")
    p.add_argument("--instalar", action="store_true", help="pone la clave pública en actualizador.py")
    p = sub.add_parser("configurar", help="pone los servidores HTTPS en actualizador.py")
    p.add_argument("--servidor", action="append", required=True)
    p = sub.add_parser("preparar")
    p.add_argument("version")
    p.add_argument("--notas", required=True)
    p.add_argument("--clave", help=f"la clave privada de firma (o la variable de entorno {VARIABLE_CLAVE})")
    p.add_argument("--actualizable-desde", default=ACTUALIZABLE_DESDE)
    p.add_argument("--python-min", default=PYTHON_MIN)
    p.add_argument("--zip-compartir", action="store_true",
                   help="copia también el paquete a Sincronizar_Doblaje_compartir.zip")
    p.add_argument("--por-fecha", action="store_true",
                   help="admite un número por fecha (AAAA.MM.DD), solo para las copias de antes de la 1.0")
    p.add_argument("--pruebas", action="store_true", help=argparse.SUPPRESS)
    p = sub.add_parser("github", help="publicación entera en el repositorio de GitHub (el clon, o publicacion\\sitio), "
                                      "con su etiqueta y su Release")
    p.add_argument("version")
    p.add_argument("--sitio", default=SITIO, help="copia local del repositorio")
    p.add_argument("--carpeta", default="", help=argparse.SUPPRESS)
    p.add_argument("--url", help="dirección HTTPS desde la que lo descargan (por defecto, la de SERVIDORES)")
    p.add_argument("--solo-lista", action="store_true",
                   help="solo enseña lo que irá en cada commit, sin hacer nada")
    p.add_argument("--si", action="store_true",
                   help="sin preguntar antes de cada commit (después de haber visto la lista con --solo-lista)")
    p = sub.add_parser("release", help="solo la etiqueta y la Release de GitHub de una versión ya publicada")
    p.add_argument("version")
    p.add_argument("--sitio", default=SITIO, help="copia local del repositorio")
    p.add_argument("--carpeta", default="", help=argparse.SUPPRESS)
    for nombre in ("subir", "activar", "publicar"):
        p = sub.add_parser(nombre)
        p.add_argument("version")
        p.add_argument("--destino", required=True)
    p = sub.add_parser("verificar")
    p.add_argument("--destino")
    p.add_argument("--url")
    p.add_argument("--version")
    p.add_argument("--ca", help=argparse.SUPPRESS)
    p = sub.add_parser("restaurar")
    p.add_argument("--destino", required=True)
    p.add_argument("--version")
    p = sub.add_parser("estado")
    p.add_argument("--destino", required=True)
    args = ap.parse_args(argv)
    args.programa = os.path.realpath(args.programa)
    if args.orden == "verificar" and not (args.destino or args.url):
        ap.error("indica --destino o --url")
    try:
        return {"claves": cmd_claves, "configurar": cmd_configurar, "preparar": cmd_preparar, "subir": cmd_subir,
                "activar": cmd_activar, "publicar": cmd_publicar, "verificar": cmd_verificar,
                "restaurar": cmd_restaurar, "estado": cmd_estado, "github": cmd_github,
                "release": cmd_release}[args.orden](args)
    except (Error, act.ErrorActualizacion, ValueError) as e:
        print(f"ERROR: {getattr(e, 'mensaje', None) or e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
