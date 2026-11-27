"""Subtítulos forzados de lo que va en versión original: los trozos que el sincronizador rellena con el audio del
original (el castellano no los tiene) se transcriben con faster-whisper y se traducen al castellano con opus-mt
(ctranslate2, el mismo motor que faster-whisper). Es opcional: si falta alguna pieza, se avisa y no se hace nada.

Piezas: faster-whisper (trae ctranslate2 y huggingface_hub) y sentencepiece. El traductor es el oficial de
Helsinki-NLP (opus-mt-tc-big-<idioma>-es, o opus-mt-<idioma>-es si no hay el grande); la primera vez se descarga y
se convierte a int8 (hace falta transformers, con torch, solo para esa conversión) y se guarda en
~/.cache/sincronizar_doblaje. De otro idioma sin traductor directo, Whisper lo pasa al inglés y de ahí al castellano.

Solo vale lo que se oye: cada trozo se transcribe suelto (sin lo de al lado, que es castellano), con el filtro de voz
de Whisper, y fuera lo que Whisper se inventa en el silencio o el ruido («You», «Thanks for watching!»...)."""
import os, re, shutil, subprocess, tempfile

VERSION = '1.4.22'
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "sincronizar_doblaje")
WHISPER = "medium"          # small: unas 3 veces más rápido y algo peor; nunca large (demasiado para estos equipos)
MAX_LINEA = 42              # caracteres por línea del subtítulo, dos líneas como mucho
MAX_DUR = 6.5               # s que dura un subtítulo como mucho
MIN_DUR = 1.0               # ...y como poco (si cabe antes del siguiente)
HUECO = 0.8                 # s sin palabras: otro subtítulo
COLA = 0.4                  # s que sigue en pantalla después de la última palabra (si cabe)
BORDE = 0.12                # s: las palabras con el centro tan cerca del borde del trozo son del castellano de al lado
INVENTOS = re.compile(r"(?i)^\W*(?:you|thank you|thanks for watching|thank you for watching|please subscribe|"
                      r"subtitles? by.*|subtitled by.*|transcri(?:bed|ption) by.*|amara\.org.*|bye|oh|uh|um|hmm|mm)"
                      r"\W*$")
# (en castellano, lo que se inventa en la música: «Subtítulos realizados por la comunidad de Amara.org», «subtítulos en
# español de la Iglesia de Jesucristo de los Santos de los Últimos Días»; 1x01 37:02-37:07 del doblaje)
INVENTOS_ES = re.compile(r"(?i)subt[ií]tul|amara\.org|iglesia de jesucristo|suscr[ií]b|gracias por ver")
# (1.4.20) lo que se inventa en la música con frases de vídeos de internet, que lo de arriba no pilla (mal escrito o
# dicho de otra forma; Crash Canyon 1x01, en la canción de la cabecera: «Y nos vemos en el próximo vídeo, ¡hasta la
# próxima!», «¡Scribete y lláete y haz un spazm!»): fuera solo si además el detector de voz de Whisper (Silero) no oye
# voz ahí (sin_coletillas). Con él solo no vale: no oye voz en las canciones, las voces de robot ni lo dicho bajo los efectos
# (unas 170 palabras de verdad en 8 trozos de 3 min de los finales), y lo que no da Whisper se toma por pausa
COLETILLAS = re.compile(r"(?i)pr[oó]xim[oa] v[ií]deo|hasta la pr[oó]xima|nos vemos en (?:el|la|los) pr[oó]xim|"
                        r"\b(?:sus)?s?cr[ií]b[ae]te\b|dale (?:a |un )?like|\blike y |activa la campan|"
                        r"comparte (?:el|este) v[ií]deo|deja (?:tu|un) comentario|gracias por (?:ver|vernos|mirar)|"
                        r"thanks? (?:you )?for watching|see you (?:in|on) the next (?:video|one|episode)|"
                        r"(?:like|please) (?:and )?subscribe|hit the bell|don'?t forget to (?:like|subscribe)|"
                        r"leave a comment")


def _voz_silero(x16):
    """[(desde, hasta)] (s) de lo que el detector de voz de Whisper (Silero) da por voz en x16 (mono a 16 kHz), con su
    margen de siempre; sin él, todo (como si hubiera voz: no se quita nada)."""
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        return [(v["start"] / 16000, v["end"] / 16000)
                for v in get_speech_timestamps(x16, VadOptions(min_silence_duration_ms=200))]
    except Exception:
        return [(0.0, len(x16) / 16000)]


def sin_coletillas(segs, x16, voz=_voz_silero):
    """Los segmentos de Whisper sin los de COLETILLAS en los que no hay voz (voz(x16): [(desde, hasta)] en s): tienen que
    decirlo las dos cosas. El detector solo se pasa si alguno tiene una coletilla."""
    habla, out = None, []
    for s in segs:
        if COLETILLAS.search(s.text or ""):
            if habla is None:
                habla = voz(x16)
            if not any(a < s.end and b > s.start for a, b in habla):
                continue
        out.append(s)
    return out


def piezas():
    """None si está todo para hacer los forzados de la VO; si no, qué falta (para el aviso)."""
    falta = []
    for m, pip in (("faster_whisper", "faster-whisper"), ("ctranslate2", "ctranslate2"),
                   ("sentencepiece", "sentencepiece"), ("huggingface_hub", "huggingface_hub")):
        try:
            __import__(m)
        except Exception:
            falta.append(pip)
    return f"falta instalar {', '.join(falta)} (pip install {' '.join(falta)})" if falta else None


def _carpeta_traductor(src):
    return os.path.join(CACHE, f"opus-mt-{src}-es-int8")


def preparar_traductor(src="en", p=print):
    """La carpeta del traductor src -> castellano (ctranslate2 int8, con source.spm y target.spm), descargándolo y
    convirtiéndolo la primera vez; None si no hay traductor directo para ese idioma (o no se puede preparar: se avisa)."""
    destino = _carpeta_traductor(src)
    if os.path.exists(os.path.join(destino, "model.bin")):
        return destino
    from huggingface_hub import HfApi, snapshot_download
    repo = None
    for r in (f"Helsinki-NLP/opus-mt-tc-big-{src}-es", f"Helsinki-NLP/opus-mt-{src}-es"):
        try:
            ficheros = [s.rfilename for s in HfApi().model_info(r).siblings]
        except Exception:
            continue
        if "source.spm" in ficheros and "target.spm" in ficheros:
            repo = r
            break
    if repo is None:
        return None
    try:
        import ctranslate2.converters, transformers     # noqa: F401 (solo para convertirlo, una vez)
    except Exception:
        raise RuntimeError("para preparar el traductor la primera vez hace falta transformers y torch "
                           "(pip install transformers torch); luego ya no")
    pesos = "model.safetensors" if "model.safetensors" in ficheros else "pytorch_model.bin"
    p(f"Forzados de la VO: preparando el traductor {repo} (solo la primera vez: se descarga unos "
      f"{'470' if 'tc-big' in repo else '310'} MB y se convierte)...")
    os.makedirs(CACHE, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="opus_", dir=CACHE) as tmp:
        bajado = snapshot_download(repo, local_dir=os.path.join(tmp, "hf"),
                                   allow_patterns=["config.json", "generation_config.json", "*.spm", "vocab.json",
                                                   "tokenizer_config.json", "special_tokens_map.json", pesos])
        nuevo = os.path.join(tmp, "ct2")

        class Conversor(ctranslate2.converters.TransformersConverter):
            def load_model(self, model_class, model_name_or_path, **kwargs):
                # ctranslate2 4.7+ pasa dtype=, que transformers antes de la 4.56 llama torch_dtype
                if "dtype" in kwargs and tuple(int(x) for x in re.findall(r"\d+", transformers.__version__)[:2]) \
                        < (4, 56):
                    kwargs["torch_dtype"] = kwargs.pop("dtype")
                return model_class.from_pretrained(model_name_or_path, **kwargs)

        Conversor(bajado).convert(nuevo, quantization="int8")
        for f in ("source.spm", "target.spm"):
            shutil.copy(os.path.join(bajado, f), nuevo)
        with open(os.path.join(nuevo, "origen.txt"), "w", encoding="utf-8") as f:
            f.write(f"{repo}\n")
        if os.path.exists(destino):
            shutil.rmtree(destino)
        shutil.move(nuevo, destino)
    return destino


class Traductor:
    def __init__(self, carpeta):
        import ctranslate2, sentencepiece
        self.tr = ctranslate2.Translator(carpeta, device="cpu", compute_type="int8")
        self.sp_src = sentencepiece.SentencePieceProcessor(model_file=os.path.join(carpeta, "source.spm"))
        self.sp_tgt = sentencepiece.SentencePieceProcessor(model_file=os.path.join(carpeta, "target.spm"))

    def __call__(self, frases):
        if not frases:
            return []
        lotes = [self.sp_src.encode(f, out_type=str) + ["</s>"] for f in frases]
        res = self.tr.translate_batch(lotes, beam_size=4, max_decoding_length=256)
        return [self.sp_tgt.decode([t for t in r.hypotheses[0] if t != "</s>"]).strip() for r in res]


def _audio(ruta, t0, t1, wav):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(t0, 0.0):.3f}", "-t", f"{t1 - t0:.3f}", "-i", ruta,
                    "-map", "0:a:0", "-ac", "1", "-ar", "16000", wav], check=True)


def _vale(seg):
    """¿Es voz de verdad o un invento de Whisper en el silencio o el ruido?"""
    texto = seg.text.strip()
    if not re.search(r"\w", texto) or INVENTOS.match(texto) or INVENTOS_ES.search(texto):
        return False
    if seg.avg_logprob < -1.0 or seg.compression_ratio > 2.4:
        return False
    # (lo inventado trae las palabras amontonadas en un instante, sin duración: «realizados por la comunidad...» todas
    # en 37:05,64)
    ws = [w for w in (seg.words or []) if w.word.strip()]
    if len(ws) >= 3 and sum(w.end - w.start < 0.02 for w in ws) > len(ws) / 2:
        return False
    return not (seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.5)


_MODELOS = {}


def modelo_whisper(nombre):
    """El modelo de Whisper (int8, en la CPU), cargado una sola vez por proceso."""
    if nombre not in _MODELOS:
        from faster_whisper import WhisperModel
        _MODELOS[nombre] = WhisperModel(nombre, device="cpu", compute_type="int8")
    return _MODELOS[nombre]


REPASO_HUECO = 1.0          # s sin palabras (también al principio o al final) que se vuelven a escuchar solos...
REPASO_SILENCIO = -45.0     # ...salvo si no pasan de estos dBFS (silencio: ahí no hay voz que saltarse)
REPASO_VUELTAS = 1          # repasos (dentro de un repaso, otro...): uno basta y cada uno cuesta lo que una escucha


def _escuchar(x16, idioma, modelo):
    segs, _ = modelo_whisper(modelo).transcribe(x16, language=idioma, word_timestamps=True, vad_filter=False,
                                                condition_on_previous_text=False, beam_size=5)
    return [(w.word.strip(), float(w.start), float(w.end)) for s in sin_coletillas([s for s in segs if _vale(s)], x16)
            for w in (s.words or []) if w.word.strip()]


def palabras(x16, idioma=None, modelo="small", vueltas=REPASO_VUELTAS):
    """[(palabra, inicio, fin)] (s desde el principio de x16: audio mono a 16 kHz, float32) que dice Whisper, sin lo que
    se inventa en el silencio o el ruido. Para los cortes del sincronizador: dónde acaba de verdad una frase (la
    puntuación de Whisper), que el nivel de la voz no distingue de un respiro dentro de ella.
    Whisper a veces se salta voz, y lo que no da se toma por pausa (El último superviviente 1x02, 23:18-23:44 del
    doblaje: el trozo empezaba a media palabra, daba «...haga menos calor.» y nada de «Me han dejado en medio del
    desierto... de los Estados Unidos.», y el recorte partía «Estados»): cada hueco sin palabras de REPASO_HUECO s o más
    que no es silencio se vuelve a escuchar solo, y lo que se oiga ahí cuenta (sin lo pegado a una palabra de al lado,
    que sería un trozo de ella)."""
    import numpy as np
    x16 = np.asarray(x16, np.float32)
    out = _escuchar(x16, idioma, modelo)
    dur = len(x16) / 16000
    if vueltas <= 0 or dur < REPASO_HUECO:
        return out
    marcas = [0.0] + [t for _, s, e in out for t in (s, e)] + [dur]
    nuevas = []
    for a, b in zip(marcas[0::2], marcas[1::2]):
        if b - a < REPASO_HUECO:
            continue
        z = x16[int(a * 16000):int(b * 16000)]
        n = len(z) // 320
        if n < 1 or 10 * np.log10(float((z[:n * 320].reshape(n, 320) ** 2).mean(1).max()) + 1e-12) < REPASO_SILENCIO:
            continue
        for w, s, e in palabras(z, idioma, modelo, vueltas - 1):
            centro = a + (s + e) / 2
            if (a > 0 and centro < a + BORDE) or (b < dur and centro > b - BORDE):
                continue
            nuevas.append((w, a + s, a + e))
    return sorted(out + nuevas, key=lambda w: w[1]) if nuevas else out


def _palabras(modelo, wav, idioma, tarea):
    segs, info = modelo.transcribe(wav, language=idioma, task=tarea, word_timestamps=True, vad_filter=True,
                                   vad_parameters=dict(min_silence_duration_ms=500), condition_on_previous_text=False,
                                   beam_size=5)
    return [(w.word.strip(), w.start, w.end, w.probability) for s in segs if _vale(s) for w in (s.words or [])
            if w.word.strip()], info


def _frases(pal, t0, t1, sigue_antes, sigue_despues):
    """Palabras [(texto, ini, fin, prob)] (en el tiempo del resultado) de un trozo [t0, t1] -> subtítulos
    [(ini, fin, texto)] en el idioma de origen: un subtítulo por frase, partida si es larga o tiene un hueco."""
    cues, cur = [], []
    for w in pal:
        if cur and (w[1] - cur[-1][2] > HUECO or w[2] - cur[0][1] > MAX_DUR or
                    (re.search(r"[.?!]$", cur[-1][0]) and len(" ".join(x[0] for x in cur)) >= 20) or
                    (re.search(r"[,;:]$", cur[-1][0]) and len(" ".join(x[0] for x in cur)) >= 60)):
            cues.append(cur)
            cur = []
        cur.append(w)
    if cur:
        cues.append(cur)
    out = []
    for c in cues:
        texto = re.sub(r"\s+", " ", " ".join(x[0] for x in c)).strip()
        out.append([c[0][1], c[-1][2], texto])
    if out and sigue_antes and out[0][2][:1].islower():
        out[0][2] = "..." + out[0][2]          # el trozo empieza a media frase
    if out and sigue_despues and not re.search(r"[.?!…]$", out[-1][2]):
        out[-1][2] = out[-1][2].rstrip(",;:") + "..."
    return out


def _partir(texto):
    """El texto en una o dos líneas de MAX_LINEA o menos, cortando lo más al medio posible (mejor tras un signo); o
    None si no cabe en dos."""
    if len(texto) <= MAX_LINEA:
        return texto
    if len(texto) > 2 * MAX_LINEA + 1:
        return None
    mejor = None
    for m in re.finditer(r" ", texto):
        a, b = texto[:m.start()], texto[m.end():]
        if len(a) > MAX_LINEA or len(b) > MAX_LINEA:
            continue
        coste = abs(len(a) - len(b)) - (12 if re.search(r"[,.;:?!]$", a) else 0)
        if mejor is None or coste < mejor[0]:
            mejor = (coste, f"{a}\n{b}")
    return mejor[1] if mejor else None


def _repartir(texto, largos):
    """La traducción de una frase en tantos trozos como subtítulos tenía en el original, en la proporción de sus largos
    (largos: caracteres de cada uno), cortando en un espacio (mejor tras un signo, a ±15 caracteres del sitio)."""
    if len(largos) == 1:
        return [texto]
    out, resto = [], texto
    for i, n in enumerate(largos[:-1]):
        quedan = sum(largos[i:])
        ideal = len(resto) * n / max(quedan, 1)
        espacios = [m.start() for m in re.finditer(r" ", resto)]
        if not espacios:
            break
        signos = [j for j in espacios if re.search(r"[,.;:?!]$", resto[:j]) and abs(j - ideal) <= 15]
        j = min(signos or espacios, key=lambda j: abs(j - ideal))
        out.append(resto[:j].strip())
        resto = resto[j:].strip()
    out.append(resto)
    return out + [""] * (len(largos) - len(out))


def _en_lineas(ini, fin, texto):
    """[(ini, fin, texto en líneas)]: lo que no cabe en dos líneas, en varios subtítulos (el tiempo, por caracteres)."""
    lineas = _partir(texto)
    if lineas is not None:
        return [(ini, fin, lineas)]
    pal = texto.split(" ")
    corte = len(pal) // 2
    cerca = [i for i in range(1, len(pal)) if re.search(r"[,.;:?!]$", pal[i - 1])]
    if cerca:
        corte = min(cerca, key=lambda i: abs(i - len(pal) / 2))
    a, b = " ".join(pal[:corte]), " ".join(pal[corte:])
    t = ini + (fin - ini) * len(a) / max(len(texto), 1)
    return _en_lineas(ini, t, a) + _en_lineas(t, fin, b)


def srt_tiempo(t):
    ms = int(round(max(t, 0.0) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def crear(audio, trozos, ruta_srt, modelo=WHISPER, p=print, juntar=False):
    """Los forzados de lo que va en VO: audio (el resultado del sincronizador, o cualquier archivo con ese audio en su
    primera pista), trozos [(desde, hasta)] (s del archivo) que llevan el audio original con su voz. Escribe ruta_srt
    (UTF-8 con BOM, como los de OCR) si sale algo; con juntar, junto a lo que ya tenga (los del OCR de esta vez).
    Devuelve (subtítulos [(ini, fin, texto)], avisos)."""
    falta = piezas()
    if falta:
        return [], [f"forzados de la VO: {falta}"]
    trozos = sorted((float(a), float(b)) for a, b in trozos if b - a >= 0.5)
    if not trozos:
        return [], []
    total = sum(b - a for a, b in trozos)
    p(f"Forzados de la VO: transcribiendo {len(trozos)} trozo{'s' if len(trozos) != 1 else ''} en versión original "
      f"({total / 60:.1f} min) con "
      f"Whisper {modelo} (en la CPU tarda más o menos lo que dura el audio con medium, y un tercio con small)...")
    wm = modelo_whisper(modelo)
    subs_src, avisos = [], []
    with tempfile.TemporaryDirectory(prefix="forzados_vo_") as tmp:
        wavs = []
        for i, (a, b) in enumerate(trozos):
            w = os.path.join(tmp, f"{i}.wav")
            _audio(audio, a, b, w)
            wavs.append(w)
        # el idioma, por cómo suena (no por la etiqueta): el que más dice Whisper en los trozos largos, por su duración
        votos = {}
        for (a, b), w in sorted(zip(trozos, wavs), key=lambda x: x[0][0] - x[0][1])[:4]:
            _, info = wm.transcribe(w, vad_filter=True, without_timestamps=True)
            votos[info.language] = votos.get(info.language, 0.0) + (b - a) * info.language_probability
        idioma = max(votos, key=votos.get)
        if idioma == "es":
            p("Forzados de la VO: lo que va en VO ya suena en castellano; no hacen falta")
            return [], []
        try:
            carpeta = preparar_traductor(idioma, p)
        except Exception as e:
            return [], [f"forzados de la VO: no se ha podido preparar el traductor ({e})"]
        tarea, origen = "transcribe", idioma
        if carpeta is None:             # sin traductor directo: Whisper lo pasa al inglés
            try:
                carpeta = preparar_traductor("en", p)
            except Exception as e:
                return [], [f"forzados de la VO: no se ha podido preparar el traductor ({e})"]
            tarea, origen = "translate", "en"
            avisos.append(f"forzados de la VO: no hay traductor directo del idioma «{idioma}» al castellano; "
                          f"va pasando por el inglés (peor)")
        for (a, b), w in zip(trozos, wavs):
            pal, _ = _palabras(wm, w, idioma, tarea)
            pal = [(x, a + s, a + e, pr) for x, s, e, pr in pal if BORDE <= (s + e) / 2 <= (b - a) - BORDE]
            if not pal:
                continue
            subs_src += [(s, e, x) for s, e, x in
                         _frases(pal, a, b, pal[0][1] - a < 0.3, b - pal[-1][2] < 0.3)]
    if not subs_src:
        p("Forzados de la VO: en esos trozos no se entiende ninguna frase")
        return [], avisos
    trad = Traductor(carpeta)
    # se traduce cada frase entera (la que va en dos o tres subtítulos seguidos, juntos: suelto, cada trozo se traducía
    # mal, «Una de las cosas que estoy encontrando difícil es / Sólo falta compañía») y la traducción se reparte entre
    # ellos; los «...» de un trozo que empieza o acaba a media frase, fuera para traducir (con ellos traduce peor) y
    # luego otra vez
    grupos = []
    for i, c in enumerate(subs_src):
        if grupos and not re.search(r"[.?!…]$", subs_src[i - 1][2]) and c[0] - subs_src[i - 1][1] <= HUECO and \
                len(grupos[-1]) < 3 and not c[2].startswith("..."):
            grupos[-1].append(c)
        else:
            grupos.append([c])
    frases = [" ".join(x for _, _, x in g) for g in grupos]
    textos = []
    for g, es in zip(grupos, trad([f.strip(".").strip() if f.startswith("...") or f.endswith("...") else f
                                   for f in frases])):
        textos += _repartir(es, [len(x) for _, _, x in g])
    subs = []
    for (s, e, x), es in zip(subs_src, textos):
        if not es.strip():
            continue
        if x.startswith("..."):
            es = "..." + es.lstrip(".¡¿ ")[:1].lower() + es.lstrip(".¡¿ ")[1:] if not es.startswith(("¿", "¡")) \
                else "..." + es
        if x.endswith("...") and not es.endswith("..."):
            es = es.rstrip(".,;: ") + "..."
        subs += _en_lineas(s, e, es)
    # tiempos: un mínimo para leerlo y un poco de cola, sin pisar el siguiente ni salirse del trozo
    final, n = [], len(subs)
    for i, (s, e, x) in enumerate(subs):
        sig = subs[i + 1][0] - 0.05 if i + 1 < n else float("inf")
        fin_trozo = next((b for a, b in trozos if a - 0.01 <= s <= b + 0.01), e)
        e2 = min(max(e + COLA, s + MIN_DUR), sig, max(fin_trozo, e))
        final.append((s, max(e2, e), x))
    otros = leer_srt(ruta_srt) if juntar and os.path.exists(ruta_srt) else []
    with open(ruta_srt, "w", encoding="utf-8-sig", newline="\r\n") as f:
        for i, (s, e, x) in enumerate(sorted(otros + final), 1):
            f.write(f"{i}\n{srt_tiempo(s)} --> {srt_tiempo(e)}\n{x}\n\n")
    origen_txt = open(os.path.join(carpeta, "origen.txt"), encoding="utf-8").read().strip()
    p(f"Forzados de la VO: {len(final)} subtítulos (Whisper {modelo}, idioma «{idioma}»; traducidos con "
      f"{origen_txt}" + (f"; junto a los {len(otros)} del OCR" if otros else "") + f") -> {ruta_srt}")
    for s, e, x in final:
        p(f"Forzado VO {_mmss(s):>9}-{_mmss(e)}  {x.replace(chr(10), ' | ')}")
    return final, avisos


def _mmss(t):
    t = round(float(t), 2)
    return f"{int(t // 60)}:{t % 60:05.2f}"


def leer_srt(ruta):
    """[(ini, fin, texto)] de un .srt (UTF-8, con o sin BOM)."""
    texto = open(ruta, encoding="utf-8-sig").read().replace("\r\n", "\n")
    seg = lambda h, m, s, ms: int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    return [(seg(*m.groups()[:4]), seg(*m.groups()[4:8]), m[9].strip("\n")) for m in
            re.finditer(r"(\d+):(\d+):(\d+)[,.](\d+) --> (\d+):(\d+):(\d+)[,.](\d+)[^\n]*\n(.*?)(?:\n\n|\n*$)", texto,
                        re.S)]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("audio")
    ap.add_argument("srt")
    ap.add_argument("trozos", nargs="+", help="desde-hasta en segundos (p. ej. 1028.4-1053.2)")
    ap.add_argument("--modelo", default=WHISPER, choices=("small", "medium"))
    ap.add_argument("--preparar", action="store_true", help="solo prepara el traductor (inglés)")
    a = ap.parse_args()
    if a.preparar:
        print(preparar_traductor("en"))
    else:
        subs, av = crear(a.audio, [tuple(float(v) for v in t.split("-")) for t in a.trozos], a.srt, a.modelo)
        for x in av:
            print("Aviso:", x)
