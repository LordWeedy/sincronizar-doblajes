# Cómo contribuir a Sync Master

Este repositorio es dos cosas a la vez:

- **El canal de actualizaciones firmadas** que descargan los programas instalados: `actual.json`, `estable.json`,
  `versiones/`, `historial/` y `Sincronizar_Doblaje.zip`.
- **El código fuente**, en [`src/`](src/).

## El código

Todo el programa está en `src/`:

| Archivo | Qué es |
| --- | --- |
| `Sincronizar_Doblaje_Gui.py` | La ventana del programa (tkinter). |
| `sincronizar_doblaje.py` | El motor: mide, corta, rellena y muxea. Se puede usar solo, por línea de órdenes. |
| `limpiar_vhs.py` | Limpieza del ruido de las cintas VHS. |
| `forzados_vo.py` | Transcripción y traducción con Whisper (forzados de lo que va en versión original). |
| `actualizador.py`, `actualizador_ventana.py` | Las actualizaciones firmadas y su ventana. |
| `probar_sincronizador.py` | La batería de pruebas. |
| `Iniciar Sincronizar Doblaje.bat`, `Sync_Master.ico`, `LEEME.txt` | El arranque, el icono y las instrucciones del paquete. |

En `herramientas/` están las herramientas de publicación: `publicar_version.py` (prepara, firma y publica las versiones;
solo la usa el autor), `archivos_publicados.txt` (qué archivos van en cada versión) y `probar_actualizador.py` (la
batería del actualizador).

Los comentarios y los mensajes del programa van en español.

## Probarlo

Hace falta Windows, Python 3.9 o posterior (se prueba con 3.12), `ffmpeg` y `ffprobe` en el PATH, y:

```
python -m pip install numpy scipy
```

Opcional: `faster-whisper` (cortes y comprobaciones con Whisper, forzados de la versión original) y `tkinterdnd2`
(arrastrar archivos a la ventana).

- **Pruebas sin vídeos** (funciones del motor; unos minutos):

  ```
  python src/probar_sincronizador.py --sin-videos
  ```

- **La batería completa** fabrica sus casos a partir de vídeos de prueba que no se reparten. Se le indica la carpeta con
  `--carpeta` o con la variable de entorno `SYNC_MASTER_PRUEBAS`. El autor la pasa entera antes de publicar cada
  versión.
- **La batería del actualizador** (unos 20 minutos; necesita `openssl`, el de Git para Windows vale): monta servidores,
  claves y certificados de prueba en una carpeta temporal, sin tocar nada tuyo.

  ```
  python herramientas/probar_actualizador.py
  ```

- **En GitHub:** cada pull request que toque `src/` pasa solo las pruebas sin vídeos y la batería del actualizador, en
  la pestaña *Actions*.
- **Abrir el programa:** `python src/Sincronizar_Doblaje_Gui.py`. Una copia dentro de un clon de git cuenta como
  copia de desarrollo: no busca actualizaciones sola, así que no pisa tus cambios.

## Pull requests

- Cambia solo `src/` y, si hace falta, `README.md` o `CONTRIBUTING.md`.
- **No toques** `versiones/`, `historial/`, `actual.json`, `estable.json`, `Sincronizar_Doblaje.zip` ni
  `.gitattributes`: son las actualizaciones que descargan las instalaciones, y una versión publicada no se cambia
  nunca.
- **Tampoco** `herramientas/` ni `.github/`: son la publicación firmada y las pruebas automáticas. Si crees que hace
  falta un cambio ahí, ábrelo como *issue*.
- No subas ajustes, registros, rutas de tu equipo ni archivos de trabajo. El `.gitignore` ya deja fuera lo que crea el
  programa al usarlo.
- Explica qué cambia y por qué; si arreglas un fallo, cómo se reproduce. Antes, pasa las pruebas sin vídeos.
- El código se publica con la licencia [GPL-3.0](LICENSE): al contribuir, aceptas que tu cambio vaya con ella.

## Publicar

Las versiones las prepara, firma y publica **solo el autor**, en su equipo: la clave de firma no está en el
repositorio ni en las herramientas (`publicar_version.py` la recibe con `--clave` o con la variable de entorno
`SYNC_MASTER_CLAVE`, sin ruta por defecto). Un cambio aceptado sale en la siguiente versión numerada, y las instalaciones lo reciben con su
actualizador.
