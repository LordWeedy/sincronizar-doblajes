# Sincronizar Doblaje · actualizaciones

Canal de actualizaciones del programa Sincronizar Doblaje.

**Para instalarlo por primera vez** (o si tu copia es anterior al 25-10-2026 y no tiene el botón «Actualizar»):
descarga [Sincronizar_Doblaje.zip](https://github.com/LordWeedy/sincronizar-doblajes/raw/main/Sincronizar_Doblaje.zip),
descomprímelo donde quieras (o encima de la carpeta de antes: tus ajustes y lo descargado se quedan) y abre
«Iniciar Sincronizar Doblaje.bat».

A partir de ahí no hay que descargar nada de aquí a mano: el programa lo consulta solo y, cuando hay una versión
nueva, sale el botón «Actualizar a la …» junto a su número de versión.

Contenido:
- `estable.json`: el manifiesto firmado (Ed25519) de la versión actual.
- `versiones/<versión>/`: cada versión publicada (manifiesto firmado, archivos con su SHA-256 como nombre y el
  paquete .zip). Una versión publicada no se cambia nunca.
- `historial/`: los manifiestos anunciados antes.
- `Sincronizar_Doblaje.zip`: el paquete completo de la versión actual.
