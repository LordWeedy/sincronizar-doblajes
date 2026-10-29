# Sync Master · actualizaciones

Canal de actualizaciones de Sync Master (sincroniza doblajes con la RAW; antes se llamaba Sincronizar Doblaje).

**Versiones:** van numeradas (1.0, 1.1, 1.2...). Cada una tiene su página en
[Releases](https://github.com/LordWeedy/sincronizar-doblajes/releases), con sus novedades y su paquete .zip.

**Para instalarlo por primera vez** (o si tu copia es anterior al 25-10-2026 y no tiene el botón «Actualizar»):
descarga el .zip de la [última versión](https://github.com/LordWeedy/sincronizar-doblajes/releases/latest)
(o [Sincronizar_Doblaje.zip](https://github.com/LordWeedy/sincronizar-doblajes/raw/main/Sincronizar_Doblaje.zip),
que es el mismo), descomprímelo donde quieras (o encima de la carpeta de antes: tus ajustes y lo descargado se
quedan) y abre «Iniciar Sincronizar Doblaje.bat».

A partir de ahí no hay que descargar nada de aquí a mano: el programa lo consulta solo y, cuando hay una versión
nueva, sale el botón «Actualizar a la …» junto a su número de versión.

Contenido:
- `actual.json`: el manifiesto firmado (Ed25519) de la versión actual.
- `estable.json`: el de las copias de antes de la 1.0 (numeradas por fecha, como la 2026.10.25), que solo leen este:
  les ofrece la 2026.10.26, el paso a la numeración nueva, y desde ella la versión actual.
- `versiones/<versión>/`: cada versión publicada (manifiesto firmado, archivos con su SHA-256 como nombre y el
  paquete .zip). Una versión publicada no se cambia nunca.
- `historial/`: los manifiestos anunciados antes.
- `Sincronizar_Doblaje.zip`: el paquete completo de la versión actual.
