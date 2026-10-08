# Traductor en vivo en la PC usando tu AWS (sin Python en la PC)

La PC solo **captura el audio y dibuja el overlay**. Todo lo pesado (Deepgram, Groq, voz del doblaje) y **tus claves** quedan en tu servidor de AWS.

```
 PC (Windows)                               AWS (tu instancia, junto al bot)
 ┌───────────────────────┐   túnel SSH      ┌────────────────────────────────────┐
 │ TraductorCliente.exe  │  (cifrado)       │ live_server.py (servicio propio)   │
 │ • captura audio WASAPI├─────────────────►│ • Deepgram nova-3 (transcribe)     │
 │ • overlay + 🎨 + F11  │  audio 16 kHz    │ • Groq/Google (traduce, tono,      │
 │ • reproduce el doblaje│◄─────────────────┤   glosario, contexto)              │
 │ (sin Python, sin      │  subtítulos+voz  │ • edge-tts (voz del doblaje)       │
 │  modelos, sin claves) │                  │ • claves: las del config.json      │
 └───────────────────────┘                  └────────────────────────────────────┘
```

## Qué necesitas
- Tu instancia de AWS con el bot ya instalado (se reutilizan sus claves; **no se modifica el bot**).
- Windows 10/11 con el **cliente OpenSSH** (viene incluido; comprueba con `ssh -V` en PowerShell. Si no: Configuración → Aplicaciones → Características opcionales → *Cliente OpenSSH*).
- La **clave `.pem` de tu instancia** (Lightsail → Cuenta → Claves SSH → descargar la clave de la región Ohio).

## Paso 1 — Servidor en AWS (una vez)
En la terminal de Lightsail:
```bash
cd ~ && git clone -b claude/traductor-cliente-aws https://github.com/ehyenmanft/traductor.git traductor-live
cd traductor-live && bash deploy_live/install_live.sh
```
Instala todo en `~/traductor-live` (carpeta, entorno virtual y servicio `traductor-live` propios), lee las claves del `config.json` del bot (`~/traductor/config.json`) y al final **imprime tu token y el bloque de configuración para la PC**. No abre ningún puerto: el servidor solo escucha en `127.0.0.1`.

> ⚠️ El token es como una contraseña: no lo compartas ni lo pegues en chats.

## Paso 2 — Cliente en la PC
1. Descarga el programa ya compilado: en GitHub → pestaña **Actions** → *Compilar cliente de Windows* → última ejecución en verde → **Artifacts → TraductorCliente-windows** → descomprime el zip donde quieras.
2. Copia `config.client.example.json` como `config.json` (en la misma carpeta que el `.exe`) y completa:
   ```json
   {
     "server": "ws://127.0.0.1:8765",
     "token": "EL_TOKEN_DEL_PASO_1",
     "ssh": { "host": "IP_PUBLICA_DE_AWS", "user": "ubuntu", "key": "auto" }
   }
   ```
   **Clave SSH (`"key"`):** con `"auto"` el programa usa la única clave `.pem` que haya **en la misma carpeta que el `.exe`**. También puedes poner solo el nombre del archivo (`"mi_clave.pem"`) o una ruta completa (`"C:/Users/Ana/.ssh/mi_clave"`, admite `~` y `%USERPROFILE%`); si la ruta está mal escrita pero hay un archivo con ese mismo nombre junto al programa, usa ese. Si hay varias `.pem` en la carpeta, indica cuál. `"key": ""` = sin clave (usa tu agente SSH).
3. Doble clic en **TraductorCliente.exe**. Verás "🔌 Conectando con AWS…" y luego "✅ Conectado con AWS".

La primera vez Windows SmartScreen puede avisar de que el programa no está firmado: *Más información → Ejecutar de todas formas*.

## Uso
Es el mismo overlay de la app de escritorio: **F6** modo HUD, **F7** opacidad, **F8** click-through, **F9** ocultar, **F10** compacto, **F11** doblaje (también el botón 🔊/🔇), **🎨** estilo, y en la bandeja idioma, **tono** y recarga del glosario. Los cambios de idioma, tono y doblaje se aplican en el servidor al instante.

## Seguridad
- El audio y el token viajan por un **túnel SSH** (cifrado); el servidor no escucha en internet.
- Las claves de Deepgram/Groq **nunca salen de AWS**: la PC solo tiene el token.
- Un solo cliente a la vez: si abres otro, reemplaza al anterior (así una reconexión tras un corte no queda bloqueada).
- Si pierdes el token o crees que se filtró: borra `live_config.json` en el servidor y vuelve a ejecutar `install_live.sh` (genera uno nuevo).

## Aislamiento del bot de Telegram
El servidor usa **carpeta, entorno virtual y servicio propios** y solo **lee** el `config.json` del bot. El servicio lleva `MemoryMax=600M`, `CPUQuota=80%` y `Nice=10` para no quitarle recursos al bot. Puedes comprobarlo:
```bash
systemctl status traductor-bot traductor-live --no-pager | grep -E "●|Active"
```

## Consumo
- **PC:** captura de audio + dibujar el overlay; sin modelos. Sube ≈ 32 KB/s (16 kHz, 16 bits, mono).
- **AWS/APIs:** Deepgram (por tiempo de audio enviado), Groq y edge-tts se usan igual que en la app de escritorio, pero con tus claves del servidor.

## Diferencias con la app de escritorio
- Solo motor **Deepgram** (sin Whisper local ni Groq-Whisper).
- El **glosario** está en el `config.json` del servidor; "Recargar glosario" de la bandeja lo relee.
- Necesita internet y que AWS esté encendido; añade el retardo de red de ida y vuelta.

## Si algo falla
| Síntoma | Qué mirar |
|---|---|
| "Sin conexión; reconectando…" | ¿Funciona el túnel? En PowerShell: `ssh -i "C:\ruta\clave.pem" ubuntu@IP echo ok`. Si pide permisos de la clave, el cliente usa una copia protegida; revisa la ruta en `config.json`. |
| "Token inválido" | El token del `config.json` de la PC no coincide con `live_config.json` del servidor. |
| "Otra conexión tomó la sesión" | Hay otra PC/ventana conectada: ciérrala. |
| Conecta pero no aparecen subtítulos | `journalctl -u traductor-live -n 40 --no-pager` en AWS (¿clave de Deepgram válida?). |
| El servidor no responde | `bash deploy_live/update_live.sh` (muestra el resultado del autochequeo). |

## Actualizar / desinstalar
```bash
cd ~/traductor-live && bash deploy_live/update_live.sh        # actualizar
cd ~/traductor-live && bash deploy_live/uninstall_live.sh     # quitar el servicio (no toca el bot)
```
